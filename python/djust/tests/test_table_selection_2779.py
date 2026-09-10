"""``TableComponent(selectable=True)`` checkboxes must select rows (#2779), and
a sortable column must SAY it is sortable (#2778).

#2779, symptom-up: the row checkbox rendered as a bare
``<input type="checkbox" class="form-check-input">`` — no ``dj-change``, no
``data-component-id``, no identity — and the component had no ``toggle_row`` /
``toggle_all`` at all, so ``selected_rows`` could never change. The #2776 pin
(``test_ui_dismiss_handlers_2756.py``) could not see it: it walks rendered
``dj-*`` controls, and an unwired checkbox renders none. This PR adds the
complementary pin (``test_every_rendered_checkbox_is_wired``) there.

Row identity mirrors ``{% data_table %}`` / ``DataTableMixin``: the row's
``row_key`` value (default ``"id"``) as a string — ``rust_handlers.py`` compares
``str(row.get(row_key))`` against ``{str(v) for v in selected_rows}`` and
``on_table_select`` stores ``str(value)``; the component now does the same, so
a ``selected_rows`` list moves between the two tables unchanged.

The reproducers build the event the way the client does from the RENDERED
control (``data-*`` → params, ``data-component-id`` → ``component_id``, and for
``dj-change`` the checkbox's ``checked`` as ``value`` — ``09-event-binding.js``
line ~756), over a real ``WebsocketCommunicator``. Harness lifted from
``test_table_sort_handler_2776.py``.
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

_MODULE = "djust.tests.test_table_selection_2779"
_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")

_COLUMNS = [
    {"key": "id", "label": "ID", "sortable": True},
    {"key": "name", "label": "Name", "sortable": True},
    {"key": "email", "label": "Email"},
]
_ROWS = [
    {"id": 2, "name": "Zed", "email": "z@x"},
    {"id": 3, "name": "Amy", "email": "a@x"},
    {"id": 1, "name": "Mia", "email": "m@x"},
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


def _checkboxes(html: str) -> list:
    return [t for t in _TAG.findall(html) if 'type="checkbox"' in t]


def _row_checkbox(html: str, row_id: str) -> str:
    for t in _checkboxes(html):
        if _attrs(t).get("data-row-id") == row_id:
            return t
    raise AssertionError(f"no row checkbox for {row_id!r} in: {html}")


def _header_checkbox(html: str) -> str:
    for t in _checkboxes(html):
        if "data-row-id" not in t:
            return t
    raise AssertionError(f"no header checkbox in: {html}")


def _client_change_params(tag: str) -> Dict[str, Any]:
    """The params the client sends for a ``dj-change`` on ``tag``: every
    ``data-*`` as a snake_case key, ``data-component-id`` → ``component_id``
    (ONLY if present), and ``value`` = the checkbox's toggled ``checked``
    (``09-event-binding.js``: ``e.target.type === 'checkbox' ? e.target.checked
    : e.target.value``)."""
    params: Dict[str, Any] = {}
    attrs = _attrs(tag)
    for name, value in attrs.items():
        if not name.startswith("data-"):
            continue
        if name == "data-component-id":
            params["component_id"] = value
            continue
        params[name[5:].replace("-", "_")] = value
    params["value"] = " checked" not in tag  # the click flips it
    return params


def _framework(framework: str):
    """Patch ONLY ``css_framework``; every other key keeps its real value (a
    blanket patch turns ``max_message_size`` into a string, #2779 first run)."""
    real_get = config_module.config.get

    def _get(key, default=None):
        return framework if key == "css_framework" else real_get(key, default)

    return patch.object(config_module.config, "get", _get)


def _render_under(component, framework: str) -> str:
    with _framework(framework):
        return str(component.render())


def _table(**kw: Any) -> TableComponent:
    kw.setdefault("component_id", "t1")
    kw.setdefault("columns", list(_COLUMNS))
    kw.setdefault("rows", [dict(r) for r in _ROWS])
    kw.setdefault("selectable", True)
    return TableComponent(**kw)


# ---------------------------------------------------------------------------
# Host view
# ---------------------------------------------------------------------------


class SelectableTableHostView(LiveView):
    """Renders the table (so the response HTML carries ``checked``) and a
    ``sel=`` summary of ``selected_rows`` for the assertions."""

    template = (
        f'<div dj-root dj-view="{_MODULE}.SelectableTableHostView" dj-id="0">'
        "<p>sel={{ sel }}</p>{{ table.render }}</div>"
    )

    def mount(self, request, **kwargs):
        self.table = _table()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["sel"] = ",".join(self.table.selected_rows) or "-"
        return ctx


async def _open(framework: str):
    from django.test import override_settings

    with _framework(framework):
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
            communicator, mount_frame = await _connect_and_mount(
                f"{_MODULE}.SelectableTableHostView"
            )
    return communicator, mount_frame


async def _change(communicator, tag: str, event: str, extra: Optional[Dict[str, Any]] = None):
    params = _client_change_params(tag)
    params.update(extra or {})
    await communicator.send_json_to({"type": "event", "event": event, "params": params, "ref": 1})
    return await communicator.receive_json_from(timeout=3)


def _sel(frame: Dict[str, Any]) -> str:
    m = re.search(r"sel=([^<]*)<", frame.get("html", ""))
    assert m, frame
    return m.group(1)


# ---------------------------------------------------------------------------
# #2779 reproducers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
class TestRowSelectionOverWebSocket:
    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    async def test_row_checkbox_toggles_that_row(self, framework):
        with _framework(framework):
            communicator, mount_frame = await _open(framework)
            try:
                assert _sel(mount_frame) == "-"
                tag = _row_checkbox(mount_frame["html"], "3")
                # Send exactly what the client builds from the rendered checkbox.
                resp = await _change(communicator, tag, _attrs(tag)["dj-change"])
                assert resp.get("type") != "error", (
                    f"#2779 [{framework}]: the row checkbox must be handled; got {resp!r}"
                )
                assert _sel(resp) == "3", resp
                # The re-render reflects the selection: that row is ``checked``.
                assert " checked" in _row_checkbox(resp["html"], "3")
                assert " checked" not in _row_checkbox(resp["html"], "2")
                # Toggling again clears it.
                tag2 = _row_checkbox(resp["html"], "3")
                resp2 = await _change(communicator, tag2, "toggle_row")
                assert _sel(resp2) == "-", resp2
                assert " checked" not in _row_checkbox(resp2["html"], "3")
            finally:
                await communicator.disconnect()

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    async def test_header_checkbox_selects_all_then_clears(self, framework):
        with _framework(framework):
            communicator, mount_frame = await _open(framework)
            try:
                head = _header_checkbox(mount_frame["html"])
                assert " checked" not in head
                resp = await _change(communicator, head, _attrs(head)["dj-change"])
                assert resp.get("type") != "error", (
                    f"#2779 [{framework}]: the header checkbox must be handled; got {resp!r}"
                )
                assert _sel(resp) == "2,3,1", resp
                for rid in ("1", "2", "3"):
                    assert " checked" in _row_checkbox(resp["html"], rid), rid
                assert " checked" in _header_checkbox(resp["html"])
                # Second click clears everything.
                resp2 = await _change(communicator, _header_checkbox(resp["html"]), "toggle_all")
                assert _sel(resp2) == "-", resp2
                assert not any(" checked" in t for t in _checkboxes(resp2["html"])), resp2
            finally:
                await communicator.disconnect()

    async def test_selection_survives_a_sort(self):
        """Selection is by identity, not position: sorting keeps it."""
        communicator, mount_frame = await _open("plain")
        try:
            tag = _row_checkbox(mount_frame["html"], "1")
            resp = await _change(communicator, tag, "toggle_row")
            assert _sel(resp) == "1"
            await communicator.send_json_to(
                {
                    "type": "event",
                    "event": "sort_by",
                    "params": {"component_id": "t1", "column": "id"},
                    "ref": 2,
                }
            )
            sorted_resp = await communicator.receive_json_from(timeout=3)
            assert _sel(sorted_resp) == "1", sorted_resp
            assert " checked" in _row_checkbox(sorted_resp["html"], "1")
        finally:
            await communicator.disconnect()

    async def test_unknown_event_still_errors(self):
        """Contrast (#1468): an event the table does NOT define is rejected."""
        communicator, _ = await _open("plain")
        try:
            await communicator.send_json_to(
                {"type": "event", "event": "not_a_handler", "params": {"component_id": "t1"}}
            )
            resp = await communicator.receive_json_from(timeout=3)
        finally:
            await communicator.disconnect()
        assert resp.get("type") == "error", resp


class TestSelectionState:
    """Handler-level semantics, including the row-identity choice."""

    def test_row_identity_is_the_row_key_value_as_a_string(self):
        t = _table(selected_rows=[2])  # int in, string stored — like DataTableMixin
        assert t.selected_rows == ["2"]
        t.toggle_row(row_id="3")
        assert t.selected_rows == ["2", "3"]
        t.toggle_row(row_id=2)  # a non-string id still matches
        assert t.selected_rows == ["3"]

    def test_row_key_is_configurable(self):
        t = _table(row_key="email")
        html = _render_under(t, "plain")
        assert 'data-row-id="z@x"' in html
        t.toggle_all()
        assert t.selected_rows == ["z@x", "a@x", "m@x"]

    def test_toggle_all_clears_only_when_every_row_is_selected(self):
        t = _table(selected_rows=["2"])
        t.toggle_all()  # partial selection → select all
        assert t.selected_rows == ["2", "3", "1"]
        t.toggle_all()  # full selection → clear
        assert t.selected_rows == []

    def test_toggle_ignores_the_client_value_and_trusts_server_state(self):
        t = _table()
        t.toggle_row(row_id="1", value=False)  # a stale client says "unchecked"
        assert t.selected_rows == ["1"]

    def test_row_id_is_escaped_in_the_attribute(self):
        t = _table(rows=[{"id": 'a"><b', "name": "x", "email": "y"}])
        html = _render_under(t, "plain")
        assert 'data-row-id="a&quot;&gt;&lt;b"' in html
        assert 'data-row-id="a">' not in html

    def test_selection_is_in_the_component_context(self):
        t = _table(selected_rows=["2"])
        ctx = t.get_context()
        assert ctx["selectable"] is True
        assert ctx["selected_rows"] == ["2"]

    def test_not_selectable_renders_no_checkbox(self):
        for fw in _FRAMEWORKS:
            assert _checkboxes(_render_under(_table(selectable=False), fw)) == []


class TestRenderedCheckboxesCarryTheirRouting:
    """Every branch (#1646): each checkbox is a ``dj-change`` routed to the
    table with the identity its handler takes."""

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_row_checkboxes(self, framework):
        html = _render_under(_table(selected_rows=["3"]), framework)
        rows = [t for t in _checkboxes(html) if "data-row-id" in t]
        assert [_attrs(t)["data-row-id"] for t in rows] == ["2", "3", "1"], html
        for t in rows:
            a = _attrs(t)
            assert a["dj-change"] == "toggle_row", t
            assert a["data-component-id"] == "t1", t
            assert a["aria-label"] == "Select row", t
            assert (" checked" in t) == (a["data-row-id"] == "3"), t

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_header_checkbox(self, framework):
        html = _render_under(_table(), framework)
        head = _header_checkbox(html)
        a = _attrs(head)
        assert a["dj-change"] == "toggle_all", head
        assert a["data-component-id"] == "t1", head
        assert a["aria-label"] == "Select all rows", head
        assert " checked" not in head
        all_html = _render_under(_table(selected_rows=["1", "2", "3"]), framework)
        assert " checked" in _header_checkbox(all_html)


# ---------------------------------------------------------------------------
# #2778 — the sortable affordance
# ---------------------------------------------------------------------------


def _th(html: str, key: str) -> str:
    for t in _TAG.findall(html):
        if t.startswith("<th") and _attrs(t).get("data-column") == key:
            return t
    raise AssertionError(f"no <th data-column={key!r}> in: {html}")


def _th_inner(html: str, key: str) -> str:
    """The header cell's content (label + icon markup)."""
    start = html.index(_th(html, key)) + len(_th(html, key))
    return html[start : html.index("</th>", start)]


# ``(state, sort kwargs)`` — the three states every sortable header can be in.
_STATES = [
    ("none", {}),
    ("ascending", {"sort_column": "id", "sort_direction": "asc"}),
    ("descending", {"sort_column": "id", "sort_direction": "desc"}),
]

# Per-branch, per-state icon markup — the branch's existing icon convention.
_ICON = {
    "bootstrap5": {
        "none": '<i class="bi bi-arrow-down-up dj-table-sort-icon" aria-hidden="true"></i>',
        "ascending": '<i class="bi bi-caret-up-fill dj-table-sort-icon" aria-hidden="true"></i>',
        "descending": '<i class="bi bi-caret-down-fill dj-table-sort-icon" aria-hidden="true"></i>',
    },
    "tailwind": {  # heroicons via djust.components.icons.render_icon
        "none": 'd="M3 7.5L7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5"',
        "ascending": 'd="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18"',
        "descending": 'd="M19.5 13.5L12 21m0 0l-7.5-7.5M12 21V3"',
    },
    "plain": {
        "none": '<span class="dj-table-sort-icon" aria-hidden="true">⇅</span>',
        "ascending": '<span class="dj-table-sort-icon" aria-hidden="true">▲</span>',
        "descending": '<span class="dj-table-sort-icon" aria-hidden="true">▼</span>',
    },
}


class TestSortableAffordance:
    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    @pytest.mark.parametrize("state,kw", _STATES, ids=[s for s, _ in _STATES])
    def test_sortable_header_carries_aria_sort_and_an_icon(self, framework, state, kw):
        html = _render_under(_table(selectable=False, **kw), framework)
        th = _th(html, "id")
        assert _attrs(th)["aria-sort"] == state, th
        inner = _th_inner(html, "id")
        assert _ICON[framework][state] in inner, (framework, state, inner)
        if framework == "tailwind":
            assert 'aria-hidden="true"' in inner and "<svg" in inner, inner

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_the_other_sortable_column_is_none_while_id_is_sorted(self, framework):
        html = _render_under(_table(selectable=False, sort_column="id"), framework)
        assert _attrs(_th(html, "name"))["aria-sort"] == "none"
        assert _ICON[framework]["none"] in _th_inner(html, "name")
        assert _ICON[framework]["ascending"] in _th_inner(html, "id")

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_non_sortable_header_has_no_affordance(self, framework):
        html = _render_under(_table(selectable=False), framework)
        email_th = [t for t in _TAG.findall(html) if t.startswith("<th") and "Email" in html]
        assert email_th
        # Exactly the two sortable columns carry aria-sort / an icon.
        assert html.count("aria-sort=") == 2, html
        assert html.count("dj-table-sort-icon") == 2, html

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_affordance_tracks_a_sort_round_trip(self, framework):
        t = _table(selectable=False)
        t.sort_by(column="name")
        html = _render_under(t, framework)
        assert _attrs(_th(html, "name"))["aria-sort"] == "ascending"
        t.sort_by(column="name")
        html = _render_under(t, framework)
        assert _attrs(_th(html, "name"))["aria-sort"] == "descending"
        assert _ICON[framework]["descending"] in _th_inner(html, "name")


class TestTriggerUpdateReachesTheParentRender:
    """The second half of #2779: routing alone left ``{{ table.render }}``
    STALE over WS — a ``LiveComponent`` is an opaque leaf to change detection
    (``deep_fingerprint`` compares it by ``id()``), and ``trigger_update`` looked
    for a ``_trigger_update`` hook no view defines. It now hands the attr name
    recorded at registration to the parent's ``set_changed_keys``."""

    def _host(self):
        from django.test import RequestFactory

        view = SelectableTableHostView()
        view.request = RequestFactory().get("/t/")
        view.mount(view.request)
        view.get_context_data()  # the registration walk (mixins/context.py)
        return view

    def test_registration_records_parent_and_attr_name(self):
        view = self._host()
        assert view.table._parent is view
        assert view.table._parent_attr == "table"
        assert view._components["t1"] is view.table

    def test_trigger_update_marks_the_attr_changed(self):
        view = self._host()
        assert not getattr(view, "_changed_keys", None)
        view.table.toggle_row(row_id="1")  # calls trigger_update()
        assert "table" in (view._changed_keys or set()), view._changed_keys

    def test_bare_base_class_trigger_update(self):
        """#1947 shape: the BARE ``LiveComponent`` path, no subclass override."""
        from djust.components.base import LiveComponent

        class _Host:
            def __init__(self):
                self.marked = []

            def set_changed_keys(self, keys=None):
                self.marked.append(keys)

        comp = LiveComponent(component_id="c")
        comp.trigger_update()  # no parent: a no-op, not an AttributeError
        host = _Host()
        comp._parent = host
        comp.trigger_update()
        assert host.marked == [None]  # unknown attr → zero-arg force render
        comp._parent_attr = "widget"
        comp.trigger_update()
        assert host.marked == [None, "widget"]

    def test_legacy_parent_hook_still_wins(self):
        from djust.components.base import LiveComponent

        calls = []

        class _Host:
            def _trigger_update(self):
                calls.append("hook")

            def set_changed_keys(self, keys=None):
                calls.append(("mark", keys))

        comp = LiveComponent(component_id="c")
        comp._parent = _Host()
        comp._parent_attr = "widget"
        comp.trigger_update()
        assert calls == ["hook"]


class TestSortIconStates:
    """The icon helper is a pure function of ``(state, framework)``."""

    def test_each_branch_has_three_distinct_marks(self):
        for fw in _FRAMEWORKS:
            marks = {_table(**kw)._sort_icon("id", fw) for _, kw in _STATES}  # pylint: disable=protected-access
            assert len(marks) == 3, (fw, marks)

    def test_arrows_up_down_is_in_the_vendored_heroicons(self):
        from djust.components.icons import get_icon_names, render_icon

        assert "arrows-up-down" in get_icon_names()
        assert "<svg" in render_icon("arrows-up-down")


__all__: Tuple[str, ...] = ("SelectableTableHostView",)
