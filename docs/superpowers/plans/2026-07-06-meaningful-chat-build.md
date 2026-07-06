# Meaningful Content-First Chat Build — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the chat assistant turn slide briefs into typed, editable structured fields that render deterministically (no LLM at render time), so building a deck is a transparent dialogue on visible content instead of a black box.

**Architecture:** Add an optional typed layer to `DraftSlide` (`slide_type` + `fields`). A new pure module `webapp/slide_types.py` validates the fields per type and maps `(slide_type, fields)` to an existing engine template + content dict. `draft_render` renders typed slides deterministically through that mapping (reusing the existing coercion/assemble path); slides with no `slide_type` keep the old LLM-fill path (fallback). The chat agent gains one intent, `propose_content`, that fills the typed fields from briefs. The outline UI renders typed slides as editable field-cards.

**Tech Stack:** Python 3.10, FastAPI, Pydantic v2, pytest + `fastapi.testclient`; vanilla JS/HTML/CSS frontend (no build step).

**Design decisions locked (from spec `docs/superpowers/specs/2026-07-06-meaningful-chat-build-design.md`):**
- 4 deterministic types cover the common cases; anything else stays a raw slide → old LLM-fill (fallback). `quote` is intentionally deferred to fallback (no clean quote template exists yet).
- Type → template mapping (fixed in code, never in a prompt):
  - `title`   → `cover`      — fields `heading` (req), `subtitle` (opt)
  - `bullets` → `cards-6`    — fields `heading` (req), `bullets: list[str]`
  - `stats`   → `stats-row`  — fields `heading` (req), `stats: list[{value,label}]`
  - `two_col` → `three-col`  — fields `heading` (req), `left: list[str]`, `right: list[str]`
- No per-slide HTML cache exists (deck.html is fully re-rendered by `assemble` on every persist), so typed slides render live from `fields` on each render — no `filled`-flag juggling is needed for them. `filled` stays meaningful only for raw (LLM-filled) slides.

---

## File Structure

- **Create** `webapp/slide_types.py` — typed-field Pydantic models, `validate_fields()`, `map_typed()`. Pure, no I/O. One responsibility: the typed-content contract + its mapping to engine templates.
- **Modify** `webapp/draft.py` — add `slide_type` / `fields` to `DraftSlide`.
- **Modify** `webapp/draft_render.py` — branch typed slides through `map_typed` in `_to_deck_plan`.
- **Modify** `webapp/chat_agent.py` — `propose_content` intent (classifier text, models, `_propose_content`, dispatch), and skip typed slides in `build_outline`.
- **Modify** `webapp/app.py` — new `PUT /api/drafts/{sid}/slides/{index}/fields` endpoint; exclude typed slides from the `build` target guard.
- **Modify** `webapp/static/editor.js` — render typed slides as editable field-cards, debounced save to the new endpoint, "Предложить контент" action for raw slides, exclude typed from `hasBuildTargets`.
- **Modify** `webapp/static/editor.html` — fix the misleading build-overlay note; add minimal CSS for field-cards.
- **Modify** `tests/test_draft.py`, `tests/test_chat_agent.py`, **create** `tests/test_slide_types.py` — coverage.

Run the whole suite with: `python -m pytest -q` (from repo root). Individual tests as noted per task.

---

## Task 1: Typed-field schema + validation (`slide_types.validate_fields`)

**Files:**
- Create: `webapp/slide_types.py`
- Test: `tests/test_slide_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_slide_types.py`:

```python
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from webapp import slide_types as st


def test_validate_title_ok_and_defaults():
    out = st.validate_fields("title", {"heading": "Строение гриба"})
    assert out == {"heading": "Строение гриба", "subtitle": ""}


def test_validate_bullets_ok():
    out = st.validate_fields(
        "bullets", {"heading": "Тезисы", "bullets": ["a", "b", "c"]})
    assert out["heading"] == "Тезисы" and out["bullets"] == ["a", "b", "c"]


def test_validate_stats_ok():
    out = st.validate_fields("stats", {"heading": "В цифрах",
                                       "stats": [{"value": "99%", "label": "аптайм"}]})
    assert out["stats"][0] == {"value": "99%", "label": "аптайм"}


def test_validate_two_col_ok():
    out = st.validate_fields("two_col", {"heading": "Сравнение",
                                         "left": ["x"], "right": ["y"]})
    assert out["left"] == ["x"] and out["right"] == ["y"]


def test_validate_missing_required_heading_returns_none():
    assert st.validate_fields("bullets", {"bullets": ["a"]}) is None


def test_validate_unknown_type_returns_none():
    assert st.validate_fields("quote", {"heading": "h"}) is None
    assert st.validate_fields(None, {"heading": "h"}) is None


def test_validate_non_dict_returns_none():
    assert st.validate_fields("title", "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_slide_types.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'webapp.slide_types'`.

- [ ] **Step 3: Write minimal implementation**

Create `webapp/slide_types.py`:

