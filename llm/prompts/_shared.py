"""Shared constants injected into multiple prompts.

Per `<project>/memory/prompt_adaptation.md`: brand glossary, template slot
names, and donor map IDs live here so all prompts see the same source of
truth. Editing here updates every dependent agent.
"""
from __future__ import annotations

# ─── Brand glossary (Cloud.ru 2.0 verbatim, do not translate) ────────────────

BRAND_NAME = "Cloud.ru"
PRIMARY_FONT = "SB Sans Display"
SEMIBOLD_FONT = "SB Sans Display Semibold"
BRAND_PALETTE = {
    "green":    "#26D07C",  # accent only, 5–10% of slide area
    "graphite": "#222222",  # primary text + dark backgrounds
    "gray":     "#F2F2F2",  # background blocks (zebra rows, flow blocks)
    "white":    "#FFFFFF",
    "stroke":   "#C8C8C8",  # vertical separators in tables
}
WHITELISTED_PRODUCTS = [
    "Cloud.ru", "Evolution Stack", "Christofari Neo", "Платформа",
]

# ─── Canvas geometry (slides are 1280×720 px = 16:9) ─────────────────────────

CANVAS_PX = (1280, 720)
EMU_PER_PX = 9525
SAFE_AREA_PX = {"left": 30, "right": 1250, "top": 140, "bottom": 660}

# ─── Common WS-E directives reused across agents ─────────────────────────────

JSON_ONLY_FOOTER = (
    "ФОРМАТ ВЫВОДА: только JSON по указанной выше схеме. "
    "Без префиксов, без объяснений, без markdown-ограждений ```. "
    "Без комментариев внутри JSON. "
    "Лишние поля игнорируются — добавлять их не нужно."
)

LANGUAGE_RULE = (
    "Все текстовые поля — на русском языке. "
    "Латиница допускается только для собственных имён продуктов "
    f"({', '.join(WHITELISTED_PRODUCTS)}) и технических аббревиатур."
)
