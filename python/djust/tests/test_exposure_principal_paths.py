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
from django.contrib.auth.decorators import login_required as django_login_required
from django.test import Client, RequestFactory
from django.utils.decorators import method_decorator
from django.urls import path

from djust import LiveView, event_handler
from djust.decorators import permission_required, state
from djust.hooks import on_mount
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


class Gates(LiveView):
    """Public and explicit, with a permission-gated handler."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root><b>ran={{ ran }}</b></div>"

    def mount(self, request, **kwargs):
        self.ran = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(ran=self.ran, **kwargs)

    @permission_required("auth.view_group")
    @event_handler()
    def guarded(self, **kwargs):
        self.ran += 1


class LegacyGates(Gates):
    exposure_policy = "legacy"


class PostMethodGated(LiveView):
    """Public page whose own ``post()`` carries ``@method_decorator(login_required)``."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root><b>ran={{ ran }}</b></div>"

    def mount(self, request, **kwargs):
        self.ran = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(ran=self.ran, **kwargs)

    @method_decorator(django_login_required)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    @event_handler()
    def touch(self, **kwargs):
        self.ran += 1


@method_decorator(django_login_required, name="post")
class PostGated(LiveView):
    """Public page whose ``post()`` (so every event) is for signed-in users."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root><b>ran={{ ran }}</b></div>"

    def mount(self, request, **kwargs):
        self.ran = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(ran=self.ran, **kwargs)

    @event_handler()
    def touch(self, **kwargs):
        self.ran += 1


@on_mount
def _signed_in_only(view, request, **kwargs):
    if not getattr(getattr(request, "user", None), "is_authenticated", False):
        return "/login/"
    return None


class HookGated(LiveView):
    """Public and explicit; admitted by an ``on_mount`` hook."""

    exposure_policy = "explicit"
    login_required = False
    on_mount = [_signed_in_only]
    template = "<div dj-root>HOOK_GATED</div>"


class StaffObject(LiveView):
    """Public and explicit; its object is for staff only."""

    exposure_policy = "explicit"
    login_required = False
    template = "<div dj-root>STAFF_OBJECT</div>"

    def get_object(self):
        return {"id": 1}

    def has_object_permission(self, request, obj):
        return bool(getattr(getattr(request, "user", None), "is_staff", False))


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
    path("gates/", Gates.as_view()),
    path("post-gated/", PostGated.as_view()),
    path("post-method-gated/", PostMethodGated.as_view()),
    path("staff-object/", StaffObject.as_view()),
    path("hook-gated/", HookGated.as_view()),
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


# --------------------------------------------------------------------------
# Every gate judges the request as the stack built it
# --------------------------------------------------------------------------


@pytest.mark.parametrize("view", ["Gates", "LegacyGates"])
@pytest.mark.parametrize("stack, granted", [("session-only", False), ("auth(djust)", True)])
async def test_a_permission_gated_handler_follows_the_stack(
    django_user_model, view, stack, granted
):
    """A superuser's session behind a stack that authenticated nobody does not
    satisfy ``@permission_required``, on an explicit view as on a legacy one."""
    browser = await _logged_in(django_user_model)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        mounted = await ws.answer(_mount_frame(MODULE + "." + view, "/gates/"), *_MOUNT_ANSWERS)
        assert mounted.get("type") == "mount", mounted
        answered = await ws.answer(_event("guarded"), *_EVENT_ANSWERS)
    if granted:
        assert "ran=1" in json.dumps(answered), answered
    else:
        assert answered.get("type") in ("error", "permission_denied"), answered
        assert "ran=1" not in json.dumps(answered)


async def _touch(django_user_model, view, url, stack) -> bool:
    """Whether a logged-in browser's ``touch`` event runs on ``view`` behind ``stack``."""
    browser = await _logged_in(django_user_model)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        mounted = await ws.answer(_mount_frame(MODULE + "." + view, url), *_MOUNT_ANSWERS)
        assert mounted.get("type") == "mount", mounted
        answered = await ws.answer(_event("touch"), *_EVENT_ANSWERS)
    return "ran=1" in json.dumps(answered)


_POST_GATED = [("PostGated", "/post-gated/"), ("PostMethodGated", "/post-method-gated/")]


@pytest.mark.parametrize("view, url", _POST_GATED)
@pytest.mark.parametrize("stack, granted", [("session-only", False), ("auth(djust)", True)])
async def test_access_control_on_post_follows_the_stack(
    django_user_model, view, url, stack, granted
):
    """``login_required`` on ``post()`` gates every event of a public page.

    Behind a stack that authenticated nobody the event is refused although the
    session is logged in; behind an auth stack it runs.
    """
    assert await _touch(django_user_model, view, url, stack) is granted