```python
"""Typed structured slide content (feature: content-first chat build).

A ``DraftSlide`` may carry a ``slide_type`` + ``fields``: a small, strictly typed
contract per type. ``validate_fields`` normalises raw field dicts (returning None
when they don't fit the type — the caller then keeps the slide "raw" and lets the
old LLM-fill path handle it). ``map_typed`` deterministically maps a typed slide
to an existing engine template + content dict, so rendering needs no LLM.

This module is pure: no I/O, no engine imports beyond the template *ids* it names.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError


class TitleFields(BaseModel):
    heading: str
    subtitle: str = ""


class BulletsFields(BaseModel):
    heading: str
    bullets: list[str] = Field(default_factory=list)


class StatItem(BaseModel):
    value: str = ""
    label: str = ""


class StatsFields(BaseModel):
    heading: str
    stats: list[StatItem] = Field(default_factory=list)


class TwoColFields(BaseModel):
    heading: str
    left: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)


# slide_type → (Pydantic model, engine template id)
_SPECS: dict[str, tuple[type[BaseModel], str]] = {
    "title":   (TitleFields,   "cover"),
    "bullets": (BulletsFields, "cards-6"),
    "stats":   (StatsFields,   "stats-row"),
    "two_col": (TwoColFields,  "three-col"),
}

SLIDE_TYPES = tuple(_SPECS)


def validate_fields(slide_type, raw) -> dict | None:
    """Normalise ``raw`` for ``slide_type``; None if the type is unknown or the
    fields don't satisfy the contract (missing required heading, wrong shape)."""
    spec = _SPECS.get(slide_type)
    if spec is None or not isinstance(raw, dict):
        return None
    model, _ = spec
    try:
        return model.model_validate(raw).model_dump()
    except ValidationError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_slide_types.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/slide_types.py tests/test_slide_types.py
git commit -m "feat: typed slide-field schema + validation (slide_types)"
```

---

## Task 2: Map typed fields to engine template + content (`slide_types.map_typed`)

**Files:**
- Modify: `webapp/slide_types.py`
- Test: `tests/test_slide_types.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_slide_types.py`:

```python
def test_map_title_to_cover():
    tid, content = st.map_typed("title", {"heading": "Привет", "subtitle": "мир"})
    assert tid == "cover"
    assert content == {"title": "Привет", "subtitle": "мир"}


def test_map_bullets_to_cards6_drops_empty():
    tid, content = st.map_typed(
        "bullets", {"heading": "H", "bullets": ["a", "", "  ", "b"]})
    assert tid == "cards-6"
    assert content["title"] == "H"
    assert content["cards"] == [{"text": "a"}, {"text": "b"}]


def test_map_stats_to_stats_row():
    tid, content = st.map_typed(
        "stats", {"heading": "H", "stats": [{"value": "9", "label": "L"}]})
    assert tid == "stats-row"
    assert content["stats"] == [{"value": "9", "label": "L"}]


def test_map_two_col_to_three_col_joins_bullets():
    tid, content = st.map_typed(
        "two_col", {"heading": "H", "left": ["a", "b"], "right": ["c"]})
    assert tid == "three-col"
    assert content["columns"] == [{"text": "a • b"}, {"text": "c"}]


def test_map_invalid_returns_none_template():
    tid, content = st.map_typed("bullets", {"bullets": ["a"]})  # no heading
    assert tid is None and content == {}
    assert st.map_typed("quote", {"heading": "h"}) == (None, {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_slide_types.py -q`
Expected: FAIL — `AttributeError: module 'webapp.slide_types' has no attribute 'map_typed'`.

- [ ] **Step 3: Write minimal implementation**

Append to `webapp/slide_types.py`:

```python
def _clean(items) -> list[str]:
    return [str(x).strip() for x in items if str(x).strip()]


def map_typed(slide_type, fields) -> tuple[str | None, dict]:
    """Map a typed slide to (engine_template_id, content_dict). Returns
    (None, {}) when the type/fields are invalid — the caller falls back to the
    raw LLM-fill path. Content is left permissive; draft_render's ``_safe_content``
    clamps text/lists to the template's slot contract."""
    norm = validate_fields(slide_type, fields)
    if norm is None:
        return None, {}
    if slide_type == "title":
        return "cover", {"title": norm["heading"], "subtitle": norm["subtitle"]}
    if slide_type == "bullets":
        cards = [{"text": b} for b in _clean(norm["bullets"])]
        return "cards-6", {"title": norm["heading"], "cards": cards}
    if slide_type == "stats":
        stats = [{"value": s.get("value", ""), "label": s.get("label", "")}
                 for s in norm["stats"]]
        return "stats-row", {"title": norm["heading"], "stats": stats}
    if slide_type == "two_col":
        columns = [{"text": " • ".join(_clean(norm["left"]))},
                   {"text": " • ".join(_clean(norm["right"]))}]
        return "three-col", {"title": norm["heading"], "columns": columns}
    return None, {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_slide_types.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/slide_types.py tests/test_slide_types.py
git commit -m "feat: map typed slide fields to engine templates (map_typed)"
```

---

## Task 3: Add `slide_type` / `fields` to `DraftSlide`

**Files:**
- Modify: `webapp/draft.py:22-27`
- Test: `tests/test_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_draft.py` (after `test_load_save_roundtrip`, before the render section):

