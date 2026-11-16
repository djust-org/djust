"""#4046: interactive components under explicit exposure, on every transport.

A view that declares a ``DropdownMenu`` and a keyed collection of them is driven
through the page GET plus real HTTP-fallback POSTs (Django's test ``Client``,
the whole middleware stack), and the same view over a real
``WebsocketCommunicator`` and the real SSE endpoints as the reference. The same
event script must give the same state and the same component markup on all
three.

Then: the wire id cannot be forged or replayed across sessions, users,
tenants, tabs or routes; the saved record's lifecycle (opt-out, missing,
expired, tampered, over the limit, rows other tabs show, removed rows); tabs,
pages and ids (the table in ``djust._component_state``); a refused input (an
unknown selection) answered as a refused event on each transport; and, for a
component event: the view's authorization, object permission, handler
parameter contract and rate limit on the HTTP fallback, and the parameter
contract on the WebSocket.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Dict, List, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView, state
from djust._component_state import (
    PASS_THROUGH,
    TAG_LENGTH,
    resolve_component_target,
    wire_id,
)
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests.http_fallback_matrix import PAGE, post_event

MODULE = __name__
URL = harness.PATH
OTHER_URL = "/per-turn-b/"

BODY = (
    "{{ project_menu }}"
    "<ul>{% for menu in row_menus.values %}<li>{{ menu }}</li>{% endfor %}</ul>"
    '<p id="picked">{{ selected_action }}|{{ row_selection }}</p>'
)

ITEMS = [{"label": "Edit", "value": "edit"}, {"label": "Archive", "value": "archive"}]
ROW_ITEMS = [{"label": "Details", "value": "details"}, {"label": "Rename", "value": "rename"}]
ROW_NAMES = (("42", "Alpha"), ("87", "Beta"))


class Menus(LiveView):
    """A fixed menu and a keyed collection, explicit exposure (the default)."""

    exposure_policy = "explicit"

    template = PAGE % BODY
    login_required = False

    project_menu = DropdownMenu(label="Project", items=ITEMS)
    row_menus = DropdownMenu.collection()
    selected_action = state("", persist="server")
    row_selection = state("", persist="server")

    def mount(self, request, **kwargs):
        self.row_menus.sync(
            [(key, DropdownMenu(label=name, items=ROW_ITEMS)) for key, name in ROW_NAMES]
        )

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value

    @row_menus.on.selected
    def on_row_menus_selected(self, component: DropdownMenu, value: str) -> None:
        self.row_selection = "%s:%s" % (component.key, value)


class OtherMenus(LiveView):
    """The same declaration on another view class: another id."""

    exposure_policy = "explicit"

    template = PAGE % BODY
    login_required = False
    project_menu = DropdownMenu(label="Project", items=ITEMS)
    row_menus = DropdownMenu.collection()
    selected_action = state("", persist="server")
    row_selection = state("", persist="server")


class Transient(LiveView):
    """``persist=False``: the menu's state is not saved (#4046 opt-out)."""

    exposure_policy = "explicit"

    template = PAGE % "{{ project_menu }}"
    login_required = False
    project_menu = DropdownMenu(label="Project", items=ITEMS, persist=False)


#: The rows a ``Rows`` view builds, as a database would hold them.
ROWS: List[str] = ["42", "87"]


class Rows(LiveView):
    exposure_policy = "explicit"
    template = PAGE % "<ul>{% for menu in rows.values %}<li>{{ menu }}</li>{% endfor %}</ul>"
    login_required = False
    rows = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        self._sync()

    def _sync(self):
        self.rows.sync([(key, DropdownMenu(label="Row " + key, items=ROW_ITEMS)) for key in ROWS])

    @event_handler()
    def resync(self, **kwargs):
        self._sync()

    @event_handler()
    def set_rows(self, keys: str = "", **kwargs):
        ROWS[:] = [key for key in keys.split(",") if key]
        self._sync()


class Huge(LiveView):
    """A collection whose record does not fit the saved state's budget."""

    exposure_policy = "explicit"

    template = PAGE % '{{ project_menu }}<p id="picked">{{ selected_action }}|</p>'
    login_required = False
    project_menu = DropdownMenu(label="Project", items=ITEMS)
    rows = DropdownMenu.collection()
    selected_action = state("", persist="server")

    def mount(self, request, **kwargs):
        self.rows.sync(
            [
                ("k%03d" % i + "x" * 120, DropdownMenu(label="r", items=ROW_ITEMS))
                for i in range(700)
            ]
        )

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value


class LoggedIn(LiveView):
    exposure_policy = "explicit"
    template = PAGE % "{{ project_menu }}"
    login_required = True
    project_menu = DropdownMenu(label="Project", items=ITEMS)


class Owned(LiveView):
    """Object permission on a real ORM row: the signed-in user's own record."""

    exposure_policy = "explicit"

    template = PAGE % '{{ project_menu }}<p id="picked">{{ selected_action }}|</p>'
    login_required = True
    project_menu = DropdownMenu(label="Project", items=ITEMS)
    selected_action = state("", persist="server")

    def get_object(self):
        return User.objects.get(pk=self.request.user.pk)

    def has_object_permission(self, request, obj):
        return obj.first_name != "locked"

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value


class Paged(LiveView):
    """Rows chosen by ``?page``: two tabs on one route can show different rows."""

    exposure_policy = "explicit"

    template = PAGE % "<ul>{% for menu in rows.values %}<li>{{ menu }}</li>{% endfor %}</ul>"
    login_required = False
    rows = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        page = request.GET.get("page", "1")
        keys = PAGES.get(page, [])
        self.rows.sync([(key, DropdownMenu(label=key, items=ROW_ITEMS)) for key in keys])

    @event_handler()
    def drop(self, key: str = "", **kwargs):
        """Remove a row by sync() and from the "data" (a removal djust sees)."""
        for keys in PAGES.values():
            if key in keys:
                keys.remove(key)
        self.rows.sync(
            [
                (k, DropdownMenu(label=k, items=ROW_ITEMS))
                for k in [m.key for m in self.rows]
                if k != key
            ]
        )

    @event_handler()
    def add(self, key: str = "", page: str = "1", **kwargs):
        PAGES.setdefault(page, []).append(key)
        self.rows.sync(
            [
                (k, DropdownMenu(label=k, items=ROW_ITEMS))
                for k in [m.key for m in self.rows] + [key]
            ]
        )


#: The rows of each page of a ``Paged`` view, as a database would hold them.
PAGES: Dict[str, List[str]] = {}


class LongPaged(LiveView):
    """40 rows a page with 128-character keys: other pages' rows fill the
    saved state's budget before the 512-row cap."""

    exposure_policy = "explicit"

    template = PAGE % "<ul>{% for menu in rows.values %}<li>{{ menu }}</li>{% endfor %}</ul>"
    login_required = False
    rows = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        page = int(request.GET.get("page", "1"))
        keys = [("p%03dr%02d" % (page, i)).ljust(128, "x") for i in range(40)]
        self.rows.sync([(key, DropdownMenu(label="r", items=ROW_ITEMS)) for key in keys])


