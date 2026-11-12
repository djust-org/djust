"""djust.V026: a plain component instance in a view's class body (#4045).

A plain ``Component`` has no descriptor, so a class-body instance is one object
shared by every user of the view in the process. 1.3 keeps the runtime
behaviour and reports the declaration. The two-session test at the end records
that behaviour (and that the fix the check names, ``mount()``, is per view)
over real ``WebsocketCommunicator`` connections with different session cookies.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings

from djust import LiveView, event_handler
from djust.checks.components import check_shared_class_level_components
from djust.components.components.badge import Badge
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.tests.test_exposure_runtime import make_request
from djust.websocket import LiveViewConsumer

MODULE = __name__
SECRET = "alice@example.com"
TEMPLATE = "<div dj-root><p>{{ n }}</p>{{ badge }}</div>"


class Labeled(LiveComponent):
    class State(TypedState):
        label: str = "initial"

    template = '<span class="labeled">{{ label }}</span>'


class Stateless(LiveComponent):
    template = '<span class="stateless">{{ label }}</span>'

    def mount(self, **kwargs: Any) -> None:
        self.label = "initial"

    def get_context_data(self) -> dict:
        return {"label": self.label}


class SharedBadgeView(LiveView):
    """Reported: a plain component in the class body."""

    exposure_policy = "legacy"
    template = TEMPLATE
    badge = Badge("initial")

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.n = 0

    @event_handler()
    def who(self, who: str = "", **kwargs: Any) -> None:
        self.badge.label = who

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.n += 1


class MountBadgeView(SharedBadgeView):
    """Not reported: the name is redefined, and the component is created in mount()."""

    badge = None

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.n = 0
        self.badge = Badge("initial")


class DescriptorView(LiveView):
    """Not reported: LiveComponent declarations, with and without a State class."""

    exposure_policy = "legacy"
    template = TEMPLATE
    badge = Labeled()
    other = Stateless()


class SharedBadgeMixin:
    badge = Badge("initial")


class FromMixinView(SharedBadgeMixin, LiveView):
    """Reported once, against the mixin that declares it."""

    exposure_policy = "legacy"
    template = TEMPLATE


class FromBaseView(SharedBadgeView):
    """Inherits the base's declaration; the base is the class reported."""


def _v026() -> list:
    return [m for m in check_shared_class_level_components(None) if m.id == "djust.V026"]


def _about(name: str) -> list:
    return [m for m in _v026() if f".{name} declares" in m.msg]


def test_reports_a_class_body_plain_component_once():
    hits = _about("SharedBadgeView")
    assert len(hits) == 1
    assert "'badge'" in hits[0].msg
    assert "shared by every user" in hits[0].msg
    assert hits[0].fix_hint and "mount()" in hits[0].fix_hint
    assert "self.badge = Badge(...)" in hits[0].fix_hint


def test_reports_the_mixin_that_declares_it():
    assert len(_about("SharedBadgeMixin")) == 1


def test_an_inheriting_view_is_reported_through_the_declaring_class():
    assert _about("FromBaseView") == [] and _about("FromMixinView") == []
    assert len(_about("SharedBadgeView")) == 1


def test_live_components_and_mount_created_components_are_not_reported():
    text = " ".join(m.msg for m in _v026())
    assert "DescriptorView" not in text
    assert "MountBadgeView" not in text


def test_djust_shipped_classes_are_not_reported():
    assert not [m for m in _v026() if m.msg.startswith("djust.") and ".tests." not in m.msg]


def test_can_be_suppressed(settings):
    settings.DJUST_CONFIG = {"suppress_checks": ["V026"]}
    assert _v026() == []


# ------------------------------------------------------------------ #
# The runtime behaviour the check reports (unchanged in 1.3)
# ------------------------------------------------------------------ #


async def _open(view_name: str):
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": f"{MODULE}.{view_name}", "url": "/x/"})
    return socket, await socket.receive_json_from(timeout=5)


async def _event(socket, name: str, ref: int, **params: Any) -> str:
    await socket.send_json_to({"type": "event", "event": name, "params": params, "ref": ref})
    frames = [await socket.receive_json_from(timeout=5)]
    while not await socket.receive_nothing(timeout=0.3):
        frames.append(await socket.receive_json_from())
    return json.dumps(frames)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("view_name,shared", [("SharedBadgeView", True), ("MountBadgeView", False)])
async def test_two_sessions(view_name, shared, monkeypatch):
    monkeypatch.setattr(SharedBadgeView.__dict__["badge"], "label", "initial")
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=[MODULE], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        a, mount_a = await _open(view_name)
        b, mount_b = await _open(view_name)
        try:
            assert mount_a["type"] == "mount" and mount_b["type"] == "mount"
            await _event(a, "who", 1, who=SECRET)
            seen_by_b = SECRET in await _event(b, "bump", 2)
            c, mount_c = await _open(view_name)
            await c.disconnect()
            assert seen_by_b is shared
            assert (SECRET in json.dumps(mount_c)) is shared
        finally:
            await a.disconnect()
            await b.disconnect()
