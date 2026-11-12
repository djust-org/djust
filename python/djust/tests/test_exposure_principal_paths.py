"""One principal for an explicit view's mount and its events, on every mount path.

A socket (or HTTP) request has a ``user`` only when the stack in front of it
supplied one. Behind a session-only stack there is none, and an explicit mount
reads it from the session, as its events do. ``test_exposure_framework_views``
covers the plain mount in four stack configurations; this file covers the
other ways a view comes to be mounted, in the configuration where the two
could disagree: a session-only stack and a browser whose session is logged in.

It also pins the rule for access: a ``login_required`` view is judged against
the user the stack supplied (none, here), wherever the check runs. A child
that reuses its public parent's request is refused as a root would be.
"""

from __future__ import annotations

import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory
from django.urls import path

from djust import LiveView, event_handler
from djust.decorators import state
from djust.tests.test_admin_page_render import _session_cookie
from djust.tests.test_exposure_framework_views import (
    _EVENT_ANSWERS,
    _MOUNT_ANSWERS,
    _StackSocket,
    _event,
    _text_patches,
    _who,
)

MODULE = __name__
URL = "/who/"


class Who(LiveView):
    """Public, explicit; renders who ``mount()`` saw and who the last event saw."""

    exposure_policy = "explicit"
    login_required = False
    template = (
        "<div dj-root><i>mount={{ mount_who }}</i><b>event={{ event_who }}</b>"
        "<u>count={{ count }}</u></div>"
    )
    count = state(0, persist="server")
    theme = state("light", persist="client", client=True)

    def mount(self, request, **kwargs):
        self.mount_who = _who(request)
        self.event_who = "-"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            mount_who=self.mount_who, event_who=self.event_who, count=self.count, **kwargs
        )

    @event_handler()
    def ask(self, **kwargs):
        self.event_who = _who(self.request)
        self.count += 1

    @event_handler()
    def go(self, **kwargs):
        self.live_redirect("/who-next/")


class WhoNext(Who):
    count = state(0, persist="server")
    theme = state("light", persist="client", client=True)


