"""Transport BEHAVIORAL-parity nets (Iter 0 / #1885, ADR-022).

The security nets in ``test_transport_parity_security.py`` pin the SECURITY
re-forks across transports (mount-URL traversal, import allowlist, view auth,
object permission, rate limit, origin/host). They do NOT pin *behavioral*
parity between the WS handlers and ``ViewRuntime`` — and the Stage-4 scoping for
the ViewRuntime convergence (ADR-022) found the runtime had already DRIFTED from
WS on exactly that axis:

* ``ViewRuntime.dispatch_mount`` did NOT run the post-mount object-permission
  check the WS ``handle_mount`` runs (a latent IDOR — not live yet because nothing
  mounts through the runtime, but it would go live the instant Iter 1 routes SSE
  through the runtime). Fixed in Iter 0 (``runtime.py`` dispatch_mount now routes
  through the shared ``enforce_object_permission`` chokepoint).

* The runtime drained only 3 of WS's 8 flush queues (push_events / navigation /
  deferred), silently dropping flash / page_metadata / layout / a11y / i18n on
  its one production user, ``url_change`` (a live #1646 instance). Fixed in Iter 0
  (the runtime now has a single ``_flush_all_pending`` that drains all 8 in WS's
  canonical order, called from both turn-end sites).

This file is the ENFORCEMENT net for those two fixes plus the wire-version pin:

1. **Flush-queue-count parity** — AST-compares the WS ``_flush_all_pending`` body
   to the runtime ``_flush_all_pending`` body and asserts they drain the SAME
   ordered set of ``_flush_*`` queues. A future drop of a queue from EITHER path
   re-forks this RED (mechanical drift detection, #1646 / #1125).

2. **Object-permission parity** — drives the REAL ``dispatch_mount`` against a
   view whose ``has_object_permission`` returns ``False`` and asserts the denied
   object is NEVER rendered or sent (only a ``permission_denied`` error frame is
   emitted, and ``view_instance`` is cleared). Gate-off (#1468): remove the
   ``enforce_object_permission`` call from ``dispatch_mount`` → this goes RED.

3. **Wire-version parity** — asserts ``dispatch_url_change`` stamps the
   transport-owned wire version via ``transport.next_client_version`` (already
   true post-#1858; pinned here so a future render-branch edit can't drop it).

4. **WS-only-behavior enumeration** — a count-test enumerating the mount + event
   behaviors that live ONLY on the WS path today (per ADR-022's refined
   sequencing). If a future iteration MOVES one of these onto the runtime, the
   pin trips and must be updated deliberately — keeping the WS↔runtime delta
   visible as the convergence proceeds (Iters 1-3).
"""

import ast
import contextlib
import pathlib
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.test import override_settings

from djust.decorators import event_handler
from djust.live_view import LiveView

_PKG_DIR = pathlib.Path(__file__).resolve().parents[1]
_WEBSOCKET = _PKG_DIR / "websocket.py"
_RUNTIME = _PKG_DIR / "runtime.py"

# The dispatch_mount tests stub _instantiate_view, so the view path only needs
# to pass the shape + allowlist gate (is_view_path_allowed runs BEFORE
# instantiation, runtime.py:347). The view CLASSES live in this test module;
# allowing the ``djust.`` prefix admits a real dotted path under it.
_DENIED_VIEW_PATH = "djust.tests.test_transport_behavioral_parity._ObjectDeniedView"
_ALLOWED_VIEW_PATH = "djust.tests.test_transport_behavioral_parity._ObjectAllowedView"


