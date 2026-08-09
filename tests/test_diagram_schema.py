"""Схема DiagramSpec + каталог типов (htmlslides/diagrams)."""
import json

import pytest

from htmlslides.diagrams import (AVAILABLE_KINDS, CANVAS_H, CANVAS_W,
                                 DiagramValidationError, catalog_for_ui,
                                 get_type, parse_diagram, sample_spec,
                                 spec_json, validate_diagram)


# ── сэмплы каталога = живой контракт ─────────────────────────────────────────
def test_available_kinds_all_waves():
    assert AVAILABLE_KINDS == ("flowchart", "process", "cycle", "funnel",
                               "hierarchy", "matrix", "pyramid", "hub_spoke",
                               "comparison", "venn", "swimlanes", "gantt_lite",
                               "mindmap", "network", "steps")


@pytest.mark.parametrize("kind", AVAILABLE_KINDS)
def test_every_sample_is_valid(kind):
    spec = parse_diagram(sample_spec(kind))
    assert spec.kind == kind


def test_sample_spec_returns_copy():
    a = sample_spec("flowchart")
    a["nodes"][0]["label"] = "MUTATED"
    assert sample_spec("flowchart")["nodes"][0]["label"] != "MUTATED"


def test_sample_spec_unknown_raises():
    with pytest.raises(KeyError):
        sample_spec("nonsense")


def test_catalog_for_ui_shape():
    cat = catalog_for_ui()
    assert len(cat) == 15
    by_kind = {c["kind"]: c for c in cat}
    assert by_kind["flowchart"]["available"] is True
    assert by_kind["flowchart"]["display_name"] == "Блок-схема"
    assert by_kind["matrix"]["available"] is True
    assert by_kind["gantt_lite"]["available"] is True
    assert all(c["when_to_use"] for c in cat)
    # весь каталог реализован: карточек «скоро» больше нет
    assert all(c["available"] for c in cat)


def test_get_type_unknown_raises():
    assert get_type("cycle").display_name == "Цикл"
    with pytest.raises(KeyError):
        get_type("nope")


# ── parse_diagram: форма ─────────────────────────────────────────────────────
def test_parse_accepts_json_string():
    spec = parse_diagram(json.dumps(sample_spec("process")))
    assert spec.kind == "process"


def test_parse_bad_json_string():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram("{не json")
    assert any("JSON" in e for e in ei.value.errors)


def test_parse_unknown_kind():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "sankey", "nodes": [{"id": "a"}]})


def test_parse_caps_nodes():
    nodes = [{"id": f"n{i}"} for i in range(13)]   # MAX_NODES = 12
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "process", "nodes": nodes})


def test_parse_extra_keys_forbidden():
    raw = sample_spec("process")
    raw["hacker"] = 1
    with pytest.raises(DiagramValidationError):
        parse_diagram(raw)


def test_parse_label_cap():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "process",
                       "nodes": [{"id": "a", "label": "x" * 61}]})


# ── семантика ────────────────────────────────────────────────────────────────
def test_duplicate_node_id():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "process",
                       "nodes": [{"id": "a"}, {"id": "a"}]})
    assert any("дублирующийся" in e for e in ei.value.errors)


def test_edge_to_missing_node():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "flowchart",
                       "nodes": [{"id": "a"}, {"id": "b"}],
                       "edges": [{"from": "a", "to": "ghost"}]})
    assert any("несуществующий" in e for e in ei.value.errors)


def test_self_loop_rejected():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "flowchart", "nodes": [{"id": "a"}],
                       "edges": [{"from": "a", "to": "a"}]})


def test_flowchart_needs_edges_for_multi_nodes():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "flowchart",
                       "nodes": [{"id": "a", "label": "Раз"},
                                 {"id": "b", "label": "Два"}]})
    assert any("ребро" in e for e in ei.value.errors)
    # один узел — можно без рёбер
    parse_diagram({"kind": "flowchart",
                   "nodes": [{"id": "a", "label": "Раз"}]})


