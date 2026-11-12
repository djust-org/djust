"""Tests for Service Worker advanced features (v0.6.0).

Covers the three features landed in one PR:

* VDOM cache (config + checks).
* State snapshot restoration on live_redirect_mount.
* Mount batching (``handle_mount_batch`` + ``_mount_one`` seam).

Assertion discipline: any test that inspects rendered HTML uses
``html.parser.HTMLParser`` (see ``test_sticky_preserve.py`` / Phase A-B
precedent). Substring matching on HTML is forbidden — it masks
attribute-injection bugs.
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple

import pytest

from djust.live_view import LiveView


# ---------------------------------------------------------------------------
# HTML parse helpers
# ---------------------------------------------------------------------------


class _AttrCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.elements: List[Tuple[str, Dict[str, str]]] = []

    def handle_starttag(self, tag, attrs):
        d: Dict[str, str] = {name: (v if v is not None else "") for name, v in attrs}
        self.elements.append((tag, d))

    def handle_startendtag(self, tag, attrs):
        d: Dict[str, str] = {name: (v if v is not None else "") for name, v in attrs}
        self.elements.append((tag, d))


def _parse(html: str) -> _AttrCollector:
    p = _AttrCollector()
    p.feed(html)
    p.close()
    return p


# ---------------------------------------------------------------------------
# Module-scope views — resolvable by dotted path for handle_mount.
# ---------------------------------------------------------------------------


class _SimpleCounter(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _SnapshotCounter(LiveView):
    """A legacy counter, for the ``_capture_snapshot_state`` cases."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _SnapshotRejecting(LiveView):
    """Rejects every client snapshot via a _should_restore_snapshot override."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.mount_was_called = True

    def _should_restore_snapshot(self, request):
        return False

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _PushEventOnMount(LiveView):
    """View that calls push_event() during mount() — exercises #1295."""

    exposure_policy = "legacy"
    template = "<div dj-root><span>Hello</span></div>"

    def mount(self, request, **kwargs):
        self.push_event("greeting", {"msg": "Welcome"})

    def get_context_data(self, **kwargs):
        return {}


# ---------------------------------------------------------------------------
# 4-8. State snapshot unit tests — LiveView API surface only.
# ---------------------------------------------------------------------------


class TestStateSnapshotAPI:
    def test_capture_snapshot_includes_public_attrs(self):
        view = _SnapshotCounter()
        view.count = 5
        view.name = "alice"
        view._private = "secret"  # underscore excluded
        snap = view._capture_snapshot_state()
        assert snap.get("count") == 5
        assert snap.get("name") == "alice"
        assert "_private" not in snap

    def test_capture_snapshot_excludes_framework_internals(self):
        view = _SnapshotCounter()
        view.count = 5
        # Framework-internal names in _FRAMEWORK_INTERNAL_ATTRS must be
        # filtered even if set as instance attrs.
        view.template_name = "custom.html"  # in _FRAMEWORK_INTERNAL_ATTRS
        snap = view._capture_snapshot_state()
        assert "template_name" not in snap

    def test_capture_snapshot_skips_non_serializable(self):
        view = _SnapshotCounter()
        view.count = 5
        view.bad_value = lambda x: x  # callable — always skipped
        snap = view._capture_snapshot_state()
        assert "count" in snap
        assert "bad_value" not in snap

    def test_should_restore_snapshot_default_true(self):
        view = _SnapshotCounter()
        assert view._should_restore_snapshot(None) is True

    def test_should_restore_snapshot_override_can_reject(self):
        view = _SnapshotRejecting()
        assert view._should_restore_snapshot(None) is False


# ---------------------------------------------------------------------------
# Consumer shim — bypasses Channels ASGI wiring. Mirrors the pattern in
# tests/integration/test_sticky_redirect_flow.py so we can drive
# handle_mount / handle_mount_batch without a full channel layer.
# ---------------------------------------------------------------------------


