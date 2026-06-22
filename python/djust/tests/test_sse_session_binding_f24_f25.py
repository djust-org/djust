"""
Tests for SSE session owner-binding (Finding #24) and resource-exhaustion caps
(Finding #25), both in ``python/djust/sse.py``.

Finding #24 (CWE-639/CWE-862, session hijack / missing authorization): the SSE
stream-GET creates an ``SSESession`` for a *client-chosen* ``session_id`` and the
event/message POST endpoints dispatched on it with NO check that the POSTer owns
the session — so anyone who learned a leaked ``session_id`` could drive the
mounter's view with the mounter's captured ``request.user``. The fix binds each
session to its creating principal at stream-GET (``_owner_user_pk`` for an
authenticated mounter, a forced-non-None Django ``_owner_session_key`` for an
anonymous mounter) and re-verifies on EVERY event AND message POST via the shared
``_request_owns_session`` helper; a mismatch returns 403 and does not dispatch.

Finding #25 (CWE-770/CWE-400, unauthenticated resource-exhaustion DoS): the
stream-GET registered a session in the process-global ``_sse_sessions`` BEFORE
mounting and did not remove it on mount failure, with no cap or rate-limit — so
sessions accumulated unboundedly, even for unauthorized/failed mounts. The fix
adds a per-principal/IP concurrency cap (429) + a global cap (503) checked BEFORE
allocation, and registers the session only AFTER a successful mount (a failed /
unauthorized / redirecting mount leaves no live POST-routable session).

GATE-OFF (#1468):
  * Removing the owner check (e.g. ``if False and not _request_owns_session(...)``)
    makes ``test_cross_user_event_post_is_forbidden_and_does_not_dispatch`` and
    ``test_anonymous_session_key_mismatch_forbidden`` fail (the POST would 200 and
    dispatch).
  * Removing the per-client cap (return early) makes
    ``test_per_client_cap_returns_429_and_does_not_register`` fail (the N+1 GET
    registers a session). Removing register-after-mount (always register) makes
    ``test_failed_mount_leaves_no_registered_session`` fail.
"""

import json
import uuid

import pytest

import django
from django.conf import settings

# Configure Django if not already done (mirrors test_sse_csrf_origin.py). Under
# the repo's pytest config DJANGO_SETTINGS_MODULE=demo_project.settings is already
# set, so this block is skipped; it keeps the file runnable standalone.
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
        SECRET_KEY="test-secret-key-for-sse-session-binding-tests",
        SESSION_ENGINE="django.contrib.sessions.backends.cache",
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from django.contrib.sessions.backends.cache import SessionStore  # noqa: E402
from django.http import StreamingHttpResponse  # noqa: E402
from django.test import RequestFactory, override_settings  # noqa: E402

from djust.sse import (  # noqa: E402
    SSESession,
    DjustSSEStreamView,
    DjustSSEEventView,
    DjustSSEMessageView,
    _sse_sessions,
    _request_owns_session,
)

ALLOWED_ORIGIN = "https://example.com"


def _auth_user(pk):
    """A minimal authenticated user (binding reads only is_authenticated + pk)."""
    return MagicMock(is_authenticated=True, pk=pk)


def _anon_user():
    """A minimal anonymous user."""
    return MagicMock(is_authenticated=False, pk=None)


def _make_session(*, owner_user_pk=None, owner_session_key=None, mounted=True):
    """Create + register an owner-bound SSE session (mounted by default)."""
    from djust.runtime import ViewRuntime, SSESessionTransport

    sid = str(uuid.uuid4())
    session = SSESession(sid)
    session._owner_user_pk = owner_user_pk
    session._owner_session_key = owner_session_key
    if mounted:
        session.view_instance = MagicMock()
        session.runtime = ViewRuntime(SSESessionTransport(session))
        session.runtime.view_instance = session.view_instance
    _sse_sessions[sid] = session
    return session


def _event_post(factory, session_id, *, user=None, session_key=None):
    request = factory.post(
        f"/djust/sse/{session_id}/event/",
        data=json.dumps({"event": "delete_account", "params": {}}),
        content_type="application/json",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
    )
    _bind_identity(request, user=user, session_key=session_key)
    return request


def _message_post(factory, session_id, *, user=None, session_key=None):
    request = factory.post(
        f"/djust/sse/{session_id}/message/",
        data=json.dumps({"type": "event", "event": "delete_account", "params": {}}),
        content_type="application/json",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
    )
    _bind_identity(request, user=user, session_key=session_key)
    return request


def _bind_identity(request, *, user=None, session_key=None):
    """Stamp request.user / request.session to control the POSTer identity."""
    request.user = user if user is not None else _anon_user()
    sess = MagicMock()
    sess.session_key = session_key
    request.session = sess
    return request


@pytest.fixture(autouse=True)
def _clear_sessions():
    _sse_sessions.clear()
    yield
    _sse_sessions.clear()


