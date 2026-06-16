#!/usr/bin/env python3
"""
validate_deck.py — структурный валидатор .pptx, ловит порчу, из-за которой
PowerPoint показывает «обнаружена проблема с содержимым» / «не удалось прочитать
часть содержимого» (а LibreOffice и наивные проверки её пропускают).

Проверяет (по опыту бага 2026-06-02 с clone_slide):
  1) ORPHAN-слайды         — slideN.xml есть в пакете, но НЕ в presentation (sldIdLst).
  2) IMAGE-MISMATCH        — <a:blip r:embed="rIdX"> указывает НЕ на media-часть
                             (напр. на slideLayout) — главная причина «слетают картинки».
  3) DANGLING rId          — XML ссылается на rId, которого нет в .rels части.
  4) MISSING TARGET        — .rels указывает на часть, которой нет в пакете.
  5) STRAY NOTES           — notesSlide ссылается на слайд, которого нет в presentation.
  6) CONTENT-TYPE          — часть без объявленного content-type.

CLI:
  python3 validate_deck.py file.pptx [...]   # exit 0 если чисто, иначе 1
API:
  from validate_deck import validate_pptx; problems = validate_pptx(path)
"""
import sys
import re
import zipfile
import posixpath
import xml.etree.ElementTree as ET

CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_SLIDE_RE = re.compile(r"ppt/slides/slide\d+\.xml$")
_NOTES_RE = re.compile(r"ppt/notesSlides/notesSlide\d+\.xml$")
_EMBED_RE = re.compile(r'r:embed="([^"]+)"')
_ANYREF_RE = re.compile(r'r:(?:embed|id|link|pict)="([^"]+)"')


def _rels_of(z, part, names):
    relp = posixpath.dirname(part) + "/_rels/" + posixpath.basename(part) + ".rels"
    if relp not in names:
        return {}
    out = {}
    for r in ET.fromstring(z.read(relp)):
        out[r.get("Id")] = (r.get("Target"), r.get("TargetMode"))
    return out


def validate_pptx(path):
    """→ list[str] проблем (пустой = чисто)."""
    problems = []
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    # content-types
    ct = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {e.get("Extension").lower() for e in ct.findall(f"{{{CT_NS}}}Default")}
    overrides = {e.get("PartName") for e in ct.findall(f"{{{CT_NS}}}Override")}

    # presentation slide set
    pres_rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "ignore")
    in_pres = {"ppt/" + t for t in re.findall(r'Target="(slides/slide\d+\.xml)"', pres_rels)}

    slides = sorted(n for n in names if _SLIDE_RE.match(n))

    # 1) orphan slides
    for s in slides:
        if s not in in_pres:
            problems.append(f"ORPHAN slide: {s} (в пакете, но не в presentation)")

    # 2/3) per-slide: image-mismatch + dangling
    for s in slides:
        raw = z.read(s).decode("utf-8", "ignore")
        rels = _rels_of(z, s, names)
        for rid in set(_EMBED_RE.findall(raw)):  # blip embeds → должны быть media
            tgt = rels.get(rid, (None, None))[0]
            if tgt is None:
                problems.append(f"DANGLING rId: {s} ссылается на {rid}, нет в .rels")
            elif "media/" not in tgt:
                problems.append(f"IMAGE-MISMATCH: {s} r:embed {rid} -> {tgt} (не media!)")
        for rid in set(_ANYREF_RE.findall(raw)):
            if rid not in rels:
                problems.append(f"DANGLING rId: {s} ссылается на {rid}, нет в .rels")

    # 4) missing targets across ALL rels
    for n in [x for x in names if x.endswith(".rels")]:
        base = posixpath.dirname(posixpath.dirname(n))
        for r in ET.fromstring(z.read(n)):
            if r.get("TargetMode") == "External":
                continue
            res = posixpath.normpath(posixpath.join(base, r.get("Target")))
            if res not in names:
                problems.append(f"MISSING TARGET: {n}: {r.get('Id')} -> {r.get('Target')}")

    # 5) stray notes → absent slide
    for n in [x for x in names if x.startswith("ppt/notesSlides/_rels/")]:
        for r in ET.fromstring(z.read(n)):
            t = r.get("Target") or ""
            if "slides/slide" in t:
                res = "ppt/" + t.replace("../", "")
                if res not in in_pres:
                    problems.append(f"STRAY NOTES: {n} -> {t} (слайд не в presentation)")

    # 6) content-type coverage
    for n in names:
        if n.endswith("/") or n == "[Content_Types].xml":
            continue
        ext = n.rsplit(".", 1)[-1].lower() if "." in n else ""
        if ("/" + n) in overrides:
            continue
        if ext and ext not in defaults:
            problems.append(f"NO CONTENT-TYPE: {n} (.{ext})")

    return problems


def main():
    rc = 0
    for path in sys.argv[1:]:
        probs = validate_pptx(path)
        name = posixpath.basename(path)
        if probs:
            rc = 1
            print(f"❌ {name}: {len(probs)} проблем(ы)")
            for p in probs[:50]:
                print("   -", p)
        else:
            print(f"✅ {name}: структурно чисто")
    sys.exit(rc)


if __name__ == "__main__":
    main()