class Closing(LiveView):
    """``handle_params()`` closes the menu when the URL says so."""

    exposure_policy = "explicit"

    template = PAGE % "{{ project_menu }}"
    login_required = False
    project_menu = DropdownMenu(label="Project", items=ITEMS)

    def mount(self, request, **kwargs):
        # Automatic state: the saved URL is recorded, so the socket's
        # re-apply after handle_params() runs (the path under test).
        self.note = "kept"

    def handle_params(self, params, url, **kwargs):
        if params.get("closed"):
            self.project_menu.open = False


urlpatterns = [
    path(URL.strip("/") + "/", Menus.as_view()),
    path(OTHER_URL.strip("/") + "/", Menus.as_view()),
    path("other-class/", OtherMenus.as_view()),
    path("transient/", Transient.as_view()),
    path("rows/", Rows.as_view()),
    path("huge/", Huge.as_view()),
    path("logged-in/", LoggedIn.as_view()),
    path("owned/", Owned.as_view()),
    path("paged/", Paged.as_view()),
    path("closing/", Closing.as_view()),
    path("long-paged/", LongPaged.as_view()),
]

_ID = re.compile(r'data-component-id="([^"]+)"')
_PICKED = re.compile(r'<p[^>]*\bid="picked"[^>]*>([^<]*)</p>')
_TAB = re.compile(r'<meta name="djust-tab" content="([^"]+)">')


def ids(html: str) -> List[str]:
    return _ID.findall(html)


def canonical(html: str) -> str:
    """The root's markup independent of transport framing: attributes sorted,
    ``dj-id`` dropped, each component id replaced by its position on the page
    (so the same page from two sessions compares equal), whitespace between
    tags dropped. Everything outside ``dj-root`` is ignored."""
    from html.parser import HTMLParser

    start = html.find("dj-root")
    if start != -1:
        html = html[html.index(">", start) + 1 :]
        end = html.rfind("</div>")
        html = html[:end] if end != -1 else html
    positions: Dict[str, str] = {}
    for found in ids(html):
        positions.setdefault(found, str(len(positions)))
    out: List[str] = []

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            kept = []
            for name, value in sorted(attrs):
                if name == "dj-id":
                    continue
                if value is not None:
                    for wire, pos in positions.items():
                        value = value.replace(wire, "C%s" % pos)
                kept.append('%s="%s"' % (name, value) if value is not None else name)
            out.append("<%s%s>" % (tag, "".join(" " + k for k in kept)))

        def handle_endtag(self, tag):
            out.append("</%s>" % tag)

        def handle_data(self, data):
            if data.strip():
                out.append(data.strip())

    _P(convert_charrefs=True).feed(html)
    return "".join(out)


def picked(html: str) -> str:
    match = _PICKED.search(html)
    return match.group(1) if match else ""


def opened(html: str) -> List[bool]:
    """Each component's open flag, in page order."""
    return [
        "dj-dropdown-menu--open" in tag
        for tag in re.findall(r"<div[^>]*data-component-id=[^>]*>", html)
    ]


#: The script: indexes are positions in the page's component ids
#: (0 = project menu, 1 = row 42, 2 = row 87).
SCRIPT: List[Tuple[str, int, Dict[str, Any]]] = [
    ("toggle", 0, {}),
    ("toggle", 0, {}),
    ("toggle", 0, {}),
    ("select", 0, {"value": "edit"}),
    ("toggle", 2, {}),
    ("select", 2, {"value": "details"}),
]


# --------------------------------------------------------------------------- #
# The three transports
# --------------------------------------------------------------------------- #


class FallbackPage:
    """A page GET, then one real fallback POST per event, on one client."""

    def __init__(self, url: str = URL, client: Client | None = None) -> None:
        self.url = url
        self.client = client or Client()
        got = self.client.get(url)
        assert got.status_code == 200, got.content[:500]
        self.html = got.content.decode()
        self.tab = _TAB.search(self.html).group(1)
        self.ids = ids(self.html)
        self.bodies: List[str] = [self.html]

    def post(self, name: str, params: Dict[str, Any], *, tab: str | None = None) -> int:
        answer = post_event(
            self.client, self.url, name, params, HTTP_X_DJUST_TAB=self.tab if tab is None else tab
        )
        self.last = json.loads(answer.content) if answer.content else {}
        self.bodies.append(answer.content.decode())
        body = json.loads(answer.content) if answer.content else {}
        if "html" in body:
            self.html = body["html"]
        return answer.status_code

    def event(self, name: str, which: Any, **params: Any) -> int:
        component_id = self.ids[which] if type(which) is int else which
        return self.post(name, {"component_id": component_id, **params})


def run_fallback(script=SCRIPT) -> List[Tuple[int, str]]:
    page = FallbackPage()
    return [(page.event(name, which, **params), page.html) for name, which, params in script]


async def ws_event(comm: Any, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """One event; its reply, or the error frame that refused it (an error
    frame carries no ``ref``)."""
    from ._ws_frames import receive_until

    harness._REF[0] += 1
    ref = harness._REF[0]
    await comm.send_json_to({"type": "event", "event": name, "params": params, "ref": ref})
    frames = await receive_until(
        comm,
        lambda fs: any(
            f.get("type") == "error"
            or (f.get("type") in harness.REPLY_TYPES and f.get("ref") == ref)
            for f in fs
        ),
        what="the reply to %r" % name,
    )
    return frames[-1]


async def run_websocket(script=SCRIPT, key: str | None = None) -> List[Tuple[int, str]]:
    key = key or await sync_to_async(harness.new_session_key)()
    comm, mount = await harness.mount_view(Menus, key)
    html = mount["html"]
    page_ids = ids(html)
    out = []
    try:
        for name, which, params in script:
            frame = await ws_event(comm, name, {"component_id": page_ids[which], **params})
            if frame.get("html"):
                html = frame["html"]
            out.append((400 if frame["type"] == "error" else 200, html))
    finally:
        await comm.disconnect()
    return out


#: The error texts the ``run_sse`` calls received, in order.
SSE_ERRORS: List[Any] = []
SSE_ERROR_FRAMES: List[Any] = []  # whole error frames, for the DEBUG-detail checks


async def run_sse(
    script=SCRIPT, view: str = "Menus", key: str | None = None, between: Any = None
) -> List[Tuple[int, str]]:
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    from .test_sse_runtime_convergence_1887 import _drain

    def request_for(method: str, target: str, body: Any, key: str | None = None) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        if key is None:
            made.session.create()
        made.session = SessionStore(made.session.session_key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    first = await sync_to_async(request_for)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": f"{MODULE}.{view}", "_djust_url": URL, "_djust_tab": harness.TAB},
        key,
    )
    response = await DjustSSEStreamView().get(first, session_id=sid)
    assert response.status_code == 200
    key = first.session.session_key
    session = _sse_sessions[sid]
    out = []
    try:
        (mount,) = [m for m in _drain(session) if m.get("type") == "mount"]
        html = mount["html"]
        page_ids = ids(html)
        for name, which, params in script:
            if between is not None:
                await sync_to_async(between)()
            component_id = page_ids[which] if type(which) is int else which
            body = {
                "type": "event",
                "event": name,
                "params": {"component_id": component_id, **params},
            }
            posted = await sync_to_async(request_for)(
                "POST", f"/djust/sse/{sid}/message/", body, key
            )
            await DjustSSEMessageView().post(posted, session_id=sid)
            frames = _drain(session)
            SSE_ERRORS.extend(f.get("error") for f in frames if f.get("type") == "error")
            SSE_ERROR_FRAMES.extend(f for f in frames if f.get("type") == "error")
            status = 400 if any(f.get("type") == "error" for f in frames) else 200
            for frame in frames:
                if frame.get("html"):
                    html = frame["html"]
            out.append((status, html))
    finally:
        _sse_sessions.pop(sid, None)
    return out


@pytest.fixture
def routes():
    from djust import _component_state

    ROWS[:] = ["42", "87"]
    PAGES.clear()
    PAGES.update({"1": ["a1", "a2"], "2": ["b1", "b2"]})
    _component_state._WARNED_OVER_LIMIT.clear()
    _component_state._WARNED_ROWS_LEFT_OUT.clear()
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, ALLOWED_HOSTS=["*"]):
        yield


