#!/usr/bin/env python3
"""
design_tokens.py — единый загрузчик дизайн-токенов Cloud.ru 2.0.

Сливает два источника истины в один API:
  - brand/palette.json       — цвета (base / extended / semantic-ish)
  - brand/design-tokens.yaml — типографика, сетка, отступы, геометрия,
                               размещение лого/заголовка, компоненты.

Зачем (v2.4, Point 1 token-contract): до этого константы (размеры шрифта,
координаты заголовка, safe-зона, geometry) были захардкожены в каждом рендере
(kpi_renderer, flow_renderer, table_renderer…). Теперь — один контракт, который
читают все рендеры и агенты. Значения = точный снимок прежних констант
(нулевая регрессия);改進 значений приходят отдельными пунктами.

Использование:
    from design_tokens import load_tokens
    T = load_tokens()
    T.hex("Green")           # "#26D07C"
    T.rgb("Black")           # RGBColor(0x22,0x22,0x22)  (pptx импортится лениво)
    T.role("header")         # {"size":20,"weight":"semibold","caps":True,...}
    T.font_face("header")    # "SB Sans Display Semibold"
    T.space("margin")        # 35
    T.safe["left"]           # 35
    T.geom("rounded","none") # 0
    T.place("header")        # {"x":35,"y":38,...}
    T.comp("card")           # {"fill":"Gray",...}
    T.comp_hex("card","fill")# "#F2F2F2"
    T.emu(px)                # px * 9525

CLI:
    python3 design_tokens.py            # печатает сводку токенов
    python3 design_tokens.py --check    # parity-self-check (exit!=0 при расхождении)
"""
import os
import sys
import json

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# ---- robust path resolution (тот же приём, что в flow_renderer._load_palette) ----
def _find_brand_file(filename):
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "brand", filename),
        os.path.join(os.getcwd(), "brand", filename),
        os.path.join(os.getcwd(), "pptx-skill", "brand", filename),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class Tokens:
    def __init__(self, palette, tokens):
        self._pal = palette or {}
        self._tok = tokens or {}
        self.meta = self._tok.get("meta", {})
        self.safe = self._tok.get("safe", {})
        self.grid = self._tok.get("grid", {})
        self.EMU = int(self.meta.get("emu_per_px", 9525))

    # ---------- цвета ----------
    def hex(self, name):
        """Резолвит имя цвета → '#RRGGBB'.
        Порядок: литерал hex → palette.base → palette.extended →
                 design-tokens.semantic_colors. Иначе KeyError."""
        if isinstance(name, str) and name.startswith("#"):
            return name.upper()
        for section in ("base", "extended"):
            sec = self._pal.get(section, {})
            if name in sec:
                return sec[name].upper()
        sem = self._tok.get("semantic_colors", {})
        if name in sem:
            return sem[name].upper()
        raise KeyError(f"design-token color not found: {name!r}")

    def rgb(self, name):
        """→ pptx RGBColor (ленивый импорт, чтобы не-pptx консьюмеры не падали)."""
        from pptx.dml.color import RGBColor
        h = self.hex(name).lstrip("#")
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    # ---------- типографика ----------
    def role(self, name):
        r = self._tok.get("typography", {}).get("roles", {}).get(name)
        if r is None:
            raise KeyError(f"typography role not found: {name!r}")
        return r

    def size(self, role_name):
        return self.role(role_name)["size"]

    def is_semibold(self, role_name):
        return self.role(role_name).get("weight") == "semibold"

    def font_face(self, role_name=None):
        typ = self._tok.get("typography", {})
        if role_name is None:
            return typ.get("family")
        if self.is_semibold(role_name):
            return typ.get("semibold_face")
        return typ.get("family")

    @property
    def family(self):
        return self._tok.get("typography", {}).get("family")

    @property
    def semibold_face(self):
        return self._tok.get("typography", {}).get("semibold_face")

    # ---------- сетка / отступы ----------
    def space(self, name):
        if name in self.grid:
            return self.grid[name]
        raise KeyError(f"grid/space token not found: {name!r}")

    def emu(self, px):
        return int(round(px * self.EMU))

    # ---------- геометрия ----------
    def geom(self, group, key=None):
        g = self._tok.get("geometry", {}).get(group)
        if key is None:
            return g
        return g[key]

    # ---------- размещение ----------
    def place(self, name):
        p = self._tok.get("placement", {}).get(name)
        if p is None:
            raise KeyError(f"placement token not found: {name!r}")
        return p

    # ---------- компоненты ----------
    def comp(self, name):
        c = self._tok.get("components", {}).get(name)
        if c is None:
            raise KeyError(f"component token not found: {name!r}")
        return c

    def comp_hex(self, comp_name, key):
        """Цвет компонента, резолвленный через palette/semantic."""
        return self.hex(self.comp(comp_name)[key])