def test_nodes_without_label_are_rejected():
    """Пустой label проходит по форме (у поля есть дефолт), но на слайде это
    пустая плашка. Претензия нужна, чтобы филлер починил её ретраем."""
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "process",
                       "nodes": [{"id": "a", "label": "Раз"},
                                 {"id": "b"},
                                 {"id": "c", "label": "   "}]})
    claim = [e for e in ei.value.errors if "без подписи" in e]
    assert len(claim) == 1 and "'b'" in claim[0] and "'c'" in claim[0]


def test_cycle_min_three():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "cycle", "nodes": [{"id": "a"}, {"id": "b"}]})


def test_funnel_min_two():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "funnel", "nodes": [{"id": "a"}]})


def test_hierarchy_multi_parent_rejected():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "hierarchy",
                       "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
                       "edges": [{"from": "a", "to": "c"},
                                 {"from": "b", "to": "c"}]})
    assert any("родител" in e for e in ei.value.errors)


def test_hierarchy_parent_cycle_rejected():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "hierarchy",
                       "nodes": [{"id": "a"}, {"id": "b"}],
                       "edges": [{"from": "a", "to": "b"},
                                 {"from": "b", "to": "a"}]})
    assert any("цикл" in e for e in ei.value.errors)


def test_matrix_requires_exactly_four_nodes():
    raw = sample_spec("matrix")
    raw["nodes"] = raw["nodes"][:3]
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram(raw)
    assert any("ровно 4" in e for e in ei.value.errors)


def test_pyramid_min_three():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "pyramid", "nodes": [{"id": "a"}, {"id": "b"}]})


def test_hub_spoke_min_three():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "hub_spoke", "nodes": [{"id": "a"}, {"id": "b"}]})


def test_comparison_lane_rules():
    # без lane — ошибка
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "comparison",
                       "nodes": [{"id": "a", "lane": "Слева"}, {"id": "b"}]})
    assert any("lane" in e for e in ei.value.errors)
    # три стороны — ошибка
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "comparison",
                       "nodes": [{"id": "a", "lane": "X"},
                                 {"id": "b", "lane": "Y"},
                                 {"id": "c", "lane": "Z"}]})
    assert any("ровно 2 стороны" in e for e in ei.value.errors)


def test_venn_two_or_three():
    with pytest.raises(DiagramValidationError):
        parse_diagram({"kind": "venn", "nodes": [{"id": "a"}]})
    raw = sample_spec("venn")
    raw["nodes"] = [{"id": f"v{i}"} for i in range(4)]
    with pytest.raises(DiagramValidationError):
        parse_diagram(raw)


def test_swimlanes_lane_rules():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "swimlanes",
                       "nodes": [{"id": "a", "lane": "Инженер"}, {"id": "b"}]})
    assert any("lane" in e for e in ei.value.errors)
    # одна дорожка — мало
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "swimlanes",
                       "nodes": [{"id": "a", "lane": "X"},
                                 {"id": "b", "lane": "X"}]})
    assert any("дорожек" in e for e in ei.value.errors)


def test_gantt_requires_duration():
    """value = ширина полосы. Без числа работу нечем рисовать, поэтому это
    претензия, а не «нулевая длительность»."""
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "gantt_lite",
                       "nodes": [{"id": "a", "label": "Аудит", "value": "2"},
                                 {"id": "b", "label": "Пилот"}]})
    claim = [e for e in ei.value.errors if "длительность" in e]
    assert len(claim) == 1 and "'b'" in claim[0] and "'a'" not in claim[0]


def test_gantt_duration_accepts_units_and_comma():
    spec = parse_diagram({"kind": "gantt_lite",
                          "nodes": [{"id": "a", "label": "Аудит", "value": "2 мес"},
                                    {"id": "b", "label": "Пилот", "value": "1,5"}]})
    assert [n.value for n in spec.nodes] == ["2 мес", "1,5"]