# --------------------------------------------------------------------------- #
# Shared mock transport (mirrors test_runtime.MockTransport + next_client_version).
# --------------------------------------------------------------------------- #
class MockTransport:
    """Records outbound frames; ``next_client_version`` is identity (SSE-shape)."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None
        self.version_calls: List = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return None

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        self.version_calls.append((html, rust_version))
        return rust_version

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        """No-op event context (#1899): the SSE-shape mock has no consumer lock to
        borrow — the runtime wraps the event handler+render in
        ``transport.event_context``, so the mock must provide one."""
        yield


# --------------------------------------------------------------------------- #
# AST helper — extract the ordered list of ``self._flush_*`` calls inside the
# ``_flush_all_pending`` method body of a given module.
# --------------------------------------------------------------------------- #
def _flush_calls_in_all_pending(path: pathlib.Path) -> List[str]:
    """Return the ordered ``_flush_*`` method names called inside the
    ``_flush_all_pending`` method of *path* (the single source of truth for that
    transport's turn-end drain). Catches both ``await self._flush_x()`` and the
    bare ``self._flush_x()`` shapes."""
    tree = ast.parse(path.read_text())
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "_flush_all_pending"
        ):
            target = node
            break
    assert target is not None, f"_flush_all_pending not found in {path.name}"

    # Walk the body STATEMENTS in source order (ast.walk is breadth-first and
    # would reorder the calls, defeating the order-parity assertion). Each drain
    # is one statement: ``self._flush_x()`` or ``await self._flush_x()``.
    calls: List[str] = []
    for stmt in target.body:
        expr = stmt.value if isinstance(stmt, ast.Expr) else None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await):
            expr = stmt.value.value
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            func = expr.func
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "self"
                and func.attr.startswith("_flush_")
            ):
                calls.append(func.attr)
    return calls


# --------------------------------------------------------------------------- #
# Net 1 — flush-queue-count parity (#1885 / #1646 / #1125).
# --------------------------------------------------------------------------- #
class TestFlushQueueParity:
    def test_runtime_and_ws_flush_all_pending_drain_the_same_queues(self):
        """The runtime ``_flush_all_pending`` drains the SAME ordered queue set as
        WS ``_flush_all_pending``.

        Gate-off (#1468): drop any ``_flush_*`` line from the runtime's
        ``_flush_all_pending`` (e.g. delete ``await self._flush_flash()``) and this
        FAILS — the two ordered lists diverge. Recorded gate-off in the PR body:
        removing the 5 Iter-0 lines reproduces the pre-fix 3-of-8 state and the
        sets differ by exactly {flash, page_metadata, pending_layout,
        accessibility, i18n}.
        """
        ws_calls = _flush_calls_in_all_pending(_WEBSOCKET)
        rt_calls = _flush_calls_in_all_pending(_RUNTIME)

        # WS is the canonical source: 8 queues in a fixed order.
        expected = [
            "_flush_push_events",
            "_flush_flash",
            "_flush_page_metadata",
            "_flush_pending_layout",
            "_flush_deferred",
            "_flush_navigation",
            "_flush_accessibility",
            "_flush_i18n",
        ]
        assert ws_calls == expected, (
            "WS _flush_all_pending drifted from the canonical 8-queue order this "
            f"net pins. Got {ws_calls!r}. If WS changed deliberately, update the "
            "expected order here AND the runtime to match."
        )
        assert rt_calls == ws_calls, (
            "ViewRuntime._flush_all_pending drains a DIFFERENT queue set/order than "
            f"WS (#1646 parallel-path drift). runtime={rt_calls!r} ws={ws_calls!r}. "
            f"Missing from runtime: {set(ws_calls) - set(rt_calls)}; "
            f"extra on runtime: {set(rt_calls) - set(ws_calls)}."
        )

    def test_runtime_defines_all_eight_flush_methods(self):
        """Each of the 8 queue-flush methods the parity order names actually exists
        on ViewRuntime (so the order pin can't pass against phantom methods)."""
        from djust.runtime import ViewRuntime

        for name in (
            "_flush_push_events",
            "_flush_flash",
            "_flush_page_metadata",
            "_flush_pending_layout",
            "_flush_deferred",
            "_flush_navigation",
            "_flush_accessibility",
            "_flush_i18n",
            "_flush_all_pending",
        ):
            assert hasattr(ViewRuntime, name), f"ViewRuntime missing {name}"


# --------------------------------------------------------------------------- #
# Behavioral views for the runtime-driven nets.
# --------------------------------------------------------------------------- #
class _RuntimeRenderMixin:
    """Common rust/render short-circuits so the REAL dispatch_mount /
    dispatch_url_change run without needing a compiled Rust view + template."""

    def _initialize_temporary_assigns(self):
        pass

    def _initialize_rust_view(self, request):
        pass

    def _sync_state_to_rust(self):
        pass

    def render_with_diff(self):
        return ("<div>SECRET</div>", None, 1)

    def _strip_comments_and_whitespace(self, html):
        return html

    def _extract_liveview_content(self, html):
        return html


class _ObjectDeniedView(_RuntimeRenderMixin, LiveView):
    """Opts into the object-permission lifecycle and DENIES access."""

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False

    def mount(self, request, **kwargs):
        self.mounted = True

    def handle_params(self, params, uri):
        self.handle_params_ran = True


class _ObjectAllowedView(_RuntimeRenderMixin, LiveView):
    """Opts in and ALLOWS access — the positive half (non-vacuous deny test)."""

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return True

    def mount(self, request, **kwargs):
        self.mounted = True

    def handle_params(self, params, uri):
        self.handle_params_ran = True


def _runtime_with_view(view):
    """Build a ViewRuntime driving the REAL dispatch_mount against *view*.

    Short-circuits only view-loading + the pre-mount auth seam (the two seams
    the existing test_runtime.py mounts stub); the object-permission check, mount,
    handle_params, and render all run for real."""
    from djust.runtime import ViewRuntime

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime._instantiate_view = MagicMock(return_value=view)
    runtime._check_auth = AsyncMock(return_value=None)
    runtime._resolve_url_kwargs = MagicMock(return_value={})
    return runtime, transport


# --------------------------------------------------------------------------- #
# Net 2 — object-permission parity on dispatch_mount (#1885, gate-off #1468).
# --------------------------------------------------------------------------- #
class TestDispatchMountObjectPermission:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_denied_object_is_not_rendered_or_sent(self):
        """A view whose has_object_permission returns False is DENIED by the REAL
        dispatch_mount: no mount frame, no rendered HTML to the client, a
        permission_denied error frame, and view_instance cleared.

        Gate-off (#1468): remove the ``enforce_object_permission`` call from
        ``runtime.py:dispatch_mount`` and this FAILS — the denied view mounts,
        renders its SECRET html, and a mount frame carrying it reaches the client.
        Empirically verified pre-fix via the repro probe (BUG_A_PRESENT=True).
        """
        view = _ObjectDeniedView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {"type": "mount", "view": _DENIED_VIEW_PATH, "params": {}, "url": "/items/42/"}
        )

        # No mount frame, and no frame leaking the denied object's HTML.
        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert not mount_frames, "denied object produced a mount frame — IDOR (#10-#12)"
        leaked = [f for f in transport.sent if "SECRET" in str(f.get("html", ""))]
        assert not leaked, "denied object's rendered HTML reached the client"

        # The denial envelope is emitted and the view is torn down.
        assert transport.errors, "no permission_denied error frame emitted on denial"
        assert transport.errors[0].get("code") == "permission_denied"
        assert runtime.view_instance is None, "view_instance not cleared after denial"

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_allowed_object_mounts_and_renders(self):
        """Positive half: an allowed object mounts + renders (so the deny test
        can't pass vacuously by failing every mount)."""
        view = _ObjectAllowedView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": _ALLOWED_VIEW_PATH,
                "params": {},
                "url": "/items/42/",
            }
        )

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert mount_frames, "allowed object failed to mount — object-perm over-denied"
        assert runtime.view_instance is view
        assert getattr(view, "handle_params_ran", False), "handle_params skipped on allow"

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_object_perm_runs_after_mount_before_handle_params(self):
        """The object check runs AFTER mount() (so get_object can read URL-derived
        attrs) and BEFORE handle_params + render (so a denied object never renders)
        — the WS placement (websocket.py:2554-2573)."""
        view = _ObjectDeniedView()
        runtime, _ = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {"type": "mount", "view": _DENIED_VIEW_PATH, "params": {}, "url": "/x/"}
        )

        assert getattr(view, "mounted", False), "mount() must run before the object check"
        assert not getattr(view, "handle_params_ran", False), (
            "handle_params ran on a denied object — the object check must short-"
            "circuit BEFORE handle_params"
        )


# --------------------------------------------------------------------------- #
# Net 3 — wire-version parity on dispatch_url_change (#1858 pin).
# --------------------------------------------------------------------------- #
class _UrlChangeView(_RuntimeRenderMixin, LiveView):
    def mount(self, request, **kwargs):
        pass

    def handle_params(self, params, uri):
        self.last_uri = uri


class TestWireVersionParity:
    @pytest.mark.asyncio
    async def test_url_change_stamps_version_via_transport(self):
        """dispatch_url_change stamps the wire version through
        ``transport.next_client_version`` (the consumer-owned counter on WS; the
        Rust version unchanged on SSE) — pinned post-#1858.

        Gate-off (#1468): send ``version`` straight from ``render_with_diff`` in
        ``_dispatch_url_change_inner`` instead of ``transport.next_client_version``
        and ``transport.version_calls`` stays empty → this FAILS.
        """
        from djust.runtime import ViewRuntime

        view = _UrlChangeView()
        transport = MockTransport()
        runtime = ViewRuntime(transport)
        runtime.view_instance = view

        await runtime.dispatch_url_change({"type": "url_change", "params": {}, "uri": "/p/"})

        assert transport.version_calls, (
            "dispatch_url_change did not stamp the wire version through "
            "transport.next_client_version (#1858 wire-version parity regression)"
        )
        # The frame's version is the one the transport returned.
        frames = [f for f in transport.sent if f.get("type") in ("patch", "html_update")]
        assert frames, "no patch/html_update frame from url_change"
        assert "version" in frames[0]


