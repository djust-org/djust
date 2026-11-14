"""#4037: a component tag's default event has a framework answer.

The component mixins (``ModalMixin``, ``SheetMixin``, ``CarouselMixin``,
``DropdownMixin``, ``TooltipMixin``) used to answer the defaults the tags emit
(``close_modal``, ``close_sheet``, ``carousel_prev`` / ``carousel_next`` ...).
They were removed (#4025); the descriptors now answer them through
``Meta.events`` + ``@event_handler`` methods, with a view-level alias so an
event without ``component_id`` still reaches the single instance.

Each tag is rendered through both engines and every ``dj-click`` it draws by
default is sent to a view that declares only the descriptor.
"""

from __future__ import annotations

import re

import pytest

from djust import LiveView
from djust.components.base import BoundComponent
from djust.components.descriptors import Carousel, Dropdown, Modal, Sheet, Tooltip
from djust.decorators import is_event_handler
from djust.testing import LiveViewTestClient
from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

_ENGINES = {"django": _django, "rust": _rust}


class ModalPage(LiveView):
    exposure_policy = "legacy"
    dialog = Modal(is_open=True)

    def mount(self, request, **kwargs):
        pass


class SheetPage(LiveView):
    exposure_policy = "legacy"
    panel = Sheet(is_open=True)

    def mount(self, request, **kwargs):
        pass


class CarouselPage(LiveView):
    exposure_policy = "legacy"
    slides = Carousel(total=3)

    def mount(self, request, **kwargs):
        pass


def _clicks(html: str) -> set:
    return set(re.findall(r'dj-click="([^"]+)"', html))


@pytest.mark.parametrize("engine", ["rust", "django"])
def test_modal_default_close_event_closes_the_modal(engine):
    client = LiveViewTestClient(ModalPage).mount()
    clicks = _clicks(_ENGINES[engine]('{% modal id="m" title="T" open=True %}body{% endmodal %}'))
    assert "close_modal" in clicks
    for event in clicks:
        assert client.send_event(event)["success"], event
    assert client.view_instance.dialog.is_open is False


@pytest.mark.parametrize("engine", ["rust", "django"])
def test_sheet_default_close_event_closes_the_sheet(engine):
    client = LiveViewTestClient(SheetPage).mount()
    clicks = _clicks(_ENGINES[engine]('{% sheet title="T" open=True %}body{% endsheet %}'))
    assert clicks == {"close_sheet"}
    assert client.send_event("close_sheet")["success"]
    assert client.view_instance.panel.is_open is False


@pytest.mark.parametrize("engine", ["rust", "django"])
def test_carousel_default_prev_next_events_step_and_wrap(engine):
    client = LiveViewTestClient(CarouselPage).mount()
    clicks = _clicks(
        _ENGINES[engine]("{% carousel images=images %}", {"images": ["/a.png", "/b.png", "/c.png"]})
    )
    assert {"carousel_prev", "carousel_next", "carousel_go"} <= clicks
    view = client.view_instance
    assert client.send_event("carousel_next")["success"]
    assert view.slides.active == 1
    assert client.send_event("carousel_prev")["success"]
    assert client.send_event("carousel_prev")["success"]
    assert view.slides.active == 2  # wrapped
    assert client.send_event("carousel_go", value="0")["success"]
    assert view.slides.active == 0


@pytest.mark.parametrize(
    "descriptor, event, attr, start, end",
    [
        (Modal, "open_modal", "is_open", False, True),
        (Modal, "close_modal", "is_open", True, False),
        (Sheet, "open_sheet", "is_open", False, True),
        (Sheet, "close_sheet", "is_open", True, False),
        (Dropdown, "close_dropdown", "is_open", True, False),
        (Tooltip, "show_tooltip", "is_visible", False, True),
        (Tooltip, "hide_tooltip", "is_visible", True, False),
    ],
)
def test_each_default_event_is_answered_by_alias_and_by_the_bound_component(
    descriptor, event, attr, start, end
):
    page = type(
        "Page_%s" % event,
        (LiveView,),
        {"exposure_policy": "legacy", "first": descriptor(**{attr: start})},
    )
    # The view-level alias, auto-resolving the single instance.
    view = page()
    assert is_event_handler(getattr(type(view), event))
    getattr(view, event)()
    assert getattr(view.first, attr) is end

    # With component_id: the runtime resolves the bound component and calls
    # its @event_handler method.
    view = page()
    bound = view.first
    assert isinstance(bound, BoundComponent)
    handler = getattr(bound, event)
    assert is_event_handler(handler)
    handler()
    assert getattr(view.first, attr) is end


def test_alias_needs_one_instance_or_a_component_id():
    class TwoModals(LiveView):
        exposure_policy = "legacy"
        a = Modal(is_open=True)
        b = Modal(is_open=True)

    view = TwoModals()
    view.close_modal()
    assert (view.a.is_open, view.b.is_open) == (True, True)
    view.close_modal(component_id="b")
    assert (view.a.is_open, view.b.is_open) == (True, False)


def test_an_app_handler_of_the_same_name_wins():
    calls = []

    from djust.decorators import event_handler

    class Own(LiveView):
        exposure_policy = "legacy"
        dialog = Modal(is_open=True)

        @event_handler
        def close_modal(self, **kwargs):
            calls.append("own")

    view = Own()
    view.close_modal()
    assert calls == ["own"]
    assert view.dialog.is_open is True


class RuntimeModalPage(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0"><p>{{ dialog.is_open }}</p></div>'
    dialog = Modal(is_open=True)

    def mount(self, request, **kwargs):
        pass


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"component_id": "dialog"}, {}])
async def test_the_runtime_dispatches_the_tag_event_with_and_without_component_id(params):
    """Through ``ViewRuntime.dispatch_event``: with ``component_id`` the event
    resolves the bound component's ``@event_handler`` method (the D4 path);
    without it, the view-level alias. Neither is an error frame."""
    from djust.runtime import ViewRuntime
    from djust.tests.test_bound_component_skip_gate_2900 import MockTransport

    view = RuntimeModalPage()
    view.mount(None)
    view.render_with_diff()
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    await runtime.dispatch_event(
        {"type": "event", "event": "close_modal", "params": params, "ref": 1}
    )
    types = [f.get("type") for f in transport.sent]
    assert "error" not in types, transport.sent
    assert view.dialog.is_open is False
