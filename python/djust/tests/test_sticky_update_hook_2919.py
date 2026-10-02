"""#2919: changed ``{% live_render %}`` inputs reach a reused sticky child.

A sticky child keeps its live instance across parent re-renders, so the tag's
keyword arguments reached ``mount()`` only once and later values were dropped.
A child that overrides ``_on_sticky_update(self, changed)`` now receives the
arguments that changed, before it renders, without being remounted or having
its state reset. A child without the override behaves as before.
"""

from __future__ import annotations

import logging

import pytest
from asgiref.sync import sync_to_async
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.security import is_safe_event_name
from djust.websocket_utils import _validate_event_security

_MOD = "djust.tests.test_sticky_update_hook_2919"


class HookChild(LiveView):
    """Takes ``n`` and ``label`` at mount; ``_on_sticky_update`` re-applies them."""

    sticky = True
    sticky_id = "hookchild"
    template = '<div>child {{ n }} {{ label }} clicks={{ clicks }}</div><b dj-click="bump">b</b>'
    mounts = 0
    updates: list = []

    def mount(self, request, n=0, label="", **kwargs):
        type(self).mounts += 1
        self.n = n
        self.label = label
        self.clicks = 0

    def _on_sticky_update(self, changed):
        type(self).updates.append(dict(changed))
        if "n" in changed:
            self.n = changed["n"]
        if "label" in changed:
            self.label = changed["label"]

    @event_handler()
    def bump(self, **kwargs):
        self.clicks += 1

    def get_context_data(self, **kwargs):
        return {"n": self.n, "label": self.label, "clicks": self.clicks}


class HookParent(LiveView):
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + _MOD + '.HookParent">'
        "<h1>{{ n }}</h1>"
        '{% live_render "' + _MOD + '.HookChild" sticky=True n=n label=label %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 1
        self.label = "a"

    @event_handler()
    def set_n(self, n=0, **kwargs):
        self.n = int(n)

    def get_context_data(self, **kwargs):
        return {"view": self, "n": self.n, "label": self.label}


class PlainChild(LiveView):
    """No override: kwargs stay mount-time only, with a warning."""

    sticky = True
    sticky_id = "plainchild"
    template = "<div>plain {{ n }}</div>"

    def mount(self, request, n=0, **kwargs):
        self.n = n

    def get_context_data(self, **kwargs):
        return {"n": self.n}


class PlainParent(LiveView):
    template = (
        "{% load live_tags %}"
        '<div dj-root dj-view="' + _MOD + '.PlainParent">'
        '{% live_render "' + _MOD + '.PlainChild" sticky=True n=n %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.n = 1

    def get_context_data(self, **kwargs):
        return {"view": self, "n": self.n}


class RaisingChild(PlainChild):
    sticky_id = "raisingchild"
    calls = 0

    def _on_sticky_update(self, changed):
        type(self).calls += 1
        raise RuntimeError("boom")


class RaisingParent(PlainParent):
    template = PlainParent.template.replace("PlainParent", "RaisingParent").replace(
        "PlainChild", "RaisingChild"
    )


class AsyncChild(PlainChild):
    sticky_id = "asyncchild"

    async def _on_sticky_update(self, changed):  # a mistake: it cannot be awaited
        pass


class AsyncParent(PlainParent):
    template = PlainParent.template.replace("PlainParent", "AsyncParent").replace(
        "PlainChild", "AsyncChild"
    )


@pytest.fixture(autouse=True)
def _reset():
    HookChild.mounts = 0
    HookChild.updates = []
    RaisingChild.calls = 0
    with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MOD], DEBUG=False):
        yield


def _parent(cls, **attrs):
    request = RequestFactory().get("/")
    view = cls()
    view.request = request
    view.mount(request)
    for key, value in attrs.items():
        setattr(view, key, value)
    return view


def _render(view):
    return view.render_with_diff()[0]