# --------------------------------------------------------------------------- #
# Net 4 — enumerate the KNOWN WS-only mount/event behaviors (ADR-022).
#
# These behaviors live ONLY on the WS handler path today (handle_mount /
# handle_event / receive in websocket.py) and are NOT yet on the runtime —
# Iters 1-3 of ADR-022 will migrate the runtime onto real transports and grow
# the transport-agnostic hooks for these. The pin makes the WS↔runtime delta
# VISIBLE: if a future iteration MOVES one of these onto the runtime, the
# corresponding count drops and this test trips — forcing a deliberate update
# (and a note in the convergence ADR) rather than a silent drift.
# --------------------------------------------------------------------------- #

# (marker substring → minimum occurrences expected in websocket.py today). Each
# is a WS-only mount/event mechanism the runtime does not implement (ADR-022
# "~16 WS-only behaviors": binary upload, presence, cursor, time-travel,
# sticky-child preservation, signed-snapshot restore, actor channel-layer,
# mount_batch, channels groups, ticks).
_WS_ONLY_MARKERS = {
    "handle_mount_batch": 1,  # mount_batch multiplexer (no runtime equivalent)
    "_send_sticky_update": 1,  # sticky-child preservation across live_redirect
    "_send_child_update": 1,  # sticky-child VDOM patches
    # ``group_add`` / ``channel_layer`` / ``tick_interval`` MOVED to ViewRuntime in
    # ADR-022 Iter 3 Phase 3.3b (#1919, THE MOUNT FLIP): the WS ``on_view_mounted``
    # transport hook now performs the WS post-mount channel-layer wiring (view /
    # presence / db_notify ``group_add``) + the periodic ``tick_interval`` task
    # start (Finding B residual), writing onto the consumer during the runtime
    # mount. They textually appear in runtime.py now (inside the WS transport hook),
    # so they are no longer WS-ONLY *symbols* and are removed from this
    # enumeration (mirrors the Phase-3.1 ``state_snapshot_signed`` move). The WS
    # behavior is pinned by test_ws_mount_flip_parity_1911.py
    # (TestGroupAddReachability + TestTickAtMount, real-WebsocketCommunicator).
    # --- Mount-spine WS-only behaviors (ADR-022 Iter 3 Phase 3.0, #1911) ---
    # Each is a genuinely-WS-only mount behavior that the runtime's dispatch_mount
    # does NOT implement (and must NOT — they are HOOKS for Phases 3.1-3.3b, not
    # Phase-3.0 grows). If a future iteration MOVES one onto ViewRuntime, the
    # ``marker not in rt_src`` assertion trips → update this pin deliberately AND
    # note the move in ADR-022. This keeps the mount-side WS↔runtime delta VISIBLE
    # as the mount convergence proceeds.
    # ``create_session_actor``, ``_find_sticky_slot_ids``, and ``register_view``
    # gained DORMANT ``WSConsumerTransport`` mount-hook impls in ADR-022 Iter 3
    # Phase 3.2 (#1915): ``dispatch_actor_mount`` (Finding D),
    # ``on_mount_render_ready`` (Finding B sticky preservation), and
    # ``on_view_instantiated`` (Finding B back-refs) now reference these symbols in
    # runtime.py. The hooks are DORMANT — ``dispatch_mount`` does NOT call them yet
    # (3.3a wires them) and the WS bespoke ``handle_mount`` still does the work
    # inline (untouched until 3.3b) — so these are no longer WS-ONLY symbols and are
    # removed from this enumeration (mirrors the Phase-3.1 ``state_snapshot_signed``
    # move). The DORMANT contract + the WS-impl behavior are pinned by
    # test_transport_mount_hooks_1915.py (dispatch_mount-doesn't-call pins +
    # handle_mount-still-inline pins + MockTransport / real-WebsocketCommunicator
    # hook tests). The remaining WS-only mount mechanisms stay pinned below.
    #
    # ``state_snapshot_signed`` MOVED to ViewRuntime.dispatch_mount in ADR-022
    # Iter 3 Phase 3.1 (#1913): the signed session-snapshot EMIT + restore are now
    # transport-agnostic (LIVE for the SSE mount path, gated on
    # ``enable_state_snapshot``). It remains on the WS path too (handle_mount,
    # untouched until 3.3b), so it is no longer a WS-ONLY behavior — removed from
    # this enumeration. The signed-snapshot HMAC caps are pinned by
    # test_runtime_mount_state_restore_1913.py (runtime) +
    # test_state_snapshot_signing.py (WS).
    # NOTE: ``tick_interval`` was removed from this enumeration in #1919 (it MOVED
    # into the runtime's WS ``on_view_mounted`` hook — see the block above).
    # ``_run_tick`` was never a marker here (it already appears in runtime.py
    # docstrings that reference the WS-only tick loop the event render-lock
    # serializes against).
}


class TestWsOnlyBehaviorEnumeration:
    def test_ws_only_behaviors_still_ws_only(self):
        """Each enumerated WS-only mount/event behavior is still present on the WS
        path and NOT (yet) on the runtime. If a convergence iteration moves one
        onto ViewRuntime, update this pin deliberately (and note it in ADR-022).

        This is a VISIBILITY pin, not a security gate — it tracks the WS↔runtime
        delta as Iters 1-3 proceed so 'moved to runtime' is never silent.
        """
        ws_src = _WEBSOCKET.read_text()
        rt_src = _RUNTIME.read_text()

        for marker, minimum in _WS_ONLY_MARKERS.items():
            ws_count = ws_src.count(marker)
            assert ws_count >= minimum, (
                f"WS-only behavior marker {marker!r} dropped from websocket.py "
                f"(found {ws_count}, expected >= {minimum}). If it MOVED to the "
                f"runtime as part of an ADR-022 convergence iteration, update "
                f"_WS_ONLY_MARKERS deliberately."
            )
            assert marker not in rt_src, (
                f"WS-only behavior {marker!r} now appears in runtime.py — a "
                f"convergence iteration moved it onto ViewRuntime. This is expected "
                f"during Iters 1-3, but update _WS_ONLY_MARKERS (and note the move "
                f"in ADR-022) so the WS↔runtime delta stays tracked."
            )

    def test_runtime_has_no_mount_batch(self):
        """ViewRuntime intentionally has no mount_batch multiplexer (ADR-022: the
        mount-batch + transport-terminating side effects are WS-only, #291). Pin it
        so a future addition is a deliberate, reviewed change."""
        from djust.runtime import ViewRuntime

        assert not hasattr(ViewRuntime, "dispatch_mount_batch")
        assert not hasattr(ViewRuntime, "handle_mount_batch")


