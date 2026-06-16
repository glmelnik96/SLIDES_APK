#!/usr/bin/env python3
"""
validate_plan.py — gate перед build_v6.

Принимает plan.json + donor-slot-map.yaml. Делает:
1. Проверка что каждый donor существует в slot-map
2. Проверка что все slots в плане определены в donor schema
3. Авто-добавление canonical_color/canonical_size_pt/canonical_bold в slot_styles_override
   (даже если автор плана забыл — Layout Designer не должен ошибиться)
4. Флагает overflow: длина текста > safe_max_chars → warning, > max_chars → error
5. Сохраняет validated_plan.json с enriched overrides

Возвращает exit code:
    0 — OK (план готов к build)
    1 — WARN (есть overflow или потенциальные проблемы, но build возможен)
    2 — FAIL (есть критические ошибки, build остановлен)

Usage:
    python3 validate_plan.py <input_plan.json> <slot_map.yaml> [<output_validated_plan.json>]
"""
import sys
import json
import yaml


def load_inputs(plan_path, slot_map_path):
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    with open(slot_map_path, encoding="utf-8") as f:
        smap = yaml.safe_load(f)
    return plan, smap


def validate_slide(slide_idx, slide, donors):
    """Returns (enriched_slide, errors, warnings)."""
    errors, warnings = [], []

    # v0.20+: native slide types не используют clone_from_slide
    # (kpi_native, image_native, chart_native, chart_pptx_native, flow_diagram_native,
    #  table_native — рендерятся через build_v9 с blank donor)
    slide_type = slide.get("slide_type")
    if slide_type in ("kpi_native", "image_native", "chart_native",
                       "chart_pptx_native", "flow_diagram_native", "table_native"):
        # Проверяем наличие соответствующих data блоков
        data_key = {
            "kpi_native": "kpi",
            "image_native": "image",
            "chart_native": "chart",
            "chart_pptx_native": "chart",
            "flow_diagram_native": "flow",
            "table_native": "table",
        }[slide_type]
        if data_key not in slide:
            errors.append(f"slide[{slide_idx}]: slide_type={slide_type} требует поля '{data_key}'")
            return slide, errors, warnings

        # Doer-проверки для flow_diagram_native
        if slide_type == "flow_diagram_native":
            flow = slide.get("flow", {})
            if not flow.get("header"):
                warnings.append(
                    f"slide[{slide_idx}]: flow_diagram_native без header — слайд без заголовка"
                )
            blocks = flow.get("blocks", [])
            if not blocks:
                errors.append(
                    f"slide[{slide_idx}]: flow_diagram_native без blocks — схема пустая"
                )
            is_grid = bool(flow.get("grid"))
            if is_grid:
                # Grid-режим: блоки логические (row/col/lines), геометрию (frame-to-text
                # + сетка) считает рендерер. Пиксельных bounds тут нет.
                cells = set()
                for i, blk in enumerate(blocks):
                    if "row" not in blk or "col" not in blk:
                        errors.append(
                            f"slide[{slide_idx}].blocks[{i}]: grid-блок без row/col"
                        )
                    else:
                        cells.add((blk["row"], blk["col"]))
                # Arrows по [row,col]
                for i, arr in enumerate(flow.get("arrows", [])):
                    for key in ("from", "to"):
                        v = arr.get(key)
                        if isinstance(v, (list, tuple)) and tuple(v) not in cells:
                            errors.append(
                                f"slide[{slide_idx}].arrows[{i}]: {key}={list(v)} "
                                f"не указывает на ячейку грида"
                            )
            else:
                # Явные координаты: bounds + align/vanchor.
                for i, blk in enumerate(blocks):
                    x = blk.get("x", 0); y = blk.get("y", 0)
                    w = blk.get("w", 0); h = blk.get("h", 0)
                    if x < 0 or y < 0 or x + w > 1280 or y + h > 720:
                        warnings.append(
                            f"slide[{slide_idx}].blocks[{i}]: блок вне канваса 1280×720 "
                            f"({x},{y},{w},{h})"
                        )
                    if w < 60 or h < 24:
                        warnings.append(
                            f"slide[{slide_idx}].blocks[{i}]: блок слишком мелкий "
                            f"(w={w}, h={h}) — текст может не уместиться"
                        )
                    blk_align = blk.get("align")
                    if blk_align in ("center", "right"):
                        warnings.append(
                            f"slide[{slide_idx}].blocks[{i}]: align='{blk_align}' "
                            f"нарушает canonical (left+top). Используй только для header-плашек."
                        )
                    if blk.get("vanchor") == "middle":
                        warnings.append(
                            f"slide[{slide_idx}].blocks[{i}]: vanchor='middle' "
                            f"нарушает canonical (left+top). Используй только для header-плашек."
                        )
                block_ids = {b["id"] for b in blocks if "id" in b}
                for i, arr in enumerate(flow.get("arrows", [])):
                    for key in ("from", "to"):
                        v = arr.get(key)
                        if v is not None and not isinstance(v, (list, tuple)) and v not in block_ids:
                            errors.append(
                                f"slide[{slide_idx}].arrows[{i}]: {key}='{v}' "
                                f"не соответствует ни одному block.id"
                            )

        # Doer-проверки для table_native (v1.8)
        if slide_type == "table_native":
            tbl = slide.get("table", {})
            if not tbl.get("header"):
                warnings.append(
                    f"slide[{slide_idx}]: table_native без header — слайд без заголовка"
                )
            headers = tbl.get("headers", [])
            data = tbl.get("data", [])
            if not headers:
                errors.append(
                    f"slide[{slide_idx}]: table_native без headers — таблица без шапки"
                )
            if not data:
                errors.append(
                    f"slide[{slide_idx}]: table_native без data — таблица без строк"
                )
            n_cols = len(headers)
            # Canonical триггер: ≥3 cols × ≥3 rows
            if n_cols < 3:
                warnings.append(
                    f"slide[{slide_idx}]: table_native ≤2 cols (n={n_cols}). "
                    f"Canonical триггер — ≥3 cols. Подумай о multicolumn или KPI."
                )
            if len(data) < 3:
                warnings.append(
                    f"slide[{slide_idx}]: table_native ≤2 data rows (n={len(data)}). "
                    f"Canonical триггер — ≥3 rows."
                )
            # Кол-во ячеек в каждой строке = кол-ву headers
            for i, row in enumerate(data):
                if len(row) != n_cols:
                    errors.append(
                        f"slide[{slide_idx}].data[{i}]: {len(row)} ячеек, "
                        f"ожидалось {n_cols} (по headers)"
                    )
            # Sanity: bounds
            tx = tbl.get("x", 30); ty = tbl.get("y", 170)
            tw = tbl.get("w", 1220); th = tbl.get("h")
            if tx < 0 or tx + tw > 1280:
                warnings.append(
                    f"slide[{slide_idx}]: table выходит за горизонталь канваса "
                    f"({tx}..{tx+tw} vs 0..1280)"
                )
            if th is not None and ty + th > 720:
                warnings.append(
                    f"slide[{slide_idx}]: table выходит за вертикаль канваса "
                    f"({ty+th} vs 720)"
                )

        return slide, errors, warnings

    donor_id = slide.get("clone_from_slide")

    if donor_id not in donors:
        errors.append(f"slide[{slide_idx}]: donor {donor_id} НЕ найден в donor-slot-map.yaml")
        return slide, errors, warnings

    donor = donors[donor_id]
    donor_slots = donor.get("slots", {})

    # plan может иметь slot_styles_override (опционально)
    overrides = dict(slide.get("slot_styles_override", {}))

    plan_slots = slide.get("slots", {})

    for slot_name, content in plan_slots.items():
        if slot_name not in donor_slots:
            warnings.append(
                f"slide[{slide_idx}].{slot_name}: slot НЕ определён в donor {donor_id} schema"
            )
            continue

        donor_slot = donor_slots[slot_name]

        # Проверка длины
        text = content if isinstance(content, str) else (content.get("text", "") if isinstance(content, dict) else "")
        text_len = len(str(text))
        max_chars = donor_slot.get("max_chars")
        # A3: слоты без явного safe_max_chars получают ~70% от max_chars,
        # а не сам потолок (синхронно с graph.donor_map.SAFE_MAX_FALLBACK_RATIO).
        safe_max = donor_slot.get("safe_max_chars") or (int(0.70 * max_chars) if max_chars else None)

        if max_chars and text_len > max_chars:
            # Auto-apply STRATEGY 3 (brand-rules.md §14b): уменьшить размер на 20%
            # для overflow до 30% над max, чтобы текст влез
            overflow_ratio = text_len / max_chars
            if overflow_ratio <= 1.3:
                base_size = donor_slot.get("size_pt", 60)
                # Уменьшаем на ~20-25% (но не ниже 12pt — комфортный минимум, Problem #5)
                reduction_pct = min(25, max(15, int((overflow_ratio - 1.0) * 100 * 1.5)))
                new_size = max(12, round(base_size * (1 - reduction_pct / 100)))
                slot_override = dict(overrides.get(slot_name, {}))
                if "size_pt" not in slot_override:
                    slot_override["size_pt"] = new_size
                    overrides[slot_name] = slot_override
                warnings.append(
                    f"slide[{slide_idx}].{slot_name}: AUTO STRATEGY 3 — overflow {text_len}>{max_chars} "
                    f"(ratio {overflow_ratio:.2f}x), уменьшен размер {base_size}pt → {new_size}pt (-{reduction_pct}%)"
                )
            else:
                errors.append(
                    f"slide[{slide_idx}].{slot_name}: текст {text_len} chars > max {max_chars} (overflow {overflow_ratio:.2f}x). "
                    f">30% — нужен другой donor или split. STRATEGY 3 не применима"
                )
        elif safe_max and text_len > safe_max:
            warnings.append(
                f"slide[{slide_idx}].{slot_name}: текст {text_len} chars > safe {safe_max} (visual risk)"
            )

        # Авто-добавление canonical overrides
        slot_override = dict(overrides.get(slot_name, {}))
        added_fields = []

        if "canonical_color" in donor_slot and "color" not in slot_override:
            slot_override["color"] = donor_slot["canonical_color"]
            added_fields.append(f"color={donor_slot['canonical_color']}")
        if "canonical_size_pt" in donor_slot and "size_pt" not in slot_override:
            slot_override["size_pt"] = donor_slot["canonical_size_pt"]
            added_fields.append(f"size_pt={donor_slot['canonical_size_pt']}")
        if "canonical_bold" in donor_slot and "bold" not in slot_override:
            slot_override["bold"] = donor_slot["canonical_bold"]
            added_fields.append(f"bold={donor_slot['canonical_bold']}")

        if slot_override and slot_name not in overrides:
            overrides[slot_name] = slot_override
        elif slot_override and slot_name in overrides:
            overrides[slot_name].update(slot_override)

        if added_fields:
            warnings.append(
                f"slide[{slide_idx}].{slot_name}: auto-added canonical " + ", ".join(added_fields)
            )

    enriched = dict(slide)
    if overrides:
        enriched["slot_styles_override"] = overrides

    # Проверка canonical_rule на уровне donor (если описан)
    if "canonical_rule" in donor:
        warnings.append(
            f"slide[{slide_idx}] (donor {donor_id}): применено canonical правило — '{donor['canonical_rule'][:80]}'"
        )

    return enriched, errors, warnings