```python
def test_draftslide_typed_fields_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        brief="строение гриба", slide_type="bullets",
        fields={"heading": "Строение", "bullets": ["шляпка", "ножка"]})])
    draft.save_plan("typed", p)
    back = draft.load_plan("typed")
    assert back.slides[0].slide_type == "bullets"
    assert back.slides[0].fields["bullets"] == ["шляпка", "ножка"]
    # default: a plain slide has no type
    assert draft.DraftSlide().slide_type is None
    assert draft.DraftSlide().fields is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_draft.py::test_draftslide_typed_fields_roundtrip -q`
Expected: FAIL — `TypeError`/`ValidationError` (unexpected keyword `slide_type`).

- [ ] **Step 3: Write minimal implementation**

In `webapp/draft.py`, replace the `DraftSlide` class body (lines 22-27):

```python
class DraftSlide(BaseModel):
    template_id: str | None = None
    freeform: bool = False
    content: dict = Field(default_factory=dict)
    brief: str = ""              # тема слайда в аутлайне (до сборки)
    filled: bool = False         # прогнан ли через fill_slide (только для сырых)
    slide_type: str | None = None  # типизированный слайд: title|bullets|stats|two_col
    fields: dict | None = None     # структурированные поля под slide_type
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_draft.py::test_draftslide_typed_fields_roundtrip -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/draft.py tests/test_draft.py
git commit -m "feat: DraftSlide carries optional typed slide_type + fields"
```

---

## Task 4: Render typed slides deterministically in `draft_render`

**Files:**
- Modify: `webapp/draft_render.py:11-50`
- Test: `tests/test_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_draft.py` (in the render section):

```python
def test_render_typed_bullets_slide_uses_cards6_no_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        brief="строение", slide_type="bullets",
        fields={"heading": "Строение гриба",
                "bullets": ["шляпка", "ножка", "мицелий"]})])
    html = draft_render.render_draft("sidt", p).read_text("utf-8")
    assert html.count("<section") == 1
    assert 'data-template="cards-6"' in html
    assert "Строение гриба" in html and "мицелий" in html


def test_render_typed_invalid_fields_falls_back_to_raw(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    # slide_type set but fields invalid (no heading) → not the typed template;
    # falls through to the raw path (template_id or 'blank'), still renders.
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        brief="x", slide_type="bullets", fields={"bullets": ["a"]})])
    html = draft_render.render_draft("sidt2", p).read_text("utf-8")
    assert html.count("<section") == 1
    assert 'data-template="cards-6"' not in html
```

