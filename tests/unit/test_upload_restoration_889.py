"""Regression tests for issue #889 — UploadMixin state restoration.

(2.0: the replays these issues hardened, ``_restore_upload_configs`` and
``_restore_listen_channels``, ran only on the WebSocket session restore,
removed with ``enable_state_snapshot``. They went with it; what is left here
is the recording ``allow_upload()`` still does and the listener's loop reset.)

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

from djust.uploads import UploadMixin


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
