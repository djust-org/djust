"""#3725: an overriding ``get_context_data()`` runs ONCE per HTTP GET.

The GET evaluates the view's context once (``RequestMixin._get_turn_body``) and
caches it on ``self._cached_context`` so the render that follows does not
evaluate it again. The cache short-circuit lives inside the stock
``get_context_data()``, so a view that OVERRIDES it (with or without
``super()``) bypassed the cache: the override ran a second time from
``_sync_state_to_rust()`` -- queries, API calls and side effects of the override
happened twice on first paint, and a query budget read double.

The render path now takes the context the request path already computed
(``ContextMixin._context_for_render``), for both exposure policies.

What is pinned here, each by running the real thing:

* the call count per transport (Django test client GET; ``WebsocketCommunicator``
  mount; SSE mount), for legacy and explicit views, with and without ``super()``,
  inline template and ``{% extends %}`` page shell;
* that the HTML carries the value computed by THE one call (``stamp``), not a
  second pass's;
* the stale-context hazard: the cache is cleared at the end of the GET, and
  a later render on the same instance (what an event does) evaluates the
  override again and sees the new state; a WebSocket event re-renders with
  fresh context, once;
* the caller set: no render sink evaluates ``get_context_data`` directly.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView, event_handler, sse
from djust.decorators import state
from djust.sse import DjustSSEStreamView, _sse_sessions
from djust.utils import clear_template_dirs_cache
from djust.websocket import LiveViewConsumer

from ._ws_frames import has_type, receive_settled

MOD = __name__
CALLS: list = []
INSTANCES: list = []


class _Base(LiveView):
    login_required = False

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


def _stamp(view) -> int:
    """Record one evaluation; the value is how the HTML names which call it used."""
    CALLS.append(type(view).__name__)
    INSTANCES.append(view)
    return 100 + len(CALLS)


def _make(name: str, policy: str, use_super: bool, shell: bool):
    if shell:
        tpl = {"template_name": "t3725/page.html"}
    else:
        tpl = {
            "template": '<div dj-root dj-view="' + MOD + "." + name + '">{{ n }}-{{ stamp }}</div>'
        }
    body: dict = {"exposure_policy": policy, **tpl}
    if policy == "explicit":
        body["n"] = state(1, persist="server")

        def mount(self, request, **kwargs):
            pass

        if use_super:

            def get_context_data(self, **kwargs):
                return super(cls, self).get_context_data(n=self.n, stamp=_stamp(self), **kwargs)
        else:

            def get_context_data(self, **kwargs):
                return {"n": self.n, "stamp": _stamp(self)}
    else:

        def mount(self, request, **kwargs):
            self.n = 1

        if use_super:

            def get_context_data(self, **kwargs):
                ctx = super(cls, self).get_context_data(**kwargs)
                ctx["stamp"] = _stamp(self)
                return ctx
        else:

            def get_context_data(self, **kwargs):
                return {"n": self.n, "stamp": _stamp(self)}

    body["mount"] = mount
    body["get_context_data"] = get_context_data
    cls = type(name, (_Base,), body)
    globals()[name] = cls
    return cls


def _name(policy: str, use_super: bool, shell: bool) -> str:
    return f"V_{policy}_{'super' if use_super else 'nosuper'}_{'shell' if shell else 'inline'}"


VIEWS: dict = {}
for _policy in ("legacy", "explicit"):
    for _super in (True, False):
        for _shell in (False, True):
            _n = _name(_policy, _super, _shell)
            VIEWS[_n] = _make(_n, _policy, _super, _shell)

INLINE = [n for n in VIEWS if n.endswith("_inline")]
ALL = list(VIEWS)

urlpatterns = [path(n.lower() + "/", v.as_view()) for n, v in VIEWS.items()]


class _Plain(LiveView):
    """A view with NO override, for the unchanged-baseline check."""

    login_required = False
    exposure_policy = "legacy"
    template = '<div dj-root dj-view="' + MOD + '._Plain">{{ n }}</div>'

    def mount(self, request, **kwargs):
        self.n = 1


urlpatterns.append(path("plain/", _Plain.as_view()))


class _ModelShell(LiveView):
    """An override that returns a FRESH dict holding a raw model, rendered in a page shell.

    ``{{ u.groups.count }}`` is a reverse manager: it resolves only from the raw
    model, which the page shell reads from the GET's cached context. The render
    adds keys to the dict it is handed and replaces models in it, so handing it
    the cache itself would have turned that model into a serialized dict and
    this into an empty string.
    """

    login_required = False
    exposure_policy = "legacy"
    template_name = "t3725/model_page.html"

    def mount(self, request, **kwargs):
        from django.contrib.auth.models import User

        self.u = User.objects.get(username="u3725")

    def get_context_data(self, **kwargs):
        return {"u": self.u, "stamp": _stamp(self)}


urlpatterns.append(path("modelshell/", _ModelShell.as_view()))


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    d = tmp_path / "templates" / "t3725"
    d.mkdir(parents=True)
    (d / "base.html").write_text(
        "<!DOCTYPE html><html><body>shell=[{{ stamp }}]{% block content %}{% endblock %}"
        "</body></html>"
    )
    (d / "page.html").write_text(
        '{% extends "t3725/base.html" %}{% block content %}<div dj-root>{{ n }}-{{ stamp }}</div>'
        "{% endblock %}"
    )
    (d / "model_page.html").write_text(
        '{% extends "t3725/base.html" %}{% block content %}shell-groups=[{{ u.groups.count }}]'
        "<div dj-root>root-groups=[{{ u.groups.count }}]</div>{% endblock %}"
    )
    CALLS.clear()
    INSTANCES.clear()
    _sse_sessions.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    ):
        clear_template_dirs_cache()
        yield
    clear_template_dirs_cache()
    _sse_sessions.clear()


# --------------------------------------------------------------------------- #
# HTTP GET
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
@pytest.mark.parametrize("name", ALL)
def test_an_overriding_get_context_data_runs_once_per_get(name):
    response = Client().get("/" + name.lower() + "/")
    assert response.status_code == 200, response.content[:300]
    assert len(CALLS) == 1, f"{name}: the override ran {len(CALLS)} times on one GET"


@pytest.mark.django_db
@pytest.mark.parametrize("name", ALL)
def test_the_page_carries_the_value_the_one_call_computed(name):
    """The render took call 1's context; it did not compute its own second one."""
    html = Client().get("/" + name.lower() + "/").content.decode()
    assert "1-101" in html, html
    assert "102" not in html, "a second evaluation of get_context_data reached the page"
    if name.endswith("_shell"):
        assert "shell=[101]" in html, html