def main():
    if len(sys.argv) < 3:
        print("Usage: validate_plan.py <plan.json> <slot_map.yaml> [<output.json>]", file=sys.stderr)
        sys.exit(2)

    plan_path = sys.argv[1]
    smap_path = sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else None

    plan, smap = load_inputs(plan_path, smap_path)
    donors = smap.get("donors", {})

    enriched_slides = []
    all_errors, all_warnings = [], []

    for i, slide in enumerate(plan.get("slides", []), 1):
        e_slide, errs, warns = validate_slide(i, slide, donors)
        enriched_slides.append(e_slide)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    enriched_plan = dict(plan)
    enriched_plan["slides"] = enriched_slides
    enriched_plan["_validated_by"] = "validate_plan.py v0.9"
    enriched_plan["_validation"] = {
        "errors": all_errors,
        "warnings": all_warnings,
        "verdict": "FAIL" if all_errors else ("WARN" if all_warnings else "OK"),
    }

    print("=" * 60, file=sys.stderr)
    print(f"Plan validation — {plan_path}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"Verdict: {enriched_plan['_validation']['verdict']}", file=sys.stderr)
    print(f"Slides: {len(enriched_slides)}, Errors: {len(all_errors)}, Warnings: {len(all_warnings)}", file=sys.stderr)
    print(file=sys.stderr)

    for err in all_errors:
        print(f"  ❌ {err}", file=sys.stderr)
    for w in all_warnings[:30]:  # limit verbose output
        print(f"  ⚠️  {w}", file=sys.stderr)
    if len(all_warnings) > 30:
        print(f"  ... +{len(all_warnings) - 30} more warnings", file=sys.stderr)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(enriched_plan, f, ensure_ascii=False, indent=2)
        print(f"\nEnriched plan → {out_path}", file=sys.stderr)

    sys.exit(2 if all_errors else (1 if all_warnings else 0))


if __name__ == "__main__":
    main()
