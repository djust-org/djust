"""Regression tests — early, actionable error for ORM objects in LiveView
state PERSISTENCE (the ``enable_state_snapshot`` back-navigation path), as
distinct from the rendering JIT context-serialization path.

Bug: ``LiveView._is_serializable`` (used by ``get_state()``) intentionally
passes Django ``Model``/``QuerySet`` instances through as ``True`` — the
rendering JIT pipeline knows how to serialize them into a template context
(see ``live_view.py`` comment "serialized by JIT pipeline"). That's correct
for rendering.

But ``_capture_snapshot_state`` (the PUBLIC-state persistence path feeding
the signed ``state_snapshot_signed`` mount payload — ``runtime.py``
``handle_mount``) reuses ``djust.serialization.DjangoJSONEncoder``, which
*also* knows how to serialize a ``Model`` via ``_serialize_model_safely``.
So a Model stored on a public LiveView attr (e.g. ``self.user = request.user``
in ``mount()``) does NOT raise or get skipped here — it silently succeeds,
converting the live model into a plain field-value ``dict``. On the
back-navigation restore path (``_restore_snapshot``), that dict is
``safe_setattr``'d back verbatim: ``self.user`` comes back a ``dict``, not a
``User`` instance, and any handler calling a model method on it
(``self.user.get_full_name()``) breaks with a confusing, origin-unclear
``AttributeError`` far from the actual mistake (storing the model in the
first place).

This is the sibling of #1994 (private-attr model→dict round-trip, fixed by
re-hydrating a DB ref in ``encode_private_model_refs``/
``decode_private_model_refs``) but for PUBLIC, client-signed state. Public
state a client can influence should not attempt automatic re-hydration by
pk (that is exactly the mass-assignment shape ``state_snapshot_signed``'s
HMAC signing was built to guard against) — so the fix here is instead to
FAIL LOUD, EARLY, in the persistence path only:

* In ``DEBUG`` (dev), ``_capture_snapshot_state(strict=True)`` raises
  ``TypeError`` with an actionable message (store the pk, refetch in the
  handler) — matching the existing ``get_state()`` DEBUG-friendly-error
  convention (``live_view.py`` ~1019-1028).
* In production, it logs a warning and skips the attribute (never crashes
  a mount/reconnect over this).

Two contexts share ``_capture_snapshot_state`` and MUST NOT be conflated
(the core trap this fix has to avoid):

1. The rendering JIT context-serialization path (``_is_serializable`` /
   ``get_state()`` itself) — MUST be unaffected; Model/QuerySet must keep
   rendering normally.
2. The dev-only time-travel debug capture (``time_travel.py``, which calls
   ``_capture_snapshot_state()`` with NO ``strict`` kwarg to record
   ``state_before``/``state_after`` for the replay ring buffer) — MUST
   also be unaffected; it already accepts a lossy, disconnected snapshot
   by design, and is not the client-signed persistence path this fix
   targets. Only ``strict=True`` (the real ``runtime.py``
   ``state_snapshot_signed`` mount-emission caller) triggers the new
   rejection.
"""

from __future__ import annotations

import logging

import pytest
from django.test import override_settings

from djust import LiveView


def _make_user(*, username="alice", pk=7):
    from django.contrib.auth.models import User

    user = User(username=username, email=f"{username}@example.com")
    user.pk = pk
    user.id = pk
    return user


class _PublicOrmStateView(LiveView):
    """Opt-in snapshot view storing a Model instance on PUBLIC state."""

    enable_state_snapshot = True
    template_name = "test.html"

    def mount(self, request, **kwargs):
        self.user = _make_user()
        self.label = "safe-scalar"


class TestCaptureSnapshotStateRejectsOrmObjectsInDebug:
    """DEBUG + strict=True (the real client-signed persistence caller):
    ``_capture_snapshot_state`` must raise a clear, actionable error — not
    silently convert the model to a dict."""

    @override_settings(DEBUG=True)
    def test_model_instance_raises_actionable_type_error(self):
        view = _PublicOrmStateView()
        view.mount(None)

        with pytest.raises(TypeError) as exc_info:
            view._capture_snapshot_state(strict=True)

        msg = str(exc_info.value)
        assert "user" in msg
        assert "User" in msg
        # Actionable guidance: store the pk, refetch in the handler.
        assert "pk" in msg

    @override_settings(DEBUG=True)
    def test_queryset_raises_actionable_type_error(self):
        from django.contrib.auth.models import User

        class QsView(LiveView):
            enable_state_snapshot = True
            template_name = "test.html"

            def mount(self, request, **kwargs):
                self.candidates = User.objects.none()

        view = QsView()
        view.mount(None)

        with pytest.raises(TypeError) as exc_info:
            view._capture_snapshot_state(strict=True)

        assert "candidates" in str(exc_info.value)


class TestCaptureSnapshotStateSkipsOrmObjectsInProduction:
    """Production (``DEBUG=False``) + strict=True: warn + skip, never crash
    the mount/reconnect."""

    @override_settings(DEBUG=False)
    def test_model_instance_is_skipped_not_silently_converted_to_dict(self, caplog):
        view = _PublicOrmStateView()
        view.mount(None)

        with caplog.at_level(logging.WARNING, logger="djust.live_view"):
            state = view._capture_snapshot_state(strict=True)

        assert "user" not in state, (
            "ORM object must be excluded from the persisted public-state "
            "snapshot, not silently degraded into a plain dict."
        )
        assert state.get("label") == "safe-scalar"  # sibling scalar unaffected
        assert any("user" in rec.message for rec in caplog.records), (
            "Skipping an ORM object from state persistence must log a warning "
            "so the developer isn't left silently missing state."
        )


class TestNonStrictCallersUnaffected:
    """Non-strict callers (default) — e.g. the dev-only time-travel debug
    capture in ``time_travel.py`` — MUST keep their pre-existing behavior:
    no raise, and the model still silently becomes a dict (accepted,
    pre-existing trade-off for that debug-only feature; not this fix's
    target). Only the real ``runtime.py`` persistence caller opts into
    ``strict=True``."""

    @override_settings(DEBUG=True)
    def test_default_call_does_not_raise_even_in_debug(self):
        view = _PublicOrmStateView()
        view.mount(None)

        state = view._capture_snapshot_state()  # no strict= kwarg — old behavior

        assert "user" in state  # still present, still a plain dict (unaffected)
        assert isinstance(state["user"], dict)


class TestRenderingJitPipelineUnaffected:
    """The rendering-context path (``_is_serializable`` / ``get_state()``) must
    NOT be touched by this fix — Model/QuerySet keep rendering normally."""

    @override_settings(DEBUG=True)
    def test_is_serializable_still_true_for_model(self):
        user = _make_user()
        assert LiveView._is_serializable(user) is True

    @override_settings(DEBUG=True)
    def test_get_state_still_returns_raw_model_for_rendering(self):
        """``get_state()`` (JIT rendering context) must still hand back the
        live Model instance untouched — only the snapshot-persistence path
        (``_capture_snapshot_state``) gets the new early rejection."""

        view = _PublicOrmStateView()
        view.mount(None)

        state = view.get_state()
        assert state["user"] is view.user
