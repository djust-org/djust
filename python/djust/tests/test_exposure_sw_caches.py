"""ADR-038 D-b / D-n: the server half of the service-worker cache contract.

The browser half (tests/js/exposure_sw_caches.test.js) proves the client and
worker honour these signals. Here the signals are asserted where they leave
the server: the HTTP response headers and the real mount frame produced by
``ViewRuntime.dispatch_mount``. Only the staged construction gate is bypassed.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust._exposure import service_worker_cache_eligible
from djust.decorators import state
from djust.runtime import ViewRuntime
from djust.security.service_worker import SW_CACHE_HEADER, identity_marker
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

SESSION_KEY = "sessionkeySENTINEL0000000000000"


class _User:
    def __init__(self, pk, authenticated=True):
        self.pk = pk
        self.is_authenticated = authenticated


class ExplicitSWView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")
    navigation = state("initial", persist="client", client=True)

    def mount(self, request, **kwargs):
        self.count = 5


class LegacySWView(LiveView):
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 5


class LegacySnapshotSWView(LegacySWView):
    enable_state_snapshot = True


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


# ---------------------------------------------------------------------------
# identity_marker
# ---------------------------------------------------------------------------


@override_settings(SECRET_KEY="first-secret-key-for-sw-identity-tests-0000000000")
def test_identity_marker_is_a_value_free_digest_of_the_binding():
    user = _User(pk=4242)
    marker = identity_marker(SESSION_KEY, user)
    assert isinstance(marker, str) and len(marker) == 32
    int(marker, 16)  # hex digest
    assert "SENTINEL" not in marker and "4242" not in marker
    assert marker == identity_marker(SESSION_KEY, _User(pk=4242))
    # Session change (login cycles the key, logout flushes it) and user change.
    assert marker != identity_marker("othersessionkey000000000000000", user)
    assert marker != identity_marker(SESSION_KEY, _User(pk=4243))
    assert marker != identity_marker(SESSION_KEY, None)


def test_identity_marker_is_bound_to_secret_key():
    user = _User(pk=1)
    with override_settings(SECRET_KEY="first-secret-key-for-sw-identity-tests-0000000000"):
        first = identity_marker(SESSION_KEY, user)
    with override_settings(SECRET_KEY="second-secret-key-for-sw-identity-tests-000000000"):
        second = identity_marker(SESSION_KEY, user)
    assert first != second


def test_identity_marker_absent_without_session_or_authenticated_user():
    assert identity_marker(None, None) is None
    assert identity_marker("", AnonymousUser()) is None
    assert identity_marker(None, _User(pk=7, authenticated=False)) is None
    assert identity_marker(None, _User(pk=7)) is not None


# ---------------------------------------------------------------------------
# eligibility
# ---------------------------------------------------------------------------


def test_eligibility_is_legacy_only_and_fails_closed(staged):
    assert service_worker_cache_eligible(LegacySWView()) is True
    assert service_worker_cache_eligible(ExplicitSWView()) is False

    legacy = LegacySWView()
    legacy._get_all_child_views = lambda: {"child": ExplicitSWView()}
    assert service_worker_cache_eligible(legacy) is False

    def broken():
        raise RuntimeError("CHILD_SENTINEL")

    legacy._get_all_child_views = broken
    assert service_worker_cache_eligible(legacy) is False


# ---------------------------------------------------------------------------
# HTTP GET: the header the worker reads before writing SHELL_CACHE
# ---------------------------------------------------------------------------


def _http_request(session):
    request = RequestFactory().get("/sw/")
    request.session = session
    request.user = AnonymousUser()
    request.tenant = None
    return request


@pytest.mark.django_db
def test_http_explicit_page_is_marked_no_store_and_legacy_is_unchanged(staged):
    explicit = ExplicitSWView.as_view()(_http_request(SessionStore()))
    assert explicit.status_code == 200
    assert explicit[SW_CACHE_HEADER] == "no-store"

    legacy = LegacySWView.as_view()(_http_request(SessionStore()))
    assert legacy.status_code == 200
    assert b">5</span>" in legacy.content  # the legacy page did render
    assert not legacy.has_header(SW_CACHE_HEADER)


# ---------------------------------------------------------------------------
# Mount frame: sw_cache, sw_identity, state_snapshot_max_age
# ---------------------------------------------------------------------------


def _mount_request(user=None):
    request = RequestFactory().get("/sw-runtime/")
    request.user = user if user is not None else AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


async def _mount_frame(view_class, request):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return request

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    return next(frame for frame in transport.sent if frame.get("type") == "mount")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_mount_frame_marks_explicit_ineligible_and_carries_identity(staged):
    request = await sync_to_async(_mount_request)()
    frame = await _mount_frame(ExplicitSWView, request)
    assert frame["sw_cache"] == "no-store"
    assert "html" in frame  # the frame the client would otherwise cache
    expected = identity_marker(request.session.session_key, request.user)
    assert expected and frame["sw_identity"] == expected
    assert request.session.session_key not in json.dumps(frame)
    # Explicit client snapshot: its lifetime travels with it.
    assert frame["state_snapshot_signed"]
    from djust.security import get_max_age

    assert frame["state_snapshot_max_age"] == get_max_age()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_mount_frame_legacy_control_stays_cacheable(staged):
    request = await sync_to_async(_mount_request)()
    frame = await _mount_frame(LegacySWView, request)
    assert "html" in frame and ">5</span>" in frame["html"]
    assert "sw_cache" not in frame
    assert frame["sw_identity"] == identity_marker(request.session.session_key, request.user)
    assert "state_snapshot_max_age" not in frame  # no snapshot, no lifetime

    with override_settings(DJUST_STATE_SNAPSHOT_MAX_AGE=120):
        snap = await _mount_frame(LegacySnapshotSWView, await sync_to_async(_mount_request)())
    assert snap["state_snapshot_signed"]
    assert snap["state_snapshot_max_age"] == 120
    assert "sw_cache" not in snap