class Plain(LiveView):
    """Public, explicit, nothing persisted: needs no session binding over HTTP."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root><i>mount={{ mount_who }}</i><b>event={{ event_who }}</b></div>"

    def mount(self, request, **kwargs):
        self.mount_who = _who(request)
        self.event_who = "-"

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            mount_who=self.mount_who, event_who=self.event_who, **kwargs
        )

    @event_handler()
    def ask(self, **kwargs):
        self.event_who = _who(self.request)


class GatedChild(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<p>GATED_CHILD_CONTENT</p>"


class StickyGatedChild(GatedChild):
    sticky = True
    sticky_id = "gated"


def _parent(name, tag):
    return type(
        name,
        (LiveView,),
        {
            "__module__": MODULE,
            "exposure_policy": "explicit",
            "login_required": False,
            "template": "<div dj-root>{% load live_tags %}<h1>PARENT</h1>" + tag + "</div>",
        },
    )


EmbeddingParent = _parent("EmbeddingParent", '{%% live_render "%s.GatedChild" %%}' % MODULE)
StickyParent = _parent(
    "StickyParent", '{%% live_render "%s.StickyGatedChild" sticky=True %%}' % MODULE
)

urlpatterns = [
    path("who/", Who.as_view()),
    path("who-next/", WhoNext.as_view()),
    path("plain/", Plain.as_view()),
    path("parent/", EmbeddingParent.as_view()),
    path("sticky-parent/", StickyParent.as_view()),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    from djust.routing import _reset_route_map_cache

    settings.ROOT_URLCONF = MODULE
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MODULE]
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE]
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


async def _logged_in(django_user_model) -> Client:
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)
    return browser


def _mount_frame(view=MODULE + ".Who", url=URL, **extra):
    return {"type": "mount", "view": view, "url": url, "params": {}, **extra}


async def _ask(ws) -> str:
    """Fire ``ask`` and return what the answer rendered (a patch or fresh HTML)."""
    answered = await ws.answer(_event("ask"), *_EVENT_ANSWERS)
    assert answered.get("type") in ("patch", "html_update"), answered
    return " ".join(_text_patches(answered)) + " " + str(answered.get("html", ""))


pytestmark = pytest.mark.django_db(transaction=True)


# --------------------------------------------------------------------------
# The mount paths, behind the session-only stack with a logged-in session
# --------------------------------------------------------------------------


async def test_mount_batch(django_user_model):
    browser = await _logged_in(django_user_model)
    async with _StackSocket("session-only", _session_cookie(browser)) as ws:
        batch = await ws.answer(
            {
                "type": "mount_batch",
                "views": [{"view": MODULE + ".Who", "url": URL, "target_id": "t1"}],
            },
            "mount_batch",
            "error",
        )
        assert batch.get("type") == "mount_batch" and batch["failed"] == [], batch
        assert ">mount=root</i>" in batch["views"][0]["html"], batch
        assert "event=root" in await _ask(ws)


async def test_live_redirect_remount(django_user_model):
    browser = await _logged_in(django_user_model)
    async with _StackSocket("session-only", _session_cookie(browser)) as ws:
        mounted = await ws.answer(_mount_frame(), *_MOUNT_ANSWERS)
        assert ">mount=root</i>" in mounted["html"], mounted
        again = await ws.answer(
            {"type": "live_redirect_mount", "view": MODULE + ".WhoNext", "url": "/who-next/"},
            *_MOUNT_ANSWERS,
        )
        assert again.get("type") == "mount" and again["view"].endswith(".WhoNext"), again
        assert ">mount=root</i>" in again["html"], again
        assert "event=root" in await _ask(ws)


async def test_reconnect_restores_and_answers(django_user_model):
    browser = await _logged_in(django_user_model)
    cookie = _session_cookie(browser)
    async with _StackSocket("session-only", cookie) as ws:
        await ws.answer(_mount_frame(), *_MOUNT_ANSWERS)
        first = await _ask(ws)
        assert "event=root" in first and "count=1" in first
    async with _StackSocket("session-only", cookie) as ws:
        restored = await ws.answer(_mount_frame(), *_MOUNT_ANSWERS)
        assert restored.get("type") == "mount", restored
        # The state the first connection saved, read back under the same principal.
        assert ">mount=root</i>" in restored["html"] and ">count=1</u>" in restored["html"]
        second = await _ask(ws)
        assert "event=root" in second and "count=2" in second


async def test_mount_with_a_signed_client_state_token(django_user_model):
    browser = await _logged_in(django_user_model)
    cookie = _session_cookie(browser)
    async with _StackSocket("session-only", cookie) as ws:
        mounted = await ws.answer(_mount_frame(), *_MOUNT_ANSWERS)
        token = mounted.get("state_snapshot_signed")
        assert token, mounted
    async with _StackSocket("session-only", cookie) as ws:
        restored = await ws.answer(
            _mount_frame(
                has_prerendered=True,
                state_snapshot={"view_slug": MODULE + ".Who", "state_json": token},
            ),
            *_MOUNT_ANSWERS,
        )
        # The token is bound to the principal that minted it; it is accepted.
        assert restored.get("type") == "mount", restored
        assert "event=root" in await _ask(ws)


# --------------------------------------------------------------------------
# HTTP without AuthenticationMiddleware: the request has no user at all
# --------------------------------------------------------------------------


def _http(method, url, session_key, event=None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url)
    else:
        request = factory.post(
            url, data="{}", content_type="application/json", HTTP_X_DJUST_EVENT=event
        )
    request.session = SessionStore(session_key)  # session middleware only: no request.user
    return request


def test_http_fallback_mount_and_event_agree(django_user_model):
    """The page GET and the fallback POST are both judged as the stack built them.

    Nothing reads a user for an HTTP request, so both see none. That is
    ``main``'s behaviour, and the two agree.
    """
    django_user_model.objects.create_superuser("root", password="pw")
    browser = Client()
    browser.force_login(django_user_model.objects.get(username="root"))
    key = browser.session.session_key
    page = Plain.as_view()(_http("GET", "/plain/", key))
    assert page.status_code == 200 and ">mount=none</i>" in page.content.decode()
    answer = Plain.as_view()(_http("POST", "/plain/", key, event="ask"))
    assert answer.status_code == 200, answer.content[:300]
    assert "event=none" in answer.content.decode()


def test_http_page_with_persisted_state_still_needs_a_user(django_user_model):
    """Unchanged from ``main``: a persisted field binds the HTTP page to
    ``request.user``, and a project without AuthenticationMiddleware has none."""
    django_user_model.objects.create_superuser("root", password="pw")
    browser = Client()
    browser.force_login(django_user_model.objects.get(username="root"))
    page = Who.as_view()(_http("GET", URL, browser.session.session_key))
    assert page.status_code == 500


# --------------------------------------------------------------------------
# SSE without AuthenticationMiddleware
# --------------------------------------------------------------------------


@pytest.mark.parametrize("logged_in, expected", [(True, "root"), (False, "anonymous")])
async def test_sse_mount_and_event_post_agree(django_user_model, logged_in, expected):
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    if logged_in:
        browser = await _logged_in(django_user_model)
        key = await sync_to_async(lambda: browser.session.session_key)()
    else:
        store = SessionStore()
        await sync_to_async(store.create)()
        key = store.session_key
    sid = str(uuid.uuid4())
    stream = RequestFactory().get(
        "/djust/sse/%s/" % sid, data={"view": MODULE + ".Who", "_djust_url": URL}
    )
    stream.session = SessionStore(key)  # no request.user
    response = await DjustSSEStreamView().get(stream, session_id=sid)
    assert response.status_code == 200
    session = _sse_sessions[sid]
    try:
        frames = []
        while not session.queue.empty():
            frames.append(session.queue.get_nowait())
        mount = next(f for f in frames if f and f.get("type") == "mount")
        assert ">mount=%s</i>" % expected in mount["html"], mount
        post = RequestFactory().post(
            "/djust/sse/%s/message/" % sid,
            data=json.dumps({"type": "event", "event": "ask", "params": {}}),
            content_type="application/json",
        )
        post.session = SessionStore(key)  # no request.user either
        answer = await DjustSSEMessageView().post(post, session_id=sid)
        assert answer.status_code == 200, answer.content[:300]
        frames = []
        while not session.queue.empty():
            frames.append(session.queue.get_nowait())
        assert "event=%s" % expected in json.dumps(frames), frames
    finally:
        _sse_sessions.pop(sid, None)


# --------------------------------------------------------------------------
# A gated child follows the root rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "parent, url", [("EmbeddingParent", "/parent/"), ("StickyParent", "/sticky-parent/")]
)
@pytest.mark.parametrize("stack, admitted", [("session-only", False), ("auth(djust)", True)])
async def test_a_login_required_child_is_judged_as_a_root_would_be(
    django_user_model, parent, url, stack, admitted
):
    browser = await _logged_in(django_user_model)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        frame = await ws.answer(_mount_frame(MODULE + "." + parent, url), *_MOUNT_ANSWERS)
    rendered = json.dumps(frame)
    if admitted:
        assert frame.get("type") == "mount" and "GATED_CHILD_CONTENT" in rendered, frame
    else:
        # The stack authenticated nobody, so the gated child is refused, even
        # though its public parent's request carries the session's user.
        assert "GATED_CHILD_CONTENT" not in rendered, frame


@pytest.mark.parametrize("child", ["GatedChild", "StickyGatedChild"])
async def test_the_same_child_as_a_root_is_refused_behind_the_session_only_stack(
    django_user_model, child
):
    browser = await _logged_in(django_user_model)
    async with _StackSocket("session-only", _session_cookie(browser)) as ws:
        frame = await ws.answer(_mount_frame(MODULE + "." + child, URL), *_MOUNT_ANSWERS)
    assert frame.get("type") != "mount", frame
