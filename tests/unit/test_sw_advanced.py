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
import json
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
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _SnapshotCounter(LiveView):
    """Counter with state-snapshot restoration opt-in."""

    enable_state_snapshot = True
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0  # Default — overridden by snapshot when opt-in fires.

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _SnapshotRejecting(LiveView):
    """Opt-in but rejects all snapshots via _should_restore_snapshot override."""

    enable_state_snapshot = True
    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.mount_was_called = True

    def _should_restore_snapshot(self, request):
        return False

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class _NonSnapshot(LiveView):
    """No enable_state_snapshot — default False; mount() must always run."""

    template = "<div dj-root><span>count={{ count }}</span></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.mount_was_called = True

    def get_context_data(self, **kwargs):
        return {"count": self.count}


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

    def test_restore_snapshot_applies_dict_keys(self):
        view = _SnapshotCounter()
        view.mount(None)
        assert view.count == 0
        view._restore_snapshot({"count": 42, "label": "restored"})
        assert view.count == 42
        assert view.label == "restored"

    def test_restore_snapshot_blocks_dunder(self):
        """safe_setattr refuses dunder keys."""
        view = _SnapshotCounter()
        original_class = view.__class__
        view._restore_snapshot({"__class__": object, "count": 99})
        # __class__ blocked; count allowed.
        assert view.__class__ is original_class
        assert view.count == 99

    def test_restore_snapshot_blocks_private(self):
        """safe_setattr with allow_private=False refuses _-prefixed keys."""
        view = _SnapshotCounter()
        view._restore_snapshot({"_private_thing": "nope", "count": 3})
        assert not hasattr(view, "_private_thing")
        assert view.count == 3

    def test_should_restore_snapshot_default_true(self):
        view = _SnapshotCounter()
        assert view._should_restore_snapshot(None) is True

    def test_should_restore_snapshot_override_can_reject(self):
        view = _SnapshotRejecting()
        assert view._should_restore_snapshot(None) is False

    def test_enable_state_snapshot_default_false(self):
        """Opt-in flag MUST default to False — security-critical."""
        assert LiveView.enable_state_snapshot is False
        assert _NonSnapshot.enable_state_snapshot is False
        assert _SnapshotCounter.enable_state_snapshot is True


# ---------------------------------------------------------------------------
# Consumer shim — bypasses Channels ASGI wiring. Mirrors the pattern in
# tests/integration/test_sticky_redirect_flow.py so we can drive
# handle_mount / handle_mount_batch without a full channel layer.
# ---------------------------------------------------------------------------


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
            self.use_actors = False
            self.actor_handle = None
            # Rate limiter — handle_mount_batch goes through ``receive``
            # but our tests call handle_mount_batch directly so we skip it.
            self._debug_panel_active = False

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

        def _flush_push_events(self):
            return None

    return _FakeConsumer()


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


