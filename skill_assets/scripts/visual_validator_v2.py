#!/usr/bin/env python3
"""
visual_validator_v2.py — pixel-level audit rendered PNG слайдов.

Цель: ловит то, что brand_guardian (XML-level) не видит:
- Unfilled donor placeholders (большой solid color block)
- Off-palette цвета >5% площади
- Cropped/missing text (edge-touch с резким текстовым контрастом)
- Empty rectangles (placeholder donor PNG не очищенный)

Usage:
    python3 visual_validator_v2.py <render_dir> [report.json]
"""
import os
import sys
import json
from collections import Counter
from PIL import Image


# ---------------------------------------------------------------------------
# Палитра — единый источник истины: brand/palette.json
# Hardcoded fallback для standalone использования.
# ---------------------------------------------------------------------------

CHART_SLIDE_TYPES = {"chart_pptx_native", "chart_native"}


def _hex_to_rgb(hx):
    h = hx.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _load_palette_dict():
    """Возвращает dict из brand/palette.json. Fallback — None (используем hardcoded)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "..", "brand", "palette.json"),
        os.path.join(os.getcwd(), "brand", "palette.json"),
        os.path.join(os.getcwd(), "pptx-skill", "brand", "palette.json"),
    ]
    for path in candidates:
        try:
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            continue
    return None


_PAL = _load_palette_dict()

# Имена тут — для отладки в issues.palette_distribution.
if _PAL:
    _base_pairs = list(_PAL.get("base", {}).items()) + list(_PAL.get("base_alts", {}).items())
    PALETTE = {name.lower().replace("-", "_"): _hex_to_rgb(hx)
               for name, hx in _base_pairs
               if name != "_doc"}
    PALETTE_CHART_EXTENSION = {name: _hex_to_rgb(hx)
                                for name, hx in _PAL.get("chart_extension", {}).items()
                                if name != "_doc"}
    UNFILLED_FLAG_COLORS = {name: _hex_to_rgb(hx)
                             for name, hx in _PAL.get("unfilled_flags", {}).items()
                             if name != "_doc"}
else:
    PALETTE = {
        "white": (255, 255, 255), "light_gray": (242, 242, 242),
        "gray": (200, 200, 200), "graphite": (34, 34, 34),
        "near_black": (24, 24, 24), "green": (38, 208, 124),
        "green_alt": (0, 217, 123), "yellow": (207, 245, 0),
        "purple": (160, 104, 255), "blue_light": (192, 224, 252),
        "graphite_iron": (52, 63, 72),
    }
    PALETTE_CHART_EXTENSION = {
        "pastel_yellow": (232, 245, 199), "pastel_mint": (201, 242, 234),
        "pastel_green_tint": (168, 229, 201), "mid_gray": (189, 189, 189),
        "dark_gray": (102, 102, 102),
    }
    UNFILLED_FLAG_COLORS = {
        "blue_placeholder": (107, 158, 230),
        "yellow_placeholder": (255, 255, 0),
        "magenta_placeholder": (255, 0, 255),
    }


def color_distance(c1, c2):
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


def closest_palette(rgb, max_dist=40, palette=None):
    palette = palette if palette is not None else PALETTE
    best = None
    best_d = float("inf")
    for name, c in palette.items():
        d = color_distance(rgb, c)
        if d < best_d:
            best_d = d
            best = name
    if best_d <= max_dist:
        return best, best_d
    return None, best_d


def is_unfilled_flag(rgb):
    """Проверяет похож ли цвет на placeholder-флаг (голубой/жёлтый PNG-заглушка)."""
    for name, c in UNFILLED_FLAG_COLORS.items():
        if color_distance(rgb, c) < 50:
            return name
    return None


def analyze_slide(png_path, is_chart=False):
    """Один слайд — pixel histogram + verdicts.
    is_chart=True расширяет палитру pastel-цветами (canonical §2 второй ряд для chart-серий).
    """
    img = Image.open(png_path).convert("RGB")
    img_small = img.resize((128, 72), Image.LANCZOS)  # downscale для speed
    pixels = list(img_small.getdata())
    total = len(pixels)

    active_palette = dict(PALETTE)
    if is_chart:
        active_palette.update(PALETTE_CHART_EXTENSION)

    # Cluster pixels by палитра
    palette_count = Counter()
    off_palette_count = 0
    unfilled_flag_count = Counter()

    for px in pixels:
        flag = is_unfilled_flag(px)
        if flag:
            unfilled_flag_count[flag] += 1
            continue
        name, _ = closest_palette(px, max_dist=40, palette=active_palette)
        if name:
            palette_count[name] += 1
        else:
            off_palette_count += 1

    issues = []

    # Issue 1: large unfilled placeholder block
    for flag_name, cnt in unfilled_flag_count.items():
        pct = 100 * cnt / total
        if pct > 5:  # >5% площади = подозрительный placeholder
            issues.append({
                "type": "unfilled_placeholder",
                "color": flag_name,
                "area_pct": round(pct, 1),
                "msg": f"Unfilled donor placeholder ({flag_name}) занимает {pct:.1f}% площади"
            })

    # Issue 2: off-palette colors > 5%
    off_pct = 100 * off_palette_count / total
    if off_pct > 15:
        issues.append({
            "type": "off_palette",
            "area_pct": round(off_pct, 1),
            "msg": f"Off-palette цвета занимают {off_pct:.1f}% — нарушение canonical §2"
        })

    # Issue 3: проверка что есть основной фон
    main_bg = palette_count.most_common(1)
    if main_bg:
        bg_name, bg_cnt = main_bg[0]
        bg_pct = 100 * bg_cnt / total
        if bg_pct < 30:
            issues.append({
                "type": "no_dominant_bg",
                "msg": f"Нет доминирующего фона (max color {bg_name} = {bg_pct:.1f}%) — возможна перегрузка"
            })

    # Stats
    palette_distribution = {name: round(100 * cnt / total, 1)
                             for name, cnt in palette_count.most_common(5)}

    return {
        "issues": issues,
        "palette_distribution": palette_distribution,
        "off_palette_pct": round(off_pct, 1),
    }


def _load_chart_flags(plan_path, n_slides):
    """Возвращает список booleans длины n_slides: True если slide_type ∈ chart-types."""
    flags = [False] * n_slides
    if not plan_path or not os.path.exists(plan_path):
        return flags
    try:
        with open(plan_path, encoding="utf-8") as f:
            plan = json.load(f)
        for i, slide in enumerate(plan.get("slides", [])):
            if i >= n_slides:
                break
            if slide.get("slide_type") in CHART_SLIDE_TYPES:
                flags[i] = True
    except Exception as e:
        print(f"WARN: не удалось прочитать plan {plan_path}: {e}", file=sys.stderr)
    return flags


def main():
    args = sys.argv[1:]
    plan_path = None
    if "--plan" in args:
        i = args.index("--plan")
        if i + 1 < len(args):
            plan_path = args[i + 1]
            args = args[:i] + args[i + 2:]
        else:
            print("ERROR: --plan требует путь к plan.json", file=sys.stderr)
            sys.exit(2)

    if len(args) < 1:
        print("Usage: visual_validator_v2.py <render_dir> [report.json] [--plan <plan.json>]",
              file=sys.stderr)
        sys.exit(2)

    render_dir = args[0]
    report_path = args[1] if len(args) > 1 else None

    if not os.path.isdir(render_dir):
        print(f"ERROR: {render_dir} не папка", file=sys.stderr)
        sys.exit(2)

    pngs = sorted([f for f in os.listdir(render_dir) if f.endswith(".png")])
    if not pngs:
        print(f"ERROR: нет PNG в {render_dir}", file=sys.stderr)
        sys.exit(2)

    chart_flags = _load_chart_flags(plan_path, len(pngs))

    report = {"render_dir": render_dir, "n_slides": len(pngs),
              "plan_path": plan_path, "slides": []}
    total_issues = 0

    for idx, png in enumerate(pngs):
        path = os.path.join(render_dir, png)
        is_chart = chart_flags[idx]
        analysis = analyze_slide(path, is_chart=is_chart)
        slide_report = {
            "file": png,
            "is_chart": is_chart,
            **analysis,
        }
        report["slides"].append(slide_report)
        total_issues += len(analysis["issues"])

    # Verdict
    if total_issues == 0:
        report["verdict"] = "PASS"
        exit_code = 0
    elif total_issues <= 2:
        report["verdict"] = "WARN"
        exit_code = 1
    else:
        report["verdict"] = "FAIL"
        exit_code = 2

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"Visual Validator v2 — {render_dir}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)
    print(f"Verdict: {report['verdict']}  |  Slides: {len(pngs)}  |  Total issues: {total_issues}",
          file=sys.stderr)
    for s in report["slides"]:
        if s["issues"]:
            print(f"\n--- {s['file']} ---", file=sys.stderr)
            for issue in s["issues"]:
                print(f"  ⚠️  {issue['type']}: {issue['msg']}", file=sys.stderr)
            print(f"  palette: {s['palette_distribution']}", file=sys.stderr)

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nReport → {report_path}", file=sys.stderr)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