# --------------------------------------------------------------------------- #
# Net 5 — runtime EVENT-spine parity grows (ADR-022 Iter 2 Phase 2.0, #1889).
#
# Phase 2.0 grows ``ViewRuntime._dispatch_event_inner`` / ``_render_and_send``
# (the minimal SSE event spine) toward the WS ``_handle_event_inner`` by adding
# the transport-agnostic shared behaviors the runtime lacked:
#
#   1. ``ref`` echo on BOTH the noop and the update frame (#560).
#   2. ``source="event"`` + ``event_name`` on the noop frame (#560 sequencing).
#   3. ``_force_full_html`` honored on the event render path (discard patches →
#      full html_update; flag consumed). WS source: websocket.py:4039-4040.
#   4. ``_notify_waiters`` (ADR-002) called after the handler. WS: websocket.py:3608.
#   5. #700 identity push-only auto-skip (id()-identity variant beyond the
#      assigns-snapshot skip). WS source: websocket.py:3867-3891.
#
# This file's Net 4 enumerates WS-only behaviors NOT on the runtime; this Net 5
# pins the behaviors that Phase 2.0 MOVED ONTO the runtime — so a future drop
# of one of them re-forks RED (mechanical drift detection, #1646 / #1125 / #1859
# "a pin must be load-bearing"). Each behavioral pin is paired with the gate-off
# witness (#1468) documented in its docstring.
# --------------------------------------------------------------------------- #
class _EventSpineMixin(_RuntimeRenderMixin):
    """Render short-circuit + a couple of handlers for the event-spine pins.

    ``render_with_diff`` returns ``(html, None, version)`` (no patches) so the
    event render lands in the no-diff ``html_update`` branch — the branch every
    transport reaches. The handlers below exercise the changed/unchanged/push-
    only/force-html arms of the event spine.
    """

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}


def _event_runtime_with_view(view):
    """Build a ViewRuntime with *view* already mounted (event path entry)."""
    from djust.runtime import ViewRuntime

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, transport


class _BumpView(_EventSpineMixin, LiveView):
    """A handler that changes public state (forces a render)."""

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


class _NoChangeView(_EventSpineMixin, LiveView):
    """A handler that does NOT change state (auto-skip → noop)."""

    @event_handler()
    def touch_nothing(self, **kwargs):
        pass


class _PushOnlyView(_EventSpineMixin, LiveView):
    """A handler that only pushes an event — #700 identity push-only skip → noop."""

    @event_handler()
    def ping(self, **kwargs):
        self.push_event("pong", {"x": 1})


class _ForceHtmlView(_EventSpineMixin, LiveView):
    """Sets ``_force_full_html`` in ``mount`` (so it is already in ``__dict__``
    pre-handler — its identity/value is stable across the snapshot, NOT a
    detected 'change') and the handler makes NO public-state change. Without the
    ``not force_html`` guard on the auto-skip, this view would auto-skip to a
    noop; WITH the guard it must render a full html_update. This makes the
    guard's gate-off witness sharp (non-tautological, #1468): if the handler
    instead SET ``_force_full_html`` fresh, that assignment would itself trip the
    assigns snapshot and the auto-skip would never fire regardless of the guard.
    """

    def mount(self, request, **kwargs):
        self.count = 0
        self._force_full_html = True  # pre-existing flag, stable across the turn

    @event_handler()
    def force(self, **kwargs):
        # No state change — only force_html (set at mount) defeats the auto-skip.
        pass


class TestEventSpineRefEcho:
    """Grow #1 + #2: ``ref`` on noop + update; ``source``/``event_name`` on noop."""

    @pytest.mark.asyncio
    async def test_update_frame_echoes_ref_source_event_name(self):
        """A state-changing event echoes ``ref`` + ``source="event"`` +
        ``event_name`` on the update frame (#560).

        Gate-off (#1468): delete ``if event_ref is not None: msg["ref"] =
        event_ref`` from the no-diff html_update branch in ``_render_and_send``
        → ``ref`` drops from the frame → this FAILS.
        """
        runtime, transport = _event_runtime_with_view(_BumpView())
        runtime.view_instance.count = 0

        await runtime.dispatch_event({"type": "event", "event": "bump", "params": {}, "ref": 42})

        frames = [f for f in transport.sent if f.get("type") in ("patch", "html_update")]
        assert frames, f"bump must emit an update frame, got {transport.sent!r}"
        f = frames[0]
        assert f.get("ref") == 42, f"update frame must echo the event ref (#560); got {f!r}"
        assert f.get("source") == "event", f"update frame must carry source=event; got {f!r}"
        assert f.get("event_name") == "bump", f"update frame must carry event_name; got {f!r}"

    @pytest.mark.asyncio
    async def test_noop_frame_echoes_ref_source_event_name(self):
        """A no-change event echoes ``ref`` + ``source="event"`` + ``event_name``
        on the noop frame (#560 sequencing).

        Gate-off (#1468): delete ``if event_ref is not None: noop_msg["ref"] =
        event_ref`` (or the ``source``/``event_name`` keys) from the noop branch
        in ``_dispatch_event_inner`` → this FAILS.
        """
        runtime, transport = _event_runtime_with_view(_NoChangeView())
        runtime.view_instance.count = 0

        await runtime.dispatch_event(
            {"type": "event", "event": "touch_nothing", "params": {}, "ref": 9}
        )

        noops = [f for f in transport.sent if f.get("type") == "noop"]
        assert noops, f"no-change event must emit a noop frame, got {transport.sent!r}"
        n = noops[0]
        assert n.get("ref") == 9, f"noop must echo the event ref (#560); got {n!r}"
        assert n.get("source") == "event", f"noop must carry source=event; got {n!r}"
        assert n.get("event_name") == "touch_nothing", f"noop must carry event_name; got {n!r}"

    @pytest.mark.asyncio
    async def test_missing_ref_is_omitted_not_null(self):
        """When the client sends no ``ref``, neither the noop nor the update frame
        carries a ``ref`` key (matches WS, which only sets it when present)."""
        runtime, transport = _event_runtime_with_view(_NoChangeView())
        runtime.view_instance.count = 0

        await runtime.dispatch_event({"type": "event", "event": "touch_nothing", "params": {}})

        noops = [f for f in transport.sent if f.get("type") == "noop"]
        assert noops, transport.sent
        assert "ref" not in noops[0], f"absent ref must be omitted, not null; got {noops[0]!r}"

    @pytest.mark.asyncio
    async def test_non_numeric_ref_is_coerced_to_none(self):
        """A non-numeric ``ref`` is dropped (type-confusion guard, matches WS
        ``int(raw_ref) if isinstance(raw_ref, (int, float))`` at
        websocket.py:3120)."""
        runtime, transport = _event_runtime_with_view(_NoChangeView())
        runtime.view_instance.count = 0

        await runtime.dispatch_event(
            {"type": "event", "event": "touch_nothing", "params": {}, "ref": "evil"}
        )
        noops = [f for f in transport.sent if f.get("type") == "noop"]
        assert noops, transport.sent
        assert "ref" not in noops[0], "non-numeric ref must be dropped, not echoed"


