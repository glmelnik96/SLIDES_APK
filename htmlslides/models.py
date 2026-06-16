"""Pydantic-модели плана деки (контракт planner -> assembler)."""
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class SlidePlan(BaseModel):
    index: int = Field(ge=1)
    type: str
    template_id: Optional[str] = None
    freeform: bool = False
    content: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _template_required_unless_freeform(self) -> "SlidePlan":
        if not self.freeform and not self.template_id:
            raise ValueError("template_id is required when freeform is false")
        return self


class DeckPlan(BaseModel):
    title: str = ""
    slides: list[SlidePlan] = Field(min_length=1)
