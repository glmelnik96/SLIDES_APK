"""Typed structured slide content (feature: content-first chat build).

A ``DraftSlide`` may carry a ``slide_type`` + ``fields``: a small, strictly typed
contract per type. ``validate_fields`` normalises raw field dicts (returning None
when they don't fit the type — the caller then keeps the slide "raw" and lets the
old LLM-fill path handle it). ``map_typed`` deterministically maps a typed slide
to an existing engine template + content dict, so rendering needs no LLM.

This module is pure: no I/O, no engine imports beyond the template *ids* it names.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError, field_validator

from htmlslides.diagrams import DiagramValidationError, parse_diagram


class FieldsError(ValueError):
    """Ошибка контракта полей со СПИСКОМ читаемых претензий.

    Pydantic заворачивает ValueError из field_validator в ValidationError, но
    кладёт сам объект в ``ctx['error']`` — так перечень претензий диаграммы
    доезжает до эндпоинта, а не схлопывается в одно «invalid diagram spec».
    Без этого правка, ломающая схему по смыслу (удалили последнее ребро,
    свели дорожки к одной), давала пользователю молчаливый статус «error»."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class TitleFields(BaseModel):
    heading: str
    subtitle: str = ""


class BulletsFields(BaseModel):
    heading: str
    bullets: list[str] = Field(default_factory=list)


class StatItem(BaseModel):
    value: str = ""
    label: str = ""


class StatsFields(BaseModel):
    heading: str
    stats: list[StatItem] = Field(default_factory=list)


class TwoColFields(BaseModel):
    heading: str
    left: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)


class DiagramFields(BaseModel):
    """Диаграмма: heading + строгий DiagramSpec (htmlslides/diagrams/schema.py).

    ``diagram`` нормализуется валидатором схемы: невалидный спек = невалидные
    fields целиком (validate_fields → None, слайд остаётся «сырым» — как и для
    остальных типов). Дальше map_typed сериализует спек в компактный JSON для
    слота шаблона; рисует его детерминированный engine/diagram.js без LLM."""
    heading: str
    subtitle: str = ""
    diagram: dict

    @field_validator("diagram")
    @classmethod
    def _valid_spec(cls, v: dict) -> dict:
        try:
            spec = parse_diagram(v)
        except DiagramValidationError as exc:
            raise FieldsError(exc.errors) from exc
        return spec.model_dump(by_alias=True)


# slide_type → (Pydantic model, engine template id)
_SPECS: dict[str, tuple[type[BaseModel], str]] = {
    "title":   (TitleFields,   "cover"),
    "bullets": (BulletsFields, "cards-6"),
    "stats":   (StatsFields,   "stats-row"),
    "two_col": (TwoColFields,  "three-col"),
    "diagram": (DiagramFields, "diagram"),
}

SLIDE_TYPES = tuple(_SPECS)


def validate_fields(slide_type, raw) -> dict | None:
    """Normalise ``raw`` for ``slide_type``; None if the type is unknown or the
    fields don't satisfy the contract (missing required heading, wrong shape)."""
    return validate_fields_verbose(slide_type, raw)[0]


# R-1 (раунд 2 аудита, 2026-08-15): претензии показываются человеку в панели
# правки — типовые pydantic-сообщения переводим, редкие уходят как есть.
_PYDANTIC_RU: dict[str, str] = {
    "missing": "обязательное поле не заполнено",
    "string_type": "нужна строка",
    "int_type": "нужно целое число",
    "float_type": "нужно число",
    "bool_type": "нужно да/нет",
    "list_type": "нужен список",
    "dict_type": "нужен объект",
    "string_too_long": "текст слишком длинный",
    "too_long": "слишком много элементов",
}