def test_gantt_row_cap():
    nodes = [{"id": f"g{i}", "label": f"Работа {i}", "value": "1"}
             for i in range(9)]          # MAX_GANTT_ROWS = 8
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "gantt_lite", "nodes": nodes})
    assert any("до 8 работ" in e for e in ei.value.errors)


def test_steps_cap():
    """Кап ступеней ниже общего MAX_NODES: 12 ступеней на 1800px нечитаемы."""
    nodes = [{"id": f"s{i}", "label": f"Уровень {i}"} for i in range(7)]
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "steps", "nodes": nodes})
    assert any("до 6 ступеней" in e for e in ei.value.errors)
    parse_diagram({"kind": "steps", "nodes": nodes[:6]})


def test_mindmap_center_cannot_be_child():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "mindmap",
                       "nodes": [{"id": "c", "label": "Центр"},
                                 {"id": "a", "label": "Раз"},
                                 {"id": "b", "label": "Два"}],
                       "edges": [{"from": "a", "to": "c"}]})
    assert any("не может быть подветвью" in e for e in ei.value.errors)


def test_mindmap_multi_parent_rejected():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "mindmap",
                       "nodes": [{"id": "c", "label": "Центр"},
                                 {"id": "a", "label": "Раз"},
                                 {"id": "b", "label": "Два"},
                                 {"id": "d", "label": "Три"}],
                       "edges": [{"from": "a", "to": "d"},
                                 {"from": "b", "to": "d"}]})
    assert any("родител" in e for e in ei.value.errors)


def test_mindmap_flat_map_allowed():
    """Карта БЕЗ рёбер — законный плоский веер ветвей вокруг центра."""
    parse_diagram({"kind": "mindmap",
                   "nodes": [{"id": "c", "label": "Центр"},
                             {"id": "a", "label": "Раз"},
                             {"id": "b", "label": "Два"}]})


def test_mindmap_mixed_orphans_rejected():
    """Есть рёбра, но часть узлов без них — двухуровневая карта схлопывается в
    плоский веер: модель забыла связи, и это надо переспросить, а не рисовать."""
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "mindmap",
                       "nodes": [{"id": "c", "label": "Центр"},
                                 {"id": "a", "label": "Раз"},
                                 {"id": "a1", "label": "Раз-один"},
                                 {"id": "b", "label": "Два"}],
                       "edges": [{"from": "a", "to": "a1"}]})
    assert any("не ведёт ни одно ребро" in e for e in ei.value.errors)


def test_network_requires_edges():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "network",
                       "nodes": [{"id": "a", "label": "Раз"},
                                 {"id": "b", "label": "Два"},
                                 {"id": "c", "label": "Три"}]})
    assert any("хотя бы одну связь" in e for e in ei.value.errors)


def test_network_isolated_node_rejected():
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "network",
                       "nodes": [{"id": "a", "label": "Раз"},
                                 {"id": "b", "label": "Два"},
                                 {"id": "c", "label": "Три"}],
                       "edges": [{"from": "a", "to": "b"}]})
    claim = [e for e in ei.value.errors if "вне схемы" in e]
    assert len(claim) == 1 and "'c'" in claim[0]


def test_flowchart_sample_contains_back_edge():
    # сэмпл каталога намеренно содержит цикл fix→check — layout обязан его пережить
    raw = sample_spec("flowchart")
    assert {"from": "fix", "to": "check"} in [
        {"from": e["from"], "to": e["to"]} for e in raw["edges"]]


# ── offsets ──────────────────────────────────────────────────────────────────
def test_offsets_clamped_to_canvas():
    raw = sample_spec("process")
    raw["offsets"] = {"s1": {"dx": 99999, "dy": -99999}}
    spec = parse_diagram(raw)
    assert spec.offsets["s1"].dx == CANVAS_W
    assert spec.offsets["s1"].dy == -CANVAS_H