class TestEventSpineForceFullHtml:
    """Grow #3: ``_force_full_html`` honored on the event render path."""

    @pytest.mark.asyncio
    async def test_force_full_html_defeats_autoskip_and_sends_html_update(self):
        """A handler that sets ``_force_full_html`` (without otherwise changing
        public state) must NOT auto-skip — it sends a full ``html_update`` — and
        the flag is consumed.

        Gate-off (#1468): remove the ``not force_html`` term from the auto-skip
        guard in ``_dispatch_event_inner`` (``if not skip_render and not
        force_html:`` → ``if not skip_render:``) → the no-change handler
        auto-skips to a noop → this FAILS (no html_update frame). VERIFIED RED.
        Non-tautological because ``_force_full_html`` is pre-set (at mount) and
        the handler makes no change, so the only thing keeping the auto-skip from
        firing is the guard itself.
        """
        view = _ForceHtmlView()
        view.count = 0
        # Pre-existing flag (as _ForceHtmlView.mount sets it) — stable identity,
        # not a detected change.
        view._force_full_html = True
        runtime, transport = _event_runtime_with_view(view)

        await runtime.dispatch_event({"type": "event", "event": "force", "params": {}, "ref": 5})

        noops = [f for f in transport.sent if f.get("type") == "noop"]
        assert not noops, (
            f"force_full_html must defeat the auto-skip (no noop); got {transport.sent!r}"
        )
        updates = [f for f in transport.sent if f.get("type") == "html_update"]
        assert updates, f"force_full_html must emit a full html_update; got {transport.sent!r}"
        # Flag consumed so it forces exactly one render.
        assert getattr(view, "_force_full_html", False) is False, (
            "_force_full_html must be reset after the forced render"
        )

    @pytest.mark.asyncio
    async def test_force_full_html_discards_patches(self):
        """When patches ARE available but ``_force_full_html`` is set, the runtime
        discards the patches and sends html_update (WS websocket.py:4039-4040).

        Gate-off (#1468): remove the ``if force_html: patches = None`` line from
        ``_render_and_send`` → a patch frame is emitted instead → this FAILS.
        """
        from djust.runtime import ViewRuntime

        class _PatchForceView(_EventSpineMixin, LiveView):
            def render_with_diff(self):
                # A real patch list is available this render.
                return ("<div>x</div>", [{"op": "noop"}], 1)

            @event_handler()
            def force(self, **kwargs):
                self.count += 1  # also change state so the assigns-skip doesn't fire
                self._force_full_html = True

        view = _PatchForceView()
        view.count = 0
        transport = MockTransport()
        runtime = ViewRuntime(transport)
        runtime.view_instance = view

        await runtime.dispatch_event({"type": "event", "event": "force", "params": {}})

        patches = [f for f in transport.sent if f.get("type") == "patch"]
        updates = [f for f in transport.sent if f.get("type") == "html_update"]
        assert not patches, f"force_full_html must discard patches; got {transport.sent!r}"
        assert updates, f"force_full_html must send html_update; got {transport.sent!r}"


class TestEventSpineNotifyWaiters:
    """Grow #4: ``_notify_waiters`` (ADR-002) called after the handler."""

    @pytest.mark.asyncio
    async def test_notify_waiters_called_after_handler(self):
        """The runtime calls ``view._notify_waiters(event_name, params)`` after the
        handler runs (ADR-002 Phase 1b, transport-agnostic; WS websocket.py:3608).

        Gate-off (#1468): delete the ``_notify_waiters`` call from
        ``_dispatch_event_inner`` → ``calls`` stays empty → this FAILS.
        """
        from unittest.mock import MagicMock

        view = _BumpView()
        view.count = 0
        spy = MagicMock()
        view._notify_waiters = spy  # instance override; runtime hasattr-guards it
        runtime, _ = _event_runtime_with_view(view)

        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"a": 1}, "ref": 1}
        )

        assert spy.called, "_notify_waiters was not called by the runtime event path"
        called_event, called_kwargs = spy.call_args[0]
        assert called_event == "bump"
        assert called_kwargs == {"a": 1}, (
            f"waiter kwargs must be the coerced params; got {called_kwargs!r}"
        )

    @pytest.mark.asyncio
    async def test_real_waiter_resolves_on_event(self):
        """End-to-end through the REAL waiter registry: a ``wait_for_event``
        future resolves when the matching event is dispatched through the
        runtime (proving the spy test isn't testing a phantom API)."""
        import asyncio

        view = _BumpView()
        view.count = 0
        runtime, _ = _event_runtime_with_view(view)

        waiter_future = asyncio.ensure_future(view.wait_for_event("bump", timeout=2))
        await asyncio.sleep(0)  # let the waiter register

        await runtime.dispatch_event({"type": "event", "event": "bump", "params": {"v": 7}})

        result = await waiter_future
        assert result == {"v": 7}, f"waiter should resolve with the event kwargs; got {result!r}"


