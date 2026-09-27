"""#3231: component-event frames on an explicit view carry the refreshed client snapshot.

Since #3211/#3229 an event routed to a component (``component_id``) on an
explicit view commits the view's ``persist="server"`` fields before its frame.
The view route then adds the refreshed signed client snapshot
(``_explicit_event_snapshot``: the ``persist="client"`` fields) to its frame;
the component route did not. After a component event, storage held the server
fields at turn N+1 while the client still held the signed token from turn N,
so a reconnect restored a mix of the two turns that never existed.

The view here has one ``persist="server"`` and one ``persist="client"`` field,
and one of its components writes both through ``send_parent``. Each component frame
shape (full HTML, scoped patch, noop) must carry the token, and a reconnect with
the latest token the client received restores both fields at the same turn.

The views pass the real ``_validate_exposure_configuration``.
"""

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust import LiveView, event_handler
from djust.components.descriptors.base import LiveComponent as DescriptorComponent
from djust.components.descriptors.base import TypedState
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

TOKEN = "state_snapshot_signed"


class Pinger(DescriptorComponent):
    """State-less: its event writes the parent's fields through ``send_parent``
    and is answered with a full ``html_update``."""

    template = "<b>ping</b>"

    def mount(self, **kwargs):
        pass

    def get_context_data(self):
        return {}

    @event_handler()
    def step(self, **kwargs):
        self.send_parent("stepped")


class Stepper(DescriptorComponent):
    """A bound component (it declares ``State``): ``tick`` changes only its own
    state and takes the scoped patch route; ``idle`` changes nothing and is
    answered ``noop`` (ADR-032); ``leave`` changes nothing and queues a redirect."""

    class State(TypedState):
        n: int = 0

    template = "<b>{{ n }}</b>"

    @event_handler()
    def tick(self, **kwargs):
        self.state.n += 1

    @event_handler()
    def idle(self, **kwargs):
        pass

    @event_handler()
    def leave(self, **kwargs):
        self._view.live_redirect("/elsewhere/")


class TwoPersistencePage(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root>{{ pinger }}{{ stepper }}<span>{{ count }}</span><i>{{ step_name }}</i></div>"
    )
    count = state(0, persist="server")
    step_name = state("initial", persist="client", client=True)
    pinger = Pinger()
    stepper = Stepper()

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, step_name=self.step_name, **kwargs)

    def handle_component_event(self, component_id, event, data):
        self.count += 1
        self.step_name = "step-%d" % self.count


async def _mount(request, **extra):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": __name__ + ".TwoPersistencePage",
                "url": request.path,
                **extra,
            }
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    return runtime, transport


def _event(view, name):
    component = view.pinger if name == "step" else view.stepper
    return {
        "type": "event",
        "event": name,
        "params": {"component_id": component.component_id},
    }


def _latest_token(frames):
    """The token the client holds: the last one any frame delivered."""
    tokens = [frame[TOKEN] for frame in frames if TOKEN in frame]
    assert tokens, "no frame carried a signed snapshot"
    return tokens[-1]


async def test_reconnect_after_a_component_event_restores_both_fields_at_the_same_turn():
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance

    await runtime.dispatch_event(_event(view, "step"))
    assert not transport.errors, transport.errors
    assert (view.count, view.step_name) == (1, "step-1")

    token = _latest_token(transport.sent)
    fresh = await sync_to_async(make_request)(request.session.session_key)
    restored, _ = await _mount(
        fresh,
        state_snapshot={"view_slug": __name__ + ".TwoPersistencePage", "state_json": token},
    )
    assert (restored.view_instance.count, restored.view_instance.step_name) == (1, "step-1")


@pytest.mark.parametrize(
    "event_name,frame_type",
    [("step", "html_update"), ("tick", "patch"), ("idle", "noop")],
)
async def test_every_component_frame_carries_the_refreshed_snapshot(event_name, frame_type):
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance
    transport.sent.clear()

    await runtime.dispatch_event(_event(view, event_name))
    assert not transport.errors, transport.errors
    [frame] = [f for f in transport.sent if f.get("type") in {"html_update", "patch", "noop"}]
    # A component event answers ``patch`` only from the scoped branch.
    assert frame["type"] == frame_type
    assert frame.get(TOKEN), frame
    assert frame["view"] == __name__ + ".TwoPersistencePage"


async def test_a_component_noop_publishes_the_snapshot_before_its_navigation():
    """The view route sends an explicit noop, then the queued navigation, so the
    client stores the new token before a redirect triggers before-navigate.
    The component noop keeps that order."""
    request = await sync_to_async(make_request)()
    runtime, transport = await _mount(request)
    view = runtime.view_instance
    transport.sent.clear()

    await runtime.dispatch_event(_event(view, "leave"))
    assert not transport.errors, transport.errors
    types = [f.get("type") for f in transport.sent]
    assert types.index("noop") < types.index("navigation"), types
    assert transport.sent[types.index("noop")].get(TOKEN)