Note: `data-template="..."` is how the assembler tags each rendered `<section>` (see `test_render_freeform_slide`, which asserts `data-template="freeform"`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_draft.py -q -k "typed_bullets or typed_invalid"`
Expected: FAIL — the typed slide renders via the raw path (`blank`), so `data-template="cards-6"` is absent.

- [ ] **Step 3: Write minimal implementation**

In `webapp/draft_render.py`, add the import (after line 20 `from webapp.draft import DraftPlan`):

```python
from webapp import slide_types
```

Then in `_to_deck_plan`, insert the typed branch inside the loop — right after the `if s.freeform:` block (after line 45's `continue`) and before `tid = s.template_id or "blank"`:

```python
        if s.slide_type:
            tid, content = slide_types.map_typed(s.slide_type, s.fields or {})
            if tid:
                spec = library.get(tid)
                slides.append(SlidePlan(
                    index=i, type=spec.type, template_id=tid,
                    content=_safe_content(library, tid, content)))
                continue
            # invalid typed fields → fall through to the raw path below
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_draft.py -q -k "typed_bullets or typed_invalid"`
Expected: PASS. Then run the whole draft module: `python -m pytest tests/test_draft.py -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add webapp/draft_render.py tests/test_draft.py
git commit -m "feat: render typed slides deterministically via map_typed"
```

---

## Task 5: `propose_content` intent — models + classifier text

**Files:**
- Modify: `webapp/chat_agent.py:68-90` (Intent doc + `_INTENT_SYSTEM`), and add models near line 154
- Test: covered in Task 6 (behavioural). This task is prep only.

- [ ] **Step 1: Update the Intent action docstring**

In `webapp/chat_agent.py`, update the `Intent.action` comment (line 69) to include the new action:

```python
    action: str          # plan | add | rewrite | delete | move | retitle | enrich | propose_content | build_now | chat
```

- [ ] **Step 2: Add the classifier description**

In `_INTENT_SYSTEM` (lines 74-90), insert this line right after the `enrich` bullet (after line 86, before the `build_now` bullet):

```python
    "- propose_content: пользователь просит РАЗЛОЖИТЬ слайды по структурированным полям — предложить конкретный контент по типам (заголовок, тезисы-список, цифры, две колонки). Отличие от enrich: enrich дописывает текстовое описание, propose_content раскладывает слайд в типизированные поля. НЕ создаёт слайды.\n"
```

- [ ] **Step 3: Add the proposal models + system prompt**

In `webapp/chat_agent.py`, after the `EnrichedOutline` class (line 154) add:

```python
class ProposedItem(BaseModel):
    index: int                 # 1-based слайд плана
    slide_type: str = ""       # title | bullets | stats | two_col
    fields: dict = Field(default_factory=dict)


class ProposedContent(BaseModel):
    slides: list[ProposedItem] = Field(default_factory=list)
```

And after `_ENRICH_SYSTEM` (line 176) add:

```python
_PROPOSE_SYSTEM = (
    "Ты раскладываешь слайды презентации Cloud.ru по СТРУКТУРИРОВАННЫМ полям. "
    "Тебе дан список слайдов (номер, заголовок, описание). Для каждого выбери "
    "ОДИН тип и заполни поля по его смыслу. Типы и поля:\n"
    "- title: обложка/титул. fields: {\"heading\": str, \"subtitle\": str}\n"
    "- bullets: список тезисов. fields: {\"heading\": str, \"bullets\": [str, ...]} (2–6 пунктов)\n"
    "- stats: цифры/метрики. fields: {\"heading\": str, \"stats\": [{\"value\": str, \"label\": str}, ...]} (2–4)\n"
    "- two_col: две колонки/сравнение. fields: {\"heading\": str, \"left\": [str, ...], \"right\": [str, ...]}\n"
    "Если слайд не ложится ни в один тип — пропусти его (не включай в ответ). "
    "heading обязателен у каждого. Отвечай СРАЗУ одним JSON-объектом вида "
    '{"slides":[{"index":1,"slide_type":"bullets","fields":{...}}]}. '
    "НЕ пиши текста, рассуждений или Markdown до или после JSON."
)
```

- [ ] **Step 4: Verify it imports**

Run: `python -c "import webapp.chat_agent as m; print(m.ProposedContent, m._PROPOSE_SYSTEM[:20])"`
Expected: prints the class and the prompt prefix, no error.

- [ ] **Step 5: Commit**

```bash
git add webapp/chat_agent.py
git commit -m "feat: propose_content intent scaffolding (models + prompts)"
```

---

## Task 6: `_propose_content` action + dispatch

**Files:**
- Modify: `webapp/chat_agent.py` (add `_propose_content`, add dispatch branch in `run_turn`)
- Test: `tests/test_chat_agent.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_chat_agent.py`, first extend `FakeClient.chat_json` so it can script a `ProposedContent` reply. Replace the `chat_json` method (lines 16-23) with:

```python
    def __init__(self, intent: dict, *, template="cards-6", text="ответ",
                 proposed=None):
        self._intent = intent
        self._template = template
        self._text = text
        self._proposed = proposed  # dict for ProposedContent, or None

    def chat_json(self, messages, model_cls, **kw):
        name = model_cls.__name__
        if name == "Intent":
            return model_cls.model_validate(self._intent)
        if name == "ProposedContent":
            return model_cls.model_validate(self._proposed or {"slides": []})
        # SlideContent for fill_slide → minimal valid-ish content
        return model_cls.model_validate({"content": {"title": "T",
                                                     "cards": [{"text": "x"}]}})
```

Then append this test:

```python
def test_intent_propose_content_fills_typed_fields(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="строение гриба", filled=False),
        draft.DraftSlide(brief="в цифрах", filled=False)])
    proposed = {"slides": [
        {"index": 1, "slide_type": "bullets",
         "fields": {"heading": "Строение", "bullets": ["шляпка", "ножка"]}},
        {"index": 2, "slide_type": "stats",
         "fields": {"heading": "Цифры", "stats": [{"value": "90%", "label": "лес"}]}},
    ]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed
    assert plan.slides[0].slide_type == "bullets"
    assert plan.slides[0].fields["bullets"] == ["шляпка", "ножка"]
    assert plan.slides[1].slide_type == "stats"


def test_propose_content_skips_invalid_item(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(brief="x", filled=False)])
    # missing heading → invalid → slide stays raw
    proposed = {"slides": [{"index": 1, "slide_type": "bullets",
                            "fields": {"bullets": ["a"]}}]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    chat_agent.run_turn("s", "разложи", 1, client=c)
    assert draft.load_plan("s").slides[0].slide_type is None


def test_propose_content_no_targets(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)  # empty plan
    c = FakeClient({"action": "propose_content"}, proposed={"slides": []})
    res = chat_agent.run_turn("s", "разложи", 1, client=c)
    assert res.changed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chat_agent.py -q -k propose`
Expected: FAIL — `propose_content` falls to the `else: chat` branch, no typing happens.

- [ ] **Step 3: Write minimal implementation**

In `webapp/chat_agent.py`, add the import at the top near the other `webapp` imports (line 32 area):

```python
from webapp import draft, slide_types
```

(replace the existing `from webapp import draft`).

Add the dispatch branch in `run_turn` — right after the `enrich` branch (after line 260):

```python
    elif intent.action == "propose_content":
        plan, result = _propose_content(client, session_id, plan, ctx, message)
```

Add the function after `_enrich_briefs` (after line 363):

```python
def _propose_content(client: Any, session_id: str, plan: draft.DraftPlan,
                     ctx: str, message: str) -> tuple[draft.DraftPlan, AgentResult]:
    """Разложить сырые слайды аутлайна по типизированным полям. Невалидные
    предложения игнорируются (слайд остаётся сырым → fallback на сборке).
    Слайды не добавляются/не удаляются. Возвращает (plan, result)."""
    targets = [i for i, s in enumerate(plan.slides, start=1)
               if s.brief and not s.filled and not s.freeform and not s.slide_type]
    if not targets:
        return plan, AgentResult(
            reply="Нет сырых слайдов для раскладки — сначала набросаем план.",
            changed=False)
    lines = "\n".join(
        f"{i}. {plan.slides[i - 1].content.get('title', '')} — {plan.slides[i - 1].brief}"
        for i in targets)
    user = f"Контекст:\n{ctx}\n\nСлайды:\n{lines}\n\nЗапрос:\n{message}"
    try:
        proposed = client.chat_json(
            [{"role": "system", "content": _PROPOSE_SYSTEM},
             {"role": "user", "content": user}],
            ProposedContent, max_tokens=4000, retries=2,
            extra_body={"thinking": {"type": "disabled"}})
    except Exception:  # noqa: BLE001
        proposed = None
    applied = 0
    if proposed and proposed.slides:
        for item in proposed.slides:
            if item.index not in targets:
                continue
            norm = slide_types.validate_fields(item.slide_type, item.fields)
            if norm is None:
                continue  # не ложится в тип → слайд остаётся сырым (fallback)
            plan.slides[item.index - 1] = plan.slides[item.index - 1].model_copy(
                update={"slide_type": item.slide_type, "fields": norm,
                        "filled": False})
            applied += 1
    if applied:
        draft.save_plan(session_id, plan)
        return plan, AgentResult(
            reply=(f"Разложил по полям {applied} сл. Проверь и поправь в "
                   "аутлайне — потом жми «Собрать»."), changed=True)
    return plan, AgentResult(
        reply="Не получилось разложить слайды по полям — попробуй иначе.",
        changed=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chat_agent.py -q -k propose`
Expected: PASS (3 tests). Then `python -m pytest tests/test_chat_agent.py -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add webapp/chat_agent.py tests/test_chat_agent.py
git commit -m "feat: _propose_content turns raw briefs into typed slide fields"
```

---

## Task 7: `build_outline` skips typed slides

**Files:**
- Modify: `webapp/chat_agent.py:379-380` (loop guard in `build_outline`)
- Test: `tests/test_chat_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_agent.py`:

```python
def test_build_outline_skips_typed_slides(monkeypatch, tmp_path):
    # a typed slide must NOT be sent through the LLM fill during build.
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        brief="строение", slide_type="bullets",
        fields={"heading": "Строение", "bullets": ["a", "b"]})])

    class Boom:
        def chat_json(self, *a, **k):
            raise AssertionError("classifier/fill must not run for typed slides")

        def chat(self, *a, **k):
            raise AssertionError("template pick must not run for typed slides")

    chat_agent.build_outline("s", client=Boom())
    plan = draft.load_plan("s")
    assert plan.slides[0].slide_type == "bullets"   # unchanged
    assert plan.slides[0].filled is False           # never LLM-filled
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_chat_agent.py::test_build_outline_skips_typed_slides -q`
Expected: FAIL — `AssertionError: template pick must not run...` (build tries to `_pick_template` the typed slide).

- [ ] **Step 3: Write minimal implementation**

In `webapp/chat_agent.py`, in `build_outline`, change the skip guard (line 380) from:

```python
        if not (s.brief and not s.filled and not s.freeform):
            continue
