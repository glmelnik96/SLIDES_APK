"""Каталог шаблонов слайдов и валидация контента по слот-контрактам."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Optional


class SlotValidationError(Exception):
    """Шаблон не найден или каталог повреждён."""


@dataclass(frozen=True)
class SlotSpec:
    kind: str                                   # text | list | group
    required: bool = False
    max_chars: int = 0                          # 0 = не ограничено
    max_items: int = 0
    item_max_chars: int = 0
    item_slots: dict[str, "SlotSpec"] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "SlotSpec":
        return cls(
            kind=data["kind"],
            required=data.get("required", False),
            max_chars=data.get("max_chars", 0),
            max_items=data.get("max_items", 0),
            item_max_chars=data.get("item_max_chars", 0),
            item_slots={
                name: SlotSpec.from_json(spec)
                for name, spec in data.get("item_slots", {}).items()
            },
        )


@dataclass(frozen=True)
class ContentError:
    code: str           # missing_required | too_long | too_many_items | unknown_slot
    slot: str
    detail: str = ""


@dataclass(frozen=True)
class TemplateSpec:
    id: str
    type: str
    file: str
    intent: str
    slots: dict[str, SlotSpec]


@dataclass
class TemplateLibrary:
    templates: list[TemplateSpec]
    _by_id: dict[str, TemplateSpec] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {t.id: t for t in self.templates}

    @classmethod
    def load(cls) -> "TemplateLibrary":
        raw = (resources.files("htmlslides") / "templates" / "library.json").read_text("utf-8")
        data = json.loads(raw)
        templates = [
            TemplateSpec(
                id=t["id"],
                type=t["type"],
                file=t["file"],
                intent=t["intent"],
                slots={name: SlotSpec.from_json(s) for name, s in t["slots"].items()},
            )
            for t in data["templates"]
        ]
        return cls(templates=templates)

    def get(self, template_id: str) -> TemplateSpec:
        try:
            return self._by_id[template_id]
        except KeyError:
            raise SlotValidationError(f"unknown template: {template_id}") from None

    def validate_content(self, template_id: str, content: dict[str, Any]) -> list[ContentError]:
        template = self.get(template_id)
        errors: list[ContentError] = []
        for name, spec in template.slots.items():
            value = content.get(name)
            if value in (None, "", []):
                if spec.required:
                    errors.append(ContentError("missing_required", name))
                continue
            errors.extend(_check_slot(name, spec, value))
        for key in content:
            if key not in template.slots:
                errors.append(ContentError("unknown_slot", key))
        return errors


def _check_slot(name: str, spec: SlotSpec, value: Any) -> list[ContentError]:
    errors: list[ContentError] = []
    if spec.kind == "text":
        text = str(value)
        if spec.max_chars and len(text) > spec.max_chars:
            errors.append(ContentError(
                "too_long", name, f"{len(text)} > {spec.max_chars}"))
    elif spec.kind == "list":
        items = list(value)
        if spec.max_items and len(items) > spec.max_items:
            errors.append(ContentError(
                "too_many_items", name, f"{len(items)} > {spec.max_items}"))
        for i, item in enumerate(items):
            errors.extend(_check_group(f"{name}[{i}]", spec, item))
    elif spec.kind == "group":
        errors.extend(_check_group(name, spec, value))
    return errors


def _check_group(prefix: str, spec: SlotSpec, item: Any) -> list[ContentError]:
    errors: list[ContentError] = []
    if not isinstance(item, dict):
        errors.append(ContentError("too_long", prefix, "expected object"))
        return errors
    if spec.item_max_chars:
        total = sum(len(str(v)) for v in item.values())
        if total > spec.item_max_chars:
            errors.append(ContentError(
                "too_long", prefix, f"total {total} > {spec.item_max_chars}"))
    for sub_name, sub_spec in spec.item_slots.items():
        sub_value = item.get(sub_name)
        if sub_value in (None, "", []):
            if sub_spec.required:
                errors.append(ContentError("missing_required", f"{prefix}.{sub_name}"))
            continue
        errors.extend(_check_slot(f"{prefix}.{sub_name}", sub_spec, sub_value))
    return errors
