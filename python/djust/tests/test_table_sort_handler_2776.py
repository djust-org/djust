"""Sorting a ``TableComponent`` must not raise ``No handler found for event:
sort_by`` (#2776).

Link N+2 of the #2748 → #2756 chain (#2142). PR #2764 pinned every
``LiveComponent`` under ``djust.components.ui``; ``TableComponent`` lives in
``djust.components.data``, outside that walk, so the pin could not see it.
Symptom-up trace of what the sortable header renders:

    <th dj-click="sort_by" data-column="name">

1. **No ``data-component-id``** on the control (nor any ancestor the component
   renders), so the client routes the click to the PARENT view — which has no
   ``sort_by`` → ``No handler found for event: sort_by``. That is the
   reporter's exact string.
2. ``sort_by`` was **undecorated**, so even a ``component_id``-routed event is
   rejected under the default ``event_security = "strict"``.
3. The control sends ``column`` (from ``data-column``) but the handler took
   ``column_key`` and no ``**kwargs``, so a routed, decorated call would still
   fail parameter validation.

The same three defects were live on ``PaginationComponent`` (``data``) and
``TabsComponent`` (``layout``), and the decoration/routing halves on
``ManyToManySelect`` (``forms``); every one is exercised here and covered by the
package-wide pin extended in ``test_ui_dismiss_handlers_2756.py``.

The reproducers build the event the way the client does from the RENDERED
control (``data-*`` → params, ``data-component-id`` → ``component_id``), so the
pre-fix request has no ``component_id`` and lands on the parent — the actual
production path, not a hand-written ``component_id`` the client never sent.
Harness lifted from ``test_ui_dismiss_handlers_2756.py``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust import config as config_module
from djust.components.data.pagination import PaginationComponent
from djust.components.data.table import TableComponent
from djust.components.layout.tabs import TabsComponent

_MODULE = "djust.tests.test_table_sort_handler_2776"
_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")

_COLUMNS = [
    {"key": "id", "label": "ID", "sortable": True},
    {"key": "name", "label": "Name", "sortable": True},
    {"key": "email", "label": "Email"},
]
# Neither column is pre-sorted, so sorting by ``id`` and by ``name`` each
# produce an order distinct from the mount order and from each other.
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


def _first_control(html: str, event: str) -> str:
    """The first rendered tag whose ``dj-click`` names ``event``."""
    for m in _TAG.finditer(html):
        attrs = dict(_ATTR.findall(m.group(0)))
        if attrs.get("dj-click") == event:
            return m.group(0)
    raise AssertionError(f"no dj-click={event!r} control in: {html}")


def _client_params(tag: str) -> Dict[str, Any]:
    """Build the event params the client would send for ``tag``
    (``08-event-parsing.js`` ``extractTypedParams`` + ``09-event-binding.js``
    ``addEventContext``): every ``data-*`` becomes a snake_case key, and
    ``data-component-id`` becomes ``component_id`` — ONLY if present."""
    params: Dict[str, Any] = {}
    attrs = dict(_ATTR.findall(tag))
    for name, value in attrs.items():
        if not name.startswith("data-"):
            continue
        if name == "data-component-id":
            params["component_id"] = value
            continue
        params[name[5:].replace("-", "_")] = value
    return params


def _render_under(component, framework: str) -> str:
    with patch.object(config_module.config, "get", lambda key, default=None: framework):
        return str(component.render())


# ---------------------------------------------------------------------------
# Host views
# ---------------------------------------------------------------------------


class TableHostView(LiveView):
    template = f'<div dj-root dj-view="{_MODULE}.TableHostView" dj-id="0"><p>order={{{{ order }}}}</p></div>'

    def mount(self, request, **kwargs):
        self.table = TableComponent(
            component_id="t1", columns=list(_COLUMNS), rows=[dict(r) for r in _ROWS]
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["order"] = ",".join(r["name"] for r in self.table.rows)
        return ctx


class PaginationHostView(LiveView):
    template = f'<div dj-root dj-view="{_MODULE}.PaginationHostView" dj-id="0"><p>page={{{{ p }}}}</p></div>'

    def mount(self, request, **kwargs):
        self.pager = PaginationComponent(component_id="p1", current_page=2, total_pages=5)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["p"] = self.pager.current_page
        return ctx


class TabsHostView(LiveView):
    template = (
        f'<div dj-root dj-view="{_MODULE}.TabsHostView" dj-id="0"><p>active={{{{ a }}}}</p></div>'
    )

    def mount(self, request, **kwargs):
        self.tabs = TabsComponent(
            component_id="tb1",
            tabs=[{"id": "one", "label": "One"}, {"id": "two", "label": "Two"}],
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["a"] = self.tabs.active
        return ctx


async def _click_over_ws(
    view_name: str, control_tag: str, event: str, extra: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Mount ``view_name``, send the event the client would build from
    ``control_tag``, return ``(mount_frame, response)``."""
    from django.test import override_settings

    params = _client_params(control_tag)
    params.update(extra or {})
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator, mount_frame = await _connect_and_mount(f"{_MODULE}.{view_name}")
        await communicator.send_json_to(
            {"type": "event", "event": event, "params": params, "ref": 1}
        )
        resp = await communicator.receive_json_from(timeout=3)
        await communicator.disconnect()
    return mount_frame, resp


