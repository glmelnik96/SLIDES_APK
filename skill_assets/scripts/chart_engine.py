#!/usr/bin/env python3
"""
chart_engine.py — Cloud.ru canonical-palette chart redraw via matplotlib.

Зачем: в исходных draft часто графики с не-канонической палитрой (красные,
синие, бежевые). Canonical §3 требует «свето-серый + фирменный зелёный».
Эта функция перерисовывает chart через matplotlib с Cloud.ru palette →
сохраняет как PNG → вставляется как image-content.

Canonical palette:
  background: #FFFFFF (white)
  text: #222222 (graphite)
  primary lines/areas: #222222 (graphite)
  accent: #26D07C (green)
  secondary fills: #F2F2F2, #C0E0FC (light blue), #A8E5C9 (light green tint)
  hero highlight: #26D07C bold

Supported chart types:
  - area_stacked
  - bar
  - line
  - pie
"""
import os
import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches


# Canonical palette
GRAPHITE = "#222222"
GREEN = "#26D07C"
WHITE = "#FFFFFF"
LIGHT_GRAY = "#F2F2F2"
LIGHT_BLUE = "#C0E0FC"
LIGHT_GREEN_TINT = "#A8E5C9"
DARK_GRAY = "#666666"

# CANONICAL palette для charts — изучено по эталонам шаблона slides 45/46/47/50.
# Slide 45 (главный эталон bar chart с 5 рядами) использует:
# - Ряд 1: GREEN — accent / hero
# - Ряд 2: pastel light blue (canonical Blue #C0E0FC из brand-rules §2)
# - Ряд 3: pastel aquamarine (Aquamarine 2 #C9F2EA из brand-rules)
# - Ряд 4: pastel light yellow (приглушённый Yellow)
# - Ряд 5: pastel light purple (приглушённый Magenta 3)
# ВСЕ pastel приглушённые — НЕ saturated.
# canonical §2: «использовать можно только палитру второго и третьего ряда. Насыщенные цвета НЕ используются»
SERIES_COLORS_CANONICAL = [
    DARK_GRAY,          # series 1: graphite-like для commodity/legacy (canonical эталон 50)
    "#C9F2EA",          # series 2: pastel aquamarine — приглушённый mid (transitional)
    GREEN,              # series 3 / accent: brand GREEN #26D07C — hero
    "#A8E5C9",          # series 4: backup pastel green
    "#C0E0FC",          # series 5: backup canonical Blue
    "#E8F5C7",          # series 6: backup pastel yellow
]
# Default — canonical
SERIES_COLORS = SERIES_COLORS_CANONICAL


def setup_canonical_axes(ax):
    """Apply Cloud.ru canonical aesthetics to axes."""
    ax.set_facecolor(WHITE)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRAPHITE)
    ax.spines["bottom"].set_color(GRAPHITE)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(colors=GRAPHITE, labelsize=10)
    ax.grid(axis="y", color=LIGHT_GRAY, linestyle="-", linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)


def render_area_stacked(chart_config, output_path, dpi=120):
    """
    Area chart по эталону slide 50 шаблона: DARK background + semi-transparent
    overlapping series (НЕ stacked!) + canonical зелёные/синие.
    chart_config может задать "dark": False для light variant.
    NB: title задаётся снаружи (slide_title через image_renderer).
    """
    is_dark = chart_config.get("dark", True)  # default DARK (как эталон 50)
    bg = GRAPHITE if is_dark else WHITE
    text_color = WHITE if is_dark else GRAPHITE
    grid_color = "#444444" if is_dark else LIGHT_GRAY

    fig, ax = plt.subplots(figsize=(12.4, 5.6), dpi=dpi)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    # Canonical axes для dark/light
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(text_color)
    ax.spines["bottom"].set_color(text_color)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(colors=text_color, labelsize=10)
    ax.grid(axis="y", color=grid_color, linestyle="-", linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)

    x = chart_config["x"]
    series = chart_config["series"]
    accent_idx = chart_config.get("accent_idx", -1)

    # Эталон slide 50 palette: deep blue + green 2 (light tint) + green hero
    DARK_AREA_PALETTE = [DARK_GRAY if not is_dark else "#888888",  # secondary muted
                         "#5DDFA8",                                 # green 2 (lighter)
                         GREEN]                                     # main green hero

    # Render OVERLAPPING (НЕ stacked — как эталон 50)
    for i, s in enumerate(series):
        is_accent = (i == accent_idx)
        if is_accent:
            color = GREEN
        elif i < len(DARK_AREA_PALETTE):
            color = DARK_AREA_PALETTE[i]
        else:
            color = SERIES_COLORS[min(i, len(SERIES_COLORS) - 1)]
        ax.fill_between(x, s["data"], alpha=0.7, color=color,
                        label=s["name"], edgecolor="none", zorder=2 + i)

    if chart_config.get("x_label"):
        ax.set_xlabel(chart_config["x_label"], fontsize=11, color=text_color)
    if chart_config.get("y_label"):
        ax.set_ylabel(chart_config["y_label"], fontsize=11, color=text_color)

    # Legend top-left маленькая с square markers (как эталон 50)
    ax.legend(loc="upper left", frameon=False, fontsize=10,
              labelcolor=text_color, ncol=1)

    plt.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=bg, edgecolor="none")
    plt.close(fig)
    return output_path