class _AllowAllRateLimiter:
    """Permissive rate limiter for the fake consumer. ``_get_runtime`` (#1919)
    constructs ``ViewRuntime(..., rate_limiter=self._rate_limiter)`` when the
    handle_mount shim runs, so the fake needs one that never throttles."""

    def check(self, _msg_type):
        return True

    def check_upload(self):
        return True

    def should_disconnect(self):
        return False


def _make_fake_consumer():
    from djust.websocket import LiveViewConsumer

    class _NullChannelLayer:
        async def group_add(self, *a, **kw):
            return None

        async def group_discard(self, *a, **kw):
            return None

    class _FakeConsumer(LiveViewConsumer):
        def __init__(self):  # type: ignore[no-untyped-def]
            self.view_instance = None
            self._view_group = None
            self._presence_group = None
            self._tick_task = None
            self._render_lock = asyncio.Lock()
            self._processing_user_event = False
            self._sticky_preserved = {}
            self._db_notify_channels = set()
            self.sent_frames: List[Dict[str, Any]] = []
            self.session_id = "test-session"
            self.scope = {"path": "/", "query_string": b"", "session": None}
            self.channel_name = "test-channel"
            # Wire-version counter attrs the runtime mount path reads via the
            # next_mount_version hook (#1919 THE MOUNT FLIP: handle_mount is now a
            # thin shim over ViewRuntime.dispatch_mount, so these fakes drive the
            # runtime path). ``_next_version`` uses getattr-with-default, but set
            # them explicitly for clarity + a clean fresh baseline.
            self._last_sent_version = 0
            self._recovery_html = None
            self._recovery_version = 0
            # Permissive rate limiter — the runtime is constructed with
            # ``rate_limiter=self._rate_limiter`` in ``_get_runtime`` (post-#1919
            # the shim calls it). Allow-all so the direct handle_mount /
            # handle_mount_batch calls these tests make reach the mount logic.
            self._rate_limiter = _AllowAllRateLimiter()

        @property
        def channel_layer(self):  # type: ignore[override]
            return _NullChannelLayer()

        async def send_json(self, payload):  # type: ignore[override]
            self.sent_frames.append(payload)

        async def send(self, *a, **kw):  # type: ignore[override]
            return None

        async def send_error(self, message, **kwargs):  # type: ignore[override]
            self.sent_frames.append({"type": "error", "message": message})

        async def close(self, code=None):  # type: ignore[override]
            return None

        async def _flush_push_events(self):
            return None

    return _FakeConsumer()


def _make_fake_consumer_with_push_events():
    """Same as _make_fake_consumer but preserves the real _flush_push_events.

    The default fake consumer overrides _flush_push_events to a no-op so
    that tests that don't use push_event() don't accidentally trip over
    missing view_instance setup.  When we DO want to test push_event
    capture (mount_batch + push_event in mount()), we need the real
    implementation so the queued events flow into the collector.
    """
    consumer = _make_fake_consumer()
    # Remove the no-op override — LiveViewConsumer's real _flush_push_events
    # calls self.send_json for each queued event, which in this fake
    # consumer appends to sent_frames (or captured[] when send_json is
    # swapped by _mount_one's collector).
    del type(consumer)._flush_push_events
    return consumer


# ---------------------------------------------------------------------------
# 4-8. State snapshot integration with handle_mount.
# ---------------------------------------------------------------------------


@pytest.fixture
def _allow_test_module(settings):
    """Register tests.unit.test_sw_advanced with LIVEVIEW_ALLOWED_MODULES.

    The demo settings whitelist doesn't include the test module, so
    handle_mount would refuse to import our module-scoped view classes
    without this fixture.
    """
    existing = list(getattr(settings, "LIVEVIEW_ALLOWED_MODULES", []) or [])
    settings.LIVEVIEW_ALLOWED_MODULES = existing + ["tests.unit.test_sw_advanced"]
    yield


