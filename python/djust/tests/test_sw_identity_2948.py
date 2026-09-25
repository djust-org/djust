"""#2948: mount frames carry a value-free service-worker identity marker and,
with a signed snapshot, the snapshot lifetime.

The client clears the worker's state, VDOM and shell caches when the marker
changes or disappears (logout), and the worker expires state entries on read
with the server's max age. The browser side is covered by
``tests/js/sw_state_cache_lifetime_2948.test.js``.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.runtime import ViewRuntime
from djust.security.service_worker import identity_marker
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

SESSION_KEY = "sessionkeySENTINEL0000000000000"


class _User:
    def __init__(self, pk, authenticated=True):
        self.pk = pk
        self.is_authenticated = authenticated


class SWView(LiveView):
    template = "<div dj-root><span>{{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 5


class SnapshotSWView(SWView):
    enable_state_snapshot = True


@override_settings(SECRET_KEY="first-secret-key-for-sw-identity-tests-0000000000")
def test_identity_marker_is_a_value_free_digest_of_the_binding():
    user = _User(pk=4242)
    marker = identity_marker(SESSION_KEY, user)
    assert isinstance(marker, str) and len(marker) == 32
    assert SESSION_KEY not in marker and "4242" not in marker
    assert marker == identity_marker(SESSION_KEY, _User(pk=4242))
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


def _mount_request():
    request = RequestFactory().get("/sw-runtime/")
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore()
    request.session.create()
    return request


async def _mount_frame(view_class, request):
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    return next(frame for frame in transport.sent if frame.get("type") == "mount")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_mount_frame_carries_identity_but_not_the_session_key():
    request = await sync_to_async(_mount_request)()
    frame = await _mount_frame(SWView, request)
    assert "html" in frame and ">5</span>" in frame["html"]
    expected = identity_marker(request.session.session_key, request.user)
    assert expected and frame["sw_identity"] == expected
    assert request.session.session_key not in json.dumps(frame)
    assert "state_snapshot_max_age" not in frame  # no snapshot, no lifetime


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_mount_frame_with_snapshot_carries_the_server_max_age():
    with override_settings(DJUST_STATE_SNAPSHOT_MAX_AGE=120):
        frame = await _mount_frame(SnapshotSWView, await sync_to_async(_mount_request)())
    assert frame["state_snapshot_signed"]
    assert frame["state_snapshot_max_age"] == 120
