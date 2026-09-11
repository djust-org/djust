"""``TableComponent`` filtering (#2782): a global filter input
(``filterable=True``) and per-column filter inputs (``{"filterable": True}`` on
a column), composing with each other, with the sort, and with selection.

The contract MIRRORS ``DataTableMixin`` / ``{% data_table %}`` so the two tables
do not drift (#1646):

- global search: ``on_table_search(value)`` stores ``str(value)`` and matches
  ``icontains`` across fields (``mixins/data_table.py`` ``_apply_table_search``);
  here ``filter_rows(value)`` matches ANY column case-insensitively.
- per-column: ``on_table_filter(value, column)`` sets ``table_filters[column]``
  or POPS it on an empty value (``_apply_table_filters`` is ``icontains`` per
  key); here ``filter_column(value, column)`` does the same on
  ``column_filters``.
- order: ``refresh_table`` runs search -> filter -> sort; ``_visible_rows``
  filters then sorts.
- select-all: ``on_table_select("__all__")`` selects ``table_rows`` — the
  POST-FILTER set; ``toggle_all`` selects the visible rows.
- controls: the tag's search box is ``dj-input`` + ``dj-debounce="300"`` with
  ``role="searchbox" aria-label="Search table"``; its filter row is a second
  ``<thead>`` row of ``aria-label="Filter {label}"`` inputs carrying
  ``data-column`` (``rust_handlers.py`` search bar / ``filter_cells``).

The reproducers send exactly what the client builds from the RENDERED input
(``dj-input`` → ``{value, <data-*>}``, ``data-component-id`` → ``component_id``)
over a real ``WebsocketCommunicator``. Harness lifted from
``test_table_selection_2779.py``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust import config as config_module
from djust.components.data.table import TableComponent

_MODULE = "djust.tests.test_table_filter_2782"
_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")

_COLUMNS = [
    {"key": "id", "label": "ID", "sortable": True},
    {"key": "name", "label": "Name", "sortable": True, "filterable": True},
    {"key": "email", "label": "Email", "filterable": True},
]
_ROWS = [
    {"id": 2, "name": "Zed", "email": "zed@x.io"},
    {"id": 3, "name": "Amy", "email": "amy@y.io"},
    {"id": 1, "name": "Mia", "email": "mia@x.io"},
    {"id": 4, "name": "Max", "email": "max@y.io"},
]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect_and_mount(view_path: str, url: str = "/t/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


_TAG = re.compile(r"<[a-zA-Z][^>]*>")
_ATTR = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)="([^"]*)"')


def _attrs(tag: str) -> Dict[str, str]:
    return dict(_ATTR.findall(tag))


def _inputs(html: str, dj_input: str) -> list:
    return [t for t in _TAG.findall(html) if _attrs(t).get("dj-input") == dj_input]


def _global_input(html: str) -> str:
    found = _inputs(html, "filter_rows")
    assert len(found) == 1, f"expected exactly one global filter input in: {html}"
    return found[0]


def _column_input(html: str, key: str) -> str:
    for t in _inputs(html, "filter_column"):
        if _attrs(t).get("data-column") == key:
            return t
    raise AssertionError(f"no column filter input for {key!r} in: {html}")


def _client_input_params(tag: str, typed: str) -> Dict[str, Any]:
    """The params the client sends for a ``dj-input`` on ``tag`` after the user
    typed ``typed``: every ``data-*`` as a snake_case key, ``data-component-id``
    → ``component_id``, and ``value`` = the input's text (``09-event-binding.js``
    ``value: e.target.value``)."""
    params: Dict[str, Any] = {}
    for name, value in _attrs(tag).items():
        if not name.startswith("data-"):
            continue
        if name == "data-component-id":
            params["component_id"] = value
            continue
        params[name[5:].replace("-", "_")] = value
    params["value"] = typed
    return params


def _framework(framework: str):
    """Patch ONLY ``css_framework``; every other key keeps its real value."""
    real_get = config_module.config.get

    def _get(key, default=None):
        return framework if key == "css_framework" else real_get(key, default)

    return patch.object(config_module.config, "get", _get)


def _render_under(component, framework: str) -> str:
    with _framework(framework):
        return str(component.render())


def _table(**kw: Any) -> TableComponent:
    kw.setdefault("component_id", "t1")
    kw.setdefault("columns", [dict(c) for c in _COLUMNS])
    kw.setdefault("rows", [dict(r) for r in _ROWS])
    kw.setdefault("filterable", True)
    kw.setdefault("selectable", True)
    return TableComponent(**kw)


def _visible_names(t: TableComponent) -> list:
    return [r["name"] for r in t._visible_rows()]  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# Host view
# ---------------------------------------------------------------------------


class FilterableTableHostView(LiveView):
    """Renders the table plus ``vis=`` (visible row ids, in render order) and
    ``sel=`` (``selected_rows``) summaries for the assertions."""

    template = (
        f'<div dj-root dj-view="{_MODULE}.FilterableTableHostView" dj-id="0">'
        "<p>vis={{ vis }}</p><p>sel={{ sel }}</p>{{ table.render }}</div>"
    )

    def mount(self, request, **kwargs):
        self.table = _table()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["vis"] = ",".join(self.table._row_ids()) or "-"  # pylint: disable=protected-access
        ctx["sel"] = ",".join(self.table.selected_rows) or "-"
        return ctx


async def _open(framework: str):
    from django.test import override_settings

    with _framework(framework):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
            communicator, mount_frame = await _connect_and_mount(
                f"{_MODULE}.FilterableTableHostView"
            )
    return communicator, mount_frame


async def _type(communicator, tag: str, typed: str, ref: int = 1):
    params = _client_input_params(tag, typed)
    await communicator.send_json_to(
        {"type": "event", "event": _attrs(tag)["dj-input"], "params": params, "ref": ref}
    )
    return await communicator.receive_json_from(timeout=3)


async def _send(communicator, event: str, params: Optional[Dict[str, Any]] = None, ref: int = 9):
    await communicator.send_json_to(
        {
            "type": "event",
            "event": event,
            "params": {"component_id": "t1", **(params or {})},
            "ref": ref,
        }
    )
    return await communicator.receive_json_from(timeout=3)


def _summary(frame: Dict[str, Any], name: str) -> str:
    m = re.search(rf"{name}=([^<]*)<", frame.get("html", ""))
    assert m, frame
    return m.group(1)


def _body_rows(html: str) -> list:
    """``dj-value-row-id`` of every rendered body checkbox, in order.

    Row identity rides ``dj-value-row-id``, not ``data-row-id`` (#2781): a
    checkbox's ``dj-change`` runs the client's form-event path
    (``buildFormEventParams``), which never reads ``data-*`` — only
    ``dj-value-*``. ``data-row-id`` was the pre-#2781 shape.
    """
    return [
        _attrs(t)["dj-value-row-id"]
        for t in _TAG.findall(html)
        if 'type="checkbox"' in t and "dj-value-row-id" in t
    ]


# ---------------------------------------------------------------------------
# #2782 reproducers over a real WebSocket
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
class TestFilterOverWebSocket:
    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    async def test_global_query_narrows_then_clearing_restores(self, framework):
        with _framework(framework):
            communicator, mount_frame = await _open(framework)
            try:
                assert _summary(mount_frame, "vis") == "2,3,1,4"
                assert _body_rows(mount_frame["html"]) == ["2", "3", "1", "4"]
                tag = _global_input(mount_frame["html"])
                # Send exactly what the client builds from the rendered input.
                resp = await _type(communicator, tag, "M")
                assert resp.get("type") != "error", (
                    f"#2782 [{framework}]: the filter input must be handled; got {resp!r}"
                )
                # "M" matches Mia and Max by name, and amy@... / max@... by
                # email — any column, case-insensitively.
                assert _summary(resp, "vis") == "3,1,4", resp
                assert _body_rows(resp["html"]) == ["3", "1", "4"]
                # The re-rendered input carries the query.
                assert _attrs(_global_input(resp["html"]))["value"] == "M"
                # Clearing restores every row.
                resp2 = await _type(communicator, _global_input(resp["html"]), "", ref=2)
                assert _summary(resp2, "vis") == "2,3,1,4", resp2
                assert _attrs(_global_input(resp2["html"]))["value"] == ""
            finally:
                await communicator.disconnect()

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    async def test_column_filter_narrows_on_that_column_only(self, framework):
        with _framework(framework):
            communicator, mount_frame = await _open(framework)
            try:
                tag = _column_input(mount_frame["html"], "email")
                resp = await _type(communicator, tag, "X.IO")
                assert resp.get("type") != "error", (
                    f"#2782 [{framework}]: the column filter must be handled; got {resp!r}"
                )
                assert _summary(resp, "vis") == "2,1", resp  # zed@x.io, mia@x.io
                assert _attrs(_column_input(resp["html"], "email"))["value"] == "X.IO"
                assert _attrs(_column_input(resp["html"], "name"))["value"] == ""
                resp2 = await _type(communicator, _column_input(resp["html"], "email"), "", ref=2)
                assert _summary(resp2, "vis") == "2,3,1,4", resp2
            finally:
                await communicator.disconnect()

    async def test_global_and_column_filters_compose(self):
        communicator, mount_frame = await _open("plain")
        try:
            resp = await _type(communicator, _global_input(mount_frame["html"]), "m")
            assert _summary(resp, "vis") == "3,1,4"  # Amy (amy@), Mia, Max
            resp = await _type(communicator, _column_input(resp["html"], "name"), "i", ref=2)
            assert _summary(resp, "vis") == "1"  # Mia — the NAME must contain i
            resp = await _type(communicator, _column_input(resp["html"], "name"), "a", ref=3)
            assert _summary(resp, "vis") == "3,1,4"  # Amy, Mia, Max
            resp = await _type(communicator, _column_input(resp["html"], "email"), "y", ref=4)
            assert _summary(resp, "vis") == "3,4"  # amy@y.io, max@y.io
            # Lifting the global filter keeps the column filters.
            resp = await _type(communicator, _global_input(resp["html"]), "", ref=5)
            assert _summary(resp, "vis") == "3,4"
            resp = await _type(communicator, _column_input(resp["html"], "email"), "", ref=6)
            assert _summary(resp, "vis") == "3,1,4"
            resp = await _type(communicator, _column_input(resp["html"], "name"), "", ref=7)
            assert _summary(resp, "vis") == "2,3,1,4"
        finally:
            await communicator.disconnect()

    async def test_filter_composes_with_sort_filter_then_sort(self):
        communicator, mount_frame = await _open("plain")
        try:
            resp = await _send(communicator, "sort_by", {"column": "name"})
            assert _summary(resp, "vis") == "3,4,1,2"  # Amy, Max, Mia, Zed
            resp = await _type(communicator, _global_input(resp["html"]), "m", ref=2)
            assert _summary(resp, "vis") == "3,4,1"  # sorted AND narrowed
            resp = await _send(communicator, "sort_by", {"column": "name"}, ref=3)  # → desc
            assert _summary(resp, "vis") == "1,4,3"
            resp = await _type(communicator, _global_input(resp["html"]), "", ref=4)
            assert _summary(resp, "vis") == "2,1,4,3"  # sort survives clearing
        finally:
            await communicator.disconnect()

    async def test_toggle_all_selects_only_the_visible_rows(self):
        """The ``{% data_table %}`` contract: ``__all__`` selects the
        post-filter rows (``on_table_select`` selects ``table_rows``)."""
        communicator, mount_frame = await _open("plain")
        try:
            resp = await _type(communicator, _global_input(mount_frame["html"]), "x.io")
            assert _summary(resp, "vis") == "2,1"
            resp = await _send(communicator, "toggle_all", ref=2)
            assert _summary(resp, "sel") == "2,1", resp
            # Clearing the filter shows the hidden rows UNselected.
            resp = await _type(communicator, _global_input(resp["html"]), "", ref=3)
            assert _summary(resp, "sel") == "2,1"
            assert _summary(resp, "vis") == "2,3,1,4"
            boxes = {
                _attrs(t)["dj-value-row-id"]: " checked" in t
                for t in _TAG.findall(resp["html"])
                if "dj-value-row-id" in t
            }
            assert boxes == {"2": True, "1": True, "3": False, "4": False}, boxes
        finally:
            await communicator.disconnect()


# ---------------------------------------------------------------------------
# Handler-level semantics
# ---------------------------------------------------------------------------


class TestFilterState:
    def test_global_matches_any_column_case_insensitively(self):
        t = _table()
        t.filter_rows(value="X.IO")
        assert _visible_names(t) == ["Zed", "Mia"]
        t.filter_rows(value="3")  # a non-string cell is matched via str()
        assert _visible_names(t) == ["Amy"]

    def test_rows_are_never_narrowed_so_clearing_restores(self):
        t = _table()
        t.filter_rows(value="zzz")
        assert _visible_names(t) == []
        assert len(t.rows) == 4
        t.filter_rows(value="")
        assert _visible_names(t) == ["Zed", "Amy", "Mia", "Max"]

    def test_column_filter_pops_on_empty_like_on_table_filter(self):
        t = _table()
        t.filter_column(value="a", column="name")
        assert t.column_filters == {"name": "a"}
        assert _visible_names(t) == ["Amy", "Mia", "Max"]
        t.filter_column(value="", column="name")
        assert t.column_filters == {}

    def test_column_filter_does_not_look_at_other_columns(self):
        t = _table()
        t.filter_column(value="zed", column="name")
        assert _visible_names(t) == ["Zed"]
        t.filter_column(value="zed@", column="name")  # only the email has "zed@"
        assert _visible_names(t) == []

    def test_filters_compose_as_an_and(self):
        t = _table()
        t.filter_rows(value="m")
        t.filter_column(value="y", column="email")
        assert _visible_names(t) == ["Amy", "Max"]
        t.filter_column(value="ma", column="name")
        assert _visible_names(t) == ["Max"]

    def test_filter_then_sort(self):
        t = _table(sort_column="name", sort_direction="desc")
        t.filter_rows(value="m")
        assert _visible_names(t) == ["Mia", "Max", "Amy"]

    def test_toggle_all_under_a_filter(self):
        t = _table(selected_rows=["3"])
        t.filter_rows(value="x.io")  # Zed (2), Mia (1) visible
        assert not t._all_selected()  # pylint: disable=protected-access
        t.toggle_all()
        assert t.selected_rows == ["2", "1"]  # the hidden 3 is not "all"
        t.toggle_all()  # every visible row selected → clear
        assert t.selected_rows == []

    def test_kwargs_seed_the_state(self):
        t = _table(filter_query="m", column_filters={"email": "y", "name": ""})
        assert t.filter_query == "m"
        assert t.column_filters == {"email": "y"}  # empty values dropped
        assert _visible_names(t) == ["Amy", "Max"]

    def test_state_is_in_the_component_context(self):
        t = _table(filter_query="q", column_filters={"name": "a"})
        ctx = t.get_context()
        assert ctx["filterable"] is True
        assert ctx["filter_query"] == "q"
        assert ctx["column_filters"] == {"name": "a"}


# ---------------------------------------------------------------------------
# Per-branch render pins
# ---------------------------------------------------------------------------


class TestRenderedFilterControlsCarryTheirRouting:
    """Every branch (#1646): the global input and each column input are
    ``dj-input`` controls routed to the table, debounced, labelled, and carrying
    the ``data-column`` their handler takes as ``column``."""

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_global_input(self, framework):
        html = _render_under(_table(filter_query='a"b'), framework)
        a = _attrs(_global_input(html))
        assert a["dj-input"] == "filter_rows"
        assert a["data-component-id"] == "t1"
        assert a["dj-debounce"] == "300"
        assert a["role"] == "searchbox"
        assert a["aria-label"] == "Search table"
        assert a["value"] == "a&quot;b"  # escaped
        # The input precedes the table, inside the component's root element.
        assert html.index('id="t1"') < html.index('dj-input="filter_rows"') < html.index("<table")

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_column_inputs(self, framework):
        html = _render_under(_table(column_filters={"email": "<x>"}), framework)
        cols = _inputs(html, "filter_column")
        assert [_attrs(t)["data-column"] for t in cols] == ["name", "email"], html
        for t in cols:
            a = _attrs(t)
            assert a["data-component-id"] == "t1", t
            assert a["dj-debounce"] == "300", t
            assert a["aria-label"] == f"Filter {a['data-column'].capitalize()}", t
        assert _attrs(_column_input(html, "email"))["value"] == "&lt;x&gt;"
        # A second <thead> row: one cell per column plus the checkbox column.
        thead = html[html.index("<thead") : html.index("</thead>")]
        filter_row = thead[thead.index('<tr class="dj-table-filters">') :]
        assert filter_row.count("<th") == 4, filter_row  # checkbox + 3 columns
        assert filter_row.count("<input") == 2, filter_row  # only the filterable ones

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_not_filterable_renders_no_filter_controls(self, framework):
        cols = [{k: v for k, v in c.items() if k != "filterable"} for c in _COLUMNS]
        html = _render_under(_table(filterable=False, columns=cols), framework)
        assert "dj-input" not in html
        assert "dj-table-filters" not in html
        assert html.count("<tr") == 1 + len(_ROWS)  # header row + body rows

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_filter_row_without_selection_has_no_leading_cell(self, framework):
        html = _render_under(_table(selectable=False), framework)
        filter_row = html[html.index('<tr class="dj-table-filters">') : html.index("</thead>")]
        assert filter_row.count("<th") == 3, filter_row

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_body_renders_only_the_visible_rows(self, framework):
        t = _table()
        t.filter_column(value="y", column="email")
        html = _render_under(t, framework)
        assert _body_rows(html) == ["3", "4"]
        assert "Zed" not in html and "Mia" not in html


__all__: Tuple[str, ...] = ("FilterableTableHostView",)