class TestEventSpineIdentityPushSkip:
    """Grow #5: #700 identity push-only auto-skip → noop, not a render."""

    @pytest.mark.asyncio
    async def test_push_only_handler_auto_skips_to_noop(self):
        """A handler that only calls ``push_event`` (no real state change) emits a
        noop, not an html_update — even though the push-event side-effect drained
        a push frame (#700, identity variant).

        Gate-off (#1468): delete the ``#700`` identity-skip block from
        ``_dispatch_event_inner`` → the assigns-snapshot may still detect 'no
        change' for this simple view, so to make the gate-off witness sharp, the
        sibling ``test_push_only_skip_is_identity_not_assigns`` uses a view whose
        assigns-snapshot would FALSE-POSITIVE a change.
        """
        import asyncio

        view = _PushOnlyView()
        view.count = 0
        runtime, transport = _event_runtime_with_view(view)

        await runtime.dispatch_event({"type": "event", "event": "ping", "params": {}, "ref": 3})
        # ``_flush_push_events`` schedules the send fire-and-forget
        # (asyncio.ensure_future); yield once so the scheduled push frame lands.
        await asyncio.sleep(0)

        updates = [f for f in transport.sent if f.get("type") in ("patch", "html_update")]
        noops = [f for f in transport.sent if f.get("type") == "noop"]
        assert not updates, f"push-only handler must NOT render; got {transport.sent!r}"
        assert noops, f"push-only handler must emit a noop; got {transport.sent!r}"
        # The push frame was still emitted (push_events flushed on the noop path).
        pushes = [f for f in transport.sent if f.get("type") == "push_event"]
        assert pushes, "the push_event side-effect must still reach the client on the noop path"

    @pytest.mark.asyncio
    async def test_push_only_skip_is_identity_not_assigns(self):
        """The #700 skip is the IDENTITY variant — load-bearing exactly when the
        ASSIGNS snapshot reports a change but the IDENTITY snapshot does not.

        ``_snapshot_assigns`` includes a CONTENT fingerprint for list/dict attrs,
        so an in-place mutation of a dict nested inside a list (same list object
        id, same dict object id, different value) makes the assigns snapshot
        differ — while the identity snapshot ``{attr: id(value)}`` stays
        unchanged (no attr was reassigned). The #700 block recognises this as a
        no-reassignment turn and, with push events pending, skips the render. The
        assigns-snapshot skip ALONE cannot (it sees the fingerprint change).

        Construction note (faithful to the WS quirk this ports): the assigns-else
        branch sets ``view._changed_keys`` when assigns differ. ``_changed_keys``
        is a framework slot on a normally-constructed view (set before the
        ``_framework_attrs`` snapshot via the lifecycle), so it is excluded from
        BOTH identity snapshots and does not pollute the comparison. The
        ``_event_runtime_with_view`` helper builds the view WITHOUT running the
        full mount lifecycle, so we add ``_changed_keys`` to ``_framework_attrs``
        here to reproduce a mounted view's framework-attr set — otherwise the
        else-branch's fresh ``_changed_keys`` object would itself differ the
        identity snapshot (a test-harness artifact, not the production shape).

        Gate-off (#1468): delete the #700 identity-skip block from
        ``_dispatch_event_inner`` → the assigns snapshot's fingerprint change is
        treated as a real change → this view RENDERS instead of noop → FAILS.
        VERIFIED RED.
        """

        class _InPlacePushView(_EventSpineMixin, LiveView):
            def mount(self, request, **kwargs):
                self.count = 0
                self.rows = [{"v": 1}]  # list-of-dict: fingerprinted by assigns

            @event_handler()
            def ping(self, **kwargs):
                # In-place mutate the nested dict value: changes the assigns
                # fingerprint (different content) but NOT the identity of `rows`
                # (same list object) — the #700 false-positive the identity skip
                # neutralizes. Plus a push so push events are pending.
                self.rows[0]["v"] += 1
                self.push_event("pong", {})

        view = _InPlacePushView()
        view.count = 0
        view.rows = [{"v": 1}]
        # Reproduce a mounted view's framework-attr set (see docstring): the
        # assigns-else writes _changed_keys; on a live mounted view that key is a
        # framework slot and is excluded from the identity snapshot.
        view._framework_attrs = view._framework_attrs | {"_changed_keys"}
        runtime, transport = _event_runtime_with_view(view)

        await runtime.dispatch_event({"type": "event", "event": "ping", "params": {}})

        updates = [f for f in transport.sent if f.get("type") in ("patch", "html_update")]
        noops = [f for f in transport.sent if f.get("type") == "noop"]
        assert not updates, (
            "the #700 identity skip must skip a push-only handler even when the "
            f"assigns fingerprint reports an in-place change; got {transport.sent!r}"
        )
        assert noops, f"expected a noop, got {transport.sent!r}"


# --------------------------------------------------------------------------- #
# Net 6 — EVENT-spine behavior enumeration (source pin, #1646 / #1125 / #1859).
#
# A mechanical source-level enumeration of the 5 Phase-2.0 grows inside the
# runtime event spine. Each entry is a substring that MUST appear in the
# relevant ``runtime.py`` method body. A future refactor that drops one of the
# grows re-forks this RED (the pin is load-bearing: it asserts on the actual
# production method source, not a phantom). Mirrors the AST/source style of
# Net 1 (flush-queue enumeration).
# --------------------------------------------------------------------------- #
import inspect  # noqa: E402


class TestEventSpineEnumeration:
    def test_dispatch_event_inner_has_all_five_grows(self):
        """Enumerate the 5 Phase-2.0 grows present in the event spine. A dropped
        grow trips this pin.

        (#1899, ADR-022 Phase 2.3a: the spine body was extracted from
        ``_dispatch_event_inner`` into ``_dispatch_event_render`` so the
        handler+render runs inside ``transport.event_context``; the 5 grows moved
        with the body, so this pin reads ``_dispatch_event_render``.)"""
        from djust.runtime import ViewRuntime

        src = inspect.getsource(ViewRuntime._dispatch_event_render)

        # 1. ref extraction (#560).
        assert 'data.get("ref")' in src, "event ref (#560) extraction missing from spine"
        assert "event_ref" in src, "event_ref not threaded through the spine"
        # 2. source + event_name on the noop frame.
        assert '"source": "event"' in src, "source=event missing from the noop frame"
        assert '"event_name": event_name' in src, "event_name missing from the noop frame"
        # 3. _force_full_html honored on the skip decision.
        assert "_force_full_html" in src, "_force_full_html guard missing from the spine"
        assert "force_html" in src, "force_html not threaded to the render call"
        # 4. _notify_waiters (ADR-002).
        assert "_notify_waiters" in src, "_notify_waiters (ADR-002) missing from the spine"
        # 5. #700 identity push-only skip.
        assert "_pending_push_events" in src, "#700 identity push-only skip missing from the spine"
        assert "pre_identity" in src and "post_identity" in src, (
            "#700 identity comparison (pre_identity/post_identity) missing from the spine"
        )

    def test_render_and_send_honors_force_html_and_ref(self):
        """``_render_and_send`` discards patches under force_html and echoes the
        event ref on every frame."""
        from djust.runtime import ViewRuntime

        src = inspect.getsource(ViewRuntime._render_and_send)
        assert "if force_html:" in src, "force_html patch-discard missing from _render_and_send"
        assert "patches = None" in src, "force_html must discard patches"
        # ref echoed on all three frame branches (patch / compression / no-diff).
        assert src.count('msg["ref"] = event_ref') == 3, (
            "the event ref must be echoed on all 3 update-frame branches "
            "(patch / compression-fallback / no-diff html_update)"
        )
        # source=event on all three update branches.
        assert src.count('"source": "event"') == 3, (
            "source=event must be stamped on all 3 update-frame branches"
        )


# --------------------------------------------------------------------------- #
# Net 7 — MOUNT-spine parity grows (ADR-022 Iter 3 Phase 3.0, #1911).
#
# Phase 3.0 GROWS ``ViewRuntime.dispatch_mount`` toward the WS ``handle_mount``
# by adding the transport-agnostic shared MOUNT behaviors the runtime lacked:
#
#   1. ``_djust_mount_request`` / ``_djust_mount_kwargs`` stash (#1895) — the
#      runtime's OWN per-event session-save fallback reads it.
#   2. ``_snapshot_user_private_attrs`` + ``_capture_dirty_baseline`` post-mount.
#   3. ``has_prerendered`` → ``skip_html_for_resume`` machinery on the frame.
#   4. ``optimistic_rules`` (DEP-002) + ``upload_configs`` on the mount frame.
#   5. Mount-time ``_flush_push_events()`` + ``_dispatch_async_work(None)`` —
#      ONLY those two (NOT ``_flush_all_pending``), pinned in
#      test_handle_mount_drains_queues.py.
#
# Each behavioral pin drives the REAL ``dispatch_mount`` (object-permission
# parity already does this in Net 2) and carries a gate-off witness (#1468).
# These are the regression net the 3.3b mount flip rides.
# --------------------------------------------------------------------------- #


