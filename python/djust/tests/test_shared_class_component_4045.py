"""#4045: a plain component in a view's class body is one object shared by every session.

A plain ``Component`` has no descriptor, so ``self.badge`` on a view is the class
attribute itself: one object for every session of the worker. On a view that
renders it (legacy, or an explicit view that hands it to the template) one user's
handler write shows in every other user's render and in every later mount.

* Explicit views refuse the declaration when the class is created, and again on
  the mount path (a component assigned to the class afterwards, a policy changed
  since). The gate-off sibling below proves the refused shape really leaks.
* Legacy views keep running and get the ``djust.V026`` system check.
* Each form the error message recommends is isolated per session.

Sessions here are real ``WebsocketCommunicator`` connections with different
session cookies; session C mounts after A's write.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler
from djust import _exposure_providers
from djust._exposure import ExposureConfigurationError
from djust.checks.components import check_shared_class_level_components
from djust.components.components.badge import Badge
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.tests.test_exposure_runtime import make_request
from djust.websocket import LiveViewConsumer

MODULE = __name__
SECRET = "alice@example.com"
TEMPLATE = "<div dj-root><p>{{ n }}</p>{{ badge }}</div>"


# ------------------------------------------------------------------ #
# Views
# ------------------------------------------------------------------ #


class Labeled(LiveComponent):
    class State(TypedState):
        label: str = "initial"

    template = '<span class="labeled">{{ label }}</span>'

    @event_handler()
    def relabel(self, who: str = "", **kwargs: Any) -> None:
        self.label = who


class FieldView(LiveView):
    """Recommended: a ``state()`` field built per view, supplied from get_context_data()."""

    exposure_policy = "explicit"
    template = TEMPLATE
    n = state(0, persist="server")
    badge = state(default_factory=lambda: Badge("initial"))

    @event_handler()
    def who(self, who: str = "", **kwargs: Any) -> None:
        self.badge.label = who

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(n=self.n, badge=self.badge, **kwargs)


class PrivateView(LiveView):
    """Recommended (legacy-compatible): a private attribute built in mount()."""

    exposure_policy = "explicit"
    template = TEMPLATE
    n = state(0, persist="server")

    def mount(self, request: Any, **kwargs: Any) -> None:
        self._badge = Badge("initial")

    @event_handler()
    def who(self, who: str = "", **kwargs: Any) -> None:
        self._badge.label = who

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(n=self.n, badge=self._badge, **kwargs)


class BuiltView(LiveView):
    """Recommended: built in get_context_data() from the view's own fields."""

    exposure_policy = "explicit"
    template = TEMPLATE
    n = state(0, persist="server")
    label = state("initial", persist="server")

    @event_handler()
    def who(self, who: str = "", **kwargs: Any) -> None:
        self.label = who

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(n=self.n, badge=Badge(self.label), **kwargs)


class DescriptorView(LiveView):
    """A LiveComponent in the class body is not refused: each view gets its own copy."""

    exposure_policy = "explicit"
    template = TEMPLATE
    n = state(0, persist="server")
    badge = Labeled()

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(n=self.n, **kwargs)


class LegacySharedView(LiveView):
    """Legacy is not refused (V026 reports it)."""

    exposure_policy = "legacy"
    template = TEMPLATE
    n = 0
    badge = Badge("initial")

    @event_handler()
    def who(self, who: str = "", **kwargs: Any) -> None:
        self.badge.label = who

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1


class LateView(LiveView):
    """Explicit and valid at class creation; tests assign to it afterwards."""

    exposure_policy = "explicit"
    template = TEMPLATE
    n = state(0, persist="server")


class BadgeMixin:
    """A plain mixin (no ``__init_subclass__`` of ours) carrying the shared shape."""

    badge = Badge("initial")


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


@pytest.fixture
def ws_settings():
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MODULE], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        yield


async def _open(view_name: str):
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": f"{MODULE}.{view_name}", "url": "/x/"})
    return socket, await socket.receive_json_from(), request.session.session_key


async def _event(socket, name: str, ref: int, **params: Any) -> list:
    await socket.send_json_to({"type": "event", "event": name, "params": params, "ref": ref})
    frames = [await socket.receive_json_from()]
    while not await socket.receive_nothing(timeout=0.3):
        frames.append(await socket.receive_json_from())
    return frames


