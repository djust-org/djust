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
  ``NonPersistableStateError`` (a ``TypeError`` subclass) with an actionable
  message (store the pk, refetch in the handler) — matching the existing
  ``get_state()`` DEBUG-friendly-error convention (``live_view.py``
  ~1019-1028). The dedicated class exists so the REAL mount-path caller
  (``runtime.py``'s snapshot emission, wrapped in a broad
  ``except Exception`` per the #1788 "snapshot emission must never break
  mount" posture) can re-raise the deliberate rejection instead of
  swallowing it — without the re-raise, DEBUG silently degraded to
  log-and-continue on the real path and the "raises in DEBUG" claim was
  only true for direct method calls (review finding on PR #2022).
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
from djust.live_view import NonPersistableStateError


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
        # The dedicated subclass is what lets the runtime's #1788 fail-soft
        # wrapper re-raise this deliberate rejection (see
        # TestRealMountPathDebugFailsLoud below).
        assert isinstance(exc_info.value, NonPersistableStateError)

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


# --------------------------------------------------------------------------- #
# REAL mount path (review finding on PR #2022): the only strict=True caller
# (``runtime.py`` snapshot emission) wraps the capture in a broad
# ``except Exception`` (#1788 "snapshot emission must never break mount").
# Before the ``NonPersistableStateError`` re-raise, that wrapper swallowed the
# DEBUG rejection, so the real mount path logged-and-continued and the
# "raises in DEBUG" claim held only for DIRECT ``_capture_snapshot_state``
# calls (the classes above). These tests drive ``ViewRuntime.dispatch_mount``
# end-to-end — reproduction fidelity per the CLAUDE.md triage canon: the
# harness must exercise the real path, not a convenient proxy.
# --------------------------------------------------------------------------- #


class _RealPathOrmView(LiveView):
    """Opt-in snapshot view with an ORM object on public state, renderable
    end-to-end (the JIT rendering path handles the Model fine — the bug lives
    exclusively in the persistence capture that follows the render)."""

    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">{{ label }}</div>'

    def mount(self, request, **kwargs):
        self.user = _make_user()
        self.label = "safe-scalar"

    def get_context_data(self, **kwargs):
        return {"label": self.label}


_REAL_PATH_VIEW = f"{__name__}._RealPathOrmView"
_REAL_PATH_URL = "/orm-snap/"


@pytest.mark.django_db
class TestRealMountPathDebugFailsLoud:
    """DEBUG + the REAL runtime mount path: the rejection must propagate out
    of ``dispatch_mount`` (fail loud), not be swallowed into a log line by
    the #1788 fail-soft wrapper."""

    @pytest.mark.asyncio
    async def test_debug_rejection_propagates_out_of_dispatch_mount(self):
        from asgiref.sync import sync_to_async

        from djust.tests.test_runtime_mount_state_restore_1913 import (
            _make_db_session,
            _make_request,
            _make_runtime,
        )

        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)

        with override_settings(DEBUG=True, LIVEVIEW_ALLOWED_MODULES=["djust"]):
            with pytest.raises(NonPersistableStateError) as exc_info:
                await runtime.dispatch_mount(
                    {"type": "mount", "view": _REAL_PATH_VIEW, "url": _REAL_PATH_URL}
                )

        # The actionable guidance survives the real path end-to-end.
        assert "pk" in str(exc_info.value)
        # Fail LOUD means the mount frame was never shipped — the developer
        # sees the error instead of a half-mounted view missing state.
        assert transport.mount_frame is None

    @pytest.mark.asyncio
    async def test_production_mount_survives_and_skips_orm_key(self, caplog):
        """Production on the SAME real path: mount succeeds (#1788 posture
        intact), the ORM key is skipped from the signed blob, the sibling
        scalar persists, and the skip is warned. Doubles as the gate-off
        sibling proving the re-raise is scoped to DEBUG — if the re-raise
        ever fired in production, this mount would crash."""
        import json as _json

        from asgiref.sync import sync_to_async

        from djust.security import unsign_snapshot
        from djust.tests.test_runtime_mount_state_restore_1913 import (
            _make_db_session,
            _make_request,
            _make_runtime,
        )

        session = await sync_to_async(_make_db_session)()
        request = _make_request(session)
        runtime, transport = _make_runtime(request)

        with override_settings(DEBUG=False, LIVEVIEW_ALLOWED_MODULES=["djust"]):
            with caplog.at_level(logging.WARNING, logger="djust.live_view"):
                await runtime.dispatch_mount(
                    {"type": "mount", "view": _REAL_PATH_VIEW, "url": _REAL_PATH_URL}
                )

        frame = transport.mount_frame
        assert frame is not None, "production mount must never break over this (#1788)"

        signed = frame.get("state_snapshot_signed")
        assert isinstance(signed, str) and signed
        inner = unsign_snapshot(signed, _REAL_PATH_VIEW, session.session_key)
        assert inner is not None
        persisted = _json.loads(inner)
        assert "user" not in persisted, "ORM object must be skipped from the signed blob"
        assert persisted.get("label") == "safe-scalar"
        assert any("user" in rec.message for rec in caplog.records)