def _summary(steps):
    return [(status, picked(html), canonical(html)) for status, html in steps]


def _envelope(session_key: str) -> Dict[str, Any]:
    """The view's declared server state as stored (one envelope)."""
    stored = SessionStore(session_key)
    envelopes = [v for k, v in stored.items() if k.startswith("_djust_explicit_")]
    assert len(envelopes) == 1, list(stored.keys())
    return envelopes[0]["state"]["values"]


# --------------------------------------------------------------------------- #
# Reproduction and parity: the same script on each transport
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_fallback_runs_the_script(routes):
    steps = run_fallback()
    assert [s for s, _ in steps] == [200] * len(SCRIPT), _summary(steps)
    assert [opened(html)[0] for _, html in steps[:3]] == [True, False, True]
    assert picked(steps[-1][1]) == "edit|87:details"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_runs_the_script(routes):
    steps = await run_websocket()
    assert [s for s, _ in steps] == [200] * len(SCRIPT), _summary(steps)
    assert picked(steps[-1][1]) == "edit|87:details"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_runs_the_script(routes):
    steps = await run_sse()
    assert [s for s, _ in steps] == [200] * len(SCRIPT), _summary(steps)
    assert picked(steps[-1][1]) == "edit|87:details"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_three_transports_render_the_same_page_after_every_step(routes):
    fallback = await sync_to_async(run_fallback)()
    websocket = await run_websocket()
    sse = await run_sse()
    for step, (f, w, s) in enumerate(zip(fallback, websocket, sse)):
        assert f[0] == w[0] == s[0] == 200, (step, f[0], w[0], s[0])
        assert canonical(f[1]) == canonical(w[1]) == canonical(s[1]), (
            step,
            canonical(f[1]),
            canonical(w[1]),
            canonical(s[1]),
        )


@pytest.mark.django_db(transaction=True)
def test_the_fallback_saves_the_record_every_turn(routes):
    page = FallbackPage()
    key = page.client.session.session_key
    for expected in (True, False, True):
        assert page.event("toggle", 0) == 200
        assert _envelope(key)["project_menu"][0] is expected
    assert page.event("select", 2, value="rename") == 200
    rows = _envelope(key)["row_menus"]
    assert rows["members"]["87"][3] == "rename"
    # Only what the component owns: no label, no items, no callbacks.
    assert "Beta" not in json.dumps(_envelope(key))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_websocket_reconnect_restores_the_menus(routes):
    key = await sync_to_async(harness.new_session_key)()
    await run_websocket([("toggle", 0, {}), ("toggle", 1, {})], key=key)
    comm, mount = await harness.mount_view(Menus, key)
    try:
        assert opened(mount["html"]) == [True, True, False]
        page_ids = ids(mount["html"])
        frame = await ws_event(comm, "toggle", {"component_id": page_ids[0]})
        assert frame["type"] != "error", frame
        assert opened(frame["html"]) == [False, True, False]
    finally:
        await comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_menu_opened_over_the_fallback_is_open_on_a_socket_of_the_same_page(routes):
    def open_over_fallback():
        page = FallbackPage()
        assert page.event("toggle", 0) == 200
        return page.client.session.session_key, page.tab

    key, tab = await sync_to_async(open_over_fallback)()
    comm, mount = await harness.mount_view(Menus, key, tab=tab)
    await comm.disconnect()
    assert opened(mount["html"])[0] is True


# --------------------------------------------------------------------------- #
# Forgery and replay
# --------------------------------------------------------------------------- #


def _request(session_key: str | None = None, *, user=None, tenant=None, path_=URL) -> Any:
    request = RequestFactory().post(path_)
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    request.user = user if user is not None else AnonymousUser()
    request.tenant = tenant
    return request


def _view(request, tab: str = "tab-1", cls=Menus) -> Any:
    view = cls()
    view.request = request
    view.__dict__["_djust_tab"] = tab
    view.mount(request)
    return view


def _wire(request, tab: str = "tab-1", cls=Menus) -> str:
    return wire_id(_view(request, tab, cls).project_menu)


@pytest.mark.django_db(transaction=True)
def test_the_page_ids_are_derived_and_tagged(routes):
    request = _request()
    first, second = _view(request), _view(request)
    # The same declaration builds the same id on every request (another
    # server, a restart): derived, not random.
    assert first.project_menu.component_id == second.project_menu.component_id
    raw, tag = wire_id(first.project_menu).split(".")
    assert raw == first.project_menu.component_id and len(tag) == TAG_LENGTH
    assert resolve_component_target(second, wire_id(first.project_menu)) is second.project_menu


@pytest.mark.django_db(transaction=True)
def test_a_bare_derived_id_is_refused(routes):
    page = FallbackPage()
    bare = page.ids[0].split(".")[0]
    assert page.event("toggle", bare) == 400
    view = _view(_request())
    assert resolve_component_target(view, view.project_menu.component_id) is None


@pytest.mark.django_db(transaction=True)
def test_an_id_from_another_session_is_refused(routes):
    mine, theirs = FallbackPage(), FallbackPage()
    # The tab id is not a secret (the client sends it): send the victim's, so
    # only the session in the binding can refuse.
    assert theirs.post("toggle", {"component_id": mine.ids[0]}, tab=mine.tab) == 400
    assert theirs.last == {"error": "Component not found"}
    assert theirs.event("toggle", 0) == 200


