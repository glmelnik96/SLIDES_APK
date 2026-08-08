"""Строгий контракт данных диаграммы (DiagramSpec).

Схема — единственный источник истины о том, что может нарисовать движок
diagram.js: узлы, связи, группы, ручные сдвиги (offsets). Валидация двухслойная:
Pydantic (форма) + семантика (рёбра ссылаются на существующие id, капы) — обе
собираются в СПИСОК читаемых ошибок ``DiagramValidationError.errors``, чтобы
LLM-филлер (фаза 2) мог ретраить с перечнем претензий, а редактор — показать их
пользователю.

Капы подобраны так, чтобы канонический JSON валидной диаграммы ЗАВЕДОМО влезал
в слот-контракт шаблона ``diagram`` (max_chars 8000): draft_render._coerce_slot
молча обрезает текст по капу, а обрезанный JSON — сломанный слайд. Худший случай
(12 узлов × 60-симв. label + 20 рёбер + offsets) ≈ 4К символов — двойной запас.

Неизвестные ключи offsets (узел удалили после drag) молча отбрасываются: смена
контента не должна ломать валидацию, ручные сдвиги уцелевших узлов живут дальше.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import (BaseModel, ConfigDict, Field, TypeAdapter,
                      ValidationError)

# Рабочая зона диаграммы на слайде (px виртуального холста 1920×1080):
# контент-зона под шапкой, как .freeform-zone (left 60 / top 300 / 1800×720).
CANVAS_W = 1800
CANVAS_H = 720

MAX_NODES = 12
MAX_EDGES = 20
MAX_GROUPS = 5
MAX_LABEL = 60


class DiagramValidationError(Exception):
    """Диаграмма не прошла контракт; ``errors`` — читаемый список претензий."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=24)
    label: str = Field("", max_length=MAX_LABEL)
    # shape осмыслен для flowchart; остальные типы его игнорируют.
    shape: Literal["start", "end", "process", "decision", "io"] = "process"
    accent: bool = False
    value: str = Field("", max_length=12)      # величина (funnel), опционально
    lane: str = Field("", max_length=40)       # дорожка (swimlanes, фаза 2)
    level: int | None = Field(None, ge=0, le=11)  # подсказка уровня (pyramid)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1, max_length=24)
    to: str = Field(min_length=1, max_length=24)
    label: str = Field("", max_length=30)
    style: Literal["solid", "dashed"] = "solid"


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=24)
    label: str = Field("", max_length=40)


class Meta(BaseModel):
    """Подписи, специфичные для отдельных типов (matrix/hub — фаза 2)."""
    model_config = ConfigDict(extra="forbid")

    x_axis: str = Field("", max_length=40)
    y_axis: str = Field("", max_length=40)
    center_label: str = Field("", max_length=MAX_LABEL)


class Offset(BaseModel):
    """Ручной сдвиг узла относительно авто-раскладки (гибридный режим).

    Дельты клампятся в размер холста ещё на валидации: drag-редактор пишет сырое
    значение, но за пределами рабочей зоны узлу делать нечего."""
    model_config = ConfigDict(extra="forbid")

    dx: float = 0.0
    dy: float = 0.0

    def model_post_init(self, __context) -> None:
        object.__setattr__(self, "dx", max(-CANVAS_W, min(CANVAS_W, self.dx)))
        object.__setattr__(self, "dy", max(-CANVAS_H, min(CANVAS_H, self.dy)))


class _BaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    direction: Literal["right", "down"] = "right"
    nodes: list[Node] = Field(min_length=1, max_length=MAX_NODES)
    edges: list[Edge] = Field(default_factory=list, max_length=MAX_EDGES)
    groups: list[Group] = Field(default_factory=list, max_length=MAX_GROUPS)
    meta: Meta = Field(default_factory=Meta)
    offsets: dict[str, Offset] = Field(default_factory=dict)

    # Семантика поверх формы. Возвращает список ошибок, НЕ бросает: parse_diagram
    # собирает претензии со всех слоёв в один DiagramValidationError.
    def semantic_errors(self) -> list[str]:
        errors: list[str] = []
        ids = [n.id for n in self.nodes]
        seen: set[str] = set()
        for nid in ids:
            if nid in seen:
                errors.append(f"дублирующийся id узла: {nid!r}")
            seen.add(nid)
        for e in self.edges:
            for ref in (e.from_, e.to):
                if ref not in seen:
                    errors.append(f"ребро ссылается на несуществующий узел: {ref!r}")
            if e.from_ == e.to:
                errors.append(f"петля на узле {e.from_!r} не поддерживается")
        # offsets на удалённые узлы — не ошибка: молча чистим (см. докстринг).
        stale = [k for k in self.offsets if k not in seen]
        for k in stale:
            del self.offsets[k]
        return errors


class FlowchartSpec(_BaseSpec):
    """Блок-схема: процесс с ветвлениями. Рёбра обязательны при ≥2 узлах."""
    kind: Literal["flowchart"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) >= 2 and not self.edges:
            errors.append("flowchart с несколькими узлами требует хотя бы одно ребро")
        return errors


class ProcessSpec(_BaseSpec):
    """Этапы: узлы в порядке следования; рёбра не нужны (порядок = список)."""
    kind: Literal["process"]


