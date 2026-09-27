"""#3212 item 2: ``live_redirect`` re-checks sticky children as the mount does.

The WebSocket ``live_redirect`` re-checks the old page's sticky children
against a request built by ``_build_live_redirect_request``, whose user was
the connect-time ``scope["user"]``. Since #3201 an explicit mount whose
session has vanished (a cache flush, expiry, a logout elsewhere) runs as
anonymous under a replacement session, because a lost session cannot vouch
for that user. The sticky re-check is a parallel path to that re-derivation
(#1646): it kept authorizing the children as the old authenticated user, so a
``login_required`` sticky child survived into a page whose parent mounts
anonymous.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings
from django.urls import path

from djust import LiveView

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


class Page(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


class LegacyPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root></div>"


class StickyChild(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root></div>"

    def __init__(self):
        super().__init__()
        self.unmounted = False

    def _on_sticky_unmount(self):
        self.unmounted = True
        super()._on_sticky_unmount()


urlpatterns = [path("page/", Page.as_view()), path("next/", Page.as_view())]


@pytest.fixture(autouse=True)
def urls():
    with override_settings(ROOT_URLCONF=__name__):
        yield


def _user_and_session(*, vanished):
    """A logged-in session for a real user, optionally deleted from the store
    afterwards (the #3201 condition). Returns the scope pieces a connected
    socket holds: its session object and the user resolved at connect."""
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY

    user = get_user_model().objects.create_user(username="u3212", password="pw-3212-x")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    session.save()
    if vanished:
        SessionStore(session.session_key).delete()
    return user, SessionStore(session.session_key)


def _consumer(view, scope_session, user):
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.scope = {"session": scope_session, "user": user, "headers": []}
    consumer.view_instance = view
    consumer._runtime = ViewRuntime(MockTransport())
    return consumer


async def _redirect_request(consumer):
    request = consumer._build_live_redirect_request({"url": "/next/"})
    await consumer._rederive_live_redirect_user(request)
    return request


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def test_a_vanished_session_rechecks_as_anonymous(staged):
    user, scope_session = await sync_to_async(_user_and_session)(vanished=True)
    consumer = _consumer(Page(), scope_session, user)

    request = await _redirect_request(consumer)

    assert request.user.is_authenticated is False
    replacement = request.session.session_key
    assert replacement and replacement != scope_session.session_key
    # The redirect's own mount then reuses this replacement, as repeated
    # mounts on one socket do, instead of creating another row.
    assert consumer._runtime._replacement_sessions == {scope_session.session_key: replacement}


async def test_a_live_session_keeps_its_user(staged):
    user, scope_session = await sync_to_async(_user_and_session)(vanished=False)
    consumer = _consumer(Page(), scope_session, user)

    request = await _redirect_request(consumer)

    assert request.user == user
    assert request.session.session_key == scope_session.session_key
    assert consumer._runtime._replacement_sessions == {}


async def test_a_legacy_page_is_unchanged(staged):
    """Legacy mounts do not re-derive the user (#3201 is explicit-only), so
    neither does their sticky re-check."""
    user, scope_session = await sync_to_async(_user_and_session)(vanished=True)
    consumer = _consumer(LegacyPage(), scope_session, user)

    request = await _redirect_request(consumer)

    assert request.user == user


async def test_login_required_sticky_child_does_not_survive_a_vanished_session(staged):
    """End to end through ``handle_live_redirect_mount`` and the real helpers."""
    user, scope_session = await sync_to_async(_user_and_session)(vanished=True)
    parent, child = Page(), StickyChild()
    child.sticky, child.sticky_id = True, "child"
    parent._register_child("child", child)
    consumer = _consumer(parent, scope_session, user)
    consumer._view_group = consumer._tick_task = None
    consumer._flush_all_pending = AsyncMock()
    consumer._resolve_view_path_from_url = lambda url: None

    async def mount(*args, **kwargs):
        consumer.view_instance = Page()

    consumer.handle_mount = mount
    await consumer.handle_live_redirect_mount({"url": "/next/", "view": __name__ + ".Page"})

    assert not consumer._sticky_preserved
    assert child.unmounted is True


async def test_a_request_the_helper_could_not_build_is_left_alone(staged):
    consumer = _consumer(Page(), None, AnonymousUser())
    await consumer._rederive_live_redirect_user(None)
    stub = SimpleNamespace(user=AnonymousUser())
    await consumer._rederive_live_redirect_user(stub)
    assert consumer._runtime._replacement_sessions == {}