@pytest.mark.django_db(transaction=True)
def test_an_id_from_another_user_is_refused(routes):
    alice = User.objects.create_user("alice", password="x")
    bob = User.objects.create_user("bob", password="x")
    key = SessionStore()
    key.create()
    session_key = key.session_key
    # Same session and route, another principal: the binding differs.
    wire = _wire(_request(session_key, user=alice))
    assert resolve_component_target(_view(_request(session_key, user=bob)), wire) is None
    assert resolve_component_target(_view(_request(session_key, user=alice)), wire) is not None
    # End to end: two signed-in clients.
    a, b = Client(), Client()
    a.force_login(alice)
    b.force_login(bob)
    alices, bobs = FallbackPage(client=a), FallbackPage(client=b)
    assert bobs.post("toggle", {"component_id": alices.ids[0]}, tab=alices.tab) == 400
    assert bobs.event("toggle", 0) == 200


@pytest.mark.django_db(transaction=True)
def test_an_id_from_another_tenant_is_refused(routes):
    from djust.tenants.resolvers import TenantInfo

    key = SessionStore()
    key.create()
    session_key = key.session_key
    wire = _wire(_request(session_key, tenant=TenantInfo("acme")))
    assert (
        resolve_component_target(_view(_request(session_key, tenant=TenantInfo("other"))), wire)
        is None
    )
    assert (
        resolve_component_target(_view(_request(session_key, tenant=TenantInfo("acme"))), wire)
        is not None
    )


@pytest.mark.django_db(transaction=True)
def test_an_id_from_another_tab_is_refused(routes):
    client = Client()
    first, second = FallbackPage(client=client), FallbackPage(client=client)
    assert first.tab != second.tab
    # The second tab's request with the first tab's id.
    assert second.post("toggle", {"component_id": first.ids[0]}) == 400
    assert first.post("toggle", {"component_id": first.ids[0]}) == 200


@pytest.mark.django_db(transaction=True)
def test_an_id_from_another_route_is_refused(routes):
    client = Client()
    here, there = FallbackPage(URL, client), FallbackPage(OTHER_URL, client)
    # Same session, the page's own tab: only the route can refuse.
    assert there.post("toggle", {"component_id": here.ids[0]}, tab=here.tab) == 400
    assert there.event("toggle", 0) == 200


@pytest.mark.django_db(transaction=True)
def test_an_id_of_another_view_class_is_refused(routes):
    client = Client()
    other = FallbackPage("/other-class/", client)
    page = FallbackPage(URL, client)
    assert page.post("toggle", {"component_id": other.ids[0]}, tab=page.tab) == 400


@pytest.mark.django_db(transaction=True)
def test_a_tag_made_without_the_secret_is_refused(routes, settings):
    request = _request()
    with override_settings(SECRET_KEY="not-the-servers-secret-" + "x" * 40):
        forged = _wire(request)
    assert resolve_component_target(_view(request), forged) is None
    assert resolve_component_target(_view(request), _wire(request)) is not None


@pytest.mark.django_db(transaction=True)
def test_a_rotated_secret_is_honoured_through_its_fallbacks(routes, settings):
    request = _request()
    old = settings.SECRET_KEY
    rendered = _wire(request)
    with override_settings(SECRET_KEY="rotated-" + "y" * 50, SECRET_KEY_FALLBACKS=[old]):
        assert resolve_component_target(_view(request), rendered) is not None
    with override_settings(SECRET_KEY="rotated-" + "y" * 50, SECRET_KEY_FALLBACKS=[]):
        assert resolve_component_target(_view(request), rendered) is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "variant",
    [
        lambda w: w.upper(),
        lambda w: w[:-1] + ("0" if w[-1] != "0" else "1"),
        lambda w: w[:-1],
        lambda w: w + "0",
        lambda w: w.replace(".", "%2E"),
        lambda w: w.replace(".", ".."),
        lambda w: w + " ",
        lambda w: " " + w,
        lambda w: w.split(".")[0] + "." + w.split(".")[1].upper(),
        lambda w: w + "." + w.split(".")[1],
        lambda w: w.split(".")[0] + "." + "0" * TAG_LENGTH,
    ],
    ids=[
        "upper",
        "last-digit",
        "short",
        "long",
        "percent-encoded",
        "double-dot",
        "trailing-space",
        "leading-space",
        "upper-tag",
        "two-tags",
        "zero-tag",
    ],
)
def test_encoded_and_altered_wire_ids_are_refused(routes, variant):
    request = _request()
    view = _view(request)
    wire = wire_id(view.project_menu)
    altered = variant(wire)
    assert altered != wire
    assert resolve_component_target(view, altered) is None


@pytest.mark.django_db(transaction=True)
def test_a_forged_member_key_is_refused(routes):
    view = _view(_request())
    raw = view.row_menus.get("42").component_id
    forged_member = raw[:-1] + ("0" if raw[-1] != "0" else "1")
    assert resolve_component_target(view, forged_member + "." + "a" * TAG_LENGTH) is None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_socket_refuses_a_bare_id_and_another_sessions_id(routes):
    key_a = await sync_to_async(harness.new_session_key)()
    key_b = await sync_to_async(harness.new_session_key)()
    a, mount_a = await harness.mount_view(Menus, key_a)
    b, mount_b = await harness.mount_view(Menus, key_b)
    try:
        theirs = ids(mount_a["html"])[0]
        for refused in (theirs, theirs.split(".")[0]):
            frame = await ws_event(b, "toggle", {"component_id": refused})
            assert frame["type"] == "error", frame
        frame = await ws_event(b, "toggle", {"component_id": ids(mount_b["html"])[0]})
        assert frame["type"] != "error", frame
    finally:
        await a.disconnect()
        await b.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_refuses_a_bare_id(routes):
    steps = await run_sse([("toggle", 0, {})])
    assert steps[0][0] == 200
    bare = ids(steps[0][1])[0].split(".")[0]
    refused = await run_sse([("toggle", bare, {})])
    assert refused[0][0] == 400


@pytest.mark.django_db(transaction=True)
def test_nothing_of_the_record_reaches_the_client(routes):
    page = FallbackPage()
    assert page.event("toggle", 2) == 200
    assert page.event("select", 0, value="edit") == 200
    sent = "\n".join(page.bodies)
    assert "_djust_explicit_" not in sent
    assert '"members"' not in sent and '"epoch"' not in sent
    assert "row_menus" not in sent