class _MountStashView(_RuntimeRenderMixin, LiveView):
    """Mounts cleanly; used to assert the post-mount stash + baselines."""

    def mount(self, request, **kwargs):
        self.mounted = True


class _MountPushAsyncView(_RuntimeRenderMixin, LiveView):
    """``mount()`` queues a push event AND schedules background work — the two
    queues the mount-time drain must flush (#1283 / #1280)."""

    def mount(self, request, **kwargs):
        self.value = 0
        self.push_event("hello", {"x": 1})  # → _flush_push_events drains it

        def _work():
            self.value = 42

        self.start_async(_work)  # → _dispatch_async_work spawns it

    def get_context_data(self, **kwargs):
        return {"value": self.value}


class TestMountStashAndBaselines:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_mount_stashes_request_and_kwargs(self):
        """#1895: ``dispatch_mount`` stashes the mount request + kwargs on the view
        so the runtime's own per-event session-save fallback
        (``_persist_state_after_event`` runtime.py:2030) can discover the save
        session + ``liveview_{path}`` namespace on the converged event path.

        Gate-off (#1468): delete the ``view_instance._djust_mount_request = request``
        stash from ``dispatch_mount`` → this FAILS (and the per-event save silently
        degrades to the scope session, dropping the mount-request path namespace).
        """
        view = _MountStashView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._MountStashView",
                "params": {"a": 1},
                "url": "/items/7/",
            }
        )

        assert getattr(view, "_djust_mount_request", None) is not None, (
            "dispatch_mount must stash _djust_mount_request (#1895) — the runtime's "
            "per-event session-save fallback reads it (runtime.py:2030/2109)"
        )
        # The stashed request is the one mount ran against (carries the path).
        assert view._djust_mount_request.path == "/items/7/"
        assert isinstance(getattr(view, "_djust_mount_kwargs", None), dict), (
            "dispatch_mount must stash _djust_mount_kwargs (#1895)"
        )

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_mount_captures_private_and_dirty_baselines(self):
        """``dispatch_mount`` calls ``_snapshot_user_private_attrs`` +
        ``_capture_dirty_baseline`` post-mount (WS websocket.py:2598-2603) so
        change-detection sees the correct since-mount delta.

        Gate-off (#1468): delete the two baseline calls from ``dispatch_mount`` →
        the spies below never fire → this FAILS.
        """
        from unittest.mock import MagicMock

        view = _MountStashView()
        snap_spy = MagicMock()
        dirty_spy = MagicMock()
        view._snapshot_user_private_attrs = snap_spy
        view._capture_dirty_baseline = dirty_spy
        runtime, _ = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._MountStashView",
                "params": {},
                "url": "/x/",
            }
        )

        assert snap_spy.called, "_snapshot_user_private_attrs must be called at mount (#1911)"
        assert dirty_spy.called, "_capture_dirty_baseline must be called at mount (#1911)"


class TestMountAsyncAndPushDrain:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_mount_drains_push_events_after_frame(self):
        """A ``push_event`` queued during ``mount()`` is drained AFTER the mount
        frame (#1283) — a ``push_event`` frame reaches the transport.

        Gate-off (#1468): delete the ``self._flush_push_events()`` call at the end
        of ``dispatch_mount`` → no push_event frame is emitted → this FAILS.
        """
        import asyncio

        view = _MountPushAsyncView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._MountPushAsyncView",
                "params": {},
                "url": "/p/",
            }
        )
        # _flush_push_events schedules the send fire-and-forget (ensure_future);
        # yield once so the scheduled push frame lands on the transport.
        await asyncio.sleep(0)

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        push_frames = [f for f in transport.sent if f.get("type") == "push_event"]
        assert mount_frames, "the view must mount"
        assert push_frames, (
            "a push_event queued in mount() must drain after the mount frame "
            "(#1283); got " + repr([f.get("type") for f in transport.sent])
        )
        # Ordering: mount frame BEFORE the push frame (the view must exist first).
        mount_idx = transport.sent.index(mount_frames[0])
        push_idx = transport.sent.index(push_frames[0])
        assert mount_idx < push_idx, "the mount frame must precede the drained push frame"

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_mount_dispatches_async_work(self):
        """``start_async`` scheduled in ``mount()`` is dispatched after the mount
        frame (#1280): the background callback runs and updates state.

        Deflake (#1931, #1830/#1815 family): the dispatched callback runs
        fire-and-forget in its own ``asyncio.ensure_future`` task that ITSELF
        awaits a ``sync_to_async`` thread-pool round-trip before setting
        ``value=42``. The original test bounded-polled a fixed 10 ``sleep(0)``
        yields for that to land — a wall-clock race the saturated ``-n auto``
        loop lost intermittently (the scheduler can fail to run the task within
        the bound). We instead OWN the completion: capture the exact task handle
        the runtime spawns via ``ensure_future`` and ``await`` it, so completion
        is deterministic regardless of scheduler load — no timing bound.

        Gate-off (#1468): delete the ``self._dispatch_async_work(None)`` call at
        the end of ``dispatch_mount`` → ``_async_tasks`` stays queued, no task is
        spawned, nothing to await, ``view.value`` stays 0 → this FAILS.
        """
        import asyncio
        from unittest.mock import patch

        from djust import runtime as runtime_module

        view = _MountPushAsyncView()
        runtime, _ = _runtime_with_view(view)

        # Capture the actual async-work task handle the runtime spawns so we can
        # await it directly — a deterministic completion signal, NOT a bounded
        # poll racing the scheduler. The runtime spawns the background callback
        # via ``asyncio.ensure_future(self._execute_async_task(...))`` inside
        # ``_dispatch_async_work``; we wrap that primitive and keep only the
        # ``_execute_async_task`` task (``_flush_push_events`` also spawns a
        # fire-and-forget send via the same primitive — that one is NOT the
        # async-work task and must be excluded so the gate-off below is sharp).
        async_work_tasks: List[asyncio.Future] = []
        real_ensure_future = asyncio.ensure_future

        def _capturing_ensure_future(coro_or_future, *args, **kwargs):
            fut = real_ensure_future(coro_or_future, *args, **kwargs)
            name = getattr(getattr(coro_or_future, "cr_code", None), "co_name", "")
            if name == "_execute_async_task":
                async_work_tasks.append(fut)
            return fut

        with patch.object(runtime_module.asyncio, "ensure_future", _capturing_ensure_future):
            await runtime.dispatch_mount(
                {
                    "type": "mount",
                    "view": "djust.tests.test_transport_behavioral_parity._MountPushAsyncView",
                    "params": {},
                    "url": "/p/",
                }
            )

        # The dispatch must have spawned the start_async() background task
        # (gate-off #1468: with the ``_dispatch_async_work(None)`` call removed
        # from dispatch_mount, ``_execute_async_task`` is never spawned → empty
        # list → this assertion fails before we even reach the value check).
        assert async_work_tasks, (
            "dispatch_mount must spawn the start_async() callback as an "
            "_execute_async_task via ensure_future (#1280); "
            "_dispatch_async_work(None) was not called"
        )
        # Await the REAL task handle(s) to completion — deterministic, no poll.
        await asyncio.gather(*async_work_tasks)

        assert view.value == 42, (
            "start_async() scheduled in mount() must be dispatched after the mount "
            "frame (#1280) — the background callback must run and set value=42"
        )

    def test_mount_drain_is_exactly_two_queues(self):
        """Source pin (#1646 / #1859 load-bearing): the mount drain at the end of
        ``dispatch_mount`` calls EXACTLY ``_flush_push_events`` +
        ``_dispatch_async_work`` and NOT ``_flush_all_pending``. Mount establishes
        a baseline; it must not run the 8-queue turn-end flush."""
        import inspect as _inspect

        from djust.runtime import ViewRuntime

        src = _inspect.getsource(ViewRuntime.dispatch_mount)
        assert "_flush_push_events()" in src, "mount must drain push events"
        assert "_dispatch_async_work(" in src, "mount must dispatch async work"
        # Assert the CALL form (``_flush_all_pending(``) is absent — a bare
        # substring would false-match the explanatory comment that names the
        # method to explain why it must NOT be called.
        assert "_flush_all_pending(" not in src, (
            "dispatch_mount must NOT call _flush_all_pending — the mount drain is "
            "ONLY the two queues (ADR-022 Iter 3 Phase 3.0)"
        )


