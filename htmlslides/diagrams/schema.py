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
import re
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
# Капы полей вынесены в имена, чтобы промпт филлера называл ТЕ ЖЕ числа: молчащее
# расхождение стоило лишнего ретрая на 8192 токена там, где хватало строки.
MAX_ID = 24
MAX_EDGE_LABEL = 30
MAX_VALUE = 12
MAX_LANE = 40
# Читаемость волны 3: строк плана-графика и ступеней лестницы меньше общего капа
# узлов — 12 полос на 720px холста и 12 ступеней на 1800px нечитаемы физически.
MAX_GANTT_ROWS = 8
MAX_STEPS = 6
# Стадий цикла — не больше, чем каталог обещает пикеру и промпту («3–8 стадий по
# кругу»). Дуга между соседними плашками на эллипсе 1800×720 меряется: 76px при
# восьми стадиях, 46 при десяти и 19 при двенадцати — а сам наконечник стрелки
# 21px (markerWidth 7 × stroke 3). То есть с одиннадцати стадий стрелки цикла
# вырождаются в оторванные треугольники без древка, и кольцо читается как
# сломанное. Ужать плашки не помогает: дуга упирается в их ВЫСОТУ, а не ширину.
MAX_CYCLE = 8
MAX_DURATION = 12          # длительность работы в периодах шкалы


