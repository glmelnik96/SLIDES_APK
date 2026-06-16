"""Deterministic classification of a parsed slide into a routing class.

Classes:
  none        — ordinary text slide; feature does not intervene.
  structured  — grouped text nodes forming a diagram → flow_diagram_native (C).
  raster      — slide carries a content-sized embedded picture → image (A).
  opaque      — visual slide with neither text structure nor raster → image (B).

Pure function — no I/O, no LLM. Thresholds are named constants.
"""

EMU_PER_PX = 9525

RASTER_MIN_AREA_PX = 200 * 200      # smaller is an icon/logo, not content
STRUCTURED_MIN_NODES = 3            # fewer is not a diagram
STRUCTURED_MAX_NODES = 8            # more does not fit presets → render whole (B)

# Standard 16:9 slide geometry in EMU (12192000 x 6858000 = 1280 x 720 px-at-96).
# Used as a fallback when slide_data carries no explicit ``slide_size`` (e.g. unit
# fixtures); parse_pptx now stamps the real deck dimensions onto each slide.
DEFAULT_SLIDE_W_EMU = 12192000
DEFAULT_SLIDE_H_EMU = 6858000

# A hybrid (text + image) slide is routed to "raster" only when the largest image
# covers at least this fraction of the slide. Calibration: a 200x200 px icon on a
# 1280x720 slide is ~4.3%; a typical screenshot/diagram fills a third to a half of
# the slide (33–50%). 0.22 sits comfortably above inline icons/logos yet below any
# genuine content image, so small decorations never hijack a real text slide while
# dominant visuals are recovered instead of being silently dropped.
DOMINANT_RASTER_FRACTION = 0.22


def _has_normal_text(slide_data):
    title = (slide_data.get("title") or "").strip()
    body = [b for b in (slide_data.get("body") or []) if str(b).strip()]
    return bool(title) or len(body) >= 1


def _largest_image_area_px(slide_data):
    best = 0
    for im in slide_data.get("images") or []:
        w = im.get("width_emu") or 0
        h = im.get("height_emu") or 0
        area = (w / EMU_PER_PX) * (h / EMU_PER_PX)
        best = max(best, area)
    return best


def _slide_area_px(slide_data):
    size = slide_data.get("slide_size") or {}
    w_emu = size.get("width_emu") or DEFAULT_SLIDE_W_EMU
    h_emu = size.get("height_emu") or DEFAULT_SLIDE_H_EMU
    return (w_emu / EMU_PER_PX) * (h_emu / EMU_PER_PX)


def classify_visual_kind(slide_data):
    nodes = [n for n in (slide_data.get("group_nodes") or [])
             if str(n.get("text", "")).strip()]
    largest_px = _largest_image_area_px(slide_data)
    has_raster = largest_px >= RASTER_MIN_AREA_PX
    slide_px = _slide_area_px(slide_data)
    # "Dominant" still requires the absolute min-area gate so pathological
    # tiny-slide math can't promote a sub-icon image.
    dominant = has_raster and slide_px > 0 \
        and (largest_px / slide_px) >= DOMINANT_RASTER_FRACTION

    if _has_normal_text(slide_data):
        # A text slide carrying a genuinely dominant image (screenshot/diagram)
        # would otherwise drop the image entirely; recover it as a raster
        # (title + big image) which is strictly better than losing the visual.
        # Small/decorative images stay "none" so ordinary text slides keep their
        # native text layout and are not hijacked.
        return "raster" if dominant else "none"
    if STRUCTURED_MIN_NODES <= len(nodes) <= STRUCTURED_MAX_NODES:
        return "structured"
    if has_raster:
        return "raster"
    return "opaque"