def load_tokens():
    pal_path = _find_brand_file("palette.json")
    tok_path = _find_brand_file("design-tokens.yaml")
    palette = {}
    tokens = {}
    if pal_path:
        with open(pal_path, encoding="utf-8") as f:
            palette = json.load(f)
    if tok_path and yaml is not None:
        with open(tok_path, encoding="utf-8") as f:
            tokens = yaml.safe_load(f)
    return Tokens(palette, tokens)


# ---- parity self-check: токены == прежние хардкоды (доказывает безопасность миграции) ----
_LEGACY_EXPECTED = {
    # (описание, фактическое значение токена, ожидаемый legacy-хардкод)
    "color.Black":      lambda T: (T.hex("Black"), "#222222"),
    "color.Green":      lambda T: (T.hex("Green"), "#26D07C"),
    "color.Gray":       lambda T: (T.hex("Gray"), "#F2F2F2"),
    "color.text_gray":  lambda T: (T.hex("text_gray"), "#5C5C5C"),  # flow_renderer TEXT_GRAY
    "color.arrow":      lambda T: (T.hex("arrow"), "#434343"),       # flow_renderer ARROW_COLOR
    "type.kpi_hero":    lambda T: (T.size("kpi_hero"), 199),         # kpi_renderer
    "type.kpi_pct":     lambda T: (T.size("kpi_pct"), 88),
    "type.header":      lambda T: (T.size("header"), 20),            # DP §A.4
    "type.body":        lambda T: (T.size("body"), 16),              # BR §6
    "font.family":      lambda T: (T.family, "SB Sans Display"),
    "font.semibold":    lambda T: (T.semibold_face, "SB Sans Display Semibold"),
    "grid.margin":      lambda T: (T.space("margin"), 35),
    "grid.preset_gap":  lambda T: (T.space("preset_gap"), 4),        # flow_renderer PRESET_GAP
    "safe.left":        lambda T: (T.safe["left"], 35),              # flow_renderer SAFE_LEFT
    "safe.right":       lambda T: (T.safe["right"], 1245),
    "safe.top":         lambda T: (T.safe["top"], 140),
    "safe.bottom":      lambda T: (T.safe["bottom"], 660),
    "geom.rounded":     lambda T: (T.geom("rounded", "none"), 0),
    "emu":              lambda T: (T.EMU, 9525),
    "place.header.x":   lambda T: (T.place("header")["x"], 35),
    "place.header.y":   lambda T: (T.place("header")["y"], 38),
    "arrow.width":      lambda T: (T.comp("arrow")["width_pt"], 1.0),
}


def selfcheck():
    T = load_tokens()
    rows, ok = [], True
    for label, fn in _LEGACY_EXPECTED.items():
        actual, expected = fn(T)
        match = actual == expected
        ok = ok and match
        rows.append((label, actual, expected, match))
    return ok, rows


def main():
    if "--check" in sys.argv:
        ok, rows = selfcheck()
        w = max(len(r[0]) for r in rows)
        print("design-tokens parity check (токен vs прежний хардкод):\n")
        for label, actual, expected, match in rows:
            flag = "OK " if match else "MISMATCH"
            print(f"  [{flag}] {label:<{w}}  token={actual!r}  legacy={expected!r}")
        print("\n" + ("ВСЕ ТОКЕНЫ СОВПАДАЮТ — миграция безопасна." if ok
                       else "!!! РАСХОЖДЕНИЕ — токены не равны прежним константам."))
        sys.exit(0 if ok else 1)

    T = load_tokens()
    print("=== Cloud.ru design tokens ===")
    print("family:", T.family, "/", T.semibold_face)
    print("canvas:", T.meta.get("canvas"), "EMU/px:", T.EMU)
    print("safe:", T.safe)
    print("margin:", T.space("margin"), "preset_gap:", T.space("preset_gap"))
    print("rounded.none:", T.geom("rounded", "none"))
    print("header place:", T.place("header"))
    print("colors: Black", T.hex("Black"), "Green", T.hex("Green"),
          "Gray", T.hex("Gray"), "text_gray", T.hex("text_gray"))
    print("roles:")
    for r in ("display", "kpi_hero", "header", "body", "caption"):
        print(f"  {r}: {T.role(r)}  face={T.font_face(r)}")


if __name__ == "__main__":
    main()
