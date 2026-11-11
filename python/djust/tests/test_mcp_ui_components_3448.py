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


def test_ui_component_kind_consistent_across_tools_and_suggest(mcp_server):
    from djust.ai_discovery.runner import render_suggest

    get = _call_tool(mcp_server, "get_ui_component", name="data_table")
    listed = _call_tool(mcp_server, "list_ui_components", query="data table")["results"][0]
    suggested = json.loads(render_suggest("data table", 1, as_json=True))["results"][0]
    catalog = _call_tool(mcp_server, "list_ui_components")
    summary = next(
        e
        for category in catalog["categories"]
        for e in category["components"]
        if e["name"] == "data_table"
    )
    assert all(e["component_kind"] == "tag" for e in (get, listed, suggested, summary))
    assert all("kind" not in e for e in (listed, suggested, summary))


def test_mcp_megabyte_query_is_bounded(mcp_server, monkeypatch):
    from djust.ai_discovery import catalog
    from djust.ai_discovery.catalog import MAX_QUERY_LENGTH, load_catalog

    load_catalog()
    query = ("table filters searchable dropdown " * 40000)[:1_048_576]
    # "Bounded" means the query is cut to 512 characters before any work, so the cost
    # cannot grow with it. Asserted as what the work is GIVEN, not how long it takes
    # (#3835): every string the tokenizer sees is at most MAX_QUERY_LENGTH characters,
    # and 256 KB of query and 1 MB of query make the same calls on the same lengths.
    seen = []
    real = catalog._tokens

    def spy(value):
        seen.append(len(value))
        return real(value)

    monkeypatch.setattr(catalog, "_tokens", spy)
    runs = {}
    for scale in (1, 4):
        seen.clear()
        _call_tool(mcp_server, "list_ui_components", query=query[: 262_144 * scale])
        runs[scale] = list(seen)
    assert runs[1], "the tokenizer was never reached, so nothing was measured"
    assert max(runs[4]) <= MAX_QUERY_LENGTH, max(runs[4])
    assert runs[1] == runs[4]
    monkeypatch.setattr(catalog, "_tokens", real)
    result = _call_tool(mcp_server, "list_ui_components", query=query)
    assert result["query"] == query[:512]
    assert (
        result["results"]
        == _call_tool(mcp_server, "list_ui_components", query=query[:512])["results"]
    )
