"""``reauth_on_event`` re-checks the page's path and query on every transport (#4255).

With ``LIVEVIEW_CONFIG['reauth_on_event']`` on, a legacy-exposure view that
sets ``login_required`` / ``permission_required`` has its auth re-run before
each client event (#1777). The re-check must see the same route the mount was
authorized against:

- WebSocket: ``WSConsumerTransport.recheck_event_auth`` re-checks ``view.request``,
  the mount request (page path, page query from the mount params) with a
  freshly read user.
- HTTP fallback: the event is a POST to the page URL itself (page path + query).
- SSE: ``SSESessionTransport.recheck_event_auth`` re-checked the raw
  ``/djust/sse/<id>/message/`` POST: no page path, no page query. A
  ``check_permissions`` that reads ``request.GET`` or ``request.path`` saw
  different input than at mount, and denied (or allowed) a turn the other
  transports decide the other way.

Each transport mounts ``/o/reauth4255/?org=acme`` as a signed-in user and sends
one event; ``check_permissions`` records the path and ``org`` it was shown and
admits only the page path with ``org=acme``. The three must agree.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest
from asgiref.sync import sync_to_async
from django.test import Client, RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler

pytestmark = [pytest.mark.django_db(transaction=True)]

CHECKS: list[tuple[str, str]] = []
RAN: list[str] = []

_USERS = itertools.count()

PATH = "/o/reauth4255/"
URL = PATH + "?org=acme"


class OrgReauthView(LiveView):
    exposure_policy = "legacy"  # reauth_on_event re-checks legacy views only
    login_required = True
    template = "<div dj-root><p>{{ count }}</p></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def check_permissions(self, request):
        org = request.GET.get("org", "none")
        CHECKS.append((request.path, org))
        # A permission revoked mid-session rides on the live request's user.
        if getattr(request.user, "revoked4255", False):
            return False
        return request.path == PATH and org == "acme"

    @event_handler
    def bump(self, **kwargs):
        RAN.append(self.request.GET.get("org", "none"))
        self.count += 1


VIEW = "%s.OrgReauthView" % __name__
ORIGIN = "http://testserver"
urlpatterns = [path("o/reauth4255/", OrgReauthView.as_view())]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
    "LIVEVIEW_CONFIG": {"reauth_on_event": True},
}

#: What the event's own auth check sees, and that the handler ran.
EXPECTED = ((PATH, "acme"), ["acme"])


@pytest.fixture(autouse=True)
def _routes():
    CHECKS.clear()
    RAN.clear()
    with override_settings(**SETTINGS):
        config.reset()
        yield
    config.reset()
    CHECKS.clear()
    RAN.clear()


def _page() -> tuple[Client, str, object]:
    """A signed-in page GET: its client, the tab id, and the user."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("reauth4255-%d" % next(_USERS), password="x")
    client = Client()
    client.force_login(user)
    response = client.get(URL)
    assert response.status_code == 200, response.content[:300]
    tab = re.search(r'<meta name="djust-tab" content="([^"]+)">', response.content.decode())
    assert tab, "the page names its tab"
    return client, tab.group(1), user


def _result() -> tuple[object, list[str]]:
    """The last auth check (the event's) and the handler runs."""
    return (CHECKS[-1] if CHECKS else None), list(RAN)


def _fallback():
    client, tab, _user = _page()
    CHECKS.clear()
    response = client.post(
        URL,
        data="{}",
        content_type="application/json",
        HTTP_X_DJUST_EVENT="bump",
        HTTP_X_DJUST_TAB=tab,
    )
    assert response.status_code == 200, response.content[:300]
    return _result()


async def _socket():
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    client, tab, user = await sync_to_async(_page)()
    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", ORIGIN.encode())],
    )
    comm.scope["session"] = await sync_to_async(lambda: client.session)()
    comm.scope["user"] = user
    connected, _ = await comm.connect()
    assert connected
    try:
        await comm.send_json_to(
            {
                "type": "mount",
                "view": VIEW,
                # The client sends the pathname and the page query as params.
                "url": PATH,
                "params": {"org": "acme"},
                "tab": tab,
                "has_prerendered": True,
            }
        )
        for _ in range(20):
            frame = json.loads(await comm.receive_from(timeout=30))
            if frame.get("type") in ("mount", "error", "navigate"):
                break
        assert frame["type"] == "mount", frame
        CHECKS.clear()
        await comm.send_json_to({"type": "event", "event": "bump", "params": {}})
        for _ in range(20):
            frame = json.loads(await comm.receive_from(timeout=30))
            if frame.get("type") in ("patch", "html_update", "noop", "error", "navigate"):
                break
        assert frame["type"] not in ("error", "navigate"), frame
    finally:
        await comm.disconnect()
    return _result()