# ---------------------------------------------------------------------------
# F24 — owner binding on event/message POST
# ---------------------------------------------------------------------------


class TestF24OwnerBinding:
    def setup_method(self):
        self.factory = RequestFactory()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_cross_user_event_post_is_forbidden_and_does_not_dispatch(self):
        """Session created by user A; user B POSTs an event to it → 403, and the
        handler does NOT run on A's view. This is the core hijack reproduction
        (and the gate-off witness for the owner check)."""
        session = _make_session(owner_user_pk=1)  # owned by user A (pk=1)
        request = _event_post(self.factory, session.session_id, user=_auth_user(2))  # user B

        view = DjustSSEEventView()
        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 403
        mock_dispatch.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_cross_user_message_post_is_forbidden_and_does_not_dispatch(self):
        """Same hijack, message endpoint: user B can't drive user A's session."""
        session = _make_session(owner_user_pk=1)
        session.runtime.dispatch_message = AsyncMock(return_value=None)
        request = _message_post(self.factory, session.session_id, user=_auth_user(2))

        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 403
        session.runtime.dispatch_message.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_same_user_event_post_allowed(self):
        """The owning user (same pk) reaches dispatch."""
        session = _make_session(owner_user_pk=1)
        request = _event_post(self.factory, session.session_id, user=_auth_user(1))

        view = DjustSSEEventView()
        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        mock_dispatch.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_same_user_message_post_allowed(self):
        session = _make_session(owner_user_pk=1)
        session.runtime.dispatch_message = AsyncMock(return_value=None)
        request = _message_post(self.factory, session.session_id, user=_auth_user(1))

        view = DjustSSEMessageView()
        response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        session.runtime.dispatch_message.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_anonymous_session_key_mismatch_forbidden(self):
        """Anonymous session bound to session-key X cannot be driven by a request
        carrying a different anonymous session-key Y → 403, no dispatch."""
        session = _make_session(owner_user_pk=None, owner_session_key="anon-key-X")
        request = _event_post(
            self.factory, session.session_id, user=_anon_user(), session_key="anon-key-Y"
        )

        view = DjustSSEEventView()
        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 403
        mock_dispatch.assert_not_called()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_anonymous_same_session_key_allowed(self):
        """Anonymous owner with the SAME session-key reaches dispatch."""
        session = _make_session(owner_user_pk=None, owner_session_key="anon-key-X")
        request = _event_post(
            self.factory, session.session_id, user=_anon_user(), session_key="anon-key-X"
        )

        view = DjustSSEEventView()
        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 200
        mock_dispatch.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_authenticated_user_cannot_drive_anonymous_session(self):
        """An anonymous-bound session is not owned by any authenticated user
        (their session_key won't match the bound anon key)."""
        session = _make_session(owner_user_pk=None, owner_session_key="anon-key-X")
        request = _event_post(
            self.factory, session.session_id, user=_auth_user(99), session_key="anon-key-Y"
        )

        view = DjustSSEEventView()
        with patch("djust.sse._sse_handle_event", new_callable=AsyncMock) as mock_dispatch:
            response = await view.post(request, session_id=session.session_id)

        assert response.status_code == 403
        mock_dispatch.assert_not_called()

    def test_owns_helper_rejects_unbound_session(self):
        """A session with no owner at all is unownable (defensive)."""
        unbound = SSESession(str(uuid.uuid4()))
        req = self.factory.post("/x/")
        _bind_identity(req, user=_anon_user(), session_key=None)
        assert _request_owns_session(req, unbound) is False


# ---------------------------------------------------------------------------
# F24 — owner capture at stream-GET creation
# ---------------------------------------------------------------------------


class TestF24OwnerCapture:
    def setup_method(self):
        self.factory = RequestFactory()

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_authenticated_get_captures_owner_pk(self):
        """A stream GET as an authenticated user binds the session to that pk."""
        sid = str(uuid.uuid4())
        request = self.factory.get(
            f"/djust/sse/{sid}/", {"view": "demo.View"}, HTTP_ORIGIN=ALLOWED_ORIGIN
        )
        request.user = _auth_user(55)
        request.session = SessionStore()

        view = DjustSSEStreamView()
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = True
            response = await view.get(request, session_id=sid)

        assert isinstance(response, StreamingHttpResponse)
        assert sid in _sse_sessions
        assert _sse_sessions[sid]._owner_user_pk == 55

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_anonymous_get_forces_and_binds_session_key(self):
        """A stream GET as an anonymous user (no session key yet) forces a Django
        session key to exist and binds the session to it (so the browser session
        cookie becomes the capability)."""
        sid = str(uuid.uuid4())
        request = self.factory.get(
            f"/djust/sse/{sid}/", {"view": "demo.View"}, HTTP_ORIGIN=ALLOWED_ORIGIN
        )
        request.user = _anon_user()
        request.session = SessionStore()
        assert request.session.session_key is None  # no key before

        view = DjustSSEStreamView()
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = True
            await view.get(request, session_id=sid)

        assert sid in _sse_sessions
        bound = _sse_sessions[sid]
        assert bound._owner_user_pk is None
        assert bound._owner_session_key is not None  # forced non-None
        assert bound._owner_session_key == request.session.session_key


