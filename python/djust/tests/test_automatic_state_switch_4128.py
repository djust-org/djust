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
MARKERS = (
    "SWEEP_PUBLIC",
    "SWEEP_PRIVATE",
    "SWEEP_STATE",
    "SWEEP_HANDLER",
    "SWEEP_COMPONENT",
    "SWEEP_LAZY",
    "SWEEP_CHILD",
    "SWEEP_FORM",
)
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
        # The WHOLE session: every key is one this list accounts for, and no
        # marker value is anywhere in it.
        wrong += ["key %s" % key for key in row if not str(key).startswith(ALLOWED_KEYS)]
        text = json.dumps(row, default=str)
        wrong += ["value %s" % marker for marker in MARKERS if marker in text]
    return wrong


#: Every session key a flow with saving off may leave. Anything else is a
#: finding: view content that reached the session by another road.
ALLOWED_KEYS = (
    "_djust_explicit_",  # fields declared persist="server" (asked for by name), and their index
    "_djust_lazy_binding",  # a random token that binds lazy children to this session
    "_auth_user_",  # Django's login
)


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


# ---------------------------------------------------------------------------
# Review of the switch: the other roads into the session
# ---------------------------------------------------------------------------

LAZY = '<div dj-view="' + MODULE + '.LazyChild" dj-lazy></div>'


class LazyChild(LiveView):
    """A legacy lazy child: over the fallback its state was written to the
    session by its own code path (``_lazy_containers.save_http_lazy``)."""

    exposure_policy = "legacy"
    template = '<div><span class="n">{{ count }}</span><i>{{ note }}</i></div>'

    def mount(self, request, **kwargs):
        self.count = 0
        self.note = "SWEEP_LAZY"
        self._hidden = "SWEEP_LAZY"

    @event_handler
    def inc(self, **kwargs):
        self.count += 1


class LazyPage(LiveView):
    # Only a legacy page registers lazy children (``register_lazy_containers``).
    exposure_policy = "legacy"
    template = "<div dj-root><section>" + LAZY + "</section></div>"


class LazyPageOff(LiveView):
    exposure_policy = "legacy"
    automatic_state = False
    template = "<div dj-root><section>" + LAZY + "</section></div>"


class LazyChildOff(LazyChild):
    automatic_state = False


class LazyPageChildOff(LiveView):
    exposure_policy = "legacy"
    template = (
        '<div dj-root><section><div dj-view="' + MODULE + '.LazyChildOff" dj-lazy></div>'
        "</section></div>"
    )


urlpatterns += [
    path("lazy-on/", LazyPage.as_view()),
    path("lazy-page-off/", LazyPageOff.as_view()),
    path("lazy-child-off/", LazyPageChildOff.as_view()),
]


def _lazy_flow(url: str) -> List[int]:
    """A page GET, then three events for the lazy child over the fallback: the
    counts each answer showed."""
    import re

    from django.conf import settings
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    page = client.get(url).content.decode()
    (view_id,) = re.findall(r'data-djust-lazy-id="([^"]+)"', page)
    counts = []
    for event in ("djust_lazy_mount", "inc", "inc"):
        response = client.post(
            url,
            json.dumps({"view_id": view_id}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value,
        )
        assert response.status_code == 200, response.content
        found = re.search(r'class="n"[^>]*>(\d+)</span>', json.loads(response.content)["html"])
        counts.append(int(found.group(1)))
    return counts


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url,project",
    [
        pytest.param("/lazy-on/", False, id="project-off"),
        pytest.param("/lazy-page-off/", True, id="page-off"),
        pytest.param("/lazy-child-off/", True, id="child-off"),
    ],
)
def test_sweep_a_lazy_legacy_child_leaves_nothing(url, project, settings):
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_AUTOMATIC_STATE = project
    assert _lazy_flow(url) == [0, 1, 1]  # each request starts from mount()
    assert sweep() == []


@pytest.mark.django_db
def test_control_a_lazy_legacy_child_is_saved_with_saving_on(settings):
    settings.ROOT_URLCONF = MODULE
    settings.DJUST_AUTOMATIC_STATE = True
    assert _lazy_flow("/lazy-on/") == [0, 1, 2]
    wrong = sweep()
    assert "value SWEEP_LAZY" in wrong and any(item.startswith("key liveview_") for item in wrong)


@pytest.mark.django_db
def test_a_lazy_childs_state_saved_earlier_is_removed_on_first_contact(settings):
    settings.ROOT_URLCONF = MODULE
    assert _lazy_flow("/lazy-on/") == [0, 1, 2]
    assert sweep()
    settings.DJUST_AUTOMATIC_STATE = False
    # The same browser would be needed to reach the same entry: do it in place.
    from djust._lazy_containers import LazyContainer, mount_http_lazy

    (row,) = Session.objects.all()
    session = SessionStore(row.session_key)
    (state_key,) = [key for key in session.keys() if key.startswith("liveview_")]
    parent = LazyPage()
    parent._http_lazy_containers = {"lazy_x": LazyContainer(LazyChild, state_key, "")}
    request = _request("GET", row.session_key, "/lazy-on/")
    child = mount_http_lazy(parent, request, "lazy_x")
    assert child.count == 0 and state_key not in request.session


