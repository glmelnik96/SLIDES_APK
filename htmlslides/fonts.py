"""@font-face CSS со встроенными woff2 (base64) — дека самодостаточна офлайн."""
from __future__ import annotations

import base64
from functools import lru_cache
from importlib import resources

# Medium otf отсутствует в источнике; диапазон 500 600 отдаёт Semibold.
# KPI (.t-number-320) — Display weight 500: без диапазона на Display он по
# CSS-подбору упал бы на Regular(400), поэтому Semibold-фейс ловит и 500 600.
_FACES = [
    ("SB Sans Display", "400", "sb-sans-display-400.woff2"),
    ("SB Sans Display", "500 600", "sb-sans-display-600.woff2"),
    ("SB Sans Text", "400", "sb-sans-text-400.woff2"),
    ("SB Sans Text", "500 600", "sb-sans-text-600.woff2"),
]


@lru_cache(maxsize=1)
def fonts_css() -> str:
    blocks = []
    for family, weight, filename in _FACES:
        data = (resources.files("htmlslides") / "assets" / "fonts"
                / filename).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append(
            f'@font-face{{font-family:"{family}";font-weight:{weight};'
            f"font-style:normal;font-display:swap;"
            f"src:url(data:font/woff2;base64,{b64}) format(\"woff2\");}}")
    return "\n".join(blocks)
