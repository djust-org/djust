"""#4134 review: every component added for #4134 survives a REAL save of the view.

``Toasts`` first named its state field ``items``. State is a dict
(``TypedState``) whose fields are properties, so the field hid ``dict.items``
and the paths that save a view's state (``state.items()``) crashed on the page
GET of a view declaring it. ``LiveViewTestClient`` never saves state, so the
tests that drove the component through it passed. Those paths now read a state
through the unbound dict methods (``test_state_field_shadows_dict_method_4134.py``).

Here each component is declared on an explicit-policy view and taken through:

- a page GET that saves the view's state to the session, then a stateless
  HTTP fallback POST that restores it and sends the tag's event;
- the runtime's commit over a real WebSocket, then a reconnect that reads the
  saved state back.

"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, _automatic_state
from djust.components import descriptors as D
from djust.tests import _per_turn_harness as harness

MODULE = __name__


class _Page(LiveView):
    exposure_policy = "explicit"


class DropdownPage(_Page):
    template = (
        '<div dj-root>{% dropdown_menu label="M" open=w.is_open %}{% enddropdown_menu %}</div>'
    )
    w = D.Dropdown()


class SidebarPage(_Page):
    template = '<div dj-root>{% sidebar title="T" collapsed=w.collapsed %}{% endsidebar %}</div>'
    w = D.Sidebar()


class NotificationPage(_Page):
    template = "<div dj-root>{% notification_center is_open=w.is_open %}</div>"
    w = D.NotificationCenter()


class TreePage(_Page):
    template = "<div dj-root>{% tree_view nodes=nodes expanded=w.expanded %}</div>"
    w = D.TreeView()

    def get_context_data(self, **kwargs):
        return {"nodes": [{"id": "a", "label": "A", "children": [{"id": "b", "label": "B"}]}]}


class AlertPage(_Page):
    template = (
        "<div dj-root>{% if not w.dismissed %}{% alert dismissible=True %}x{% endalert %}"
        "{% endif %}</div>"
    )
    w = D.Alert()


class ToastsPage(_Page):
    template = "<div dj-root>{% toast_container w.toasts %}</div>"
    w = D.Toasts(toasts=[{"id": "t1", "type": "info", "message": "hello"}])


class DatePage(_Page):
    template = "<div dj-root>{% date_picker year=w.year month=w.month %}</div>"
    w = D.DatePicker(year=2026, month=3)


#: (view, event, params, attribute, expected after the event)
CASES = [
    (DropdownPage, "toggle_menu", {}, "is_open", True),
    (SidebarPage, "toggle_sidebar", {}, "collapsed", True),
    (NotificationPage, "toggle_notifications", {}, "is_open", True),
    (TreePage, "tree_expand", {"value": "a"}, "expanded", ["a"]),
    (AlertPage, "dismiss_alert", {}, "dismissed", True),
    (ToastsPage, "dismiss_toast", {"value": "t1"}, "toasts", []),
    (DatePage, "date_next_month", {}, "month", 4),
]
IDS = [c[0].__name__ for c in CASES]


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


def _request(method: str, key: Any, event: str = "", params: Any = None):
    import json

    factory = RequestFactory()
    if method == "GET":
        request = factory.get(harness.PATH)
    else:
        request = factory.post(
            harness.PATH,
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=harness.TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


def _saved(key: str) -> list:
    return [n for n in SessionStore(key).keys() if n.startswith("_djust_auto_")]


@pytest.mark.django_db
@pytest.mark.parametrize("view_class, event, params, attr, expected", CASES, ids=IDS)
def test_a_page_get_saves_and_a_fallback_post_restores_the_component(
    view_class, event, params, attr, expected
):
    with harness.ws_settings(MODULE):
        first = _request("GET", None)
        assert view_class().get(first).status_code == 200
        key = first.session.session_key
        assert _saved(key), "the GET saved nothing"
        view = view_class()
        response = view.post(_request("POST", key, event, params))
        assert response.status_code == 200, response.content
    value = getattr(view.w, attr)
    assert (list(value) if attr in ("expanded", "toasts") else value) == expected


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class, event, params, attr, expected", CASES, ids=IDS)
async def test_the_runtime_commit_saves_the_component_and_a_reconnect_mounts(
    view_class, event, params, attr, expected
):
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(view_class, key)
        try:
            frame = await harness.event(comm, event, params)
            assert frame["type"] != "error", frame
        finally:
            await comm.disconnect()
        again, mount = await harness.mount_view(view_class, key)
        await again.disconnect()
    assert mount["type"] != "error", mount
    saved = await sync_to_async(_saved)(key)
    assert saved, "the runtime commit saved nothing"


class TwoSidebarsPage(_Page):
    template = (
        "<div dj-root>"
        '{% sidebar title="L" collapsed=left.collapsed component_id="left" %}{% endsidebar %}'
        '{% sidebar title="R" collapsed=right.collapsed component_id="right" %}{% endsidebar %}'
        "</div>"
    )
    left = D.Sidebar()
    right = D.Sidebar()


class TwoSidebarsNoSavePage(TwoSidebarsPage):
    #: Nothing is saved between requests, so the POST mounts the view afresh.
    automatic_state = False


def _post_right(view_class):
    with harness.ws_settings(MODULE):
        first = _request("GET", None)
        assert view_class().get(first).status_code == 200
        key = first.session.session_key
        view = view_class()
        response = view.post(_request("POST", key, "toggle_sidebar", {"component_id": "right"}))
    return view, response


@pytest.mark.django_db
def test_the_http_fallback_routes_component_id_once_the_saved_state_is_restored():
    view, response = _post_right(TwoSidebarsPage)
    assert response.status_code == 200, response.content
    assert (view.left.collapsed, view.right.collapsed) == (False, True)


@pytest.mark.django_db
def test_the_http_fallback_routes_component_id_on_a_fresh_mount():
    """#4046: an ADR-031 descriptor nothing has materialized falls through to
    the view's own alias on the fallback too (one shared lookup), as the
    socket and SSE route it."""
    view, response = _post_right(TwoSidebarsNoSavePage)
    assert response.status_code == 200, response.content
    assert (view.left.collapsed, view.right.collapsed) == (False, True)


@pytest.mark.django_db
@pytest.mark.parametrize("bad", [["right"], {"a": 1}])
def test_the_http_fallback_refuses_an_unhashable_component_id(bad):
    """As the runtime does: refused before the lookup, which raised a TypeError."""
    import json

    with harness.ws_settings(MODULE):
        first = _request("GET", None)
        assert TwoSidebarsPage().get(first).status_code == 200
        key = first.session.session_key
        view = TwoSidebarsPage()
        response = view.post(_request("POST", key, "toggle_sidebar", {"component_id": bad}))
    assert response.status_code == 400
    assert json.loads(response.content)["error"] == "Event rejected"
    assert (view.left.collapsed, view.right.collapsed) == (False, False)