# --------------------------------------------------------------------------- #
# The saved record's lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_persist_false_starts_the_menu_from_mount_on_every_request(routes):
    page = FallbackPage("/transient/")
    assert page.event("toggle", 0) == 200
    assert opened(page.html) == [True]
    # Not saved: the next request's menu starts closed, so toggle opens it.
    assert page.event("toggle", 0) == 200
    assert opened(page.html) == [True]
    stored = SessionStore(page.client.session.session_key)
    assert not [k for k in stored.keys() if k.startswith("_djust_explicit_")]


def test_persist_accepts_only_server_or_false():
    for bad in ("client", None, True, "SERVER"):
        with pytest.raises(TypeError):
            DropdownMenu(label="x", items=ITEMS, persist=bad)
        with pytest.raises(TypeError):
            DropdownMenu.collection(persist=bad)


@pytest.mark.django_db(transaction=True)
def test_a_collection_key_is_bounded_only_where_it_is_saved(routes):
    view = _view(_request())
    with pytest.raises(ValueError):
        view.row_menus.sync([("k" * 129, DropdownMenu(label="x", items=ROW_ITEMS))])
    view.row_menus.sync([("k" * 128, DropdownMenu(label="x", items=ROW_ITEMS))])

    class Unsaved(LiveView):
        exposure_policy = "explicit"
        template = PAGE % ""
        rows = DropdownMenu.collection(persist=False)

    class Legacy(LiveView):
        exposure_policy = "legacy"  # legacy subject: no key limit on a legacy view
        template = PAGE % ""
        rows = DropdownMenu.collection()

    for cls in (Unsaved, Legacy):
        other = cls()
        other.rows.sync([("k" * 500, DropdownMenu(label="x", items=ROW_ITEMS))])
        assert [m.key for m in other.rows] == ["k" * 500]


def _drop_envelope(session_key: str) -> None:
    stored = SessionStore(session_key)
    for key in [k for k in stored.keys() if k.startswith("_djust_explicit_")]:
        del stored[key]
    stored.save()


def _rewrite_envelope(session_key: str, change) -> None:
    stored = SessionStore(session_key)
    (key,) = [k for k in stored.keys() if k.startswith("_djust_explicit_")]
    envelope = stored[key]
    change(envelope)
    stored[key] = envelope
    stored.save()


@pytest.mark.django_db(transaction=True)
def test_a_missing_record_starts_the_menus_fresh_and_still_routes(routes):
    page = FallbackPage()
    assert page.event("toggle", 0) == 200
    _drop_envelope(page.client.session.session_key)
    # Fresh (closed), so this toggle opens it; no error.
    assert page.event("toggle", 0) == 200
    assert opened(page.html)[0] is True
    assert page.event("toggle", 0) == 200
    assert opened(page.html)[0] is False


@pytest.mark.django_db(transaction=True)
def test_an_expired_record_starts_the_menus_fresh_and_still_routes(routes, monkeypatch):
    import time as real_time

    from djust import _exposure_sessions

    page = FallbackPage()
    assert page.event("toggle", 0) == 200
    later = real_time.time() + _exposure_sessions.server_state_max_age() + 5
    monkeypatch.setattr(_exposure_sessions.time, "time", lambda: later)
    assert page.event("toggle", 0) == 200
    assert opened(page.html)[0] is True  # fresh, then toggled open


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "change",
    [
        lambda e: e["state"]["values"].__setitem__("project_menu", [True, "edit"]),
        lambda e: e["state"]["values"].__setitem__("project_menu", [1, "edit", "", 0]),
        lambda e: e["state"]["values"]["row_menus"]["members"]["42"].__setitem__(2, "yes"),
        lambda e: e["state"]["values"]["row_menus"]["members"].__setitem__(
            "x" * 200, [0, False, False, "", "", 0]
        ),
        lambda e: e["state"]["values"]["row_menus"]["members"]["42"].__setitem__(0, -1),
        lambda e: e["state"]["values"]["row_menus"].__setitem__(
            "members", {"k%d" % i: [0, False, False, "", "", 0] for i in range(9000)}
        ),
    ],
    ids=[
        "short",
        "wrong-type",
        "member-state",
        "long-key",
        "negative-generation",
        "over-the-limit",
    ],
)
def test_a_tampered_record_rejects_the_whole_envelope(routes, change):
    page = FallbackPage()
    assert page.event("select", 0, value="edit") == 200
    assert page.event("toggle", 1) == 200
    _rewrite_envelope(page.client.session.session_key, change)
    # The whole envelope is a miss: the declared field is back to its default
    # too, the menus are fresh, and the event still routes.
    assert page.event("toggle", 0) == 200
    assert picked(page.html) == "|"
    assert opened(page.html) == [True, False, False]


@pytest.mark.django_db(transaction=True)
def test_component_records_over_the_limit_are_left_out_without_an_error_loop(routes, caplog):
    page = FallbackPage("/huge/")
    key = page.client.session.session_key
    assert _envelope(key)["rows"] is None
    for _ in range(3):
        assert page.event("select", 0, value="edit") == 200
    assert picked(page.html) == "edit|"
    values = _envelope(key)
    assert values["selected_action"] == "edit" and values["rows"] is None
    assert values["project_menu"] is None
    warnings = [
        r for r in caplog.records if "interactive component state was not saved" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "x" * 20 not in warnings[0].getMessage()


@pytest.mark.django_db(transaction=True)
def test_a_row_that_stops_being_built_keeps_its_id_and_state(routes):
    """Not a sync() removal (the row left the data between requests): djust
    cannot tell it from another tab's row, so it is carried, not dropped."""
    page = FallbackPage("/rows/")
    old_42 = page.ids[0]
    assert page.event("toggle", 0) == 200  # row 42 open, saved
    ROWS[:] = ["87"]  # row 42 leaves the "database"
    assert page.post("resync", {}) == 200
    assert len(ids(page.html)) == 1
    ROWS[:] = ["42", "87"]  # and comes back
    assert page.post("resync", {}) == 200
    assert ids(page.html)[0] == old_42
    assert opened(page.html) == [True, False]


@pytest.mark.django_db(transaction=True)
def test_a_removed_rows_old_id_gets_the_current_page_across_requests(routes):
    page = FallbackPage("/rows/")
    old_42, old_87 = page.ids
    assert page.post("set_rows", {"keys": "87"}) == 200  # removed by sync()
    assert page.post("set_rows", {"keys": "42,87"}) == 200
    new_42, same_87 = ids(page.html)
    assert same_87 == old_87 and new_42 != old_42
    for _ in range(2):
        # A stale id (its tag is genuine): no handler, the current page back.
        assert page.post("toggle", {"component_id": old_42}) == 200
        assert opened(page.html) == [False, False] and old_42 not in page.html
        assert ids(page.html) == [new_42, same_87]
    assert page.post("toggle", {"component_id": same_87}) == 200
    assert opened(page.html) == [False, True]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "store",
    ["djust.state_store.SessionViewStateStore", "djust.state_store.SealedSessionViewStateStore"],
)
def test_the_script_runs_with_each_state_store(routes, store):
    from djust.state_store import generate_key

    with override_settings(DJUST_STATE_STORE=store, DJUST_STATE_STORE_KEYS=[generate_key()]):
        steps = run_fallback()
    assert [s for s, _ in steps] == [200] * len(SCRIPT), _summary(steps)
    assert picked(steps[-1][1]) == "edit|87:details"