@pytest.mark.parametrize("view, url", _POST_GATED)
async def test_the_post_gate_is_what_refuses_that_event(django_user_model, monkeypatch, view, url):
    """The gate-off of the test above, kept in the suite.

    With the wrapper made the identity, the same event behind the session-only
    stack is granted to the user read from the session. So the refusal above
    comes from judging the request as the stack built it, not from anything
    else on the path.
    """
    from djust.auth import core

    monkeypatch.setattr(core, "request_as_the_stack_built_it", lambda request: request)
    assert await _touch(django_user_model, view, url, "session-only") is True


@pytest.mark.parametrize("stack, granted", [("session-only", False), ("auth(djust)", True)])
async def test_has_object_permission_follows_the_stack(django_user_model, stack, granted):
    browser = await _logged_in(django_user_model)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        frame = await ws.answer(
            _mount_frame(MODULE + ".StaffObject", "/staff-object/"), *_MOUNT_ANSWERS
        )
    if granted:
        assert frame.get("type") == "mount" and "STAFF_OBJECT" in frame["html"], frame
    else:
        assert frame.get("type") != "mount" and "STAFF_OBJECT" not in json.dumps(frame), frame


@pytest.mark.parametrize("stack, granted", [("session-only", False), ("auth(djust)", True)])
async def test_an_on_mount_hook_follows_the_stack(django_user_model, stack, granted):
    browser = await _logged_in(django_user_model)
    async with _StackSocket(stack, _session_cookie(browser)) as ws:
        frame = await ws.answer(
            _mount_frame(MODULE + ".HookGated", "/hook-gated/"), *_MOUNT_ANSWERS
        )
    assert (frame.get("type") == "mount") is granted, frame


async def test_a_permission_gated_handler_is_denied_over_sse_without_auth_middleware(
    django_user_model,
):
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    browser = await _logged_in(django_user_model)
    key = await sync_to_async(lambda: browser.session.session_key)()
    sid = str(uuid.uuid4())
    stream = RequestFactory().get(
        "/djust/sse/%s/" % sid, data={"view": MODULE + ".Gates", "_djust_url": "/gates/"}
    )
    stream.session = SessionStore(key)
    assert (await DjustSSEStreamView().get(stream, session_id=sid)).status_code == 200
    session = _sse_sessions[sid]
    try:
        post = RequestFactory().post(
            "/djust/sse/%s/message/" % sid,
            data=json.dumps({"type": "event", "event": "guarded", "params": {}}),
            content_type="application/json",
        )
        post.session = SessionStore(key)
        await DjustSSEMessageView().post(post, session_id=sid)
        assert session.view_instance.ran == 0
    finally:
        _sse_sessions.pop(sid, None)


#: The gates of ``djust.auth.core`` and how each comes to judge the request as
#: the stack built it: by calling the wrapper itself, or by going through a
#: gate that does. A new function there that takes a request and decides
#: access has to be added here, which is the point.
_CALLS_THE_WRAPPER = {
    "check_view_auth",
    "acheck_view_auth",
    "check_event_auth",
    "check_handler_permission",
    "_resolve_object_permission",
}
_GOES_THROUGH = {
    "check_view_auth_lightweight": "check_view_auth",
    "acheck_view_auth_lightweight": "acheck_view_auth",
    "run_pre_mount_auth": "check_view_auth",
    "arun_pre_mount_auth": "acheck_view_auth",
    "check_object_permission": "_resolve_object_permission",
    "check_object_permission_for_event": "check_object_permission",
    "enforce_object_permission": "check_object_permission",
}


def test_every_gate_judges_the_request_as_the_stack_built_it():
    import ast
    from pathlib import Path

    from djust.auth import core

    tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def calls(name):
        return {
            n.func.id
            for n in ast.walk(functions[name])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        } | {n.id for n in ast.walk(functions[name]) if isinstance(n, ast.Name)}

    gates = {
        name
        for name, node in functions.items()
        if "request" in {a.arg for a in node.args.args}
        and name.lstrip("_").split("_")[0]
        in {"check", "acheck", "enforce", "run", "arun", "resolve"}
        and name not in {"request_as_the_stack_built_it"}
    }
    # Step functions of check_view_auth: they receive the request it already wrapped.
    inner = {
        "_check_view_auth",
        "_check_login_and_permission",
        "_check_after_custom",
        "_check_django_access_mixins",
    }
    assert gates - inner == _CALLS_THE_WRAPPER | set(_GOES_THROUGH), sorted(gates - inner)
    for name in _CALLS_THE_WRAPPER:
        assert "request_as_the_stack_built_it" in calls(name), name
    for name, through in _GOES_THROUGH.items():
        assert through in calls(name), (name, through)
