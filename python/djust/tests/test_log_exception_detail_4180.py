"""``LIVEVIEW_CONFIG["log_exception_detail"]``: exception detail to server sinks only (#4180).

In production an explicit view's failures are value-free everywhere by
default. The opt-in keeps the exception text and traceback in the server log
and sends Django's ``got_request_exception`` (Sentry's hook) on the WebSocket,
SSE and the HTTP fallback, and for the HTTP page view. Client frames and HTTP
responses stay generic either way.
"""

from __future__ import annotations

import json
import logging
import sys

import pytest
from asgiref.sync import sync_to_async
from django.core.signals import got_request_exception
from django.test import Client, override_settings
from django.urls import include, path

from djust import LiveView
from djust._exposure_diagnostics import log_failure_for, server_exception_detail_enabled
from djust.decorators import event_handler
from djust.sse import sse_urlpatterns
from djust.testing import open_live_page

pytestmark = [pytest.mark.django_db(transaction=True)]

SECRET = "SECRET_4180"


class Boom(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>boom</div>"

    @event_handler
    def boom(self, **kwargs):
        raise ValueError(SECRET)


class GetBoom(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>never</div>"

    def mount(self, request, **kwargs):
        raise ValueError(SECRET)


urlpatterns = [
    path("t/boom/", Boom.as_view()),
    path("t/getboom/", GetBoom.as_view()),
    path("djust/", include(sse_urlpatterns)),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__.rsplit(".", 1)[0]],
}
OPT_IN = {"log_exception_detail": True}


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield


@pytest.fixture
def received():
    """The exceptions ``got_request_exception`` receivers saw (``sys.exc_info()``)."""
    seen: list = []

    def receiver(sender, request=None, **kwargs):
        seen.append(sys.exc_info()[1])

    got_request_exception.connect(receiver, weak=False)
    try:
        yield seen
    finally:
        got_request_exception.disconnect(receiver)


def _logged(caplog) -> str:
    formatter = logging.Formatter()
    return "\n".join(formatter.format(record) for record in caplog.records)


def _page():
    return open_live_page(
        "/t/boom/", client=Client(enforce_csrf_checks=True, raise_request_exception=False)
    )


async def _fail(transport: str):
    """Make ``boom`` fail over ``transport``; return the client answer."""
    page = await sync_to_async(_page)()
    if transport == "fallback":
        response = await sync_to_async(page.post_event)("boom")
        return response.content.decode()
    if transport == "ws":
        async with await page.connect() as socket:
            return await socket.send_event("boom")
    async with await page.connect_sse() as stream:
        stream._client.raise_request_exception = False
        return await stream.send_event("boom")


TRANSPORTS = ["ws", "sse", "fallback"]


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_opt_in_logs_detail_and_signals_but_the_client_answer_stays_generic(
    transport, caplog, received
):
    caplog.set_level(logging.DEBUG)
    with override_settings(LIVEVIEW_CONFIG=OPT_IN):
        answer = await _fail(transport)
    logged = _logged(caplog)
    assert SECRET in logged and "Traceback" in logged, logged
    assert any(isinstance(exc, ValueError) and str(exc) == SECRET for exc in received), received
    text = json.dumps(answer)
    assert SECRET not in text and "Traceback" not in text, text


@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_without_the_opt_in_nothing_changes(transport, caplog, received):
    caplog.set_level(logging.DEBUG)
    answer = await _fail(transport)
    assert SECRET not in _logged(caplog)
    assert received == []
    assert SECRET not in json.dumps(answer)


def test_djust_config_spelling_enables_it():
    with override_settings(DJUST_CONFIG=OPT_IN):
        assert server_exception_detail_enabled() is True


def test_string_false_is_false():
    with override_settings(LIVEVIEW_CONFIG={"log_exception_detail": "false"}):
        assert server_exception_detail_enabled() is False
    assert server_exception_detail_enabled() is False


def test_http_page_view_opt_in(caplog, received):
    caplog.set_level(logging.DEBUG)
    with override_settings(LIVEVIEW_CONFIG=OPT_IN):
        response = Client(raise_request_exception=False).get("/t/getboom/")
    assert response.status_code == 500
    assert SECRET not in response.content.decode()
    logged = _logged(caplog)
    assert SECRET in logged and "Traceback" in logged, logged
    assert len(received) == 1 and isinstance(received[0], ValueError), received


def test_http_page_view_without_the_opt_in(caplog, received):
    from djust._exposure import ExposureError

    caplog.set_level(logging.DEBUG)
    response = Client(raise_request_exception=False).get("/t/getboom/")
    assert response.status_code == 500
    assert SECRET not in response.content.decode()
    assert SECRET not in _logged(caplog)
    assert len(received) == 1 and isinstance(received[0], ExposureError), received


def test_log_failure_opt_in(caplog):
    log = logging.getLogger("djust.tests.4180")
    view = Boom()
    exc = ValueError(SECRET)
    caplog.set_level(logging.DEBUG)
    log_failure_for(log, (view,), exc, "x %s", exc, traceback=True)
    assert SECRET not in _logged(caplog)
    caplog.clear()
    with override_settings(LIVEVIEW_CONFIG=OPT_IN):
        log_failure_for(log, (view,), exc, "x %s", exc, traceback=True)
    assert SECRET in _logged(caplog)