def test_stale_offsets_silently_dropped():
    raw = sample_spec("process")
    raw["offsets"] = {"s1": {"dx": 10, "dy": 0}, "deleted": {"dx": 5, "dy": 5}}
    out = validate_diagram(raw)
    assert out is not None
    assert set(out["offsets"]) == {"s1"}


# ── validate_diagram / spec_json ─────────────────────────────────────────────
def test_validate_diagram_none_on_garbage():
    assert validate_diagram({"kind": "process", "nodes": []}) is None
    assert validate_diagram("мусор") is None
    assert validate_diagram(42) is None


def test_validate_diagram_uses_from_alias():
    out = validate_diagram(sample_spec("flowchart"))
    assert "from" in out["edges"][0] and "from_" not in out["edges"][0]


def test_spec_json_compact_and_fits_slot():
    for kind in AVAILABLE_KINDS:
        s = spec_json(sample_spec(kind))
        json.loads(s)                       # валидный JSON
        assert ": " not in s and ", " not in s   # компактные сепараторы
        assert len(s) < 8000                # слот-контракт diagram (max_chars)


def test_spec_json_raises_on_invalid():
    with pytest.raises(DiagramValidationError):
        spec_json({"kind": "cycle", "nodes": [{"id": "a"}]})


def test_worst_case_spec_fits_slot_cap():
    """Худший разрешённый случай (капы схемы) обязан влезать в max_chars=8000:
    draft_render._coerce_slot молча режет текст по капу, обрезанный JSON =
    сломанный слайд."""
    nodes = [{"id": f"node-{i:02d}", "label": "Щ" * 60, "shape": "decision",
              "accent": True, "value": "9" * 12, "lane": "Д" * 40, "level": 11}
             for i in range(12)]
    edges = ([{"from": f"node-{i:02d}", "to": f"node-{(i + 1) % 12:02d}",
               "label": "х" * 30, "style": "dashed"} for i in range(12)]
             + [{"from": f"node-{i:02d}", "to": f"node-{(i + 2) % 12:02d}",
                 "label": "х" * 30, "style": "dashed"} for i in range(8)])
    raw = {"kind": "flowchart", "direction": "down", "nodes": nodes,
           "edges": edges,
           "groups": [{"id": f"g{i}", "label": "Г" * 40} for i in range(5)],
           "meta": {"x_axis": "х" * 40, "y_axis": "у" * 40,
                    "center_label": "ц" * 60},
           "offsets": {f"node-{i:02d}": {"dx": -1799.5, "dy": 719.25}
                       for i in range(12)}}
    s = spec_json(raw)
    assert len(s) < 8000, f"канонический JSON {len(s)} симв. не влезает в слот"


def test_node_outside_the_graph_is_rejected():
    """Живой прогон: модель забыла ребро «Подготовка → Услуга подключена», и
    узел молча сел в нулевой ранг рядом со стартом — на слайде это выглядело
    поломкой раскладки. Претензия переводит потерю в ретрай филлера."""
    with pytest.raises(DiagramValidationError) as ei:
        parse_diagram({"kind": "flowchart",
                       "nodes": [{"id": "a", "label": "Заявка"},
                                 {"id": "b", "label": "Проверка"},
                                 {"id": "end", "label": "Подключено"}],
                       "edges": [{"from": "a", "to": "b"}]})
    claim = [e for e in ei.value.errors if "вне схемы" in e]
    assert len(claim) == 1 and "'end'" in claim[0]


def test_edgeless_kinds_keep_their_nodes():
    """Порядок-по-списку — законный режим: у process/swimlanes без рёбер все
    узлы «одиночки», и претензия там была бы ложной."""
    parse_diagram({"kind": "process",
                   "nodes": [{"id": "a", "label": "Раз"},
                             {"id": "b", "label": "Два"}]})
    parse_diagram({"kind": "swimlanes",
                   "nodes": [{"id": "a", "label": "Раз", "lane": "Продажи"},
                             {"id": "b", "label": "Два", "lane": "Юристы"}]})
