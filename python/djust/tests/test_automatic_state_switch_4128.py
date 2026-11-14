"""The switch that turns saved view state off, and subclassed session stores (#4128).

``DJUST_AUTOMATIC_STATE = False`` (the project) and ``automatic_state = False``
(a view, which may also say ``True`` over the project's ``False``) keep explicit
exposure as it is and stop SAVING between requests: nothing of the view's
undeclared state, its ``_private`` state, its components' state or the
changed/deleted record is written to the session, on any transport.

The sweep below drives a page GET, events over a socket, over SSE and over the
page-POST fallback against a view full of marker values, then reads every row
of the session table: no ``_djust_auto_*`` key and none of the markers.
A field declared ``persist="server"`` is still kept: the developer asked for
exactly that field.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView, _automatic_state, event_handler
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.tests import _per_turn_harness as harness

MODULE = __name__
MARKERS = ("SWEEP_PUBLIC", "SWEEP_PRIVATE", "SWEEP_STATE", "SWEEP_HANDLER", "SWEEP_COMPONENT")
MOUNTS: List[str] = []


class Tag(LiveComponent):
    template = "<span>{{ text }}</span>"

    class State(TypedState):
        text: str = ""


def _body(view: Any) -> Dict[str, Any]:
    return {
        "count": view.count,
        "note": view.note,
        "kept": view.kept,
        "private": view._private_note,
        "tag": view.tag.state.text,
    }


class Marked(LiveView):
    """Every kind of state the automatic save covers, each holding a marker,
    beside one declared ``persist="server"`` field. Takes the project's switch."""

    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ probe }}</pre></div>"
    kept = state(0, persist="server")
    label = state("SWEEP_STATE")
    tag = Tag()

    def mount(self, request, **kwargs):
        MOUNTS.append(type(self).__name__)
        self.count = 0
        self.note = "SWEEP_PUBLIC"
        self._private_note = "SWEEP_PRIVATE"

    def get_context_data(self, **kwargs):
        return {"probe": json.dumps(_body(self), sort_keys=True)}

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self.kept += 1
        self.note = "SWEEP_HANDLER"
        self.tag.state.text = "SWEEP_COMPONENT"
        self.scratch = "SWEEP_PUBLIC"  # an attribute the handler adds
        del self.scratch  # ... and one it removes (the changed/deleted record)


def _variant(name: str, value: Any) -> type:
    """The same view with ``automatic_state`` set. (Declared fields are stated
    again: exposure grants are not inherited.)"""
    body = {
        "__module__": MODULE,
        "automatic_state": value,
        "kept": state(0, persist="server"),
        "label": state("SWEEP_STATE"),
        "tag": Tag(),
    }
    return type(name, (Marked,), body)


ViewOff = _variant("ViewOff", False)
ViewOn = _variant("ViewOn", True)
ViewNotABool = _variant("ViewNotABool", "off")
for _cls in (ViewOff, ViewOn, ViewNotABool):
    setattr(sys.modules[MODULE], _cls.__name__, _cls)

VIEWS = (Marked, ViewOff, ViewOn, ViewNotABool)
urlpatterns = [path(f"sw-{cls.__name__}/", cls.as_view()) for cls in VIEWS]


@pytest.fixture(autouse=True)
def _each(monkeypatch):
    MOUNTS.clear()
    _automatic_state._NOTICED.clear()
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


# ---------------------------------------------------------------------------
# Drivers: one flow per transport, each from a page GET
# ---------------------------------------------------------------------------


def _url(view_class: type) -> str:
    return f"/sw-{view_class.__name__}/"


def _request(method: str, key: str | None, url: str, event: str = "", store: type = SessionStore):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url)
    else:
        request = factory.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=harness.TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = store(key)
    if key is None:
        request.session.create()
    return request


def _page(view_class: type, store: type = SessionStore) -> str:
    first = _request("GET", None, _url(view_class), store=store)
    response = view_class().get(first)
    assert response.status_code == 200
    assert response.content.count(b'<meta name="djust-tab"') == 1  # the id is still handed out
    return str(first.session.session_key)


def _fallback(view_class: type, events: int = 2, store: type = SessionStore):
    key = _page(view_class, store)
    view = None
    for _ in range(events):
        view = view_class()
        response = view.post(_request("POST", key, _url(view_class), "bump", store=store))
        assert response.status_code == 200, response.content
    return key, view