@pytest.mark.django_db
def test_a_view_without_an_override_is_unchanged():
    """The cache short-circuit already served it; the count and the page stay the same."""
    response = Client().get("/plain/")
    assert response.status_code == 200
    assert ">1</div>" in response.content.decode()


@pytest.mark.django_db
def test_the_render_does_not_write_into_the_cache_the_shell_reads_raw_values_from():
    """The page shell's sidecar reads RAW models from the cache (#2589).

    Handing the render the cached dict itself let it replace the model with its
    serialized form, and the shell lost ``{{ u.groups.count }}``.
    """
    from django.contrib.auth.models import Group, User

    user = User.objects.create(username="u3725")
    user.groups.add(Group.objects.create(name="g3725"))
    html = Client().get("/modelshell/").content.decode()
    assert len(CALLS) == 1, f"the override ran {len(CALLS)} times"
    assert "root-groups=[1]" in html, html
    assert "shell-groups=[1]" in html, html


@pytest.mark.django_db
@pytest.mark.parametrize("name", INLINE)
def test_the_cache_is_cleared_and_a_later_render_sees_new_state(name):
    """The stale-context hazard: the cache must not outlive the GET.

    After the response the cache is gone, so a render on the same instance
    (what a later event's render is) evaluates the override AGAIN and the page
    reflects state changed since. A cache that survived the GET would serve
    ``n=1`` here.
    """
    Client().get("/" + name.lower() + "/")
    view = INSTANCES[0]
    assert view._cached_context is None, "the GET left its context cache on the view"

    CALLS.clear()  # the GET's own count is pinned elsewhere; this is about what follows it
    view.n = 7
    html, _patches, _version = view.render_with_diff()
    assert len(CALLS) == 1, "a render after the GET must evaluate the override afresh, once"
    assert "7-101" in html, html


@pytest.mark.django_db
@pytest.mark.parametrize("name", INLINE)
def test_the_helper_reads_the_cache_and_evaluates_without_one(name):
    """``_context_for_render``: preloaded first, then the cache, then a real evaluation."""
    Client().get("/" + name.lower() + "/")
    view = INSTANCES[0]
    CALLS.clear()

    preloaded = {"n": 5, "stamp": "p"}
    assert view._context_for_render(preloaded) is preloaded
    assert CALLS == []

    view._cached_context = {"n": 3, "stamp": "c"}
    got = view._context_for_render()
    assert (got["n"], got["stamp"]) == (3, "c")
    assert CALLS == [], "a cached context must be served without evaluating the override"

    view._cached_context = None
    got = view._context_for_render()
    assert CALLS == [type(view).__name__] and got["stamp"] == 101


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


