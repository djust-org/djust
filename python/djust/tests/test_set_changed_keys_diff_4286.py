"""#4286 — ``set_changed_keys()`` forces the render, not a whole page.

Before the fix both forms set ``_force_full_html = True``, which also meant
"discard the patches": every such turn was answered with ``html_update``
(and DJE-053), and the client's morph stripped ``open`` from an open
``{% modal native=True %}``, closing it at the first key typed. Now the call
sets the truthy ``RENDER_REQUESTED`` sentinel: it bypasses every skip but the
render is diffed and sent as a ``patch``. ``request_full_render()`` and a
framework ``True`` still send full HTML.

Every test drives the real render path (the runtime event spine on a mounted
explicit view, and the real ``LiveViewConsumer`` tick) and asserts on the
frames the client receives.
"""

from __future__ import annotations

import json

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.mixins.rust_bridge import RENDER_REQUESTED, full_html_requested
from djust.tests.test_transport_behavioral_parity import _mounted_event_runtime

_HERE = "djust.tests.test_set_changed_keys_diff_4286"


def _updates(sent):
    return [f for f in sent if f.get("type") in ("patch", "html_update")]


def _patch_text(frame):
    return json.dumps(frame.get("patches"))


class Box:
    """An opaque object: change detection cannot see an attribute write."""

    def __init__(self, text=""):
        self.text = text


class Row:
    """Model-like: compared by identity, so an in-place write is invisible."""

    def __init__(self, name):
        self.name = name


class NoteView(LiveView):
    """The issue's repro: a native modal and an opaque ``_box``."""

    exposure_policy = "explicit"
    template = (
        "{% load djust_components %}"
        f'<div dj-root dj-view="{_HERE}.NoteView">'
        '{% modal id="m" open=True native=True title="Ask" close_event="close" %}'
        '<textarea dj-input="typed"></textarea><p id="count">{{ count }}</p>'
        "{% endmodal %}</div>"
    )

    def mount(self, request, **kwargs):
        self._box = Box()
        self._rows = [Row("alpha")]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["count"] = len(self._box.text)
        context["rows"] = [r.name for r in self._rows]
        return context

    @event_handler()
    def typed(self, value: str = "", **kwargs):
        self._box.text = value
        self.set_changed_keys()

    @event_handler()
    def rename(self, name: str = "", **kwargs):
        self._rows[0].name = name
        self.set_changed_keys("rows")

    @event_handler()
    def reload_all(self, value: str = "", **kwargs):
        self._box.text = value
        self.request_full_render()

    @event_handler()
    def typed_quietly(self, value: str = "", **kwargs):
        self._box.text = value
        self.set_changed_keys()
        self.skip_render()

    @event_handler()
    def close(self, **kwargs):
        pass


class RowsView(LiveView):
    """The keyed form: the template reads the renamed row."""

    exposure_policy = "explicit"
    template = (
        f'<div dj-root dj-view="{_HERE}.RowsView">'
        '<ul>{% for r in rows %}<li class="row">{{ r }}</li>{% endfor %}</ul></div>'
    )

    def mount(self, request, **kwargs):
        self._rows = [Row("alpha"), Row("beta")]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rows"] = [r.name for r in self._rows]
        return context

    @event_handler()
    def rename(self, name: str = "", **kwargs):
        self._rows[0].name = name
        self.set_changed_keys("rows")


class TickBoxView(LiveView):
    """The tick path: ``handle_tick`` writes an opaque object."""

    exposure_policy = "explicit"
    tick_interval = 50
    template = f'<div dj-root dj-view="{_HERE}.TickBoxView"><p id="n">{{{{ n }}}}</p></div>'

    def mount(self, request, **kwargs):
        self._box = Box()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["n"] = len(self._box.text)
        return context

    def handle_tick(self):
        self._box.text += "x"
        self.set_changed_keys()


async def _dispatch(view, event, **params):
    runtime, transport = await _mounted_event_runtime(view)
    transport.sent.clear()
    await runtime.dispatch_event({"type": "event", "event": event, "params": params})
    return runtime.view_instance, transport.sent


class TestSentinel:
    def test_both_forms_request_a_render_not_a_full_page(self):
        view = NoteView()
        view.set_changed_keys()
        assert view._force_full_html == RENDER_REQUESTED
        assert not full_html_requested(view)
        view2 = NoteView()
        view2.set_changed_keys("rows")
        assert view2._force_full_html == RENDER_REQUESTED

    def test_request_full_render_is_a_full_page(self):
        view = NoteView()
        view.request_full_render()
        assert view._force_full_html is True
        assert full_html_requested(view)

    def test_a_pending_full_page_is_never_downgraded(self):
        view = NoteView()
        view._force_full_html = True
        view.set_changed_keys()
        assert view._force_full_html is True

    def test_cancel_restores_the_raw_prior(self):
        view = NoteView()
        view._force_full_html = True
        view.set_changed_keys()
        view.cancel_full_render()
        assert view._force_full_html is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestEventSpine:
    async def test_zero_arg_form_sends_a_patch(self):
        view, sent = await _dispatch(NoteView(), "typed", value="Wh")
        frames = _updates(sent)
        assert [f["type"] for f in frames] == ["patch"], sent
        assert '"2"' in _patch_text(frames[0]), frames[0]
        assert not view.__dict__.get("_force_full_html"), "consumed by the render"

    async def test_keyed_form_sends_a_patch_with_the_new_text(self):
        _view, sent = await _dispatch(RowsView(), "rename", name="gamma")
        frames = _updates(sent)
        assert [f["type"] for f in frames] == ["patch"], sent
        assert "gamma" in _patch_text(frames[0]), frames[0]

    async def test_request_full_render_still_sends_full_html(self):
        _view, sent = await _dispatch(NoteView(), "reload_all", value="Wh")
        assert [f["type"] for f in _updates(sent)] == ["html_update"], sent

    async def test_a_framework_full_page_stays_a_full_page(self):
        """Upgrade only: a restore set ``True`` before dispatch."""
        runtime, transport = await _mounted_event_runtime(NoteView())
        transport.sent.clear()
        runtime.view_instance._force_full_html = True
        await runtime.dispatch_event({"type": "event", "event": "typed", "params": {"value": "W"}})
        assert [f["type"] for f in _updates(transport.sent)] == ["html_update"], transport.sent

    async def test_set_changed_keys_wins_over_skip_render(self):
        """#2834 precedence holds for the sentinel."""
        _view, sent = await _dispatch(NoteView(), "typed_quietly", value="abc")
        frames = _updates(sent)
        assert [f["type"] for f in frames] == ["patch"], sent
        assert '"3"' in _patch_text(frames[0]), frames[0]


async def _consumer_with_view(view_class):
    from djust.tests._live_session import amount_on_consumer
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.sent = []

    async def _capture(payload):
        consumer.sent.append(payload)

    consumer.send_json = _capture
    await amount_on_consumer(consumer, f"{_HERE}.{view_class.__name__}")
    return consumer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_tick_that_calls_set_changed_keys_sends_a_patch():
    """The parallel-path pin (#1646): the tick renders and diffs too."""
    consumer = await _consumer_with_view(TickBoxView)
    consumer.sent.clear()
    await consumer._tick_once()
    frames = _updates(consumer.sent)
    assert [f["type"] for f in frames] == ["patch"], consumer.sent
    assert '"1"' in _patch_text(frames[0]), frames[0]
    assert not consumer.view_instance.__dict__.get("_force_full_html")