# ---------------------------------------------------------------------------
# 1-3. Mount-batch tests.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_allow_test_module")
class TestMountBatch:
    def test_mount_batch_emitted_for_multiple_views(self):
        """Batch request with 2 successful views → one mount_batch frame."""
        consumer = _make_fake_consumer()
        data = {
            "type": "mount_batch",
            "views": [
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "target-a",
                },
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "target-b",
                },
            ],
        }
        asyncio.run(consumer.handle_mount_batch(data))
        # Exactly one mount_batch frame (no per-view mount frames).
        batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
        mount_frames = [f for f in consumer.sent_frames if f.get("type") == "mount"]
        assert len(batch_frames) == 1
        assert len(mount_frames) == 0
        batch = batch_frames[0]
        assert len(batch["views"]) == 2
        assert len(batch["failed"]) == 0
        target_ids = {v["target_id"] for v in batch["views"]}
        assert target_ids == {"target-a", "target-b"}

    def test_mount_batch_single_view_still_uses_batch_frame(self):
        """Batch request with 1 view still emits mount_batch (1 entry)."""
        consumer = _make_fake_consumer()
        data = {
            "type": "mount_batch",
            "views": [
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "only",
                },
            ],
        }
        asyncio.run(consumer.handle_mount_batch(data))
        batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
        assert len(batch_frames) == 1
        assert len(batch_frames[0]["views"]) == 1
        assert batch_frames[0]["views"][0]["target_id"] == "only"

    def test_mount_one_returns_5_tuple_on_unhandled_exception(self):
        """#1343 (CodeQL py/mixed-tuple-returns at websocket.py:2400):
        ``_mount_one`` previously returned a 4-tuple in the
        ``except Exception`` branch but a 5-tuple on every other path.
        The caller in ``handle_mount_batch`` unpacks 5 values; the
        mismatch raised ``ValueError: not enough values to unpack``,
        which masked the per-view error in the batch ``failed[]``
        plumbing. All return paths must now yield a 5-tuple
        ``(ok, payload, err, nav, push_events)``.
        """
        consumer = _make_fake_consumer()
        result = asyncio.run(
            consumer._mount_one(
                {
                    "target_id": "regress",
                    "view": "tests.unit.test_sw_advanced._DoesNotExist",
                    "params": {},
                    "url": "/",
                }
            )
        )
        assert len(result) == 5, (
            "_mount_one must return a 5-tuple on every path; got %d-tuple" % len(result)
        )
        ok, payload, err, nav, push_events = result
        assert ok is False
        assert payload["target_id"] == "regress"
        assert err is not None
        assert nav is None
        assert push_events == []

    def test_mount_batch_failure_isolated_in_failed_array(self):
        """One invalid view_path → failed[] entry, others succeed."""
        consumer = _make_fake_consumer()
        data = {
            "type": "mount_batch",
            "views": [
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "ok-1",
                },
                {
                    "view": "tests.unit.test_sw_advanced._DoesNotExist",
                    "params": {},
                    "url": "/",
                    "target_id": "bad",
                },
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "ok-2",
                },
            ],
        }
        asyncio.run(consumer.handle_mount_batch(data))
        batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
        assert len(batch_frames) == 1
        batch = batch_frames[0]
        # Two survivors, one failure.
        assert len(batch["views"]) == 2
        assert len(batch["failed"]) == 1
        surviving_targets = {v["target_id"] for v in batch["views"]}
        assert surviving_targets == {"ok-1", "ok-2"}
        assert batch["failed"][0]["target_id"] == "bad"

    def test_mount_batch_html_uses_dj_root(self):
        """Every surviving entry's html parses and contains the view's rendered content."""
        consumer = _make_fake_consumer()
        data = {
            "type": "mount_batch",
            "views": [
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "t1",
                },
            ],
        }
        asyncio.run(consumer.handle_mount_batch(data))
        batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
        assert len(batch_frames) == 1
        view_entries = batch_frames[0]["views"]
        assert len(view_entries) == 1
        entry = view_entries[0]
        assert "html" in entry
        assert entry["target_id"] == "t1"
        # Parse the HTML — it must contain at least a span (from the template).
        tree = _parse(entry["html"])
        tags = [tag for tag, _ in tree.elements]
        assert "span" in tags, (
            "mount_batch view html should contain rendered <span>, got tags: %s" % tags
        )

    def test_mount_batch_preserves_push_events_from_mount(self):
        """#1295: push_event() during mount() is not silently dropped.

        When a batch-mounted view calls push_event() in its mount() method,
        the push event must reach the client — not be swallowed by the
        _mount_one capture extractor (which previously only pulled mount,
        error, and navigate frames).
        """
        consumer = _make_fake_consumer_with_push_events()
        data = {
            "type": "mount_batch",
            "views": [
                {
                    "view": "tests.unit.test_sw_advanced._PushEventOnMount",
                    "params": {},
                    "url": "/",
                    "target_id": "greeter",
                },
            ],
        }
        asyncio.run(consumer.handle_mount_batch(data))

        # The mount_batch frame must still be present.
        batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
        assert len(batch_frames) == 1
        assert len(batch_frames[0]["views"]) == 1

        # The push_event frame must arrive AFTER the batch frame.
        push_frames = [f for f in consumer.sent_frames if f.get("type") == "push_event"]
        assert len(push_frames) == 1, (
            "push_event() from mount() must survive the _mount_one collector; "
            "got 0 push_event frames (all frames: %s)" % consumer.sent_frames
        )
        assert push_frames[0]["event"] == "greeting"
        assert push_frames[0]["payload"] == {"msg": "Welcome"}

        # Verify ordering: push_event after mount_batch.
        batch_idx = consumer.sent_frames.index(batch_frames[0])
        push_idx = consumer.sent_frames.index(push_frames[0])
        assert push_idx > batch_idx, "push_event must flush after mount_batch, not before"