class DiagramValidationError(Exception):
    """Диаграмма не прошла контракт; ``errors`` — читаемый список претензий."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_ID)
    label: str = Field("", max_length=MAX_LABEL)
    # shape осмыслен для flowchart; остальные типы его игнорируют.
    shape: Literal["start", "end", "process", "decision", "io"] = "process"
    accent: bool = False
    value: str = Field("", max_length=MAX_VALUE)   # величина (funnel), опционально
    lane: str = Field("", max_length=MAX_LANE)     # дорожка (swimlanes, фаза 2)
    level: int | None = Field(None, ge=0, le=11)  # подсказка уровня (pyramid)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1, max_length=MAX_ID)
    to: str = Field(min_length=1, max_length=MAX_ID)
    label: str = Field("", max_length=MAX_EDGE_LABEL)
    style: Literal["solid", "dashed"] = "solid"


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=MAX_ID)
    label: str = Field("", max_length=MAX_LANE)


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
        # label по форме необязателен (у него есть дефолт), но узел без подписи —
        # пустая плашка на слайде: молча пропустить хуже, чем попросить переделать.
        blank = [n.id for n in self.nodes if not n.label.strip()]
        if blank:
            errors.append("узлы без подписи (label): "
                          + ", ".join(repr(i) for i in blank[:5]))
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

    def isolated_errors(self) -> list[str]:
        """Претензия к узлам, до которых не ведёт и от которых не идёт ни одно
        ребро — для типов, где раскладку задают именно рёбра.

        Модель регулярно забывает последнюю связь («…→ Услуга подключена»), и
        схема формально валидна: узел просто выпадает из потока и садится в
        нулевой ранг рядом со стартом — на слайде это выглядит поломкой, а не
        недосказанностью. Пустой список рёбер не трогаем: там свои правила.
        """
        if not self.edges:
            return []
        linked = {e.from_ for e in self.edges} | {e.to for e in self.edges}
        lone = [n.id for n in self.nodes if n.id not in linked]
        if not lone:
            return []
        return ["узлы вне схемы — к ним не ведёт ни одно ребро: "
                + ", ".join(repr(i) for i in lone[:5])]


class FlowchartSpec(_BaseSpec):
    """Блок-схема: процесс с ветвлениями. Рёбра обязательны при ≥2 узлах."""
    kind: Literal["flowchart"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) >= 2 and not self.edges:
            errors.append("flowchart с несколькими узлами требует хотя бы одно ребро")
        return errors + self.isolated_errors()


class ProcessSpec(_BaseSpec):
    """Этапы: узлы в порядке следования; рёбра не нужны (порядок = список)."""
    kind: Literal["process"]


class CycleSpec(_BaseSpec):
    """Цикл: 3–8 узлов по кругу, стрелки по порядку списка."""
    kind: Literal["cycle"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if not 3 <= len(self.nodes) <= MAX_CYCLE:
            errors.append(f"cycle требует от 3 до {MAX_CYCLE} стадий, "
                          f"сейчас {len(self.nodes)}")
        return errors


class FunnelSpec(_BaseSpec):
    """Воронка: слои сверху вниз, ширина по value (или равномерное сужение)."""
    kind: Literal["funnel"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 2:
            errors.append("funnel требует минимум 2 слоя")
        return errors


def _tree_errors(edges) -> list[str]:
    """Претензии к рёбрам, которые обязаны складываться в дерево (hierarchy,
    mindmap): второй родитель и кольцо родительских связей.

    Лишнее ребро не просто «некрасиво»: обе раскладки строят дерево обходом от
    корня и второе ребро к тому же узлу молча теряют — на слайде связь, которую
    автор задал, отсутствует."""
    errors: list[str] = []
    parents: dict[str, str] = {}
    for e in edges:
        if e.to in parents:
            errors.append(f"у узла {e.to!r} несколько родителей — это не дерево")
        parents[e.to] = e.from_
    for nid in parents:
        hops, cur = 0, nid
        while cur in parents:
            cur = parents[cur]
            hops += 1
            if hops > MAX_NODES:
                errors.append(f"цикл родительских связей вокруг узла {nid!r}")
                break
    return errors


class HierarchySpec(_BaseSpec):
    """Оргсхема/дерево: рёбра = родитель→ребёнок, у узла максимум один родитель."""
    kind: Literal["hierarchy"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors() + _tree_errors(self.edges)
        return errors + self.isolated_errors()


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
        return errors + self.isolated_errors()


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _periods(value: str) -> float:
    """Число из строки величины («3», «2,5», «3 мес») — зеркало diagram.js num.
    0 = числа нет, то есть длительность не задана."""
    m = _NUM_RE.search(str(value or ""))
    return float(m.group(0).replace(",", ".")) if m else 0.0


class GanttLiteSpec(_BaseSpec):
    """План-график: узел = работа, ``value`` — длительность в периодах шкалы,
    ``level`` — стартовый период (без него работа встаёт сразу за предыдущей).
    Рёбра не нужны: положение полосы задают числа, а не связи."""
    kind: Literal["gantt_lite"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 2:
            errors.append("gantt_lite требует минимум 2 работы")
        elif len(self.nodes) > MAX_GANTT_ROWS:
            errors.append(f"gantt_lite вмещает до {MAX_GANTT_ROWS} работ, "
                          f"сейчас {len(self.nodes)}")
        bad = [n.id for n in self.nodes
               if not 0 < _periods(n.value) <= MAX_DURATION]
        if bad:
            errors.append("gantt_lite: длительность работы (value) — число от 1 "
                          f"до {MAX_DURATION} периодов; не задана у "
                          + ", ".join(repr(i) for i in bad[:5]))
        return errors


class StepsSpec(_BaseSpec):
    """Лестница: ступени слева направо и снизу вверх; ``value`` — необязательная
    пометка ступени («Уровень 2», «2026»)."""
    kind: Literal["steps"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if not 2 <= len(self.nodes) <= MAX_STEPS:
            errors.append(f"steps требует от 2 до {MAX_STEPS} ступеней, "
                          f"сейчас {len(self.nodes)}")
        return errors


class MindmapSpec(_BaseSpec):
    """Ментальная карта: ПЕРВЫЙ узел — центр, рёбра = ветвь→подветвь. Без рёбер
    ВСЕ узлы становятся ветвями центра — это законная плоская карта.

    Но если рёбра есть, узел без ребра тоже молча садится ветвью центра: модель
    забывает часть связей, и двухуровневая карта схлопывается в плоский веер —
    визуально не отличимый от задуманного. Поэтому смешанный случай ловим
    ``isolated_errors`` (он сам пропускает случай «рёбер нет вовсе»)."""
    kind: Literal["mindmap"]

    def semantic_errors(self) -> list[str]:
        errors = (super().semantic_errors() + _tree_errors(self.edges)
                  + self.isolated_errors())
        if len(self.nodes) < 3:
            errors.append("mindmap требует минимум 3 узла (центр и две ветви)")
        if any(e.to == self.nodes[0].id for e in self.edges):
            errors.append(f"центр карты {self.nodes[0].id!r} не может быть "
                          "подветвью — ребро ведёт в него")
        return errors


class NetworkSpec(_BaseSpec):
    """Граф связей: произвольная сеть, раскладку задают именно рёбра."""
    kind: Literal["network"]

    def semantic_errors(self) -> list[str]:
        errors = super().semantic_errors()
        if len(self.nodes) < 3:
            errors.append("network требует минимум 3 узла")
        if not self.edges:
            errors.append("network требует хотя бы одну связь — без связей это "
                          "не граф, а россыпь плашек")
        return errors + self.isolated_errors()


DiagramSpec = Annotated[
    Union[FlowchartSpec, ProcessSpec, CycleSpec, FunnelSpec, HierarchySpec,
          MatrixSpec, PyramidSpec, HubSpokeSpec, ComparisonSpec, VennSpec,
          SwimlanesSpec, GanttLiteSpec, StepsSpec, MindmapSpec, NetworkSpec],
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