# --- an embedded child ----------------------------------------------------------


def _family(name: str, child_switch: Any, parent_switch: Any):
    """A page with a sticky child. Both hold undeclared state; the child also
    declares one ``persist="server"`` field."""

    def child_mount(self, request, **kwargs):
        self.note = "SWEEP_CHILD"
        self._hidden = "SWEEP_CHILD"

    def poke(self, **kwargs):
        self.note = "SWEEP_CHILD"
        self.kept += 1

    child_body = {
        "__module__": MODULE,
        "exposure_policy": "explicit",
        "sticky": True,
        "sticky_id": "menu",
        "template": "<div>Child={{ kept }}</div>",
        "kept": state(0, persist="server"),
        "mount": child_mount,
        "get_context_data": lambda self, **kwargs: {"kept": self.kept},
        "poke": event_handler()(poke),
    }
    if child_switch is not None:
        child_body["automatic_state"] = child_switch
    child = type(name + "Child", (LiveView,), child_body)
    setattr(sys.modules[MODULE], child.__name__, child)

    def parent_mount(self, request, **kwargs):
        self.title = "SWEEP_PUBLIC"

    def retitle(self, **kwargs):
        self.title = "SWEEP_HANDLER"

    body = {
        "__module__": MODULE,
        "exposure_policy": "explicit",
        "template": "<div dj-root><h1>{{ title }}</h1>{% load live_tags %}{% live_render "
        f'"{MODULE}.{child.__name__}" sticky=True %}}</div>',
        "mount": parent_mount,
        "get_context_data": lambda self, **kwargs: {"title": self.title},
        "retitle": event_handler()(retitle),
    }
    if parent_switch is not None:
        body["automatic_state"] = parent_switch
    parent = type(name + "Page", (LiveView,), body)
    setattr(sys.modules[MODULE], parent.__name__, parent)
    return parent


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "child,parent,page_saved",
    [
        (None, None, True),
        (False, None, True),  # the child's switch changes nothing: see below
        (True, False, False),  # a child cannot switch saving on under a page that is off
        (None, False, False),
    ],
)
async def test_an_embedded_childs_own_state_is_never_saved_and_the_page_governs(
    child, parent, page_saved, settings
):
    """Only a PAGE's state is saved automatically. An embedded child keeps its
    declared ``persist="server"`` fields and nothing else, whatever its own
    ``automatic_state`` says; the page's switch decides the page's state."""
    settings.DJUST_AUTOMATIC_STATE = True
    page = _family("Fam%s%s" % (child, parent), child, parent)
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(page, key)
        assert (await harness.event(comm, "retitle"))["type"] != "error"
        assert (await harness.event(comm, "poke", {"view_id": "menu"}))["type"] != "error"
        await comm.disconnect()

    def read() -> Dict[str, Any]:
        return dict(SessionStore(key).items())

    session = await sync_to_async(read)()
    text = json.dumps(session, default=str)
    assert "SWEEP_CHILD" not in text  # never, in any combination
    assert any(name.startswith("_djust_explicit_") for name in session)  # its declared field
    assert ("SWEEP_HANDLER" in text) is page_saved
    assert any(name.startswith("_djust_auto_") for name in session) is page_saved


# --- form validation errors -----------------------------------------------------