# ---------------------------------------------------------------------------
# 9-10. Config / checks tests.
# ---------------------------------------------------------------------------


class TestServiceWorkerChecks:
    def _run_check(self, **settings_overrides):
        """Invoke the C3xx check function with patched settings."""
        from django.test import override_settings
        from djust.checks import check_service_worker_advanced

        with override_settings(**settings_overrides):
            return check_service_worker_advanced(app_configs=None)

    def test_c301_fires_on_zero_ttl(self):
        """TTL of 0 must produce djust.C301 error."""
        errors = self._run_check(LIVEVIEW_CONFIG={"service_worker": {"vdom_cache_ttl_seconds": 0}})
        ids = [e.id for e in errors]
        assert "djust.C301" in ids

    def test_c301_fires_on_negative_ttl(self):
        errors = self._run_check(
            LIVEVIEW_CONFIG={"service_worker": {"vdom_cache_ttl_seconds": -60}}
        )
        ids = [e.id for e in errors]
        assert "djust.C301" in ids

    def test_c301_clean_for_positive_ttl(self):
        errors = self._run_check(
            LIVEVIEW_CONFIG={"service_worker": {"vdom_cache_ttl_seconds": 1800}}
        )
        ids = [e.id for e in errors]
        assert "djust.C301" not in ids

    def test_c302_fires_on_zero_max_entries(self):
        """max_entries < 1 must produce djust.C302 error."""
        errors = self._run_check(LIVEVIEW_CONFIG={"service_worker": {"vdom_cache_max_entries": 0}})
        ids = [e.id for e in errors]
        assert "djust.C302" in ids

    def test_c303_info_when_disabled(self):
        errors = self._run_check(LIVEVIEW_CONFIG={"service_worker": {"vdom_cache_enabled": False}})
        ids = [e.id for e in errors]
        assert "djust.C303" in ids

    def test_c303_silent_when_enabled(self):
        errors = self._run_check(LIVEVIEW_CONFIG={"service_worker": {"vdom_cache_enabled": True}})
        ids = [e.id for e in errors]
        assert "djust.C303" not in ids


# ---------------------------------------------------------------------------
# Config-layer smoke test — top-level setting aliases propagate.
# ---------------------------------------------------------------------------


