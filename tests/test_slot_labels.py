"""К§2: русские подписи слотов и макетов (label / hint / display_name).

Проверяем, что движок парсит новые поля, даёт дефолт "" при их отсутствии,
не ломается на неизвестных ключах, сохраняет сырые id/контракт слотов, и что
webapp.templates_api пробрасывает label/hint/display_name наружу.
"""
from htmlslides.library import SlotSpec, TemplateLibrary, TemplateSpec
from webapp import templates_api


# ── SlotSpec.from_json ───────────────────────────────────────────────────────
def test_slotspec_parses_label_and_hint():
    spec = SlotSpec.from_json({
        "kind": "text", "required": True, "max_chars": 8,
        "label": "Доля 1, %", "hint": "По порядку легенды",
    })
    assert spec.label == "Доля 1, %"
    assert spec.hint == "По порядку легенды"
    # сырой контракт не тронут
    assert spec.kind == "text"
    assert spec.required is True
    assert spec.max_chars == 8


def test_slotspec_label_hint_default_empty_when_absent():
    spec = SlotSpec.from_json({"kind": "text", "required": True, "max_chars": 20})
    assert spec.label == ""
    assert spec.hint == ""


def test_slotspec_ignores_unknown_json_keys():
    spec = SlotSpec.from_json({"kind": "text", "totally_unknown": 1, "label": "X"})
    assert spec.kind == "text"
    assert spec.label == "X"


def test_slotspec_nested_item_slots_parse_labels_and_hints():
    spec = SlotSpec.from_json({
        "kind": "list", "required": True, "max_items": 5, "label": "Категории",
        "item_slots": {
            "label": {"kind": "text", "required": True, "max_chars": 28,
                      "label": "Название категории"},
            "v1": {"kind": "text", "required": True, "max_chars": 8,
                   "label": "Доля 1, %", "hint": "По порядку легенды"},
        },
    })
    assert spec.label == "Категории"
    assert spec.item_slots["label"].label == "Название категории"
    assert spec.item_slots["v1"].hint == "По порядку легенды"
    # under-slot ids preserved verbatim
    assert set(spec.item_slots) == {"label", "v1"}


# ── TemplateSpec / TemplateLibrary ───────────────────────────────────────────
def test_templatespec_display_name_defaults_empty():
    t = TemplateSpec(id="x", type="content", file="x.html", intent="", slots={})
    assert t.display_name == ""


def test_library_every_template_has_display_name():
    lib = TemplateLibrary.load()
    assert len(lib.templates) == 26  # 25 + diagram («Схема»)
    for t in lib.templates:
        assert t.display_name, f"{t.id}: пустой display_name"


def test_library_known_display_names():
    by_id = {t.id: t for t in TemplateLibrary.load().templates}
    assert by_id["cover"].display_name == "Обложка"
    assert by_id["statement"].display_name == "Акцентный тезис"
    assert by_id["kpi-rings"].display_name == "KPI-кольца"
    assert by_id["stacked-bar"].display_name == "Составные бары"


def test_library_raw_slot_ids_and_contract_preserved():
    """Правка additive: сырые id слотов/саб-слотов и kind/required/max_chars
    остаются нетронутыми, лейблы лишь добавлены."""
    sb = next(t for t in TemplateLibrary.load().templates if t.id == "stacked-bar")
    assert set(sb.slots) == {"title", "subtitle", "legend", "bars"}
    bars = sb.slots["bars"]
    assert set(bars.item_slots) == {"label", "v1", "v2", "v3", "v4"}
    v1 = bars.item_slots["v1"]
    assert v1.kind == "text"
    assert v1.required is True
    assert v1.max_chars == 8
    # additive label/hint присутствуют
    assert v1.label == "Доля 1, %"
    assert v1.hint == "По порядку легенды"


def test_library_every_slot_labelled():
    for t in TemplateLibrary.load().templates:
        for name, spec in t.slots.items():
            assert spec.label, f"{t.id}.{name}: нет label"


# ── templates_api проброс ────────────────────────────────────────────────────
def test_catalog_exposes_display_name():
    cat = templates_api.catalog()
    assert cat
    for entry in cat:
        assert "display_name" in entry
        assert entry["display_name"]


def test_catalog_slot_dict_emits_label_and_hint_only_when_set():
    by_id = {c["id"]: c for c in templates_api.catalog()}
    # верхнеуровневый слот с лейблом, без hint → ключ hint отсутствует
    title = by_id["cover"]["slots"]["title"]
    assert title["label"] == "Заголовок"
    assert "hint" not in title
    # саб-слот с label + hint
    v1 = by_id["stacked-bar"]["slots"]["bars"]["item_slots"]["v1"]
    assert v1["label"] == "Доля 1, %"
    assert v1["hint"] == "По порядку легенды"
