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
