"""Regression: live_redirect_mount must resolve the TARGET view server-side
from the URL, not trust the client-supplied (source) view (#1647).

The client's `resolveViewPath()` falls back to the current container's `dj-view`
— the SOURCE view — when its route map is empty (the default for plain Django
`path()` URLconfs with no `live_session()`). The server then instantiated the
source class against the new URL's request and raised "Failed to load view".

`LiveViewConsumer._resolve_view_path_from_url(url)` resolves the destination URL
to its djust LiveView via Django's URL dispatcher, and `handle_live_redirect_mount`
overrides `data["view"]` with it (falling back to the client value when the URL
doesn't map to a LiveView).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from django.test import override_settings

from djust.websocket import LiveViewConsumer

URLCONF = "tests.redirect_mount_test_urls"
SOURCE = "tests.redirect_mount_test_urls.RedirectSourceView"
TARGET = "tests.redirect_mount_test_urls.RedirectTargetView"


@override_settings(ROOT_URLCONF=URLCONF)
def test_resolve_view_path_from_url_returns_target():
    consumer = LiveViewConsumer()
    assert consumer._resolve_view_path_from_url("/redirect-target/") == TARGET


@override_settings(ROOT_URLCONF=URLCONF)
def test_resolve_returns_none_for_unmapped_url():
    consumer = LiveViewConsumer()
    assert consumer._resolve_view_path_from_url("/no-such-url/") is None


@override_settings(ROOT_URLCONF=URLCONF)
def test_resolve_returns_none_for_non_liveview():
    consumer = LiveViewConsumer()
    # A plain Django view must NOT be overridden — caller keeps the client value.
    assert consumer._resolve_view_path_from_url("/plain/") is None


@override_settings(ROOT_URLCONF=URLCONF)
def test_resolve_returns_none_for_empty_url():
    consumer = LiveViewConsumer()
    assert consumer._resolve_view_path_from_url("") is None


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=URLCONF)
async def test_handle_live_redirect_mount_overrides_source_view():
    """The end-to-end fix: client ships the SOURCE view + the TARGET url; the
    server must mount the TARGET (resolved from the url), not the source."""
    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.view_instance = None  # no old view → no sticky staging
    consumer._sticky_auto_reattached = set()
    consumer._sticky_preserved = {}

    captured = {}

    async def _fake_handle_mount(data, **kwargs):
        captured["view"] = data.get("view")

    with patch.object(consumer, "handle_mount", new=AsyncMock(side_effect=_fake_handle_mount)):
        await consumer.handle_live_redirect_mount(
            {"view": SOURCE, "url": "/redirect-target/", "params": {}}
        )

    assert captured.get("view") == TARGET, (
        "handle_live_redirect_mount must override the client-supplied SOURCE view "
        f"with the URL-resolved TARGET view; handle_mount received {captured.get('view')!r}"
    )


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=URLCONF)
async def test_handle_live_redirect_mount_keeps_client_view_when_url_unresolvable():
    """Control: when the URL doesn't map to a LiveView, the client-supplied view
    is preserved (the live_session route-map path is unaffected)."""
    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.view_instance = None
    consumer._sticky_auto_reattached = set()
    consumer._sticky_preserved = {}

    captured = {}

    async def _fake_handle_mount(data, **kwargs):
        captured["view"] = data.get("view")

    with patch.object(consumer, "handle_mount", new=AsyncMock(side_effect=_fake_handle_mount)):
        await consumer.handle_live_redirect_mount(
            {"view": SOURCE, "url": "/no-such-url/", "params": {}}
        )

    assert captured.get("view") == SOURCE


@pytest.mark.asyncio
@override_settings(ROOT_URLCONF=URLCONF)
async def test_back_nav_state_snapshot_view_not_overridden():
    """Back-nav guard: when a `state_snapshot` is present it carries the
    authoritative view, and its `url` may be generic and resolve to an
    unrelated view. The URL-override must NOT fire — otherwise back-nav restores
    the wrong view (regression caught by the pre-push suite during #1647)."""
    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.view_instance = None
    consumer._sticky_auto_reattached = set()
    consumer._sticky_preserved = {}

    captured = {}

    async def _fake_handle_mount(data, **kwargs):
        captured["view"] = data.get("view")

    # url="/redirect-target/" WOULD resolve to TARGET, but the snapshot's view
    # (SOURCE here) must win because a snapshot is present.
    with patch.object(consumer, "handle_mount", new=AsyncMock(side_effect=_fake_handle_mount)):
        await consumer.handle_live_redirect_mount(
            {
                "view": SOURCE,
                "url": "/redirect-target/",
                "params": {},
                "state_snapshot": {"view_slug": SOURCE, "state_json": "{}", "ts": 0},
            }
        )

    assert captured.get("view") == SOURCE, (
        "a live_redirect_mount carrying a state_snapshot must NOT have its view "
        f"overridden from the URL; handle_mount received {captured.get('view')!r}"
    )