def _readable(exc: ValidationError) -> list[str]:
    """Претензии Pydantic — по строке на ошибку; вложенный FieldsError
    (диаграмма) разворачивается в свой список: он уже написан по-русски."""
    out: list[str] = []
    for err in exc.errors():
        inner = (err.get("ctx") or {}).get("error")
        if isinstance(inner, FieldsError):
            out.extend(inner.errors)
            continue
        loc = ".".join(str(p) for p in err["loc"])
        msg = _PYDANTIC_RU.get(err.get("type", ""), err["msg"])
        out.append(f"{loc or 'поля'}: {msg}")
    return out


def validate_fields_verbose(slide_type, raw) -> tuple[dict | None, list[str]]:
    """``(нормализованные поля, [])`` либо ``(None, список претензий)``.

    Отдельная verbose-форма нужна эндпоинту правки: он единственный отвечает
    человеку, а не машине, и обязан сказать, ЧТО именно не так со схемой."""
    spec = _SPECS.get(slide_type)
    if spec is None:
        return None, [f"неизвестный тип слайда: {slide_type!r}"]
    if not isinstance(raw, dict):
        return None, ["поля слайда должны быть объектом"]
    model, _ = spec
    try:
        return model.model_validate(raw).model_dump(), []
    except ValidationError as exc:
        return None, _readable(exc)


def typed_from_content(template_id, content) -> tuple[str, dict] | None:
    """Обратная map_typed для схем: контент LLM-филлера → (slide_type, fields).

    Филлер отдаёт диаграмму слотами шаблона (спек — JSON-СТРОКОЙ в ``diagram``),
    а панель узлов и перетаскивание в редакторе работают только с typed-слайдом.
    Без этой конверсии схема, собранная ИИ в черновике (стеклянная сборка, чат),
    правилась лишь как сырой JSON в textarea — при том что руками созданная
    схема редактировалась полноценно. None — контент не похож на схему (тогда
    слайд остаётся сырым: рендер у обоих форм одинаковый)."""
    if template_id != "diagram" or not isinstance(content, dict):
        return None
    spec = content.get("diagram")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError:
            return None
    if not isinstance(spec, dict):
        return None
    norm = validate_fields("diagram", {
        "heading": str(content.get("title") or ""),
        "subtitle": str(content.get("subtitle") or ""),
        "diagram": spec,
    })
    if norm is None:
        return None
    return "diagram", norm


def _clean(items) -> list[str]:
    return [str(x).strip() for x in items if str(x).strip()]


def map_typed(slide_type, fields) -> tuple[str | None, dict]:
    """Map a typed slide to (engine_template_id, content_dict). Returns
    (None, {}) when the type/fields are invalid — the caller falls back to the
    raw LLM-fill path. Content is left permissive; draft_render's ``_safe_content``
    clamps text/lists to the template's slot contract."""
    norm = validate_fields(slide_type, fields)
    if norm is None:
        return None, {}
    if slide_type == "title":
        return "cover", {"title": norm["heading"], "subtitle": norm["subtitle"]}
    if slide_type == "bullets":
        cards = [{"text": b} for b in _clean(norm["bullets"])]
        return "cards-6", {"title": norm["heading"], "cards": cards}
    if slide_type == "stats":
        stats = [{"value": s.get("value", ""), "label": s.get("label", "")}
                 for s in norm["stats"]]
        return "stats-row", {"title": norm["heading"], "stats": stats}
    if slide_type == "two_col":
        columns = [{"text": " • ".join(_clean(norm["left"]))},
                   {"text": " • ".join(_clean(norm["right"]))}]
        return "three-col", {"title": norm["heading"], "columns": columns}
    if slide_type == "diagram":
        # Компактный JSON: слот diagram капится max_chars=8000, капы схемы
        # (12 узлов / 20 рёбер / 60 симв.) держат худший случай ~4К — запас x2.
        spec = json.dumps(norm["diagram"], ensure_ascii=False,
                          separators=(",", ":"))
        return "diagram", {"title": norm["heading"], "subtitle": norm["subtitle"],
                           "diagram": spec}
    return None, {}
