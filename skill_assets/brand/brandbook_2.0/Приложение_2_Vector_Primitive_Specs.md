
# Cloud.ru 2.0 Brand-Book — Vector Primitive Specs

**Calibration:** The PDF is 49 pages; PDF page = logical page **1:1** (no offset/factor). Page canvas is **1920×1080 pt**, so 1 pt ≈ 1 px — all geometry below is in px. Line illustrations (cloud/star/arrow) are embedded raster images, so their stroke widths come from the stated rules, not vector data; the Портал, grid, and color shapes are native vectors I measured directly.

---

## 1. Портал (stepped black graphic) — pp.29–33

The marquee element. **NOT pixel-cubes** — it is identical squares duplicated with a small up-right offset, producing a staircase silhouette.

**Construction (measured vector rects, p.29):**
- **Base unit = a perfect SQUARE.** Single square measured 387×387 px (left example) and 414×414 px (right example) — size is arbitrary, the constraint is square (1:1).
- **2-rect "portal" (middle):** two 387×387 squares; 2nd offset **(+103 px right, −26 px up)** from the 1st.
- **3-rect staircase (right):** three 414×414 squares; each offset **(+93 px right, −30 px up)** from the previous.
- **Offset rule (derived):** horizontal step ≈ **22–27% of side**, vertical step ≈ **7% of side**; horizontal:vertical step ratio ≈ **3:1 to 4:1**. Steps go **up-and-to-the-right** (top-left grows up in steps, bottom-right recedes down in steps).
- **Color: #222222** (measured fill 0.13,0.13,0.13 = brand "Black"). NOTE: the prior analysis said #0E0E0E — the actual brand-book fill is **#222222**, not #0E0E0E. Use #222222.
- **Transforms (p.30):** rotate **90° and 180° only**; mirror vertical and horizontal. Dynamic compositions may mix squares of **different sizes** (p.30 bottom).
- **Usage (p.32):** portal serves as a backing plate (подложка) OR as an image container/mask. Scale & symmetry set per-layout.
- Right angles only, square corners (no rounding) — consistent with Look&feel p.4.

**Primitive recipe:** draw N identical squares (python-pptx rectangles), each translated by (+0.24·side, −0.075·side) relative to previous, z-ordered so later squares sit on top; fill #222222, no line.

---

## 2. Sparkle-облако / cloud + stars — p.37 (cover/templates p.1,5)

Outline (stroke-only) geometric icons. Three core symbols:
- **Облако (Cloud)** = brand symbol. Outline cloud, **flat bottom edge**, bumps are **geometric/gротеск (angular, not soft humps)**, **square stroke caps & corners** (no round caps — per p.4 rule "Окончание штриха квадратное").
- **Звезда / sparkle (magic)** = AI symbol. **4-pointed star with concave (curved-inward) sides** — classic sparkle. Outline stroke. Can be grouped ("звёзды в сборе").
- Two clouds together = cloud-infrastructure symbol (recommend single shared color).

**Stroke weight:** **multiples of micromodule = 2, 4, 6 … px**; **1 px allowed only as exception** for very small icons (p.37 text). No numeric size for the shapes themselves — scalable; size per layout.
**Color:** any single brand-palette color; max **2 colors** per illustration (one base + one contrast).

---

## 3. Сетка / микромодуль — pp.24–25

- **Micromodule = 2 px.** Every internal spacing/offset MUST be a multiple of 2 px (divisible by 2). Stated explicitly, strict.
- **Margins from sheet edge: multiples of 10 px** — i.e. **10, 20, 30, 40 …** px (the four shown examples). Edge padding must be round 10s; non-10 edge margins "не приемлемо."
- **Layout corner markers:** small square guides at each corner (shown pink in book) — corner registration squares define the content frame.
- **Column grid:** vertical column guides (multi-column) shown on portrait & landscape frames (p.24 right, p.25). Number of columns not numerically fixed; gutters follow micromodule.
- Square-module composition: square side **multiple of 10 px** for easy math (p.28).