```

to:

```python
        if not (s.brief and not s.filled and not s.freeform and not s.slide_type):
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_chat_agent.py::test_build_outline_skips_typed_slides -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/chat_agent.py tests/test_chat_agent.py
git commit -m "fix: build_outline skips typed slides (they render without LLM)"
```

---

## Task 8: `PUT /slides/{index}/fields` endpoint

**Files:**
- Modify: `webapp/app.py` (add endpoint after `update_draft_slide_html`, ~line 356)
- Test: `tests/test_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_draft.py`:

```python
def test_update_slide_fields_endpoint(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        # set typed fields
        r = c.put(f"/api/drafts/{sid}/slides/1/fields",
                  json={"slide_type": "bullets",
                        "fields": {"heading": "Строение",
                                   "bullets": ["шляпка", "ножка"]}}, headers=H())
        assert r.status_code == 200
        slide = r.json()["plan"]["slides"][0]
        assert slide["slide_type"] == "bullets"
        assert slide["fields"]["bullets"] == ["шляпка", "ножка"]
        # the deck now renders that slide as cards-6 (deterministic, no LLM)
        assert 'data-template="cards-6"' in c.get(
            f"/api/jobs/{sid}/deck", headers=H()).text
        # invalid fields (no heading) → 400
        assert c.put(f"/api/drafts/{sid}/slides/1/fields",
                     json={"slide_type": "bullets", "fields": {"bullets": ["a"]}},
                     headers=H()).status_code == 400
        # unknown type → 400
        assert c.put(f"/api/drafts/{sid}/slides/1/fields",
                     json={"slide_type": "quote", "fields": {"heading": "h"}},
                     headers=H()).status_code == 400
        # out of range → 404
        assert c.put(f"/api/drafts/{sid}/slides/9/fields",
                     json={"slide_type": "title", "fields": {"heading": "h"}},
                     headers=H()).status_code == 404
        # cross-user → 404
        assert c.put(f"/api/drafts/{sid}/slides/1/fields",
                     json={"slide_type": "title", "fields": {"heading": "h"}},
                     headers=H("intruder")).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_draft.py::test_update_slide_fields_endpoint -q`
Expected: FAIL — 404/405 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `webapp/app.py`, add the import for `slide_types` where `draft`/`draft_render` are imported (top of file — find the existing `from webapp import ...` line that includes `draft` and add `slide_types`). Then add this endpoint after `update_draft_slide_html` (after line 355):

```python
@app.put("/api/drafts/{session_id}/slides/{index}/fields")
async def update_draft_slide_fields(session_id: str, index: int, request: Request,
                                    user=Depends(get_current_user)) -> JSONResponse:
    """Set a slide's typed structured content (slide_type + fields). Validated
    against the type contract; on success the slide renders deterministically
    (no LLM). Invalid fields → 400 (the slide is left untouched)."""
    from webapp import slide_types
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    slide_type = data.get("slide_type")
    norm = slide_types.validate_fields(slide_type, data.get("fields"))
    if norm is None:
        raise HTTPException(400, "invalid slide_type or fields")
    if not 1 <= index <= len(plan.slides):
        raise HTTPException(404, "slide not found")
    plan.slides[index - 1] = plan.slides[index - 1].model_copy(
        update={"slide_type": slide_type, "fields": norm, "filled": False})
    _persist_draft(session_id, plan)
    return JSONResponse({"plan": plan.model_dump(), "errors": []})
```

Note: check the exact 400/404 ordering matches the test — validate fields first (400), then bounds (404). The `mutate=True` in `_draft_or_404` already yields the 409/404/ownership guarding used by the sibling endpoints.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_draft.py::test_update_slide_fields_endpoint -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/app.py tests/test_draft.py
git commit -m "feat: PUT /slides/{i}/fields endpoint for typed slide content"
```

---

## Task 9: Exclude typed slides from the build target guard

**Files:**
- Modify: `webapp/app.py:444-445` (the `build_draft` targets list)
- Test: `tests/test_draft.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_draft.py`:

```python
def test_build_guard_ignores_typed_only_deck(monkeypatch, tmp_path):
    # a deck whose only slide is typed has nothing to LLM-build → 400 guard.
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        c.put(f"/api/drafts/{sid}/slides/1/fields",
              json={"slide_type": "title", "fields": {"heading": "H"}}, headers=H())
        assert c.post(f"/api/drafts/{sid}/build", headers=H()).status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_draft.py::test_build_guard_ignores_typed_only_deck -q`
Expected: FAIL — the typed slide still has a `brief` and `filled=False`, so it's counted as a target and `build_outline` runs (attempting a real LLM call / returning 200).

- [ ] **Step 3: Write minimal implementation**

In `webapp/app.py`, in `build_draft`, change the targets comprehension (lines 444-445) from:

```python
    targets = [s for s in plan.slides if s.brief and not s.filled
               and not s.freeform]
```

to:

```python
    targets = [s for s in plan.slides if s.brief and not s.filled
               and not s.freeform and not s.slide_type]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_draft.py::test_build_guard_ignores_typed_only_deck -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp/app.py tests/test_draft.py
git commit -m "fix: build guard treats typed slides as already-built"
```

---

## Task 10: Full backend suite green

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `python -m pytest -q`
Expected: all tests PASS (previous 112 + the new ones). If anything unrelated broke, read the failure and fix the offending task's code before continuing.

- [ ] **Step 2: Commit (only if a fix was needed)**

```bash
git add -A && git commit -m "test: keep full suite green after typed-slide backend"
```

---

## Task 11: Outline UI — render typed slides as editable field-cards

**Files:**
- Modify: `webapp/static/editor.js:729-758` (`hasBuildTargets`, `renderOutline`)
- Modify: `webapp/static/editor.html` (add field-card CSS)

There is no JS test harness in this repo; verify via the preview workflow (server already running on the dev port).

- [ ] **Step 1: Exclude typed slides from build targets**

In `webapp/static/editor.js`, change `hasBuildTargets` (lines 729-732) to:

```javascript
function hasBuildTargets() {
  return (draftPlan.slides || []).some(
    (s) => s && s.brief && !s.filled && !s.freeform && !s.slide_type);
}
```

- [ ] **Step 2: Replace `renderOutline` with type-aware rendering**

Replace `renderOutline` (lines 737-758) with:

```javascript
const TYPE_LABEL = { title: "титул", bullets: "список", stats: "цифры",
                     two_col: "две колонки" };

// Render the live outline. Typed slides show editable field-cards (what you see
// is what renders); raw slides show the brief + a "Предложить контент" action.
function renderOutline() {
  const list = byId("outlineList");
  if (!list) return;
  list.innerHTML = "";
  (draftPlan.slides || []).forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "outline-item";
    const head = document.createElement("div");
    head.className = "outline-head";
    const num = document.createElement("span");
    num.className = "outline-num";
    num.textContent = i + 1;
    const label = document.createElement("span");
    label.className = "outline-label";
    label.textContent =
      (s.fields && s.fields.heading) || (s.content && s.content.title) ||
      s.brief || "—";
    const badge = document.createElement("span");
    if (s.slide_type) {
      badge.className = "outline-badge is-typed";
      badge.textContent = TYPE_LABEL[s.slide_type] || s.slide_type;
    } else {
      badge.className = "outline-badge " + (s.filled ? "is-ready" : "is-plan");
      badge.textContent = s.filled ? "готов" : "сырой";
    }
    head.appendChild(num);
    head.appendChild(label);
    head.appendChild(badge);
    li.appendChild(head);
    if (s.slide_type) li.appendChild(renderFieldCard(s, i + 1));
    else li.appendChild(renderRawActions(i + 1));
    list.appendChild(li);
  });
  byId("buildDeck")?.classList.toggle("hidden", !hasBuildTargets());
}

// A raw slide: offer to structure it via the chat agent (propose_content).
function renderRawActions(idx) {
  const wrap = document.createElement("div");
  wrap.className = "outline-raw";
  const btn = document.createElement("button");
  btn.className = "outline-propose";
  btn.textContent = "Предложить контент";
  btn.addEventListener("click", () => {
    chatText.value = "Разложи слайды по структурированным полям";
    sendAgent();
  });
  wrap.appendChild(btn);
  return wrap;
}
```

- [ ] **Step 3: Add the field-card editor + debounced save**

In `webapp/static/editor.js`, add these functions just above `renderOutline` (they build inputs per type and PATCH the new endpoint):

```javascript
// Debounced per-slide field save. Mirrors the manual builder's edit debounce so a
// rapid edit followed by navigation/rebuild isn't lost.
const fieldTimers = {};
function saveFields(idx, slideType, fields) {
  clearTimeout(fieldTimers[idx]);
  fieldTimers[idx] = setTimeout(async () => {
    try {
      const r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx}/fields`), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slide_type: slideType, fields }),
      });
      if (r.ok) { draftPlan = (await r.json()).plan; loadDeck(); }
    } catch (_) { /* transient; next edit retries */ }
  }, 500);
}