async def _three_sessions(view_name: str, write: dict) -> dict:
    """A writes; B (another cookie) runs an event; C mounts afterwards."""
    a, mount_a, key_a = await _open(view_name)
    b, mount_b, key_b = await _open(view_name)
    try:
        assert mount_a["type"] == "mount" and mount_b["type"] == "mount", (mount_a, mount_b)
        assert key_a != key_b
        assert "initial" in mount_a["html"]
        params = dict(write)
        if params.pop("component", False):
            params["component_id"] = re.search(
                r'data-component-id="([^"]+)"', mount_a["html"]
            ).group(1)
        event_name = params.pop("event")
        a_frames = await _event(a, event_name, 1, **params)
        b_frames = await _event(b, "bump", 2)
        c, mount_c, _ = await _open(view_name)
        await c.disconnect()
        return {
            "a": json.dumps(a_frames),
            "b": json.dumps(b_frames),
            "c": json.dumps(mount_c),
        }
    finally:
        await a.disconnect()
        await b.disconnect()


# ------------------------------------------------------------------ #
# Refusal at class creation
# ------------------------------------------------------------------ #


def test_explicit_class_body_plain_component_is_refused_at_class_creation():
    with pytest.raises(ExposureConfigurationError) as raised:

        class Refused(LiveView):
            exposure_policy = "explicit"
            template = TEMPLATE
            badge = Badge("CLASS_VALUE_SENTINEL")

    message = str(raised.value)
    assert "'badge'" in message
    assert "shared by every session" in message
    assert "badge = state(default_factory=lambda: Badge(...))" in message
    assert "get_context_data()" in message
    # Names the attribute and the class, never a value.
    assert "CLASS_VALUE_SENTINEL" not in message


def test_plain_component_inherited_from_a_mixin_is_refused():
    with pytest.raises(ExposureConfigurationError, match="'badge'"):

        class FromMixin(BadgeMixin, LiveView):
            exposure_policy = "explicit"
            template = TEMPLATE


def test_inherited_from_a_legacy_view_base_is_refused():
    with pytest.raises(ExposureConfigurationError, match="'badge'"):

        class Child(LegacySharedView):
            exposure_policy = "explicit"


def test_a_subclass_that_redefines_the_name_is_not_refused():
    class Redefined(BadgeMixin, LiveView):
        exposure_policy = "explicit"
        template = TEMPLATE
        badge = state(default_factory=lambda: Badge("initial"))

    assert _exposure_providers.class_level_plain_components(Redefined) == []


def test_live_component_and_legacy_declarations_are_not_refused():
    assert _exposure_providers.class_level_plain_component_error(DescriptorView) is None
    assert _exposure_providers.class_level_plain_component_error(LegacySharedView) is None
    assert _exposure_providers.class_level_plain_components(LegacySharedView) == ["badge"]


# ------------------------------------------------------------------ #
# Refusal on the mount path
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_component_assigned_to_the_class_later_is_refused_at_mount(ws_settings, monkeypatch):
    monkeypatch.setattr(LateView, "badge", Badge("LATE_VALUE_SENTINEL"), raising=False)
    socket, frame, _ = await _open("LateView")
    try:
        assert frame["type"] == "error", frame
        assert "'badge'" in json.dumps(frame)
        assert "LATE_VALUE_SENTINEL" not in json.dumps(frame)
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_policy_changed_to_explicit_later_is_refused_at_mount(ws_settings, monkeypatch):
    monkeypatch.setattr(LegacySharedView, "exposure_policy", "explicit")
    socket, frame, _ = await _open("LegacySharedView")
    try:
        assert frame["type"] == "error", frame
        assert "'badge'" in json.dumps(frame)
    finally:
        await socket.disconnect()