# --------------------------------------------------------------------------- #
# A component event passes every gate a view event passes
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_view_authorization_runs_before_the_component(routes):
    user = User.objects.create_user("carol", password="x")
    client = Client()
    client.force_login(user)
    page = FallbackPage("/logged-in/", client)
    assert page.event("toggle", 0) == 200
    client.logout()
    assert page.event("toggle", 0) == 403


@pytest.mark.django_db(transaction=True)
def test_object_permission_runs_before_the_component(routes):
    user = User.objects.create_user("dave", password="x")
    client = Client()
    client.force_login(user)
    page = FallbackPage("/owned/", client)
    assert page.event("toggle", 0) == 200
    User.objects.filter(pk=user.pk).update(first_name="locked")
    assert page.event("select", 0, value="edit") == 403
    User.objects.filter(pk=user.pk).update(first_name="")
    values = _envelope(client.session.session_key)
    assert values["selected_action"] == "" and values["project_menu"][0] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "params",
    [{"value": 1}, {"value": "edit", "extra": "x"}, {}],
    ids=["wrong-type", "unknown-key", "missing"],
)
def test_the_handler_parameter_contract_applies_on_the_fallback(routes, params):
    page = FallbackPage()
    assert page.event("select", 0, **params) == 400
    assert page.event("select", 0, value="edit") == 200


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_handler_parameter_contract_applies_on_the_socket(routes):
    steps = await run_websocket(
        [
            ("select", 0, {"value": 1}),
            ("select", 0, {"value": "edit", "extra": "x"}),
            ("select", 0, {"value": "edit"}),
        ]
    )
    assert [s for s, _ in steps] == [400, 400, 200]


@pytest.mark.django_db(transaction=True)
def test_the_rate_limit_applies_to_a_component_event_on_the_fallback(routes, monkeypatch):
    from djust import rate_limit

    seen = []

    def deny(request, owner, event_name, handler):
        seen.append((type(owner).__name__, event_name))
        return False

    page = FallbackPage()
    monkeypatch.setattr(rate_limit, "http_handler_rate_check", deny)
    assert page.event("toggle", 0) == 429
    assert seen == [("DropdownMenu", "toggle")]


@pytest.mark.django_db(transaction=True)
def test_a_component_id_never_routes_through_view_id(routes):
    page = FallbackPage()
    assert page.post("toggle", {"component_id": page.ids[0], "view_id": "not-a-child"}) == 400
    assert page.event("toggle", 0) == 200
    assert opened(page.html)[0] is True  # the refused event changed nothing


@pytest.mark.django_db(transaction=True)
def test_an_unmaterialized_descriptor_name_falls_through_on_every_transport(routes):
    view = _view(_request())
    type(view)._component_descriptors = {"legacy_widget": object()}
    try:
        assert resolve_component_target(view, "legacy_widget") is PASS_THROUGH
    finally:
        del type(view)._component_descriptors
    assert resolve_component_target(view, "legacy_widget") is None


@pytest.mark.django_db(transaction=True)
def test_a_member_retired_by_sync_in_a_handler_takes_a_new_identity_when_added_back(routes):
    page = FallbackPage("/rows/")
    old_42, old_87 = page.ids
    assert page.post("set_rows", {"keys": "87"}) == 200  # retired by sync() in the handler
    assert ids(page.html) == [old_87]
    assert page.post("set_rows", {"keys": "42,87"}) == 200
    new_42, same_87 = ids(page.html)
    assert same_87 == old_87 and new_42 != old_42
    assert page.post("toggle", {"component_id": old_42}) == 200  # stale: page, no toggle
    assert opened(page.html) == [False, False]
    assert page.post("toggle", {"component_id": new_42}) == 200
    assert opened(page.html) == [True, False]


# --------------------------------------------------------------------------- #
# Tabs, pages and ids (the table in djust._component_state's docstring)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_two_tabs_on_different_pages_of_one_route_keep_their_ids(routes):
    client = Client()
    one, two = FallbackPage("/paged/?page=1", client), FallbackPage("/paged/?page=2", client)
    steps = []
    for _ in range(3):
        steps.append(one.event("toggle", 0))
        steps.append(two.event("toggle", 1))
    assert steps == [200] * 6
    assert opened(one.html) == [True, False] and opened(two.html) == [False, True]
    assert ids(one.html) == one.ids and ids(two.html) == two.ids


@pytest.mark.django_db(transaction=True)
def test_two_tabs_on_the_same_page_share_the_state_not_the_ids(routes):
    client = Client()
    one, two = FallbackPage("/paged/?page=1", client), FallbackPage("/paged/?page=1", client)
    assert [i.split(".")[0] for i in one.ids] == [i.split(".")[0] for i in two.ids]
    assert one.ids != two.ids  # the tag is per tab
    assert one.event("toggle", 0) == 200
    assert two.event("toggle", 1) == 200
    assert opened(two.html) == [True, True]  # tab 1's open row, per session and route


@pytest.mark.django_db(transaction=True)
def test_a_reload_in_one_tab_leaves_the_other_tab_working(routes):
    client = Client()
    one = FallbackPage("/paged/?page=1", client)
    assert one.post("drop", {"key": "a1"}) == 200
    assert one.post("add", {"key": "a1"}) == 200  # a1 now at generation 1
    readded = ids(one.html)[-1]
    FallbackPage("/paged/?page=1", client)  # another tab reloads: a fresh record
    # a2 (generation 0) still answers; a1's id is stale: the current page back.
    assert one.event("toggle", ids(one.html)[0]) == 200
    assert one.post("toggle", {"component_id": readded}) == 200
    assert readded not in one.html
    assert one.event("toggle", ids(one.html)[-1]) == 200


@pytest.mark.django_db(transaction=True)
def test_back_to_an_older_page_answers_except_for_a_removed_row(routes):
    client = Client()
    older = FallbackPage("/paged/?page=1", client)
    newer = FallbackPage("/paged/?page=1", client)
    assert newer.post("drop", {"key": "a1"}) == 200
    # The older page (Back) is a page this server rendered: a2 answers ...
    assert older.event("toggle", 1) == 200
    assert opened(older.html) == [True]
    # ... a1, removed since, gets the current page instead of a dead menu.
    assert older.post("toggle", {"component_id": older.ids[0]}) == 200
    assert older.ids[0] not in older.html


