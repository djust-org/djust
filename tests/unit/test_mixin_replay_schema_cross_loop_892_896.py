"""Regression tests for issues #892 and #896.

(2.0: the replays these issues hardened, ``_restore_upload_configs`` and
``_restore_listen_channels``, ran only on the WebSocket session restore,
removed with ``enable_state_snapshot``. They went with it; what is left here
is the recording ``allow_upload()`` still does and the listener's loop reset.)

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


# ---------------------------------------------------------------------------
# Issue #896 — NotificationMixin cross-loop restore
# ---------------------------------------------------------------------------


from djust.mixins.notifications import NotificationMixin  # noqa: E402


class _NotifyView(NotificationMixin):
    """Concrete NotificationMixin host."""


class TestNotificationCrossLoopRestore:
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
