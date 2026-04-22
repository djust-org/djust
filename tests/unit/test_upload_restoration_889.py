"""Regression tests for issue #889 — UploadMixin state restoration.

Bug: after a pre-rendered HTTP load + session save + WS mount, the WS
consumer's restoration path skipped ``mount()`` and ``_upload_manager``
was left at the class-default ``None`` (the live ``UploadManager``
instance from the HTTP mount isn't JSON-serializable, so it never
made it into the session). Result: the next upload request hit
``_handle_upload_register`` which responded "No uploads configured
for this view".

Fix: ``allow_upload()`` now also records a JSON-serializable list of
call kwargs in ``self._upload_configs_saved``. The list rides the
session round-trip. The WS consumer's restoration path calls the
new ``_restore_upload_configs()`` which replays the saved calls and
rebuilds the manager on the WS-side view instance.
"""

from __future__ import annotations

import json
import logging

from djust.uploads import UploadManager, UploadMixin


class _View(UploadMixin):
    """Concrete mixin host with no other bases — pure UploadMixin exercise."""


class TestAllowUploadTracking:
    def test_allow_upload_records_config_in_saved_list(self):
        v = _View()
        assert v._upload_configs_saved is None  # class default
        v.allow_upload("docs", accept=".jpg,.pdf", max_entries=5)

        assert isinstance(v._upload_configs_saved, list)
        assert len(v._upload_configs_saved) == 1
        entry = v._upload_configs_saved[0]
        assert entry["name"] == "docs"
        assert entry["accept"] == ".jpg,.pdf"
        assert entry["max_entries"] == 5
        # max_file_size/chunk_size/auto_upload defaults captured too
        assert "max_file_size" in entry
        assert "chunk_size" in entry
        assert entry["auto_upload"] is True
        assert entry["_had_writer"] is False

    def test_allow_upload_records_writer_marker(self):
        """Custom writer classes are non-serializable; we record only a
        flag so the replay path can warn."""
        from djust.uploads import BufferedUploadWriter

        v = _View()
        v.allow_upload("avatar", writer=BufferedUploadWriter)
        assert v._upload_configs_saved[0]["_had_writer"] is True
        # The writer class itself is NOT in the saved dict.
        assert "writer" not in v._upload_configs_saved[0]

    def test_multiple_allow_upload_calls_all_recorded(self):
        v = _View()
        v.allow_upload("a")
        v.allow_upload("b")
        v.allow_upload("c")
        assert [e["name"] for e in v._upload_configs_saved] == ["a", "b", "c"]

    def test_saved_list_is_json_serializable(self):
        """The whole point — the list must survive a JSON round-trip."""
        v = _View()
        v.allow_upload("docs", accept=".jpg", max_entries=3)
        v.allow_upload("photos", accept=".png,.jpeg", max_entries=10)

        blob = json.dumps(v._upload_configs_saved)
        restored = json.loads(blob)
        assert restored == v._upload_configs_saved


