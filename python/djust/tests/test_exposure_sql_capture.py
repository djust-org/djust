"""ADR-038 D-d: observability SQL capture redacts parameters for explicit views.

``observability/sql.py`` records every query a WebSocket event turn runs,
including its raw parameters, and the DEBUG-only, localhost-only
``/_djust/observability/sql_queries/`` endpoint serves them. Parameters are
often derived from view state. For a nonlegacy owner they are now redacted;
the SQL text, tags and timing stay. Legacy views are unchanged.

Driven through a real WebSocket mount and event; the assertion reads the
endpoint's response body under DEBUG. The handler is async and queries on the
event-loop thread because the turn installs its ``execute_wrapper`` on that
thread's connection: queries from a sync handler run on a ``sync_to_async``
worker thread's connection and are not captured at all (a pre-existing gap in
the runtime's capture, outside this slice). Two direct cases pin each
redaction trigger on its own: the capture's owner, and the diagnostic scope.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.db import connection
from django.test import RequestFactory, override_settings

from djust import LiveView, event_handler
from djust._exposure_diagnostics import diagnostic_scope, restrict_diagnostics
from djust.observability import sql as sql_capture
from djust.observability.views import sql_queries
from djust.tests.test_exposure_runtime import make_request
from djust.websocket import LiveViewConsumer

SENTINEL = "SQL_PARAM_SENTINEL"
CALLS = []


class SqlView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root>sql</div>"

    @event_handler()
    async def lookup(self):
        # Queries on the event-loop thread, where the turn's capture wrapper is
        # installed (see the module docstring).
        CALLS.append(True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s AS adr038_probe", [SENTINEL])
        self._skip_render = True


def _endpoint_body():
    response = sql_queries(RequestFactory().get("/_djust/observability/sql_queries/"))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_sql_capture_params_through_ws_event(monkeypatch, policy):
    CALLS.clear()
    sql_capture._clear_queries()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(SqlView, "exposure_policy", policy)
    monkeypatch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    with override_settings(
        LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DEBUG=True, DJUST_TENANTS=None, DJUST_CONFIG={}
    ):
        request = await sync_to_async(make_request)()
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=request.session, user=request.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {"type": "mount", "view": __name__ + ".SqlView", "url": request.path}
            )
            mounted = await socket.receive_json_from(timeout=3)
            assert mounted["type"] == "mount", mounted
            await socket.send_json_to({"type": "event", "event": "lookup", "params": {}, "ref": 5})
            await socket.receive_json_from(timeout=3)
        finally:
            await socket.disconnect()
        body = await sync_to_async(_endpoint_body)()
    assert CALLS, "the handler never ran; the test would be vacuous"
    entries = [e for e in json.loads(body)["entries"] if "adr038_probe" in e["sql"]]
    assert len(entries) == 1, body
    if policy == "legacy":
        assert entries[0]["params"] == [SENTINEL]
    else:
        assert SENTINEL not in body
        assert entries[0]["params"] == ["[redacted]"]


class _Owner:
    def __init__(self, policy):
        self.exposure_policy = policy


@pytest.mark.django_db
@pytest.mark.parametrize("where", ["legacy", "explicit"])
def test_sql_capture_redacts_under_restricted_diagnostic_scope(where):
    """With no owner passed to the capture, the active diagnostic scope decides."""
    sql_capture._clear_queries()
    with diagnostic_scope():
        restrict_diagnostics(_Owner(where))
        with sql_capture.capture_for_event(session_id="s", handler_name="h"):
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s AS adr038_scope_probe", [SENTINEL])
    with override_settings(DEBUG=True):
        body = _endpoint_body()
    entries = [e for e in json.loads(body)["entries"] if "adr038_scope_probe" in e["sql"]]
    assert len(entries) == 1, body
    assert (SENTINEL in body) == (where == "legacy")


@pytest.mark.django_db
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_sql_capture_redacts_for_nonlegacy_owner_outside_any_scope(policy):
    """The owner passed to the capture decides even with no diagnostic scope."""
    sql_capture._clear_queries()
    with sql_capture.capture_for_event(session_id="s", owner=_Owner(policy)):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s AS adr038_owner_probe", [SENTINEL])
    with override_settings(DEBUG=True):
        body = _endpoint_body()
    entries = [e for e in json.loads(body)["entries"] if "adr038_owner_probe" in e["sql"]]
    assert len(entries) == 1, body
    assert entries[0]["params"] == ([SENTINEL] if policy == "legacy" else ["[redacted]"])
