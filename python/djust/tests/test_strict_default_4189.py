"""Strict event parameters are the project default (#4189).

A handler with no ``parameter_policy`` of its own now binds like a strict one,
through the real transports (``open_live_page``): the page carries the
parameter contracts, the mount frame lists the handler as strict, and an
argument its annotation cannot accept is refused instead of defaulted.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state
from djust.testing import open_live_page
from djust._parameter_metadata import expand_parameter_manifest

pytestmark = [pytest.mark.django_db(transaction=True)]

CALLS: list[bool] = []


class Toggle(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ on }}</p></div>"
    on = state(False)

    @event_handler()
    def flip(self, on: bool = False):
        CALLS.append(on)
        self.on = on


urlpatterns = [path("t4189/", Toggle.as_view())]


@pytest.fixture(autouse=True)
def _routes():
    CALLS.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        LIVEVIEW_ALLOWED_MODULES=[__name__.rsplit(".", 1)[0]],
        ASGI_APPLICATION=None,
    ):
        yield
    CALLS.clear()


async def test_an_unpinned_handler_is_strict_over_the_websocket():
    page = await sync_to_async(open_live_page)("/t4189/")
    async with await page.connect() as socket:
        owners = expand_parameter_manifest(socket.mount["parameter_contracts"])
        assert owners[0]["handlers"]["flip"]["policy"] == "strict"
        refused = await socket.send_event("flip", {"on": "maybe"})
        assert refused["type"] == "error", refused
        assert CALLS == []  # legacy would have called flip(on=False)
        answer = await socket.send_event("flip", {"on": "on"})
        assert answer["type"] != "error", answer
        assert CALLS == [True]


def test_the_http_page_carries_the_strict_contract():
    assert "data-djust-parameter-contracts" in open_live_page("/t4189/").html
