"""#4092: ``persist="server"`` state under a session engine that cannot hold it.

A view declaring ``state(..., persist="server")`` answered a bare 500 under
``SESSION_ENGINE = "...signed_cookies"``, logged as "Protected view operation
failed", and nothing reported the combination at startup. The combination
cannot be made to work safely: server state is kept in the server-side session
precisely so it never travels in the cookie. So:

* system check ``djust.C030`` reports it, naming the supported engines;
* the runtime refusal is a configuration error
  (``UnsupportedSessionEngine``, an ``ExposureConfigurationError``) that names
  the engine found and the supported ones, on the GET, the HTTP fallback POST
  and in the socket transport's log.
"""

from __future__ import annotations

import asyncio
import json
import logging
from importlib import import_module

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings

from djust import LiveView, state
from djust._exposure import ExposureConfigurationError, ExposureError
from djust._exposure_sessions import SUPPORTED_SESSION_ENGINES, UnsupportedSessionEngine
from djust.decorators import event_handler

COOKIE_ENGINE = "django.contrib.sessions.backends.signed_cookies"
DB_ENGINE = "django.contrib.sessions.backends.db"
MOD = __name__


class ServerCounter(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-view="' + MOD + '.ServerCounter">n={{ n }}</div>'
    n = state(0, persist="server")

    @event_handler
    def inc(self, **kwargs):
        self.n += 1

    def get_context_data(self, **kwargs):
        return {"n": self.n}


def _request(method="get"):
    from django.conf import settings

    factory = RequestFactory()
    if method == "get":
        request = factory.get("/counter/")
    else:
        request = factory.post(
            "/counter/",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="inc",
        )
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    request.user = AnonymousUser()
    return request


def _c030(engine):
    from djust.checks.configuration import _check_server_state_session_engine

    errors = []
    with override_settings(SESSION_ENGINE=engine):
        _check_server_state_session_engine(errors)
    return [e for e in errors if e.id == "djust.C030"]


def test_the_check_reports_server_state_under_signed_cookies():
    from djust._exposure import ExposureContract
    from djust.checks.configuration import _views_declaring_server_state

    (error,) = _c030(COOKIE_ENGINE)
    assert error.level >= 40  # Error
    assert "signed_cookies" in error.msg and "declared by" in error.msg
    assert MOD + ".ServerCounter" in _views_declaring_server_state(LiveView, ExposureContract)
    for engine in SUPPORTED_SESSION_ENGINES:
        assert engine in error.hint


@pytest.mark.parametrize("engine", SUPPORTED_SESSION_ENGINES)
def test_the_check_is_quiet_for_a_supported_engine(engine):
    assert _c030(engine) == []


def test_the_check_reports_a_subclass_engine_too():
    """The runtime accepts only the exact audited stores; so does the check."""
    assert _c030("myproject.sessions.custom_db")


def test_the_engine_list_is_the_runtime_list():
    from djust._exposure_sessions import _SERVER_SESSION_TYPES

    assert SUPPORTED_SESSION_ENGINES == tuple(t.__module__ for t in _SERVER_SESSION_TYPES)
    assert DB_ENGINE in SUPPORTED_SESSION_ENGINES and COOKIE_ENGINE not in SUPPORTED_SESSION_ENGINES


@pytest.mark.django_db
@override_settings(SESSION_ENGINE=COOKIE_ENGINE, DEBUG=False)
@pytest.mark.parametrize("method", ["get", "post"])
def test_the_http_refusal_names_the_engine_not_a_generic_500(method):
    with pytest.raises(UnsupportedSessionEngine) as raised:
        ServerCounter.as_view()(_request(method))
    message = str(raised.value)
    assert "signed_cookies" in message
    for engine in SUPPORTED_SESSION_ENGINES:
        assert engine in message
    # Django's ImproperlyConfigured family, and still an ExposureError for callers.
    assert isinstance(raised.value, ExposureConfigurationError)
    assert isinstance(raised.value, ExposureError)


@pytest.mark.django_db
@override_settings(SESSION_ENGINE=DB_ENGINE, DEBUG=False)
def test_the_db_engine_still_serves_the_view():
    response = ServerCounter.as_view()(_request("get"))
    assert response.status_code == 200


def test_a_protected_log_line_carries_a_configuration_reason(caplog):
    """The socket transports log through ``log_failure``: a configuration error
    keeps its framework-authored reason when details are not allowed."""
    from djust._exposure_diagnostics import PROTECTED_FAILURE, log_failure_for

    log = logging.getLogger("djust.tests.c030")
    view = ServerCounter()
    with caplog.at_level(logging.WARNING, logger="djust.tests.c030"):
        log_failure_for(log, (view,), UnsupportedSessionEngine("the reason"), "save failed")
        log_failure_for(log, (view,), ValueError("secret value"), "save failed")
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0] == "%s: the reason" % PROTECTED_FAILURE
    assert messages[1] == PROTECTED_FAILURE


pytest.importorskip("channels")


@pytest.mark.django_db(transaction=True)
def test_the_socket_mount_is_refused_with_the_reason_logged(caplog):
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.signed_cookies import SessionStore

    from djust.websocket import LiveViewConsumer

    async def run():
        communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        communicator.scope["session"] = SessionStore()
        communicator.scope["user"] = AnonymousUser()
        connected, _ = await communicator.connect()
        assert connected
        await communicator.receive_json_from()  # connect ack
        await communicator.send_json_to(
            {"type": "mount", "view": MOD + ".ServerCounter", "url": "/counter/"}
        )
        frames = []
        while True:
            frame = await communicator.receive_json_from(timeout=15)
            frames.append(frame)
            if frame.get("type") in ("mount", "error"):
                break
        await communicator.disconnect()
        return frames

    with (
        override_settings(
            LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False, SESSION_ENGINE=COOKIE_ENGINE
        ),
        caplog.at_level(logging.WARNING),
    ):
        frames = asyncio.run(run())
    assert frames[-1]["type"] == "error", frames
    assert any("signed_cookies" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]