# ---------------------------------------------------------------------------
# Reproducers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
class TestSortableHeaderOverWebSocket:
    """The reporter's case: click a sortable column header (#2776)."""

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    async def test_clicking_a_sortable_header_sorts(self, framework):
        comp = TableComponent(component_id="t1", columns=list(_COLUMNS), rows=list(_ROWS))
        tag = _first_control(_render_under(comp, framework), "sort_by")
        assert 'data-column="id"' in tag, tag
        # Send exactly what the client builds from the rendered <th>.
        mount_frame, resp = await _click_over_ws("TableHostView", tag, "sort_by")
        assert "order=Zed,Amy,Mia" in mount_frame.get("html", ""), mount_frame
        assert resp.get("type") != "error", (
            f"#2776 [{framework}]: sort_by must be handled by the table; got {resp!r}"
        )
        assert "order=Mia,Zed,Amy" in resp.get("html", ""), resp

    async def test_sorting_by_name_then_toggles_direction(self):
        comp = TableComponent(component_id="t1", columns=list(_COLUMNS), rows=list(_ROWS))
        html = _render_under(comp, "plain")
        tag = [t for t in _TAG.findall(html) if 'data-column="name"' in t][0]
        _, resp = await _click_over_ws("TableHostView", tag, "sort_by")
        assert "order=Amy,Mia,Zed" in resp.get("html", ""), resp

    async def test_unknown_event_still_errors(self):
        """Contrast (#1468): an event the table does NOT define is rejected."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
            communicator, _ = await _connect_and_mount(f"{_MODULE}.TableHostView")
            await communicator.send_json_to(
                {"type": "event", "event": "not_a_handler", "params": {"component_id": "t1"}}
            )
            resp = await communicator.receive_json_from(timeout=3)
            await communicator.disconnect()
        assert resp.get("type") == "error", resp


@pytest.mark.django_db
@pytest.mark.asyncio
class TestSiblingsFoundByTheExtendedPin:
    """PaginationComponent and TabsComponent had the identical shape (#1646)."""

    @pytest.mark.parametrize("event,expect", [("next_page", "page=3"), ("go_to_page", "page=4")])
    async def test_pagination_control_routes_to_the_pager(self, event, expect):
        comp = PaginationComponent(component_id="p1", current_page=2, total_pages=5)
        html = _render_under(comp, "bootstrap5")
        tags = [t for t in _TAG.findall(html) if f'dj-click="{event}"' in t]
        tag = [t for t in tags if 'data-page="4"' in t][0] if event == "go_to_page" else tags[0]
        mount_frame, resp = await _click_over_ws("PaginationHostView", tag, event)
        assert "page=2" in mount_frame.get("html", "")
        assert resp.get("type") != "error", f"#2776 sibling: {event} rejected: {resp!r}"
        assert expect in resp.get("html", ""), resp

    async def test_tab_control_routes_to_the_tabs(self):
        comp = TabsComponent(
            component_id="tb1",
            tabs=[{"id": "one", "label": "One"}, {"id": "two", "label": "Two"}],
        )
        html = _render_under(comp, "bootstrap5")
        tag = [t for t in _TAG.findall(html) if 'data-tab="two"' in t][0]
        mount_frame, resp = await _click_over_ws("TabsHostView", tag, "activate_tab")
        assert "active=one" in mount_frame.get("html", "")
        assert resp.get("type") != "error", f"#2776 sibling: activate_tab rejected: {resp!r}"
        assert "active=two" in resp.get("html", ""), resp


class TestRenderedControlsCarryTheirRouting:
    """Each fixed component's controls are routed to itself on every branch."""

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_table_headers(self, framework):
        comp = TableComponent(component_id="t1", columns=list(_COLUMNS), rows=list(_ROWS))
        html = _render_under(comp, framework)
        heads = [t for t in _TAG.findall(html) if 'dj-click="sort_by"' in t]
        assert len(heads) == 2, html
        for t in heads:
            assert 'data-component-id="t1"' in t, t
        # Non-sortable columns render no control at all.
        assert html.count("dj-click=") == 2, html

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_pagination_controls(self, framework):
        comp = PaginationComponent(component_id="p1", current_page=2, total_pages=5)
        html = _render_under(comp, framework)
        ctrls = [t for t in _TAG.findall(html) if "dj-click=" in t]
        assert {re.search(r'dj-click="([^"]+)"', t).group(1) for t in ctrls} == {
            "first_page",
            "previous_page",
            "go_to_page",
            "next_page",
            "last_page",
        }, html
        for t in ctrls:
            assert 'data-component-id="p1"' in t, t

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_tabs_controls(self, framework):
        comp = TabsComponent(
            component_id="tb1",
            tabs=[{"id": "one", "label": "One"}, {"id": "two", "label": "Two"}],
        )
        html = _render_under(comp, framework)
        ctrls = [t for t in _TAG.findall(html) if 'dj-click="activate_tab"' in t]
        assert len(ctrls) == 2, html
        for t in ctrls:
            assert 'data-component-id="tb1"' in t, t

    def test_tabs_custom_action_routes_to_the_parent(self):
        """A parent-supplied ``action`` is the parent's handler: no component routing."""
        comp = TabsComponent(
            component_id="tb1", action="switch_section", tabs=[{"id": "one", "label": "One"}]
        )
        html = _render_under(comp, "plain")
        tag = _first_control(html, "switch_section")
        assert "data-component-id" not in tag, tag