@pytest.mark.usefixtures("_allow_test_module")
class TestStateSnapshotMountIntegration:
    def _run(self, view_path: str, state_snapshot=None):
        consumer = _make_fake_consumer()
        data = {
            "view": view_path,
            "params": {},
            "url": "/",
            "has_prerendered": False,
        }
        asyncio.run(consumer.handle_mount(data, state_snapshot=state_snapshot))
        return consumer

    def test_state_snapshot_restored_when_enabled(self):
        """Snapshot with matching view_slug restores count, skipping mount()."""
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotCounter",
            "state_json": json.dumps({"count": 7, "label": "from-snapshot"}),
            "ts": 0,
        }
        consumer = self._run(
            "tests.unit.test_sw_advanced._SnapshotCounter",
            state_snapshot=snapshot,
        )
        # mount() sets count=0; snapshot overwrites to 7.
        assert consumer.view_instance.count == 7
        assert consumer.view_instance.label == "from-snapshot"

    def test_state_snapshot_ignored_when_class_attr_false(self):
        """enable_state_snapshot=False (default) ignores snapshot."""
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._NonSnapshot",
            "state_json": json.dumps({"count": 99}),
            "ts": 0,
        }
        consumer = self._run(
            "tests.unit.test_sw_advanced._NonSnapshot",
            state_snapshot=snapshot,
        )
        # mount() was called normally — count stays at 0.
        assert consumer.view_instance.count == 0
        assert consumer.view_instance.mount_was_called is True

    def test_state_snapshot_rejected_by_should_restore_snapshot(self):
        """Override returning False falls back to mount()."""
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotRejecting",
            "state_json": json.dumps({"count": 50}),
            "ts": 0,
        }
        consumer = self._run(
            "tests.unit.test_sw_advanced._SnapshotRejecting",
            state_snapshot=snapshot,
        )
        # mount() ran because _should_restore_snapshot returned False.
        assert consumer.view_instance.count == 0
        assert consumer.view_instance.mount_was_called is True

    def test_state_snapshot_malformed_json_falls_back_to_mount(self):
        """Malformed state_json is logged and fresh mount proceeds."""
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotCounter",
            "state_json": "{not-valid-json",
            "ts": 0,
        }
        consumer = self._run(
            "tests.unit.test_sw_advanced._SnapshotCounter",
            state_snapshot=snapshot,
        )
        # Fallback fresh mount — count is 0 (not NaN/raise).
        assert consumer.view_instance.count == 0

    def test_state_snapshot_view_slug_mismatch_rejected(self):
        """Snapshot for a different view_slug must be ignored."""
        snapshot = {
            "view_slug": "some.other.View",  # Doesn't match
            "state_json": json.dumps({"count": 77}),
            "ts": 0,
        }
        consumer = self._run(
            "tests.unit.test_sw_advanced._SnapshotCounter",
            state_snapshot=snapshot,
        )
        # Slug mismatch — mount() ran, count is 0.
        assert consumer.view_instance.count == 0


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
        errors = self._run_check(DJUST_VDOM_CACHE_TTL_SECONDS=0)
        ids = [e.id for e in errors]
        assert "djust.C301" in ids

    def test_c301_fires_on_negative_ttl(self):
        errors = self._run_check(DJUST_VDOM_CACHE_TTL_SECONDS=-60)
        ids = [e.id for e in errors]
        assert "djust.C301" in ids

    def test_c301_clean_for_positive_ttl(self):
        errors = self._run_check(DJUST_VDOM_CACHE_TTL_SECONDS=1800)
        ids = [e.id for e in errors]
        assert "djust.C301" not in ids

    def test_c302_fires_on_zero_max_entries(self):
        """max_entries < 1 must produce djust.C302 error."""
        errors = self._run_check(DJUST_VDOM_CACHE_MAX_ENTRIES=0)
        ids = [e.id for e in errors]
        assert "djust.C302" in ids

    def test_c303_info_when_disabled(self):
        errors = self._run_check(DJUST_VDOM_CACHE_ENABLED=False)
        ids = [e.id for e in errors]
        assert "djust.C303" in ids

    def test_c303_silent_when_enabled(self):
        errors = self._run_check(DJUST_VDOM_CACHE_ENABLED=True)
        ids = [e.id for e in errors]
        assert "djust.C303" not in ids


# ---------------------------------------------------------------------------
# Config-layer smoke test — top-level setting aliases propagate.
# ---------------------------------------------------------------------------


class TestConfigAliases:
    def test_top_level_settings_aliases_reach_service_worker_dict(self):
        from django.test import override_settings
        from djust.config import LiveViewConfig

        with override_settings(
            DJUST_VDOM_CACHE_ENABLED=False,
            DJUST_VDOM_CACHE_TTL_SECONDS=42,
            DJUST_VDOM_CACHE_MAX_ENTRIES=7,
            DJUST_STATE_SNAPSHOT_ENABLED=False,
        ):
            cfg = LiveViewConfig()
            sw = cfg.get("service_worker")
            assert sw["vdom_cache_enabled"] is False
            assert sw["vdom_cache_ttl_seconds"] == 42
            assert sw["vdom_cache_max_entries"] == 7
            assert sw["state_snapshot_enabled"] is False