@pytest.mark.django_db
def test_form_input_and_validation_errors_are_not_saved_with_saving_off(settings):
    from django import forms

    from djust.forms import FormMixin

    class Signup(forms.Form):
        name = forms.CharField(max_length=5)

    class SignupView(FormMixin, LiveView):
        exposure_policy = "explicit"
        automatic_state = False
        form_class = Signup
        template = "<div dj-root>{{ form_data.name }}|{{ field_errors.name.0 }}</div>"

    setattr(sys.modules[MODULE], "SignupView", SignupView)
    first = _request("GET", None, "/signup/")
    assert SignupView().get(first).status_code == 200
    key = str(first.session.session_key)
    view = SignupView()
    request = RequestFactory().post(
        "/signup/",
        data=json.dumps({"field": "name", "value": "SWEEP_FORM-too-long"}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="validate_field",
        HTTP_X_DJUST_TAB=harness.TAB,
    )
    request.user, request.tenant, request.session = AnonymousUser(), None, SessionStore(key)
    response = view.post(request)
    assert response.status_code == 200, response.content
    # The answer carries the input and its error; the session carries neither.
    assert view.form_data["name"] == "SWEEP_FORM-too-long" and view.field_errors["name"]
    assert "SWEEP_FORM" in response.content.decode()
    assert "SWEEP_FORM" not in json.dumps(dict(SessionStore(key).items()), default=str)
    assert not [name for name in SessionStore(key).keys() if not name.startswith(ALLOWED_KEYS)]


# --- Q1: an instance attribute does not switch it, and says so --------------------


@pytest.mark.django_db
def test_an_instance_attribute_is_not_the_switch_and_a_warning_says_so(settings, caplog):
    import logging

    settings.DJUST_AUTOMATIC_STATE = True

    class SetsItInMount(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>{{ n }}</div>"

        def mount(self, request, **kwargs):
            self.automatic_state = False
            self.n = "SWEEP_PUBLIC"

        def get_context_data(self, **kwargs):
            return {"n": self.n}

    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        for _ in range(2):
            request = _request("GET", None, "/x/")
            assert SetsItInMount().get(request).status_code == 200
    said = [r.getMessage() for r in caplog.records if "on the instance" in r.getMessage()]
    assert len(said) == 1 and "SetsItInMount" in said[0] and "class attribute" in said[0]
    assert "SWEEP_PUBLIC" not in said[0]
    # Still saved: the instance value is not read (and is not itself state).
    (envelope,) = [v for k, v in request.session.items() if k.startswith("_djust_auto_")]
    assert envelope["values"] == {"n": "SWEEP_PUBLIC"}


# --- Q2: state saved before the switch went off does not stay at rest -------------


@pytest.mark.django_db
def test_switching_off_removes_this_tabs_saved_state_on_first_contact(settings):
    key, _view = _fallback(Marked)
    assert [k for k in SessionStore(key).keys() if k.startswith("_djust_auto_")]
    settings.DJUST_AUTOMATIC_STATE = False
    view = Marked()
    assert view.post(_request("POST", key, _url(Marked), "bump")).status_code == 200
    # Read back from the store, not from the request's own copy.
    assert not [k for k in SessionStore(key).keys() if k.startswith("_djust_auto_")]
    assert sweep() == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_switching_off_removes_it_on_a_socket_mount_too(settings):
    settings.DJUST_AUTOMATIC_STATE = True
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        comm, _mount = await harness.mount_view(Marked, key)
        await harness.event(comm, "bump")
        await comm.disconnect()
        assert await sync_to_async(sweep)()
        settings.DJUST_AUTOMATIC_STATE = False
        again, _mount = await harness.mount_view(Marked, key)
        await again.disconnect()
    assert await sync_to_async(sweep)() == []


@pytest.mark.django_db
def test_another_tabs_saved_state_is_left_for_djust_clear(settings):
    """Only the tab that makes contact is known; the rest expire or are cleared."""
    key, _view = _fallback(Marked)
    settings.DJUST_AUTOMATIC_STATE = False
    request = _request("POST", key, _url(Marked), "bump")
    request.META["HTTP_X_DJUST_TAB"] = "tab-another-one"
    assert Marked().post(request).status_code == 200
    assert [k for k in SessionStore(key).keys() if k.startswith("_djust_auto_")]


# --- D2: the switch inside a config dict is refused, not ignored ------------------


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
@pytest.mark.parametrize(
    "key", ["automatic_state", "AUTOMATIC_STATE", "DJUST_AUTOMATIC_STATE", "djust_automatic_state"]
)
def test_the_switch_inside_a_config_dict_is_a_startup_error(setting, key):
    from django.core.exceptions import ImproperlyConfigured

    from djust._removed_settings import reject_removed_settings

    with override_settings(**{setting: {key: False}}):
        with pytest.raises(ImproperlyConfigured) as raised:
            reject_removed_settings()
    said = str(raised.value)
    assert f"{setting}[{key!r}] is not read" in said
    assert "top-level setting DJUST_AUTOMATIC_STATE" in said


def test_the_top_level_setting_is_not_refused():
    from djust._removed_settings import reject_removed_settings

    with override_settings(DJUST_AUTOMATIC_STATE=False, LIVEVIEW_CONFIG={}, DJUST_CONFIG={}):
        reject_removed_settings()


# --- D3: C031 decides by the store class too --------------------------------------


def test_c031_warns_for_a_subclass_of_the_cache_store_over_a_local_cache(monkeypatch):
    from django.contrib.sessions.backends.cache import SessionStore as CacheStore
    from django.contrib.sessions.backends.cached_db import SessionStore as CachedDBStore

    locmem = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    for base in (CacheStore, CachedDBStore):
        own = type("Own", (base,), {})
        engine = _engine(monkeypatch, "project_sessions_c031_%s" % base.__module__[-5:], own)
        (found,) = _check(
            "_check_process_local_session_cache", SESSION_ENGINE=engine, CACHES=locmem, DEBUG=False
        )
        assert found.id == "djust.C031" and engine in found.msg
    # A subclass of the db store keeps no sessions in a cache: silent.
    engine = _engine(monkeypatch, "project_sessions_c031_db", OwnStore)
    assert (
        _check(
            "_check_process_local_session_cache", SESSION_ENGINE=engine, CACHES=locmem, DEBUG=False
        )
        == []
    )
