"""MCP UI discovery delegates to the settings-free shared catalog."""

import json

import pytest


@pytest.fixture
def mcp_server():
    from djust.mcp.server import create_server

    return create_server()


def _call_tool(server, tool_name, **kwargs):
    tool = server._tool_manager._tools.get(tool_name)
    assert tool is not None, f"Tool {tool_name} not registered"
    return json.loads(tool.fn(**kwargs))


def test_list_ui_components_without_query_lists_catalog(mcp_server):
    from djust.ai_discovery.catalog import load_catalog

    result = _call_tool(mcp_server, "list_ui_components")
    assert result["kind"] == "catalog" and result["count"] == len(load_catalog())


def test_list_ui_components_query_matches_suggest(mcp_server):
    from djust.ai_discovery.catalog import search

    query = "table with filters"
    result = _call_tool(mcp_server, "list_ui_components", query=query)
    assert [e["name"] for e in result["results"]] == [e.name for e, _ in search(query, 10)]


def test_get_ui_component_full_entry(mcp_server):
    result = _call_tool(mcp_server, "get_ui_component", name="data_table")
    assert result["version"] == 1 and result["kind"] == "component"
    assert result["variants"] and result["load"] == "{% load djust_components %}"
    assert set(result["required_props"]) >= {"rows", "columns"}


def test_get_ui_component_child_tag_returns_parent(mcp_server):
    assert _call_tool(mcp_server, "get_ui_component", name="tab")["name"] == "tabs"


def test_get_ui_component_unknown_suggests_names(mcp_server):
    result = _call_tool(mcp_server, "get_ui_component", name="data_tabel")
    assert "Unknown component" in result["error"] and "data_table" in result["did_you_mean"]


def test_ui_tools_need_no_django(mcp_server, monkeypatch):
    def forbidden():
        raise AssertionError("Django setup called")

    monkeypatch.setattr("djust.mcp.server._ensure_django", forbidden)
    assert _call_tool(mcp_server, "list_ui_components")["count"] > 0
    assert _call_tool(mcp_server, "get_ui_component", name="modal")["name"] == "modal"
