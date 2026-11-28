"""A view that declares a component answers its first socket event with patches.

The page GET renders the view, which binds its class-level component
descriptors, and saves the view's state with the components' state in it. The
socket mount that follows builds the view again and asks whether putting the
saved state back changes it (#1977, #4107); if it does, the first event answers
with the whole page. The mount compared before any component had bound, so a
view declaring one always compared unequal: its first event sent the whole page
and logged DJE-053, though nothing had changed. Found by
``make benchmark-components`` (the ``dashboard`` workload).
"""

from __future__ import annotations

import logging

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler, state
from djust.testing import open_live_page

pytestmark = [pytest.mark.django_db(transaction=True)]


class Badge(LiveComponent):
    class State(TypedState):
        label: str = "new"

    template = "<em>{{ label }}</em>"


class _Counter(LiveView):
    n = state(0)

    @event_handler()
    def tick(self, **kwargs):
        self.n += 1


class Plain(_Counter):
    template = "<div dj-root><b>{{ n }}</b></div>"


class Rendered(_Counter):
    template = "<div dj-root><b>{{ n }}</b>{{ badge }}</div>"
    badge = Badge()


class Unrendered(_Counter):
    template = "<div dj-root><b>{{ n }}</b></div>"
    badge = Badge()


urlpatterns = [
    path("t-plain/", Plain.as_view()),
    path("t-rendered/", Rendered.as_view()),
    path("t-unrendered/", Unrendered.as_view()),
]


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        LIVEVIEW_ALLOWED_MODULES=[__name__.rsplit(".", 1)[0]],
        ASGI_APPLICATION=None,
    ):
        yield


@pytest.mark.parametrize("url", ["/t-plain/", "/t-rendered/", "/t-unrendered/"])
async def test_the_first_event_after_the_page_load_answers_with_patches(url, caplog):
    page = await sync_to_async(open_live_page)(url)
    caplog.set_level(logging.WARNING)
    async with await page.connect() as socket:
        for expected in ("1", "2"):
            answer = await socket.send_event("tick", {})
            assert answer["type"] == "render", answer
            assert "patches" in answer and "html" not in answer, answer
            assert any(p.get("text") == expected for p in answer["patches"]), answer
    assert "DJE-053" not in caplog.text


async def test_a_changed_component_state_still_answers_with_the_whole_page():
    """The comparison still sees a real difference: state the page saved that
    the socket mount does not build (here, a component label changed after the
    GET) makes the first event a whole page, as #1977 requires."""
    from djust import _automatic_state

    page = await sync_to_async(open_live_page)("/t-rendered/")
    real = _automatic_state._encoded_components

    def changed(found, mine):
        out = real(found, mine)
        return {name: {**values, "label": "edited"} for name, values in out.items()}

    _automatic_state._encoded_components = changed
    try:
        async with await page.connect() as socket:
            answer = await socket.send_event("tick", {})
    finally:
        _automatic_state._encoded_components = real
    assert answer["type"] == "render" and "html" in answer, answer