class TestConfigAliases:
    def test_top_level_settings_aliases_reach_service_worker_dict(self):
        """Only ``DJUST_STATE_SNAPSHOT_ENABLED`` survives; 2.0 removed the
        ``DJUST_VDOM_CACHE_*`` aliases (``djust._removed_settings``)."""
        from django.test import override_settings
        from djust.config import LiveViewConfig

        with override_settings(DJUST_STATE_SNAPSHOT_ENABLED=False):
            cfg = LiveViewConfig()
            sw = cfg.get("service_worker")
            assert sw["state_snapshot_enabled"] is False


# ---------------------------------------------------------------------------
# Pipeline-review fixes 2026-04-23 (post Self-Review + Security retro).
# Covers Fix #1 (public_state wire emission), #4 (navigate passthrough),
# #6 (state_json size cap), #7 (keyset DoS cap), #8 (non-dict guard),
# #10 (PII regex extended), #11 (master-switch runtime), #12 (no exc leak).
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_allow_test_module")
class TestMountBatchNavigatePassthrough:
    """Fix #4 — redirect frames from on_mount hooks must propagate."""

    def test_redirect_hook_surfaces_in_mount_batch_navigate(self):
        """An on_mount hook returning a path must show up in navigate[]."""
        # Attach a redirect hook dynamically via the class attr. Use the
        # existing _SimpleCounter — we re-bind 'on_mount' on a subclass
        # to keep isolation.
        from djust.hooks import on_mount as on_mount_decorator

        @on_mount_decorator
        def _redirect_to_login(_view, _request, **_kwargs):
            return "/login"

        class _RedirectingView(_SimpleCounter):
            on_mount = [_redirect_to_login]

        # Register the new class under the module namespace so
        # handle_mount can import it by dotted path.
        module_name = __name__
        setattr(
            __import__(module_name, fromlist=["_RedirectingView"]),
            "_RedirectingView",
            _RedirectingView,
        )
        consumer = _make_fake_consumer()
        data = {
            "type": "mount_batch",
            "views": [
                {
                    "view": "%s._RedirectingView" % module_name,
                    "params": {},
                    "url": "/",
                    "target_id": "needs-redirect",
                },
                {
                    "view": "tests.unit.test_sw_advanced._SimpleCounter",
                    "params": {},
                    "url": "/",
                    "target_id": "ok-1",
                },
            ],
        }
        asyncio.run(consumer.handle_mount_batch(data))
        batch_frames = [f for f in consumer.sent_frames if f.get("type") == "mount_batch"]
        assert len(batch_frames) == 1
        batch = batch_frames[0]
        # Fix #4, as of #3252: the redirect is reported for that view alone in
        # refused[] (its container shows it; no page navigates, so no
        # navigate[]); the other view still mounts. The redirecting view must
        # NOT appear in views[] OR failed[] (a refusal, not a failure).
        assert "navigate" not in batch
        assert len(batch["refused"]) == 1
        assert batch["refused"][0]["reason"] == "redirect"
        assert batch["refused"][0]["to"] == "/login"
        assert batch["refused"][0]["target_id"] == "needs-redirect"
        # Successful mount for the non-redirecting view.
        survivor_ids = {v["target_id"] for v in batch["views"]}
        assert survivor_ids == {"ok-1"}
        failed_ids = {f["target_id"] for f in batch["failed"]}
        assert "needs-redirect" not in failed_ids


class TestPiiRegexExtended:
    """Fix #10 — regex must match common credential attr names."""

    @pytest.mark.parametrize(
        "name",
        [
            "password",
            "api_key",
            "apikey",
            "ssn",
            "credit_card",
            "ccnum",
            "bearer_header",
            "private_key",
            "privatekey",
            "auth_header",
            "sensitive_info",
            "user_credential",
        ],
    )
    def test_pii_regex_matches_common_credential_names(self, name):
        from djust.checks import _PII_NAME_PATTERN

        assert _PII_NAME_PATTERN.search(name) is not None