function fieldInput(value, onInput) {
  const el = document.createElement("input");
  el.className = "field-input";
  el.value = value || "";
  el.addEventListener("input", onInput);
  return el;
}

// Build the editable card for a typed slide. Reads current values from s.fields,
// writes edits back through saveFields. One small builder per type.
function renderFieldCard(s, idx) {
  const card = document.createElement("div");
  card.className = "field-card";
  const f = Object.assign({}, s.fields || {});
  const commit = () => saveFields(idx, s.slide_type, f);

  const addRow = (labelText, input) => {
    const row = document.createElement("label");
    row.className = "field-row";
    const cap = document.createElement("span");
    cap.className = "field-cap";
    cap.textContent = labelText;
    row.appendChild(cap);
    row.appendChild(input);
    card.appendChild(row);
  };

  addRow("Заголовок", fieldInput(f.heading, (e) => {
    f.heading = e.target.value; commit();
  }));

  if (s.slide_type === "title") {
    addRow("Подзаголовок", fieldInput(f.subtitle, (e) => {
      f.subtitle = e.target.value; commit();
    }));
  } else if (s.slide_type === "bullets") {
    f.bullets = f.bullets || [];
    addRow("Тезисы", fieldInput(f.bullets.join(" | "), (e) => {
      f.bullets = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    hint(card, "Пункты через | (вертикальная черта)");
  } else if (s.slide_type === "stats") {
    f.stats = f.stats || [];
    addRow("Цифры", fieldInput(
      f.stats.map((x) => `${x.value}=${x.label}`).join(" | "), (e) => {
        f.stats = e.target.value.split("|").map((p) => {
          const [value, label] = p.split("=");
          return { value: (value || "").trim(), label: (label || "").trim() };
        }).filter((x) => x.value || x.label);
        commit();
      }));
    hint(card, "value=label, пары через |  (напр. 99%=аптайм | 3=региона)");
  } else if (s.slide_type === "two_col") {
    f.left = f.left || []; f.right = f.right || [];
    addRow("Левая колонка", fieldInput(f.left.join(" | "), (e) => {
      f.left = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    addRow("Правая колонка", fieldInput(f.right.join(" | "), (e) => {
      f.right = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    hint(card, "Пункты через | в каждой колонке");
  }
  return card;
}

function hint(card, text) {
  const h = document.createElement("div");
  h.className = "field-hint";
  h.textContent = text;
  card.appendChild(h);
}
```

- [ ] **Step 4: Add CSS for field-cards**

In `webapp/static/editor.html`, inside the existing `<style>` block (near the `.outline-*` rules — search for `outline-badge`), add:

```css
.outline-item { display: block; }
.outline-head { display: flex; align-items: center; gap: 8px; }
.outline-badge.is-typed { background: #204a2e; color: #7bd06f; }
.field-card { margin: 6px 0 10px 26px; padding: 8px;
  border: 1px solid #2a2a2a; border-radius: 8px; background: #1b1b1b; }
.field-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.field-cap { flex: 0 0 96px; font-size: 11px; color: #9a9a9a; }
.field-input { flex: 1; background: #111; color: #eee; border: 1px solid #333;
  border-radius: 6px; padding: 4px 6px; font-size: 12px; }
.field-hint { font-size: 11px; color: #6a6a6a; margin-top: 4px; }
.outline-raw { margin: 4px 0 10px 26px; }
.outline-propose { background: #204a2e; color: #7bd06f; border: 0;
  border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
```

Match the surrounding palette if these exact colors clash — the point is a distinct, legible field-card. If the outline rules live in an external CSS file rather than inline `<style>`, add them there instead.

- [ ] **Step 5: Verify in the browser preview**

Ensure the dev server is running (`preview_start` if needed). Then:
1. `preview_eval`: `window.location.reload()`.
2. Create/open a manual draft, add a couple of slides via chat, then click **Предложить контент** on a raw slide (or send "разложи слайды по полям").
3. `preview_snapshot` — confirm typed slides now show editable field-cards with a type chip.
4. Edit a field; `preview_console_logs` shows no errors; the deck iframe updates.
5. `preview_screenshot` — capture the field-cards for the user.

Fix any console errors by reading `editor.js` and re-checking from step 5's checks.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/editor.js webapp/static/editor.html
git commit -m "feat: outline renders typed slides as editable field-cards"
```

---

## Task 12: Fix the misleading build-overlay note

**Files:**
- Modify: `webapp/static/editor.html` (overlay note, ~lines 33-38)
- Modify: `webapp/static/editor.js:768-769` (overlay copy)

- [ ] **Step 1: Update the overlay note markup**

In `webapp/static/editor.html`, find the `build-overlay__note` block (it currently reads "Можно закрыть страницу или переключить раздел — сборка идёт на сервере…" with a "← К списку сборок" link). Replace that note's text with copy honest for the synchronous chat build:

```html
<p class="build-overlay__note">Собираю деку — это займёт несколько секунд. Типовые слайды рендерятся сразу; заполняются только сырые.</p>
```

Remove the "← К списку сборок" `<a>` inside that note (the synchronous build returns here on its own; that link belonged to the async long-build flow). Leave the rest of the overlay (`#buildTitle`, `#buildSub`) intact.

- [ ] **Step 2: Keep the JS overlay copy consistent**

In `webapp/static/editor.js` `doBuild` (lines 768-769), set a clearer subtitle:

```javascript
  buildTitle.textContent = "Собираю деку…";
  buildSub.textContent = "заполняю сырые слайды";
```

- [ ] **Step 3: Verify in the browser preview**

1. `preview_eval`: `window.location.reload()`.
2. Trigger a build (with at least one raw slide) and observe the overlay copy via `preview_snapshot` while it's up (or `preview_inspect` the `.build-overlay__note`).
3. Confirm no "← К списку сборок" link remains and the note text is the new one.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/editor.html webapp/static/editor.js
git commit -m "fix: honest build-overlay copy for the synchronous chat build"
```

---

## Task 13: End-to-end local smoke (per memory: realistic-size context)

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite once more**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Local e2e via the preview**

Following the local-e2e memory (`workflow_local_e2e.md`): open a manual draft, use chat to make a real ~8-slide outline, click **Предложить контент**, confirm typed field-cards appear and are editable, edit several fields, then **Собрать**. Confirm:
- typed slides render immediately/deterministically (no LLM latency for them),
- editing a field updates the deck,
- a raw slide (one the model skipped) still builds via the fallback path,
- no console errors (`preview_console_logs`) and no server errors (`preview_logs`).

Capture a `preview_screenshot` of the finished deck + outline for the user.

- [ ] **Step 3: No commit** (verification only). Report results to the user; deployment/prod-e2e is a separate, user-authorized step per the established workflow.

---

## Self-Review

**Spec coverage:**
- Model data (`slide_type`/`fields`, states, no migration) → Tasks 3, 4. ✓
- Chat agent `propose_content` (classify, validate-per-type, fallback, max_tokens≥4000) → Tasks 5, 6. ✓
- Render fork (typed deterministic / raw fallback), edits reflected → Task 4 (+ live re-render via `_persist_draft`; note: spec's `filled`-invalidation is unnecessary because deck.html is fully re-rendered — documented in the header). ✓
- Outline UI: typed field-cards (fixes invisible enrich), raw "Предложить контент", chip → Task 11. ✓
- Build trigger: typed excluded, overlay copy fixed → Tasks 7, 9, 12. ✓
- Error handling: invalid fields keep slide raw (Tasks 6, 8); build guard preserved (Task 9). ✓
- Testing: per-type validation, render fork, propose behaviour, endpoint, build skip → Tasks 1-9. ✓
- Out of scope (async progress build, >4 types, template changes) — not implemented, as specified. `quote` deferred to fallback (spec allowed "4-5 types"). ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. Frontend steps have no unit tests by design (no JS harness) → verified via preview workflow, which is stated explicitly. ✓

**Type consistency:** `validate_fields(slide_type, raw) -> dict|None` and `map_typed(slide_type, fields) -> (str|None, dict)` are used consistently in draft_render (Task 4), chat_agent (Task 6), and the endpoint (Task 8). `ProposedContent`/`ProposedItem` names match between the scaffolding (Task 5), the FakeClient dispatch and the function (Task 6). Field-shape (`heading`, `bullets`, `stats[{value,label}]`, `left`/`right`) is identical across schema, mapping, prompt, endpoint test, and UI. ✓