async def _socket(view_class: type, events: int = 2):
    """The page GET, a socket with two events, then a reconnect on a new consumer."""
    key = await sync_to_async(_page)(view_class)
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        comm, _mount = await harness.mount_view(view_class, key)
        for _ in range(events):
            assert (await harness.event(comm, "bump"))["type"] != "error"
        await comm.disconnect()
        again, mount = await harness.mount_view(view_class, key)
        await again.disconnect()
    return key, _probe_of(mount["html"])


async def _sse(view_class: type, events: int = 2):
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    def request_for(method: str, target: str, body: Any, key: str) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        made.user = AnonymousUser()
        made.tenant = None
        return made

    key = await sync_to_async(_page)(view_class)
    sid = str(uuid.uuid4())
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        first = request_for(
            "GET",
            f"/djust/sse/{sid}/",
            {
                "view": f"{MODULE}.{view_class.__name__}",
                "_djust_url": _url(view_class),
                "_djust_tab": harness.TAB,
            },
            key,
        )
        response = await DjustSSEStreamView().get(first, session_id=sid)
        assert response.status_code == 200
        try:
            for _ in range(events):
                posted = request_for(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {"type": "event", "event": "bump", "params": {}},
                    key,
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
        finally:
            _sse_sessions.pop(sid, None)
    return key


def _probe_of(markup: str) -> Dict[str, Any]:
    import html
    import re

    found = re.search(r"<pre[^>]*>(.*?)</pre>", markup, re.S)
    assert found, markup[:400]
    return json.loads(html.unescape(found.group(1)))


def sweep(store: type = SessionStore) -> List[str]:
    """Everything wrong with the session table for a view whose saving is off.

    Each row is read through the store class that wrote it: Django signs a
    session with the store's class name, so another class reads it as empty.
    """
    wrong = []
    rows = [dict(store(row.session_key).items()) for row in Session.objects.all()]
    assert rows, "the flow stored no session at all: the sweep would prove nothing"
    for row in rows:
        wrong += ["key %s" % key for key in row if str(key).startswith("_djust_auto_")]
        text = json.dumps(row, default=str)
        wrong += ["value %s" % marker for marker in MARKERS if marker in text]
    return wrong


def _declared(key: str, store: type = SessionStore) -> List[int]:
    """The declared field's saved values (its own envelope), for one session."""
    found = []
    for name, value in store(key).items():
        if str(name).startswith("_djust_explicit_"):
            found.append(value["state"]["values"]["kept"])
    return found


# ---------------------------------------------------------------------------
# The sweep: nothing of the view rests in the session
# ---------------------------------------------------------------------------

OFF = [
    pytest.param(Marked, False, id="project-off"),
    pytest.param(ViewOff, True, id="view-off"),
    pytest.param(ViewOff, False, id="both-off"),
    # Not a bool is read as off, never as on.
    pytest.param(ViewNotABool, True, id="view-not-a-bool"),
    pytest.param(Marked, "yes", id="project-not-a-bool"),
    pytest.param(Marked, None, id="project-none"),
    pytest.param(Marked, 1, id="project-one"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("view_class,project", OFF)
def test_sweep_the_page_get_and_the_fallback_leave_nothing(view_class, project, settings):
    settings.DJUST_AUTOMATIC_STATE = project
    key, view = _fallback(view_class)
    assert sweep() == []
    # Every request ran mount() again (the GET and both POSTs), then the handler.
    assert MOUNTS == [view_class.__name__] * 3
    # The declared field is still kept, and only it: 0 at the GET, then 1, 2.
    assert _declared(key) == [2]
    assert _body(view) == {
        "count": 1,
        "note": "SWEEP_HANDLER",
        "kept": 2,
        "private": "SWEEP_PRIVATE",
        "tag": "SWEEP_COMPONENT",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class,project", OFF[:3])
async def test_sweep_a_socket_and_its_reconnect_leave_nothing(view_class, project, settings):
    settings.DJUST_AUTOMATIC_STATE = project
    key, reconnected = await _socket(view_class)
    assert await sync_to_async(sweep)() == []
    # The reconnect starts from mount(); the declared field is restored.
    assert reconnected == {
        "count": 0,
        "note": "SWEEP_PUBLIC",
        "kept": 2,
        "private": "SWEEP_PRIVATE",
        "tag": "",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class,project", OFF[:3])
async def test_sweep_sse_leaves_nothing(view_class, project, settings):
    settings.DJUST_AUTOMATIC_STATE = project
    key = await _sse(view_class)
    assert await sync_to_async(sweep)() == []
    assert await sync_to_async(_declared)(key) == [2]


ON = [
    pytest.param(Marked, True, id="project-on"),
    pytest.param(ViewOn, False, id="view-on-over-project-off"),
    pytest.param(ViewOn, True, id="both-on"),
]


@pytest.mark.django_db
@pytest.mark.parametrize("view_class,project", ON)
def test_control_with_saving_on_the_same_flow_fills_the_session(view_class, project, settings):
    """The sweep is not vacuous: the same flow with saving on trips every part of it."""
    settings.DJUST_AUTOMATIC_STATE = project
    key, view = _fallback(view_class)
    wrong = sweep()
    assert any(item.startswith("key _djust_auto_") for item in wrong)
    assert {item for item in wrong if item.startswith("value ")} >= {
        "value SWEEP_PRIVATE",
        "value SWEEP_STATE",
        "value SWEEP_HANDLER",
        "value SWEEP_COMPONENT",
    }
    assert MOUNTS == [view_class.__name__]  # the page GET only
    assert _body(view)["count"] == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_control_with_saving_on_a_socket_and_sse_fill_the_session(settings):
    settings.DJUST_AUTOMATIC_STATE = False
    _key, reconnected = await _socket(ViewOn)
    assert reconnected["count"] == 2 and reconnected["tag"] == "SWEEP_COMPONENT"
    assert await sync_to_async(sweep)()
    await sync_to_async(Session.objects.all().delete)()
    await _sse(ViewOn)
    assert await sync_to_async(sweep)()


def test_the_default_is_on_and_only_the_class_decides():
    with override_settings():
        from django.conf import settings

        if hasattr(settings, "DJUST_AUTOMATIC_STATE"):
            del settings.DJUST_AUTOMATIC_STATE
        assert _automatic_state.project_saving_enabled() is True
        assert _automatic_state.saving_enabled(Marked()) is True
    # An instance attribute (a handler, a restored value) cannot switch it.
    view = ViewOff()
    view.__dict__["automatic_state"] = True
    assert _automatic_state.saving_enabled(view) is False
    view = Marked()
    view.__dict__["automatic_state"] = False
    assert _automatic_state.saving_enabled(view) is True
    # A subclass takes its base's switch unless it states its own.
    assert (
        _automatic_state.saving_enabled(type("Sub", (ViewOff,), {"__module__": MODULE})()) is False
    )
    again = type("SubOn", (ViewOff,), {"__module__": MODULE, "automatic_state": None})
    assert _automatic_state.saving_enabled(again()) is True  # None: the project's value


@pytest.mark.django_db
def test_switched_off_no_size_limit_is_applied_and_nothing_is_reported(settings, caplog):
    """Nothing is saved, so nothing is too large to save and nothing is unsaveable."""
    import logging

    settings.DJUST_AUTOMATIC_STATE = False
    view = Marked()
    request = _request("GET", None, _url(Marked))
    view.mount(request)
    view._djust_tab = harness.TAB
    view.blob = "x" * (2 * 1024 * 1024)
    view.service = object()
    with caplog.at_level(logging.WARNING, logger="djust"):
        assert _automatic_state.stage(view, request) is None
        _automatic_state.settle(view)
        assert _automatic_state.saved(view, request) is None
        assert _automatic_state.load(view, request) is None
    assert view.__dict__.get("_djust_auto_basis") is None  # no copy held in memory either
    assert not [r for r in caplog.records if "not saved between requests" in r.getMessage()]
    assert not [key for key in request.session.keys() if key.startswith("_djust_auto_")]


@pytest.mark.django_db
def test_switched_off_an_envelope_saved_earlier_is_not_read(settings):
    """Turning the switch off stops reading too: state saved before is not put back."""
    key, _view = _fallback(Marked)
    assert sweep()
    settings.DJUST_AUTOMATIC_STATE = False
    MOUNTS.clear()
    view = Marked()
    assert view.post(_request("POST", key, _url(Marked), "bump")).status_code == 200
    assert MOUNTS == ["Marked"] and view.count == 1  # mount(), not the saved count of 2


# ---------------------------------------------------------------------------
# A project's own session store, subclassing Django's
# ---------------------------------------------------------------------------


class OwnStore(SessionStore):
    """What a project writes: Django's db store with something of its own."""

    def get_session_cookie_age(self):  # another expiry
        return 1800


def _engine(monkeypatch, name: str, store: type) -> str:
    module = types.ModuleType(name)
    module.SessionStore = store
    monkeypatch.setitem(sys.modules, name, module)
    return name


@pytest.mark.django_db
def test_a_subclassed_store_saves_when_on(settings):
    settings.DJUST_AUTOMATIC_STATE = True
    key, view = _fallback(Marked, store=OwnStore)
    assert MOUNTS == ["Marked"] and view.count == 2  # rebuilt from saved state
    assert any(item.startswith("key _djust_auto_") for item in sweep(OwnStore))
    assert _declared(key, OwnStore) == [2]  # and the declared field is no longer refused


@pytest.mark.django_db
@pytest.mark.parametrize("view_class,project", OFF[:2])
def test_a_subclassed_store_saves_nothing_when_off(view_class, project, settings):
    settings.DJUST_AUTOMATIC_STATE = project
    key, _view = _fallback(view_class, store=OwnStore)
    assert sweep(OwnStore) == []
    assert _declared(key, OwnStore) == [2]


# ---------------------------------------------------------------------------
# System checks
# ---------------------------------------------------------------------------

COOKIES = "django.contrib.sessions.backends.signed_cookies"


def _check(name: str, **settings: Any) -> list:
    from djust.checks import configuration

    errors: list = []
    with override_settings(**settings):
        getattr(configuration, name)(errors)
    return errors


def _own(errors: list) -> list:
    """The findings that name this module's views (the process holds other
    projects' test views too)."""
    return [e for e in errors if MODULE in e.msg or "DJUST_AUTOMATIC_STATE" in e.msg]


def test_c034_names_the_engine_and_both_ways_out():
    (found,) = _check(
        "_check_automatic_state_session_engine", SESSION_ENGINE=COOKIES, DJUST_AUTOMATIC_STATE=True
    )
    assert found.id == "djust.C034" and COOKIES in found.msg
    assert "DJUST_AUTOMATIC_STATE = False" in found.fix_hint
    assert "automatic_state = False" in found.fix_hint


def test_c034_is_silent_for_a_supported_engine_a_subclass_and_a_project_that_saves_nothing(
    monkeypatch,
):
    check = "_check_automatic_state_session_engine"
    assert _check(check, SESSION_ENGINE="django.contrib.sessions.backends.db") == []
    own = _engine(monkeypatch, "project_sessions_own_4128", OwnStore)
    assert _check(check, SESSION_ENGINE=own, DJUST_AUTOMATIC_STATE=True) == []
    # Off for the project: only a view that says True is still to be saved.
    (found,) = _check(check, SESSION_ENGINE=COOKIES, DJUST_AUTOMATIC_STATE=False)
    assert f"{MODULE}.ViewOn" in found.msg and f"{MODULE}.Marked" not in found.msg


def test_c034_reports_an_engine_built_on_no_server_side_store(monkeypatch):
    from django.contrib.sessions.backends.base import SessionBase

    class Elsewhere(SessionBase):
        pass

    engine = _engine(monkeypatch, "project_sessions_elsewhere_4128", Elsewhere)
    (found,) = _check("_check_automatic_state_session_engine", SESSION_ENGINE=engine)
    assert engine in found.msg


def test_c035_reports_a_switch_that_is_not_a_bool():
    (found,) = _check("_check_automatic_state_switch", DJUST_AUTOMATIC_STATE="False")
    assert found.id == "djust.C035"
    assert "DJUST_AUTOMATIC_STATE is 'False'" in found.msg
    assert f"{MODULE}.ViewNotABool" in found.msg and f"{MODULE}.ViewOff" not in found.msg
    (found,) = _check("_check_automatic_state_switch", DJUST_AUTOMATIC_STATE=False)
    assert "DJUST_AUTOMATIC_STATE is" not in found.msg  # only the view is wrong now


def test_the_removed_1x_key_names_its_replacement():
    from django.core.exceptions import ImproperlyConfigured

    from djust._removed_settings import reject_removed_settings

    with override_settings(LIVEVIEW_CONFIG={"view_state_in_session": False}):
        with pytest.raises(ImproperlyConfigured) as raised:
            reject_removed_settings()
    said = str(raised.value)
    assert "DJUST_AUTOMATIC_STATE = False" in said and "automatic_state = False" in said