class CycleSpec(_BaseSpec):
    """Цикл: минимум 3 узла по кругу, стрелки по порядку списка."""
    kind: Literal["cycle"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 3:
            errors.append("cycle требует минимум 3 узла")
        return errors


class FunnelSpec(_BaseSpec):
    """Воронка: слои сверху вниз, ширина по value (или равномерное сужение)."""
    kind: Literal["funnel"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 2:
            errors.append("funnel требует минимум 2 слоя")
        return errors


class HierarchySpec(_BaseSpec):
    """Оргсхема/дерево: рёбра = родитель→ребёнок, у узла максимум один родитель."""
    kind: Literal["hierarchy"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        parents: dict[str, str] = {}
        for e in self.edges:
            if e.to in parents:
                errors.append(f"у узла {e.to!r} несколько родителей — это не дерево")
            parents[e.to] = e.from_
        # Дерево не должно содержать циклов: поднимаемся от каждого узла к корню.
        for nid in parents:
            hops, cur = 0, nid
            while cur in parents:
                cur = parents[cur]
                hops += 1
                if hops > MAX_NODES:
                    errors.append(f"цикл родительских связей вокруг узла {nid!r}")
                    break
        return errors


def _lanes(nodes) -> list[str]:
    """Дорожки в порядке первого появления (порядок = порядок колонок/рядов)."""
    seen: list[str] = []
    for n in nodes:
        if n.lane not in seen:
            seen.append(n.lane)
    return seen


class MatrixSpec(_BaseSpec):
    """Матрица 2×2: ровно 4 узла-квадранта в порядке верх-лево, верх-право,
    низ-лево, низ-право; подписи осей — meta.x_axis / meta.y_axis."""
    kind: Literal["matrix"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) != 4:
            errors.append("matrix требует ровно 4 узла (по одному на квадрант: "
                          "верх-лево, верх-право, низ-лево, низ-право)")
        return errors


class PyramidSpec(_BaseSpec):
    """Пирамида: узлы сверху вниз (вершина первой), минимум 3 уровня."""
    kind: Literal["pyramid"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 3:
            errors.append("pyramid требует минимум 3 уровня")
        return errors


class HubSpokeSpec(_BaseSpec):
    """Хаб и лучи: первый узел — центр, остальные — вокруг. Рёбра опциональны
    (без них движок сам соединяет центр с каждым лучом)."""
    kind: Literal["hub_spoke"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 3:
            errors.append("hub_spoke требует минимум 3 узла (центр и два луча)")
        return errors


class ComparisonSpec(_BaseSpec):
    """Сравнение сторон: у каждого узла lane = название стороны, сторон ровно 2."""
    kind: Literal["comparison"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if any(not n.lane for n in self.nodes):
            errors.append("comparison: у каждого узла должен быть lane — "
                          "название его стороны")
        elif len(_lanes(self.nodes)) != 2:
            errors.append("comparison требует ровно 2 стороны "
                          f"(разных lane), сейчас {len(_lanes(self.nodes))}")
        return errors


class VennSpec(_BaseSpec):
    """Венн: 2–3 узла-множества; подпись пересечения — meta.center_label."""
    kind: Literal["venn"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if not 2 <= len(self.nodes) <= 3:
            errors.append("venn требует 2 или 3 узла-множества")
        return errors


class SwimlanesSpec(_BaseSpec):
    """Дорожки процесса: lane = исполнитель шага (2–5 дорожек); рёбра задают
    порядок шагов (без них порядок = список узлов)."""
    kind: Literal["swimlanes"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if any(not n.lane for n in self.nodes):
            errors.append("swimlanes: у каждого узла должен быть lane — "
                          "исполнитель шага")
        elif not 2 <= len(_lanes(self.nodes)) <= MAX_GROUPS:
            errors.append(f"swimlanes требует 2–{MAX_GROUPS} дорожек (разных "
                          f"lane), сейчас {len(_lanes(self.nodes))}")
        return errors


DiagramSpec = Annotated[
    Union[FlowchartSpec, ProcessSpec, CycleSpec, FunnelSpec, HierarchySpec,
          MatrixSpec, PyramidSpec, HubSpokeSpec, ComparisonSpec, VennSpec,
          SwimlanesSpec],
    Field(discriminator="kind"),
]

_ADAPTER: TypeAdapter = TypeAdapter(DiagramSpec)


def _pydantic_errors(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        out.append(f"{loc or 'spec'}: {err['msg']}")
    return out


def parse_diagram(raw) -> _BaseSpec:
    """Разобрать сырой dict/JSON-строку в валидный spec.

    Бросает ``DiagramValidationError`` со СПИСКОМ всех претензий (форма Pydantic
    + семантика) — контракт для ретраев LLM-филлера и подсветки в редакторе."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DiagramValidationError([f"невалидный JSON: {exc}"]) from exc
    try:
        spec = _ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise DiagramValidationError(_pydantic_errors(exc)) from exc
    errors = spec.semantic_errors()
    if errors:
        raise DiagramValidationError(errors)
    return spec


def validate_diagram(raw) -> dict | None:
    """Нормализованный dict спека (ключ ребра — ``from``) или None, если данные
    не проходят контракт. Зеркалит семантику slide_types.validate_fields."""
    try:
        spec = parse_diagram(raw)
    except DiagramValidationError:
        return None
    return spec.model_dump(by_alias=True)


def spec_json(raw) -> str:
    """Канонический компактный JSON для data-атрибута слайда. Бросает
    DiagramValidationError на невалидных данных."""
    spec = parse_diagram(raw)
    return json.dumps(spec.model_dump(by_alias=True), ensure_ascii=False,
                      separators=(",", ":"))
