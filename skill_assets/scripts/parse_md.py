#!/usr/bin/env python3
"""
parse_md.py — markdown → classified.json для layout_designer.

Эвристики:
- # H1 → title slide (если первый) или divider
- ## H2 → divider или section title
- ### H3 → content slide (title with body)
- bullet list `- ` или `* ` → body items
- numbered list `1.` `2.` → KPI numbers если есть %, иначе body
- > blockquote → callout
- table → table-content
- ![alt](path) → image_path

Usage:
    python3 parse_md.py <input.md> <output_classified.json>
"""
import sys
import json
import re
import os


def parse_md(text):
    """Parses markdown into list of slides. Each slide = dict for classified.json."""
    lines = text.split("\n")
    slides = []
    current = None
    body_buffer = []
    state = "free"  # free|in_quote|in_table

    def flush_current():
        nonlocal current, body_buffer
        if current is not None:
            if body_buffer:
                current["body"] = body_buffer.copy()
            slides.append(current)
        current = None
        body_buffer = []

    quote_buffer = []
    table_buffer = []  # list of rows, each row = list of cells

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        # H1 = title
        m = re.match(r'^# (.+)$', line)
        if m:
            flush_current()
            title = m.group(1)
            cat = "title_open" if not slides else "divider"
            current = {"num": len(slides) + 1, "category": cat, "tone_hint": "light",
                       "title": title}
            i += 1
            continue

        # H2 = divider OR section title
        m = re.match(r'^## (.+)$', line)
        if m:
            flush_current()
            title = m.group(1)
            current = {"num": len(slides) + 1, "category": "divider", "tone_hint": "dark",
                       "title": title, "number": f"{len(slides)+1:02d}"}
            i += 1
            continue

        # H3 = content slide
        m = re.match(r'^### (.+)$', line)
        if m:
            flush_current()
            title = m.group(1)
            current = {"num": len(slides) + 1, "category": "content_text", "tone_hint": "light",
                       "title": title}
            i += 1
            continue

        # Blockquote → callout
        m = re.match(r'^> (.+)$', line)
        if m:
            quote_buffer.append(m.group(1))
            i += 1
            continue
        else:
            if quote_buffer:
                flush_current()
                current = {"num": len(slides) + 1, "category": "callout", "tone_hint": "light",
                           "title": " ".join(quote_buffer), "body": [" ".join(quote_buffer)]}
                flush_current()
                quote_buffer = []

        # Image
        m = re.match(r'^!\[.*?\]\((.+?)\)', line)
        if m:
            img_path = m.group(1)
            if current is not None:
                current["image_path"] = img_path
            i += 1
            continue

        # Bullet
        m = re.match(r'^[-*] (.+)$', line)
        if m:
            body_buffer.append(m.group(1))
            i += 1
            continue

        # Numbered (treat as body unless KPI-like)
        m = re.match(r'^\d+\.\s+(.+)$', line)
        if m:
            body_buffer.append(m.group(1))
            i += 1
            continue

        # Table row
        if line.strip().startswith("|") and line.strip().endswith("|"):
            row = [c.strip() for c in line.strip("|").split("|")]
            if all(re.match(r'^-+$', c.strip()) for c in row):
                pass  # separator row
            else:
                table_buffer.append(row)
            i += 1
            continue
        else:
            if table_buffer and current is not None:
                current["table_data"] = table_buffer
                current["category"] = "table"
                table_buffer = []

        # Plain paragraph
        if line.strip():
            if current is None:
                current = {"num": len(slides) + 1, "category": "content_text", "tone_hint": "light",
                           "title": line.strip()[:60]}
            else:
                body_buffer.append(line.strip())
        i += 1

    if quote_buffer:
        current = {"num": len(slides) + 1, "category": "callout", "tone_hint": "light",
                   "title": " ".join(quote_buffer), "body": [" ".join(quote_buffer)]}
    flush_current()

    # Auto-classify: if slide has 3+ bullets → 3-col / 4-col
    for s in slides:
        body = s.get("body", [])
        if s["category"] == "content_text" and len(body) >= 4:
            s["category"] = "content_4subtitles"
            s["subtitles"] = [b[:30] for b in body[:4]]
            s["col_bodies"] = body[:4]
        elif s["category"] == "content_text" and len(body) == 3:
            s["category"] = "content_3col"
            s["subtitles"] = [b[:30] for b in body[:3]]
            s["col_bodies"] = body[:3]

    # Add finale
    if slides and slides[-1]["category"] != "logo_finale":
        slides.append({"num": len(slides) + 1, "category": "logo_finale", "tone_hint": "green",
                       "title": "Спасибо!", "subtitle": "Cloud.ru"})

    return slides


def main():
    if len(sys.argv) < 3:
        print("Usage: parse_md.py <input.md> <output_classified.json>", file=sys.stderr)
        sys.exit(1)
    md_path = sys.argv[1]
    out_path = sys.argv[2]

    with open(md_path, encoding="utf-8") as f:
        text = f.read()

    slides = parse_md(text)
    classified = {
        "_source": md_path,
        "_parser": "parse_md.py",
        "slides": slides,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)

    print(f"Parsed {md_path}: {len(slides)} slides → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