async def _connect():
    from channels.testing import WebsocketCommunicator

    store = SessionStore()
    await sync_to_async(store.create)()
    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = store
    communicator.scope["user"] = AnonymousUser()
    assert (await communicator.connect())[0]
    await communicator.receive_json_from()
    return communicator


async def _mount(communicator, name):
    await communicator.send_json_to(
        {"type": "mount", "view": MOD + "." + name, "url": "/" + name.lower() + "/"}
    )
    return await receive_settled(communicator, has_type("mount"))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name", INLINE)
async def test_a_websocket_mount_runs_the_override_once(name):
    communicator = await _connect()
    try:
        frames = await _mount(communicator, name)
        assert [f for f in frames if f.get("type") == "mount"], frames
        assert len(CALLS) == 1, f"{name}: the override ran {len(CALLS)} times on one mount"
        mount = [f for f in frames if f.get("type") == "mount"][0]
        assert "1-101" in mount["html"], mount["html"]
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "#3725 sibling, not fixed here: an EXPLICIT view whose page shell comes from "
        "template_name evaluates the override three times on a live mount "
        "(runtime._reconstruct_explicit_page_shell renders the full page with no cached "
        "context, then the root renders again). Delete this marker when it is one."
    ),
)
@pytest.mark.parametrize(
    "name", [n for n in ALL if n.startswith("V_explicit") and n.endswith("_shell")]
)
async def test_an_explicit_page_shell_websocket_mount_runs_the_override_once(name):
    communicator = await _connect()
    try:
        await _mount(communicator, name)
        assert len(CALLS) == 1, f"{name}: the override ran {len(CALLS)} times on one mount"
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name", INLINE)
async def test_a_websocket_event_re_renders_with_fresh_context_once(name):
    """The hazard the fix must not create: an event that changed state re-renders afresh."""
    communicator = await _connect()
    try:
        await _mount(communicator, name)
        assert len(CALLS) == 1
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        frames = await receive_settled(communicator, has_type("patch"))
        assert len(CALLS) == 2, f"{name}: the event ran the override {len(CALLS) - 1} times"
        assert "102" in json.dumps(frames), frames
    finally:
        await communicator.disconnect()


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


def _sse_request(url, params, key):
    request = RequestFactory().get(url, data=params)
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name", INLINE)
async def test_an_sse_mount_runs_the_override_once(name):
    store = SessionStore()
    await sync_to_async(store.create)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_sse_request)(
        f"/djust/sse/{sid}/",
        {"view": MOD + "." + name, "_djust_url": "/" + name.lower() + "/"},
        store.session_key,
    )
    response = await DjustSSEStreamView().get(request, session_id=sid)
    stream = response._iterator
    seen = []
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        while "mount" not in seen:
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
            if chunk.startswith("data:"):
                seen.append(json.loads(chunk[len("data:") :]).get("type"))
    finally:
        await stream.aclose()
    assert len(CALLS) == 1, f"{name}: the override ran {len(CALLS)} times on one SSE mount"


# --------------------------------------------------------------------------- #
# The caller set
# --------------------------------------------------------------------------- #


def _function_node(module, qualname):
    tree = ast.parse(inspect.getsource(module))
    cls_name, fn_name = qualname.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == fn_name:
                    return item
    raise AssertionError(f"{qualname} not found in {module.__name__}")


@pytest.mark.parametrize(
    "module_name, qualname",
    [
        ("djust.mixins.rust_bridge", "RustBridgeMixin._sync_state_to_rust"),
        ("djust.mixins.template", "TemplateMixin._render_full_template_inner"),
    ],
)
def test_the_render_sinks_take_their_context_through_the_one_helper(module_name, qualname):
    """Parallel-path drift (#1646): grep the SINK, not the callers you expect.

    The two places the render path used to evaluate ``get_context_data`` itself
    both call ``_context_for_render`` now, which is the one place that decides
    between a preloaded context, the GET's cached context and a fresh evaluation.
    A third copy of the old call is how the double evaluation comes back.
    """
    import importlib

    module = importlib.import_module(module_name)
    node = _function_node(module, qualname)
    attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    assert "get_context_data" not in attrs, (
        f"{qualname} evaluates get_context_data() itself; use self._context_for_render()"
    )
    assert "_context_for_render" in attrs, f"{qualname} no longer goes through the helper"