class TestRestoreUploadConfigs:
    def test_restore_rebuilds_upload_manager(self):
        """Simulates the post-session-restore state: the saved list is
        present, but _upload_manager is None (it was dropped as
        non-serializable). After restore, the manager exists and has
        the right configs.
        """
        v = _View()
        v.allow_upload("docs", accept=".jpg,.pdf", max_entries=5)

        # Simulate what _get_private_state/JSON/deserialize would do:
        # the manager is dropped, only the saved list remains.
        saved_list = list(v._upload_configs_saved)
        v._upload_manager = None
        v._upload_configs_saved = saved_list

        # Before restoration — the bug symptom.
        assert v._upload_manager is None
        assert not (hasattr(v, "_upload_manager") and v._upload_manager)

        # Restoration replays the saved calls.
        v._restore_upload_configs()

        # After restoration — the consumer-style check passes.
        assert hasattr(v, "_upload_manager") and v._upload_manager
        assert isinstance(v._upload_manager, UploadManager)
        # The original config is reconstructed.
        cfg = v._upload_manager._configs["docs"]
        assert cfg.accept == ".jpg,.pdf"
        assert cfg.max_entries == 5

    def test_restore_with_no_saved_is_noop(self):
        v = _View()
        assert v._upload_configs_saved is None
        v._restore_upload_configs()  # must not raise
        assert v._upload_manager is None

    def test_restore_with_empty_list_is_noop(self):
        v = _View()
        v._upload_configs_saved = []
        v._restore_upload_configs()
        assert v._upload_manager is None

    def test_restore_is_idempotent_across_multiple_calls(self):
        """Second restore (after the first has already run) must not
        duplicate configs. The first call resets ``_upload_configs_saved``
        so the second call sees an empty list and no-ops."""
        v = _View()
        v.allow_upload("docs", accept=".jpg")
        v.allow_upload("photos", accept=".png")

        # Snapshot for bookkeeping
        saved_before = list(v._upload_configs_saved)
        v._upload_manager = None
        v._upload_configs_saved = list(saved_before)

        v._restore_upload_configs()
        first_mgr = v._upload_manager
        first_names = set(first_mgr._configs)

        # Second restore — configs_saved was rebuilt by allow_upload(),
        # so calling restore again replays them on top of the same
        # manager (idempotent from the manager's perspective: configure
        # overwrites existing slots with the same name).
        v._restore_upload_configs()
        assert v._upload_manager is first_mgr  # same instance
        assert set(first_mgr._configs) == first_names

    def test_restore_warns_on_writer_marker(self, caplog):
        """Configs recorded with a writer= get a warning at replay
        time — the writer class isn't round-trippable."""
        v = _View()
        from djust.uploads import BufferedUploadWriter

        v.allow_upload("avatar", writer=BufferedUploadWriter)

        # Simulate round-trip: drop manager, preserve saved list
        saved = list(v._upload_configs_saved)
        v._upload_manager = None
        v._upload_configs_saved = saved

        with caplog.at_level(logging.WARNING, logger="djust.uploads"):
            v._restore_upload_configs()

        assert any("writer=" in r.message for r in caplog.records)
        # But the manager IS rebuilt (with the default buffered writer).
        assert v._upload_manager is not None
        assert "avatar" in v._upload_manager._configs


class TestEndToEndSessionRoundTrip:
    """Drive the full HTTP→session→WS-restore flow in Python, verifying
    the bug from the issue (#889) is fixed end-to-end."""

    def test_http_mount_then_session_restore_preserves_uploads(self):
        from djust.live_view import LiveView

        class MyUploadView(UploadMixin, LiveView):
            template_name = "ignored.html"

            def mount(self, request, **kwargs):
                self.allow_upload(
                    "docs", accept=".jpg,.pdf", max_entries=10, max_file_size=5_000_000
                )

        # 1. HTTP mount
        http_view = MyUploadView()

        class _R:
            method = "GET"
            path = "/"
            GET = {}
            user = None

        http_view.mount(_R())
        http_view._snapshot_user_private_attrs()
        assert http_view._upload_manager is not None  # HTTP path works

        # 2. Session save — only JSON-serializable parts
        private_state = http_view._get_private_state()
        # _upload_manager gets dropped (non-serializable).
        assert "_upload_manager" not in private_state
        # _upload_configs_saved MUST be present and correct.
        assert "_upload_configs_saved" in private_state
        blob = json.dumps(private_state)  # must not raise

        # 3. WS-side: fresh instance, restore from session
        ws_view = MyUploadView()
        restored_state = json.loads(blob)
        ws_view._restore_private_state(restored_state)
        # At this point the bug: manager is None.
        assert ws_view._upload_manager is None

        # 4. Apply the fix: replay the saved config list.
        ws_view._restore_upload_configs()

        # 5. Consumer-style check now passes.
        assert hasattr(ws_view, "_upload_manager") and ws_view._upload_manager
        cfg = ws_view._upload_manager._configs["docs"]
        assert cfg.max_entries == 10
        assert cfg.max_file_size == 5_000_000
