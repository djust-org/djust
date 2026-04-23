"""Regression tests for issues #892 and #896.

Both harden the mixin-side-effect replay pattern (ADR-009) against
failure modes that #889 / #893 / #894 didn't address:

- **#892** — ``UploadMixin._restore_upload_configs`` splats each saved
  dict into ``allow_upload(**kwargs)``. If the allow_upload signature
  changed between the djust version that SAVED the session and the
  djust version that RESTORES it (unknown kwarg added, required kwarg
  renamed, etc.), the splat raises ``TypeError`` and kills the replay
  for every remaining slot on the page.

  Fix: wrap each replay in try/except TypeError; on mismatch log a
  WARNING identifying the slot + the mismatch, fall back to
  ``allow_upload(slot_name)`` — bare-minimum replay. Also: tag each
  saved dict with ``_upload_configs_version`` so future migrations
  have an explicit knob.

- **#896** — ``NotificationMixin._restore_listen_channels`` calls
  ``PostgresNotifyListener.instance().ensure_listening(channel)``.
  The singleton is loop-bound; if the loop it was initialized on is
  now closed (server restart with a fresh ASGI loop, test harness
  spinning up per-test loops), ``_assert_same_loop`` raises
  ``RuntimeError`` and the restore path silently drops NOTIFYs.

  Fix: detect the stranded singleton before replay (check
  ``listener._loop.is_closed()``) and call ``reset_for_new_loop()``
  to drop it; the next ``ensure_listening`` creates a fresh instance
  bound to the current loop. Also catch ``RuntimeError`` at the
  per-channel level as a belt-and-suspenders fallback.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Issue #892 — UploadMixin defensive replay for schema-changed configs
# ---------------------------------------------------------------------------


from djust.uploads import UploadMixin


class _UploadView(UploadMixin):
    """Concrete UploadMixin host."""


class TestUploadConfigsSchemaDrift:
    def test_saved_dict_has_version_tag(self):
        """Every saved dict carries ``_version`` so future restores can
        branch on it."""
        v = _UploadView()
        v.allow_upload("docs", accept=".jpg")
        assert v._upload_configs_saved[0]["_version"] == 1

    def test_restore_tolerates_unknown_kwarg(self, caplog):
        """If a saved dict contains a kwarg the current allow_upload()
        signature doesn't recognize (e.g. saved on a newer djust, being
        restored on an older one, or the kwarg was renamed), the replay
        MUST NOT raise. Falls back to allow_upload(name) with defaults.
        """
        v = _UploadView()
        # Simulate a saved config with a kwarg that doesn't exist on
        # the current allow_upload() — e.g. a future "priority" kwarg
        # that was saved in a later version and is being replayed on
        # an older one.
        v._upload_configs_saved = [
            {
                "name": "docs",
                "accept": ".jpg",
                "max_entries": 3,
                "max_file_size": 1_000_000,
                "chunk_size": 64 * 1024,
                "auto_upload": True,
                "_had_writer": False,
                "_version": 1,
                # THIS is the unknown kwarg that would raise TypeError:
                "priority": "high",
            }
        ]

        with caplog.at_level(logging.WARNING, logger="djust.uploads"):
            v._restore_upload_configs()  # must not raise

        # The slot still exists (fallback replay used defaults).
        assert v._upload_manager is not None
        assert "docs" in v._upload_manager._configs
        # The fallback used defaults, not the saved accept=.jpg
        # (we accept this trade-off — better to have a broken-but-
        # usable slot than crash the whole page).
        # The warning was logged identifying the slot.
        assert any("docs" in r.message and "issue #892" in r.message for r in caplog.records)

    def test_restore_with_missing_required_kwarg_falls_back_cleanly(self, caplog):
        """Hypothetical: allow_upload() gained a required positional
        kwarg in a future version, and the old saved dict doesn't have
        it. Same code path — TypeError — same recovery.
        """
        v = _UploadView()
        v._upload_configs_saved = [
            {
                # "name" is present — we need this to fall back
                "name": "photos",
                # But we inject an unknown kwarg to force TypeError
                "mystery_kwarg": "x",
            }
        ]

        with caplog.at_level(logging.WARNING, logger="djust.uploads"):
            v._restore_upload_configs()

        assert v._upload_manager is not None
        assert "photos" in v._upload_manager._configs

    def test_restore_continues_past_broken_slot(self, caplog):
        """One broken saved dict does NOT block later valid slots.
        This is the whole point — before the fix, the first TypeError
        killed the loop."""
        v = _UploadView()
        v._upload_configs_saved = [
            {"name": "broken", "mystery": "x"},  # will TypeError
            {
                "name": "good",
                "accept": ".png",
                "max_entries": 2,
                "max_file_size": 5_000_000,
                "chunk_size": 64 * 1024,
                "auto_upload": True,
                "_had_writer": False,
                "_version": 1,
            },
        ]

        with caplog.at_level(logging.WARNING, logger="djust.uploads"):
            v._restore_upload_configs()

        # BOTH slots exist on the manager (broken one via fallback,
        # good one via normal replay).
        assert v._upload_manager is not None
        assert "broken" in v._upload_manager._configs
        assert "good" in v._upload_manager._configs
        # The good slot got its real config, not defaults:
        good_cfg = v._upload_manager._configs["good"]
        assert good_cfg.accept == ".png"
        assert good_cfg.max_entries == 2

    def test_restore_with_no_slot_name_and_bad_kwargs_skips_entry(self, caplog):
        """If the saved dict is so broken it doesn't even have a
        ``name`` key, the fallback can't construct a slot — skip it
        rather than crash the restore path.
        """
        v = _UploadView()
        v._upload_configs_saved = [
            {"mystery": "x"},  # no name, no anything
            {
                "name": "good",
                "accept": ".png",
                "max_entries": 1,
                "max_file_size": 1_000_000,
                "chunk_size": 64 * 1024,
                "auto_upload": True,
                "_had_writer": False,
                "_version": 1,
            },
        ]

        with caplog.at_level(logging.WARNING, logger="djust.uploads"):
            v._restore_upload_configs()

        # Good slot survives; broken one silently skipped.
        assert v._upload_manager is not None
        assert "good" in v._upload_manager._configs
        assert "mystery" not in v._upload_manager._configs

    def test_version_tag_stripped_before_replay(self):
        """``_version`` is bookkeeping, not an allow_upload kwarg —
        stripping it prevents a spurious TypeError on valid configs."""
        v = _UploadView()
        v._upload_configs_saved = [
            {
                "name": "docs",
                "accept": ".jpg",
                "max_entries": 1,
                "max_file_size": 1_000_000,
                "chunk_size": 64 * 1024,
                "auto_upload": True,
                "_had_writer": False,
                "_version": 1,
            }
        ]
        v._restore_upload_configs()
        # No TypeError was raised, and the slot got the real config.
        assert v._upload_manager is not None
        cfg = v._upload_manager._configs["docs"]
        assert cfg.accept == ".jpg"


# ---------------------------------------------------------------------------
# Issue #896 — NotificationMixin cross-loop restore
# ---------------------------------------------------------------------------


from djust.mixins.notifications import NotificationMixin  # noqa: E402


class _NotifyView(NotificationMixin):
    """Concrete NotificationMixin host."""


class TestNotificationCrossLoopRestore:
    def test_restore_detects_closed_loop_and_resets_singleton(self, caplog):
        """If the singleton's bound loop is closed, the pre-check
        calls ``reset_for_new_loop`` and a fresh instance handles
        the replay. Verify the reset happened."""
        from djust.db.notifications import PostgresNotifyListener

        PostgresNotifyListener.reset_for_tests()  # clean start

        # Build a stranded singleton: bind its _loop to a closed loop.
        stale_loop = asyncio.new_event_loop()
        stale_loop.close()  # now asyncio.get_event_loop().is_closed() == True
        stale = PostgresNotifyListener.instance()
        stale._loop = stale_loop  # pretend the old loop went away

        # The stranded instance is the singleton right now.
        assert PostgresNotifyListener._instance is stale

        v = _NotifyView()
        v._listen_channels = {"orders"}

        called = []

        async def _spy(ch):
            called.append(ch)

        # After reset_for_new_loop, instance() returns a NEW object.
        # Patch that fresh instance's ensure_listening.
        original_instance = PostgresNotifyListener.instance

        def _instance_with_spy():
            inst = original_instance()
            # Only attach the spy if we got a fresh instance — we want
            # to verify the stale one was discarded.
            inst.ensure_listening = _spy
            return inst

        with patch.object(PostgresNotifyListener, "instance", _instance_with_spy):
            with caplog.at_level(logging.INFO, logger="djust.mixins.notifications"):
                v._restore_listen_channels()

        # Ensure listening was called on the fresh singleton.
        assert called == ["orders"]
        # We logged the reset.
        assert any(
            "closed event loop" in r.message or "issue #896" in r.message for r in caplog.records
        )
        # The old stranded singleton is no longer the current one.
        assert PostgresNotifyListener._instance is not stale

        # Cleanup so other tests in the suite get a clean slate.
        PostgresNotifyListener.reset_for_tests()

    def test_restore_retry_path_on_runtime_error_mentioning_event_loop(self, caplog):
        """Belt-and-suspenders: if the pre-check didn't catch the stale
        loop (e.g. the listener initializes asynchronously and the
        loop is fine *now* but goes away mid-call), the per-channel
        ``except RuntimeError`` branch catches it, resets, and retries
        once.
        """
        from djust.db.notifications import PostgresNotifyListener

        PostgresNotifyListener.reset_for_tests()

        v = _NotifyView()
        v._listen_channels = {"orders"}

        call_count = {"n": 0}

        async def _flaky(ch):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError(
                    "PostgresNotifyListener singleton is bound to a different "
                    "event loop than the calling coroutine."
                )
            # Retry succeeds.

        mock_listener = MagicMock()
        mock_listener.ensure_listening = _flaky
        # No _loop attr set => pre-check doesn't fire => we exercise
        # the per-channel except RuntimeError branch.
        mock_listener._loop = None

        with patch(
            "djust.db.notifications.PostgresNotifyListener.instance",
            return_value=mock_listener,
        ):
            with patch(
                "djust.db.notifications.PostgresNotifyListener.reset_for_new_loop"
            ) as mock_reset:
                with caplog.at_level(logging.INFO, logger="djust.mixins.notifications"):
                    v._restore_listen_channels()

        # Reset was called once (for the retry), and the retry succeeded
        # (call_count == 2).
        mock_reset.assert_called_once()
        assert call_count["n"] == 2
        assert any("cross-loop" in r.message for r in caplog.records)

        PostgresNotifyListener.reset_for_tests()

    def test_restore_no_reset_when_singleton_loop_is_open(self):
        """Happy path: the singleton's loop is still open (or never
        bound), no reset fires. Just verifies we don't over-eagerly
        reset on every restore."""
        from djust.db.notifications import PostgresNotifyListener

        PostgresNotifyListener.reset_for_tests()

        v = _NotifyView()
        v._listen_channels = {"orders"}

        called = []

        async def _spy(ch):
            called.append(ch)

        mock_listener = MagicMock()
        mock_listener.ensure_listening = _spy
        mock_listener._loop = None  # never bound — no reset needed

        with patch(
            "djust.db.notifications.PostgresNotifyListener.instance",
            return_value=mock_listener,
        ):
            with patch(
                "djust.db.notifications.PostgresNotifyListener.reset_for_new_loop"
            ) as mock_reset:
                v._restore_listen_channels()

        assert called == ["orders"]
        mock_reset.assert_not_called()

        PostgresNotifyListener.reset_for_tests()

    def test_reset_for_new_loop_drops_singleton(self):
        """Unit test for the new classmethod — isolates the
        reset-on-closed-loop primitive from the mixin call site."""
        from djust.db.notifications import PostgresNotifyListener

        PostgresNotifyListener.reset_for_tests()

        first = PostgresNotifyListener.instance()
        assert PostgresNotifyListener._instance is first

        PostgresNotifyListener.reset_for_new_loop()
        assert PostgresNotifyListener._instance is None

        second = PostgresNotifyListener.instance()
        assert second is not first

        PostgresNotifyListener.reset_for_tests()