async def _sse(message_query: str = ""):
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    client, tab, user = await sync_to_async(_page)()
    session_store = await sync_to_async(lambda: client.session)()
    sid = "42554255-4255-4255-4255-425542554255"
    _sse_sessions.pop(sid, None)
    stream = RequestFactory().get(
        "/djust/sse/%s/" % sid,
        # The client's stream URL: the pathname plus the page query (03b-sse.js).
        {"view": VIEW, "_djust_url": PATH, "_djust_tab": tab, "org": "acme"},
        HTTP_ORIGIN=ORIGIN,
        HTTP_HOST="testserver",
    )
    stream.session = session_store
    stream.user = user
    await DjustSSEStreamView().get(stream, session_id=sid)
    session = _sse_sessions.get(sid)
    try:
        assert session is not None and session.runtime.view_instance is not None, "SSE mount failed"
        while not session.queue.empty():
            session.queue.get_nowait()
        CHECKS.clear()
        post = RequestFactory().post(
            "/djust/sse/%s/message/%s" % (sid, message_query),
            data=json.dumps({"type": "event", "event": "bump", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_HOST="testserver",
        )
        post.session = session_store
        post.user = user
        response = await DjustSSEMessageView().post(post, session_id=sid)
        assert response.status_code == 200, response.content[:300]
        frames = []
        while not session.queue.empty():
            frame = session.queue.get_nowait()
            if frame is not None:
                frames.append(frame)
        errors = [f for f in frames if isinstance(f, dict) and f.get("type") == "error"]
        assert not errors, errors
        # The live POST and the stored mount request keep their own route.
        assert post.path == "/djust/sse/%s/message/" % sid
        assert post.META.get("QUERY_STRING", "") == message_query.lstrip("?")
        assert session._request.GET.get("org") == "acme"
    finally:
        session = _sse_sessions.pop(sid, None)
        if session is not None:
            session.shutdown()
    return _result()


def test_the_fallback_recheck_sees_the_page_route():
    assert _fallback() == EXPECTED


@pytest.mark.asyncio
async def test_the_socket_recheck_sees_the_page_route():
    assert await _socket() == EXPECTED


@pytest.mark.asyncio
async def test_the_sse_recheck_sees_the_mount_route():
    assert await _sse() == EXPECTED


@pytest.mark.asyncio
async def test_every_transport_rechecks_the_same_route():
    results = {}
    for name, run in (
        ("fallback", sync_to_async(_fallback)),
        ("socket", _socket),
        ("sse", _sse),
    ):
        CHECKS.clear()
        RAN.clear()
        try:
            results[name] = await run()
        except AssertionError as exc:
            results[name] = "refused: %s; checks=%r" % (str(exc)[:120], list(CHECKS))
    assert len({repr(r) for r in results.values()}) == 1, results


@pytest.mark.asyncio
async def test_the_sse_message_post_query_cannot_steer_the_recheck():
    """Only the mount's query counts; one appended to the message POST is ignored."""
    assert await _sse(message_query="?org=attacker") == EXPECTED


# --------------------------------------------------------------------------- #
# ``SSESessionTransport.turn_request``: the request a mount turn's middleware
# reads. A page mount over the stream GET gets the page request; a
# ``live_redirect_mount`` POST got the raw ``/message/`` POST instead of the
# destination's page request.
# --------------------------------------------------------------------------- #

MOUNT_TURNS: list[tuple[str, str]] = []


async def record_mount_turns(turn, next):
    """A ``LIVEVIEW_MIDDLEWARE`` entry: the route a mount turn's request names."""
    result = await next(turn)
    if turn.kind == "mount" and turn.transport == "sse" and turn.request is not None:
        MOUNT_TURNS.append((turn.request.path, turn.request.GET.get("org", "none")))
    return result


@pytest.mark.asyncio
async def test_the_sse_navigation_mount_turn_names_the_destination_route():
    from djust import turns
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    MOUNT_TURNS.clear()
    with override_settings(
        LIVEVIEW_MIDDLEWARE=["%s.record_mount_turns" % __name__],
        LIVEVIEW_CONFIG={"reauth_on_event": False},
    ):
        config.reset()
        turns._chain = None
        try:
            client, tab, user = await sync_to_async(_page)()
            session_store = await sync_to_async(lambda: client.session)()
            sid = "42554255-4255-4255-4255-4255425542ff"
            _sse_sessions.pop(sid, None)
            stream = RequestFactory().get(
                "/djust/sse/%s/" % sid,
                {"view": VIEW, "_djust_url": PATH, "_djust_tab": tab, "org": "acme"},
                HTTP_ORIGIN=ORIGIN,
                HTTP_HOST="testserver",
            )
            stream.session = session_store
            stream.user = user
            await DjustSSEStreamView().get(stream, session_id=sid)
            session = _sse_sessions.get(sid)
            try:
                assert session is not None and session.runtime.view_instance is not None
                post = RequestFactory().post(
                    "/djust/sse/%s/message/" % sid,
                    data=json.dumps(
                        {
                            "type": "live_redirect_mount",
                            "view": VIEW,
                            "url": PATH,
                            "params": {"org": "acme"},
                        }
                    ),
                    content_type="application/json",
                    HTTP_ORIGIN=ORIGIN,
                    HTTP_HOST="testserver",
                )
                post.session = session_store
                post.user = user
                response = await DjustSSEMessageView().post(post, session_id=sid)
                assert response.status_code == 200, response.content[:300]
                assert session.runtime.view_instance is not None, "navigation mount failed"
            finally:
                session = _sse_sessions.pop(sid, None)
                if session is not None:
                    session.shutdown()
        finally:
            turns._chain = None
            config.reset()
    # The stream mount, then the navigation mount: both name the page route.
    assert MOUNT_TURNS == [(PATH, "acme"), (PATH, "acme")]


# --------------------------------------------------------------------------- #
# The route comes from the mount; the principal never does.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_revoked_sse_principal_is_still_denied_with_the_mount_route():
    """The POST's user (same pk, permission revoked) is re-checked, not the mount's."""
    from copy import copy

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    client, tab, user = await sync_to_async(_page)()
    session_store = await sync_to_async(lambda: client.session)()
    sid = "42554255-4255-4255-4255-4255425542aa"
    _sse_sessions.pop(sid, None)
    stream = RequestFactory().get(
        "/djust/sse/%s/" % sid,
        {"view": VIEW, "_djust_url": PATH, "_djust_tab": tab, "org": "acme"},
        HTTP_ORIGIN=ORIGIN,
        HTTP_HOST="testserver",
    )
    stream.session = session_store
    stream.user = user
    await DjustSSEStreamView().get(stream, session_id=sid)
    session = _sse_sessions.get(sid)
    try:
        assert session is not None and session.runtime.view_instance is not None
        while not session.queue.empty():
            session.queue.get_nowait()
        CHECKS.clear()
        post = RequestFactory().post(
            "/djust/sse/%s/message/" % sid,
            data=json.dumps({"type": "event", "event": "bump", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_HOST="testserver",
        )
        post.session = session_store
        revoked = copy(user)
        revoked.revoked4255 = True
        post.user = revoked
        await DjustSSEMessageView().post(post, session_id=sid)
        frames = []
        while not session.queue.empty():
            frame = session.queue.get_nowait()
            if frame is not None:
                frames.append(frame)
    finally:
        session = _sse_sessions.pop(sid, None)
        if session is not None:
            session.shutdown()
    assert CHECKS == [(PATH, "acme")], "the re-check ran with the mount route"
    assert RAN == [], "a revoked POSTer must not drive the view"
    assert any(isinstance(f, dict) and f.get("code") == "permission_denied" for f in frames) or any(
        "permission_denied" in str(f) for f in frames
    ), frames


def test_the_mount_route_is_copied_in_one_place():
    """``apply_mount_route`` is the only copy of the route; both SSE auth paths call it."""
    import inspect
    from pathlib import Path

    from djust import _exposure_auth, runtime

    pkg = Path(_exposure_auth.__file__).parent
    owners = [
        p.relative_to(pkg).as_posix()
        for p in pkg.rglob("*.py")
        if "tests" not in p.parts and '"QUERY_STRING": mounted.META' in p.read_text()
    ]
    assert owners == ["_exposure_auth.py"], owners
    assert "apply_mount_route(" in inspect.getsource(_exposure_auth._event_request)
    recheck = inspect.getsource(runtime.SSESessionTransport.recheck_event_auth)
    assert "apply_mount_route(request, mounted)" in recheck
