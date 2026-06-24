"""
Tests for SSE-transport CSRF defense via the Origin allowlist (Finding #7, CWE-352).

The SSE client→server endpoints are ``@csrf_exempt``. Without an Origin check a
cross-origin page can drive a victim-cookie-authenticated SSE session: force the
victim's browser to GET ``/djust/sse/<attacker-uuid>/?view=...`` (mounts the view
as the victim via cookies), then POST to ``/djust/sse/<attacker-uuid>/message/``
with ``credentials: include`` to fire state-changing event handlers as the victim.

The fix mirrors the WebSocket transport's CSWSH defense (#653): every SSE endpoint
(``DjustSSEStreamView.get``, ``DjustSSEEventView.post``, ``DjustSSEMessageView.post``)
validates the ``Origin`` header against ``settings.ALLOWED_HOSTS`` via the shared
``djust.websocket._is_allowed_origin`` helper BEFORE creating/mounting a session or
dispatching an event. A browser always sends ``Origin`` on cross-origin requests,
so an attacker page's origin won't match ALLOWED_HOSTS; same-origin requests pass;
non-browser clients (no Origin) still work.

Defense-in-depth: the POST endpoints also require ``Content-Type: application/json``
(415 otherwise), closing the CORS simple-request ``text/plain`` bypass (a JSON body
sent with ``text/plain`` is a CORS "simple request" that needs no preflight).
"""

import json
import uuid

import pytest

import django
from django.conf import settings

# Configure Django if not already done (mirrors python/tests/test_sse.py).
if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
            "django.contrib.messages",
        ],
        SECRET_KEY="test-secret-key-for-sse-csrf-tests",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from django.test import RequestFactory, override_settings  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from djust.sse import (  # noqa: E402
    SSESession,
    DjustSSEStreamView,
    DjustSSEEventView,
    DjustSSEMessageView,
    _sse_sessions,
)


# A host that is NOT in the overridden ALLOWED_HOSTS — simulates an attacker page.
FOREIGN_ORIGIN = "https://attacker.example"
# A host that IS in the overridden ALLOWED_HOSTS — simulates the legitimate page.
ALLOWED_ORIGIN = "https://example.com"


# Owner pk the mock sessions are bound to (Finding #24 owner-binding). The
# happy-path requests below stamp request.user with this same pk via
# _attach_owner() so they pass the owner check; cross-user/anonymous requests
# do not, so they are 403 (the security boundary, exercised in
# test_sse_session_binding_f24_f25.py).
_OWNER_PK = 4242


def _attach_owner(request, pk=_OWNER_PK):
    """Stamp request.user so the request owns a _make_mounted_session()."""
    request.user = MagicMock(is_authenticated=True, pk=pk)
    return request


def _make_mounted_session() -> SSESession:
    """Create + register an SSE session with a mounted (mock) view + runtime.

    Bound to _OWNER_PK so the owner-binding check (Finding #24) passes for
    requests stamped via _attach_owner().
    """
    from djust.runtime import ViewRuntime, SSESessionTransport

    sid = str(uuid.uuid4())
    session = SSESession(sid)
    session.view_instance = MagicMock()  # marked as mounted
    session.runtime = ViewRuntime(SSESessionTransport(session))
    session.runtime.view_instance = session.view_instance
    session._owner_user_pk = _OWNER_PK
    _sse_sessions[sid] = session
    return session


@pytest.fixture(autouse=True)
def _clear_sessions():
    _sse_sessions.clear()
    yield
    _sse_sessions.clear()


# ---------------------------------------------------------------------------
# GET stream endpoint
# ---------------------------------------------------------------------------