# ------------------------------------------------------------------ #
# The leak the refusal prevents (gate-off sibling) and the legacy shape
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_gate_off_the_refused_shape_leaks_across_sessions(ws_settings, monkeypatch):
    """With the refusal switched off, the refused declaration leaks to B and to C.

    Proves the refusal is load-bearing: the shape it rejects is a real
    cross-user leak, not a style rule.
    """
    monkeypatch.setattr(_exposure_providers, "class_level_plain_component_error", lambda v: None)

    class GateOffView(LiveView):
        exposure_policy = "explicit"
        template = TEMPLATE
        n = state(0, persist="server")
        badge = Badge("initial")

        @event_handler()
        def who(self, who: str = "", **kwargs: Any) -> None:
            self.badge.label = who

        @event_handler()
        def bump(self, **kwargs: Any) -> None:
            self.n += 1

        def get_context_data(self, **kwargs: Any) -> dict:
            return super().get_context_data(n=self.n, badge=self.badge, **kwargs)

    monkeypatch.setattr(sys.modules[MODULE], "GateOffView", GateOffView, raising=False)
    seen = await _three_sessions("GateOffView", {"event": "who", "who": SECRET})
    assert SECRET in seen["b"], "another session renders A's write"
    assert SECRET in seen["c"], "a later mount renders A's write"
    assert GateOffView.__dict__["badge"].label == SECRET


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_legacy_class_body_component_still_runs_and_leaks(ws_settings, monkeypatch):
    """Legacy is not refused at runtime; the shared object renders for B and C (V026)."""
    monkeypatch.setattr(LegacySharedView.__dict__["badge"], "label", "initial")
    seen = await _three_sessions("LegacySharedView", {"event": "who", "who": SECRET})
    assert SECRET in seen["b"] and SECRET in seen["c"]


# ------------------------------------------------------------------ #
# The recommended forms are isolated per session
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "view_name,write",
    [
        ("FieldView", {"event": "who", "who": SECRET}),
        ("PrivateView", {"event": "who", "who": SECRET}),
        ("BuiltView", {"event": "who", "who": SECRET}),
        ("DescriptorView", {"event": "relabel", "who": SECRET, "component": True}),
    ],
)
async def test_recommended_forms_are_isolated_per_session(ws_settings, view_name, write):
    seen = await _three_sessions(view_name, write)
    assert SECRET in seen["a"], "the writer renders its own write"
    assert SECRET not in seen["b"], "another session does not"
    assert SECRET not in seen["c"], "a later mount does not"


# ------------------------------------------------------------------ #
# D-h message, split by kind
# ------------------------------------------------------------------ #


def _instance_error(component: Any) -> str:
    view = object.__new__(LateView)
    view.badge = component
    error = _exposure_providers.instance_assigned_component_error(view)
    assert error is not None
    return str(error)


def test_instance_assigned_plain_component_message_names_the_working_forms():
    message = _instance_error(Badge("INSTANCE_VALUE_SENTINEL"))
    assert "'badge'" in message
    assert "badge = state(default_factory=lambda: Badge(...))" in message
    assert "get_context_data()" in message
    assert "class body" not in message
    assert "INSTANCE_VALUE_SENTINEL" not in message


def test_instance_assigned_live_component_message_says_class_body():
    message = _instance_error(Labeled(component_id="badge"))
    assert "'badge'" in message
    assert "declare it in the class body" in message
    assert "own copy" in message


# ------------------------------------------------------------------ #
# djust.V026 (legacy)
# ------------------------------------------------------------------ #


def _v026() -> list:
    return [m for m in check_shared_class_level_components(None) if m.id == "djust.V026"]


def test_v026_reports_a_legacy_class_body_component_once():
    hits = [m for m in _v026() if "LegacySharedView" in m.msg]
    assert len(hits) == 1
    assert "'badge'" in hits[0].msg
    assert hits[0].fix_hint and "mount()" in hits[0].fix_hint


def test_v026_skips_explicit_views_and_live_components():
    text = " ".join(m.msg for m in _v026())
    for name in ("FieldView", "PrivateView", "BuiltView", "DescriptorView", "LateView"):
        assert name not in text


def test_v026_reports_the_mixin_that_declares_it(monkeypatch):
    class LegacyWithMixin(BadgeMixin, LiveView):
        exposure_policy = "legacy"
        template = TEMPLATE

    owners = [m.msg for m in _v026() if "BadgeMixin" in m.msg]
    assert len(owners) == 1


def test_v026_can_be_suppressed(settings):
    settings.DJUST_CONFIG = {"suppress_checks": ["V026"]}
    assert _v026() == []
