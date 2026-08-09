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


# ── diagram ──────────────────────────────────────────────────────────────────
def _diagram_fields():
    from htmlslides.diagrams import sample_spec
    return {"heading": "Как проходит заявка", "subtitle": "Путь клиента",
            "diagram": sample_spec("flowchart")}


def test_validate_diagram_ok_normalises_spec():
    out = st.validate_fields("diagram", _diagram_fields())
    assert out["heading"] == "Как проходит заявка"
    assert out["diagram"]["kind"] == "flowchart"
    assert "from" in out["diagram"]["edges"][0]   # алиас, не from_


def test_validate_diagram_bad_spec_returns_none():
    fields = _diagram_fields()
    fields["diagram"] = {"kind": "flowchart", "nodes": []}
    assert st.validate_fields("diagram", fields) is None
    fields["diagram"] = "не спек"
    assert st.validate_fields("diagram", fields) is None


def test_verbose_keeps_diagram_claims():
    """Претензии схемы обязаны доехать до эндпоинта: ручная правка ломает схему
    по смыслу (удалили последнюю связь), и «invalid fields» без деталей —
    молчаливый «error» в панели, из которого не видно, что чинить."""
    fields = _diagram_fields()
    fields["diagram"] = {"kind": "flowchart",
                         "nodes": [{"id": "a", "label": "Раз"},
                                   {"id": "b", "label": "Два"}],
                         "edges": []}
    norm, claims = st.validate_fields_verbose("diagram", fields)
    assert norm is None
    assert any("ребро" in c for c in claims), claims


def test_verbose_names_the_broken_field_for_plain_types():
    norm, claims = st.validate_fields_verbose("bullets", {"bullets": ["a"]})
    assert norm is None
    assert any(c.startswith("heading:") for c in claims), claims
    assert st.validate_fields_verbose("quote", {})[1] == [
        "неизвестный тип слайда: 'quote'"]
    assert st.validate_fields_verbose("title", "nope")[1] == [
        "поля слайда должны быть объектом"]


def test_map_diagram_serialises_compact_json():
    import json
    tid, content = st.map_typed("diagram", _diagram_fields())
    assert tid == "diagram"
    assert content["title"] == "Как проходит заявка"
    assert content["subtitle"] == "Путь клиента"
    spec = json.loads(content["diagram"])
    assert spec["kind"] == "flowchart" and len(spec["nodes"]) == 6
    assert ": " not in content["diagram"]         # компактные сепараторы


def test_map_diagram_invalid_falls_back():
    assert st.map_typed("diagram", {"heading": "H", "diagram": {}}) == (None, {})


def test_typed_from_content_round_trips_filler_output():
    """Контент филлера (спек — JSON-строкой) возвращается в typed-поля: без этого
    ИИ-схема в черновике правилась только сырым JSON, без панели узлов и drag."""
    _, content = st.map_typed("diagram", _diagram_fields())
    out = st.typed_from_content("diagram", content)
    assert out is not None
    kind, fields = out
    assert kind == "diagram"
    assert fields["heading"] == "Как проходит заявка"
    assert fields["subtitle"] == "Путь клиента"
    assert fields["diagram"] == st.validate_fields("diagram",
                                                   _diagram_fields())["diagram"]


def test_typed_from_content_accepts_dict_spec():
    content = {"title": "Как проходит заявка",
               "diagram": _diagram_fields()["diagram"]}
    kind, fields = st.typed_from_content("diagram", content)
    assert kind == "diagram" and fields["subtitle"] == ""


def test_typed_from_content_ignores_non_diagrams():
    assert st.typed_from_content("cards-6", {"title": "H"}) is None
    assert st.typed_from_content("diagram", {"title": "H"}) is None
    assert st.typed_from_content("diagram", {"diagram": "не json"}) is None
    assert st.typed_from_content("diagram", {"diagram": {"kind": "flowchart"}}) is None