class TestSSEStreamOrigin:
    """``DjustSSEStreamView.get`` must reject cross-origin GET handshakes."""

    def setup_method(self):
        self.factory = RequestFactory()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_foreign_origin_get_rejected_with_403(self):
        """A cross-origin GET (foreign Origin) must be 403 — and must NOT create
        a session (the CSRF: an attacker page mounting a view as the victim)."""
        sid = str(uuid.uuid4())
        request = self.factory.get(
            f"/djust/sse/{sid}/",
            {"view": "nonexistent.View"},
            HTTP_ORIGIN=FOREIGN_ORIGIN,
        )
        view = DjustSSEStreamView()
        response = await view.get(request, session_id=sid)

        assert response.status_code == 403
        # The session must NOT have been registered/mounted.
        assert sid not in _sse_sessions

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_same_origin_get_not_403(self):
        """A same-origin GET (Origin in ALLOWED_HOSTS) reaches normal handling."""
        sid = str(uuid.uuid4())
        request = self.factory.get(
            f"/djust/sse/{sid}/",
            {"view": "nonexistent.View"},
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        view = DjustSSEStreamView()
        response = await view.get(request, session_id=sid)
        # nonexistent.View pushes an error to the queue but the stream still
        # establishes (200 text/event-stream). Just assert it is NOT 403.
        assert response.status_code != 403

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_missing_origin_get_allowed(self):
        """Non-browser client (no Origin) is allowed — must not break curl/tests."""
        sid = str(uuid.uuid4())
        request = self.factory.get(f"/djust/sse/{sid}/", {"view": "nonexistent.View"})
        view = DjustSSEStreamView()
        response = await view.get(request, session_id=sid)
        assert response.status_code != 403


# ---------------------------------------------------------------------------
# POST message endpoint (the primary CSRF sink)
# ---------------------------------------------------------------------------


class TestSSEMessageOrigin:
    """``DjustSSEMessageView.post`` must reject cross-origin POSTs."""

    def setup_method(self):
        self.factory = RequestFactory()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_foreign_origin_post_rejected_with_403(self):
        """A cross-origin POST (foreign Origin) firing an event must be 403 and
        must NOT reach the runtime dispatch — this is the core CSRF reproduction.

        GATE-OFF EVIDENCE: before the Origin check existed this returned 200 and
        ``dispatch_message`` ran (the attacker fired an event as the victim).
        """
        session = _make_mounted_session()
        session.runtime.dispatch_message = AsyncMock(return_value=None)

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "event", "event": "delete_account", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=FOREIGN_ORIGIN,
        )
        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 403
        session.runtime.dispatch_message.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_same_origin_post_not_403(self):
        """A same-origin POST reaches normal handling (dispatch runs)."""
        session = _make_mounted_session()
        session.runtime.dispatch_message = AsyncMock(return_value=None)

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "event", "event": "increment", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        _attach_owner(request)
        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        session.runtime.dispatch_message.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_missing_origin_post_allowed(self):
        """Non-browser client (no Origin) is allowed (not 403)."""
        session = _make_mounted_session()
        session.runtime.dispatch_message = AsyncMock(return_value=None)

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "event", "event": "increment", "params": {}}),
            content_type="application/json",
        )
        _attach_owner(request)
        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        session.runtime.dispatch_message.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_text_plain_content_type_rejected(self):
        """A ``text/plain`` JSON body (CORS simple-request bypass) is rejected
        with 415 — even same-origin — and must NOT reach dispatch."""
        session = _make_mounted_session()
        session.runtime.dispatch_message = AsyncMock(return_value=None)

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "event", "event": "increment", "params": {}}),
            content_type="text/plain",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 415
        session.runtime.dispatch_message.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_application_json_content_type_accepted(self):
        """``application/json`` (what the real client sends) is accepted."""
        session = _make_mounted_session()
        session.runtime.dispatch_message = AsyncMock(return_value=None)

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "event", "event": "increment", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        _attach_owner(request)
        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        session.runtime.dispatch_message.assert_called_once()

    async def test_application_json_charset_accepted(self):
        """``application/json; charset=utf-8`` (the common real-client form) must
        be accepted — guards against a future strict ``==`` refactor of the
        content-type check (Stage-11 #158 coverage nit)."""
        session = _make_mounted_session()
        session.runtime.dispatch_message = AsyncMock(return_value=None)

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/message/",
            data=json.dumps({"type": "event", "event": "increment", "params": {}}),
            content_type="application/json; charset=utf-8",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        _attach_owner(request)
        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        session.runtime.dispatch_message.assert_called_once()


# ---------------------------------------------------------------------------
# POST event endpoint (the legacy /event/ alias — same CSRF sink)
# ---------------------------------------------------------------------------


class TestSSEEventOrigin:
    """``DjustSSEEventView.post`` (legacy alias) must reject cross-origin POSTs."""

    def setup_method(self):
        self.factory = RequestFactory()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_foreign_origin_event_post_rejected_with_403(self):
        """Cross-origin POST to the legacy /event/ endpoint must be 403 and must
        NOT dispatch the handler."""
        session = _make_mounted_session()

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"event": "delete_account", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=FOREIGN_ORIGIN,
        )
        view = DjustSSEEventView()
        session.runtime.dispatch_event = AsyncMock(return_value=None)
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 403
        session.runtime.dispatch_event.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_same_origin_event_post_not_403(self):
        """Same-origin POST to /event/ reaches normal handling."""
        session = _make_mounted_session()

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"event": "increment", "params": {}}),
            content_type="application/json",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        _attach_owner(request)
        view = DjustSSEEventView()
        session.runtime.dispatch_event = AsyncMock(return_value=None)
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        session.runtime.dispatch_event.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_text_plain_event_post_rejected(self):
        """``text/plain`` to /event/ is rejected with 415 (simple-request bypass)."""
        session = _make_mounted_session()

        request = self.factory.post(
            f"/djust/sse/{session.session_id}/event/",
            data=json.dumps({"event": "increment", "params": {}}),
            content_type="text/plain",
            HTTP_ORIGIN=ALLOWED_ORIGIN,
        )
        view = DjustSSEEventView()
        session.runtime.dispatch_event = AsyncMock(return_value=None)
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 415
        session.runtime.dispatch_event.assert_not_called()
