"""
Transport-agnostic runtime for djust LiveView.

This module factors view-lifecycle dispatch (``dispatch_mount``,
``dispatch_event``, ``dispatch_url_change``) out of the WebSocket consumer
and SSE views so both transports share one code path. See
ADR-016 and issue #1237.

Architecture
------------

The runtime owns the mounted ``view_instance`` and orchestrates the call
sequence (auth check -> mount -> handle_params -> render) without knowing
or caring how outbound frames reach the client. All output flows through
``self.transport.send(...)``.

Two transport adapters live alongside the runtime:

- :class:`WSConsumerTransport` wraps a ``LiveViewConsumer`` so the runtime
  can drive WebSocket frames.
- :class:`SSESessionTransport` wraps an ``SSESession`` so the runtime can
  push frames into the SSE event queue.

Both adapters expose the same minimal :class:`Transport` Protocol, which
also matches the interface ``websocket_utils._validate_event_security``
already expects (``send_error``, ``close``, ``_client_ip``).

Phasing
-------

In this PR (v0.9.2-1) only WebSocket's ``handle_url_change`` migrates to
the runtime. ``handle_event``/``handle_mount`` remain on the existing
WS-specific paths until follow-up PRs migrate them. SSE uses the runtime
for all three verbs (mount / event / url_change).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, runtime_checkable

from asgiref.sync import sync_to_async

from .rate_limit import ConnectionRateLimiter
from .security import handle_exception, sanitize_for_log
from .serialization import fast_json_loads
from .validation import validate_handler_params
from .websocket_utils import (
    _call_handler,
    _safe_error,
    _validate_event_security,
    get_handler_coerce_setting,
)

logger = logging.getLogger(__name__)


def _tenant_context(tenant):
    """Bind *tenant* as the current tenant for a runtime dispatch (Finding #6).

    The runtime drives the SSE mount/event/url-change path and the WS
    url-change path. ``TenantMiddleware`` only binds the tenant on the HTTP
    path, so without this the tenant-scoped managers see ``None`` here and
    (fail-closed) return empty querysets. Lazily imports the canonical
    ``tenant_context`` and falls back to a no-op when tenants is unavailable.
    """
    try:
        from .tenants.middleware import tenant_context

        return tenant_context(tenant)
    except Exception:  # noqa: BLE001 — tenants is optional; never break the live path
        from contextlib import nullcontext

        return nullcontext()


# The tenant *bind* step (set_current_tenant) for the mount path is now
# single-sourced in djust.auth.core._bind_current_tenant, invoked by the shared
# run_pre_mount_auth sequence; the runtime no longer carries its own copy. The
# per-event / url-change re-bind still uses the _tenant_context manager above.


# ------------------------------------------------------------------ #
# Transport Protocol + adapters
# ------------------------------------------------------------------ #


@runtime_checkable
class Transport(Protocol):
    """Wire-level transport for a mounted LiveView session.

    Implementations: :class:`WSConsumerTransport`, :class:`SSESessionTransport`.

    The runtime requires only this minimal surface — ``send`` for ordinary
    frames, ``send_error`` for error envelopes, ``close`` for forced
    disconnect, plus ``session_id`` and ``client_ip`` for observability and
    rate-limiting. The ``websocket_utils._validate_event_security`` helper
    already calls ``send_error`` and ``close`` on its first argument; the
    same shape works for both WS and SSE.
    """

    @property
    def session_id(self) -> str:
        """Stable per-session identifier (for observability + rate-limiting)."""

    @property
    def client_ip(self) -> Optional[str]:
        """Resolved client IP, or ``None`` when unavailable."""

    async def send(self, data: Dict[str, Any]) -> None:
        """Send an ordinary outbound frame to the client."""

    async def send_error(self, error: str, **kwargs: Any) -> None:
        """Send an error envelope to the client."""

    async def close(self, code: int = 1000) -> None:
        """Force-disconnect the transport with the given close ``code``."""

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        """Return the wire ``version`` to stamp on a client-CHECKED render frame.

        Client-checked frames (``patch`` / ``html_update``) are validated by the
        client's ``clientVdomVersion === data.version - 1`` rule, so their version
        must come from the SAME monotonic source as ``mount`` / ``event`` for that
        transport — never the raw Rust render counter (#1858, the #1788
        parallel-path twin).

        - WS: returns ``consumer._next_version_armed(html)`` — the per-connection
          counter that ``handle_mount`` / ``handle_event`` use, AND arms
          ``request_html`` recovery to that version (#1788 / #1817).
        - SSE: returns ``rust_version`` unchanged (SSE has no consumer counter;
          its existing behavior is preserved).
        """

    def build_request(self) -> Optional[Any]:
        """Return the transport's real Django request, or ``None`` to synthesize.

        SSE-backed runtimes hold the REAL HTTP request that established the
        stream (with its authenticated ``request.user``, ``request.session``,
        cookies, and path). The pre-mount auth sequence
        (``run_pre_mount_auth`` → ``check_view_auth``) and the post-mount
        object-permission check (``enforce_object_permission``) all read
        ``request.user`` / ``get_object()`` off this request, so the runtime
        MUST mount against it — synthesizing a userless ``RequestFactory``
        request would deny every authenticated SSE view (#1887, ADR-022 Iter 1).

        - SSE: returns the real request captured at stream-GET.
        - WS: returns ``None`` — the runtime synthesizes from ``self.scope``
          (which carries the authenticated ``user`` + ``session``) as before.
        """
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        """Stamp transport-specific identity on the freshly-mounted view.

        Called by ``dispatch_mount`` once ``self.view_instance`` is set, so a
        transport can attach its own back-references the way the legacy bespoke
        mount paths did. SSE stamps ``_sse_session_id`` / ``_sse_session`` (used
        for introspection + limits) and the real query string; WS is a no-op
        (its consumer already owns those attrs). Behavior-preserving extension
        hook — the runtime stays wire-blind (#1887, ADR-022 Iter 1).
        """

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        """Push a freshly-captured time-travel :class:`EventSnapshot` to the client.

        Called by ``_dispatch_event_inner`` (and the component / sticky-child
        branches) immediately after ``record_event_end`` finalizes the snapshot,
        so the debug panel's Time Travel tab can incrementally populate its
        history without re-sending the entire buffer on every event (ADR-022 Iter
        2 Phase 2.2 — replaces the WS ``_maybe_push_tt_event`` direct send with a
        transport hook so the runtime stays wire-blind).

        - WS: serializes + DEBUG-gates the entry and emits a ``time_travel_event``
          frame (the verbatim ``_maybe_push_tt_event`` logic, websocket.py:5267).
        - SSE: a no-op — SSE has no time-travel debug panel surface today.

        Always no-ops when ``snapshot`` is ``None`` (time-travel disabled on the
        view or a guard returned early). Best-effort: a failure here must never
        break the event turn.
        """

    def on_handler_timing(self, view: Any, event_name: str, duration_ms: float) -> None:
        """Record per-handler execution time for percentile telemetry.

        ADR-022 Iter 2 Phase 2.3b (#1907, THE FLIP). The WS bespoke event handler
        recorded the handler duration via ``observability.timings.record_handler_timing``
        in TWO spots — the component path (websocket.py:3498) and the view path
        (websocket.py:3645) — right after the handler call. Once Phase 2.3b routes
        WS events through ``dispatch_event``, that telemetry must keep flowing, so
        the runtime calls this hook after the handler completes on the single-view
        render path (the component path's own port keeps its inline recording).

        - WS: forwards to ``record_handler_timing`` (the same global percentile
          registry the bespoke path populated), so a runtime-routed WS event keeps
          feeding ``djust_audit`` / debug-panel handler-timing stats.
        - SSE: a no-op — the legacy SSE event path never recorded handler timing,
          so adding it here would be a behavior change for SSE; kept WS-scoped.

        Best-effort by contract: implementations MUST swallow their own errors
        (telemetry must never disturb the event turn). Called only on the
        non-actor render path (the actor path goes through ``dispatch_actor_event``
        which does not record per-handler timing on either the bespoke or the
        runtime side).
        """

    def on_render_emitted(
        self,
        view: Any,
        *,
        reason: str,
        version: int,
        event_name: Optional[str],
        html: Optional[str] = None,
        patch_count: Optional[int] = None,
    ) -> None:
        """Emit the per-render observability the WS bespoke render branch owned.

        ADR-022 Iter 2 Phase 2.3b (#1907, THE FLIP). Called by ``_render_and_send``
        whenever a SINGLE-VIEW event render falls into the full-HTML (no-patch)
        branch — i.e. the runtime is about to emit an ``html_update`` instead of a
        ``patch`` because there were no VDOM patches, the view forced full HTML, or
        patch-compression discarded the patches. The WS bespoke handler emitted two
        things here that the runtime ``_render_and_send`` did not:

          1. The **DJE-053 ``logger.warning``** (websocket.py:4215-4237): a
             user-facing diagnostic warning when an event with an established VDOM
             baseline (``version > 1``) falls back to full HTML — it tells the
             developer their event listeners + DOM state may be lost and points at
             the dj-root / push_event / system-check remedies. This is the ONE
             residual that is production-visible (not DEBUG-gated) and MUST SURVIVE
             the flip (#1079). ``reason == "first_render"`` (``version <= 1``) emits
             a DEBUG log instead, matching the WS ``else`` branch (websocket.py:4239).
          2. The **``_emit_full_html_update`` signal** (websocket.py:4254 +
             3962/4074/4091/4129): a transport-agnostic observability signal the
             debug tooling subscribes to, carrying the reason
             (``first_render`` / ``no_patches`` / ``force_full_html`` /
             ``patch_compression``) + a context snapshot (for ``no_patches``).

        ``reason`` is one of ``first_render`` / ``no_patches`` / ``force_full_html``
        / ``patch_compression``. ``version`` is the RAW Rust render counter (NOT the
        wire version) — the DJE-053 gate keys on ``version > 1`` to distinguish a
        true first render from a diff failure, mirroring websocket.py:4215.

        - WS: emits the DJE-053 warning + the ``_emit_full_html_update`` signal
          verbatim (single source — the bespoke call sites are deleted by the flip).
        - SSE: a no-op — SSE has no debug-panel signal subscriber and the legacy SSE
          render path emitted neither, so this is WS-scoped (zero SSE behavior
          change).

        Best-effort by contract: implementations MUST swallow their own errors.
        Not called on the patch branch (patches emitted → no full-HTML fallback) nor
        the noop / skip-render branch (no render at all).
        """

    def event_context(self, view: Any) -> "contextlib.AbstractAsyncContextManager[None]":
        """Async context manager wrapping a single event handler+render turn.

        Establishes (on enter) and tears down (in ``finally``) the per-event
        serialization + observability scope that the WS bespoke event handler
        owns today, so a WS event routed through the runtime in the Phase 2.3
        final flip serializes against the WS-only tick / server-push / db-notify
        render loops IDENTICALLY (the #560 version-interleave guard).

        The runtime CANNOT own this lock: the render serialization is
        consumer-owned (``LiveViewConsumer._render_lock`` websocket.py:619 +
        ``_processing_user_event`` :622) and SHARED with the ``_run_tick`` /
        ``server_push`` / ``db_notify`` loops. A runtime-local lock would be a
        DIFFERENT object and could not serialize against ticks. So the WS
        transport BORROWS the consumer's existing lock via this hook (it does
        NOT create a new one); the dead runtime-local ``_render_lock`` is gone.

        - WS: ``await consumer._render_lock.acquire()``; set
          ``consumer._processing_user_event = True``; set the #1677 origin-channel
          contextvar; start a ``PerformanceTracker`` + the SQL ``capture_for_event``
          observability scope. On exit (``finally``): reset the origin token, clear
          ``_processing_user_event``, RELEASE the borrowed lock, stop the SQL
          capture + clear the tracker. Mirrors websocket.py:3393-3400 / 3150-3154
          (enter) and websocket.py:4311-4313 (exit).
        - SSE: a no-op async CM — SSE events run single-threaded off the HTTP
          request, with no concurrent tick/push loop to serialize against.

        The actor-event branch (added later in Phase 2.3a) runs OUTSIDE this
        context, matching WS where the actor block holds no render lock.
        """
        return contextlib.nullcontext()

    def uses_actors(self, view: Any) -> bool:
        """Return whether this event turn must be handled by the actor system.

        ADR-022 Iter 2 Phase 2.3a (#1901). The WS bespoke event handler routes a
        top-level event through the per-session Rust actor whenever the consumer
        mounted in actor mode (``use_actors=True`` + a created ``actor_handle``).
        ``dispatch_event`` historically had NO actor branch — so once Phase 2.3b
        flips WS events onto the runtime, a ``use_actors`` view's events would
        silently run the handler IN-PROCESS via the normal render path, desyncing
        the actor's server-side diff baseline. This hook closes that gap.

        - WS: ``consumer.use_actors and consumer.actor_handle is not None`` — the
          exact precondition of the bespoke actor block (websocket.py:3282).
        - SSE: ``False`` — SSE has no bidirectional actor channel and refuses
          ``use_actors`` mounts outright (``dispatch_mount`` guard,
          runtime.py:602), so the actor branch is never reachable on SSE.

        DORMANT until Phase 2.3b: WS events still run on the bespoke handler and
        SSE refuses actors, so no live event turn reaches this hook yet.
        """
        return False

    async def dispatch_actor_event(
        self,
        view: Any,
        event_name: str,
        params: Dict[str, Any],
        *,
        event_ref: Optional[int] = None,
        cache_request_id: Optional[str] = None,
    ) -> None:
        """Run one event turn through the actor system + send the framed result.

        Called by ``_dispatch_event_inner`` (OUTSIDE ``event_context`` — the actor
        block holds NO render lock, matching the WS bespoke block which runs the
        actor path before acquiring the lock) when :meth:`uses_actors` is true and
        the event is NOT routed to a sticky child (the WS
        ``not is_embedded_child_target`` mutual exclusion, websocket.py:3280-3282).

        - WS: runs the bespoke actor block VERBATIM against the consumer —
          time-travel record, shared security + param validation,
          ``actor_handle.event()``, patch/HTML framing with the consumer-owned
          wire version (#1788), error handling, and the deferred-activity flush.
        - SSE: never called (``uses_actors`` is ``False``); raises
          :class:`NotImplementedError` if invoked directly.
        """
        raise NotImplementedError("Actor events are WS-only; SSE refuses use_actors mounts.")

    async def recheck_event_auth(self, view: Any) -> bool:
        """Opt-in per-event auth re-check for a live event turn (#1777, T3).

        ADR-022 Iter 2 Phase 2.3a. Auth runs once at mount and the mount-time
        principal is cached on the session, so a user who logs out / loses a
        permission mid-session would keep dispatching events on the open
        connection until they reconnect. When ``LIVEVIEW_CONFIG['reauth_on_event']``
        is set AND the view declares ``login_required`` / ``permission_required``,
        this hook re-resolves the CURRENT principal and re-runs the view's auth
        check before the handler runs. Called by ``_dispatch_event_inner`` at the
        same point the WS bespoke handler does — after the view-mounted check,
        BEFORE the actor branch and the normal handler path.

        Returns ``True`` to allow the event to proceed, ``False`` to refuse it.
        On a ``False`` return the caller has ALREADY done nothing — it is the
        hook's responsibility to emit any client-visible redirect/error and to
        terminate the transport (matching the WS bespoke behavior: navigate to
        the login url + ``close(4403)``). The caller then clears ``view_instance``
        and aborts the event (see ``_dispatch_event_inner``).

        - WS: when the flag is set + the view requires auth, re-resolves the user
          from the scope session (``channels.auth.get_user``), reflects it onto
          ``view.request.user``, re-runs ``check_view_auth_lightweight``; on
          failure sends a ``navigate`` to the login url + ``close(4403)`` (verbatim
          from websocket.py:3193-3222) and returns ``False``. Fail-safe: any error
          (no session in scope, etc.) skips the re-check and returns ``True``.
        - SSE: when the flag is set + the view requires auth, re-runs
          ``check_view_auth_lightweight`` against the LIVE event-POST request
          (``session._event_request`` — the current POSTer's request.user, not the
          stale mount request); on failure sends an auth-error frame + ends the
          stream and returns ``False``. The owner-binding check (Finding #24) runs
          at the endpoint BEFORE dispatch, so this is the SSE analog of the WS
          mid-session deauth gate — a still-owning POSTer whose auth was revoked is
          refused. Fail-safe identical to WS.

        Default (Protocol): returns ``True`` (no re-check). A transport that does
        not implement this never blocks events.
        """
        return True


class WSConsumerTransport:
    """Transport adapter wrapping ``LiveViewConsumer``.

    Forwards all outbound frames through ``send_json`` so existing client
    JSON-mode behavior is preserved verbatim.
    """

    def __init__(self, consumer: Any):
        self._consumer = consumer

    @property
    def session_id(self) -> str:
        return getattr(self._consumer, "session_id", None) or ""

    @property
    def client_ip(self) -> Optional[str]:
        return getattr(self._consumer, "_client_ip", None)

    # The following pass-throughs let existing helpers (e.g.
    # ``_validate_event_security``) treat the transport interchangeably
    # with the consumer they used to receive directly.
    @property
    def _client_ip(self) -> Optional[str]:
        return self.client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        await self._consumer.send_json(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        await self._consumer.send_error(error, **kwargs)

    async def close(self, code: int = 1000) -> None:
        await self._consumer.close(code=code)

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        """Stamp the consumer-owned wire version + arm recovery (#1858 / #1788 / #1817).

        Routes runtime-emitted client-checked frames (``url_change`` patch /
        html_update) through the SAME ``_next_version_armed`` helper ``handle_event``
        uses, so the wire version stays monotonic with the mount baseline and a later
        ``request_html`` recovery serves the matching version. ``html`` MUST be the full
        PRE-STRIP HTML returned by ``render_with_diff()`` (see
        ``LiveViewConsumer._next_version_armed``). ``rust_version`` is ignored on WS.
        """
        return self._consumer._next_version_armed(html)

    def build_request(self) -> Optional[Any]:
        """WS has no captured HTTP request — the runtime synthesizes from
        ``self.scope`` (carrying the authenticated user + session)."""
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        """No-op for WS: the consumer's own ``handle_mount`` already stamps the
        WS-specific view attrs; the runtime path is used for ``url_change`` and
        ``event`` on WS (RUNTIME_OWNED_VERBS = {"url_change", "event"} since #1907),
        but NOT ``mount`` — so this hook never fires on the WS path today."""
        return None

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        """Emit the DEBUG-gated ``time_travel_event`` frame for WS.

        Delegates to the consumer's existing ``_maybe_push_tt_event`` (the
        verbatim DEBUG-gate + size-cap + ``__components__`` mirror logic at
        websocket.py:5267), so the runtime port and the legacy WS
        ``_handle_event_inner`` path share ONE implementation of the frame shape
        (the #1646 cure — Phase 2.3 deletes the WS-side call sites but keeps this
        single source). No-op when the consumer lacks the helper."""
        push = getattr(self._consumer, "_maybe_push_tt_event", None)
        if push is not None:
            await push(view, snapshot)

    def on_handler_timing(self, view: Any, event_name: str, duration_ms: float) -> None:
        """Forward handler timing to the global percentile registry (#1907).

        Verbatim port of the WS bespoke view-path recording (websocket.py:3645):
        ``record_handler_timing(view_class, event_name, handler_ms)``. Best-effort —
        a telemetry failure must never disturb the event turn, so the import +
        record is wrapped (mirroring the bespoke ``try/except Exception: pass``)."""
        try:
            from djust.observability.timings import record_handler_timing

            record_handler_timing(view.__class__.__name__, event_name, duration_ms)
        except Exception:  # noqa: BLE001 — telemetry must never break the event turn
            pass

    def on_render_emitted(
        self,
        view: Any,
        *,
        reason: str,
        version: int,
        event_name: Optional[str],
        html: Optional[str] = None,
        patch_count: Optional[int] = None,
    ) -> None:
        """Emit the DJE-053 warning + ``_emit_full_html_update`` signal (#1907).

        Verbatim fold of the WS bespoke render-branch observability the flip moves
        off ``_handle_event_inner``:

          * DJE-053 ``logger.warning`` for the ``version > 1`` no-patch fallback
            (websocket.py:4215-4237) — the production-visible developer diagnostic
            that MUST SURVIVE the flip (#1079). ``version <= 1`` (a genuine first
            render) logs at DEBUG instead, matching websocket.py:4239.
          * The ``_emit_full_html_update`` signal carrying ``reason`` +
            (for ``no_patches``) a context snapshot — the single source now that
            the bespoke call sites (websocket.py:4074/4091/4129/4254) are deleted.

        Best-effort: a telemetry/signal failure must never break the event turn."""
        from .websocket import _emit_full_html_update

        # The WS bespoke DJE-053 warning fires for EVERY no-patch html_update frame
        # whose Rust version is > 1 — including a deliberate ``_force_full_html``
        # (the bespoke ``force_full_html`` branch sets ``patches = None`` then falls
        # into the SAME no-patches ``else`` arm that logs DJE-053, websocket.py:4087
        # → 4215). Reproduce that exactly: the warning gate is "this frame carried no
        # patches AND the view had an established VDOM baseline", i.e. NOT a
        # first_render and NOT a patch-compression fallback (which had patches).
        _emits_dje053 = reason in ("no_patches", "force_full_html") and version > 1
        try:
            if _emits_dje053:
                template = getattr(view, "template_name", None) or "<inline template>"
                logger.warning(
                    "[djust] Event '%s' on %s fell back to full HTML update "
                    "(DJE-053). Template: %s. "
                    "VDOM diff returned no patches — this may "
                    "cause event listeners and DOM state to be lost. "
                    "Debugging steps: "
                    "(1) Verify your template has <div dj-root> wrapping "
                    "all dynamic content. "
                    "(2) If this event only updates client-side state, use "
                    "push_event + _skip_render = True instead. "
                    "(3) Run with DJUST_VDOM_TRACE=1 for detailed diff output. "
                    "(4) Run 'python manage.py check --tag djust' to detect "
                    "common configuration issues. "
                    "See: https://djust.org/errors/DJE-053",
                    sanitize_for_log(event_name or ""),
                    view.__class__.__name__,
                    sanitize_for_log(str(template)),
                )
            elif reason == "first_render":
                logger.debug("[WebSocket] First render, sending full HTML update.")
        except Exception:  # noqa: BLE001 — diagnostic logging must never break the turn
            logger.debug("DJE-053 diagnostic emit failed", exc_info=True)

        # Emit the transport-agnostic full-HTML-update signal. The signal subscriber
        # is dev tooling; a failure here must never disturb the event turn. The
        # ``html`` passed in is the rendered (pre-strip) full HTML so ``html_size`` +
        # ``html_snippet`` match the WS bespoke emit (websocket.py:4254-4269). The
        # ``no_patches`` context snapshot is intentionally None here: the WS bespoke
        # path captured the ``get_context_data()`` dict it had in hand for free, but
        # the runtime render path (``render_with_diff``) does not surface that dict
        # to ``_render_and_send``, and re-deriving it would add a render-cost call on
        # the no-patch path. The snapshot is DEBUG-tooling-only metadata (the signal's
        # load-bearing fields are ``reason`` / ``version`` / ``event_name`` /
        # ``html_size``), so dropping it costs no production behavior — tracked as a
        # deferred follow-up. ``_previous_html`` is read-only (never assigned in
        # production), so ``previous_html_snippet`` is inert on both paths.
        try:
            html_for_snapshot = getattr(view, "_previous_html", None)
            _emit_full_html_update(
                view,
                reason,
                event_name,
                html,
                version,
                context_snapshot=None,
                patch_count=patch_count,
                html_snippet=(html[:500] if reason == "no_patches" and html else None),
                previous_html_snippet=(
                    html_for_snapshot[:500]
                    if reason == "no_patches" and html_for_snapshot
                    else None
                ),
            )
        except Exception:  # noqa: BLE001 — observability signal must never break the turn
            logger.debug("full-HTML-update signal emit failed", exc_info=True)

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any) -> AsyncIterator[None]:
        """Borrow the consumer's render-lock + origin + observability for one event.

        ENTER mirrors the WS bespoke ``_handle_event_inner`` setup VERBATIM:

          * ``await consumer._render_lock.acquire()`` — the EXISTING consumer lock
            (websocket.py:3393), NOT a new one, so the runtime serializes against
            the WS-only ``_run_tick`` / ``server_push`` / ``db_notify`` render
            loops (the #560 version-interleave guard);
          * ``consumer._processing_user_event = True`` (websocket.py:3394) so ticks
            yield priority to user interactions;
          * set the #1677 origin-channel contextvar to the consumer's
            ``channel_name`` (websocket.py:3398-3400) so push_to_view broadcasts
            this handler emits skip this session's own redundant self-broadcast;
          * start a ``PerformanceTracker`` + the SQL ``capture_for_event`` scope
            (websocket.py:3150-3154 + 3469-3475) for observability.

        EXIT (``finally``) mirrors websocket.py:4311-4313: reset the origin token,
        clear ``_processing_user_event``, RELEASE the borrowed lock, then close the
        SQL-capture scope + clear the tracker (the tracker is thread-local and the
        WS path overwrites it on the next event, so clearing here avoids leaking it
        across turns on the same worker thread).
        """
        consumer = self._consumer

        # Acquire render lock to serialize with tick renders (#560). The lock is
        # the consumer's EXISTING object (websocket.py:3393) — borrowed, never
        # re-created — so it serializes against the WS tick/push/notify loops.
        await consumer._render_lock.acquire()
        consumer._processing_user_event = True

        # Tag any push_to_view broadcasts this handler emits with the originating
        # channel, so this same session skips its OWN redundant self-broadcast
        # (#1677). Reset in the finally below (websocket.py:3398-3400 / 4311).
        from djust import push as _djust_push

        _origin_token = _djust_push.origin_channel.set(getattr(consumer, "channel_name", None))

        # Observability: comprehensive performance tracking (websocket.py:3150-3154)
        # + per-handler SQL-query capture (websocket.py:3469-3475). Both are
        # transport-owned scopes the runtime borrows so a runtime-routed WS event
        # populates the same debug/perf surfaces the bespoke path did.
        from djust.observability.sql import capture_for_event as _dj_sql_capture
        from djust.performance import PerformanceTracker

        tracker = PerformanceTracker()
        PerformanceTracker.set_current(tracker)

        _sid = getattr(consumer, "session_id", None)
        # ``capture_for_event`` reads ``handler_name`` at enter, but the runtime
        # parses ``event_name`` INSIDE the wrapped body (after this CM has
        # entered), so it isn't available here. The session_id + event_id tags
        # are the load-bearing ones for query attribution; the per-event handler
        # name is omitted (the WS bespoke path tags it because its SQL scope wraps
        # only the handler call, where event_name is already known). See #1899.
        sql_scope = _dj_sql_capture(
            session_id=_sid,
            event_id=f"{_sid}:{time.perf_counter()}" if _sid else None,
        )
        sql_scope.__enter__()
        try:
            yield
        finally:
            sql_scope.__exit__(None, None, None)
            PerformanceTracker.set_current(None)
            _djust_push.origin_channel.reset(_origin_token)
            consumer._processing_user_event = False
            consumer._render_lock.release()

    def uses_actors(self, view: Any) -> bool:
        """WS actor precondition — verbatim from the bespoke block (websocket.py:3282).

        ``consumer.use_actors`` is set from the view class at mount
        (websocket.py:2208) and ``consumer.actor_handle`` is the per-session Rust
        actor created at mount (websocket.py:2213). Both must be truthy for the
        actor path; a WS consumer in non-actor mode (or one where actor creation
        was skipped) returns ``False`` and falls through to the normal render path.
        """
        consumer = self._consumer
        return getattr(consumer, "use_actors", False) and consumer.actor_handle is not None

    async def dispatch_actor_event(
        self,
        view: Any,
        event_name: str,
        params: Dict[str, Any],
        *,
        event_ref: Optional[int] = None,
        cache_request_id: Optional[str] = None,
    ) -> None:
        """Run the WS bespoke actor block VERBATIM against the consumer (#1901).

        This is a line-for-line port of the WS actor branch
        (websocket.py:3282-3379) operating on ``self._consumer`` instead of
        ``self`` (the consumer). The actor branch runs OUTSIDE any render lock
        (the bespoke block acquires no lock before ``actor_handle.event()``), so
        this method is invoked from ``_dispatch_event_inner`` BEFORE
        ``event_context``.

        Framing / version-stamping notes (what Phase 2.3b must watch):
        - The consumer OWNS the monotonic wire version (#1788); the actor's
          ``result['version']`` is IGNORED for the wire — ``_send_update`` is
          stamped with ``consumer._next_version()`` (the same source
          ``handle_event`` uses). The actor's internal version still drives its
          server-side diff baseline.
        - ``cache_request_id`` is read (not popped) from ``params`` by the WS
          bespoke caller; it is forwarded here so the ``@cache`` decorator's
          client round-trip id rides the update frame.
        - ``patches`` may arrive as a JSON STRING from Rust and is parsed via
          ``fast_json_loads`` before send.
        """
        consumer = self._consumer

        # Time-travel debugging (v0.6.1): capture state_before BEFORE the
        # permission check so permission-denied events are also recorded
        # (websocket.py:3284-3292).
        from .time_travel import (
            record_event_end as _tt_end,
            record_event_start as _tt_start,
        )

        _tt_snapshot = _tt_start(view, event_name, params, event_ref)
        _tt_error: Optional[str] = None
        try:
            logger.info("Handling event '%s' with actor system", event_name)

            # Security checks (shared with non-actor paths) — run on the RESOLVED
            # target. The actor branch only fires for the top-level view (the
            # caller excludes sticky-child events), so ``view`` IS the top-level
            # view here (websocket.py:3300-3305).
            handler = await _validate_event_security(
                consumer, event_name, view, consumer._rate_limiter
            )
            if handler is None:
                _tt_error = "permission_denied"
                return

            # Validate parameters before sending to actor (websocket.py:3308-3323).
            coerce = get_handler_coerce_setting(handler)
            positional_args = params.pop("_args", []) if isinstance(params, dict) else []
            validation = validate_handler_params(
                handler, params, event_name, coerce=coerce, positional_args=positional_args
            )
            if not validation["valid"]:
                logger.error("Parameter validation failed: %s", validation["error"])
                _tt_error = "validation_failed"
                await consumer.send_error(
                    validation["error"],
                    validation_details={
                        "expected_params": validation["expected"],
                        "provided_params": validation["provided"],
                        "type_errors": validation["type_errors"],
                    },
                )
                return

            # Call actor event handler (will call Python handler internally)
            # (websocket.py:3326).
            result = await consumer.actor_handle.event(event_name, params)

            # Send patches if available, otherwise full HTML. Ignore the actor
            # ``result['version']`` for the wire — the consumer owns the monotonic
            # wire version (#1788). The actor's internal version still drives its
            # diff-baselining server-side (websocket.py:3328-3353).
            patches = result.get("patches")
            html = result.get("html")

            if patches:
                if isinstance(patches, str):
                    patches = fast_json_loads(patches)
            else:
                logger.info(
                    "No patches from actor, sending full HTML update (length: %d). "
                    "Run with DJUST_VDOM_TRACE=1 for detailed diff output.",
                    len(html) if html else 0,
                )

            await consumer._send_update(
                patches=patches,
                html=html,
                version=consumer._next_version(),  # consumer-owned (#1788)
                cache_request_id=cache_request_id,
                event_name=event_name,
            )

        except Exception as e:
            _tt_error = str(e)[:200]
            view_class_name = view.__class__.__name__ if view else "Unknown"
            response = handle_exception(
                e,
                error_type="event",
                event_name=event_name,
                view_class=view_class_name,
                logger=logger,
                log_message=f"Error in actor event handling for {view_class_name}.{sanitize_for_log(event_name)}()",
            )
            await consumer.send_json(response)
        finally:
            _tt_end(view, _tt_snapshot, error=_tt_error)
            await consumer._maybe_push_tt_event(view, _tt_snapshot)
            # v0.7.0 — Drain deferred activity queue in the actor path too. The
            # flush is async and awaited inline so drained events complete in the
            # SAME round-trip as this handler (websocket.py:3372-3379).
            if hasattr(view, "_flush_deferred_activity_events"):
                try:
                    await view._flush_deferred_activity_events(consumer)
                except Exception:  # noqa: BLE001
                    logger.exception("dj_activity: deferred-event flush raised (actor path)")

    async def recheck_event_auth(self, view: Any) -> bool:
        """WS per-event auth re-check — verbatim from websocket.py:3193-3222 (#1777).

        Re-resolves the user from the scope session, reflects it onto
        ``view.request.user`` (the mount request stored on the view), and re-runs
        the view's auth check. On failure: navigate to the login url + ``close(4403)``
        and return ``False``. On success or any error (fail-safe): return ``True``.

        Gated on ``reauth_on_event`` + ``login_required``/``permission_required``
        so default views never pay the session read. DORMANT until Phase 2.3b for
        live WS events (WS events still run on the bespoke handler today); the WS
        bespoke handler keeps its own inline copy until the flip — this is the
        runtime port for when WS events route through ``dispatch_event``.
        """
        from .config import config as djust_config

        if not (
            djust_config.get("reauth_on_event")
            and (
                getattr(view, "login_required", None) or getattr(view, "permission_required", None)
            )
        ):
            return True

        consumer = self._consumer
        try:
            from channels.auth import get_user

            from .auth.core import check_view_auth_lightweight

            fresh_user = await get_user(consumer.scope)
            # The mount request is stored on the view (handle_mount:
            # ``view.request = request``), not on the consumer.
            request = getattr(view, "request", None)
            if request is None:
                # No request to re-check against — fail-safe skip (matches the WS
                # bespoke guard, which only re-checks when a request exists).
                return True
            request.user = fresh_user  # reflect current auth for the check + handler
            authorized = await sync_to_async(check_view_auth_lightweight)(view, request)
            if not authorized:
                from django.conf import settings as _dj_settings

                login_url = getattr(view, "login_url", None) or getattr(
                    _dj_settings, "LOGIN_URL", "/accounts/login/"
                )
                await consumer.send_json({"type": "navigate", "to": login_url})
                await consumer.close(code=4403)
                return False
            return True
        except Exception:  # noqa: BLE001 — re-auth is defense-in-depth; never break events
            logger.debug("reauth_on_event re-check skipped (non-fatal, WS)", exc_info=True)
            return True


class SSESessionTransport:
    """Transport adapter wrapping ``SSESession``.

    Frames are pushed onto the session queue; the SSE stream generator
    drains them and writes ``data:`` lines to the client.
    """

    def __init__(self, session: Any):
        self._session = session

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def client_ip(self) -> Optional[str]:
        return getattr(self._session, "_client_ip", None)

    @property
    def _client_ip(self) -> Optional[str]:
        return self.client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        # SSESession.push is a sync method (it uses queue.put_nowait).
        self._session.push(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        await self._session.send_error(error, **kwargs)

    async def close(self, code: int = 1000) -> None:
        await self._session.close(code=code)

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        """SSE has no per-connection consumer counter — preserve the Rust version (#1858).

        SSE stamps the raw ``render_with_diff()`` version on its frames today (see
        ``sse.py``); the #1788 consumer-counter unification never reached SSE, so SSE
        clients are calibrated to the Rust counter. Returning it unchanged keeps SSE
        behavior identical. (If SSE ever adopts a consumer-owned counter, this is the
        single place to wire it — tracked alongside #1858.)
        """
        return rust_version

    def build_request(self) -> Optional[Any]:
        """Return the REAL HTTP request that established the SSE stream.

        The stream-GET view stashes the request on the session
        (``session._request``) before driving ``dispatch_mount`` so the runtime
        mounts against the authenticated victim/owner request — preserving the
        legacy ``_sse_mount_view`` behavior (real ``request.user`` / session /
        path for auth + object-perm) after convergence (#1887, ADR-022 Iter 1).
        Returns ``None`` if no request was stashed (defensive — falls back to
        the runtime's synthesized request).
        """
        return getattr(self._session, "_request", None)

    def on_view_mounted(self, view_instance: Any) -> None:
        """Stamp SSE-transport identity on the mounted view (legacy parity).

        ``_sse_mount_view`` set ``session.view_instance`` (the endpoint-level
        "is this session mounted?" reference the ``/event/`` + ``/message/``
        guards read, plus SSE introspection) and ``_sse_session_id`` /
        ``_sse_session`` on the view, and the real ``_websocket_query_string``
        from the request. The runtime already sets ``_websocket_session_id`` /
        ``_websocket_path``; this adds the SSE-only attrs so nothing the legacy
        path exposed is lost (#1887, ADR-022 Iter 1). Setting
        ``session.view_instance`` is load-bearing: the runtime owns
        ``runtime.view_instance`` for dispatch, but the SSE endpoints gate on
        ``session.view_instance`` — the two must agree after a successful mount.
        """
        session = self._session
        session.view_instance = view_instance
        view_instance._sse_session_id = session.session_id
        view_instance._sse_session = session
        request = getattr(session, "_request", None)
        if request is not None:
            view_instance._websocket_query_string = request.META.get("QUERY_STRING", "")

    async def on_event_recorded(self, view: Any, snapshot: Any) -> None:
        """No-op for SSE — there is no time-travel debug-panel surface on the SSE
        transport today (the debug panel is a WS-only client feature). The
        runtime still RECORDS the snapshot into the view's buffer (server-side
        time-travel state is transport-agnostic); only the incremental client
        push is WS-specific."""
        return None

    def on_handler_timing(self, view: Any, event_name: str, duration_ms: float) -> None:
        """No-op for SSE (#1907).

        The legacy SSE event path never recorded per-handler percentile timing —
        ``record_handler_timing`` was a WS-only telemetry call. Forwarding it here
        would be a behavior change for SSE (new telemetry rows), so it stays
        WS-scoped: SSE drops the timing exactly as it always has."""
        return None

    def on_render_emitted(
        self,
        view: Any,
        *,
        reason: str,
        version: int,
        event_name: Optional[str],
        html: Optional[str] = None,
        patch_count: Optional[int] = None,
    ) -> None:
        """No-op for SSE (#1907).

        SSE has no debug-panel ``full_html_update`` signal subscriber and the legacy
        SSE render path emitted neither the DJE-053 warning nor the signal, so this
        WS-scoped fold is a no-op on SSE — zero SSE behavior change. (The DJE-053
        warning is a developer diagnostic for the WS VDOM-baseline-loss case; SSE's
        own render path retains its existing logging.)"""
        return None

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any) -> AsyncIterator[None]:
        """No-op event context for SSE.

        SSE events run single-threaded off the HTTP ``/event/`` request — there
        is no concurrent tick / server-push / db-notify render loop to serialize
        against (those are WS-only), so SSE needs neither the render lock nor the
        WS-specific observability/origin scope. Yields immediately, mirroring the
        legacy ``_sse_handle_event`` (which never acquired a lock)."""
        yield

    def uses_actors(self, view: Any) -> bool:
        """SSE never uses actors (#1901).

        SSE has no bidirectional channel for actor messages and the actor
        channel-layer code lives in ``websocket.py``, which the runtime SSE path
        doesn't traverse. ``dispatch_mount`` refuses a ``use_actors`` view over
        SSE outright (runtime.py:602), so the actor branch is unreachable here.
        Returning ``False`` keeps every SSE event on the normal render path.
        """
        return False

    async def dispatch_actor_event(
        self,
        view: Any,
        event_name: str,
        params: Dict[str, Any],
        *,
        event_ref: Optional[int] = None,
        cache_request_id: Optional[str] = None,
    ) -> None:
        """Never called on SSE (``uses_actors`` is ``False``) — guard with a raise."""
        raise NotImplementedError("Actor events are WS-only; SSE refuses use_actors mounts.")

    async def recheck_event_auth(self, view: Any) -> bool:
        """SSE per-event auth re-check against the LIVE event-POST request (#1777).

        SSE parity for the WS mid-session deauth gate. The owner-binding check
        (Finding #24) already runs at the ``/event/`` + ``/message/`` endpoints
        BEFORE dispatch, so the POSTer is the session owner — but a still-owning
        principal whose ``login_required`` / ``permission_required`` posture was
        revoked mid-session would keep driving the view. When ``reauth_on_event``
        is set + the view requires auth, re-run ``check_view_auth_lightweight``
        against the CURRENT event-POST request (``session._event_request``, stamped
        by the SSE endpoint just before ``dispatch_event`` — the live POSTer's
        ``request.user``, NOT the stale mount request that ``build_request``
        returns). On failure: send an auth-error frame + end the stream and return
        ``False``. Fail-safe: any error skips the re-check and returns ``True``.

        LIVE for SSE today: SSE events route through ``dispatch_event`` →
        ``_dispatch_event_inner`` since Iter 1 (#1887), so this hook fires on the
        live SSE event path the moment ``reauth_on_event`` is enabled.
        """
        from .config import config as djust_config

        if not (
            djust_config.get("reauth_on_event")
            and (
                getattr(view, "login_required", None) or getattr(view, "permission_required", None)
            )
        ):
            return True

        session = self._session
        try:
            from .auth.core import check_view_auth_lightweight

            # The live event-POST request, stamped on the session by the SSE
            # endpoint right before dispatch. Falls back to the mount request
            # (``session._request``) defensively if the endpoint didn't stamp one
            # (e.g. a direct runtime.dispatch_event call) — the mount request
            # still carries a real user, so the re-check stays meaningful.
            request = getattr(session, "_event_request", None) or getattr(session, "_request", None)
            if request is None:
                return True
            authorized = await sync_to_async(check_view_auth_lightweight)(view, request)
            if not authorized:
                await session.send_error(
                    "Session is no longer authorized. Please reload the page.",
                    code=4403,
                )
                await session.close(code=4403)
                return False
            return True
        except Exception:  # noqa: BLE001 — re-auth is defense-in-depth; never break events
            logger.debug("reauth_on_event re-check skipped (non-fatal, SSE)", exc_info=True)
            return True


# ------------------------------------------------------------------ #
# ViewRuntime
# ------------------------------------------------------------------ #


class ViewRuntime:
    """Wire-blind runtime for a single mounted LiveView session.

    Responsible for view instantiation, auth check, mount, render, and
    all subsequent dispatch (events, URL changes). Output flows through
    ``self.transport.send(...)`` — the runtime has no notion of WebSocket
    frames or HTTP responses.

    Attributes:
        transport: The :class:`Transport` adapter for this session.
        view_instance: The mounted LiveView, or ``None`` before mount.
        session_id: Stable per-session ID, proxied from the transport.
        scope: Optional ASGI scope (only available for WS-backed runtimes).
        rate_limiter: Optional :class:`ConnectionRateLimiter` (per-session).
    """

    def __init__(
        self,
        transport: Transport,
        *,
        scope: Optional[Dict[str, Any]] = None,
        rate_limiter: Optional[ConnectionRateLimiter] = None,
    ) -> None:
        self.transport = transport
        self.view_instance: Optional[Any] = None
        self.scope = scope
        self._rate_limiter = rate_limiter or ConnectionRateLimiter()
        # NOTE: the runtime does NOT own a render lock. Render serialization is
        # consumer-owned (``LiveViewConsumer._render_lock``, websocket.py:619) and
        # SHARED with the WS-only tick / server-push / db-notify loops; a
        # runtime-local lock would be a different object and could not serialize
        # against those (#560). The transport borrows the consumer's existing lock
        # via ``transport.event_context(view)`` (ADR-022 Iter 2 Phase 2.3a, #1899).

    # ------------------------------------------------------------------ #
    # Public properties
    # ------------------------------------------------------------------ #

    @property
    def session_id(self) -> str:
        return self.transport.session_id

    # Compatibility shim — security helpers read ``_client_ip`` directly.
    @property
    def _client_ip(self) -> Optional[str]:
        return self.transport.client_ip

    # ------------------------------------------------------------------ #
    # Top-level dispatch
    # ------------------------------------------------------------------ #

    async def dispatch_message(self, data: Dict[str, Any]) -> None:
        """Route an inbound frame to the appropriate handler by ``type``.

        Unknown types emit a structured error envelope but never raise —
        this is the forward-compat seam for transports that may receive
        future frame types (uploads, presence) the runtime doesn't yet
        own.
        """
        msg_type = data.get("type")
        if msg_type == "mount":
            await self.dispatch_mount(data)
        elif msg_type == "event":
            await self.dispatch_event(data)
        elif msg_type == "url_change":
            await self.dispatch_url_change(data)
        else:
            await self.transport.send_error(f"Unknown message type: {msg_type}")

    # ------------------------------------------------------------------ #
    # Mount dispatch (used by SSE in this PR; WS still uses handle_mount)
    # ------------------------------------------------------------------ #

    async def dispatch_mount(self, data: Dict[str, Any]) -> None:
        """Mount a LiveView from a mount frame.

        Idempotent: if ``view_instance`` is already set the call is a no-op.
        This protects against the legacy ``?view=`` GET-mount + new POST
        mount-frame double-fire (Risk #1 in the plan).

        Frame schema (matches WebSocket ``handle_mount`` at
        ``websocket.py:1657-1660``)::

            {
                "type": "mount",
                "view": "myapp.views.HomeView",  # dotted view path
                "params": {...},                  # initial mount kwargs
                "url": "/items/42/",              # client's window.location.pathname
                "has_prerendered": false,
                "client_timezone": "America/New_York",
            }
        """
        # Idempotent — second mount on the same runtime is a no-op.
        if self.view_instance is not None:
            return

        view_path = data.get("view")
        params: Dict[str, Any] = data.get("params", {}) or {}
        # F23 (#1819 traversal fix, parallel-path-drift #1646): the client URL is
        # attacker-controlled and is fed to RequestFactory.get() / resolve() /
        # logs / build_absolute_uri below. Validate it through the SAME shared
        # validator the WebSocket path uses (websocket.handle_mount), so the SSE
        # / runtime path neutralises "/%2e%2e/admin/" identically. _build_request
        # validates again defensively.
        from .security.mount import is_view_path_allowed, validate_mount_url

        page_url = validate_mount_url(data.get("url", "/"))
        client_timezone = data.get("client_timezone")
        # has_prerendered (ADR-022 Iter 3 Phase 3.0): the client signals it already
        # holds server-rendered HTML for this view. Consumed below at the mount
        # frame to decide ``skip_html_for_resume`` (WS websocket.py:2086).
        has_prerendered = bool(data.get("has_prerendered", False))

        if not view_path:
            await self.transport.send_error("Missing view path in mount request")
            return

        # ---- Security (F22 — unsafe reflection / arbitrary module import) ----
        # Shape + allowlist gate (no import) for a clean early reject frame even
        # when a test overrides _instantiate_view. The full resolver
        # (resolve_view_class) runs inside _instantiate_view as defense in depth.
        if not is_view_path_allowed(view_path):
            logger.warning(
                "Blocked attempt to mount disallowed/uninitialized view: %s",
                sanitize_for_log(view_path),
            )
            await self.transport.send_error(
                _safe_error(f"View {view_path} is not allowed", "View not found")
            )
            return

        # ---- Instantiate view (extension hook for tests) ----
        view_instance = self._instantiate_view(view_path)
        if view_instance is None:
            return  # _instantiate_view already pushed an error

        # ---- use_actors limitation guard (#1240 / ADR-016 §Implementation) ----
        # SSE doesn't have a bidirectional channel for actor messages and
        # the actor channel-layer code lives in `websocket.py`, which the
        # runtime path doesn't traverse. Emit a structured error envelope
        # rather than letting the mount partially succeed and fail
        # downstream with an opaque AttributeError.
        if getattr(view_instance, "use_actors", False):
            await self.transport.send_error(
                "use_actors is not supported over SSE; mount over WebSocket instead",
                error_type="mount_error",
                view_class=view_path,
            )
            return

        self.view_instance = view_instance

        # Stash transport identity on the view (used by VDOM caching).
        view_instance._websocket_session_id = self.session_id
        view_instance._websocket_path = page_url
        view_instance._websocket_query_string = ""

        # Transport-specific identity hook (#1887): SSE stamps _sse_session_id /
        # _sse_session + the real query string here so the converged runtime
        # mount preserves everything legacy _sse_mount_view exposed. WS no-op.
        # getattr-guarded so duck-typed test transport fakes that predate this
        # Protocol method don't need to implement it.
        on_view_mounted = getattr(self.transport, "on_view_mounted", None)
        if on_view_mounted is not None:
            on_view_mounted(view_instance)

        # Optional client timezone (validate IANA string).
        view_instance.client_timezone = None
        if client_timezone:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(client_timezone)
                view_instance.client_timezone = client_timezone
            except Exception:
                logger.warning("Invalid client timezone: %s", client_timezone)

        # ---- Build request ----
        request = await self._build_request(page_url=page_url, params=params)

        try:
            view_instance.request = request
            # _django_session_key (ADR-022 Iter 3 Phase 3.1): the signed
            # state-snapshot HMAC binds ``sid`` to the Django session key (see
            # below + python/djust/security/state_snapshot.py). WS handle_mount
            # stamps this off the scope session (websocket.py:2294); the runtime
            # path sources it from ``request.session`` so the runtime/SSE mount's
            # ``unsign_snapshot`` / ``sign_snapshot`` calls validate + emit the
            # SAME session binding (a snapshot signed under session S1 cannot
            # restore under S2 on this path either). None/empty for anonymous —
            # the envelope degrades to slug-only, matching WS.
            session_obj = getattr(request, "session", None)
            view_instance._django_session_key = (
                getattr(session_obj, "session_key", None) if session_obj is not None else None
            )
            if hasattr(view_instance, "_initialize_temporary_assigns"):
                await sync_to_async(view_instance._initialize_temporary_assigns)()
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error initializing {sanitize_for_log(view_path)}",
            )
            await self.transport.send(response)
            self.view_instance = None
            return

        # ---- Pre-mount security sequence (auth + tenant resolve/bind) ----
        # _check_auth runs the shared djust.auth.core.run_pre_mount_auth sequence
        # (auth via check_view_auth, then — only on auth success — _ensure_tenant
        # + the tenant ContextVar bind). Single-sourcing the SEQUENCE with the WS
        # + SSE mount paths means a future edit cannot reorder the steps or drop
        # one on this path (#1646 / #1853). The runtime-specific verdict→frame
        # mapping (error frame / navigate frame / tenant-error envelope) stays
        # inside _check_auth; it remains the mockable auth seam tests stub.
        redirect_or_block = await self._check_auth(request)
        if redirect_or_block is not None:
            # _check_auth already pushed the appropriate frame.
            self.view_instance = None
            return

        # ---- on_mount hooks (after auth, before mount) ----
        # Ported from WS handle_mount (websocket.py:2383-2401), placed at the
        # SAME point: AFTER the pre-mount auth sequence, BEFORE mount(). The
        # registered ``on_mount`` hooks (djust.hooks.run_on_mount_hooks) run
        # against the live view + request and may return a redirect URL.
        #
        # Transport-agnostic redirect handling: the WS path additionally
        # ``close(4403)``s the orphaned socket (unless in a mount_batch). The
        # runtime has no socket-level close in its hands (the runtime is the
        # SSE mount path; closing is the transport's concern post-3.3a), so —
        # matching the runtime's existing auth-redirect handling in _check_auth
        # (which sends a ``navigate`` frame + aborts WITHOUT a socket close) —
        # this path emits the navigate frame, clears the unmounted view, and
        # returns. The transport-level ``close`` belongs to the
        # ``finalize_mount_auth`` hook landing in Phase 3.2/3.3a; it is NOT
        # added here.
        from .hooks import run_on_mount_hooks

        hook_redirect = await sync_to_async(run_on_mount_hooks)(view_instance, request, **params)
        if hook_redirect:
            await self.transport.send({"type": "navigate", "to": hook_redirect})
            self.view_instance = None
            return
        # ---- End on_mount hooks ----

        # ---- Mount kwargs ----
        mount_kwargs = dict(params)
        url_kwargs = self._resolve_url_kwargs(page_url)
        if url_kwargs:
            mount_kwargs.update(url_kwargs)

        # ---- State restoration (ADR-022 Iter 3 Phase 3.1) ----
        # Ported from WS handle_mount (websocket.py:2403-2587), VERBATIM gates +
        # caps. Two restore mechanisms run in lieu of calling mount(); each sets
        # the ``_mounted_from_restore`` resume flag (the runtime analogue of WS's
        # ``mounted`` / ``mounted_from_snapshot``) that the mount-frame
        # construction below reads for ``skip_html_for_resume``.
        #
        # GATE (#1552): BOTH mechanisms are gated on
        # ``enable_state_snapshot = True``. Default views never restore — a fresh
        # mount() runs unconditionally for them (parity with WS, which gated the
        # saved_state read on the same flag to fix the #1466-clobbered-baseline
        # regression). For SSE this is a no-op unless the view opts in AND a
        # snapshot / saved-state is present in the request/frame.
        mounted_from_restore = False

        # (1) Session-saved-state restore (WS websocket.py:2424-2474). Reattaches
        # public + private state + per-process side-effect registrations +
        # component state saved by the per-event session-save (#1466) onto a
        # plain reconnect. Gated on ``enable_state_snapshot`` (#1552).
        opt_in = getattr(view_instance, "enable_state_snapshot", False)
        session = getattr(request, "session", None)
        if opt_in and session is not None:
            view_key = f"liveview_{page_url}"
            try:
                saved_state = await session.aget(view_key, {})
            except Exception:  # noqa: BLE001 — session backend may be absent
                saved_state = {}
            if saved_state:
                from .security import safe_setattr

                for key, value in saved_state.items():
                    safe_setattr(view_instance, key, value, allow_private=False)

                # Restore user-defined _private attributes (mirrors WS + HTTP).
                private_state = await session.aget(f"{view_key}__private", {})
                if private_state:
                    await sync_to_async(view_instance._restore_private_state)(private_state)

                # Issues #889/#893/#894 — replay process-wide side effects that
                # mount() would have re-issued (UploadManager, PresenceManager,
                # PostgresNotifyListener registrations). hasattr-guarded.
                if hasattr(view_instance, "_restore_upload_configs"):
                    await sync_to_async(view_instance._restore_upload_configs)()
                if hasattr(view_instance, "_restore_presence"):
                    await sync_to_async(view_instance._restore_presence)()
                if hasattr(view_instance, "_restore_listen_channels"):
                    await sync_to_async(view_instance._restore_listen_channels)()

                await sync_to_async(view_instance._initialize_temporary_assigns)()
                await sync_to_async(view_instance._assign_component_ids)()

                # Restore component state.
                from .components.base import Component, LiveComponent

                component_state = await session.aget(f"{view_key}_components", {})
                for key, state in component_state.items():
                    component = getattr(view_instance, key, None)
                    if component and isinstance(component, (Component, LiveComponent)):
                        await sync_to_async(view_instance._restore_component_state)(
                            component, state
                        )

                mounted_from_restore = True

        # (2) Signed state_snapshot HMAC restore (WS websocket.py:2491-2587,
        # SECURITY-BOUNDARY). Only fires when NOT already restored from the
        # session above (WS gates this inside ``if not mounted:``). The client
        # echoes back an OPAQUE server-signed blob; the caps + HMAC binding below
        # are byte-identical to WS — see python/djust/security/state_snapshot.py.
        if not mounted_from_restore:
            from django.conf import settings

            state_snapshot = data.get("state_snapshot")
            # Fix #11 — operator-level master switch (WS websocket.py:2499).
            state_master_on = getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
            if state_master_on and state_snapshot and opt_in:
                snapshot_slug = state_snapshot.get("view_slug", "")
                if snapshot_slug == view_path:
                    state_dict = None
                    # Finding #4 (CWE-345 → CWE-915): verify signature + TTL +
                    # identity (slug + session) BEFORE trusting any bytes. An
                    # unsigned/forged/tampered/expired/cross-context snapshot
                    # returns None and falls through to a normal mount().
                    from .security import unsign_snapshot

                    signed_blob = state_snapshot.get("state_json", "")
                    session_key = getattr(view_instance, "_django_session_key", None)
                    raw_state = unsign_snapshot(signed_blob, view_path, session_key)
                    if raw_state is None:
                        # Rejected at the signature/identity/TTL gate.
                        # unsign_snapshot already logged the reason.
                        state_dict = None
                    # Fix #6 — hard server-side size cap on the VERIFIED inner
                    # snapshot JSON (64 KB; matches client clamp).
                    elif len(raw_state) > 65536:
                        logger.warning(
                            "state_snapshot state_json too large "
                            "(%d bytes > 64KB) for %s; ignoring",
                            len(raw_state),
                            sanitize_for_log(view_path),
                        )
                        state_dict = None
                    else:
                        try:
                            state_dict = json.loads(raw_state)
                        except (ValueError, TypeError):
                            logger.warning(
                                "state_snapshot malformed JSON for view %s; "
                                "proceeding with fresh mount",
                                sanitize_for_log(view_path),
                            )
                            state_dict = None
                        # Fix #8 — enforce dict type after decode.
                        if state_dict is not None and not isinstance(state_dict, dict):
                            logger.warning(
                                "state_snapshot state_json is not a dict (got %s) for %s; ignoring",
                                type(state_dict).__name__,
                                sanitize_for_log(view_path),
                            )
                            state_dict = None
                        # Fix #7 — keyset DoS cap (256 keys).
                        if state_dict is not None and len(state_dict) > 256:
                            logger.warning(
                                "state_snapshot keyset too large (%d keys > 256) for %s; ignoring",
                                len(state_dict),
                                sanitize_for_log(view_path),
                            )
                            state_dict = None
                    if state_dict is not None and await sync_to_async(
                        view_instance._should_restore_snapshot
                    )(request):
                        try:
                            await sync_to_async(view_instance._restore_snapshot)(state_dict)
                            mounted_from_restore = True
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "state_snapshot _restore_snapshot failed "
                                "for %s; falling back to mount()",
                                sanitize_for_log(view_path),
                            )
                            mounted_from_restore = False

        # Expose the resume flag for the mount-frame construction below
        # (skip_html_for_resume) — Phase 3.0 wired the read; this sets it.
        view_instance._mounted_from_restore = mounted_from_restore

        if not mounted_from_restore:
            try:
                await sync_to_async(view_instance.mount)(request, **mount_kwargs)
            except Exception as exc:
                response = handle_exception(
                    exc,
                    error_type="mount",
                    view_class=view_path,
                    logger=logger,
                    log_message=f"Error in {sanitize_for_log(view_path)}.mount()",
                )
                await self.transport.send(response)
                return

        # ---- Object-permission check (ADR-017 §Decision 5, post-mount) ----
        # Iter 0 / #1885: mirrors the WS handle_mount post-mount object check
        # (websocket.py:2554-2573) so the runtime mount path enforces the SAME
        # ADR-017 object-level authorization. Placed AFTER mount() — so
        # get_object() can read URL-derived attrs (e.g. self.<x>_id) that mount()
        # populated — and BEFORE handle_params + render, so a denied object is
        # NEVER rendered or sent to the client (latent IDOR, findings #10-#12).
        # Routed through the shared enforce_object_permission chokepoint (the same
        # one dispatch_url_change already uses, runtime.py + the parity net) so
        # the runtime path cannot drift from WS/SSE/API (#1646). No-op for views
        # that don't override get_object (behavior-preserving). Fail-closed: the
        # chokepoint maps a None request / any non-PermissionDenied get_object
        # failure to denial; the raised PermissionDenied becomes the runtime's
        # natural permission_denied error frame (matching dispatch_url_change).
        from django.core.exceptions import PermissionDenied

        from .auth.core import enforce_object_permission

        try:
            await sync_to_async(enforce_object_permission)(view_instance, request)
        except PermissionDenied:
            logger.info(
                "Object-permission denied for %s (runtime mount)",
                view_instance.__class__.__name__,
            )
            await self.transport.send_error(
                "Access denied for this object.", code="permission_denied"
            )
            self.view_instance = None
            return

        # ---- Mount-request stash + dirty/private baselines (ADR-022 Iter 3
        # Phase 3.0 transport-agnostic grows) ----
        # Ported from WS handle_mount (websocket.py:2596-2603), placed at the
        # SAME point: AFTER mount() + the object-permission check, BEFORE
        # handle_params.
        #
        # #1895 (KNOWN required fold): stash the mount request + kwargs on the
        # view. The runtime's OWN per-event session-save fallback
        # (_persist_state_after_event runtime.py:2030, _persist_sticky_child_
        # after_event runtime.py:2109) ALREADY reads ``_djust_mount_request`` to
        # discover the save session + namespace path. Without this stash that
        # fallback silently degrades to the scope session on the converged
        # event path; with it, the converged path persists under the SAME
        # ``liveview_{request.path}`` key the WS bespoke path uses. Stashing it
        # here makes that fallback LIVE on the runtime/SSE mount path (and, post
        # 3.3b flip, the WS mount path). Behavioral-parity pin in
        # test_transport_behavioral_parity.py (gate-off: delete this stash → the
        # mount-stash net goes RED).
        view_instance._djust_mount_request = request
        view_instance._djust_mount_kwargs = mount_kwargs

        # _snapshot_user_private_attrs + _capture_dirty_baseline (WS
        # websocket.py:2598-2603): record the post-mount private-attr name set
        # and the dirty-tracking baseline so subsequent renders/change-detection
        # see the correct "since mount" delta. Both are pure view methods (no
        # transport), hasattr-guarded to stay safe against duck-typed test views.
        if hasattr(view_instance, "_snapshot_user_private_attrs"):
            await sync_to_async(view_instance._snapshot_user_private_attrs)()
        if hasattr(view_instance, "_capture_dirty_baseline"):
            await sync_to_async(view_instance._capture_dirty_baseline)()

        # ---- handle_params (Phoenix-parity, fixes #1237 bug 3) ----
        try:
            from urllib.parse import urlencode

            query_string = urlencode(params) if params else ""
            uri = f"{page_url}?{query_string}" if query_string else page_url
            if hasattr(view_instance, "handle_params"):
                await sync_to_async(view_instance.handle_params)(params, uri)
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.handle_params()",
            )
            await self.transport.send(response)
            return

        # ---- Initial render ----
        try:
            if hasattr(view_instance, "_initialize_rust_view"):
                await sync_to_async(view_instance._initialize_rust_view)(request)
            if hasattr(view_instance, "_sync_state_to_rust"):
                await sync_to_async(view_instance._sync_state_to_rust)()
            html, _patches, version = await sync_to_async(view_instance.render_with_diff)()
            if hasattr(view_instance, "_strip_comments_and_whitespace"):
                html = await sync_to_async(view_instance._strip_comments_and_whitespace)(html)
            if hasattr(view_instance, "_extract_liveview_content"):
                html = await sync_to_async(view_instance._extract_liveview_content)(html)
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="render",
                view_class=view_path,
                logger=logger,
                log_message=f"Error rendering {sanitize_for_log(view_path)}",
            )
            await self.transport.send(response)
            return

        mount_msg: Dict[str, Any] = {
            "type": "mount",
            "session_id": self.session_id,
            "view": view_path,
            "version": version,
        }

        # has_prerendered / skip_html_for_resume (ADR-022 Iter 3 Phase 3.0 grow,
        # WS websocket.py:2804-2816). When the client carries pre-rendered HTML
        # AND the view's state was restored from a session snapshot (a resume),
        # the client's DOM already reflects the saved state, so sending the
        # freshly-rendered HTML would force a redundant DOM swap — skip the
        # ``html``/``has_ids`` keys (the ``version`` still flows so patches stay
        # in sync). ``_mounted_from_restore`` is the runtime analogue of WS's
        # ``mounted`` flag. Phase 3.0 wired this read while the flag was DORMANT;
        # Phase 3.1's state-restore block above now SETS it on a session /
        # signed-snapshot resume, so the resume optimization is LIVE for the
        # runtime/SSE mount path.
        mounted_from_restore = getattr(view_instance, "_mounted_from_restore", False)
        skip_html_for_resume = bool(mounted_from_restore) and bool(has_prerendered)
        if html is not None and not skip_html_for_resume:
            mount_msg["html"] = html
            mount_msg["has_ids"] = "dj-id=" in html
        elif skip_html_for_resume:
            logger.info(
                "Runtime: skipping mount HTML for resume of %s — client already has DOM",
                sanitize_for_log(view_path),
            )

        # state_snapshot_signed EMIT (ADR-022 Iter 3 Phase 3.1, WS
        # websocket.py:2754-2792). When the view opts in (``enable_state_snapshot``)
        # AND the master switch is on, ship the SIGNED public-state blob on the
        # mount frame so the client can store it verbatim and echo it back on the
        # next back-navigation (the restore path above verifies the HMAC + TTL +
        # slug/sid binding). Non-opt-in views never have state shipped. The
        # signature binds slug + session key (``_django_session_key``, stamped
        # above), so a valid snapshot cannot be replayed across views or sessions.
        # Wrapped so snapshot emission can NEVER break the mount (#1788 posture).
        try:
            from django.conf import settings

            state_master_on = getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
            if state_master_on and getattr(view_instance, "enable_state_snapshot", False):
                snapshot_fn = getattr(view_instance, "_capture_snapshot_state", None)
                if callable(snapshot_fn):
                    public_state = await sync_to_async(snapshot_fn)()
                    if isinstance(public_state, dict) and public_state:
                        from .security import sign_snapshot

                        # Canonical serialization so the signed bytes are stable
                        # (and match the inner JSON the restore path json.loads-es
                        # after unsigning).
                        state_json = json.dumps(public_state, sort_keys=True, separators=(",", ":"))
                        session_key = getattr(view_instance, "_django_session_key", None)
                        mount_msg["state_snapshot_signed"] = sign_snapshot(
                            state_json, view_path, session_key
                        )
        except Exception:  # noqa: BLE001 — snapshot emission must never break mount
            logger.exception(
                "Failed to emit state_snapshot_signed for %s; proceeding without snapshot",
                sanitize_for_log(view_path),
            )

        # Optional cache_config (mirrors WS consumer)
        cache_config = self._extract_cache_config(view_instance)
        if cache_config:
            mount_msg["cache_config"] = cache_config

        # optimistic_rules (DEP-002, WS websocket.py:2823-2826) — descriptor
        # components with tier="optimistic" ship their client-side rules on the
        # mount frame so the client can apply an optimistic UI update before the
        # server round-trip.
        optimistic_rules = self._extract_optimistic_rules(view_instance)
        if optimistic_rules:
            mount_msg["optimistic_rules"] = optimistic_rules

        # upload_configs (WS websocket.py:2828-2834) — views using UploadMixin
        # ship their upload configuration so the client knows the accept/size
        # constraints before the first upload frame.
        upload_manager = getattr(view_instance, "_upload_manager", None)
        if upload_manager:
            upload_state = upload_manager.get_upload_state()
            if upload_state:
                mount_msg["upload_configs"] = {
                    name: info["config"] for name, info in upload_state.items()
                }

        await self.transport.send(mount_msg)
        logger.info(
            "Runtime: mounted view %s (session %s)",
            sanitize_for_log(view_path),
            sanitize_for_log(self.session_id),
        )

        # Mount-time queue drain (ADR-022 Iter 3 Phase 3.0, WS
        # websocket.py:2916-2917). Drain push events queued during mount()/on_mount
        # hooks (#1283) and dispatch any start_async()/assign_async() callbacks
        # scheduled in mount() (#1280), AFTER the mount frame establishes the view
        # on the client. CRITICAL (#1391 source-grep pin moved to this location in
        # test_handle_mount_drains_queues.py): WS mount drains ONLY these two
        # queues — NOT the 8-queue ``_flush_all_pending`` that the turn-end event
        # path uses. Preserve EXACTLY: mount establishes the baseline, it does not
        # run a full event turn-end flush. ``_flush_push_events`` is sync (queues
        # the sends fire-and-forget); ``_dispatch_async_work`` takes the event_name
        # (None at mount).
        self._flush_push_events()
        self._dispatch_async_work(None)

    # ------------------------------------------------------------------ #
    # Event dispatch (used by SSE in this PR; WS still uses handle_event)
    # ------------------------------------------------------------------ #

    async def dispatch_event(self, data: Dict[str, Any]) -> None:
        """Dispatch a client event to the mounted view.

        Frame schema::

            {
                "type": "event",
                "event": "increment",
                "params": {"amount": 1, ...},
            }

        Returns ``noop`` on no-state-change, ``patch`` on diff-able render,
        ``html_update`` otherwise. Mirrors the simplified single-view path
        from the legacy ``_sse_handle_event``.

        Wrapped in the tenant context (Finding #6) so the handler + render see
        the correct tenant in the tenant-scoped managers, cleared on exit.
        """
        tenant = getattr(self.view_instance, "_tenant", None) if self.view_instance else None
        with _tenant_context(tenant):
            await self._dispatch_event_inner(data)

    async def _dispatch_event_inner(self, data: Dict[str, Any]) -> None:
        """Event dispatch body (see :meth:`dispatch_event` for the tenant wrapper).

        The handler + render runs inside ``transport.event_context(view)`` (ADR-022
        Iter 2 Phase 2.3a, #1899): on WS this BORROWS the consumer's existing
        ``_render_lock`` + sets ``_processing_user_event`` + the #1677 origin
        channel + observability scopes, so a WS event routed here in the Phase
        2.3b flip serializes against the WS-only tick / server-push / db-notify
        render loops identically (the #560 guard). On SSE it is a no-op. The
        view-mounted check runs OUTSIDE the context (we need a non-None view to
        borrow its lock — matching WS, which acquires only after the view exists).

        The actor-event branch (Phase 2.3a, #1901) runs OUTSIDE this context,
        matching WS where the actor block holds no render lock — so the actor
        check is made here, BEFORE entering ``event_context``. The actor path
        applies ONLY when the transport mounted in actor mode AND the event is
        NOT routed to a sticky child (the WS ``not is_embedded_child_target``
        mutual exclusion, websocket.py:3280-3282). Per #1467, ``component_id``
        events do NOT reassign the target view, so — like the WS bespoke block,
        which has no component handling in the actor branch — a ``component_id``
        event on a ``use_actors`` view goes through the actor, not component
        routing; only a ``view_id`` resolving to a different child excludes it.

        DORMANT until Phase 2.3b: ``uses_actors`` is ``False`` for both live
        transports today (WS events still run on the bespoke handler; SSE refuses
        actor mounts), so this branch changes NO live behavior.
        """
        if not self.view_instance:
            await self.transport.send_error("View not mounted. Please reload the page.")
            return

        # Opt-in per-event auth re-check (#1777, T3, ADR-022 Iter 2 Phase 2.3a).
        # Runs at the SAME point the WS bespoke handler does — after the
        # view-mounted check, BEFORE the actor branch and the normal handler path
        # (websocket.py:3193-3222 sits before the actor block at :3282). The hook
        # is default-True (no re-check) unless ``reauth_on_event`` is set + the view
        # requires auth; on a False return the transport has ALREADY emitted the
        # redirect/error + terminated itself.
        #
        # #291 multiplexed-path care: the transport-TERMINATING side effect (close)
        # is owned by the hook and gated there (events are NOT batched today —
        # mount_batch is mount-only — but if events ever get collected, the hook
        # gates its own close on "not in batch"). The STATE change that closes the
        # security gap (clearing ``view_instance`` so no later frame on this session
        # dispatches against the deauthorized view) is applied HERE,
        # UNCONDITIONALLY, regardless of whether the close fired — mirroring the WS
        # bespoke block which sets ``self.view_instance = None`` after the close.
        recheck = getattr(self.transport, "recheck_event_auth", None)
        if recheck is not None and not await recheck(self.view_instance):
            self.view_instance = None  # unconditional state-clear (#291)
            return

        uses_actors = getattr(self.transport, "uses_actors", None)
        if (
            uses_actors is not None
            and uses_actors(self.view_instance)
            and not self._event_routes_to_sticky_child(data)
        ):
            # Actor path: runs OUTSIDE event_context (no render lock), mirroring
            # the WS bespoke block. Parse ref / cache id the same way the WS event
            # handler does (websocket.py:3168-3172) so the framed actor result
            # carries the same wire metadata.
            params: Dict[str, Any] = dict(data.get("params") or {})
            event_name = data.get("event")
            raw_ref = data.get("ref")
            event_ref: Optional[int] = int(raw_ref) if isinstance(raw_ref, (int, float)) else None
            cache_request_id = params.get("_cacheRequestId")
            await self.transport.dispatch_actor_event(
                self.view_instance,
                event_name,
                params,
                event_ref=event_ref,
                cache_request_id=cache_request_id,
            )
            return

        async with self.transport.event_context(self.view_instance):
            await self._dispatch_event_render(data)

    def _event_routes_to_sticky_child(self, data: Dict[str, Any]) -> bool:
        """Return whether the event targets a sticky-child LiveView (not the top view).

        Mirrors the WS ``is_embedded_child_target`` computation
        (websocket.py:3229-3280) WITHOUT consuming ``view_id`` from ``params`` —
        the downstream :meth:`_dispatch_sticky_child_event` (run inside
        ``event_context`` on the non-actor path) still pops it. A ``view_id`` that
        is absent, or equal to the top-level view's ``_view_id``, is NOT a child
        target (the event is for the top view). Per #1467, ``component_id`` does
        NOT route away from the top view, so it is intentionally NOT consulted
        here — matching the WS actor block, which fires for ``component_id`` events.
        """
        params = data.get("params") or {}
        view_id = params.get("view_id")
        if not view_id:
            return False
        return view_id != getattr(self.view_instance, "_view_id", None)

    async def _dispatch_event_render(self, data: Dict[str, Any]) -> None:
        """Parse, validate, run the handler, and render one event turn.

        Always invoked inside ``transport.event_context`` (see
        :meth:`_dispatch_event_inner`) so the render serialization + observability
        scope is established for the whole handler+render turn."""
        event_name = data.get("event")
        params: Dict[str, Any] = dict(data.get("params") or {})

        # Event ref echo (#560, ADR-022 Iter 2 Phase 2.0): the client sends a
        # monotonic ``ref`` with each event so it can match responses to requests
        # and distinguish event responses from out-of-band tick / broadcast /
        # async pushes. The runtime echoes it back on BOTH the noop and the
        # update frame (mirrors WS handle_event websocket.py:3119-3121 + the
        # _send_noop / _send_update ref echo at websocket.py:776-780 / 1381-1403).
        # Coerce to int to prevent type confusion (the int/float-only WS rule).
        raw_ref = data.get("ref")
        event_ref: Optional[int] = int(raw_ref) if isinstance(raw_ref, (int, float)) else None

        if not event_name:
            await self.transport.send_error("Missing 'event' field")
            return

        start_time = time.perf_counter()

        # Strip internal params (matches WS handler)
        cache_request_id = params.pop("_cacheRequestId", None)
        positional_args = params.pop("_args", [])

        # Embedded-child routing (ADR-022 Iter 2 Phase 2.1).
        #
        # Two transport-agnostic child-routing subsystems route the event AWAY
        # from the top-level view before the single-view path below runs (per
        # #1467 canon they are DISTINCT mechanisms with different wire frames):
        #
        #   1. ``view_id`` → a sticky-child LiveView (resolved via
        #      ``_get_all_child_views()``; emits a scoped ``embedded_update``).
        #   2. ``component_id`` → a LiveComponent (resolved via
        #      ``_components``; validates the handler against the COMPONENT and
        #      emits a full-HTML ``component_event`` frame; does NOT reassign
        #      the target view, per #1467).
        #
        # Both mirror the WS ``_handle_event_inner`` subsystems verbatim while
        # WS routing stays on its own bespoke path (Phase 2.1 ADDs the runtime
        # copy; Phase 2.3 deletes the WS copy). SSE has neither components nor
        # sticky children, so both checks fall straight through to the
        # single-view path — the empty-registry no-op. Each helper returns
        # ``True`` when it fully handled the event (we return), ``False`` to
        # fall through.
        if await self._dispatch_sticky_child_event(
            data, event_name, params, positional_args, event_ref
        ):
            return
        if await self._dispatch_component_event(event_name, params, positional_args, event_ref):
            return

        # v0.7.0 dj_activity gate (ADR-022 Iter 2 Phase 2.3a, #1903). Verbatim
        # transport-agnostic port of the WS ``_handle_event_inner`` gate
        # (websocket.py:3254-3273): if the event was triggered inside a HIDDEN
        # (non-eager) ``{% dj_activity %}`` region, queue it per-activity and send
        # a no-op so the client's loading state clears — the event replays when
        # the panel is next shown (drained by ``_flush_deferred_activity_events``
        # below). The view methods (``is_activity_visible`` / ``_is_activity_eager``
        # / ``_queue_deferred_activity_event``) are the SAME transport-agnostic
        # ActivityMixin API the WS path uses; only the send shape differs (the
        # runtime's self-describing noop vs the WS ``_send_noop``). SSE views with
        # no ``{% dj_activity %}`` region carry no ``_activity`` marker, so the
        # whole block is a no-op for them (parity-improving but zero-cost when
        # unused). Routes LIVE for SSE today (events run through this path since
        # Iter 1, #1887); WS stays on its bespoke gate until Phase 2.3b.
        _activity_name = params.pop("_activity", None)
        if (
            _activity_name
            and hasattr(self.view_instance, "is_activity_visible")
            and not self.view_instance.is_activity_visible(_activity_name)
            and not self.view_instance._is_activity_eager(_activity_name)
        ):
            logger.debug(
                "[djust] Runtime event %r on hidden activity %r — deferring",
                sanitize_for_log(event_name or ""),
                sanitize_for_log(_activity_name),
            )
            # Queued WITHOUT permission/rate-limit check (by design: per-handler
            # auth runs on dispatch via the lock-free re-dispatcher
            # ``_dispatch_single_event``). Per-activity cap bounds memory.
            self.view_instance._queue_deferred_activity_event(_activity_name, event_name, params)
            # No-op so the client clears its loading state. Mirrors the runtime's
            # skip-render noop shape (type/source/event_name/ref) so the client
            # can match the ack to its pending event (#560 sequencing).
            noop_msg: Dict[str, Any] = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            await self.transport.send(noop_msg)
            return

        # Time-travel record (v0.6.1 dev-only, ADR-022 Iter 2 Phase 2.2). Capture
        # ``state_before`` BEFORE the security check so a PERMISSION-DENIED or
        # VALIDATION-FAILED event STILL records a snapshot (with the error set +
        # state_after == state_before) for the debug panel — mirrors WS
        # ``_handle_event_inner`` (websocket.py:3541-3565), which did
        # ``_tt_start`` → validate → on ``handler is None`` set
        # ``_tt_error = "permission_denied"`` + ``_tt_end`` BEFORE returning.
        # #1907 THE FLIP regression fix: the first flip ran ``record_event_start``
        # AFTER the security check, so a denied handler on a time-travel view
        # recorded ZERO entries (the bespoke recorded one with
        # error="permission_denied"). ``record_event_start`` is a no-op unless the
        # view opted into time-travel, so default views pay nothing.
        from .time_travel import record_event_end, record_event_start

        _tt_snapshot = record_event_start(self.view_instance, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        # Security
        handler = await _validate_event_security(
            self.transport, event_name, self.view_instance, self._rate_limiter
        )
        if handler is None:
            # Permission denied / rate-limited / unsafe name — record the denial
            # in the time-travel buffer (with error) before returning, matching the
            # WS bespoke path (websocket.py:3550-3553).
            _tt_error = "permission_denied"
            record_event_end(self.view_instance, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(self.view_instance, _tt_snapshot)
            return

        # Parameter validation
        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler, params, event_name, coerce=coerce, positional_args=positional_args
        )
        if not validation["valid"]:
            logger.error(
                "Runtime: parameter validation failed: %s",
                sanitize_for_log(validation["error"]),
            )
            # Record the validation failure in the time-travel buffer too (the WS
            # bespoke path set ``_tt_error = "validation_failed"`` + ``_tt_end``
            # before the error send, websocket.py:3563-3565).
            _tt_error = "validation_failed"
            record_event_end(self.view_instance, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(self.view_instance, _tt_snapshot)
            await self.transport.send_error(
                validation["error"],
                validation_details={
                    "expected_params": validation["expected"],
                    "provided_params": validation["provided"],
                    "type_errors": validation["type_errors"],
                },
            )
            return

        coerced_params = validation.get("coerced_params", params)

        # Snapshot pre-handler assigns for change detection.
        from .websocket import _compute_changed_keys, _snapshot_assigns

        pre_assigns = _snapshot_assigns(self.view_instance)
        # Identity snapshot for the #700 push_commands-only auto-skip below:
        # {attr: id(value)} over the public assigns. Immune to the deep-copy
        # sentinel false-positives _snapshot_assigns can produce for non-copyable
        # public attrs (querysets, file handles), so a handler that only calls
        # push_event()/push_commands() without touching real state is detected as
        # a true no-op (mirrors WS handle_event websocket.py:3551-3556).
        _fw_attrs = getattr(self.view_instance, "_framework_attrs", frozenset())
        pre_identity = {
            k: id(v) for k, v in self.view_instance.__dict__.items() if k not in _fw_attrs
        }

        # Call handler. The time-travel record is finalized + pushed in the
        # ``finally`` for BOTH the success and the raising path (mirrors WS
        # ``_handle_event_inner`` websocket.py:3625-3635, which records in a
        # finally so permission-denied / raising handlers still appear in the
        # debug panel). On a raise we set ``_tt_error`` and return early after
        # sending the error frame.
        _handler_start = time.perf_counter()
        try:
            try:
                await _call_handler(handler, coerced_params if coerced_params else None)
            except Exception as exc:
                _tt_error = str(exc)[:200]
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=self.view_instance.__class__.__name__,
                    logger=logger,
                    log_message=f"Error in handler {self.view_instance.__class__.__name__}.{sanitize_for_log(event_name)}()",
                )
                await self.transport.send(response)
                return
        finally:
            record_event_end(self.view_instance, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(self.view_instance, _tt_snapshot)

        # Per-handler percentile telemetry (#1907, THE FLIP). The WS bespoke
        # view-path recorded ``record_handler_timing`` right after a SUCCESSFUL
        # handler call (websocket.py:3645) — a raising handler returns early above
        # before this point, matching the WS path where the record line is
        # unreachable on a raise. Folded behind ``on_handler_timing`` so it stays
        # WS-scoped (SSE no-op). Called defensively (mirroring the existing
        # ``on_view_mounted`` / ``on_event_recorded`` hook calls) so a partial test
        # transport without the hook is a no-op. Best-effort by hook contract.
        on_handler_timing = getattr(self.transport, "on_handler_timing", None)
        if on_handler_timing is not None:
            on_handler_timing(
                self.view_instance,
                event_name,
                (time.perf_counter() - _handler_start) * 1000.0,
            )

        # Waiter notification (ADR-002 Phase 1b): resolve any pending
        # wait_for_event waiters on the view whose event_name matches. Runs AFTER
        # the handler so new waiters created during this call aren't self-notified.
        # Transport-agnostic; mirrors WS handle_event websocket.py:3608-3616 (and
        # the deferred-event path at websocket.py:1478-1482). No-op when the view
        # has no pending waiters. Best-effort: a waiter-callback failure must never
        # break the event turn (matches WS posture).
        if hasattr(self.view_instance, "_notify_waiters"):
            try:
                self.view_instance._notify_waiters(event_name, coerced_params or {})
            except Exception as exc:  # noqa: BLE001 — waiter bugs must not break events
                logger.warning(
                    "Waiter notification for %r failed: %s",
                    sanitize_for_log(event_name),
                    exc,
                )

        # Persist updated LiveView state to the Django session (#1466, ADR-022
        # Iter 2 Phase 2.2). Verbatim gate from the WS save block
        # (websocket.py:3700-3702): top-level view IDENTITY + ``enable_state_snapshot``
        # opt-in. The single-view path here only runs for the top-level view
        # (``component_id`` / ``view_id`` routing already returned earlier), so
        # ``target_view`` IS ``self.view_instance``; the identity clause is kept
        # in the condition so the runtime-side #1466 source-grep pin
        # (test_runtime_state_save_tt_1894) asserts the SAME gate string the WS
        # pin asserts — drift between the two save gates goes red. Default views
        # (no opt-in) MUST NOT persist (#1552). Bounded by 150ms (#1475).
        target_view = self.view_instance
        if target_view is self.view_instance and getattr(
            self.view_instance, "enable_state_snapshot", False
        ):
            await self._persist_state_after_event(target_view, event_name)

        # Auto-detect unchanged state. Never auto-skip when the view explicitly
        # requested a full-HTML render (``_force_full_html``) — mirrors the WS
        # ``force_html`` guard on the skip path (websocket.py:3851-3852). The
        # render branch consumes + resets the flag (see _render_and_send).
        skip_render = getattr(self.view_instance, "_skip_render", False)
        force_html = getattr(self.view_instance, "_force_full_html", False)
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(self.view_instance)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                self.view_instance._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        # #700: push_commands-only handlers auto-skip the render. When push events
        # are pending and the *identity* of every public attr is unchanged, the
        # handler only emitted push commands (no state mutation) so a VDOM
        # re-render is wasted work (and can trigger morphdom recovery during
        # tours). Identity comparison sidesteps the assigns-snapshot
        # false-positives on non-copyable attrs. Mirrors WS handle_event
        # websocket.py:3867-3891. Guarded by force_html so an explicit
        # full-HTML request still renders.
        if not skip_render and not force_html:
            pending = getattr(self.view_instance, "_pending_push_events", None)
            if pending:
                post_identity = {
                    k: id(v) for k, v in self.view_instance.__dict__.items() if k not in _fw_attrs
                }
                if pre_identity == post_identity:
                    skip_render = True

        has_async = getattr(self.view_instance, "_async_pending", None) is not None

        if skip_render:
            self.view_instance._skip_render = False
            # Drain ALL queued side-effects BEFORE the noop, matching the WS
            # bespoke skip-render path (websocket.py:3941 — ``await
            # self._flush_all_pending()`` then ``_send_noop``). #1907 THE FLIP:
            # the runtime skip-render branch previously only flushed push_events,
            # so a state-unchanging handler that queued navigation (e.g.
            # ``live_redirect()``) dropped its ``navigation`` frame on the WS path
            # once events routed here (caught by
            # ``test_live_redirect_from_state_unchanging_handler_emits_navigation_frame``).
            # ``_flush_all_pending`` is the single 8-queue drain (#1646) and
            # includes ``_flush_push_events``, so this also covers the push drain.
            await self._flush_all_pending()
            # ref echo (#560) + source/event_name (#560 sequencing) on the noop
            # frame so the client can match the ack to its pending event (clear
            # _pendingEventRefs / stop the right loading state) and distinguish it
            # from out-of-band pushes. WS echoes ``ref`` on its noop
            # (_send_noop, websocket.py:776-781); ``source``/``event_name`` are a
            # runtime ADD (the WS noop omits them) for self-describing #560
            # sequencing — harmless extra fields the client tolerates.
            noop_msg: Dict[str, Any] = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            if has_async:
                noop_msg["async_pending"] = True
            await self.transport.send(noop_msg)
            # Dispatch background work UNCONDITIONALLY after the turn (matches WS
            # handle_event websocket.py:4235, NOT the legacy SSE which gated this
            # on has_async). ``has_async`` reflects only the legacy ``_async_pending``
            # single-task format (never set in current code) and drives the loading
            # UX flag — the actual dispatch must also cover the ``_async_tasks``
            # named-task format that ``start_async`` populates, so converging onto
            # the correct WS behavior here FIXES the legacy SSE drop of
            # ``start_async`` work (#1887 / #1646). No-op when no tasks are queued.
            self._dispatch_async_work(event_name)
            # dj_activity flush (Phase 2.3a, #1903): a skip-render handler can
            # still flip an activity visible via set_activity_visible(); drain its
            # queue so deferred events for that panel arrive in the same
            # round-trip. Mirrors the WS flush placement after the async dispatch
            # (websocket.py:4283-4294). Safe + no-op when no activities exist.
            await self._flush_deferred_activity_events()
            return

        # Render
        await self._render_and_send(
            event_name=event_name,
            cache_request_id=cache_request_id,
            has_async=has_async,
            force_html=force_html,
            event_ref=event_ref,
        )

        # Dispatch background work UNCONDITIONALLY after the render (WS parity,
        # websocket.py:4235): start_async / @background callbacks run off-thread
        # and stream their re-rendered result via the transport when ready.
        self._dispatch_async_work(event_name)

        # dj_activity flush (Phase 2.3a, #1903): if this handler flipped any
        # activity visible, drain its deferred-event queue now so in-flight events
        # for that panel are delivered in-order in the SAME round-trip. The flush
        # is async + awaited inline (websocket.py:4290-4294). Safe to call even
        # when no activities exist (the flush early-returns on an empty queue).
        await self._flush_deferred_activity_events()

        logger.debug(
            "Runtime: event '%s' handled in %.1fms",
            sanitize_for_log(event_name),
            (time.perf_counter() - start_time) * 1000,
        )

    # ------------------------------------------------------------------ #
    # dj_activity deferred-event drain (ADR-022 Iter 2 Phase 2.3a, #1903)
    #
    # The transport-agnostic flush + lock-free re-dispatcher that the runtime
    # uses to drain queued events when a panel flips visible. The ActivityMixin
    # flush method (mixins/activity.py:212) is consumer-blind: it takes any
    # object exposing ``_dispatch_single_event(target_view, event_name, params)``
    # and awaits it once per queued event. WS passes the consumer; the runtime
    # passes ITSELF (option (a) — no change to the view-method semantics). The
    # re-dispatcher runs INSIDE ``event_context`` (the flush is invoked from
    # ``_dispatch_event_render``, which is wrapped in ``transport.event_context``),
    # which on WS already holds the borrowed consumer ``_render_lock`` — so, like
    # the WS ``_dispatch_single_event`` (websocket.py:1467), this method MUST NOT
    # re-acquire any lock and MUST NOT re-enter ``event_context`` (that would
    # deadlock on the non-reentrant ``asyncio.Lock``). On SSE ``event_context`` is
    # a no-op, so there is no lock either way. Each queued event is re-VALIDATED
    # through the full auth stack here (a denied queued event never dispatches).
    # ------------------------------------------------------------------ #

    async def _flush_deferred_activity_events(self) -> None:
        """Drain the view's deferred-activity queues via the runtime re-dispatcher.

        Thin wrapper: hands the runtime itself to the ActivityMixin flush as the
        consumer-like dispatcher. No-op when the view has no ActivityMixin or no
        queued events. Best-effort — a drain bug must never break the event turn
        (mirrors the WS flush call-site guard, websocket.py:4290-4294)."""
        view = self.view_instance
        if view is None or not hasattr(view, "_flush_deferred_activity_events"):
            return
        try:
            await view._flush_deferred_activity_events(self)
        except Exception:  # noqa: BLE001 — never fail the event for a drain bug
            logger.exception("dj_activity: runtime deferred-event flush raised")

    async def _dispatch_single_event(
        self,
        target_view: Any,
        event_name: str,
        params: Dict[str, Any],
        event_ref: Optional[int] = None,
    ) -> None:
        """Lock-free single queued-event re-dispatch (the WS ``_dispatch_single_event`` twin).

        Re-runs validate → handler → render for ONE deferred ``(event_name, params)``
        pair WITHOUT acquiring a lock and WITHOUT re-entering ``event_context`` —
        the caller (``_flush_deferred_activity_events`` → the ActivityMixin flush)
        already runs inside ``event_context`` (which on WS holds the borrowed
        ``_render_lock``). Invariants mirror the WS path (websocket.py:1456-1626):

        * The activity gate is NOT re-run — events reach this path only because
          the flush already decided they should dispatch (re-gating would re-queue
          them forever). ``params`` arrives with ``_activity`` stripped by the
          flush.
        * Still runs the FULL security pipeline (unsafe name, missing handler,
          decorator allowlist, per-handler rate limit, permission check), so a
          denied queued event is dropped exactly as a live one would be.
        * Exceptions are logged + swallowed so one bad queued event cannot break
          the rest of the drain (the flush also catches, defense-in-depth).
        """
        from .websocket import _compute_changed_keys, _snapshot_assigns

        # --- security / validation (shared with the live path) -------------
        handler = await _validate_event_security(
            self.transport, event_name, target_view, self._rate_limiter
        )
        if handler is None:
            return

        positional_args = (params or {}).pop("_args", []) if isinstance(params, dict) else []
        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler,
            params or {},
            event_name,
            coerce=coerce,
            positional_args=positional_args,
        )
        if not validation["valid"]:
            logger.warning(
                "Runtime deferred-activity event %r failed param validation: %s",
                sanitize_for_log(event_name or ""),
                sanitize_for_log(validation["error"]),
            )
            return
        coerced_params = validation.get("coerced_params", params)

        # --- handler invocation -------------------------------------------
        pre_assigns = _snapshot_assigns(self.view_instance)
        try:
            await _call_handler(handler, coerced_params if coerced_params else None)
        except Exception:  # noqa: BLE001 — never break the flush
            logger.exception(
                "Runtime deferred-activity event %r on %s raised during dispatch",
                sanitize_for_log(event_name or ""),
                type(target_view).__name__,
            )
            return

        # Waiter notification (ADR-002) — same posture as the live path.
        if hasattr(target_view, "_notify_waiters"):
            try:
                target_view._notify_waiters(event_name, coerced_params or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Waiter notification for deferred %r failed: %s",
                    sanitize_for_log(event_name or ""),
                    exc,
                )

        # --- render + emit one frame (mirrors the live skip/render split) --
        skip_render = getattr(self.view_instance, "_skip_render", False)
        force_html = getattr(self.view_instance, "_force_full_html", False)
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(self.view_instance)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                self.view_instance._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        has_async = getattr(self.view_instance, "_async_pending", None) is not None

        if skip_render:
            self.view_instance._skip_render = False
            self._flush_push_events()
            noop_msg: Dict[str, Any] = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            if has_async:
                noop_msg["async_pending"] = True
            await self.transport.send(noop_msg)
            self._dispatch_async_work(event_name)
            return

        await self._render_and_send(
            event_name=event_name,
            has_async=has_async,
            force_html=force_html,
            event_ref=event_ref,
        )
        self._dispatch_async_work(event_name)

    # ------------------------------------------------------------------ #
    # Time-travel push hook (ADR-022 Iter 2 Phase 2.2)
    # ------------------------------------------------------------------ #

    async def _push_tt_event(self, view: Any, snapshot: Any) -> None:
        """Invoke the transport's ``on_event_recorded`` hook after a record.

        Replaces the WS ``_maybe_push_tt_event`` direct send (websocket.py:5267)
        with a transport-blind dispatch: WS emits the DEBUG-gated
        ``time_travel_event`` frame, SSE no-ops. Forward-compat: transports
        predating the hook (the ``on_view_mounted`` getattr precedent) are
        tolerated. Best-effort — a debug-frame failure must never break the
        event turn."""
        if snapshot is None:
            return
        hook = getattr(self.transport, "on_event_recorded", None)
        if hook is None:
            return
        try:
            await hook(view, snapshot)
        except Exception:  # noqa: BLE001 — dev-only time-travel push; degrade silently
            logger.exception("Runtime: time_travel on_event_recorded hook failed")

    # ------------------------------------------------------------------ #
    # Per-event state persistence (ADR-022 Iter 2 Phase 2.2)
    #
    # Ported VERBATIM from the WS ``_handle_event_inner`` save block
    # (websocket.py:3666-3804) so WS events routed through the runtime in the
    # Phase 2.3 final flip persist identically. The WS copy stays UNTOUCHED until
    # 2.3 (the #1466/#1552 grep-pins in test_ws_reconnect_state_1465.py assert the
    # exact strings there). Both gates are preserved:
    #
    #   * ``target_view is self.view_instance`` — only top-level view identity
    #     saves to ``liveview_{path}`` (#1466; child LiveComponents never get
    #     ``_djust_mount_request`` stashed, so they'd write the wrong key);
    #   * ``enable_state_snapshot`` — default views MUST NOT persist (#1552; an
    #     unconditional save leaves async session I/O in flight that a host
    #     snapshot captures unrecoverably — the djustlive 0.9.7rc2 production
    #     block).
    #
    # The save body is bounded by a 150ms ``asyncio.wait_for`` (#1475) so even
    # opt-in views can't extend close-time tail latency under backend
    # backpressure. Saves must never break event handling — timeout + exception
    # are both caught and logged.
    # ------------------------------------------------------------------ #

    async def _persist_state_after_event(self, target_view: Any, event_name: Optional[str]) -> None:
        """Persist the top-level view's post-event state to the Django session.

        Caller MUST have already verified the gate
        (``target_view is self.view_instance and enable_state_snapshot``); this
        method assumes it runs only for an opted-in top-level view. Bounded by a
        150ms timeout, mirroring the WS save block (websocket.py:3704-3804)."""

        async def _save() -> None:
            # Discover the session the same way the WS save block does
            # (websocket.py:3710-3720): prefer the stashed mount request's
            # session (carries the save-key namespace + path); fall back to the
            # ASGI scope's session when no mount request was stashed.
            mount_request = getattr(target_view, "_djust_mount_request", None)
            scope_session = (
                (self.scope.get("session") if self.scope else None)
                if mount_request is None
                else None
            )
            save_session = (
                getattr(mount_request, "session", None)
                if mount_request is not None
                else scope_session
            )
            if save_session is None:
                return

            from .components.base import LiveComponent as _LC
            from .serialization import normalize_django_value as _normalize

            save_path = mount_request.path if mount_request is not None else "/"
            save_view_key = f"liveview_{save_path}"

            # Save order mirrors HTTP path (mixins/request.py:593-609): private
            # attrs FIRST, then public via get_context_data().
            if hasattr(target_view, "_get_private_state"):
                _priv = await sync_to_async(target_view._get_private_state)()
                if _priv:
                    await save_session.aset(f"{save_view_key}__private", _normalize(_priv))
                else:
                    try:
                        await save_session.apop(f"{save_view_key}__private", None)
                    except AttributeError:
                        await sync_to_async(save_session.pop)(f"{save_view_key}__private", None)

            _gcd_save = target_view.get_context_data
            if inspect.iscoroutinefunction(_gcd_save):
                save_context = await _gcd_save()
            else:
                save_context = await sync_to_async(_gcd_save)()

            save_state = {k: v for k, v in save_context.items() if not isinstance(v, _LC)}
            await save_session.aset(save_view_key, _normalize(save_state))

            # Components — sync helper, wrap with sync_to_async.
            if mount_request is not None and hasattr(target_view, "_save_components_to_session"):
                await sync_to_async(target_view._save_components_to_session)(
                    mount_request, save_context
                )

            await save_session.asave()

        try:
            await asyncio.wait_for(_save(), timeout=0.150)
        except asyncio.TimeoutError:
            logger.warning(
                "Runtime event state save exceeded 150ms for %r — session backend "
                "backpressure; skipping this event's save. Subsequent events will retry.",
                sanitize_for_log(event_name or ""),
            )
        except Exception:  # noqa: BLE001 — saves must never break event handling
            logger.exception(
                "Failed to save LiveView state after runtime event %r",
                sanitize_for_log(event_name or ""),
            )

    async def _persist_sticky_child_after_event(
        self, target_view: Any, event_name: Optional[str]
    ) -> None:
        """Persist a sticky CHILD view's post-event state (ADR-018 Branch B).

        Ported from the WS sticky-child save (websocket.py:3806-3888). Kept as a
        SEPARATE method from :meth:`_persist_state_after_event` — exactly as WS
        keeps it a separate ``if`` — so the child saves under its stable sticky
        key (Decision 1) gated on the both-opt-in predicate
        (:func:`sticky_child_should_persist`, Decision 5). Bounded by the same
        150ms timeout. Caller MUST have verified the gate."""

        async def _save() -> None:
            from .mixins.sticky import save_sticky_child_state, write_sticky_index_and_prune

            parent = self.view_instance
            mount_request = getattr(parent, "_djust_mount_request", None)
            # Precedence mirrors WS (websocket.py:3845-3847): the mount request's
            # session is authoritative; the scope session is the fallback.
            save_session = getattr(mount_request, "session", None) or (
                self.scope.get("session") if self.scope else None
            )
            if save_session is None:
                return

            parent_path = mount_request.path if mount_request is not None else "/"

            await save_sticky_child_state(target_view, save_session, parent_path)
            await write_sticky_index_and_prune(parent, save_session, parent_path)
            await save_session.asave()

        try:
            await asyncio.wait_for(_save(), timeout=0.150)
        except asyncio.TimeoutError:
            logger.warning(
                "Runtime event sticky-child state save exceeded 150ms for %r — "
                "session backend backpressure; skipping this event's save. "
                "Subsequent events will retry.",
                sanitize_for_log(event_name or ""),
            )
        except Exception:  # noqa: BLE001 — saves must never break event handling
            logger.exception(
                "Failed to save sticky-child state after runtime event %r",
                sanitize_for_log(event_name or ""),
            )

    # ------------------------------------------------------------------ #
    # Embedded-child routing (ADR-022 Iter 2 Phase 2.1)
    #
    # Two transport-agnostic child-routing subsystems, ported from the WS
    # ``_handle_event_inner`` (websocket.py) verbatim for the security-critical
    # bits. WS keeps its own copy until Phase 2.3; these are the runtime ADDs.
    # ------------------------------------------------------------------ #

    async def _dispatch_sticky_child_event(
        self,
        data: Dict[str, Any],
        event_name: str,
        params: Dict[str, Any],
        positional_args: List[Any],
        event_ref: Optional[int],
    ) -> bool:
        """Route a ``view_id``-targeted event to a sticky/embedded child view.

        Mirrors the WS ``_handle_event_inner`` sticky-child block
        (websocket.py:3176-3198 + the embedded framing at ~4010-4019):

        * resolve the child via ``view_instance._get_all_child_views()``,
        * validate the handler against the CHILD (``target_view``), call it,
        * notify the child's waiters (ADR-002), render the child's template via
          the single-sourced :func:`~djust.websocket.render_embedded_child_html`,
        * emit a scoped ``embedded_update {view_id, html, event_name}`` frame.

        Security: the client-supplied ``view_id`` is NEVER echoed into the
        user-facing error string — only logged sanitized via
        ``sanitize_for_log(view_id)`` in the structured ``extra`` (verbatim from
        the WS path, websocket.py:3191-3197).

        Returns ``True`` if the event was routed to a child (caller returns),
        ``False`` to fall through to the single-view path (the SSE no-op case:
        no ``view_id``, or no child registry, or the id is the top-level view).
        """
        view = self.view_instance
        view_id = params.pop("view_id", None)
        if not view_id or view_id == getattr(view, "_view_id", None):
            # No child routing requested (or the id IS the top-level view):
            # fall through to the single-view path.
            return False

        all_children = view._get_all_child_views() if hasattr(view, "_get_all_child_views") else {}
        target_view = all_children.get(view_id)
        if target_view is None:
            # Security: don't echo a client-supplied view_id into the
            # user-facing error string. The id is already logged via the
            # structured event for callers that need to trace it.
            await self.transport.send_error(
                "Embedded view not found",
                extra={"view_id": sanitize_for_log(view_id)},
            )
            return True

        # Validate the handler against the CHILD (not the parent) — mirrors WS
        # using ``target_view`` for handler lookup (websocket.py:3498).
        handler = await _validate_event_security(
            self.transport, event_name, target_view, self._rate_limiter
        )
        if handler is None:
            return True

        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler, params, event_name, coerce=coerce, positional_args=positional_args
        )
        if not validation["valid"]:
            logger.error(
                "Runtime: parameter validation failed (embedded child): %s",
                sanitize_for_log(validation["error"]),
            )
            await self.transport.send_error(
                validation["error"],
                validation_details={
                    "expected_params": validation["expected"],
                    "provided_params": validation["provided"],
                    "type_errors": validation["type_errors"],
                },
            )
            return True

        coerced_params = validation.get("coerced_params", params)

        # Time-travel record (ADR-022 Iter 2 Phase 2.2). For a sticky-child event
        # the snapshot records against the CHILD (``target_view``) — the child is
        # a full LiveView with its own ``_time_travel_buffer`` (#1467). Mirrors
        # the WS path's per-target ``_tt_start(target_view, ...)`` /
        # ``_tt_end(target_view, ...)`` (websocket.py:3541/3634). No-op unless the
        # child opted into time-travel.
        from .time_travel import record_event_end, record_event_start

        _tt_snapshot = record_event_start(target_view, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        try:
            try:
                await _call_handler(handler, coerced_params if coerced_params else None)
            except Exception as exc:
                _tt_error = str(exc)[:200]
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=target_view.__class__.__name__,
                    logger=logger,
                    log_message=(
                        f"Error in embedded child {target_view.__class__.__name__}."
                        f"{sanitize_for_log(event_name)}()"
                    ),
                )
                await self.transport.send(response)
                return True
        finally:
            record_event_end(target_view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(target_view, _tt_snapshot)

        # Waiter notification (ADR-002 Phase 1b) — resolve waiters on the CHILD
        # view. Best-effort: a waiter bug must never break the event.
        if hasattr(target_view, "_notify_waiters"):
            try:
                target_view._notify_waiters(event_name, coerced_params or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Waiter notification for embedded child %r failed: %s",
                    sanitize_for_log(event_name),
                    exc,
                )

        # Sticky-child state save (ADR-018 Branch B, ADR-022 Iter 2 Phase 2.2).
        # Verbatim from the WS sticky save (websocket.py:3806-3888): persist the
        # CHILD under its stable sticky key gated on the both-opt-in predicate
        # (Decision 5). The else-branch fires the one-shot opt-in-mismatch warning
        # so the silent persistence gap (child opted in, parent did not) is
        # observable. ``target_view`` is the child here (it is never the top-level
        # view — that case fell through to the single-view path), so the
        # WS ``target_view is not self.view_instance`` guard is structurally
        # satisfied; the predicate carries the opt-in gate.
        from .mixins.sticky import sticky_child_should_persist, warn_sticky_child_optin_skip

        if sticky_child_should_persist(target_view, self.view_instance):
            await self._persist_sticky_child_after_event(target_view, event_name)
        else:
            warn_sticky_child_optin_skip(target_view, self.view_instance)

        # Render just the child's subtree (single-sourced render core) and emit
        # the scoped full-HTML ``embedded_update`` frame. Mirrors the WS
        # ``_render_embedded_child`` + ``_emit_full_html_update("embedded_child")``
        # + the ``embedded_update`` send (websocket.py:3905-3920 / 4010-4018).
        from .websocket import _emit_full_html_update, render_embedded_child_html

        html = await sync_to_async(render_embedded_child_html)(target_view)
        _emit_full_html_update(target_view, "embedded_child", event_name, html, 0)

        msg: Dict[str, Any] = {
            "type": "embedded_update",
            "view_id": view_id,
            "html": html,
            "event_name": event_name,
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        await self.transport.send(msg)
        await self._flush_all_pending()

        # Dispatch any background work the child handler scheduled (WS parity).
        self._dispatch_async_work(event_name)
        return True

    async def _dispatch_component_event(
        self,
        event_name: str,
        params: Dict[str, Any],
        positional_args: List[Any],
        event_ref: Optional[int],
    ) -> bool:
        """Route a ``component_id``-targeted event to a child LiveComponent.

        Mirrors the WS ``_handle_event_inner`` component block
        (websocket.py:3336/3354-3479 + the ``component_event`` framing at
        ~4024-4032). Per #1467 canon, ``component_id`` routing does NOT reassign
        ``target_view`` — the handler runs on the COMPONENT but waiters and the
        emitted frame are scoped to the PARENT view (LiveComponents have no
        separate VDOM/waiter buffer in Phase 1).

        Key invariants carried verbatim from WS:

        * the handler is validated against the COMPONENT, not the parent;
        * ``_notify_waiters`` is called on the PARENT view with ``component_id``
          injected into the kwargs so predicates can disambiguate which
          component fired (websocket.py:3468-3469);
        * the parent re-renders to full HTML (``component_event`` frame) because
          component VDOM is separate from the parent's.

        Returns ``True`` if a ``component_id`` was present (handled here),
        ``False`` to fall through (the SSE no-op case + every plain event).
        """
        view = self.view_instance
        component_id = params.get("component_id")
        if not component_id:
            return False

        component = view._components.get(component_id) if hasattr(view, "_components") else None
        if not component:
            # Verbatim WS shape (websocket.py:3358-3362): the component_id is
            # server-assigned (not free-form client text), so echoing it in the
            # error is the existing behavior.
            error_msg = f"Component not found: {component_id}"
            logger.error("Runtime: %s", sanitize_for_log(error_msg))
            await self.transport.send_error(error_msg)
            return True

        # Validate the handler against the COMPONENT (websocket.py:3380-3382).
        handler = await _validate_event_security(
            self.transport, event_name, component, self._rate_limiter
        )
        if handler is None:
            return True

        # Strip component_id from the params passed to the handler (the handler
        # signature never expects it). Mirrors websocket.py:3388-3389.
        event_data = dict(params)
        event_data.pop("component_id", None)

        coerce = get_handler_coerce_setting(handler)
        validation = validate_handler_params(
            handler, event_data, event_name, coerce=coerce, positional_args=positional_args
        )
        if not validation["valid"]:
            logger.error(
                "Runtime: parameter validation failed (component): %s",
                sanitize_for_log(validation["error"]),
            )
            await self.transport.send_error(
                validation["error"],
                validation_details={
                    "expected_params": validation["expected"],
                    "provided_params": validation["provided"],
                    "type_errors": validation["type_errors"],
                },
            )
            return True

        coerced_event_data = validation.get("coerced_params", event_data)

        # Time-travel record (ADR-022 Iter 2 Phase 2.2). Per #1467 canon a
        # LiveComponent has NO separate time-travel buffer in Phase 1, so the
        # snapshot records against the PARENT ``view`` (not the component) —
        # state the component pushes into the parent via ``send_parent`` shows up
        # in state_before/state_after. Mirrors WS ``_tt_start_c(self.view_instance,
        # ...)`` / ``_tt_end_c(self.view_instance, ...)`` (websocket.py:3424/3489).
        # Recorded against the pre-strip ``params`` to match WS. No-op unless the
        # parent opted into time-travel.
        from .time_travel import record_event_end, record_event_start

        _tt_snapshot = record_event_start(view, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        try:
            try:
                await _call_handler(handler, coerced_event_data if coerced_event_data else None)
            except Exception as exc:
                _tt_error = str(exc)[:200]
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=view.__class__.__name__,
                    logger=logger,
                    log_message=(
                        f"Error in {view.__class__.__name__}."
                        f"{sanitize_for_log(event_name)}() (component event)"
                    ),
                )
                await self.transport.send(response)
                return True
        finally:
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)

        # Propagate the component event to the PARENT view's waiters with the
        # component_id injected (ADR-002 Phase 1b/1c, websocket.py:3456-3479).
        notify_kwargs = dict(coerced_event_data or {})
        notify_kwargs.setdefault("component_id", component_id)
        if hasattr(view, "_notify_waiters"):
            try:
                view._notify_waiters(event_name, notify_kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Waiter notification for component event %r on %s failed: %s",
                    sanitize_for_log(event_name),
                    sanitize_for_log(str(component_id)),
                    exc,
                )

        # Component VDOM is separate from the parent's, so re-render the parent
        # to full HTML and emit a ``component_event`` frame (websocket.py:4024-4032).
        from .websocket import _emit_full_html_update

        html, _patches, version = await sync_to_async(view.render_with_diff)()
        if html and hasattr(view, "_strip_comments_and_whitespace"):
            html = view._strip_comments_and_whitespace(html)
        if html and hasattr(view, "_extract_liveview_content"):
            html = view._extract_liveview_content(html)
        _emit_full_html_update(view, "component_event", event_name, html, version)

        wire_version = self.transport.next_client_version(html, version)
        msg: Dict[str, Any] = {
            "type": "html_update",
            "html": html,
            "version": wire_version,
            "event_name": event_name,
            "source": "event",
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        await self.transport.send(msg)
        await self._flush_all_pending()

        # Dispatch any background work the component handler scheduled (WS parity).
        self._dispatch_async_work(event_name)
        return True

    # ------------------------------------------------------------------ #
    # URL-change dispatch (shared between WS and SSE in this PR)
    # ------------------------------------------------------------------ #

    async def dispatch_url_change(self, data: Dict[str, Any]) -> None:
        """Handle a URL change frame (popstate or dj-patch click).

        Calls ``handle_params(params, uri)`` then re-renders + sends patches.
        Mirrors the legacy ``LiveViewConsumer.handle_url_change`` (now a
        thin shim over this method).

        Wrapped in the tenant context (Finding #6) so handle_params + the
        object-permission re-check + render see the correct tenant.
        """
        tenant = getattr(self.view_instance, "_tenant", None) if self.view_instance else None
        with _tenant_context(tenant):
            await self._dispatch_url_change_inner(data)

    async def _dispatch_url_change_inner(self, data: Dict[str, Any]) -> None:
        """URL-change body (see :meth:`dispatch_url_change` for the tenant wrapper)."""
        if not self.view_instance:
            await self.transport.send_error("View not mounted")
            return

        params = data.get("params", {})
        uri = data.get("uri", "")

        try:
            await sync_to_async(self.view_instance.handle_params)(params, uri)

            # Object-permission re-check (ADR-017) after the client-supplied URL
            # params may have changed the access-determining state. Without this,
            # url_change navigates to a denied object and re-renders it
            # (finding #10). No-op for views that don't override get_object.
            from django.core.exceptions import PermissionDenied

            from .auth.core import enforce_object_permission

            try:
                await sync_to_async(enforce_object_permission)(
                    self.view_instance, getattr(self.view_instance, "request", None)
                )
            except PermissionDenied:
                await self.transport.send_error(
                    "Access denied for this object.", code="permission_denied"
                )
                return

            if hasattr(self.view_instance, "_sync_state_to_rust"):
                await sync_to_async(self.view_instance._sync_state_to_rust)()

            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

            # Allow views to force full HTML by setting _force_full_html = True
            if getattr(self.view_instance, "_force_full_html", False):
                self.view_instance._force_full_html = False
                patches = None

            # Stamp the transport's client-checked wire version (#1858, the #1788
            # parallel-path twin). On WS this is the consumer-owned monotonic counter
            # + recovery arming (so the url_change frame stays in sequence with the
            # mount baseline and a later request_html serves the matching version);
            # on SSE this returns the Rust ``version`` unchanged. ``html`` is the RAW
            # pre-strip render — pass it BEFORE strip/extract so WS arms recovery with
            # the full pre-strip HTML (see LiveViewConsumer._next_version_armed).
            wire_version = self.transport.next_client_version(html, version)

            if patches is not None:
                if isinstance(patches, str):
                    patches = fast_json_loads(patches)
                msg: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patches,
                    "version": wire_version,
                    "event_name": "url_change",
                }
                await self.transport.send(msg)
            else:
                if hasattr(self.view_instance, "_strip_comments_and_whitespace"):
                    html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(
                        html
                    )
                if hasattr(self.view_instance, "_extract_liveview_content"):
                    html = await sync_to_async(self.view_instance._extract_liveview_content)(html)
                msg = {
                    "type": "html_update",
                    "html": html,
                    "version": wire_version,
                    "event_name": "url_change",
                }
                await self.transport.send(msg)

            # Full flush-queue parity with WS (#1885 / #1646): the url_change
            # path is the runtime's one production user, so flash / page_metadata
            # / layout / a11y / i18n queued during handle_params() were being
            # silently dropped before this. Drain ALL 8 queues in canonical order.
            await self._flush_all_pending()
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="event",
                event_name="url_change",
                logger=logger,
                log_message="Error in handle_params()",
            )
            await self.transport.send(response)

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def _instantiate_view(self, view_path: str) -> Optional[Any]:
        """Import + instantiate the LiveView class. Pushes error frames on
        failure and returns ``None``. Override-friendly for tests.
        """

        # Security (F22 — unsafe reflection / arbitrary module import): resolve
        # through the single shared resolver (shape-check → allowlist-before-
        # import → import_module + vars() PEP 562-safe lookup → LiveView subclass
        # check). Fail-closed; defense in depth even if a caller forgot to gate.
        # Shared with the WebSocket/SSE paths (#1646). See djust.security.mount.
        from .security.mount import resolve_view_class

        resolution = resolve_view_class(view_path)
        if not resolution:
            logger.error(
                "Failed to load view %s: %s", sanitize_for_log(view_path), resolution.detail
            )
            asyncio.ensure_future(
                self.transport.send(
                    {
                        "type": "error",
                        "error": _safe_error(resolution.detail, resolution.generic),
                    }
                )
            )
            return None
        view_class = resolution.view_class

        try:
            return view_class()
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Failed to instantiate {view_path}",
            )
            asyncio.ensure_future(self.transport.send(response))
            return None

    async def _build_request(self, *, page_url: str, params: Dict[str, Any]):
        """Build a Django request representing the mount target page.

        If the transport carries a REAL request (SSE — the HTTP request that
        established the stream, with its authenticated ``request.user`` /
        session / cookies), use it directly: auth + object-perm read off it, so
        synthesizing a userless ``RequestFactory`` request would deny every
        authenticated SSE view (#1887, ADR-022 Iter 1). Otherwise (WS) we
        synthesize one via RequestFactory, propagating the authenticated user +
        session + validated host from ``self.scope``.
        """
        # getattr-guarded so duck-typed test transport fakes that predate this
        # Protocol method fall through to the synthesized request (the WS shape).
        build_request = getattr(self.transport, "build_request", None)
        transport_request = build_request() if build_request is not None else None
        if transport_request is not None:
            return transport_request

        from django.test import RequestFactory
        from urllib.parse import urlencode

        from .security.mount import validate_mount_url

        # F23 (#1819 / #1646): validate defensively here too, so the request is
        # never built from a traversed URL even if a future caller reaches
        # _build_request without going through dispatch_mount's validation.
        page_url = validate_mount_url(page_url)
        factory = RequestFactory()
        query_string = urlencode(params) if params else ""
        path_with_query = f"{page_url}?{query_string}" if query_string else page_url

        # Finding #26: propagate the validated client Host (and TLS scheme) into
        # the reconstructed request so host/subdomain TenantResolvers resolve the
        # SAME tenant the WS mount and the HTTP (SSR) path resolve. Without
        # HTTP_HOST the request defaults to RequestFactory's "testserver",
        # misresolving the tenant to None on runtime-rebuilt requests (url_change
        # etc.). Sourced from ``self.scope`` (set for WS-backed runtimes), routed
        # through the SAME shared helper the WS handle_mount path uses
        # (``websocket.validated_host_from_scope``) so the two reconstructed-
        # request paths cannot drift (#1646). SSE-backed runtimes have
        # ``scope=None`` and use the real HTTP request, so they are unaffected.
        from .websocket import validated_host_from_scope

        host, is_secure = validated_host_from_scope(self.scope)
        request_extra = {}
        if host:
            request_extra["HTTP_HOST"] = host
        if is_secure:
            request_extra["secure"] = True
            request_extra["HTTP_X_FORWARDED_PROTO"] = "https"
        request = factory.get(path_with_query, **request_extra)

        # Wire session if available from WS scope
        try:
            from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415

            session_key = None
            if self.scope:
                scope_session = self.scope.get("session")
                session_key = getattr(scope_session, "session_key", None) if scope_session else None
            if session_key:
                request.session = SessionStore(session_key=session_key)
            else:
                request.session = SessionStore()
        except Exception:
            # Session backend not configured — leave request.session unset.
            pass

        if self.scope and "user" in self.scope:
            request.user = self.scope["user"]

        return request

    async def _check_auth(self, request) -> Optional[bool]:
        """Run the shared pre-mount security sequence. Returns:
        - ``None`` if mount may proceed.
        - Truthy value if blocked (and a navigate/error/mount-error frame was sent).

        Routes through ``djust.auth.core.run_pre_mount_auth`` so the auth call,
        the ``_ensure_tenant`` resolve, the tenant ContextVar bind, and the
        "skip tenant on auth denial" rule are single-sourced with the WS + SSE
        mount paths (#1646 / #1853) — a future edit cannot reorder the steps or
        drop one on this path. The runtime-specific verdict→frame mapping stays
        here:

        * auth ``PermissionDenied`` (raised inside the helper) →
          ``{"type": "error", ...}`` (then abort) — matches the legacy auth
          inner-try.
        * auth redirect URL (returned by the helper) → ``{"type": "navigate", ...}``
          (then abort) — matches the legacy redirect branch.
        * any OTHER exception (a tenant-resolution failure such as ``Http404``
          from ``_ensure_tenant``, or a buggy custom ``check_permissions`` hook
          raising a non-``PermissionDenied``) → the ``handle_exception``
          mount-error envelope (then abort, fail-closed). This consolidates the
          legacy split — the dedicated tenant try/except that used to live in
          ``dispatch_mount`` AND the auth ``except Exception`` — into one
          fail-closed envelope, matching the WebSocket mount path which already
          aborts on any non-auth-verdict exception during this sequence.
        """
        from .auth import run_pre_mount_auth
        from django.core.exceptions import PermissionDenied

        try:
            redirect_url = await sync_to_async(run_pre_mount_auth)(self.view_instance, request)
        except PermissionDenied:
            await self.transport.send({"type": "error", "error": "Permission denied"})
            return True
        except Exception as exc:  # noqa: BLE001 — fail-closed (tenant / auth-hook error)
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=self.view_instance.__class__.__name__,
                logger=logger,
                log_message="Error in pre-mount security sequence for %s"
                % sanitize_for_log(self.view_instance.__class__.__name__),
            )
            await self.transport.send(response)
            return True

        if redirect_url:
            await self.transport.send({"type": "navigate", "to": redirect_url})
            return True

        return None

    def _resolve_url_kwargs(self, page_url: str) -> Dict[str, Any]:
        """Resolve URL-pattern kwargs (e.g. ``pk``, ``slug``) from
        ``page_url``. Returns ``{}`` for unresolvable paths so callers can
        unconditionally ``mount_kwargs.update(...)``.
        """
        try:
            from django.urls import resolve

            match = resolve(page_url)
            return dict(match.kwargs) if match.kwargs else {}
        except Exception:
            return {}

    def _extract_cache_config(self, view_instance) -> Optional[Dict[str, Any]]:
        """Mirror of ``LiveViewConsumer._extract_cache_config``."""
        try:
            cache_config: Dict[str, Any] = {}
            for attr_name in dir(type(view_instance)):
                if attr_name.startswith("_"):
                    continue
                method = getattr(view_instance, attr_name, None)
                if method and hasattr(method, "_djust_decorators"):
                    cache_info = method._djust_decorators.get("cache")
                    if cache_info:
                        cache_config[attr_name] = cache_info
            return cache_config or None
        except Exception:
            return None

    def _extract_optimistic_rules(self, view_instance) -> Dict[str, Any]:
        """Extract optimistic UI rules from descriptor components (DEP-002).

        Mirror of ``LiveViewConsumer._extract_optimistic_rules``
        (websocket.py:3385) ported to the runtime for ADR-022 Iter 3 Phase 3.0
        so the converged mount frame carries the same optimistic-UI rules. Only
        components with ``Meta.tier == "optimistic"`` contribute a rule; the
        first event wins (matches WS's ``event not in rules`` guard).
        """
        if not view_instance:
            return {}

        descriptors = getattr(type(view_instance), "_component_descriptors", None)
        if not descriptors:
            return {}

        rules: Dict[str, Any] = {}
        for _name, descriptor in descriptors.items():
            meta = getattr(type(descriptor), "Meta", None)
            if meta is None:
                continue
            tier = getattr(meta, "tier", "server")
            if tier != "optimistic":
                continue
            event = getattr(meta, "event", None)
            rule = getattr(meta, "optimistic_rule", None)
            if event and rule and event not in rules:
                rules[event] = rule
        return rules

    async def _render_and_send(
        self,
        *,
        event_name: str,
        cache_request_id: Optional[str] = None,
        has_async: bool = False,
        force_html: bool = False,
        event_ref: Optional[int] = None,
    ) -> None:
        """Re-render after an event handler and emit the appropriate frame.

        Decides between ``patch`` (VDOM diff available) and ``html_update``
        (no diff or compression fallback). Mirrors the legacy
        ``_sse_handle_event`` render branch.

        ``force_html`` (the view's ``_force_full_html`` flag, read by the
        caller): when True, discard the VDOM patches and send a full
        ``html_update`` instead — mirrors WS handle_event
        (websocket.py:4039-4040). The flag is consumed (reset to False) here so
        a single ``self._force_full_html = True`` in a handler forces exactly
        one full render.

        ``event_ref`` (#560): echoed back on every emitted frame so the client
        can match the response to its pending event request.
        """
        # Consume the explicit full-HTML request (#560/#700 sibling): reset the
        # flag so it forces exactly one render, mirroring WS
        # (websocket.py:4039-4040 / _dispatch_single_event websocket.py:1489).
        if force_html and getattr(self.view_instance, "_force_full_html", False):
            self.view_instance._force_full_html = False
        try:
            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()
        except Exception as exc:
            response = handle_exception(
                exc,
                error_type="render",
                view_class=self.view_instance.__class__.__name__,
                logger=logger,
                log_message="Runtime: render error",
            )
            await self.transport.send(response)
            return

        should_reset_form = getattr(self.view_instance, "_should_reset_form", False)
        if should_reset_form:
            self.view_instance._should_reset_form = False

        # Honor the explicit full-HTML request: discard the VDOM patches so the
        # render falls into the no-diff ``html_update`` branch below (mirrors WS
        # handle_event websocket.py:4039-4040, which sets ``patches = None`` when
        # ``_force_full_html`` is set). The flag itself was already consumed at
        # the top of this method.
        if force_html:
            patches = None

        # Stamp the transport's client-checked wire version (#1858, the #1788
        # parallel-path twin) from the RAW pre-strip render. WS → consumer-owned
        # counter + recovery arming; SSE → Rust ``version`` unchanged. Computed once
        # here so every render branch below (patch / compression-fallback html_update /
        # no-diff html_update) stamps the same wire version. (Reached by SSE today and
        # by any future WS migration of dispatch_event off the bespoke consumer path.)
        wire_version = self.transport.next_client_version(html, version)

        if patches is not None:
            patch_list: Optional[List] = (
                fast_json_loads(patches) if isinstance(patches, str) else patches
            )

            # Patch compression (mirror sse.py legacy)
            PATCH_THRESHOLD = 100
            _compressed_patch_count: Optional[int] = None
            if patch_list and len(patch_list) > PATCH_THRESHOLD:
                patches_size = len(patches.encode("utf-8")) if isinstance(patches, str) else 0
                html_size = len(html.encode("utf-8")) if html else 0
                if patches_size and html_size < patches_size * 0.7:
                    if hasattr(self.view_instance, "_rust_view") and self.view_instance._rust_view:
                        self.view_instance._rust_view.reset()
                    _compressed_patch_count = len(patch_list)
                    patch_list = None

            if patch_list is not None:
                msg: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patch_list,
                    "version": wire_version,
                    "event_name": event_name,
                    "source": "event",
                }
                if cache_request_id:
                    msg["cache_request_id"] = cache_request_id
                if should_reset_form:
                    msg["reset_form"] = True
                if has_async:
                    msg["async_pending"] = True
                if event_ref is not None:
                    msg["ref"] = event_ref
                await self.transport.send(msg)
            else:
                # Compression fallback — send full HTML.
                html_stripped = self.view_instance._strip_comments_and_whitespace(html)
                html_content = self.view_instance._extract_liveview_content(html_stripped)
                # Observability fold (#1907): the WS bespoke path emitted a
                # ``patch_compression`` ``_emit_full_html_update`` signal here
                # (websocket.py:4129). DJE-053 does NOT fire on this branch (it had
                # patches; compression chose HTML), matching the WS bespoke gate.
                # Called defensively (partial test transports are a no-op).
                _on_render = getattr(self.transport, "on_render_emitted", None)
                if _on_render is not None:
                    _on_render(
                        self.view_instance,
                        reason="patch_compression",
                        version=version,
                        event_name=event_name,
                        html=html,
                        patch_count=_compressed_patch_count,
                    )
                msg = {
                    "type": "html_update",
                    "html": html_content,
                    "version": wire_version,
                    "event_name": event_name,
                    "source": "event",
                }
                if cache_request_id:
                    msg["cache_request_id"] = cache_request_id
                if should_reset_form:
                    msg["reset_form"] = True
                if has_async:
                    msg["async_pending"] = True
                if event_ref is not None:
                    msg["ref"] = event_ref
                await self.transport.send(msg)
        else:
            # No VDOM diff available — send HTML directly.
            if html and hasattr(self.view_instance, "_strip_comments_and_whitespace"):
                html = self.view_instance._strip_comments_and_whitespace(html)
            if html and hasattr(self.view_instance, "_extract_liveview_content"):
                html = self.view_instance._extract_liveview_content(html)
            # Observability fold (#1907): the WS bespoke path emitted the DJE-053
            # warning + an ``_emit_full_html_update`` signal on this no-patch branch
            # (websocket.py:4215-4269 / 4091 for force). ``force_full_html`` is the
            # reason when the handler explicitly forced HTML (``patches`` was nulled
            # by the ``force_html`` guard above); otherwise it is ``first_render``
            # (version <= 1, a benign first render → DEBUG log) or ``no_patches``
            # (version > 1, a real VDOM diff failure → the DJE-053 WARNING). The hook
            # keys DJE-053 on ``reason == "no_patches" and version > 1``.
            _reason = (
                "force_full_html"
                if force_html
                else ("first_render" if version <= 1 else "no_patches")
            )
            _on_render = getattr(self.transport, "on_render_emitted", None)
            if _on_render is not None:
                _on_render(
                    self.view_instance,
                    reason=_reason,
                    version=version,
                    event_name=event_name,
                    html=html,
                )
            msg = {
                "type": "html_update",
                "html": html,
                "version": wire_version,
                "event_name": event_name,
                "source": "event",
            }
            if cache_request_id:
                msg["cache_request_id"] = cache_request_id
            if should_reset_form:
                msg["reset_form"] = True
            if has_async:
                msg["async_pending"] = True
            if event_ref is not None:
                msg["ref"] = event_ref
            await self.transport.send(msg)

        # Full flush-queue parity with WS (#1885 / #1646): drain ALL 8 queues
        # in canonical order, not just push_events/navigation/deferred.
        await self._flush_all_pending()

    def _flush_push_events(self) -> None:
        """Drain push_events and send via the transport (sync-safe — pushes
        get queued but the actual send is fire-and-forget to keep the
        flush points cheap)."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_push_events"):
            return
        for event_name, payload in view._drain_push_events():
            asyncio.ensure_future(
                self.transport.send({"type": "push_event", "event": event_name, "payload": payload})
            )

    async def _flush_navigation(self) -> None:
        """Drain navigation commands and AWAIT the send via the transport.

        Awaited (not fire-and-forget) to match the WS bespoke turn-end drain
        (websocket.py ``_flush_navigation`` is ``async`` + awaited at
        ``_flush_all_pending``). Before #1907 THE FLIP, WS events ran on the
        bespoke path which awaited this flush WITHIN the event turn, so a
        ``live_redirect()`` from a handler emitted its ``navigation`` frame before
        ``handle_event`` returned. The runtime previously fired-and-forgot the send
        (``asyncio.ensure_future``) — fine for SSE's async stream, but once WS
        events route here that dropped the navigation frame from the event turn's
        observable output (a real regression caught by
        ``test_state_changing_handler_with_live_redirect_still_navigates``).
        Awaiting restores WS parity (#1646: converge on the WS turn-end semantics)."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_navigation"):
            return
        for cmd in view._drain_navigation():
            action = cmd.get("type")
            payload = {k: v for k, v in cmd.items() if k != "type"}
            await self.transport.send({"type": "navigation", "action": action, **payload})

    async def _flush_deferred(self) -> None:
        """Run ``self.defer(...)`` callbacks. Errors are logged and
        suppressed so deferred-callback bugs cannot break the wire.
        """
        view = self.view_instance
        if not view or not hasattr(view, "_drain_deferred"):
            return
        callbacks = view._drain_deferred()
        if not isinstance(callbacks, list) or not callbacks:
            return
        for callback, args, kwargs in callbacks:
            try:
                result = callback(*args, **kwargs)
                if inspect.iscoroutine(result):
                    await result
            except Exception:
                logger.warning(
                    "[djust runtime] Deferred callback %s on %s raised; continuing",
                    getattr(callback, "__qualname__", repr(callback)),
                    view.__class__.__name__,
                    exc_info=True,
                )

    # ------------------------------------------------------------------ #
    # Iter 0 / #1885 — full flush-queue parity with WS ``_flush_all_pending``.
    #
    # Before Iter 0 the runtime drained only 3 of WS's 8 queues
    # (push_events / navigation / deferred), so the runtime's one production
    # user (``url_change`` — dj-patch / popstate) silently dropped flash
    # messages, page-metadata updates, layout swaps, accessibility
    # announcements, and i18n commands queued during ``handle_params``
    # (live #1646 instance INSIDE the convergence target). The 5 methods
    # below + ``_flush_all_pending`` mirror WS ``websocket.py:888`` exactly:
    # same queue set, same canonical drain order, same per-queue frame shape
    # and ``_drain_*`` method names (including the defensively hasattr-guarded
    # a11y/i18n hooks that core views don't provide). ``_flush_all_pending``
    # is the single source of the turn-end drain so a future queue addition
    # can never again be wired on one path and not the other.
    # ------------------------------------------------------------------ #

    async def _flush_flash(self) -> None:
        """Drain pending flash messages and emit ``flash`` frames (WS parity)."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_flash"):
            return
        commands = view._drain_flash()
        if not isinstance(commands, list):
            return
        for cmd in commands:
            await self.transport.send({"type": "flash", **cmd})

    async def _flush_page_metadata(self) -> None:
        """Drain pending page-metadata commands and emit ``page_metadata`` frames."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_page_metadata"):
            return
        commands = view._drain_page_metadata()
        if not isinstance(commands, list):
            return
        for cmd in commands:
            await self.transport.send({"type": "page_metadata", **cmd})

    async def _flush_pending_layout(self) -> None:
        """Render + emit a pending ``set_layout`` swap (WS parity, v0.6.0).

        Mirrors ``websocket.py:_flush_pending_layout``: render the queued
        layout template with the view's current context and emit a
        ``{"type": "layout", "path": ..., "html": ...}`` frame. Layout
        template errors must never kill the live connection — ``TemplateDoesNotExist``
        warns + skips; any other error logs (and re-raises in DEBUG so
        programmer errors surface during development).
        """
        view = self.view_instance
        if not view or not hasattr(view, "_drain_pending_layout"):
            return
        layout_path = view._drain_pending_layout()
        if not layout_path:
            return
        from django.conf import settings as django_settings
        from django.template.exceptions import TemplateDoesNotExist
        from django.template.loader import render_to_string

        try:
            context = view.get_context_data() if hasattr(view, "get_context_data") else {}
            layout_html = await sync_to_async(render_to_string)(layout_path, context)
        except TemplateDoesNotExist:
            logger.warning(
                "set_layout(%r) — template not found; ignoring swap request", layout_path
            )
            return
        except Exception:  # noqa: BLE001 — layout errors must not kill the wire
            logger.exception(
                "set_layout(%r) — template rendering raised; ignoring swap request", layout_path
            )
            if getattr(django_settings, "DEBUG", False):
                raise
            return
        await self.transport.send({"type": "layout", "path": layout_path, "html": layout_html})

    async def _flush_accessibility(self) -> None:
        """Drain queued screen-reader announcements + focus command (WS parity)."""
        view = self.view_instance
        if not view:
            return
        if hasattr(view, "_drain_announcements"):
            try:
                announcements = view._drain_announcements()
                if announcements and isinstance(announcements, list):
                    await self.transport.send(
                        {"type": "accessibility", "announcements": announcements}
                    )
            except Exception:
                logger.warning("Failed to flush accessibility announcements", exc_info=True)
        if hasattr(view, "_drain_focus"):
            try:
                focus_cmd = view._drain_focus()
                if focus_cmd and isinstance(focus_cmd, tuple) and len(focus_cmd) == 2:
                    selector, options = focus_cmd
                    await self.transport.send(
                        {"type": "focus", "selector": selector, "options": options}
                    )
            except Exception:
                logger.warning("Failed to flush focus command", exc_info=True)

    async def _flush_i18n(self) -> None:
        """Drain pending i18n commands and emit ``i18n`` frames (WS parity)."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_i18n_commands"):
            return
        for cmd in view._drain_i18n_commands():
            await self.transport.send({"type": "i18n", **cmd})

    async def _flush_all_pending(self) -> None:
        """Drain every queued client side-effect at the end of a runtime turn,
        in WS's canonical order. Single source of truth for the turn-end drain
        on the runtime path (mirrors ``websocket.py:_flush_all_pending``): every
        turn-end site (event render, url_change) calls THIS so no path can drop a
        queued command (#1646). Each ``_flush_*`` drains + clears its own queue,
        so calling twice in one turn is a harmless no-op.

        Order matches WS exactly: push_events → flash → page_metadata →
        pending_layout → deferred → navigation → accessibility → i18n. Layout
        (replaces ``<body>``) goes after page_metadata so head mutations land
        first and survive the swap; deferred callbacks run after the visible
        side-effects so they observe post-patch state.
        """
        self._flush_push_events()
        await self._flush_flash()
        await self._flush_page_metadata()
        await self._flush_pending_layout()
        await self._flush_deferred()
        await self._flush_navigation()
        await self._flush_accessibility()
        await self._flush_i18n()

    # ------------------------------------------------------------------ #
    # Background work (start_async / @background) — runtime parity (#1887).
    #
    # The legacy SSE event path dispatched ``start_async`` callbacks via
    # ``_sse_run_async_work`` after the event render/noop. The runtime's
    # ``dispatch_event`` set the ``async_pending`` wire flag but had no
    # dispatcher, so converging SSE onto it without this would silently
    # break ``start_async`` / ``@background`` for SSE consumers (a #1646
    # gap inside the convergence target). These mirror the legacy SSE
    # helpers (``sse.py:_sse_run_async_work`` / ``_sse_execute_async_task``)
    # and the WS ``_dispatch_async_work`` / ``_run_async_work`` shape, but
    # push through ``self.transport.send`` so they are wire-blind.
    #
    # Reached via ``dispatch_event`` for BOTH transports now: SSE since Iter 1,
    # and WS since #1907 THE FLIP (RUNTIME_OWNED_VERBS = {"url_change", "event"}).
    # A WS event's ``start_async`` / ``@background`` work therefore dispatches
    # through THIS runtime helper (which pushes via ``self.transport.send`` →
    # ``consumer.send_json``, wire-blind) rather than the consumer's own
    # ``_dispatch_async_work``. The consumer helper is still reached by the
    # WS-only non-event paths (ticks / server_push / db_notify / the bespoke
    # deferred-activity re-dispatcher). The two are behaviorally equivalent for
    # the event turn (both flush start_async + @background callbacks off-thread).
    # ------------------------------------------------------------------ #

    def _dispatch_async_work(self, event_name: Optional[str]) -> None:
        """Schedule any ``start_async`` callbacks queued during the handler.

        Supports both the named-task dict (``_async_tasks``) and the legacy
        single-task tuple (``_async_pending``) formats, matching
        ``LiveViewConsumer._dispatch_async_work``. Fire-and-forget: each task
        runs in its own ``ensure_future`` so the event POST returns promptly
        and results stream in via the transport when ready.
        """
        view = self.view_instance
        if not view:
            return

        tasks = getattr(view, "_async_tasks", None)
        if tasks:
            for task_name, (callback, args, kwargs) in list(tasks.items()):
                asyncio.ensure_future(
                    self._execute_async_task(task_name, callback, args, kwargs, event_name)
                )
            view._async_tasks = {}

        pending = getattr(view, "_async_pending", None)
        if pending:
            view._async_pending = None
            callback, args, kwargs = pending
            asyncio.ensure_future(
                self._execute_async_task("_default", callback, args, kwargs, event_name)
            )

    async def _execute_async_task(
        self,
        task_name: str,
        callback,
        args,
        kwargs,
        event_name: Optional[str],
    ) -> None:
        """Run one background task and stream the re-rendered result.

        Mirrors ``sse.py:_sse_execute_async_task``: run the callback off-thread,
        let ``handle_async_result`` observe success/failure, re-sync + re-render,
        and emit a ``patch`` / ``html_update`` frame plus a turn-end flush. Any
        callback failure is logged and routed through ``handle_async_result`` so
        the client is never left stuck in a loading state.
        """
        view = self.view_instance
        if not view:
            return

        try:
            result = await sync_to_async(callback)(*args, **kwargs)

            if hasattr(view, "handle_async_result"):
                await sync_to_async(view.handle_async_result)(task_name, result=result, error=None)

            await self._render_async_result(event_name)

        except Exception as exc:
            logger.exception(
                "Runtime: error in start_async callback '%s' on %s",
                task_name,
                view.__class__.__name__ if view else "?",
            )
            if hasattr(view, "handle_async_result"):
                try:
                    await sync_to_async(view.handle_async_result)(task_name, result=None, error=exc)
                    await self._render_async_result(event_name)
                except Exception:
                    logger.exception(
                        "Runtime: error in handle_async_result for task '%s'", task_name
                    )

    async def _render_async_result(self, event_name: Optional[str]) -> None:
        """Re-sync + re-render after background work and emit the result frame.

        Shared by the success + error paths of ``_execute_async_task``. Stamps
        the transport's client-checked wire version (#1858) and drains the
        turn-end queues so push_events / deferred / etc. scheduled inside the
        background callback reach the client.

        The result frame carries ``source="async"`` (ADR-022 Iter 2 Phase 2.3a)
        so it matches the WS ``_run_async_work`` frames (websocket.py:1166/1186/
        1223/1238), which all tag ``source="async"``. Without this, the runtime's
        async-result frames were the lone untagged twin of the WS ones — a #1646
        parallel-path drift INSIDE the convergence target. The client uses
        ``source`` to distinguish an out-of-band background-completion update from
        the in-turn ``source="event"`` response, so the tag must agree across
        transports. Goes LIVE for SSE + ``url_change`` async work (both use the
        runtime async dispatcher today); WS picks it up post-flip (Phase 2.3b).
        """
        view = self.view_instance
        if not view:
            return
        if hasattr(view, "_sync_state_to_rust"):
            await sync_to_async(view._sync_state_to_rust)()
        html, patches, version = await sync_to_async(view.render_with_diff)()
        wire_version = self.transport.next_client_version(html, version)

        if patches is not None:
            patch_list = fast_json_loads(patches) if isinstance(patches, str) else patches
            msg: Dict[str, Any] = {
                "type": "patch",
                "patches": patch_list,
                "version": wire_version,
                "event_name": event_name,
                "source": "async",
            }
        else:
            if hasattr(view, "_strip_comments_and_whitespace"):
                html = view._strip_comments_and_whitespace(html)
            if hasattr(view, "_extract_liveview_content"):
                html = view._extract_liveview_content(html)
            msg = {
                "type": "html_update",
                "html": html,
                "version": wire_version,
                "event_name": event_name,
                "source": "async",
            }
        await self.transport.send(msg)
        await self._flush_all_pending()
