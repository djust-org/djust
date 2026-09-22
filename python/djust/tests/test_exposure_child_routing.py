"""Descendant and repeated-instance routing over the real runtime (ADR-038 E3-4).

Two same-type explicit sibling children plus an explicit grandchild. Events,
saves and background results must land only on their target, and the identity
each is bound to must be distinct.
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust._exposure_auth import fresh_socket_request
from djust._exposure_children import child_event_adapter, child_state_key
from djust.decorators import state
from djust.tests.test_exposure_child_events import mount

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__


class Box(LiveView):
    exposure_policy = "explicit"
    count = state(1)
    template = '<div>Box={{ count }}<button dj-click="increment">+</button></div>'

    def mount(self, request, **kwargs):
        self.count = 1

    @event_handler()
    def increment(self):
        self.count += 1

    @event_handler()
    def begin(self):
        def work():
            self.count = 50

        self.start_async(work, name="box-job")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class Leaf(Box):
    sticky = True
    sticky_id = "leaf"
    count = state(1, persist="server")
    template = '<div>Leaf={{ count }}<button dj-click="increment">+</button></div>'


class Middle(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "middle"
    level = state(1, persist="server")
    template = (
        "<div>Middle={{ level }}{% load live_tags %}"
        f'{{% live_render "{MODULE}.Leaf" sticky=True %}}</div>'
    )

    def mount(self, request, **kwargs):
        self.level = 1

    def get_context_data(self, **kwargs):
        return super().get_context_data(level=self.level, **kwargs)


class Root(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root>{% load live_tags %}"
        f'{{% live_render "{MODULE}.Box" view_id="a" %}}'
        f'{{% live_render "{MODULE}.Box" view_id="b" %}}'
        f'{{% live_render "{MODULE}.Middle" sticky=True %}}'
        "</div>"
    )


class OtherMiddle(Middle):
    sticky_id = "other-middle"
    level = state(1, persist="server")


class AmbiguousRoot(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root>{% load live_tags %}"
        f'{{% live_render "{MODULE}.Middle" sticky=True %}}'
        f'{{% live_render "{MODULE}.OtherMiddle" sticky=True %}}'
        "</div>"
    )


class LegacyLeaf(LiveView):
    sticky = True
    sticky_id = "legacy-leaf"
    template = '<div><button dj-click="poke">x</button></div>'

    @event_handler()
    def poke(self):
        self.poked = True


class LegacyMiddle(LiveView):
    sticky = True
    sticky_id = "legacy-middle"
    template = (
        f'<div>{{% load live_tags %}}{{% live_render "{MODULE}.LegacyLeaf" sticky=True %}}</div>'
    )

    @event_handler()
    def poke(self):
        self.poked = True


class LegacyRoot(LiveView):
    template = (
        "<div dj-root>{% load live_tags %}"
        f'{{% live_render "{MODULE}.LegacyMiddle" sticky=True %}}</div>'
    )


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE, "djust.tests.test_exposure_child_events"]


async def send(runtime, event, view_id):
    await runtime.dispatch_event({"type": "event", "event": event, "params": {"view_id": view_id}})


def tree(runtime):
    root = runtime.view_instance
    middle = root._get_child_view("middle")
    return (
        root,
        root._get_child_view("a"),
        root._get_child_view("b"),
        middle,
        (middle._get_child_view("leaf")),
    )


async def stored_values(request, *slots):
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    return stored[child_state_key(request.path, slots)]["state"]["values"]


def updates(transport):
    return [
        (f["view_id"], f.get("source"))
        for f in transport.sent
        if f.get("type") == "embedded_update"
    ]


async def test_events_and_saves_land_only_on_their_target():
    runtime, transport, request = await mount(view_class=Root)
    root, a, b, middle, leaf = tree(runtime)
    assert type(a) is type(b) is Box and a is not b and leaf._parent_view is middle
    # Same type, distinct slots: distinct reuse identities.
    digests = {c._explicit_child_reuse_identity.digest for c in (a, b, middle, leaf)}
    assert len(digests) == 4

    await send(runtime, "increment", "a")
    await send(runtime, "increment", "b")
    await send(runtime, "increment", "b")
    await send(runtime, "increment", "leaf")
    assert not transport.errors, transport.errors
    assert (a.count, b.count, leaf.count, middle.level) == (2, 3, 2, 1)
    assert updates(transport) == [("a", None), ("b", None), ("b", None), ("leaf", None)]
    leaf_html = [f["html"] for f in transport.sent if f.get("view_id") == "leaf"][0]
    assert "Leaf=2" in leaf_html and "Box=" not in leaf_html and "Middle=" not in leaf_html

    # Saves: only the grandchild's own scoped envelope changed.
    assert (await stored_values(request, "middle", "leaf"))["count"] == 2
    assert (await stored_values(request, "middle"))["level"] == 1
    stored = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert child_state_key(request.path, ("leaf",)) not in stored
    # The grandchild's binding is ancestry-scoped, distinct from its parent's.
    fresh = await sync_to_async(fresh_socket_request)(root)
    leaf_binding = (await sync_to_async(child_event_adapter)(leaf, root, fresh)).binding.view
    middle_binding = (await sync_to_async(child_event_adapter)(middle, root, fresh)).binding.view
    assert leaf_binding != middle_binding and leaf_binding.startswith("child:")

    # A fresh connection restores each envelope into its own slot.
    restored, _, _ = await mount(request.session.session_key, view_class=Root)
    _, ra, rb, rmiddle, rleaf = tree(restored)
    assert (ra.count, rb.count, rleaf.count, rmiddle.level) == (1, 1, 2, 1)


async def test_background_results_land_only_on_their_target():
    runtime, transport, request = await mount(view_class=Root)
    root, a, b, middle, leaf = tree(runtime)
    for target, owner in (("b", b), ("leaf", leaf)):
        await send(runtime, "begin", target)
        handles = tuple(owner._async_task_handles)
        assert handles
        await asyncio.wait_for(asyncio.gather(*handles), 3)
    assert not transport.errors, transport.errors
    assert (a.count, b.count, leaf.count) == (1, 50, 50)
    async_frames = [f for f in transport.sent if f.get("source") == "async"]
    assert [f["view_id"] for f in async_frames] == ["b", "leaf"]
    assert "Box=50" in async_frames[0]["html"] and "Leaf=50" in async_frames[1]["html"]
    assert (await stored_values(request, "middle", "leaf"))["count"] == 50
    assert (await stored_values(request, "middle"))["level"] == 1


async def test_unknown_descendant_id_is_not_routed():
    runtime, transport, _ = await mount(view_class=Root)
    await send(runtime, "increment", "nope")
    assert transport.errors[-1]["error"] == "Embedded view not found"


async def test_ambiguous_descendant_id_is_not_routed():
    runtime, transport, _ = await mount(view_class=AmbiguousRoot)
    root = runtime.view_instance
    leaves = [root._get_child_view(m)._get_child_view("leaf") for m in ("middle", "other-middle")]
    assert all(leaf is not None for leaf in leaves) and leaves[0] is not leaves[1]
    await send(runtime, "increment", "leaf")
    assert transport.errors[-1]["error"] == "Embedded view not found"
    assert [leaf.count for leaf in leaves] == [1, 1]


async def test_legacy_descendant_routing_is_unchanged():
    """Legacy control: legacy roots still route only to direct children."""
    runtime, transport, _ = await mount(view_class=LegacyRoot)
    middle = runtime.view_instance._get_child_view("legacy-middle")
    leaf = middle._get_child_view("legacy-leaf")
    await send(runtime, "poke", "legacy-middle")
    assert getattr(middle, "poked", False)
    await send(runtime, "poke", "legacy-leaf")
    assert not getattr(leaf, "poked", False)
    assert transport.errors[-1]["error"] == "Embedded view not found"
