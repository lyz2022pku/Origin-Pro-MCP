"""Regression tests for issues found during independent release review."""
import json
import re

import pytest

from origin_pro_mcp.tools import graph, graph_edit, figure_templates
from origin_pro_mcp.tools.graph_context import layer_scope


@pytest.mark.parametrize("bold_first", [True, False])
def test_title_bold_is_independent_of_json_key_order(fake_origin, monkeypatch, bold_first):
    state = {"xb.text$": "Old title"}
    def write(name, command):
        match = re.fullmatch(r'xb.text\$=(".*");', command)
        if match:
            state["xb.text$"] = match[1][1:-1]
        return True
    monkeypatch.setattr(graph_edit, "graph_layer_execute", write)
    monkeypatch.setattr(graph_edit, "read_string", lambda name, expr: state.get(expr))
    props = {"title_bold": True, "title": "New title"} if bold_first else {"title": "New title", "title_bold": True}
    result = json.loads(graph_edit.configure_layer("Graph1", json.dumps({"axes": {"x": props}})))
    assert state["xb.text$"] == r"\b(New title)"
    assert not result["not_applied"]


def test_top_title_uses_explicit_layer(fake_origin, monkeypatch):
    graph.add_second_y_axis("Graph1", "Book1", "Sheet1", 1, 2)
    monkeypatch.setattr(graph, "get_lt_str", lambda expr: "Inset distance")
    with layer_scope("Graph1", layer_id="mcpRightY"):
        message = graph._set_border_axis_title_impl("Graph1", "top", "Inset distance")
    assert "layer 2" in message


def test_dual_template_forwards_explicit_right_error(fake_origin, monkeypatch):
    data = {"series": [{"data_book": "Book1", "data_sheet": "Sheet1", "x_col": 1, "y_col": 2}],
            "right": {"data_book": "Book1", "data_sheet": "Sheet1", "x_col": 1, "y_col": 4, "error_col": 3, "error_kind": "SEM"}}
    monkeypatch.setattr(figure_templates, "graph_names", lambda: [])
    monkeypatch.setattr(graph, "create_graph", lambda *a, **kw: json.dumps({"name": "Graph1"}))
    calls = []
    monkeypatch.setattr(graph, "add_second_y_axis", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(graph_edit, "inspect_graph", lambda *a: json.dumps({"layers": []}))
    figure_templates.apply_figure_template("Graph1", "dual_y", json.dumps(data))
    assert calls[0].get("y_error_col") == 3


def test_right_error_binding_is_repeatable(fake_origin, monkeypatch):
    monkeypatch.setattr(graph, "_designate_error_column", lambda *a: None)
    for _ in range(2):
        graph.add_second_y_axis("Graph1", "Book1", "Sheet1", 1, 2, y_error_col=3)
    layer = fake_origin.graphs[0].Layers.Item(1)
    assert [layer.DataPlots.Item(i).Name for i in range(layer.DataPlots.Count)] == ["Book1_B", "Book1_C"]
    assert fake_origin.graphs[0].Layers.Count == 2


def test_attach_error_uses_selected_layer(fake_origin, monkeypatch):
    graph.add_second_y_axis("Graph1", "Book1", "Sheet1", 1, 2)
    calls = []
    monkeypatch.setattr(graph, "execute_labtalk", lambda command: calls.append(command) or True)
    monkeypatch.setattr(graph, "settle_new_plots", lambda *a, **kw: None)
    before = [{"name": "Book1_B", "is_error": False}]
    after = before + [{"name": "Book1_C", "is_error": True}]
    reads = iter([before, after])
    monkeypatch.setattr(graph, "get_plot_info", lambda *a: next(reads))
    fake_origin.lt_vars["__mcp_ct"] = 3
    graph.set_error_bars("Graph1", "Book1", "Sheet1", 2, 3, layer_id="mcpRightY")
    assert any("ogl:=[Graph1]2!" in c for c in calls)
    assert not any("Layer1" in c for c in calls)
    reassignment = next(c for c in calls if "set __mcp_er" in c)
    assert "page.active=2;" in reassignment


def test_reused_right_layer_checks_actual_x_before_geometry_changes(fake_origin):
    graph.add_second_y_axis("Graph1", "Book1", "Sheet1", 1, 2)
    layer = fake_origin.graphs[0].Layers.Item(1)
    layer._x_binding = "Book1_D"
    fake_origin.executed.clear()
    with pytest.raises(ValueError, match="actual X binding"):
        graph.add_second_y_axis("Graph1", "Book1", "Sheet1", 1, 2)
    assert not any("laylink" in c or "layer.left=" in c for c in fake_origin.executed)