class TestMountFrameOptimisticAndUpload:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_optimistic_rules_on_mount_frame(self):
        """DEP-002: a view whose descriptor components declare optimistic rules
        ships them on the mount frame via the runtime
        ``_extract_optimistic_rules``.

        Gate-off (#1468): delete the ``optimistic_rules`` block from
        ``dispatch_mount`` → the key drops from the mount frame → this FAILS.
        """

        class _OptDescriptor:
            class Meta:
                tier = "optimistic"
                event = "toggle"
                optimistic_rule = {"action": "toggle_class", "class": "open"}

        class _OptView(_RuntimeRenderMixin, LiveView):
            _component_descriptors = {"acc": _OptDescriptor()}

            def mount(self, request, **kwargs):
                pass

        view = _OptView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._OptView",
                "params": {},
                "url": "/o/",
            }
        )

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert mount_frames, "the view must mount"
        rules = mount_frames[0].get("optimistic_rules")
        assert rules == {"toggle": {"action": "toggle_class", "class": "open"}}, (
            "the mount frame must carry the optimistic rules from descriptor "
            f"components (DEP-002); got {mount_frames[0]!r}"
        )

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_upload_configs_on_mount_frame(self):
        """A view with an ``_upload_manager`` ships ``upload_configs`` on the mount
        frame.

        Gate-off (#1468): delete the ``upload_configs`` block from
        ``dispatch_mount`` → the key drops → this FAILS.
        """

        class _FakeUploadManager:
            def get_upload_state(self):
                return {"avatar": {"config": {"max_size": 1024, "accept": "image/*"}}}

        class _UploadView(_RuntimeRenderMixin, LiveView):
            def mount(self, request, **kwargs):
                self._upload_manager = _FakeUploadManager()

        view = _UploadView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._UploadView",
                "params": {},
                "url": "/u/",
            }
        )

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert mount_frames, "the view must mount"
        cfgs = mount_frames[0].get("upload_configs")
        assert cfgs == {"avatar": {"max_size": 1024, "accept": "image/*"}}, (
            f"the mount frame must carry upload_configs; got {mount_frames[0]!r}"
        )

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_no_optimistic_or_upload_keys_when_absent(self):
        """GATE-OFF / contrast: a plain view (no descriptors, no upload manager)
        produces a mount frame with NEITHER key — proving the keys above come from
        the view's actual config, not unconditional stamping."""
        view = _MountStashView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._MountStashView",
                "params": {},
                "url": "/x/",
            }
        )

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert mount_frames, "the view must mount"
        assert "optimistic_rules" not in mount_frames[0], (
            "a view with no optimistic descriptors must not carry optimistic_rules"
        )
        assert "upload_configs" not in mount_frames[0], (
            "a view with no upload manager must not carry upload_configs"
        )


class TestMountFrameWireVersion:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_mount_frame_carries_baseline_version(self):
        """The mount frame stamps a ``version`` field establishing the client's VDOM
        baseline.

        Phase-3.0 note (ADR-022 Finding C): the WS mount path uses the
        consumer-owned monotonic ``_next_version()`` and crucially NOT
        ``_next_version_armed`` (mount establishes the baseline; it does NOT arm
        request_html recovery). The runtime mount currently stamps the raw Rust
        ``version`` directly. The distinct ``transport.next_mount_version()`` hook
        (WS no-arm; SSE raw) is a Phase-3.3a wiring task — NOT a Phase-3.0 grow.
        This net pins the CURRENT invariant the flip must preserve: the mount frame
        carries a baseline ``version`` (it does NOT route through the ARMING
        ``transport.next_client_version`` the event path uses).

        Gate-off (#1468): drop the ``version`` key from the mount_msg dict in
        ``dispatch_mount`` → this FAILS.
        """
        view = _MountStashView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": "djust.tests.test_transport_behavioral_parity._MountStashView",
                "params": {},
                "url": "/v/",
            }
        )

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert mount_frames, "the view must mount"
        assert "version" in mount_frames[0], "the mount frame must carry a baseline version"
        # Mount must NOT route through the ARMING wire-version path (next_client_version);
        # arming is for event request_html recovery, not the mount baseline (Finding C).
        assert not transport.version_calls, (
            "mount must NOT call transport.next_client_version (that ARMS recovery) — "
            "the mount baseline is unarmed (#1911 Finding C). The distinct "
            "next_mount_version() hook is a Phase-3.3a wiring task."
        )

    def test_dispatch_mount_does_not_use_arming_wire_version(self):
        """Source pin (Finding C): ``dispatch_mount`` must NOT stamp the mount
        frame's version through the ARMING ``transport.next_client_version``
        (that helper arms request_html recovery, which mount must not do)."""
        import inspect as _inspect

        from djust.runtime import ViewRuntime

        src = _inspect.getsource(ViewRuntime.dispatch_mount)
        assert "next_client_version" not in src, (
            "dispatch_mount must not arm recovery via next_client_version on the "
            "mount baseline (ADR-022 Finding C). A distinct next_mount_version() "
            "hook lands in Phase 3.3a."
        )