@pytest.mark.django_db(transaction=True)
def test_a_row_removed_in_one_tab_is_replaced_in_the_other(routes):
    client = Client()
    one, two = FallbackPage("/paged/?page=1", client), FallbackPage("/paged/?page=1", client)
    assert one.post("drop", {"key": "a2"}) == 200
    assert two.post("toggle", {"component_id": two.ids[1]}) == 200
    assert "error" not in two.last and len(ids(two.html)) == 1
    assert two.event("toggle", ids(two.html)[0]) == 200


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_socket_tab_and_a_fallback_tab_keep_each_others_ids(routes):
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser

    from djust.websocket import LiveViewConsumer

    client = Client()
    fallback = await sync_to_async(FallbackPage)("/paged/?page=2", client)
    key = await sync_to_async(lambda: client.session.session_key)()
    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to(
        {"type": "mount", "view": f"{MODULE}.Paged", "url": "/paged/?page=1", "tab": "socket-tab"}
    )
    mount = await comm.receive_json_from()
    socket_ids = ids(mount["html"])
    try:
        for i in range(2):
            frame = await ws_event(comm, "toggle", {"component_id": socket_ids[i]})
            assert frame["type"] != "error", frame
            assert await sync_to_async(fallback.event)("toggle", i) == 200
        assert opened(fallback.html) == [True, True]
    finally:
        await comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_removed_rows_id_is_refused_on_the_socket(routes):
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser

    from djust.websocket import LiveViewConsumer

    key = await sync_to_async(harness.new_session_key)()
    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to(
        {"type": "mount", "view": f"{MODULE}.Paged", "url": "/paged/?page=1", "tab": "t"}
    )
    old = ids((await comm.receive_json_from())["html"])
    try:
        frame = await ws_event(comm, "drop", {"key": "a1"})
        assert frame["type"] != "error", frame
        frame = await ws_event(comm, "toggle", {"component_id": old[0]})
        assert frame["type"] == "error" and frame["error"].startswith("Component not found"), frame
    finally:
        await comm.disconnect()


@pytest.mark.django_db(transaction=True)
def test_a_row_deleted_from_the_data_and_re_added_answers_to_its_old_id(routes):
    """The documented limit: not a sync() removal, so the same generation."""
    page = FallbackPage("/paged/?page=1")
    PAGES["1"] = ["a2"]
    assert page.event("toggle", 1) == 200
    PAGES["1"] = ["a1", "a2"]
    assert page.event("toggle", 0) == 200
    assert opened(page.html)[0] is True


# --------------------------------------------------------------------------- #
# Malformed wire ids: one answer, never an error (review item 2)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_malformed_tags_are_refused_like_an_unknown_id(routes):
    page = FallbackPage()
    raw = page.ids[0].split(".")[0]
    unknown = page.post("toggle", {"component_id": "cmp_" + "0" * 32 + "." + "0" * 32})
    expected = page.last
    assert unknown == 400 and expected == {"error": "Component not found"}
    for tag in (
        "é" * 32,
        "\uff10" * 32,
        "\x00" * 32,
        "A" * 32,
        "g" * 32,
        "0" * 31 + "\u0660",
        "0" * 33,
    ):
        assert page.post("toggle", {"component_id": raw + "." + tag}) == 400, repr(tag)
        assert page.last == expected
    assert page.event("toggle", 0) == 200


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_malformed_tag_on_the_socket_is_the_unknown_id_error(routes):
    key = await sync_to_async(harness.new_session_key)()
    comm, mount = await harness.mount_view(Menus, key)
    raw = ids(mount["html"])[0].split(".")[0]
    try:
        for tag in ("é" * 32, "\uff10" * 32):
            frame = await ws_event(comm, "toggle", {"component_id": raw + "." + tag})
            assert frame["type"] == "error" and frame["error"].startswith("Component not found"), (
                frame
            )
        frame = await ws_event(comm, "toggle", {"component_id": ids(mount["html"])[0]})
        assert frame["type"] != "error", frame
    finally:
        await comm.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("seed", range(5))
def test_the_wire_id_parser_never_raises(routes, seed):
    import random

    rng = random.Random(4046 + seed)
    view = _view(_request())
    good = wire_id(view.project_menu)
    alphabet = "0123456789abcdefABCDEF.cmp_\x00\u00e9\uff10\u0660\ud7ff\U0001f600 %/\\"
    for _ in range(400):
        n = rng.randrange(0, 120)
        text = "".join(rng.choice(alphabet) for _ in range(n))
        if rng.random() < 0.5:
            cut = rng.randrange(0, len(good))
            text = good[:cut] + text[: len(good) - cut]
        if rng.random() < 0.2:
            text = bytes(rng.randrange(256) for _ in range(n)).decode("latin-1")
        result = resolve_component_target(view, text)
        assert result is None or result is view.project_menu
        if result is view.project_menu:
            assert text == good


# --------------------------------------------------------------------------- #
# What the reviewer's mutations survived (review item 5)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_handle_params_has_the_last_word_over_a_components_saved_state_on_the_fallback(routes):
    page = FallbackPage("/closing/?closed=1")
    assert page.event("toggle", 0) == 200
    assert opened(page.html) == [True]  # handle_params ran before the event
    # Next request: the record says open, handle_params closes it, toggle opens.
    assert page.event("toggle", 0) == 200
    assert opened(page.html) == [True]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_handle_params_has_the_last_word_over_a_components_saved_state_on_the_socket(routes):
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser

    from djust.websocket import LiveViewConsumer

    def open_over_fallback():
        page = FallbackPage("/closing/?closed=1")
        assert page.event("toggle", 0) == 200
        assert opened(page.html) == [True]  # saved open, under this URL and tab
        return page.client.session.session_key, page.tab

    key, tab = await sync_to_async(open_over_fallback)()
    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{MODULE}.Closing",
            "url": "/closing/?closed=1",
            "params": {"closed": "1"},
            "tab": tab,
        }
    )
    frame = await comm.receive_json_from()
    await comm.disconnect()
    # The record went back once, before handle_params(), which closed it; it
    # is not assigned again after handle_params() as a declared field is.
    assert frame["type"] == "mount", frame
    assert opened(frame["html"]) == [False]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_waiters_get_the_components_own_id_not_the_wire_id(routes, monkeypatch):
    from djust import runtime

    seen = []
    original = runtime.ViewRuntime._notify_waiters_safely

    def spy(self, view, event_name, kwargs, **extra):
        seen.append(kwargs.get("component_id"))
        return original(self, view, event_name, kwargs, **extra)

    monkeypatch.setattr(runtime.ViewRuntime, "_notify_waiters_safely", spy)
    key = await sync_to_async(harness.new_session_key)()
    comm, mount = await harness.mount_view(Menus, key)
    wire = ids(mount["html"])[0]
    try:
        frame = await ws_event(comm, "toggle", {"component_id": wire})
        assert frame["type"] != "error", frame
    finally:
        await comm.disconnect()
    assert seen == [wire.split(".")[0]]