**Rule of thumb for the engine:** snap all paddings to 2 px; snap outer margins to 10 px; default outer margin ~40–80 px on a 1920×1080 canvas reads as on-brand.

---

## 4. Паттерны — pp.26–28

- **Two pattern families:**
  1. **Dot/grid fills on DARK tiles** (p.26 left): base **#222222** tiles overlaid with very fine, low-contrast **grid lines / dot matrix**.
  2. **Color accent fills** (p.26 right): solid brand-palette blocks (Green #26D07C, Yellow #CFF500, Purple #A068FF, Blue #C0E0FC) each overlaid with a **fine grid pattern**.
- **Linear pattern** (p.27, and in logoblock p.13–14): array of **thin vertical bars/lines**, evenly spaced.
- **Density/scale:** element size must be a **multiple of the micromodule (2 px)**; square cells often multiple of 10 px (p.28). Recolor allowed within base + extended palettes only.
- **Where:** for branding surfaces, backgrounds, and building complex "portal + pattern" compositions. Color choice must preserve the tech image; do not mix base + extended palette carelessly (p.19).

---

## 5. Типографика — pp.20–23

**Family: SB Sans** (sub-families used by content type):
- **SB Sans Display** — Regular + SemiBold → **headings, big numbers.**
- **SB Sans Text** — Regular + Medium + SemiBold → **subheads, body text.**
- **SB Sans Interface** — Regular + SemiBold → **infographics** (as needed).
- Fallbacks: **Verdana** (system fallback when SB Sans unavailable); **SimSun / PingFang** for CJK only.

**Explicit metrics (p.21–23, "Значения для Figma"):**
- **Heading:Subhead pairing** = SB Sans Display SemiBold (heading) over SB Sans Text Regular (subhead).
- **Line height (leading) standard:** **120% of font size** (normal set).
- **Dense set:** leading = **100%** (equal to font size) + **negative tracking −2** (letter-spacing).
- **Heading tracking:** **0** (normal), subhead 0.
- **Big numbers:** SB Sans Display Reg/SemiBold, leading **100%**, **negative tracking −4 to −7** (numbers must use negative tracking; use largest possible point size for impact).
- **Spacing between text blocks:** = **cap-height of the heading** (i.e. block gap equals the height of the heading's capital letter).
- No absolute pt sizes are given (it's a ratio/Figma system) — **"not numerically specified in pt; defined as ratios"**: heading dominant, subhead ~ proportional, gap = heading cap-height.

---

## 6. Outline-decor (corner lines, plus, sparks, arrows) — p.37 (+p.41–42)

- **Стрелка (Arrow / flow)** = data-flow symbol. Straight shaft, **square cap**, can be plain line OR sit on a **45°-rotated square (rhombus) backing**. Rhombus fill must be a brand color **different from the line color**; measured backing = brand **Green #26D07C** (fill 0.15,0.82,0.49).
- **Spark/star** see §2 (4-point concave sparkle).
- **Thin decor lines** (corner ticks, the "//", "°", "·", "*" accents seen on pseudo-3D pages 41–42): hairline-to-thin, same **2/4/6 px** micromodule rule, square caps, right angles.
- **All decor:** square stroke endings, no rounding, geometric (gротеск) — global Look&feel constants (p.4).

**Primitive recipe for arrow-on-rhombus:** rotate a square 45° (diamond), fill brand accent; overlay arrow line in a contrasting brand color, square caps, weight 2–6 px.

---

## 7. Цвет применение — pp.18–19, 26 (+9,15)

**Base palette (p.18) — exact hex:**
| Name | HEX | RGB |
|---|---|---|
| Green | **#26D07C** | 38,208,124 |
| Yellow | **#CFF500** | 207,245,0 |
| Purple | **#A068FF** | 160,104,255 |
| Blue | **#C0E0FC** | 192,224,252 |
| Black | **#222222** | 34,34,34 |
| White | **#FFFFFF** | 255,255,255 |
| Gray | **#F2F2F2** | 242,242,242 |

**Extended palette (p.19, accent/attention only — don't mix with base):** Aquamarine #18F4CF, Ultramarine #0063FF, Magenta #FF00FF, Carrot #FF4517, Coral #FF0642, plus muted variants Aquamarine2 #C9F2EA, Ultramarine5 #C9D9F2, Magenta3 #C067C0, Carrot3 #DD7D64, Coral3 #E25B7C.

**Application:** Green/Yellow/Black/White are the **root brand colors**. The book uses Green as a **small accent** (the logo mark, the rhombus behind arrows, accent plashki) against large dark/white/gray fields — green is never a dominant flood; it punctuates. Extended palette only when you need to "manage attention," used **alone**, not blended with base. (The prior "green 5–10% accent / dark-slide ratio" is a reasonable operationalization; the book states it qualitatively, **not numerically specified**, visually green ≈ a small accent share.)

**Logo color combos (p.9):** Green-logo and Black-logo on light/brand backgrounds; **monochrome White logo only on photo backgrounds**.

---

## 8. Логоблок construction — pp.7, 11–17

**Logo (p.7):**
- **Safe area (охранное поле) = uniform margin "x" on all 4 sides**, where x is one module derived from the mark (visually ≈ the width of one facet/segment of the green cube mark; roughly the mark is divided into a grid and x = one cell). Square corner ticks mark the field.
- **Minimum size: 20 px** (logo width floor; "≥20 px").
- **Plashka (backing plate):** the white backing under the logo **matches the safe-area bounds** exactly (plashka = safe-field rectangle).
- Mark = **hexagonal green cube** (#26D07C) + wordmark "cloud.ru" in Black.

**Logoblock with descriptor (p.11–15):**
- Structure: **[logo] · [descriptor plashka]**, descriptor = "облачные сервисы и AI-технологии" (2-line or 1-line variant).
- **Uniform padding "x"** around the whole logoblock (same x as logo safe area).
- **Gap between the logo block and descriptor block = 1 px** (explicit).
- **Vertical logoblock variant** allowed for narrow formats (p.12); never distort proportions.
- **Stretched logoblock (p.13–14):** the block stretches to layout width by inserting a **linear vertical-bar pattern** between logo and descriptor. Trigger rule (p.14): with gaps a (logo↔descriptor natural) and b (extra stretch space) — **if b < a → no pattern; if b ≥ a → pattern is mandatory.**
  - **Pattern bar height = height of the descriptor text** (cap-to-baseline of descriptor).
  - **Bar spacing ≈ 1/4 of the logo-mark width.**
  - Length adjustments must stay a multiple of the micromodule (2 px).
- **Cobranding (p.17):** partner logo separated by a **vertical divider line**; divider **thickness = the inner counter (просвет) of the Cloud.ru mark**; spacing between logos varies **x to 2x**, where **x = height of the Cloud.ru mark**; logos balanced by visual mass.

---

### Quick build-constants cheat sheet
- Micromodule **2 px** (all spacing ÷2); edge margins **÷10 px**.
- Brand Black **#222222**, Green **#26D07C**.
- Portal = square(s), offset **(+~24% , −~7.5%)** of side per step, up-right staircase, fill #222222.
- Stroke weights **2/4/6 px** (1 px small-icon exception); **square caps, square joins, right angles, no rounding**.
- Type: SB Sans Display(head)/Text(body)/Interface(infographic); leading 120% normal / 100% dense; numbers tracking −4..−7; block gap = heading cap-height.
- Logoblock: uniform pad x, 1 px logo↔descriptor gap, min logo 20 px, stretch-pattern bar height = descriptor height, bar spacing = ¼ mark width.

No files were written (temp extraction artifacts were created and deleted). PyMuPDF was installed to the system Python (C:\Python310) to enable PDF rendering since `pdftoppm` was missing.
