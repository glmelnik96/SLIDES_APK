"""Quality gates for the HTML-render pipeline: brand-critic + vision pixel-judge.

Two complementary gates per slide, mirroring the proven /design mechanism
(graph/designer/vision_qa.py) but adapted to the HTML medium:

1. ``critic_gate`` — GLM thinking-ON reads the authored HTML against the brand
   canons (READY/NOT-READY + concrete reasons). Catches canon violations the
   renderer would faithfully draw (white canvas on a content slide, multiple
   green accents, broken logo markup, gradients/shadows).
2. ``judge_slide`` — Kimi vision judges the rendered PNG against the brand
   exemplar for the slide's archetype. Catches what only pixels show: overflow,
   clipping, top-clumped layouts, overlap, illegible contrast.

Both fail open (gate passes) on LLM/parse errors so QA is purely additive —
a flaky judge never breaks a deck build.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from llm.output_parsers import call_and_parse
from llm.prompts.designer import pixel_judge
from llm.roles import Role
from schemas.design import CriticVerdict, PixelVerdict

logger = structlog.get_logger(__name__)

# Celery's soft time limit is delivered as an exception that subclasses plain
# Exception — the fail-open handlers below MUST let it through, otherwise the
# worker keeps composing until the hard kill (observed live 2026-06-10: 1h
# hang, no terminal event). Conditional import keeps renderers/ importable
# outside the worker (host probes/scripts without celery installed).
try:
    from billiard.exceptions import SoftTimeLimitExceeded
except ImportError:  # pragma: no cover — host env without celery/billiard
    class SoftTimeLimitExceeded(BaseException):  # type: ignore[no-redef]
        pass

_REPO = Path(__file__).resolve().parent.parent.parent
_EXEMPLAR_DIR = _REPO / "skill_assets" / "brand" / "references" / "exemplars"

# Archetype → brand exemplar shown to the vision judge as the target look.
_EXEMPLAR_BY_ARCHETYPE: dict[str, str] = {
    "cover": "cover_green.png",
    "section-divider": "section_divider.png",
    "title-body": "points_6.png",
    "comparison": "points_6.png",
    "kpi": "points_6.png",
    "table": "table_zebra.png",
    "data-chart": "chart_columns.png",
    "timeline": "roadmap_timeline.png",
    "diagram-flow": "points_4.png",
    "team": "points_8.png",
}


def exemplar_for(archetype: str, dark: bool = False) -> bytes | None:
    """Brand exemplar PNG for an archetype, or None when missing."""
    name = _EXEMPLAR_BY_ARCHETYPE.get(archetype)
    if archetype == "cover" and dark:
        name = "cover_dark.png"
    if not name:
        return None
    p = _EXEMPLAR_DIR / name
    try:
        return p.read_bytes() if p.is_file() else None
    except Exception:
        return None


_CRITIC_SYSTEM = """\
Ты — самый строгий бренд-критик Cloud.ru 2.0. Тебе дают HTML-фрагмент одного
слайда (1280×720, верстается поверх brand.css) и его контент-payload. Вынеси
вердикт: соответствует ли вёрстка канонам бренда.

ПРОВЕРЯЙ (любое нарушение → NOT-READY):
- КОНТЕНТНЫЙ слайд (есть .slide__header) обязан иметь класс slide--canvas
  (серый канвас) и контент в .card / .rule-point / .bullets / .brand-table.
  Обложка (.cover-green) и разделитель (.cover-dark) — исключение.
- Лого — ровно фрагмент <div class="brand-logo">…<span class="brand-logo__cube">
  …cloud.ru (вордмарк строчными). Изменённый/самодельный лого или «Cloud.ru»
  с заглавной в лого — брак. Копирайт .copyright обязателен.
- ОДИН зелёный акцент раскладки: черты .rule-point/.takeaway — это ОДИН приём,
  их может быть несколько; но зелёные ЗАЛИВКИ (фоны карточек/плашек/текста)
  вне разрешённых мест (фон cover-green, шапка th .brand-table, заголовок
  cover-dark) — брак. Зелёный текст на белом — брак.
  НЕ брак: class="accent" на th/td в .brand-table — это СИНЯЯ акцент-колонка
  (var(--blue)/синие тинты, разрешённый data-viz приём), ровно одна на таблицу.
- Запрещено: linear-gradient/radial-gradient в декоративных заливках (radial в
  .dot-grid — разрешённый мотив), box-shadow, border-radius > 4px, font-style:
  italic, text-decoration: underline, не-брендовые цвета (кроме data-viz:
  var(--blue)/тинты в таблице, пастель в графике).
- Текст из payload присутствует ДОСЛОВНО (не переписан, не переведён, не
  выброшен). Мелкая типографика (тире/кавычки) не в счёт.
  ИСКЛЮЧЕНИЕ: key_phrase, дублирующая заголовок слайда (или почти совпадающая
  с ним), по канону НЕ выводится — её отсутствие не брак, не требуй её.
- Вертикальное распределение по вёрстке: контент растянут на безопасную зону
  (justify-content/space-between/row-gap/.takeaway), а не одним комком сверху.

НЕ проверяй пиксельные размеры и переполнение — это работа визуального судьи.
НЕ требуй переводить текст (text-is-sacred).

СХЕМА ВЫВОДА (строго JSON, без markdown):
{"verdict":"READY|NOT-READY","reasons":["краткая конкретная причина", "..."]}
Если READY — reasons пустой. Если NOT-READY — причины должны быть исправимы
за один проход вёрстки."""


def critic_gate(html: str, content: dict[str, Any]) -> CriticVerdict:
    """Brand-canon gate on the authored HTML. Fails open (READY) on errors."""
    user = (
        f"PAYLOAD={json.dumps(content, ensure_ascii=False)}\n\n"
        f"HTML:\n{html}\n\n"
        "Вынеси вердикт READY/NOT-READY по этому слайду."
    )
    try:
        verdict, _ = call_and_parse(
            role=Role.BRAND_CRITIC_V2,
            messages=[
                {"role": "system", "content": _CRITIC_SYSTEM},
                {"role": "user", "content": user},
            ],
            model_cls=CriticVerdict,
        )
        return verdict
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("html_qa.critic_fail", err=str(exc))
        return CriticVerdict(verdict="READY", reasons=[])


def judge_slide(content: dict[str, Any], png: bytes,
                archetype: str) -> PixelVerdict:
    """Vision verdict on one rendered slide PNG. Fails open (ok) on errors."""
    reference = exemplar_for(archetype, dark=bool(content.get("dark")))
    try:
        verdict, _ = call_and_parse(
            role=Role.PIXEL_JUDGE,
            messages=pixel_judge.build_messages(content, png, reference),
            model_cls=PixelVerdict,
        )
        return verdict
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.warning("html_qa.judge_fail", err=str(exc))
        return PixelVerdict(ok=True, issues=[])