# ---------------------------------------------------------------------------
# Pipeline-review fixes 2026-04-23 (post Self-Review + Security retro).
# Covers Fix #1 (public_state wire emission), #4 (navigate passthrough),
# #6 (state_json size cap), #7 (keyset DoS cap), #8 (non-dict guard),
# #10 (PII regex extended), #11 (master-switch runtime), #12 (no exc leak).
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_allow_test_module")
class TestMountFramePublicStateWiring:
    """Fix #1 — server emits public_state on mount frame when opt-in."""

    def test_mount_frame_carries_public_state_for_opt_in_view(self):
        consumer = _make_fake_consumer()
        data = {
            "view": "tests.unit.test_sw_advanced._SnapshotCounter",
            "params": {},
            "url": "/",
            "has_prerendered": False,
        }
        asyncio.run(consumer.handle_mount(data))
        mount_frames = [f for f in consumer.sent_frames if f.get("type") == "mount"]
        assert len(mount_frames) == 1
        frame = mount_frames[0]
        # Opt-in class → public_state must be present and include count.
        assert "public_state" in frame
        assert frame["public_state"].get("count") == 0

    def test_mount_frame_omits_public_state_for_non_opt_in(self):
        consumer = _make_fake_consumer()
        data = {
            "view": "tests.unit.test_sw_advanced._NonSnapshot",
            "params": {},
            "url": "/",
            "has_prerendered": False,
        }
        asyncio.run(consumer.handle_mount(data))
        mount_frames = [f for f in consumer.sent_frames if f.get("type") == "mount"]
        assert len(mount_frames) == 1
        # Non-opt-in → no public_state key emitted.
        assert "public_state" not in mount_frames[0]

    def test_mount_frame_omits_public_state_when_master_switch_off(self, settings):
        settings.DJUST_STATE_SNAPSHOT_ENABLED = False
        consumer = _make_fake_consumer()
        data = {
            "view": "tests.unit.test_sw_advanced._SnapshotCounter",
            "params": {},
            "url": "/",
            "has_prerendered": False,
        }
        asyncio.run(consumer.handle_mount(data))
        mount_frames = [f for f in consumer.sent_frames if f.get("type") == "mount"]
        assert len(mount_frames) == 1
        # Master switch disabled → opt-in view still suppresses emission.
        assert "public_state" not in mount_frames[0]


@pytest.mark.usefixtures("_allow_test_module")
class TestStateSnapshotGuards:
    """Fixes #6/#7/#8/#11 — server-side state_snapshot input guards."""

    def _run_with_snapshot(self, snapshot):
        consumer = _make_fake_consumer()
        data = {
            "view": "tests.unit.test_sw_advanced._SnapshotCounter",
            "params": {},
            "url": "/",
            "has_prerendered": False,
        }
        asyncio.run(consumer.handle_mount(data, state_snapshot=snapshot))
        return consumer

    def test_state_json_over_64kb_rejected(self):
        """Fix #6 — state_json > 64 KB → fall back to mount()."""
        big = "x" * (65 * 1024)
        # Wrap in a valid JSON string so only the size check triggers.
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotCounter",
            "state_json": '{"payload":"%s"}' % big,
            "ts": 0,
        }
        consumer = self._run_with_snapshot(snapshot)
        # Fresh mount → count == 0 (mount overwrote via self.count = 0).
        assert consumer.view_instance.count == 0

    def test_state_json_keyset_over_256_rejected(self):
        """Fix #7 — >256 keys rejected regardless of total byte size."""
        big_dict = {"k_%d" % i: i for i in range(300)}
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotCounter",
            "state_json": json.dumps(big_dict),
            "ts": 0,
        }
        consumer = self._run_with_snapshot(snapshot)
        # No key from the big dict made it onto the view.
        for i in range(300):
            assert not hasattr(consumer.view_instance, "k_%d" % i)

    def test_state_json_array_not_dict_rejected(self):
        """Fix #8 — non-dict JSON (array / number / string) is ignored."""
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotCounter",
            "state_json": "[1, 2, 3]",
            "ts": 0,
        }
        consumer = self._run_with_snapshot(snapshot)
        # Fresh mount — count stays at 0, no state error raised.
        assert consumer.view_instance.count == 0

    def test_master_switch_disables_snapshot_restoration(self, settings):
        """Fix #11 — DJUST_STATE_SNAPSHOT_ENABLED=False halts restoration."""
        settings.DJUST_STATE_SNAPSHOT_ENABLED = False
        snapshot = {
            "view_slug": "tests.unit.test_sw_advanced._SnapshotCounter",
            "state_json": json.dumps({"count": 99}),
            "ts": 0,
        }
        consumer = self._run_with_snapshot(snapshot)
        # Master switch off → fresh mount, count == 0, not 99.
        assert consumer.view_instance.count == 0


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
        # Fix #4 — the redirect appears in navigate[]; the other view
        # still mounts. The redirecting view must NOT appear in views[]
        # OR failed[] (it's a legitimate redirect, not a failure).
        assert "navigate" in batch
        assert len(batch["navigate"]) == 1
        assert batch["navigate"][0]["to"] == "/login"
        assert batch["navigate"][0]["target_id"] == "needs-redirect"
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