# ---------------------------------------------------------------------------
# F25 — concurrency caps + register-after-mount
# ---------------------------------------------------------------------------


class TestF25Caps:
    def setup_method(self):
        self.factory = RequestFactory()

    def _get_request(self, sid, *, user):
        request = self.factory.get(
            f"/djust/sse/{sid}/", {"view": "demo.View"}, HTTP_ORIGIN=ALLOWED_ORIGIN
        )
        request.user = user
        request.session = SessionStore()
        return request

    @override_settings(DJUST_SSE_MAX_SESSIONS_PER_CLIENT=2)
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_per_client_cap_returns_429_and_does_not_register(self):
        """With the per-client cap set to 2, the 3rd GET from the SAME principal
        is 429 and registers no new session (gate-off witness for the cap)."""
        view = DjustSSEStreamView()
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = True
            for _ in range(2):  # fill the cap
                sid = str(uuid.uuid4())
                resp = await view.get(self._get_request(sid, user=_auth_user(7)), session_id=sid)
                assert isinstance(resp, StreamingHttpResponse)

            assert len(_sse_sessions) == 2

            # The (N+1)th from the same principal is rejected.
            over_sid = str(uuid.uuid4())
            resp = await view.get(
                self._get_request(over_sid, user=_auth_user(7)), session_id=over_sid
            )
            assert resp.status_code == 429
            assert over_sid not in _sse_sessions
            assert len(_sse_sessions) == 2

    @override_settings(DJUST_SSE_MAX_SESSIONS_PER_CLIENT=2)
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_per_client_cap_is_per_principal(self):
        """A DIFFERENT principal is not blocked by another principal's count."""
        view = DjustSSEStreamView()
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = True
            for _ in range(2):
                sid = str(uuid.uuid4())
                await view.get(self._get_request(sid, user=_auth_user(7)), session_id=sid)

            other_sid = str(uuid.uuid4())
            resp = await view.get(
                self._get_request(other_sid, user=_auth_user(8)), session_id=other_sid
            )
            assert isinstance(resp, StreamingHttpResponse)
            assert other_sid in _sse_sessions

    @override_settings(DJUST_SSE_MAX_SESSIONS_TOTAL=1)
    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_global_cap_returns_503_and_does_not_register(self):
        """When the global cap is reached, a new GET is 503 regardless of client."""
        view = DjustSSEStreamView()
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = True
            sid = str(uuid.uuid4())
            await view.get(self._get_request(sid, user=_auth_user(7)), session_id=sid)
            assert len(_sse_sessions) == 1

            over_sid = str(uuid.uuid4())
            resp = await view.get(
                self._get_request(over_sid, user=_auth_user(9)), session_id=over_sid
            )
            assert resp.status_code == 503
            assert over_sid not in _sse_sessions

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_failed_mount_leaves_no_registered_session(self):
        """A stream GET whose mount FAILS (bad/nonexistent view path) must NOT
        leave an entry in _sse_sessions (register-after-mount, gate-off witness).

        Uses the REAL _sse_mount_view against a nonexistent view, which fails the
        view-import allowlist and returns False."""
        sid = str(uuid.uuid4())
        request = self._get_request(sid, user=_auth_user(7))
        # Point at a non-importable / non-allowlisted view so the real mount fails.
        request.GET = request.GET.copy()
        request.GET["view"] = "nonexistent.module.NopeView"

        view = DjustSSEStreamView()
        resp = await view.get(request, session_id=sid)

        # A streaming response is still returned (it will deliver the error
        # frame), but the session must NOT be POST-routable.
        assert isinstance(resp, StreamingHttpResponse)
        assert sid not in _sse_sessions

    @override_settings(ALLOWED_HOSTS=["example.com"])
    @pytest.mark.asyncio
    async def test_unauthorized_mount_leaves_no_registered_session(self):
        """A mount that returns False (e.g. auth/permission failure) leaves no
        registered session, even though the GET succeeds with a 200 stream."""
        sid = str(uuid.uuid4())
        request = self._get_request(sid, user=_auth_user(7))

        view = DjustSSEStreamView()
        with patch("djust.sse._sse_mount_view", new_callable=AsyncMock) as mock_mount:
            mock_mount.return_value = False  # simulate auth/permission failure
            resp = await view.get(request, session_id=sid)

        assert isinstance(resp, StreamingHttpResponse)
        assert sid not in _sse_sessions