def render_bar(chart_config, output_path, dpi=120):
    """Simple vertical bar chart. Title задаётся снаружи (slide_title)."""
    fig, ax = plt.subplots(figsize=(12.4, 5.6), dpi=dpi)
    setup_canonical_axes(ax)

    labels = chart_config["labels"]
    values = chart_config["values"]
    accent_idx = chart_config.get("accent_idx", -1)

    colors = [GREEN if i == accent_idx else GRAPHITE for i in range(len(values))]

    ax.bar(labels, values, color=colors, edgecolor=WHITE, linewidth=1)

    if chart_config.get("x_label"):
        ax.set_xlabel(chart_config["x_label"], fontsize=11, color=GRAPHITE)
    if chart_config.get("y_label"):
        ax.set_ylabel(chart_config["y_label"], fontsize=11, color=GRAPHITE)

    plt.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=WHITE, edgecolor="none")
    plt.close(fig)
    return output_path


def render_line(chart_config, output_path, dpi=120):
    """Light line chart — canonical Cloud.ru: hero green толстая линия + графит/grey тонкие.
    Title задаётся снаружи (slide_title через image_renderer)."""
    fig, ax = plt.subplots(figsize=(12.4, 5.6), dpi=dpi)
    setup_canonical_axes(ax)

    x = chart_config["x"]
    series = chart_config["series"]
    accent_idx = chart_config.get("accent_idx", -1)

    # Canonical line palette: hero GREEN + graphite + light gray
    LINE_COLORS = [DARK_GRAY, "#999999", "#BBBBBB", "#DDDDDD"]

    for i, s in enumerate(series):
        is_accent = (i == accent_idx)
        if is_accent:
            color = GREEN
            linewidth = 4
            zorder = 10  # accent поверх
        else:
            color = LINE_COLORS[min(i, len(LINE_COLORS) - 1)]
            linewidth = 2
            zorder = 5
        ax.plot(x, s["data"], label=s["name"], color=color,
                linewidth=linewidth, zorder=zorder)

    if chart_config.get("x_label"):
        ax.set_xlabel(chart_config["x_label"], fontsize=11, color=GRAPHITE)
    if chart_config.get("y_label"):
        ax.set_ylabel(chart_config["y_label"], fontsize=11, color=GRAPHITE)
    ax.legend(loc="upper left", frameon=False, fontsize=11, labelcolor=GRAPHITE)

    plt.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=WHITE, edgecolor="none")
    plt.close(fig)
    return output_path


def render_pie(chart_config, output_path, dpi=120):
    """Pie chart с canonical palette + accent green slice."""
    fig, ax = plt.subplots(figsize=(8, 6), dpi=dpi)
    fig.patch.set_facecolor(WHITE)

    labels = chart_config["labels"]
    values = chart_config["values"]
    accent_idx = chart_config.get("accent_idx", -1)

    colors = []
    for i in range(len(values)):
        if i == accent_idx:
            colors.append(GREEN)
        else:
            colors.append(SERIES_COLORS[min(i, len(SERIES_COLORS) - 1)])

    explode = [0.05 if i == accent_idx else 0 for i in range(len(values))]

    wedges, texts, autotexts = ax.pie(
        values, labels=labels, colors=colors, explode=explode,
        autopct="%1.0f%%", startangle=90,
        textprops={"fontsize": 11, "color": GRAPHITE},
        wedgeprops={"edgecolor": WHITE, "linewidth": 2}
    )
    for autotext in autotexts:
        autotext.set_color(GRAPHITE)
        autotext.set_fontweight("bold")

    ax.axis("equal")
    plt.tight_layout()
    fig.savefig(output_path, dpi=dpi, bbox_inches="tight",
                facecolor=WHITE, edgecolor="none")
    plt.close(fig)
    return output_path


def render_chart(chart_config, output_path, dpi=120):
    """Dispatcher по chart_type."""
    chart_type = chart_config.get("type", "bar")
    if chart_type == "area_stacked":
        return render_area_stacked(chart_config, output_path, dpi)
    elif chart_type == "bar":
        return render_bar(chart_config, output_path, dpi)
    elif chart_type == "line":
        return render_line(chart_config, output_path, dpi)
    elif chart_type == "pie":
        return render_pie(chart_config, output_path, dpi)
    else:
        raise ValueError(f"Unsupported chart type: {chart_type}")


# Test standalone — slide_graph use case
if __name__ == "__main__":
    config = {
        "type": "area_stacked",
        "title": "Структурные сдвиги занятости (1850-2050)",
        "x": [1850, 1875, 1900, 1925, 1950, 1975, 2000, 2025, 2050],
        "x_label": "Год",
        "y_label": "Доля занятости (%)",
        "accent_idx": 3,   # Реляционный сектор — green hero
        "series": [
            {"name": "Сельское хозяйство", "data": [45, 40, 30, 20, 10, 5, 3, 2, 2]},
            {"name": "Промышленность", "data": [25, 30, 40, 45, 35, 25, 18, 12, 8]},
            {"name": "Услуги", "data": [25, 25, 25, 30, 50, 65, 70, 65, 50]},
            {"name": "Реляционный сектор", "data": [3, 3, 3, 3, 3, 3, 7, 18, 35]},
            {"name": "Другое", "data": [2, 2, 2, 2, 2, 2, 2, 3, 5]},
        ]
    }
    out = "pptx-skill/output/chart_engine_test.png"
    render_chart(config, out)
    print(f"Saved {out}")