@pytest.mark.django_db(transaction=True)
def test_the_manifest_keys_components_by_the_id_the_page_carries(routes):
    from djust._parameter_metadata import parameter_contract_manifest

    request = _request()
    view = _view(request)
    page = [wire_id(view.project_menu)] + [wire_id(m) for m in view.row_menus]
    manifest = parameter_contract_manifest(view)
    keyed = {o["component_id"] for o in manifest["owners"] if o["component_id"]}
    assert set(page) <= keyed
    assert view.project_menu.component_id not in keyed


@pytest.mark.django_db(transaction=True)
def test_a_row_a_handler_adds_back_in_the_same_request_adopts_its_saved_state(routes):
    page = FallbackPage("/rows/")
    old_42 = page.ids[0]
    assert page.event("toggle", 0) == 200  # row 42 open, saved
    ROWS[:] = ["87"]  # mount() of the next request does not build row 42 ...
    assert page.post("set_rows", {"keys": "42,87"}) == 200  # ... its handler does
    assert ids(page.html)[0] == old_42
    assert opened(page.html) == [True, False]


# --------------------------------------------------------------------------- #
# A refused input is a refused event; rows left out are logged
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("value", ["nope", "delete-everything", ""])
def test_a_selection_that_is_not_an_item_is_a_refused_event_on_the_fallback(routes, value):
    page = FallbackPage()
    assert page.event("toggle", 0) == 200
    assert page.event("select", 0, value=value) == 400
    assert page.last == {"error": "Event rejected"}
    # Refused before anything changed, and nothing was saved.
    assert _envelope(page.client.session.session_key)["project_menu"][:2] == [True, ""]
    assert page.event("select", 0, value="edit") == 200


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_selection_that_is_not_an_item_is_a_refused_event_on_the_socket(routes):
    key = await sync_to_async(harness.new_session_key)()
    comm, mount = await harness.mount_view(Menus, key)
    menu = ids(mount["html"])[0]
    try:
        frame = await ws_event(comm, "select", {"component_id": menu, "value": "nope"})
        # The refusal, not the unexpected-error answer of a raising handler.
        assert frame == {"type": "error", "error": "Event rejected"}, frame
        frame = await ws_event(comm, "select", {"component_id": menu, "value": "edit"})
        assert frame["type"] != "error" and picked(frame["html"]) == "edit|"
    finally:
        await comm.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_selection_that_is_not_an_item_is_a_refused_event_over_sse(routes):
    SSE_ERRORS.clear()
    steps = await run_sse([("select", 0, {"value": "nope"}), ("select", 0, {"value": "edit"})])
    assert [s for s, _ in steps] == [400, 200]
    assert SSE_ERRORS == ["Event rejected"]
    assert picked(steps[-1][1]) == "edit|"


# The refusal's own text ("Unknown or disabled dropdown selection") never reaches the client, on
# any transport and for any view, DEBUG or not: it goes to the server log through log_failure_for
# (CodeQL "information exposure through an exception"; tests/test_log_exposure_pin.py). The
# answer is the generic message; the code path does not depend on the exposure policy.
REFUSAL_DETAIL = "Unknown or disabled dropdown selection"


def _logged(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.django_db(transaction=True)
def test_an_explicit_view_never_gets_the_refusal_detail_on_the_fallback_in_debug(routes, caplog):
    caplog.set_level(logging.WARNING)
    with override_settings(DEBUG=True):
        page = FallbackPage()
        assert page.event("select", 0, value="nope") == 400
    assert page.last == {"error": "Event rejected"}
    assert REFUSAL_DETAIL not in str(page.last)
    assert REFUSAL_DETAIL in _logged(caplog)  # the detail is in the server log


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_explicit_view_never_gets_the_refusal_detail_on_the_socket_in_debug(
    routes, caplog
):
    caplog.set_level(logging.WARNING)
    with override_settings(DEBUG=True):
        key = await sync_to_async(harness.new_session_key)()
        comm, mount = await harness.mount_view(Menus, key)
        menu = ids(mount["html"])[0]
        try:
            frame = await ws_event(comm, "select", {"component_id": menu, "value": "nope"})
        finally:
            await comm.disconnect()
    assert frame == {"type": "error", "error": "Event rejected"}, frame
    assert REFUSAL_DETAIL in _logged(caplog)  # the detail is in the server log


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_explicit_view_never_gets_the_refusal_detail_over_sse_in_debug(routes, caplog):
    caplog.set_level(logging.WARNING)
    SSE_ERRORS.clear()
    SSE_ERROR_FRAMES.clear()
    with override_settings(DEBUG=True):
        steps = await run_sse([("select", 0, {"value": "nope"})])
    assert [s for s, _ in steps] == [400]
    assert SSE_ERRORS == ["Event rejected"]
    # Not in a DEBUG field either (the traceback would carry the refusal's text).
    assert SSE_ERROR_FRAMES and all(REFUSAL_DETAIL not in str(f) for f in SSE_ERROR_FRAMES)
    assert all("traceback" not in f for f in SSE_ERROR_FRAMES), SSE_ERROR_FRAMES
    assert REFUSAL_DETAIL in _logged(caplog)  # the detail is in the server log


@pytest.mark.django_db(transaction=True)
def test_rows_that_do_not_fit_the_budget_are_left_out_and_logged_once(routes, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="djust._component_state")
    client = Client()
    for page in range(1, 16):
        assert FallbackPage("/long-paged/?page=%d" % page, client).event("toggle", 0) == 200
    found = [r for r in caplog.records if "were left out" in r.getMessage()]
    assert len(found) == 1, [r.getMessage() for r in caplog.records]
    assert "size limit" in found[0].getMessage()
    assert "x" * 20 not in found[0].getMessage()


@pytest.mark.django_db(transaction=True)
def test_the_merge_keeps_at_most_the_remembered_cap_of_other_rows(routes, caplog):
    import logging

    from djust._component_state import merge_stored_records
    from djust.components._interactive import MAX_REMEMBERED

    caplog.set_level(logging.WARNING, logger="djust._component_state")
    view = _view(_request())
    entry = [0, False, False, "", "", 0]
    stored = {
        "row_menus": {"members": {"o%04d" % i: list(entry) for i in range(MAX_REMEMBERED + 88)}}
    }
    values = {"row_menus": view.row_menus._record()}
    merge_stored_records(view, values, stored)
    members = values["row_menus"]["members"]
    others = [k for k in members if k.startswith("o")]
    assert len(others) == MAX_REMEMBERED
    assert others[0] == "o%04d" % 88  # the oldest went first
    assert {"42", "87"} <= set(members)  # the view's own rows always stay
    merge_stored_records(view, {"row_menus": view.row_menus._record()}, stored)
    assert len([r for r in caplog.records if "were left out" in r.getMessage()]) == 1