@pytest.mark.django_db
class TestUpdateHook:
    def test_changed_input_is_applied_without_a_remount(self):
        view = _parent(HookParent)
        assert "child 1 a" in _render(view)
        assert HookChild.mounts == 1
        view.n = 2
        html = _render(view)
        assert "child 2 a" in html
        assert HookChild.mounts == 1  # never remounted
        assert HookChild.updates == [{"n": 2}]

    def test_only_the_changed_inputs_are_passed(self):
        view = _parent(HookParent)
        _render(view)
        view.label = "z"
        _render(view)
        view.n = 5
        view.label = "y"
        _render(view)
        assert HookChild.updates == [{"label": "z"}, {"n": 5, "label": "y"}]

    def test_state_the_child_built_up_survives_the_update(self):
        view = _parent(HookParent)
        _render(view)
        child = view._get_child_view("hookchild")
        child.clicks = 7  # what the user did in the child since it mounted
        view.n = 2
        html = _render(view)
        assert "child 2 a clicks=7" in html
        assert view._get_child_view("hookchild") is child

    def test_hook_runs_once_per_change_not_once_per_render(self):
        view = _parent(HookParent)
        _render(view)
        _render(view)
        assert HookChild.updates == []
        view.n = 2
        _render(view)
        _render(view)
        _render(view)
        assert HookChild.updates == [{"n": 2}]
        view.n = 1  # back to the mount-time value is a change too
        _render(view)
        assert HookChild.updates == [{"n": 2}, {"n": 1}]

    def test_the_hook_runs_before_the_child_renders(self):
        view = _parent(HookParent)
        _render(view)
        view.n = 9
        # The very render that sees the new input already shows it.
        assert "child 9 a" in _render(view)

    def test_a_child_without_the_override_keeps_mount_time_kwargs_and_warns(self, caplog):
        view = _parent(PlainParent)
        assert "plain 1" in _render(view)
        view.n = 2
        with caplog.at_level(logging.WARNING, logger="djust"):
            html = _render(view)
        assert "plain 1" in html  # unchanged behaviour
        message = next(
            r.getMessage() for r in caplog.records if "mount-time only" in r.getMessage()
        )
        assert "'n'" in message
        assert "_on_sticky_update" in message  # and the warning now names the way out

    def test_the_base_class_default_is_not_an_override(self):
        from djust.mixins.sticky import StickyChildRegistry

        assert PlainChild._on_sticky_update is StickyChildRegistry._on_sticky_update
        assert HookChild._on_sticky_update is not StickyChildRegistry._on_sticky_update
        assert PlainChild()._on_sticky_update({"n": 1}) is None

    def test_a_failing_hook_propagates_and_is_retried(self):
        view = _parent(RaisingParent)
        _render(view)
        view.n = 2
        with pytest.raises(Exception):
            _render(view)
        assert RaisingChild.calls == 1
        with pytest.raises(Exception):
            _render(view)
        assert RaisingChild.calls == 2  # not recorded as applied

    def test_an_async_hook_is_refused(self):
        view = _parent(AsyncParent)
        _render(view)
        view.n = 2
        with pytest.raises(TypeError, match="plain function"):
            _render(view)

    def test_the_http_load_renders_the_initial_inputs(self):
        """First render of a page: a fresh mount, so no hook call."""
        view = _parent(HookParent)
        html = view.render_full_template(RequestFactory().get("/"))
        assert "child 1 a" in html
        assert HookChild.updates == []


class TestNotReachableFromTheClient:
    def test_the_name_is_not_a_valid_event_name(self):
        assert is_safe_event_name("_on_sticky_update") is False

    def test_the_hook_is_not_an_event_handler(self):
        from djust.decorators import is_event_handler

        assert not is_event_handler(HookChild._on_sticky_update)

    @pytest.mark.asyncio
    async def test_a_socket_event_naming_it_is_refused_and_does_not_run(self):
        sent = []

        class FakeWS:
            _client_ip = None

            async def send_error(self, error, **kwargs):
                sent.append(error)

            async def close(self, code=1000):
                sent.append(("close", code))

        child = HookChild()
        child.mount(RequestFactory().get("/"))
        from djust.rate_limit import ConnectionRateLimiter

        handler = await _validate_event_security(
            FakeWS(), "_on_sticky_update", child, ConnectionRateLimiter()
        )
        assert handler is None
        assert sent == ["Event rejected"]
        assert HookChild.updates == []

    @pytest.mark.asyncio
    async def test_other_public_hook_names_would_have_been_reachable_in_open_mode(self):
        """Why the hook is private: in ``event_security = "open"`` any public
        method is a valid event, which is the reachability a public name would
        have given the hook. The private name closes it in every mode."""
        from djust.config import config

        class FakeWS:
            _client_ip = None

            async def send_error(self, error, **kwargs):
                pass

            async def close(self, code=1000):
                pass

        class PublicHook(HookChild):
            def sticky_update(self, changed):
                type(self).updates.append(changed)

        child = PublicHook()
        from djust.rate_limit import ConnectionRateLimiter

        previous = config.get("event_security", "strict")
        config.set("event_security", "open")
        try:
            allowed = await _validate_event_security(
                FakeWS(), "sticky_update", child, ConnectionRateLimiter()
            )
            blocked = await _validate_event_security(
                FakeWS(), "_on_sticky_update", child, ConnectionRateLimiter()
            )
        finally:
            config.set("event_security", previous)
        assert allowed is not None
        assert blocked is None


# ---------------------------------------------------------------------------
# The real socket path: a parent event re-renders the parent, which runs the
# tag again; the recovery render must show the child with the new input.
# ---------------------------------------------------------------------------


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_parent_event_applies_the_new_input_to_the_live_child_over_the_socket():
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _session():
        store = SessionStore()
        store.create()
        return store.session_key

    key = await sync_to_async(_session)()
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[_MOD], DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MOD]
    ):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope["session"] = _ScopeSession(key)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=2)
        try:
            await socket.send_json_to({"type": "mount", "view": f"{_MOD}.HookParent", "url": "/p/"})
            mount = await _receive_until(socket, "mount")
            assert "child 1 a" in mount.get("html", "")
            # The user interacts with the child...
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "bump",
                    "params": {"view_id": "hookchild"},
                    "ref": 1,
                }
            )
            await _receive_until(socket, "embedded_update")
            # ...then the parent's own state changes, which feeds the tag.
            await socket.send_json_to(
                {"type": "event", "event": "set_n", "params": {"n": 4}, "ref": 2}
            )
            await _receive_until(socket, "patch")
            # What the client would recover to: the live child, fed the new input,
            # with the click it already made.
            await socket.send_json_to({"type": "request_html"})
            recovery = await _receive_until(socket, "html_recovery")
        finally:
            await socket.disconnect()
    html = recovery.get("html", "")
    assert "child 4 a clicks=1" in html, html
    assert HookChild.updates == [{"n": 4}]
    assert HookChild.mounts == 1
