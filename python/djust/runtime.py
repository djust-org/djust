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
import hashlib
import inspect
import json
import logging
import re
import time
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    AsyncIterator,
    Callable,
    ContextManager,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

from asgiref.sync import sync_to_async

if TYPE_CHECKING:
    from ._async_batch import AsyncBatch
    from ._exposure_children import ChildStateSession

from .rate_limit import ConnectionRateLimiter
from .security import handle_exception, sanitize_for_log
from .serialization import fast_json_loads
from .validation import (
    validate_handler_params,
    validated_call_arguments,
    get_handler_parameter_policy,
)
from .websocket_utils import (
    _call_handler,
    _safe_error,
    _validate_event_security,
    get_handler_coerce_setting,
)

# How long an event-path state save may take before it is abandoned (#1475).
#
# Deliberately bounded: a slow session backend must never stall event handling.
# The consequence is that a save is BEST-EFFORT, not guaranteed — under enough
# load the write is dropped and only a warning is logged.
#
# It is a named constant rather than two inline literals because a test that
# asserts a save *landed* is otherwise asserting an unconditional outcome from
# a time-bounded operation, which makes it pass everywhere except a loaded
# machine. Such a test should raise this bound so it exercises the save LOGIC
# rather than racing a wall clock (#2154).
EVENT_STATE_SAVE_TIMEOUT_S = 0.150


logger = logging.getLogger(__name__)


def _diagnostics_policy_allows(owner: Any) -> bool:
    """Legacy owner or DEBUG: see ``_exposure_diagnostics.diagnostics_policy_allows``."""
    from ._exposure_diagnostics import diagnostics_policy_allows

    return diagnostics_policy_allows(owner)


def _mount_tenant_scope(method: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Restore mount-local tenant/diagnostic scopes on failure and early return."""

    @wraps(method)
    async def scoped(*args: Any, **kwargs: Any) -> None:
        from ._exposure_diagnostics import diagnostic_scope

        with diagnostic_scope(), _tenant_context(None):
            await method(*args, **kwargs)

    return scoped


def _runtime_diagnostic_scope(
    method: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Keep a mounted owner's diagnostic restriction across one runtime turn."""

    @wraps(method)
    async def scoped(runtime: Any, *args: Any, **kwargs: Any) -> None:
        from ._exposure_diagnostics import diagnostic_scope, watch_diagnostic_owner

        with diagnostic_scope():
            watch_diagnostic_owner(runtime, "view_instance")
            await method(runtime, *args, **kwargs)

    return scoped


def _tenant_context(tenant: Any) -> ContextManager[Any]:
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


def _consume_async_cancel(view: Any, task_name: str) -> bool:
    """True (and forget the mark) when ``task_name`` was cancelled via
    ``cancel_async`` / ``cancel_async_all``. Same consume-once semantics as
    the WS consumer's ``_run_async_work`` checks."""
    cancelled = getattr(view, "_async_cancelled", None)
    if cancelled and task_name in cancelled:
        cancelled.discard(task_name)
        return True
    return False


def maybe_start_tick_task(consumer: Any, view_class: Any) -> bool:
    """Start the periodic tick task for ``view_class`` if it opted in.

    A view opts in by setting ``tick_interval`` AND overriding ``handle_tick``
    — the base implementation is a no-op, so starting a loop for it would burn
    a task per connection to render nothing.

    Extracted from the mount hook so a test can assert "mount starts the tick
    task" against THIS function rather than re-implementing the condition
    (#2124). A test that duplicates the rule passes even when the runtime stops
    applying it, which is the decorative-pin failure mode (#1859).

    Returns whether a task was started.
    """
    tick_interval = getattr(view_class, "tick_interval", None)
    if not tick_interval:
        return False

    from .live_view import LiveView as _LV

    if view_class.handle_tick is _LV.handle_tick:
        return False

    consumer._tick_task = asyncio.create_task(consumer._run_tick(tick_interval))
    # The view this task ticks for, so a failed mount stops only its own task
    # (#3027). The runtime holds the mounting view when this runs.
    runtime = getattr(consumer, "_runtime", None)
    try:
        consumer._tick_task._djust_tick_view = getattr(runtime, "view_instance", None)
    except AttributeError:
        # A custom task factory whose tasks take no new attributes: the
        # failed-mount hook then cancels whatever tick the consumer holds.
        pass
    return True


_DOCUMENT_HEAD_RE = re.compile(r"<head\b[^>]*>(.*?)</head\s*>", re.IGNORECASE | re.DOTALL)
_DOCUMENT_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)


def _head_title_inner(markup: Optional[str]) -> Optional[str]:
    """The raw inner text of the ``<title>`` inside ``markup``'s ``<head>``.

    Only the ``<head>`` is searched, so an SVG ``<title>`` in the body never
    counts."""
    if not markup:
        return None
    head = _DOCUMENT_HEAD_RE.search(markup)
    if head is None:
        return None
    match = _DOCUMENT_TITLE_RE.search(head.group(1))
    return None if match is None else match.group(1)


def document_title(html: Optional[str]) -> Optional[str]:
    """The text of the ``<title>`` inside rendered ``html``'s ``<head>``.

    Entities are decoded (the client assigns ``document.title``, a text sink)
    and whitespace is collapsed the way the browser does for a title.
    """
    inner = _head_title_inner(html)
    if inner is None:
        return None
    import html as html_lib

    text = " ".join(html_lib.unescape(inner).split())
    return text or None


def _view_document_source(view: Any) -> Optional[str]:
    """The view's page template source with inheritance flattened, or None."""
    inline = getattr(view, "template", None)
    if inline:
        return str(inline)
    template_name = getattr(view, "template_name", None)
    if not template_name:
        return None
    from django.template import loader

    source = loader.get_template(template_name).template.source
    if "{% extends" in source or "{%extends" in source:
        from ._rust import resolve_template_inheritance
        from .utils import get_template_dirs

        return str(resolve_template_inheritance(template_name, get_template_dirs()))
    return str(source)


def _queued_title(view: Any) -> bool:
    """Whether ``view`` has a ``page_title`` command waiting to be sent."""
    pending = getattr(view, "_pending_page_metadata", None)
    if not isinstance(pending, list):
        return False
    return any(isinstance(cmd, dict) and cmd.get("action") == "title" for cmd in pending)


def navigation_title(view: Any) -> Optional[str]:
    """The ``<title>`` the destination page would have on a full load (#3036).

    ``live_redirect`` mounts the destination view and swaps only its
    ``dj-root``, so the tab kept the previous page's title unless the view set
    ``page_title``. This renders just the ``<title>`` element of the view's
    page template (``{% block title %}`` included) with the values the mount
    render just used, instead of rendering the whole page shell, which could
    run ``{% live_render %}`` and other tags with side effects.

    Conservative by design: returns ``None`` (the title is left alone, as it
    was before) when the template has no ``<head><title>``, when the title
    uses ``{{ block.super }}`` (flattened inheritance loses the parent block),
    when a variable it reads is not a value the view holds, or when anything
    fails. Never raises.
    """
    try:
        inner = _head_title_inner(_view_document_source(view))
        if inner is None or "block.super" in inner:
            return None
        if "{" not in inner:
            return document_title(f"<head><title>{inner}</title></head>")
        from ._rust import extract_template_variables, render_template
        from .serialization import normalize_django_value

        immutables = getattr(view, "_prev_context_immutables", None) or {}
        context: Dict[str, Any] = {}
        for name in extract_template_variables(inner):
            if name in immutables:
                context[name] = immutables[name]
            elif not name.startswith("_") and name in getattr(view, "__dict__", {}):
                value = view.__dict__[name]
                if callable(value):
                    return None
                context[name] = normalize_django_value(value)
            else:
                return None
        rendered = render_template(inner, context)
        return document_title(f"<head><title>{rendered}</title></head>")
    except Exception as exc:  # noqa: BLE001 — a title must never break navigation
        from ._exposure_diagnostics import log_failure_for

        # The title renders view values; its error text can carry them (ADR-038).
        log_failure_for(
            logger,
            (view,),
            exc,
            "navigation title unavailable for %s",
            type(view).__name__,
            level="debug",
            traceback=True,
        )
        return None


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

    async def on_view_mounted(self, view_instance: Any) -> None:
        """Stamp transport-specific identity + post-mount setup on the freshly-mounted view.

        Called by ``dispatch_mount`` once ``self.view_instance`` is set and the
        pre-mount auth sequence + on_mount hooks have admitted it, so a
        transport can attach its own back-references the way the legacy bespoke
        mount paths did. SSE stamps ``_sse_session_id`` / ``_sse_session`` (used
        for introspection + limits) and the real query string. WS performs the
        WS-only post-mount channel-layer wiring (server-push view group, presence
        group, db_notify groups), the periodic ``_tick_task`` start, the
        ``use_actors`` flag, and the real-scope path/query-string stamps (ADR-022
        Iter 3 Phase 3.3b, Finding B residual). Async because the WS impl awaits
        ``channel_layer.group_add(...)``. Behavior-preserving extension hook — the
        runtime stays wire-blind (#1887, ADR-022 Iter 1 / #1919, ADR-022 Iter 3).
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
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit the per-render observability the WS bespoke render branch owned.

        ``context`` (#1908 item 2): the rendered ``get_context_data()`` dict, when
        the caller has it in hand on the ``no_patches`` branch, so the WS impl can
        build the ``context_snapshot`` the bespoke ``_emit_full_html_update`` carried
        for the ``no_patches`` reason. DEBUG-tooling-only metadata — ``None`` on
        every other reason and on transports/callers that do not surface it (the
        signal's load-bearing fields are ``reason`` / ``version`` / ``event_name`` /
        ``html_size``, all unaffected).

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

    def on_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: Optional[str],
        event_ref: Optional[int] = None,
    ) -> None:
        """Decorate an outbound EVENT render frame with the DEBUG residuals (#1908).

        Called by ``_render_and_send`` for EVERY ``patch`` / ``html_update`` frame it
        emits on a user-event turn — IN-PLACE, just before ``transport.send(frame)``.
        Folds the three DEBUG-only / cosmetic residuals the bespoke WS ``_send_update``
        owned that the runtime render path (which sends via ``transport.send``
        directly) dropped after THE FLIP (#1907):

          1. the per-event ``_debug`` panel payload (DEBUG + ``_debug_panel_active``
             gated) + the top-level ``timing`` / ``performance`` fields (gated on
             ``_should_expose_timing()`` = DEBUG or ``DJUST_EXPOSE_TIMING``); and
          3. the ``_current_event_name`` / ``_current_event_ref`` consumer attrs the
             bespoke handler set for its no-arg ``_dispatch_async_work()`` (cosmetic
             parity for any out-of-band consumer reader).

        PRODUCTION is byte-identical: all attached fields are DEBUG/timing-gated, so a
        prod-mode frame is unchanged (matching the bespoke path, where both were also
        absent in prod).

        - WS: attaches the gated ``_debug`` + ``timing`` / ``performance`` (verbatim
          ``_attach_debug_payload`` + ``_should_expose_timing`` shape from the deleted
          bespoke ``_send_update``) and stamps the consumer event attrs.
        - SSE / partial test transports: not implemented → the runtime's defensive
          ``getattr`` makes the call a no-op (SSE has no debug panel; zero change).

        Best-effort by contract: implementations MUST swallow their own errors — a
        debug-decoration failure must never break the event turn.
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
        - SSE: holds the session render lock so event/background results cannot
          race with another POST replacing the page.

        The WS actor-event hook also acquires this context before dispatching to
        Rust, so actor results cannot race with these Python render producers.
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

        Called by ``_dispatch_event_inner`` before its normal event context; the
        WS implementation acquires that context itself when :meth:`uses_actors` is true and
        the event is NOT routed to a sticky child (the WS
        ``not is_embedded_child_target`` mutual exclusion, websocket.py:3280-3282).

        - WS: serializes actor dispatch against the consumer's render producers —
          time-travel record, shared security + param validation,
          ``actor_handle.event()``, patch/HTML framing with the consumer-owned
          wire version (#1788), error handling, and the deferred-activity flush.
        - SSE: never called (``uses_actors`` is ``False``); raises
          :class:`NotImplementedError` if invoked directly.
        """
        raise NotImplementedError("Actor events are WS-only; SSE refuses use_actors mounts.")

    async def explicit_event_request(self, view: Any) -> Any:
        """Return a fresh trusted event request; unsupported transports fail closed."""
        raise NotImplementedError("Explicit event requests are not supported")

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

    # ------------------------------------------------------------------ #
    # Mount hooks (ADR-022 Iter 3 Phase 3.2 — DORMANT scaffolding, #1915)
    #
    # The 5 hooks below define the transport seam the Phase 3.3b WS-mount
    # flip needs, mirroring how Phase 2.3a defined the event hooks
    # (``event_context`` / ``on_event_recorded`` / ``dispatch_actor_event``)
    # DORMANT before the event flip wired + routed them. They are NOT called
    # by ``dispatch_mount`` yet — Phase 3.3a wires them in. Until then
    # ``handle_mount`` keeps doing all of this inline (untouched), and the
    # runtime/SSE mount path is unaffected because every Protocol default
    # below is a behaviour-preserving no-op (or the existing refusal).
    # ------------------------------------------------------------------ #

    def on_view_instantiated(self, view: Any) -> None:
        """Stamp transport back-references on a freshly-instantiated view.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding B — ``view_instance``
        ownership INVERTS at mount: url_change/event mirror consumer→runtime,
        but mount CREATES the view, so every WS-only consumer attr that the
        bespoke ``handle_mount`` stamped right after instantiation must move
        into a transport hook that writes onto the consumer during the runtime
        mount). Called by ``dispatch_mount`` (Phase 3.3a) immediately after the
        view is instantiated, BEFORE auth / mount / render.

        - WS: sets ``view._ws_consumer`` (streaming support, websocket.py:2128),
          wires ``view._push_events_flush_callback`` to ``consumer._flush_push_events``
          (websocket.py:2134-2135), registers the view in the observability
          registry (websocket.py:2161-2167), and stashes the validated
          ``view._websocket_host`` / ``_websocket_secure`` (websocket.py:2268-2270).
        - SSE: a no-op — SSE has no consumer to back-reference; its identity
          stamp already lands in :meth:`on_view_mounted`.

        DORMANT until Phase 3.3a: ``dispatch_mount`` does not call this yet, and
        the WS bespoke ``handle_mount`` keeps stamping these attrs inline.
        """

    def uses_actors_for_mount(self, view: Any) -> bool:
        """Return whether this MOUNT must be driven through the actor system.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding D — actor mount is actively
        REFUSED by the runtime today (``dispatch_mount`` raises a structured
        ``use_actors is not supported over SSE`` error, runtime.py:1175). Once
        Phase 3.3b flips WS mounts onto the runtime, a ``use_actors`` WS view
        must instead be mounted through the actor system, NOT refused. This hook
        + :meth:`dispatch_actor_mount` close that gap; the runtime keeps
        refusing on SSE.

        - WS: ``getattr(view, "use_actors", False) and create_session_actor is
          not None`` — the exact precondition of the bespoke actor block
          (websocket.py:2213).
        - SSE: ``False`` — SSE has no bidirectional actor channel; the mount-time
          refusal stays.

        DORMANT until Phase 3.3a: WS mounts still run on the bespoke handler and
        the runtime/SSE path still hits the refusal at runtime.py:1175.
        """
        return False

    async def dispatch_actor_mount(self, view: Any, data: Dict[str, Any]) -> Any:
        """Mount a view through the actor system + return its ``{html, version}``.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding D). Called by ``dispatch_mount``
        (Phase 3.3a) when :meth:`uses_actors_for_mount` is true, OUTSIDE the
        normal Rust render path. Returns the mount render result (an object /
        dict carrying at least ``html`` and ``version``) so the runtime can build
        the mount frame from the actor's authoritative render.

        - WS: creates the per-session actor via ``create_session_actor``
          (websocket.py:2213-2217) and runs ``actor_handle.mount(view_path,
          context_data, view)`` → ``{html, version}`` (websocket.py:2665-2706),
          verbatim.
        - SSE: never called (``uses_actors_for_mount`` is ``False``); raises
          :class:`NotImplementedError` if invoked directly.
        """
        raise NotImplementedError("Actor mounts are WS-only; SSE refuses use_actors mounts.")

    def next_mount_version(self, html: Optional[str], rust_version: int = 1) -> int:
        """Return the wire ``version`` to stamp on the MOUNT frame.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding C). Mount ESTABLISHES the client
        VDOM baseline — it must NOT arm ``request_html`` recovery (a fresh mount
        has no prior frame to recover to). This is distinct from
        :meth:`next_client_version`, which arms recovery (``_next_version_armed``)
        because it stamps render-SEND frames (patch / html_update). The mount
        frame is client-checked too (the client sets ``clientVdomVersion =
        data.version`` on mount, ``static/djust/src/03-websocket.js:382``), so the
        version MUST come from the SAME monotonic per-connection source as
        ``event`` — just WITHOUT the recovery arm.

        ``rust_version`` is the raw ``render_with_diff()`` version (SSE's source);
        it mirrors :meth:`next_client_version`'s second arg so the runtime can hand
        every transport the same inputs. WS ignores it (returns the consumer
        counter); SSE returns it unchanged. The default keeps the Phase-3.2
        single-arg callers (the dormant tests) working.

        - WS: returns ``consumer._next_version()`` — the no-arm counter the
          bespoke ``handle_mount`` uses (websocket.py:2746). It does NOT call
          ``_next_version_armed`` / ``_arm_recovery``, so ``_recovery_html`` stays
          ``None`` after a mount.
        - SSE: returns ``rust_version`` (the raw Rust ``render_with_diff()``
          version the runtime stamped inline before Phase 3.3a) — SSE has no
          consumer counter (the #1788 unification never reached SSE); ``html`` is
          accepted for signature parity and ignored.

        Wired into ``dispatch_mount`` (Phase 3.3a). The Protocol default raises so
        a transport that participates in mounts MUST override it.
        """
        raise NotImplementedError("next_mount_version is transport-specific.")

    async def on_mount_render_ready(self, view: Any, html: Optional[str]) -> Optional[str]:
        """Post-render mount hook: sticky preservation + ``sticky_hold`` frame.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding B residual). Called by
        ``dispatch_mount`` (Phase 3.3a) AFTER the initial render produced ``html``
        but BEFORE the mount frame is sent, so a transport can adjust the
        outgoing HTML and/or emit pre-mount frames. Returns the (possibly
        adjusted) HTML the caller should use for the mount frame.

        - WS: on the ``live_redirect`` path (when the consumer staged a
          ``_sticky_preserved`` dict), scans the rendered HTML for surviving
          ``[dj-sticky-slot]`` ids (``_find_sticky_slot_ids``), re-registers the
          survivors onto the new parent (``view._register_child``), and emits the
          ``sticky_hold`` frame BEFORE the mount frame so the client's
          ``reattachStickyAfterMount`` reconciliation is authoritative
          (websocket.py:2080-2082 reset + 2836-2903 emit). Returns ``html``
          unchanged (sticky preservation adjusts registration + emits a frame,
          not the mount HTML).
        - SSE: returns ``html`` unchanged (no sticky / live_redirect surface).

        DORMANT until Phase 3.3a: ``handle_mount`` keeps the sticky block inline.
        """
        return html

    async def finalize_mount_auth(self, view: Any, verdict: Any) -> None:
        """Apply the transport-level finalization of a BLOCKING mount-auth verdict.

        ADR-022 Iter 3 Phase 3.2 (#1915, Finding E — auth verdict→frame drift).
        The runtime's ``_check_auth`` / ``run_on_mount_hooks`` paths already SEND
        the verdict frame in the runtime's shape (``{type:error, error:...}`` for
        a permission denial, ``{type:navigate, to:...}`` for a login/hook
        redirect) and clear ``view_instance``. What the runtime path is MISSING
        vs the WS bespoke ``handle_mount`` is the transport-level socket
        ``close(4403)`` (websocket.py:2337-2401). This hook adds it.

        ``verdict`` is a marker describing the kind of block. The two redirect
        verdicts (login-redirect at websocket.py:2347-2370 and the
        ``run_on_mount_hooks`` redirect at 2389-2400) MUST gate the close on
        ``not consumer._mounting_in_batch`` — a batched login-required view
        reports as ``navigate[]`` and closing would kill the shared socket's
        sibling mounts (#291 / #1780; ``test_ws_auth_close_socket.py`` is the
        net). A permission denial (websocket.py:2344-2346) closes unconditionally
        in the WS bespoke path.

        - WS: ``await consumer.close(code=4403)``, gated on
          ``not consumer._mounting_in_batch`` for the redirect verdicts (the
          ``_mounting_in_batch`` flag is exposed via :attr:`mounting_in_batch`).
        - SSE: no socket close — SSE has no persistent socket to drop; the
          verdict frame (error / navigate) the runtime already sent is the
          SSE-side finalization. A no-op here.

        DORMANT until Phase 3.3a: ``handle_mount`` keeps doing the
        verdict→frame→close inline; ``dispatch_mount`` does not call this yet.
        """

    @property
    def mounting_in_batch(self) -> bool:
        """Whether this transport is mid-``mount_batch`` on a shared socket.

        ADR-022 Iter 3 Phase 3.2 (#1915, #291 / #1780). Exposes the consumer's
        ``_mounting_in_batch`` flag through the transport seam so
        :meth:`finalize_mount_auth` can gate the ``close(4403)`` without reaching
        into a WS-specific attr. Default ``False`` (single mount).
        """
        return False


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
        capture = getattr(self._consumer, "_capture_recovery_contracts", None)
        if callable(capture):
            capture(data)
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
        return int(self._consumer._next_version_armed(html))

    def build_request(self) -> Optional[Any]:
        """WS has no captured HTTP request — the runtime synthesizes from
        ``self.scope`` (carrying the authenticated user + session)."""
        return None

    async def on_view_mounted(self, view_instance: Any) -> None:
        """WS post-mount channel-layer wiring + tick + flags (#1919, Finding B residual).

        Verbatim fold of the WS bespoke ``handle_mount`` post-instantiation setup
        block (websocket.py:2148-2217 + the per-mount ``_sticky_auto_reattached``
        reset at websocket.py:2082) that ``dispatch_mount`` did NOT carry pre-flip.
        The runtime calls this hook AFTER instantiation + back-refs AND after the
        pre-mount auth sequence + on_mount hooks have admitted the view, BEFORE
        state restore / mount(). A refused or redirected mount therefore joins no
        channel-layer group. It writes onto the CONSUMER (the runtime→consumer
        ownership direction at mount, Finding B):

          * real-scope path/query-string stamps for path-aware VDOM cache keys
            (websocket.py:2153-2156) — the runtime set ``_websocket_path =
            page_url`` + ``_websocket_query_string = ""``; the WS bespoke path uses
            the handshake ``scope`` values, so overwrite them here for parity;
          * server-push view group join (``view_group_name`` + ``group_add``,
            websocket.py:2172-2174);
          * presence group join when the view supports presence
            (websocket.py:2177-2184);
          * db_notify group joins for the class-level ``_listen_channels``
            (websocket.py:2190-2200). This runs PRE-mount(), so channels
            ``listen()`` adds in mount() are joined later, by
            ``on_mount_render_ready`` (#2962);
          * periodic tick task start when the subclass overrides ``handle_tick``
            (websocket.py:2202-2208);
          * the ``use_actors`` flag off the view class (websocket.py:2211) so
            ``disconnect``'s actor-cleanup guard (``if self.use_actors and
            self.actor_handle``) reflects reality. The actor HANDLE is NOT created
            here — ``dispatch_actor_mount`` (#1915, Finding D) creates it at the
            render step, WS-verbatim, so a non-actor view never spins one up.

        Async because ``channel_layer.group_add`` is a coroutine. Mirrors the
        bespoke try/except envelope shape (a setup failure surfaces the same way
        the bespoke instantiation try/except did — see ``dispatch_mount``'s
        instantiation guard, which wraps this hook's call site is the
        instantiation block; here we keep the per-step ``try/except`` the bespoke
        path used for presence + db_notify).
        """
        consumer = self._consumer

        # Reset the per-mount sticky auto-reattach tracker (websocket.py:2082): each
        # mount starts with an empty set; the template tag pushes ids onto it as it
        # claims survivors. The live_redirect path also resets it before calling the
        # shim (websocket.py:3768), but a plain mount needs the reset too — and this
        # hook fires for EVERY runtime mount, so it is the single source post-flip.
        consumer._sticky_auto_reattached = set()

        # Real-scope path + query string for path-aware VDOM cache keys
        # (websocket.py:2153-2156). The runtime stamped ``page_url`` + ``""``; the
        # WS bespoke path sources these from the handshake ``scope`` so /emails/ and
        # /emails/?sender=1 get separate VDOM caches. Overwrite for parity.
        scope = getattr(consumer, "scope", None) or {}
        view_instance._websocket_path = scope.get("path", "/")
        view_instance._websocket_query_string = scope.get("query_string", b"").decode("utf-8")

        # The dotted view path is the client-supplied frame value
        # (``data["view"]``), stashed on the view by ``dispatch_mount`` as
        # ``_djust_mount_view_path``. The bespoke path keyed the server-push group
        # name on this EXACT string (websocket.py:2172-2174), and external
        # broadcasters (``apush_to_view("app.MyView")``) string-mangle the same
        # value — so they MUST agree byte-for-byte. The consumer also stashes it as
        # ``_view_path`` (websocket.py:2172) for server-push introspection.
        dotted = getattr(view_instance, "_djust_mount_view_path", None) or ""
        consumer._view_path = dotted

        # Join per-view channel group for server-push (websocket.py:2169-2174).
        from .push import view_group_name

        consumer._view_group = view_group_name(dotted)
        await consumer.channel_layer.group_add(consumer._view_group, consumer.channel_name)

        # Join presence group if the view supports presence tracking
        # (websocket.py:2176-2184).
        consumer._presence_group = None
        if hasattr(view_instance, "get_presence_key"):
            try:
                from .presence import PresenceManager

                presence_key = view_instance.get_presence_key()
                consumer._presence_group = PresenceManager.presence_group_name(presence_key)
                await consumer.channel_layer.group_add(
                    consumer._presence_group, consumer.channel_name
                )
            except Exception as e:  # noqa: BLE001
                from ._exposure_diagnostics import log_failure

                log_failure(logger, e, "Error setting up presence group: %s", e, level="warning")

        # Join db_notify groups for every channel the view subscribed to via
        # NotificationMixin.listen() (websocket.py:2186-2200). Addressed
        # per-channel (djust_db_notify_<channel>) so a NOTIFY on one channel never
        # fans out to views listening on another. This runs BEFORE mount(), so it
        # only sees class-level ``_listen_channels``; channels ``listen()`` adds in
        # mount(), on a session restore, or in a later handler are joined by
        # ``_join_listen_channels`` from ``on_mount_render_ready`` and at the end
        # of every event turn (#2962).
        consumer._db_notify_channels = set()
        await self._join_listen_channels(view_instance)

        # Start periodic tick if the subclass overrides handle_tick
        # (websocket.py:2202-2208).
        view_class = type(view_instance)
        maybe_start_tick_task(consumer, view_class)

        # Set the use_actors flag off the view class (websocket.py:2211) so
        # disconnect's actor cleanup guard reflects reality. The actor HANDLE is
        # created later by dispatch_actor_mount (#1915, Finding D), not here.
        consumer.use_actors = getattr(view_class, "use_actors", False)

    async def _join_listen_channels(self, view: Any) -> None:
        """Join the ``djust_db_notify_<channel>`` group for every channel the
        view listens on that this consumer has not joined yet (#2962).

        ``NotificationMixin.listen()`` only records the channel and starts the
        process listener; the group join is transport work. It is idempotent
        (already-joined channels are skipped), so it is safe to call after
        mount() and after every event turn. A join failure is logged and the
        channel is retried on the next call.
        """
        consumer = self._consumer
        listen_channels = getattr(view, "_listen_channels", None)
        if not listen_channels:
            return
        joined = getattr(consumer, "_db_notify_channels", None)
        if not isinstance(joined, set):
            joined = set()
            consumer._db_notify_channels = joined
        for ch in sorted(set(listen_channels) - joined):
            try:
                await consumer.channel_layer.group_add(
                    f"djust_db_notify_{ch}", consumer.channel_name
                )
                joined.add(ch)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error joining db_notify group for %s: %s", ch, e)

    async def _sync_push_scopes(self, view: Any) -> None:
        """Match the consumer's scoped-push groups to ``view.push_scope`` (#3004).

        See ``push.sync_push_scope_groups``: idempotent, joins new scopes and
        leaves dropped ones, logs (never raises) on a bad value or a layer
        error.
        """
        from .push import sync_push_scope_groups

        await sync_push_scope_groups(self._consumer, view)

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
        context: Optional[Dict[str, Any]] = None,
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
        # ``no_patches`` context snapshot (#1908 item 2): the WS bespoke path captured
        # the ``get_context_data()`` dict it had in hand and passed it as
        # ``context_snapshot`` for the ``no_patches`` reason. ``_render_and_send`` now
        # threads that same dict in via ``context`` (only on the no-patch branch, only
        # when it had to call ``get_context_data()`` anyway — no extra render-cost),
        # so the runtime path carries the snapshot the bespoke path did. It is
        # DEBUG-tooling-only metadata (the signal's load-bearing fields are ``reason``
        # / ``version`` / ``event_name`` / ``html_size``); ``None`` everywhere else.
        # ``_previous_html`` is read-only (never assigned in production), so
        # ``previous_html_snippet`` is inert on both paths.
        try:
            from .websocket import _build_context_snapshot
            from ._exposure import explicit_debug_projection

            html_for_snapshot = getattr(view, "_previous_html", None)
            explicit_debug = explicit_debug_projection(view)
            context_snapshot = (
                (explicit_debug if explicit_debug is not None else _build_context_snapshot(context))
                if reason == "no_patches" and context is not None
                else None
            )
            _emit_full_html_update(
                view,
                reason,
                event_name,
                html,
                version,
                context_snapshot=context_snapshot,
                patch_count=patch_count,
                html_snippet=(html[:500] if reason == "no_patches" and html else None),
                previous_html_snippet=(
                    html_for_snapshot[:500]
                    if reason == "no_patches" and html_for_snapshot
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 — observability signal must never break the turn
            from ._exposure_diagnostics import log_failure

            # full_html_update uses send(), so application receivers'
            # exceptions arrive here.
            log_failure(
                logger, exc, "full-HTML-update signal emit failed", level="debug", traceback=True
            )

    def on_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: Optional[str],
        event_ref: Optional[int] = None,
    ) -> None:
        """Fold the DEBUG event-render residuals onto a runtime-emitted WS frame (#1908).

        Verbatim fold of the three residuals the bespoke ``_send_update`` owned (now
        deleted for the event path) that the runtime ``_render_and_send`` dropped:

          1. top-level ``timing`` / ``performance`` — gated on ``_should_expose_timing()``
             (DEBUG or ``DJUST_EXPOSE_TIMING``), exactly the bespoke gate
             (websocket.py:1407-1411); ``performance`` is the borrowed
             ``PerformanceTracker`` summary established by ``event_context``; and
          1b. the per-event ``_debug`` panel payload, via the consumer's existing
             ``_attach_debug_payload`` (its own DEBUG + ``_debug_panel_active`` gate,
             websocket.py:1627); plus
          3. the cosmetic ``_current_event_name`` / ``_current_event_ref`` consumer
             attrs the bespoke handler set for its no-arg ``_dispatch_async_work()``.

        PRODUCTION byte-identical: ``_should_expose_timing()`` is False and
        ``_attach_debug_payload`` early-returns when ``DEBUG`` is False, so a prod
        frame is unchanged (both were absent in prod on the bespoke path too). The
        consumer event attrs are internal-only (never serialized).

        Best-effort: any failure here is swallowed — a debug decoration must never
        break the event turn.
        """
        consumer = self._consumer
        # (3) cosmetic parity: stamp the consumer event attrs the bespoke handler set.
        # Internal-only — never serialized onto the frame; read by the consumer's
        # ``_dispatch_async_work`` (not on the runtime event path, but kept for any
        # out-of-band consumer reader).
        try:
            consumer._current_event_name = event_name
            consumer._current_event_ref = event_ref
        except Exception:  # noqa: BLE001 — cosmetic attr stamp must never break the turn
            pass

        try:
            from .websocket import _should_expose_timing

            # (1) Performance summary from the borrowed tracker (event_context set it
            # current); mirrors the bespoke ``tracker.get_summary()`` feed.
            from .performance import PerformanceTracker

            tracker = PerformanceTracker.get_current()
            performance = tracker.get_summary() if tracker is not None else None

            # Top-level timing/performance — production-gated identically to the
            # bespoke ``_send_update`` (websocket.py:1402-1411). ``timing`` carries the
            # render duration the runtime stamped on the frame under ``_timing_render_ms``
            # (popped here so it never leaks onto the wire when the gate is closed).
            render_ms = frame.pop("_timing_render_ms", None)
            # ADR-032 D4: ``scope == "component"`` marks a scoped patch so the
            # debug panel and ``benchmark_event`` can tell it from a page render.
            scope = frame.pop("_timing_scope", None)
            if _should_expose_timing():
                if render_ms is not None:
                    frame["timing"] = {"render": render_ms}
                    if scope is not None:
                        frame["timing"]["scope"] = scope
                if performance:
                    frame["performance"] = performance

            # (1b) The per-event ``_debug`` panel payload (DEBUG + panel-active gated
            # inside the helper). Reuse the consumer's existing implementation so the
            # runtime path and the still-bespoke tick/broadcast/async/actor paths share
            # ONE ``_debug`` shape (#1646). ``performance`` rides into ``_debug`` too,
            # matching the bespoke ``_attach_debug_payload(response, event_name, performance)``.
            attach = getattr(consumer, "_attach_debug_payload", None)
            if attach is not None:
                attach(frame, event_name, performance)
        except Exception:  # noqa: BLE001 — debug decoration must never break the event turn
            logger.debug("on_event_frame debug decoration failed", exc_info=True)

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
            owner=view,  # ADR-038 D-d: params are redacted for a nonlegacy owner
        )
        sql_scope.__enter__()
        try:
            yield
            # A handler that called ``listen()`` joins its NOTIFY group now,
            # while the turn still holds the render lock (#2962).
            await self._join_listen_channels(view)
            # Likewise a handler that changed ``push_scope`` (#3004) -- unless
            # the view was replaced during the turn (a live_redirect does not
            # take the render lock): its scopes are not this socket's any more.
            if getattr(consumer, "view_instance", None) is view:
                await self._sync_push_scopes(view)
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
        """Serialize actor dispatch with Python render producers and recovery."""
        consumer = self._consumer
        cancelled = False
        try:
            async with self.event_context(view):
                if consumer.view_instance is not view:
                    await consumer.send_error(
                        "Actor owner changed. Reload the page.", recoverable=False
                    )
                    return
                await self._dispatch_actor_event_locked(
                    view,
                    event_name,
                    params,
                    event_ref=event_ref,
                    cache_request_id=cache_request_id,
                )
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            # Deferred activity can dispatch another event: release the render
            # lock before draining it, and never dispatch against a replaced owner.
            if (
                not cancelled
                and consumer.view_instance is view
                and hasattr(view, "_flush_deferred_activity_events")
            ):
                try:
                    await view._flush_deferred_activity_events(consumer)
                except Exception:
                    logger.exception("dj_activity: deferred-event flush raised (actor path)")

    async def _dispatch_actor_event_locked(
        self,
        view: Any,
        event_name: str,
        params: Dict[str, Any],
        *,
        event_ref: Optional[int] = None,
        cache_request_id: Optional[str] = None,
    ) -> None:
        """Forward contracts captured inside the actor's serialized render.

        Framing / version-stamping notes:
        - The consumer OWNS the monotonic wire version (#1788); the actor's
          ``result['version']`` is IGNORED for the wire — ``_send_update`` is
          stamped with the consumer counter and arms recovery from the actor's
          exact full HTML. The actor's internal version still drives its own
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
            strict_params = (
                dict(params) if get_handler_parameter_policy(handler) == "strict" else None
            )
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
            if strict_params is not None:
                params = strict_params
            actor_task = asyncio.ensure_future(consumer.actor_handle.event(event_name, params))
            try:
                result = await asyncio.shield(actor_task)
            except asyncio.CancelledError:
                # The Rust mailbox operation survives cancellation of its Python
                # waiter. Keep the lock until it settles, without sending a frame.
                while not actor_task.done():
                    try:
                        await asyncio.shield(actor_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not actor_task.cancelled():
                    actor_task.exception()
                raise
            if consumer.view_instance is not view:
                await consumer.send_error(
                    "Actor owner changed. Reload the page.", recoverable=False
                )
                return

            runtime = getattr(consumer, "_runtime", None)
            active = getattr(runtime, "_parameter_contracts_active", False)
            snapshot: Dict[str, Any] = {}
            if "parameter_contracts" not in result and (
                active or get_handler_parameter_policy(handler) == "strict"
            ):
                await consumer.send_error(
                    "Actor render parameter contracts unavailable.", recoverable=False
                )
                return
            manifest = result.get("parameter_contracts")
            if manifest is not None or active:
                path = getattr(runtime, "_parameter_contract_view", None)
                if (
                    runtime is None
                    or getattr(runtime, "view_instance", None) is not view
                    or not isinstance(path, str)
                    or not path
                    or not isinstance(result.get("recovery_html"), str)
                ):
                    await consumer.send_error(
                        "Actor render parameter contracts unavailable.", recoverable=False
                    )
                    return
                snapshot["parameter_contract_snapshot"] = {
                    "parameter_contracts": manifest,
                    "parameter_contract_view": path,
                }
                runtime._parameter_contracts_active = True

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

            raw_html = result.get("recovery_html")
            version = (
                consumer._next_version_armed(raw_html)
                if isinstance(raw_html, str)
                else consumer._next_version()
            )
            await consumer._send_update(
                patches=patches,
                html=html,
                version=version,  # consumer-owned (#1788)
                cache_request_id=cache_request_id,
                event_name=event_name,
                **snapshot,
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

    async def explicit_event_request(self, view: Any) -> Any:
        """Supply freshly loaded session authentication for explicit events."""
        from ._exposure_auth import fresh_socket_request

        return await sync_to_async(fresh_socket_request)(view)

    # ------------------------------------------------------------------ #
    # Mount hooks (ADR-022 Iter 3 Phase 3.2 — DORMANT, #1915)
    # WS implementations. Each encapsulates the verbatim bespoke
    # ``handle_mount`` logic for the cited site so Phase 3.3a can wire them
    # into ``dispatch_mount`` with no behaviour change. NOT called yet.
    # ------------------------------------------------------------------ #

    def on_view_instantiated(self, view: Any) -> None:
        """Stamp WS consumer back-references on the freshly-instantiated view.

        Verbatim fold of the WS bespoke ``handle_mount`` post-instantiation
        stamps (#1915, Finding B):

          * ``view._ws_consumer = consumer`` (streaming support, websocket.py:2128);
          * ``view._push_events_flush_callback = consumer._flush_push_events`` so
            ``@background`` handlers can flush push_commands mid-execution
            (websocket.py:2134-2135) — only when the attr exists on the view;
          * observability ``register_view(session_id, view)`` (weakref registry;
            never break the connection, websocket.py:2161-2167);
          * ``view._websocket_host`` / ``view._websocket_secure`` from the
            validated handshake scope (websocket.py:2268-2270) so
            runtime-rebuilt requests carry the same host (#1646).
        """
        consumer = self._consumer
        view._ws_consumer = consumer
        if hasattr(view, "_push_events_flush_callback"):
            view._push_events_flush_callback = consumer._flush_push_events

        # Observability registry (weakrefs — best-effort; must never break the
        # connection, mirroring websocket.py:2161-2167).
        try:
            from djust.observability.registry import register_view

            register_view(consumer.session_id, view)
        except Exception as e:  # noqa: BLE001 — observability must never break a WS connection
            logger.warning("Failed to register view in observability registry: %s", e)

        # Validated handshake host/scheme stash (websocket.py:2243-2270). The
        # bespoke path computes this from ``validated_host_from_scope(scope)`` and
        # stamps it onto the view so runtime-rebuilt requests carry the same host
        # (#1646). Compute from the SAME validator + scope here. ``(None, False)``
        # for absent/invalid Hosts (non-browser clients) so the runtime falls back
        # to RequestFactory's default — matching the bespoke ``if host:`` guard.
        from .websocket import validated_host_from_scope

        scope = getattr(consumer, "scope", None) or {}
        host, is_secure = validated_host_from_scope(scope)
        view._websocket_host = host
        view._websocket_secure = is_secure

    def uses_actors_for_mount(self, view: Any) -> bool:
        """Whether this WS mount must run through the actor system (#1915, Finding D).

        Exact precondition of the bespoke actor block (websocket.py:2213):
        the view opts into ``use_actors`` AND the Rust ``create_session_actor``
        factory is available in this build.
        """
        from .websocket import create_session_actor

        return getattr(view, "use_actors", False) and create_session_actor is not None

    async def dispatch_actor_mount(self, view: Any, data: Dict[str, Any]) -> Any:
        """Create the session actor + mount through it → ``{html, version}`` (#1915, Finding D).

        Verbatim fold of the WS bespoke actor-mount path:

          * create the per-session actor via ``create_session_actor(session_id)``
            and stash it on the consumer as ``actor_handle`` (websocket.py:2213-2217);
          * call ``actor_handle.mount(view_path, context_data, view)`` →
            ``{html, version}`` (websocket.py:2665-2706).

        Returns the actor mount ``result`` dict so the runtime stamps the mount
        frame from the actor's authoritative render. ``data`` carries the mount
        frame (for ``view`` path); the actor render reads the view's
        ``get_context_data()`` exactly as the bespoke path does.
        """
        from .websocket import create_session_actor

        consumer = self._consumer
        view_path = data.get("view")
        consumer.use_actors = True
        logger.info("Creating SessionActor for %s", view_path)
        if create_session_actor is None:  # pragma: no cover — actor build absent
            # Reached only when the compiled extension lacks the actor surface;
            # the actor mount path is gated upstream on actor availability, so
            # this guard narrows the Optional and never fires in an actor mount.
            raise RuntimeError("SessionActor support is not available in this build")
        consumer.actor_handle = await create_session_actor(consumer.session_id)
        logger.info("SessionActor created: %s", consumer.actor_handle.session_id)

        # #2599: hand the actor the view's OWN template + dirs. Without them
        # `ViewActor::new` builds an empty-template backend and the mount
        # frame is `<html><head></head><body></body></html>`.
        from .utils import get_template_dirs

        get_template = getattr(view, "get_template", None)
        template = await sync_to_async(get_template)() if get_template is not None else None
        # ADR-029 (#2741): the actor path never runs `_sync_state_to_rust`,
        # so nothing pushed the render environment for THIS mount. Push it on
        # the calling thread — `SessionActorHandle.mount` captures the calling
        # thread's cells onto the actor as per-view config, and converts
        # `context_data` on that thread too — deliberately NOT inside
        # `sync_to_async`, which would push on a worker the capture never sees.
        from .render_env import apply_render_env

        apply_render_env()
        context_data = await sync_to_async(view.get_context_data)()
        result = await consumer.actor_handle.mount(
            view_path,
            context_data,
            view,
            template=template,
            template_dirs=[str(d) for d in get_template_dirs()],
        )
        return result

    def next_mount_version(self, html: Optional[str], rust_version: int = 1) -> int:
        """Stamp the consumer-owned NO-ARM wire version for the mount frame (#1915, Finding C).

        Returns ``consumer._next_version()`` — the SAME monotonic per-connection
        counter the bespoke ``handle_mount`` uses (websocket.py:2746). Crucially
        this is NOT ``_next_version_armed`` / ``_arm_recovery``: a mount
        ESTABLISHES the client VDOM baseline and has no prior frame to recover to,
        so ``_recovery_html`` MUST stay unset after a mount (distinct from
        :meth:`next_client_version`, which arms recovery for render-SEND frames).
        ``html`` and ``rust_version`` are accepted for signature parity with the
        Protocol but are unused on WS (the no-arm consumer counter is the source).
        """
        return int(self._consumer._next_version())

    async def on_mount_render_ready(self, view: Any, html: Optional[str]) -> Optional[str]:
        """Sticky preservation + ``sticky_hold`` frame before the mount frame (#1915, Finding B residual).

        Verbatim fold of the WS bespoke sticky block (websocket.py:2836-2903).
        On the ``live_redirect`` path the consumer staged a ``_sticky_preserved``
        dict; this scans the just-rendered ``html`` for surviving
        ``[dj-sticky-slot]`` ids, re-registers survivors onto the new parent
        (``view._register_child``), updates ``consumer._sticky_preserved`` to the
        authoritative survivor set, and emits the ``sticky_hold`` frame BEFORE the
        mount frame (ordering matters — the client's ``reattachStickyAfterMount``
        must see the hold first). The ``_sticky_auto_reattached`` set (reset per
        mount at websocket.py:2082) records survivors the template tag already
        re-registered, so we don't double-register them.

        It first joins the NOTIFY groups for channels ``listen()`` added in
        mount() or a session restore (#2962): this is the first transport hook
        after mount() on every admitted mount.

        Returns ``html`` unchanged: sticky preservation adjusts child
        registration + emits a frame, it does not rewrite the mount HTML. No-op
        (returns ``html``) when no stickys were staged.
        """
        from ._exposure import uses_legacy_exposure
        from .websocket import _find_sticky_slot_ids

        consumer = self._consumer
        # mount() (or a session restore) has run and the view is admitted:
        # join the NOTIFY groups for channels ``listen()`` added (#2962), and
        # the scoped-push groups for the view's ``push_scope`` (#3004).
        await self._join_listen_channels(view)
        await self._sync_push_scopes(view)
        sticky_preserved = getattr(consumer, "_sticky_preserved", None)
        if not sticky_preserved:
            return html

        try:
            matched_ids = _find_sticky_slot_ids(html or "")
            survivors_final: Dict[str, Any] = {}
            auto_reattached: set[str] = getattr(consumer, "_sticky_auto_reattached", set())
            for sticky_id, child in list(sticky_preserved.items()):
                if sticky_id in auto_reattached:
                    # The tag already re-registered this survivor at render time;
                    # don't call _register_child again (it would ValueError), but
                    # keep it in survivors_final for an authoritative hold list.
                    survivors_final[sticky_id] = child
                elif sticky_id in matched_ids and uses_legacy_exposure(child):
                    # Explicit children must pass the tag's expected-class,
                    # inputs and request-identity checks. Bare slot markup
                    # cannot authorize their reattachment.
                    if hasattr(view, "_register_child"):
                        try:
                            view._register_child(sticky_id, child)
                            survivors_final[sticky_id] = child
                        except ValueError:
                            logger.warning(
                                "sticky_id %s collided with new child on reattach",
                                sticky_id,
                            )
                            hook = getattr(child, "_on_sticky_unmount", None)
                            if callable(hook):
                                try:
                                    hook()
                                except Exception:  # noqa: BLE001
                                    logger.exception("sticky child _on_sticky_unmount raised")
                else:
                    if not uses_legacy_exposure(child):
                        from ._child_lifecycle import dispose_child_subtree

                        dispose_child_subtree(child, navigation=True)
                        continue
                    hook = getattr(child, "_on_sticky_unmount", None)
                    if callable(hook):
                        try:
                            hook()
                        except Exception:  # noqa: BLE001
                            if uses_legacy_exposure(child):
                                logger.exception("sticky child _on_sticky_unmount raised")
                            else:
                                logger.error("Explicit child unmount cleanup failed")
            consumer._sticky_preserved = survivors_final
            await consumer.send_json(
                {
                    "type": "sticky_hold",
                    "views": list(survivors_final.keys()),
                }
            )
        except Exception:  # noqa: BLE001 — defensive: never break mount
            logger.exception("failed to emit sticky_hold frame before mount")
        return html

    async def finalize_mount_auth(self, view: Any, verdict: Any) -> None:
        """Close the socket (4403) after a blocking mount-auth verdict (#1915, Finding E).

        The runtime path already SENT the verdict frame (error / navigate) and
        cleared ``view_instance``; this adds the transport-level
        ``close(code=4403)`` the WS bespoke ``handle_mount`` performed
        (websocket.py:2344-2399).

        ``verdict`` is a marker string describing the block kind. ALL verdicts
        gate the ``close(4403)`` on ``not consumer._mounting_in_batch`` (#291 /
        #1780, #1922): inside a ``mount_batch`` the socket is SHARED across
        sibling mounts, so closing it on ANY single view's blocking verdict —
        login redirect OR object/permission denial — collaterally drops the
        siblings (the #291 multiplexed-path failure). The denial still holds
        regardless: the runtime already sent the verdict frame (error /
        navigate) and cleared ``view_instance`` BEFORE this hook runs, so the
        blocked view is not mounted; only the socket-level close is suppressed
        in the batch case (the denied view simply reports in ``failed[]`` /
        ``navigate[]``, exactly as the redirect case already did).

          * ``"permission_denied"`` → close GATED on ``not mounting_in_batch``
            (#1922 — was unconditional, websocket.py:2345; over-closed the
            shared socket on a batched object/perm denial);
          * ``"redirect"`` / ``"hook_redirect"`` → close GATED on
            ``not consumer._mounting_in_batch`` (websocket.py:2368 / 2398) so a
            batched login-required view does NOT drop the shared socket's sibling
            mounts (#291 / #1780).

        A SINGLE (non-batch) blocking mount still closes 4403 for every verdict
        (``mounting_in_batch`` is ``False`` outside a batch).
        """
        consumer = self._consumer
        # The refused mount leaves the consumer with no mounted view, so it also
        # leaves every channel-layer group it holds. This matters inside a
        # mount_batch, where the socket stays open: the post-mount object-permission
        # refusal runs after ``on_view_mounted`` joined the view's groups.
        await self._leave_view_groups()
        # #1922 / #291: gate the close on the batch flag for ALL verdicts so a
        # single denied/redirected view inside a shared-socket mount_batch does
        # not kill the sibling mounts. The denial itself is already enforced
        # upstream (verdict frame sent + view_instance cleared).
        if not self.mounting_in_batch:
            await consumer.close(code=4403)

    @property
    def capture_document_title(self) -> bool:
        """True while the consumer mounts a ``live_redirect`` destination
        (#3036), so the runtime records the page's document ``<title>``."""
        return bool(getattr(self._consumer, "_live_redirect_mounting", False))

    async def on_mount_failed(self, view: Any) -> None:
        """Stop the tick task ``on_view_mounted`` started for a view whose
        mount then failed (#3027). Only a task still ticking for THIS view is
        cancelled; a later mount on the socket owns its own task."""
        consumer = self._consumer
        task = getattr(consumer, "_tick_task", None)
        if task is None or task.done():
            return
        ticking_for = getattr(task, "_djust_tick_view", None)
        if ticking_for is not None and ticking_for is not view:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected: we just cancelled the tick task and only wait for it to
            # unwind.
            pass
        except Exception as exc:  # noqa: BLE001 — the loop logs its own errors
            from ._exposure_diagnostics import log_failure_for

            log_failure_for(
                logger,
                (view,),
                exc,
                "tick task ended with an error after a failed mount",
                level="debug",
                traceback=True,
            )
        if getattr(consumer, "_tick_task", None) is task:
            consumer._tick_task = None

    async def _leave_view_groups(self) -> None:
        """Leave the view / presence / db_notify groups ``on_view_mounted`` joined.

        Resets the consumer's group attributes so ``disconnect`` does not
        discard them a second time. Discard failures are logged, never raised.
        """
        consumer = self._consumer
        groups: List[str] = []
        for attr in ("_view_group", "_presence_group", "_presence_scope_group"):
            group = getattr(consumer, attr, None)
            if isinstance(group, str) and group:
                groups.append(group)
                setattr(consumer, attr, None)
        channels = getattr(consumer, "_db_notify_channels", None)
        if isinstance(channels, set) and channels:
            groups.extend(f"djust_db_notify_{ch}" for ch in channels)
            consumer._db_notify_channels = set()
        scoped = getattr(consumer, "_push_scope_groups", None)
        if isinstance(scoped, dict) and scoped:
            groups.extend(scoped.values())
            consumer._push_scope_groups = {}
        channel_layer = getattr(consumer, "channel_layer", None)
        if channel_layer is None:
            return
        for group in groups:
            try:
                await channel_layer.group_discard(group, consumer.channel_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("Error leaving channel group %s: %s", group, e)

    @property
    def mounting_in_batch(self) -> bool:
        """Expose the consumer's ``_mounting_in_batch`` flag (#1915, #291)."""
        return bool(getattr(self._consumer, "_mounting_in_batch", False))


class SSESessionTransport:
    """Transport adapter wrapping ``SSESession``.

    Frames are pushed onto the session queue; the SSE stream generator
    drains them and writes ``data:`` lines to the client.
    """

    def __init__(self, session: Any):
        self._session = session

    @property
    def session_id(self) -> str:
        return str(self._session.session_id)

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

    async def on_view_mounted(self, view_instance: Any) -> None:
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
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """No-op for SSE (#1907).

        SSE has no debug-panel ``full_html_update`` signal subscriber and the legacy
        SSE render path emitted neither the DJE-053 warning nor the signal, so this
        WS-scoped fold is a no-op on SSE — zero SSE behavior change. (The DJE-053
        warning is a developer diagnostic for the WS VDOM-baseline-loss case; SSE's
        own render path retains its existing logging.) ``context`` (#1908) is ignored
        — the SSE path never built a context snapshot."""
        return None

    def on_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: Optional[str],
        event_ref: Optional[int] = None,
    ) -> None:
        """No-op for SSE (#1908).

        The DEBUG event-render residuals (``_debug`` panel payload, top-level
        ``timing`` / ``performance``, the ``_current_event_*`` consumer attrs) are a
        WS debug-panel surface; the legacy SSE event path never carried them, so this
        fold is a no-op on SSE — zero SSE behavior change."""
        return None

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any) -> AsyncIterator[None]:
        """Serialize event/results with SSE page replacement."""
        async with self._session._render_lock:
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

    async def explicit_event_request(self, view: Any) -> Any:
        """Use the current owner-checked POST; never fall back to mount auth."""
        request = getattr(self._session, "_event_request", None)
        self._session._event_request = None
        return request

    # ------------------------------------------------------------------ #
    # Mount hooks (ADR-022 Iter 3 Phase 3.2 — DORMANT, #1915)
    # SSE implementations: no-op / raw / refuse. SSE has no consumer to
    # back-reference, no actor channel, no sticky/live_redirect surface, and
    # no persistent socket to close — so these preserve SSE behaviour exactly.
    # ------------------------------------------------------------------ #

    def on_view_instantiated(self, view: Any) -> None:
        """No-op for SSE (#1915, Finding B).

        SSE has no WS consumer to back-reference; the SSE-transport identity
        stamp (``_sse_session_id`` / ``_sse_session`` / query string) already
        lands in :meth:`on_view_mounted`. Nothing to do here."""
        return None

    def uses_actors_for_mount(self, view: Any) -> bool:
        """SSE never mounts through actors (#1915, Finding D).

        SSE has no bidirectional actor channel and ``dispatch_mount`` refuses a
        ``use_actors`` view over SSE outright (runtime.py:1175). Returning
        ``False`` keeps that refusal as the only SSE behaviour for actor views."""
        return False

    async def dispatch_actor_mount(self, view: Any, data: Dict[str, Any]) -> Any:
        """Never called on SSE (``uses_actors_for_mount`` is ``False``) — guard with a raise."""
        raise NotImplementedError("Actor mounts are WS-only; SSE refuses use_actors mounts.")

    def next_mount_version(self, html: Optional[str], rust_version: int = 1) -> int:
        """SSE stamps the raw Rust ``render_with_diff()`` version on its mount frame.

        ADR-022 Iter 3 Phase 3.3a (#1917, Finding C). SSE has no consumer-owned
        counter (the #1788 unification never reached SSE — see
        :meth:`next_client_version`), so the mount frame keeps the raw Rust
        ``render_with_diff()`` version. Phase 3.3a wires ``dispatch_mount`` to
        route the mount version through this hook; SSE returns the ``rust_version``
        the runtime computed inline (the value it stamped before the wiring), so
        SSE clients stay calibrated to the Rust counter exactly as before. ``html``
        is unused (mirrors :meth:`next_client_version`, which returns its
        ``rust_version`` unchanged for the same reason).
        """
        return rust_version

    async def on_mount_render_ready(self, view: Any, html: Optional[str]) -> Optional[str]:
        """No-op for SSE (#1915, Finding B residual).

        SSE has no sticky / ``live_redirect`` surface, so there is no survivor
        set to reconcile and no ``sticky_hold`` frame to emit. Returns ``html``
        unchanged."""
        return html

    async def finalize_mount_auth(self, view: Any, verdict: Any) -> None:
        """No-op for SSE (#1915, Finding E).

        SSE has no persistent socket to drop on a blocking mount-auth verdict —
        the verdict frame (error / navigate) the runtime already sent IS the
        SSE-side finalization. Nothing further to do."""
        return None

    @property
    def mounting_in_batch(self) -> bool:
        """SSE never mounts in a shared-socket batch (#1915, #291). Always ``False``."""
        return False


# ------------------------------------------------------------------ #
# ViewRuntime
# ------------------------------------------------------------------ #


# --------------------------------------------------------------------------- #
# ADR-032 — component-scoped rendering (#2917)
# --------------------------------------------------------------------------- #

#: ``LiveComponent._bind`` stores a view's bound component under this
#: prefix + the attribute name (``_component_nav``); that is the key the
#: change-detection snapshot sees when only the component's State changed.
_COMPONENT_SLOT_PREFIX = "_component_"

_TEMPLATE_TOKEN_RE = re.compile(r"{{.*?}}|{%.*?%}|{#.*?#}", re.S)
_UNRESOLVED_TEMPLATE_RE = re.compile(r"{%\s*(include|extends)\b")


def _scoped_component_for(view: Any, changed_keys: Optional[Any]) -> Optional[Any]:
    """ADR-032 D1(a)(c): the ONE assign the handler changed is a bound
    component's slot, and that component declares a template.

    Returns the :class:`~djust.components.base.BoundComponent`, or ``None``
    when the event changed nothing, several things, a plain assign, an
    instance component (D7 — no per-view slot), or a component without a
    template (``{{ nav }}`` is then its dict repr, not markup).
    """
    if not changed_keys or len(changed_keys) != 1:
        return None
    key = next(iter(changed_keys))
    if not isinstance(key, str) or not key.startswith(_COMPONENT_SLOT_PREFIX):
        return None
    name = key[len(_COMPONENT_SLOT_PREFIX) :]
    registry = getattr(view, "_components", None)
    component = registry.get(name) if isinstance(registry, dict) else None
    if component is None:
        return None
    from .components.base import BoundComponent

    if not isinstance(component, BoundComponent) or component.component_id != name:
        return None
    if getattr(component._descriptor, "_descriptor_storage_key", None) != key:
        return None
    if not (component.template or component.template_name):
        return None
    return component


def _template_is_component_opaque(source: str, name: str) -> bool:
    """ADR-032 D2 on a template SOURCE: every reference to ``name`` is the bare
    ``{{ name }}`` — never ``name.active``, ``name|length``, ``{% for x in
    name %}``, ``{% with name.state as s %}`` — and no ``{% include %}`` /
    ``{% extends %}`` is left that could read it elsewhere. Any other read of
    the component's state would have to re-render too, so such a template
    never takes the scoped path. Conservative by construction: the name
    inside a string literal or a comment tag also says "not opaque".
    """
    word = re.compile(rf"(?<![\w.]){re.escape(name)}(?!\w)")
    for match in _TEMPLATE_TOKEN_RE.finditer(source):
        token = match.group(0)
        if not word.search(token):
            continue
        if token.startswith("{{") and token[2:-2].strip() == name:
            continue
        return False
    return _UNRESOLVED_TEMPLATE_RE.search(source) is None


def _context_changed_besides(view: Any, context: Dict[str, Any], name: str) -> Optional[str]:
    """ADR-032 D1, verified rather than predicted (review of #2920 🔴1).

    The template is not the only reader of a component's state: a view
    ``@property``, a ``get_context_data`` override or a dep-less ``@computed``
    can derive a value from ``self.nav.active`` that the template reads under
    another name. Such a value lands in the render CONTEXT, so the context is
    compared, key by key, against what the last render synced — the same
    three-layer rule ``_sync_state_to_rust`` applies (identity, value for
    immutables, structural fingerprint for containers; #2664) — and any key
    other than the component's own that changed, appeared or vanished names
    the reason the scoped path is not exact. Returns that key, or ``None``
    when only the component changed.
    """
    from .change_detection import deep_fingerprint, fingerprints_by_content
    from .mixins.rust_bridge import _FRAMEWORK_KEYS, _IMMUTABLE_TYPES_FOR_SYNC

    prev_refs = getattr(view, "_prev_context_refs", None)
    prev_immutables = getattr(view, "_prev_context_immutables", None)
    prev_fps = getattr(view, "_prev_context_fingerprints", None)
    if prev_refs is None or prev_immutables is None or prev_fps is None:
        return "<no previous render>"
    # The same keys the sync leaves untracked or never diffs: context-
    # processor values, the request handle (re-assigned per event) and the
    # framework keys (``csrf_token``, the settings-constant date formats …).
    from .mixins.context import _request_scoped_keys

    skip = _request_scoped_keys(view)
    skip.update(_FRAMEWORK_KEYS)
    skip.add(name)
    for key, value in context.items():
        if key in skip:
            continue
        if isinstance(value, (dict, list, tuple)) or fingerprints_by_content(value):
            if key not in prev_fps or prev_fps[key] != deep_fingerprint(value)[0]:
                return str(key)
        elif isinstance(value, _IMMUTABLE_TYPES_FOR_SYNC):
            if key not in prev_immutables or prev_immutables[key] != value:
                return str(key)
        elif prev_refs.get(key) != id(value):
            return str(key)
    for key in prev_refs:
        if key not in context and key not in skip:
            return str(key)
    return None


def _view_is_component_opaque(view: Any, name: str) -> bool:
    """D2, decided once per (view class, template source, component) and
    cached on the class. Also false when a memoised ``@computed`` on the
    class lists the component among its dependencies — its value would
    change with the component's state and the template may read it."""
    cls = type(view)
    cache = cls.__dict__.get("_djust_component_opaque")
    if cache is None:
        cache = {}
        setattr(cls, "_djust_component_opaque", cache)
    # A file template (``template_name``) is resolved through the loader —
    # and the Rust inheritance resolver when it extends — on every
    # ``get_template()``; outside DEBUG the files do not change under a
    # running process, so the verdict is keyed by name and the source is
    # read once per class. In DEBUG (hot reload) the source is re-read and
    # hashed every time, so an edit that adds ``{{ nav.active }}`` is seen.
    from django.conf import settings as _dj_settings

    template_name = getattr(view, "template_name", None)
    inline = getattr(view, "template", None)
    key: Tuple[str, str]
    source: Any = None
    if isinstance(template_name, str) and not isinstance(inline, str) and not _dj_settings.DEBUG:
        key = (f"name:{template_name}", name)
        verdict = cache.get(key)
        if verdict is not None:
            return bool(verdict)
    try:
        source = view.get_template()
    except Exception:  # noqa: BLE001 — no template, no scoped path
        return False
    if not isinstance(source, str):
        return False
    if source is not None and not (
        isinstance(template_name, str) and not isinstance(inline, str) and not _dj_settings.DEBUG
    ):
        key = (
            hashlib.sha1(
                source.encode("utf-8", "surrogatepass"), usedforsecurity=False
            ).hexdigest(),
            name,
        )
    verdict = cache.get(key)
    if verdict is None:
        verdict = _template_is_component_opaque(source, name)
        if verdict:
            for attr in dir(cls):
                prop = getattr(cls, attr, None)
                deps = getattr(prop, "_computed_deps", None)
                if deps and name in deps:
                    verdict = False
                    break
        if len(cache) > 256:
            cache.clear()
        cache[key] = verdict
    return verdict


def _reconstruct_explicit_page_shell(view: Any, request: Any) -> None:
    """Rebuild an explicit root's page-shell children on a live mount (E3-6).

    Only views whose page shell comes from ``template_name`` inheritance have
    children outside ``dj-root``. The full render registers them (restoring
    their declared server state through the child adapter); the caller's
    root render then reuses them rather than remounting.
    """
    if not getattr(view, "template_name", None) or getattr(view, "template", None):
        return
    from ._child_rendering import render_view_full_template

    view.get_template()
    if getattr(view, "_full_template", None) is None:
        return
    render_view_full_template(view, request)
    view._cached_context = None


def _explicit_descendant(root: Any, view_id: Any) -> Any:
    """Resolve a nested explicit child by ``view_id`` (ADR-038 E3-4).

    Direct children keep their existing lookup. Below them, an explicit root
    routes to a descendant only through the server-owned registry chain, and
    only when exactly one owned explicit descendant carries that id; an
    ambiguous or unknown id is not routed. Legacy roots are unchanged.
    """
    from ._exposure import uses_legacy_exposure

    if type(view_id) is not str or uses_legacy_exposure(root):
        return None
    matches = []
    pending = [root]
    seen: set = set()
    while pending and len(seen) < 257:
        parent = pending.pop()
        if id(parent) in seen:
            continue
        seen.add(id(parent))
        registry = getattr(parent, "_child_views", None)
        if type(registry) is not dict:
            continue
        for slot, child in tuple(registry.items()):
            if (
                getattr(child, "_parent_view", None) is not parent
                or getattr(child, "_view_id", None) != slot
                or getattr(child, "_djust_child_disposed", False)
                or uses_legacy_exposure(child)
            ):
                continue
            if parent is not root and slot == view_id:
                matches.append(child)
            pending.append(child)
    return matches[0] if len(matches) == 1 else None


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
        renderer_factory: Optional[Any] = None,
    ) -> None:
        self.transport = transport
        self.view_instance: Optional[Any] = None
        self.scope = scope
        self._rate_limiter = rate_limiter or ConnectionRateLimiter()
        # Stashed mount-failure error frame (set by _instantiate_view, awaited +
        # cleared by dispatch_mount). See _instantiate_view for the #1919 rationale.
        self._instantiate_error_frame: Optional[Dict[str, Any]] = None
        # NOTE: the runtime does NOT own a render lock. Render serialization is
        # consumer-owned (``LiveViewConsumer._render_lock``, websocket.py:619) and
        # SHARED with the WS-only tick / server-push / db-notify loops; a
        # runtime-local lock would be a different object and could not serialize
        # against those (#560). The transport borrows the consumer's existing lock
        # via ``transport.event_context(view)`` (ADR-022 Iter 2 Phase 2.3a, #1899).
        # ADR-019 LVN-I PR-2: Renderer factory plumbed through. Stored
        # for PR-3 (handshake) to set based on ``?platform=`` selection;
        # ``None`` means "use the default ``HtmlRenderer``" which the
        # dispatch site (``TemplateMixin.render_with_diff``) already
        # constructs inline. Type kept ``Any`` to avoid circular import
        # with ``djust.renderers``; runtime use-site will cast.
        self.renderer_factory = renderer_factory
        self._explicit_mount_binding: Any = None
        # Explicit request/auth/save state is per turn, including the work
        # before entering a transport's render lock.
        self._explicit_event_lock = asyncio.Lock()
        # ADR-036: only transport-local advertisement state, never application
        # state or a cached owner/contract. A later all-legacy render must clear
        # a previously advertised strict manifest.
        self._parameter_contracts_active = False
        self._parameter_contract_view: Optional[str] = None
        # #3036: the destination page's <title>, recorded after the mount
        # render when the transport asks for it (``capture_document_title``,
        # WS: during a live_redirect mount). See ``navigation_title``.
        self.mount_document_title: Optional[str] = None

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

    @_runtime_diagnostic_scope
    async def dispatch_message(self, data: Dict[str, Any]) -> None:
        """Route an inbound frame to the appropriate handler by ``type``.

        Unknown types emit a structured error envelope but never raise —
        this is the forward-compat seam for transports that may receive
        future frame types (uploads, presence) the runtime doesn't yet
        own.
        """
        from ._exposure_diagnostics import diagnostics_allowed

        try:
            msg_type = data.get("type")
            if msg_type == "mount":
                await self.dispatch_mount(data)
            elif msg_type == "event":
                await self.dispatch_event(data)
            elif msg_type == "url_change":
                await self.dispatch_url_change(data)
            else:
                await self.transport.send_error(f"Unknown message type: {msg_type}")
        except Exception as exc:
            if diagnostics_allowed():
                # Legacy callers keep their existing transport-specific catches.
                raise
            response = handle_exception(exc, expose_details=False, logger=logger)
            if isinstance(data, dict) and data.get("type") == "event":
                response["source"] = "event"
                raw_ref = data.get("ref")
                if type(raw_ref) is int or type(raw_ref) is float:
                    try:
                        response["ref"] = int(raw_ref)
                    except (ValueError, OverflowError):
                        # Match existing numeric refs without letting a forged
                        # NaN/infinity defeat the protected error boundary.
                        pass
            try:
                await self.transport.send(response)
            except Exception:  # noqa: BLE001 — do not leak delivery errors to outer handlers
                logger.error("Protected error response could not be delivered")
                try:
                    await self.transport.close(code=1011)
                except Exception:  # noqa: BLE001 — even close failures must be value-free
                    logger.error("Protected transport could not be closed")

    # ------------------------------------------------------------------ #
    # Mount dispatch (used by SSE in this PR; WS still uses handle_mount)
    # ------------------------------------------------------------------ #

    @_mount_tenant_scope
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

        self.mount_document_title = None
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
            # _instantiate_view stashed the error frame (#1919): AWAIT-send it HERE
            # so the send lands inside this dispatch (and, under the mount_batch
            # collector, inside the CORRECT _mount_one window — a fire-and-forget
            # send leaked a failed view's error into the next survivor's collector).
            # getattr-guarded: a MagicMock-stubbed _instantiate_view that returns a
            # view never sets it, and a stub returning None without setting it sends
            # nothing (the test contract — the stubs assert on dispatch, not frames).
            error_frame = getattr(self, "_instantiate_error_frame", None)
            if error_frame is not None:
                await self.transport.send(error_frame)
                self._instantiate_error_frame = None
            return

        # Actor renderers retain their own context/state outside the explicit
        # projections. Refuse before lifecycle/transport registration, not just
        # on the first event. Do not close a shared mount_batch socket.
        from ._exposure import uses_legacy_exposure

        if getattr(view_instance, "use_actors", False) and not uses_legacy_exposure(view_instance):
            self.view_instance = None
            await self.transport.send_error(
                "Explicit actor mounts are not yet supported", error_type="mount_error"
            )
            return

        from ._exposure_diagnostics import restrict_diagnostics

        restrict_diagnostics(view_instance)

        # ---- Transport back-references on the freshly-instantiated view ----
        # ADR-022 Iter 3 Phase 3.3a (#1917, Finding B). Wire the
        # ``on_view_instantiated`` hook at the SAME point the bespoke WS
        # ``handle_mount`` stamps its consumer back-refs (websocket.py:2128 ff):
        # IMMEDIATELY after instantiation, BEFORE the actor branch / auth / mount /
        # render. WS: stamps ``_ws_consumer`` / ``_push_events_flush_callback`` /
        # observability ``register_view`` / validated handshake host. SSE: no-op.
        # getattr-guarded so duck-typed test transport fakes that predate this
        # Protocol method (and the default-bearing Protocol itself) keep working.
        on_view_instantiated = getattr(self.transport, "on_view_instantiated", None)
        if on_view_instantiated is not None:
            on_view_instantiated(view_instance)

        # ---- use_actors transport gate (#1240 / ADR-016; ADR-022 Iter 3 Phase
        # 3.3a Finding D) ----
        # Phase 3.3a wires ``uses_actors_for_mount`` so a WS actor view is no
        # longer REFUSED — it is mounted through the actor system at the render
        # step below (``dispatch_actor_mount``), WS-verbatim. SSE has no
        # bidirectional actor channel (``uses_actors_for_mount`` → False), so an
        # ``use_actors`` view over SSE still gets the structured refusal here
        # rather than partially mounting and failing downstream with an opaque
        # AttributeError. getattr-guarded; a transport without the hook (a bare
        # test fake) falls back to the legacy unconditional refusal.
        if getattr(view_instance, "use_actors", False):
            uses_actors_for_mount = getattr(self.transport, "uses_actors_for_mount", None)
            actor_mount_ok = (
                uses_actors_for_mount(view_instance) if uses_actors_for_mount else False
            )
            if not actor_mount_ok:
                await self.transport.send_error(
                    "use_actors is not supported over SSE; mount over WebSocket instead",
                    error_type="mount_error",
                    view_class=view_path,
                )
                return

        self.view_instance = view_instance

        # ADR-019 LVN-I: if the handshake selected a renderer (factory
        # passed in via __init__), bind it to the view so
        # TemplateMixin.render_with_diff can dispatch through it
        # instead of always constructing HtmlRenderer inline.
        if self.renderer_factory is not None:
            view_instance._djust_renderer = self.renderer_factory(view_instance)

        # Stash the client-supplied dotted view path from the mount frame so the
        # transport hook (WS ``on_view_mounted``) can derive the server-push channel
        # group name from the SAME string external broadcasters use
        # (``view_group_name(data["view"])`` — websocket.py:2172-2174 / push.py). It
        # MUST be the frame value, NOT ``module.qualname`` of the instance, because
        # ``apush_to_view("app.MyView")`` string-mangles whatever the caller passes
        # and the two must match byte-for-byte for delivery (#1919, Finding B).
        view_instance._djust_mount_view_path = view_path

        # Stash transport identity on the view (used by VDOM caching).
        view_instance._websocket_session_id = self.session_id
        view_instance._websocket_path = page_url
        view_instance._websocket_query_string = ""

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
                expose_details=_diagnostics_policy_allows(view_instance),
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
        # Transport-agnostic redirect handling: the runtime emits the navigate
        # frame, then the ``finalize_mount_auth`` hook (#1917, Finding E) applies
        # the transport-level ``close(4403)`` — WS gated on
        # ``not _mounting_in_batch`` (#291 / #1780: a batched login-required view
        # reports as ``navigate[]`` and closing would kill sibling mounts);
        # SSE no-op. The ``"hook_redirect"`` verdict honors the SAME batch gate as
        # the auth ``"redirect"`` verdict (websocket.py:2389-2400). The hook is
        # called BEFORE clearing ``view_instance`` so the WS impl's view arg is
        # valid (it routes the close through the consumer regardless).
        from .hooks import run_on_mount_hooks

        hook_redirect = await sync_to_async(run_on_mount_hooks)(view_instance, request, **params)
        if hook_redirect:
            await self.transport.send({"type": "navigate", "to": hook_redirect})
            await self._finalize_mount_auth("hook_redirect")
            self.view_instance = None
            return
        # ---- End on_mount hooks ----

        # Transport-specific identity + post-mount setup hook (#1887 / #1919):
        # SSE stamps _sse_session_id / _sse_session + the real query string here so
        # the converged runtime mount preserves everything legacy _sse_mount_view
        # exposed. WS performs its post-mount channel-layer wiring (view/presence/
        # db_notify group_add), tick-task start, use_actors flag, and real-scope
        # path/query-string stamps (ADR-022 Iter 3 Phase 3.3b, Finding B residual).
        # Async (the WS impl awaits group_add). getattr-guarded + awaitable-guarded
        # so duck-typed test transport fakes that predate this Protocol method (or
        # still expose a SYNC no-op) keep working.
        #
        # Runs only once the pre-mount auth sequence and the on_mount hooks have
        # admitted the view: a view that is redirected or refused never joins the
        # view / presence / db_notify groups, never starts a tick task, and never
        # becomes the SSE session's mounted view. Still BEFORE state restore and
        # mount(), so the db_notify join reads ``_listen_channels`` at the same
        # point it always has.
        on_view_mounted = getattr(self.transport, "on_view_mounted", None)
        if on_view_mounted is not None:
            result = on_view_mounted(view_instance)
            if inspect.isawaitable(result):
                await result

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
        legacy_exposure = uses_legacy_exposure(view_instance)
        # Explicit server persistence is independent of client snapshot opt-in.
        # Until the explicit signed-client adapter lands, never use either
        # legacy snapshot restore mechanism for an explicit view.
        opt_in = opt_in and legacy_exposure
        session = getattr(request, "session", None)
        if opt_in and session is not None:
            view_key = f"liveview_{page_url}"
            try:
                saved_state = await session.aget(view_key, {})
            except Exception:  # noqa: BLE001 — session backend may be absent
                saved_state = {}
            if saved_state:
                from .security import safe_setattr
                from .serialization import decode_state_roundtrip

                # #2252: the per-event session save tagged every Decimal
                # (``normalize_django_value(..., state_roundtrip=True)``).
                for key, value in decode_state_roundtrip(saved_state).items():
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
                from .components.base import is_session_component

                component_state = await session.aget(f"{view_key}_components", {})
                for key, state in component_state.items():
                    component = getattr(view_instance, key, None)
                    if component is not None and is_session_component(component):
                        await sync_to_async(view_instance._restore_component_state)(
                            component, state
                        )
                        from .components._interactive import DropdownMenu, DropdownMenuCollection

                        if isinstance(component, DropdownMenu):
                            component._renew_observation_lifetime()
                        elif isinstance(component, DropdownMenuCollection):
                            component._renew_observation_lifetimes()

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
                        # #2252: un-tag Decimals BEFORE handing the state to
                        # ``_restore_snapshot``, which is a documented
                        # subclass-override point — a user override must not
                        # have to know about the tag shape.
                        from .serialization import decode_state_roundtrip

                        state_dict = decode_state_roundtrip(state_dict)
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

        # #1977 — re-sync the diff baseline to the client's LIVE DOM on a
        # reconnect / state-restore mount. On a resume the fresh view's Rust diff
        # baseline is primed from a render that does NOT match the client's
        # pre-disconnect DOM (which already reflects the restored/filtered state);
        # diffing the FIRST post-restore EVENT against that stale baseline lands
        # SetText patches on the wrong node (often a bare ``#text`` node) →
        # ``2/N patches failed`` → an ``html_recovery`` reload/flicker. Forcing the
        # first post-restore render to emit a FULL-HTML frame makes the client
        # morph wholesale and re-primes the Rust baseline to the live DOM, so no
        # stale-baseline diff can ever reach the client.
        #
        # RENDER-TIMING (traced, load-bearing): the mount-time ``render_with_diff``
        # (~line 2375 / for actor mounts the actor render) READS ``_force_full_html``
        # but never RESETS it — every reset lives in the transport-level EVENT /
        # url_change dispatch (``_render_and_send`` runtime.py:4093-4094,
        # ``dispatch_url_change`` runtime.py:3725-3726, and the WS twins
        # websocket.py:1650-1651 / 4115-4116). On a resume the mount HTML is
        # dropped (``skip_html_for_resume`` = ``mounted_from_restore and
        # has_prerendered``), so the flag SURVIVES the mount frame and is consumed
        # by the FIRST post-restore event render — which discards patches and sends
        # a full ``html_update``. The flag is snapshot-excluded
        # (``_FRAMEWORK_INTERNAL_ATTRS``, live_view.py:143) so it never leaks into a
        # saved snapshot.
        #
        # Parallel-path (#1646): this ONE guard covers BOTH restore mechanisms —
        # session-saved-state (~2104) and signed-snapshot HMAC (~2154) — because
        # both funnel to ``mounted_from_restore`` here; and — since WS
        # ``handle_mount`` is a thin shim to ``dispatch_mount`` (websocket.py:2209)
        # — it covers WS + SSE + runtime. The HTTP GET path renders the full page
        # and primes its baseline against the same served state (request.py:276-282,
        # no cross-event stale baseline); the HTTP POST fallback re-restores +
        # re-renders atomically in each stateless request — neither has a persistent
        # mount baseline that a later event diffs against, so neither needs this.
        if mounted_from_restore:
            view_instance._force_full_html = True

        # ADR-035: a view that looks its object up from the route gets only the
        # kwargs of its own route, never client params. Bound after both restore
        # mechanisms, so restored state cannot supply it, and before mount().
        bind_route = getattr(view_instance, "_djust_bind_route_kwargs", None)
        if callable(bind_route):
            bind_route(self._own_route_kwargs(view_instance, page_url))

        if not mounted_from_restore:
            try:
                await sync_to_async(view_instance.mount)(request, **mount_kwargs)
                if not legacy_exposure:
                    from ._exposure_sessions import load_server_state
                    from .security import safe_setattr

                    if callable(getattr(view_instance, "resolve_tenant", None)):
                        request.tenant = getattr(view_instance, "_tenant", None)
                    restored = await sync_to_async(load_server_state)(view_instance, request)
                    from django.conf import settings

                    incoming = data.get("state_snapshot")
                    if (
                        getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
                        and type(incoming) is dict
                        and incoming.get("view_slug") == view_path
                        and await sync_to_async(view_instance._should_restore_snapshot)(request)
                    ):
                        from ._exposure_snapshots import snapshot_codec

                        codec = await sync_to_async(snapshot_codec)(view_instance, request)
                        client_values = codec.restore(incoming.get("state_json")) if codec else None
                        if client_values is not None:
                            # Server/client fields are disjoint by declaration;
                            # both dictionaries are completely validated first.
                            restored = {**(restored or {}), **client_values}
                    if restored is not None:
                        for key, value in restored.items():
                            safe_setattr(
                                view_instance,
                                key,
                                value,
                                allow_private=False,
                                raise_on_blocked=True,
                            )
                        mounted_from_restore = True
                        view_instance._force_full_html = True
            except Exception as exc:
                response = handle_exception(
                    exc,
                    error_type="mount",
                    view_class=view_path,
                    logger=logger,
                    log_message=f"Error in {sanitize_for_log(view_path)}.mount()",
                    expose_details=_diagnostics_policy_allows(view_instance),
                )
                await self.transport.send(response)
                await self._on_mount_failed(view_instance)
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
            # ADR-022 Iter 3 Phase 3.3b (#1919, Finding E): the runtime sent the
            # error frame; finalize_mount_auth adds the transport-level
            # ``close(4403)`` the bespoke WS handle_mount performed UNCONDITIONALLY
            # on an object-perm denial (websocket.py:2622 — matching the
            # PermissionDenied verdict, NOT the batch-gated redirect verdicts). SSE
            # no-op. Without this the WS socket stayed open after a denied mount
            # (the test_object_permission_denied_mount_refused net).
            await self._finalize_mount_auth("permission_denied")
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
        if not legacy_exposure:
            from ._exposure_sessions import request_binding

            if callable(getattr(view_instance, "resolve_tenant", None)):
                request.tenant = getattr(view_instance, "_tenant", None)
            self._explicit_mount_binding = await sync_to_async(request_binding)(request)

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
                expose_details=_diagnostics_policy_allows(view_instance),
            )
            await self.transport.send(response)
            await self._on_mount_failed(view_instance)
            return

        # ---- Initial render ----
        # ADR-022 Iter 3 Phase 3.3a (#1917, Finding D): a WS ``use_actors`` view
        # renders through the actor system instead of the Rust render path, exactly
        # as the bespoke ``handle_mount`` does (websocket.py:2691-2706 — actor mount
        # runs AFTER auth + mount() + handle_params, using the fully-initialized
        # view). ``uses_actors_for_mount`` already passed the top gate for this
        # branch to be reachable (SSE returns False and was refused above).
        rust_version = 1
        actor_mounted = False
        if getattr(view_instance, "use_actors", False):
            uses_actors_for_mount = getattr(self.transport, "uses_actors_for_mount", None)
            if uses_actors_for_mount and uses_actors_for_mount(view_instance):
                try:
                    result = await self.transport.dispatch_actor_mount(view_instance, data)
                    # The actor render is authoritative for its BYTES, but it goes
                    # through the same normalize + dj-root extraction as the
                    # non-actor frame below (#2599 review): the client does
                    # `container.innerHTML = html`, so a verbatim `<div dj-root>`
                    # wrapper nested a second dj-root and put every patch one
                    # level off. (Pre-#2599 the actor html was an empty document,
                    # so the mismatch was invisible.)
                    html = result["html"] if isinstance(result, dict) else result.get("html")
                    rust_version = result.get("version", 1) if isinstance(result, dict) else 1
                    if hasattr(view_instance, "_strip_comments_and_whitespace"):
                        html = await sync_to_async(view_instance._strip_comments_and_whitespace)(
                            html
                        )
                    if hasattr(view_instance, "_extract_liveview_content"):
                        html = await sync_to_async(view_instance._extract_liveview_content)(html)
                    actor_mounted = True
                except Exception as exc:
                    response = handle_exception(
                        exc,
                        error_type="render",
                        view_class=view_path,
                        logger=logger,
                        log_message=f"Error mounting {sanitize_for_log(view_path)} via actor",
                        expose_details=_diagnostics_policy_allows(view_instance),
                    )
                    await self.transport.send(response)
                    await self._on_mount_failed(view_instance)
                    return

        if not actor_mounted:
            try:
                if hasattr(view_instance, "_initialize_rust_view"):
                    await sync_to_async(view_instance._initialize_rust_view)(request)
                if hasattr(view_instance, "_sync_state_to_rust"):
                    await sync_to_async(view_instance._sync_state_to_rust)()
                # ADR-019 LVN: capture patches (was discarded as ``_patches``) — for
                # native renderers (NativeRenderer) ``html`` is empty and the wire
                # payload is the patch list, shipped on the mount frame below so the
                # native client can bootstrap its widget tree on connect.
                from ._child_rendering import render_view_with_diff

                if not uses_legacy_exposure(view_instance):
                    # ADR-038 E3-6: the page shell stays in the browser across
                    # a (re)connect, but this is a new root instance. Render the
                    # full page once, as the HTTP GET does, so explicit
                    # page-shell children are reconstructed from their stored
                    # state and registered for routing.
                    await sync_to_async(_reconstruct_explicit_page_shell)(view_instance, request)
                html, render_patches, rust_version = await sync_to_async(render_view_with_diff)(
                    view_instance
                )
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
                    expose_details=_diagnostics_policy_allows(view_instance),
                )
                await self.transport.send(response)
                await self._on_mount_failed(view_instance)
                return

        # #3036: a live_redirect mount records the destination page's
        # <title>; the WS consumer sends it when the view queued none.
        # Skipped when the view already queued its own title, which wins.
        if getattr(self.transport, "capture_document_title", False) and not _queued_title(
            view_instance
        ):
            self.mount_document_title = await sync_to_async(navigation_title)(view_instance)

        # ---- Post-render mount hook (#1917, Finding B residual) ----
        # ``on_mount_render_ready`` runs AFTER the render produced ``html`` but
        # BEFORE the mount frame is sent, so a transport can adjust the outgoing
        # HTML and/or emit pre-mount frames at the WS-faithful position
        # (websocket.py:2836-2903 emits the ``sticky_hold`` frame here, BEFORE the
        # mount frame — the ordering the client's reattachStickyAfterMount needs).
        # WS: sticky preservation + ``sticky_hold``; SSE: returns ``html``
        # unchanged. getattr-guarded for duck-typed test fakes.
        on_mount_render_ready = getattr(self.transport, "on_mount_render_ready", None)
        if on_mount_render_ready is not None:
            html = await on_mount_render_ready(view_instance, html)

        if not await self._persist_explicit_children_after_event(view_instance, request=request):
            return

        # ---- Mount-frame wire version (#1917, Finding C) ----
        # ``next_mount_version`` stamps the baseline the client calibrates to. WS:
        # the consumer-owned monotonic ``_next_version()`` (NO arm — mount has no
        # prior frame to recover to, websocket.py:2746). SSE: the raw Rust
        # ``render_with_diff()`` version (``rust_version``), unchanged. Crucially
        # this is the NO-ARM mount baseline — it does NOT route through the ARMING
        # wire-version helper the event path uses (mount has no prior frame to
        # recover to). getattr-guarded: a transport without the hook falls back to
        # the raw Rust version (the pre-3.3a inline behavior).
        next_mount_version = getattr(self.transport, "next_mount_version", None)
        if next_mount_version is not None:
            version = next_mount_version(html, rust_version)
        else:
            version = rust_version

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
        # Explicit restoration can combine fresh server state and a client
        # snapshot. Cached HTML is not proof that it matches that combination.
        skip_html_for_resume = (
            legacy_exposure
            and bool(mounted_from_restore)
            and bool(has_prerendered)
            and not bool(getattr(view_instance, "_component_bindings", {}))
        )
        # Interactive bindings must reconcile the browser with current server
        # declarations/configuration, including newly added or removed items.
        # A signed historical snapshot is not authority to keep stale controls.
        if html is not None and not skip_html_for_resume:
            mount_msg["html"] = html
            mount_msg["has_ids"] = "dj-id=" in html
            if getattr(view_instance, "_component_bindings", {}):
                # This fresh HTML establishes the current diff baseline; an
                # unchanged native observation must not force another render.
                view_instance._force_full_html = False
        elif skip_html_for_resume:
            logger.info(
                "Runtime: skipping mount HTML for resume of %s — client already has DOM",
                sanitize_for_log(view_path),
            )

        # ADR-019 LVN: for native renderers (NativeRenderer), ``html`` is empty and
        # the wire payload is patches. Ship them in the mount frame so the native
        # client can bootstrap its widget tree on connect (the browser/HTML path
        # needs ``html`` only — patches arrive on subsequent updates). ``render_patches``
        # was captured at the render call above; the actor branch does not set it, so
        # read it via ``locals()`` and only attach when a non-HTML renderer is bound
        # and patches are present + non-empty.
        if getattr(view_instance, "_djust_renderer", None) is not None and locals().get(
            "render_patches"
        ):
            mount_msg["patches"] = locals()["render_patches"]

        # state_snapshot_signed EMIT (ADR-022 Iter 3 Phase 3.1, WS
        # websocket.py:2754-2792). When the view opts in (``enable_state_snapshot``)
        # AND the master switch is on, ship the SIGNED public-state blob on the
        # mount frame so the client can store it verbatim and echo it back on the
        # next back-navigation (the restore path above verifies the HMAC + TTL +
        # slug/sid binding). Non-opt-in views never have state shipped. The
        # signature binds slug + session key (``_django_session_key``, stamped
        # above), so a valid snapshot cannot be replayed across views or sessions.
        # Wrapped so snapshot emission can NEVER break the mount (#1788 posture).
        if not legacy_exposure:
            # Clear stale browser state when capture is disabled or unavailable.
            mount_msg["state_snapshot_signed"] = None
        try:
            from django.conf import settings

            state_master_on = getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
            if (
                state_master_on
                and legacy_exposure
                and getattr(view_instance, "enable_state_snapshot", False)
            ):
                snapshot_fn = getattr(view_instance, "_capture_snapshot_state", None)
                if callable(snapshot_fn):
                    # strict=True: this is the real client-signed persistence
                    # path — reject (not silently dict-ify) any ORM object on
                    # public state so a back-navigation restore can't hand a
                    # handler a plain dict where it expects a Model. See
                    # LiveView._reject_orm_value_in_state_persistence.
                    public_state = await sync_to_async(snapshot_fn)(strict=True)
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
            elif state_master_on and not legacy_exposure:
                from ._exposure_snapshots import snapshot_codec

                try:
                    codec = await sync_to_async(snapshot_codec)(view_instance, request)
                    if codec is not None:
                        mount_msg["state_snapshot_signed"] = await sync_to_async(codec.capture)(
                            view_instance
                        )
                except Exception:
                    # No repr/traceback or legacy fallback: descriptor factories
                    # and codec failures can contain server-only values.
                    logger.warning(
                        "Explicit client snapshot unavailable; cached snapshot invalidated"
                    )
        except Exception as snapshot_exc:  # noqa: BLE001 — snapshot emission must never break mount
            from .live_view import NonPersistableStateError

            if isinstance(snapshot_exc, NonPersistableStateError):
                # DELIBERATE DEBUG-only rejection from
                # LiveView._reject_orm_value_in_state_persistence — the guard
                # exists to fail the mount loudly in development, so it must
                # NOT be downgraded to a log line by the fail-soft posture
                # below (which remains for UNEXPECTED emission errors).
                # Production never raises this (the guard warns + skips
                # there), so this re-raise is DEBUG-only by construction.
                raise
            logger.exception(
                "Failed to emit state_snapshot_signed for %s; proceeding without snapshot",
                sanitize_for_log(view_path),
            )

        # ADR-038 D-b / D-n: value-free service-worker cache signals — an
        # ineligibility marker for explicit pages, an HMAC identity marker the
        # client compares to clear caches on identity change or logout, and
        # the snapshot lifetime when a snapshot is shipped. Never breaks mount.
        try:
            from .security.service_worker import mount_frame_metadata

            mount_msg.update(
                await sync_to_async(mount_frame_metadata)(
                    view_instance, request, mount_msg.get("state_snapshot_signed")
                )
            )
        except Exception:  # noqa: BLE001 — value-free; fail closed on eligibility
            logger.warning("Service-worker cache metadata unavailable for mount")
            mount_msg["sw_cache"] = "no-store"

        # Optional cache_config (mirrors WS consumer)
        cache_config = self._extract_cache_config(view_instance)
        if cache_config:
            mount_msg["cache_config"] = cache_config

        # handler_config (#2656) — @debounce / @throttle metadata for the
        # client-side rate-limit gate in src/05-handler-rate-limit.js. Same
        # transport as cache_config deliberately: the mount frame is
        # CSP-strict (no inline <script>) and survives the #1610 mount morph,
        # unlike the `window.handlerMetadata` script tag, which the client
        # now reads only as an HTTP-path fallback.
        handler_config = self._extract_handler_config(view_instance)
        if handler_config:
            mount_msg["handler_config"] = handler_config

        # ADR-036: public input contracts retain their dispatch owner rather
        # than being merged into the global handler-name rate-limit map.
        from ._parameter_metadata import parameter_contract_manifest

        parameter_contracts = parameter_contract_manifest(view_instance)
        self._parameter_contracts_active = parameter_contracts is not None
        self._parameter_contract_view = view_path
        if parameter_contracts is not None:
            mount_msg["parameter_contracts"] = parameter_contracts

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

    @_runtime_diagnostic_scope
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
        from ._exposure import uses_legacy_exposure

        explicit_request = None
        if self.view_instance is not None and not uses_legacy_exposure(self.view_instance):
            try:
                # Capture the current POST before waiting on another turn's
                # lock. A later POST may replace the SSE session's request slot.
                explicit_request = await self.transport.explicit_event_request(self.view_instance)
            except Exception:
                # The inner fail-closed gate emits a static denial for None.
                explicit_request = None
        tenant = getattr(self.view_instance, "_tenant", None) if self.view_instance else None
        with _tenant_context(tenant):
            await self._dispatch_event_inner(data, explicit_request=explicit_request)

    async def _dispatch_event_inner(
        self, data: Dict[str, Any], *, explicit_request: Any = None
    ) -> None:
        """Event dispatch body (see :meth:`dispatch_event` for the tenant wrapper).

        The handler + render runs inside ``transport.event_context(view)`` (ADR-022
        Iter 2 Phase 2.3a, #1899): on WS this BORROWS the consumer's existing
        ``_render_lock`` + sets ``_processing_user_event`` + the #1677 origin
        channel + observability scopes, so a WS event routed here in the Phase
        2.3b flip serializes against the WS-only tick / server-push / db-notify
        render loops identically (the #560 guard). SSE holds its session lock. The
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
        from ._exposure import uses_legacy_exposure

        recheck = getattr(self.transport, "recheck_event_auth", None)
        if (
            uses_legacy_exposure(self.view_instance)
            and recheck is not None
            and not await recheck(self.view_instance)
        ):
            self.view_instance = None  # unconditional state-clear (#291)
            return

        uses_actors = getattr(self.transport, "uses_actors", None)
        if (
            uses_actors is not None
            and uses_actors(self.view_instance)
            and not self._event_routes_to_sticky_child(data)
        ):
            if not uses_legacy_exposure(self.view_instance):
                self.view_instance = None
                await self.transport.send_error("Explicit actor events are not yet supported")
                await self.transport.close(code=4403)
                return
            # The transport owns actor event_context acquisition. Parse ref /
            # cache id the same way the WS event
            # handler does (websocket.py:3168-3172) so the framed actor result
            # carries the same wire metadata.
            params: Dict[str, Any] = dict(data.get("params") or {})
            event_name = data.get("event")
            raw_ref = data.get("ref")
            event_ref: Optional[int] = int(raw_ref) if isinstance(raw_ref, (int, float)) else None
            cache_request_id = params.get("_cacheRequestId")
            await self.transport.dispatch_actor_event(
                self.view_instance,
                # frame-dynamic: forwarded verbatim like the WS bespoke actor block;
                # the actor handler validates the name downstream (dormant path today)
                event_name,  # type: ignore[arg-type]
                params,
                event_ref=event_ref,
                cache_request_id=cache_request_id,
            )
            return

        explicit_lock = (
            contextlib.nullcontext()
            if uses_legacy_exposure(self.view_instance)
            else self._explicit_event_lock
        )
        async with explicit_lock, self.transport.event_context(self.view_instance):
            if self.view_instance is None:
                return
            if not uses_legacy_exposure(self.view_instance):
                try:
                    from ._exposure_auth import authorize_event

                    authorized_request = await sync_to_async(authorize_event)(
                        self.view_instance, explicit_request, self._explicit_mount_binding
                    )
                    self.view_instance._djust_event_request = authorized_request
                except Exception:
                    # No exception text/traceback: auth providers may include
                    # credentials or other internal state in their exceptions.
                    self.view_instance = None
                    await self.transport.send_error(
                        "Event authorization failed. Please reload the page.",
                        code="permission_denied",
                    )
                    await self.transport.close(code=4403)
                    return
            try:
                await self._dispatch_event_render(data)
            finally:
                if self.view_instance is not None:
                    self.view_instance.__dict__.pop("_djust_event_request", None)

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
        return bool(view_id != getattr(self.view_instance, "_view_id", None))

    @_runtime_diagnostic_scope
    async def _dispatch_event_render(self, data: Dict[str, Any]) -> None:
        """Parse, validate, run the handler, and render one event turn.

        Always invoked inside ``transport.event_context`` (see
        :meth:`_dispatch_event_inner`) so the render serialization + observability
        scope is established for the whole handler+render turn."""
        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

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

        # Bind the mounted view to a local non-None handle. _dispatch_event_render
        # is only ever entered from _dispatch_event_inner AFTER its
        # `if not self.view_instance: return` guard (and inside event_context,
        # which holds the render lock so view_instance can't be nulled mid-turn),
        # so this guard never fires in practice — it narrows Optional[Any] for the
        # type checker and is behavior-equivalent to the caller's contract.
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            await self.transport.send_error("View not mounted. Please reload the page.")
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
            and hasattr(view, "is_activity_visible")
            and not view.is_activity_visible(_activity_name)
            and not view._is_activity_eager(_activity_name)
        ):
            logger.debug(
                "[djust] Runtime event %r on hidden activity %r — deferring",
                sanitize_for_log(event_name or ""),
                sanitize_for_log(_activity_name),
            )
            # Queued WITHOUT permission/rate-limit check (by design: per-handler
            # auth runs on dispatch via the lock-free re-dispatcher
            # ``_dispatch_single_event``). Per-activity cap bounds memory.
            view._queue_deferred_activity_event(_activity_name, event_name, params)
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

        _tt_snapshot = record_event_start(view, event_name, params, event_ref)
        _tt_error: Optional[str] = None

        # Security
        handler = await _validate_event_security(
            self.transport, event_name, view, self._rate_limiter
        )
        if handler is None:
            # Permission denied / rate-limited / unsafe name — record the denial
            # in the time-travel buffer (with error) before returning, matching the
            # WS bespoke path (websocket.py:3550-3553).
            _tt_error = "permission_denied"
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)
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
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)
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
        call_args, call_kwargs = validated_call_arguments(validation)

        # Snapshot pre-handler assigns for change detection.
        from .websocket import _compute_changed_keys, _resolve_skip_render, _snapshot_assigns

        # Identity snapshot for the #700 push_commands-only auto-skip below:
        # {attr: id(value)} over the public assigns. Immune to the deep-copy
        # sentinel false-positives _snapshot_assigns can produce for non-copyable
        # public attrs (querysets, file handles), so a handler that only calls
        # push_event()/push_commands() without touching real state is detected as
        # a true no-op (mirrors WS handle_event websocket.py:3551-3556).
        _fw_attrs: frozenset[str] = getattr(view, "_framework_attrs", frozenset())
        pre_assigns: Optional[Dict[str, Any]]
        pre_identity: Optional[Dict[str, int]]
        _pre_box: Dict[str, Any] = {}
        from .worker_pool import offload_enabled

        if offload_enabled() and not inspect.iscoroutinefunction(handler):
            # Worker pool on (#3074): take both snapshots on the session's
            # thread, in the SAME hop as the sync handler, instead of on the
            # event loop before it. The render lock is held across both, so
            # nothing can change the view between the snapshot and the call.
            _inner_handler = handler

            def handler(*args: Any, **kwargs: Any) -> Any:  # noqa: F811
                _pre_box["assigns"] = _snapshot_assigns(view)
                _pre_box["identity"] = {
                    k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs
                }
                return _inner_handler(*args, **kwargs)

            pre_assigns = pre_identity = None
        else:
            pre_assigns = _snapshot_assigns(view)
            pre_identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}

        # Call handler. The time-travel record is finalized + pushed in the
        # ``finally`` for BOTH the success and the raising path (mirrors WS
        # ``_handle_event_inner`` websocket.py:3625-3635, which records in a
        # finally so permission-denied / raising handlers still appear in the
        # debug panel). On a raise we set ``_tt_error`` and return early after
        # sending the error frame.
        _handler_start = time.perf_counter()
        try:
            try:
                await _call_handler(handler, call_kwargs or None, positional_args=call_args)
                restrict_diagnostics(view)
            except Exception as exc:
                restrict_diagnostics(view)
                _tt_error = str(exc)[:200] if diagnostics_allowed() else "[redacted]"
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name=event_name,
                    view_class=view.__class__.__name__,
                    logger=logger,
                    log_message=f"Error in handler {view.__class__.__name__}.{sanitize_for_log(event_name)}()",
                )
                await self.transport.send(response)
                return
        finally:
            record_event_end(view, _tt_snapshot, error=_tt_error)
            await self._push_tt_event(view, _tt_snapshot)
        if _pre_box:
            pre_assigns = _pre_box["assigns"]
            pre_identity = _pre_box["identity"]
        if pre_assigns is None or pre_identity is None:
            # Unreachable: the offloaded wrapper always snapshots before it
            # calls the handler, and a raising handler returned above.
            raise RuntimeError("pre-event snapshot missing")

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
                view,
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
        self._notify_waiters_safely(
            view,
            event_name,
            coerced_params or {},
            log_message="Waiter notification for %r failed: %s",
            log_args=(event_name,),
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
        else:
            from ._exposure import uses_legacy_exposure

            if target_view is self.view_instance and not uses_legacy_exposure(target_view):
                # A failed explicit save withholds the success frame (E3); the
                # child tree is saved on the render/noop branches below.
                if not await self.commit_explicit_turn(target_view, source="event", children=False):
                    return

        snapshot_fields = await self._explicit_event_snapshot(view)

        # Auto-detect unchanged state. _resolve_skip_render owns the skip
        # decision (#2834) — it consumes an explicit ``_skip_render`` so a
        # stale True never leaks, and ``_force_full_html`` ALWAYS wins over
        # it (a handler that explicitly requested a forced full-HTML render
        # must not be silently dropped, the #1646 hatch-dropped class) — the
        # same resolution as the WS tick/server_push/db_notify paths. The
        # render branch consumes + resets the force flag (see
        # _render_and_send).
        skip_render = _resolve_skip_render(view)
        force_html = getattr(view, "_force_full_html", False)
        from ._exposure import uses_legacy_exposure

        # The parent's assign snapshot does not include owned child state.
        # A handler can mutate a child without changing a parent declaration.
        has_explicit_children = not uses_legacy_exposure(view) and bool(
            getattr(view, "_child_views", None)
        )
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(view)
            if pre_assigns == post_assigns and not has_explicit_children:
                skip_render = True
            else:
                view._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        # #700: push_commands-only handlers auto-skip the render. When push events
        # are pending and the *identity* of every public attr is unchanged, the
        # handler only emitted push commands (no state mutation) so a VDOM
        # re-render is wasted work (and can trigger morphdom recovery during
        # tours). Identity comparison sidesteps the assigns-snapshot
        # false-positives on non-copyable attrs. Mirrors WS handle_event
        # websocket.py:3867-3891. Guarded by force_html so an explicit
        # full-HTML request still renders.
        if not skip_render and not force_html:
            pending = getattr(view, "_pending_push_events", None)
            if pending:
                post_identity = {k: id(v) for k, v in view.__dict__.items() if k not in _fw_attrs}
                if pre_identity == post_identity and not has_explicit_children:
                    skip_render = True

        from ._async_batch import AsyncBatch

        async_batch = AsyncBatch(view)
        has_async = bool(async_batch.token)

        if skip_render:
            if not await self._persist_explicit_children_after_event(view):
                return
            # (_skip_render was already consumed by _resolve_skip_render
            # above — it is the single owner of that reset, #2834.)
            # Legacy: drain ALL queued side-effects BEFORE the noop, matching the WS
            # bespoke skip-render path (websocket.py:3941 — ``await
            # self._flush_all_pending()`` then ``_send_noop``). #1907 THE FLIP:
            # the runtime skip-render branch previously only flushed push_events,
            # so a state-unchanging handler that queued navigation (e.g.
            # ``live_redirect()``) dropped its ``navigation`` frame on the WS path
            # once events routed here (caught by
            # ``test_live_redirect_from_state_unchanging_handler_emits_navigation_frame``).
            # ``_flush_all_pending`` is the single 8-queue drain (#1646) and
            # includes ``_flush_push_events``, so this also covers the push drain.
            if not snapshot_fields:
                await self._flush_all_pending()
            # ref echo (#560) + source/event_name (#560 sequencing) on the noop
            # frame so the client can match the ack to its pending event (clear
            # _pendingEventRefs / stop the right loading state) and distinguish it
            # from out-of-band pushes. WS echoes ``ref`` on its noop
            # (_send_noop, websocket.py:776-781); ``source``/``event_name`` are a
            # runtime ADD (the WS noop omits them) for self-describing #560
            # sequencing — harmless extra fields the client tolerates.
            noop_msg = {
                "type": "noop",
                "source": "event",
                "event_name": event_name,
            }
            if event_ref is not None:
                noop_msg["ref"] = event_ref
            if has_async:
                noop_msg["async_pending"] = True
            noop_msg.update(async_batch.fields())
            noop_msg.update(snapshot_fields)
            await self.transport.send(noop_msg)
            if snapshot_fields:
                # Explicit acknowledgements publish (or invalidate) navigation
                # state before a queued redirect triggers before-navigate.
                # Preserve the legacy side-effect ordering unchanged.
                await self._flush_all_pending()
            # Dispatch background work UNCONDITIONALLY after the turn (matches WS
            # handle_event websocket.py:4235, NOT the legacy SSE which gated this
            # on has_async). The captured batch includes named and legacy work
            # (both task formats, as ``has_pending_async_work`` reads them since
            # #2963), and its token was advertised in the acknowledgement. An
            # empty batch is a no-op; it must not capture later unrelated
            # queued work. Converging onto the WS behavior here FIXES the legacy
            # SSE drop of ``start_async`` work (#1887 / #1646).
            self._dispatch_async_work(event_name, async_batch)
            # dj_activity flush (Phase 2.3a, #1903): a skip-render handler can
            # still flip an activity visible via set_activity_visible(); drain its
            # queue so deferred events for that panel arrive in the same
            # round-trip. Mirrors the WS flush placement after the async dispatch
            # (websocket.py:4283-4294). Safe + no-op when no activities exist.
            await self._flush_deferred_activity_events()
            return

        # Render — scoped to one bound component when that is all that
        # changed (ADR-032 D1/D5); ``_render_and_send`` checks the other gates.
        await self._render_and_send(
            event_name=event_name,
            cache_request_id=cache_request_id,
            has_async=has_async,
            async_batch=async_batch,
            force_html=force_html,
            event_ref=event_ref,
            scoped_component=_scoped_component_for(view, getattr(view, "_changed_keys", None)),
            snapshot_fields=snapshot_fields,
        )

        # Dispatch background work UNCONDITIONALLY after the render (WS parity,
        # websocket.py:4235): start_async / @background callbacks run off-thread
        # and stream their re-rendered result via the transport when ready.
        self._dispatch_async_work(event_name, async_batch)

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

    @_runtime_diagnostic_scope
    async def _flush_deferred_activity_events(self) -> None:
        """Drain the view's deferred-activity queues via the runtime re-dispatcher.

        Thin wrapper: hands the runtime itself to the ActivityMixin flush as the
        consumer-like dispatcher. No-op when the view has no ActivityMixin or no
        queued events. Best-effort — a drain bug must never break the event turn
        (mirrors the WS flush call-site guard, websocket.py:4290-4294)."""
        view = self.view_instance
        if view is None or not hasattr(view, "_flush_deferred_activity_events"):
            return
        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        try:
            await view._flush_deferred_activity_events(self)
        except Exception:  # noqa: BLE001 — never fail the event for a drain bug
            restrict_diagnostics(view)
            restrict_diagnostics(self.view_instance)
            if diagnostics_allowed():
                logger.exception("dj_activity: runtime deferred-event flush raised")
            else:
                logger.error("Protected deferred-event flush failed")

    @_runtime_diagnostic_scope
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
        from .websocket import _compute_changed_keys, _resolve_skip_render, _snapshot_assigns

        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        restrict_diagnostics(target_view)

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
        call_args, call_kwargs = validated_call_arguments(validation)

        # --- handler invocation -------------------------------------------
        pre_assigns = _snapshot_assigns(self.view_instance)
        try:
            await _call_handler(handler, call_kwargs or None, positional_args=call_args)
            restrict_diagnostics(target_view)
        except Exception:  # noqa: BLE001 — never break the flush
            restrict_diagnostics(target_view)
            if diagnostics_allowed():
                logger.exception(
                    "Runtime deferred-activity event %r on %s raised during dispatch",
                    sanitize_for_log(event_name or ""),
                    type(target_view).__name__,
                )
            else:
                logger.error("Protected deferred event failed")
            return

        # Waiter notification (ADR-002) — same posture as the live path.
        self._notify_waiters_safely(
            target_view,
            event_name,
            coerced_params or {},
            log_message="Waiter notification for deferred %r failed: %s",
            log_args=(event_name or "",),
        )

        # --- render + emit one frame (mirrors the live skip/render split) --
        # Bind the mounted view to a non-None local for the direct-attribute
        # writes below (caller guarantees a mounted view on this re-dispatch path;
        # the guard narrows Optional[Any] and is behavior-equivalent).
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return
        # _resolve_skip_render owns the skip decision (#2834/#2847) — the same
        # resolution as dispatch_event / server_push / db_notify / tick. This
        # twin previously read the two flags inline and resolved the collision
        # the pre-#2834 way: with BOTH flags set the noop branch won, silently
        # dropping the forced full render AND leaking ``_force_full_html``
        # into a later unrelated turn (the #1646 "hatch dropped" class). The
        # helper consumes ``_skip_render`` whenever set (a stale True never
        # leaks) and lets ``_force_full_html`` win.
        skip_render = _resolve_skip_render(view)
        force_html = getattr(view, "_force_full_html", False)
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(view)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                view._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        from ._async_batch import AsyncBatch

        async_batch = AsyncBatch(view)
        has_async = bool(async_batch.token)

        if skip_render:
            # (_skip_render was already consumed by _resolve_skip_render —
            # it is the single owner of that reset, #2834/#2847.)
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
            noop_msg.update(async_batch.fields())
            await self.transport.send(noop_msg)
            self._dispatch_async_work(event_name, async_batch)
            return

        await self._render_and_send(
            event_name=event_name,
            has_async=has_async,
            async_batch=async_batch,
            force_html=force_html,
            event_ref=event_ref,
            scoped_component=_scoped_component_for(view, getattr(view, "_changed_keys", None)),
        )
        self._dispatch_async_work(event_name, async_batch)

    # ------------------------------------------------------------------ #
    # Time-travel push hook (ADR-022 Iter 2 Phase 2.2)
    # ------------------------------------------------------------------ #

    def _notify_waiters_safely(
        self,
        view: Any,
        event_name: str,
        params: Dict[str, Any],
        *,
        log_message: str,
        log_args: Tuple[Any, ...],
    ) -> None:
        """Notify once, preserving owner restrictions and legacy warning text."""
        from ._exposure_diagnostics import (
            diagnostic_scope,
            diagnostics_allowed,
            restrict_diagnostics,
            watch_diagnostic_owner,
        )

        with diagnostic_scope():
            watch_diagnostic_owner(self, "view_instance")
            restrict_diagnostics(view)
            if not hasattr(view, "_notify_waiters"):
                return
            try:
                view._notify_waiters(event_name, params)
            except Exception as exc:  # noqa: BLE001 — waiter bugs must not break events
                restrict_diagnostics(view)
                restrict_diagnostics(self.view_instance)
                if diagnostics_allowed():
                    logger.warning(
                        log_message,
                        *(sanitize_for_log(str(arg)) for arg in log_args),
                        exc,
                    )
                else:
                    logger.warning("Protected waiter notification failed")

    @_runtime_diagnostic_scope
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
        from ._exposure_diagnostics import diagnostics_allowed, restrict_diagnostics

        restrict_diagnostics(view)
        try:
            await hook(view, snapshot)
        except Exception:  # noqa: BLE001 — dev-only time-travel push; degrade silently
            restrict_diagnostics(view)
            restrict_diagnostics(self.view_instance)
            if diagnostics_allowed():
                logger.exception("Runtime: time_travel on_event_recorded hook failed")
            else:
                logger.error("Protected time-travel notification failed")

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

        Caller MUST have already verified top-level view identity and either
        legacy snapshot opt-in or the staged explicit policy. Explicit saves
        select only declared server fields, never render context. Bounded by a
        150ms timeout, mirroring the WS save block (websocket.py:3704-3804)."""

        async def _save() -> None:
            # Discover the session the same way the WS save block does
            # (websocket.py:3710-3720): prefer the stashed mount request's
            # session (carries the save-key namespace + path); fall back to the
            # ASGI scope's session when no mount request was stashed.
            mount_request = getattr(target_view, "_djust_mount_request", None)
            from ._exposure import ExposureError, uses_legacy_exposure

            if not uses_legacy_exposure(target_view):
                from ._exposure_sessions import asave_server_state, request_binding

                event_request = getattr(target_view, "_djust_event_request", None)
                if event_request is None:
                    raise ExposureError(
                        "Explicit persistence requires the authorized event request"
                    )
                if (
                    await sync_to_async(request_binding)(event_request)
                    != self._explicit_mount_binding
                ):
                    raise ExposureError("Explicit event identity changed before persistence")
                await asave_server_state(target_view, event_request)
                return
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

            from .serialization import normalize_django_value as _normalize

            save_path = mount_request.path if mount_request is not None else "/"
            save_view_key = f"liveview_{save_path}"

            # Save order mirrors HTTP path (mixins/request.py:593-609): private
            # attrs FIRST, then public via get_context_data().
            if hasattr(target_view, "_get_private_state"):
                _priv = await sync_to_async(target_view._get_private_state)()
                if _priv:
                    await save_session.aset(
                        f"{save_view_key}__private",
                        _normalize(_priv, state_roundtrip=True),
                    )
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

            from .mixins.context import legacy_render_only_keys

            render_only = legacy_render_only_keys(target_view)
            from .components.base import LiveComponent as _LC, is_component_collection

            save_state = {
                k: v
                for k, v in save_context.items()
                if not isinstance(v, _LC)
                and not is_component_collection(v)
                and k not in render_only
            }
            await save_session.aset(save_view_key, _normalize(save_state, state_roundtrip=True))

            # Components — sync helper, wrap with sync_to_async.
            if mount_request is not None and hasattr(target_view, "_save_components_to_session"):
                await sync_to_async(target_view._save_components_to_session)(
                    mount_request, save_context
                )

            await save_session.asave()

        try:
            await asyncio.wait_for(_save(), timeout=EVENT_STATE_SAVE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning(
                "Runtime event state save exceeded 150ms for %r — session backend "
                "backpressure; skipping this event's save. Subsequent events will retry.",
                sanitize_for_log(event_name or ""),
            )
        except Exception as exc:  # noqa: BLE001 — saves must never break event handling
            from ._exposure_diagnostics import log_failure

            # Explicit saves project persist="server" values and storage
            # exceptions propagate, so the exception can carry server-only data.
            log_failure(
                logger,
                exc,
                "Failed to save LiveView state after runtime event %r",
                sanitize_for_log(event_name or ""),
                traceback=True,
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
            await asyncio.wait_for(_save(), timeout=EVENT_STATE_SAVE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning(
                "Runtime event sticky-child state save exceeded 150ms for %r — "
                "session backend backpressure; skipping this event's save. "
                "Subsequent events will retry.",
                sanitize_for_log(event_name or ""),
            )
        except Exception as exc:  # noqa: BLE001 — saves must never break event handling
            from ._exposure_diagnostics import log_failure

            # Explicit saves project persist="server" values and storage
            # exceptions propagate, so the exception can carry server-only data.
            log_failure(
                logger,
                exc,
                "Failed to save sticky-child state after runtime event %r",
                sanitize_for_log(event_name or ""),
                traceback=True,
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
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return False
        view_id = params.pop("view_id", None)
        if not view_id or view_id == getattr(view, "_view_id", None):
            # No child routing requested (or the id IS the top-level view):
            # fall through to the single-view path.
            return False

        all_children = view._get_all_child_views() if hasattr(view, "_get_all_child_views") else {}
        target_view = all_children.get(view_id)
        if target_view is None:
            target_view = _explicit_descendant(view, view_id)
        if target_view is None:
            # Security: don't echo a client-supplied view_id into the
            # user-facing error string. The id is already logged via the
            # structured event for callers that need to trace it.
            await self.transport.send_error(
                "Embedded view not found",
                extra={"view_id": sanitize_for_log(view_id)},
            )
            return True

        from ._exposure import ExposureError, uses_legacy_exposure

        explicit_child = not uses_legacy_exposure(target_view)
        event_request = getattr(view, "_djust_event_request", None)

        def resolve_explicit_child() -> ChildStateSession | None:
            from ._exposure_children import child_event_adapter
            from ._exposure_sessions import request_binding
            from .auth.core import check_view_auth

            if (
                uses_legacy_exposure(view)
                or event_request is None
                or request_binding(event_request) != self._explicit_mount_binding
            ):
                raise ExposureError("Missing current child event authorization")
            adapter = child_event_adapter(target_view, view, event_request)
            target_view.request = event_request
            if check_view_auth(target_view, event_request) is not None:
                raise ExposureError("Child event authorization denied")
            return adapter

        if explicit_child:
            try:
                await sync_to_async(resolve_explicit_child)()
            except Exception:  # noqa: BLE001 — fail closed without provider values
                await self.transport.send_error(
                    "Child event authorization failed. Please reload the page.",
                    code="permission_denied",
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
        call_args, call_kwargs = validated_call_arguments(validation)

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
                await _call_handler(handler, call_kwargs or None, positional_args=call_args)
            except Exception as exc:
                if explicit_child:
                    _tt_error = "Child event failed"
                    await self.transport.send_error("Child event failed.", code="event_error")
                    return True
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
        self._notify_waiters_safely(
            target_view,
            event_name,
            coerced_params or {},
            log_message="Waiter notification for embedded child %r failed: %s",
            log_args=(event_name,),
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

        if explicit_child:
            try:
                from .auth.core import enforce_object_permission

                await sync_to_async(resolve_explicit_child)()
                await sync_to_async(enforce_object_permission)(target_view, event_request)
                # The shared post-render batch saves the owner and reconciles
                # nested slots together; no separate pre-render child flush.
            except Exception:  # noqa: BLE001 — no legacy save or success frame on failure
                await self.transport.send_error(
                    "Child state unavailable. Please reload the page.", code="state_error"
                )
                return True
        elif sticky_child_should_persist(target_view, self.view_instance):
            await self._persist_sticky_child_after_event(target_view, event_name)
        else:
            warn_sticky_child_optin_skip(target_view, self.view_instance)

        # Render just the child's subtree (single-sourced render core) and emit
        # the scoped full-HTML ``embedded_update`` frame. Mirrors the WS
        # ``_render_embedded_child`` + ``_emit_full_html_update("embedded_child")``
        # + the ``embedded_update`` send (websocket.py:3905-3920 / 4010-4018).
        from .websocket import _emit_full_html_update, render_embedded_child_html

        try:
            html = await sync_to_async(render_embedded_child_html)(target_view)
        except Exception:
            if not explicit_child:
                raise
            await self.transport.send_error("Child rendering unavailable.", code="render_error")
            return True
        if explicit_child and not await self._persist_explicit_children_after_event(view):
            return True
        _emit_full_html_update(target_view, "embedded_child", event_name, html, 0)

        msg: Dict[str, Any] = {
            "type": "embedded_update",
            "view_id": view_id,
            "html": html,
            "event_name": event_name,
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        from ._async_batch import AsyncBatch

        child_batch = AsyncBatch(target_view)
        msg.update(child_batch.fields())
        await self._send_render_frame(msg)
        await self._flush_all_pending()

        # Child side effects carry their own audio scope; drain the child's queue.
        self._flush_push_events(target_view)
        # Dispatch any background work the child handler scheduled (WS parity).
        if explicit_child:
            from ._child_async import dispatch_child_work

            assert child_batch is not None
            dispatch_child_work(self, target_view, event_name, child_batch)
            # Work the handler queued on another explicit child (a sibling or
            # descendant) runs under that child's owner too, not at its next
            # event (ADR-038 E3-3, decided 2026-09-22).
            self._dispatch_explicit_child_queues(event_name)
        else:
            from ._child_async import dispatch_legacy_child_work

            dispatch_legacy_child_work(self, target_view, view_id, event_name, child_batch)
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
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return False
        component_id = params.get("component_id")
        if not component_id:
            return False

        component = view._components.get(component_id) if hasattr(view, "_components") else None
        if not component:
            # A descriptor's `BoundComponent` is registered in
            # `view._components` by `LiveComponent.__get__` (ADR-031 D2), so
            # the lookup above finds it and this branch is normally not
            # reached — descriptor events now take the validated
            # component-dispatch path below, as events on an instance-assigned
            # component always have.
            #
            # It remains the right fall-through for a descriptor nothing has
            # materialised yet: the name is in `_component_descriptors`, but
            # `__get__` has not run, so `_components` has no entry. Such a
            # descriptor still takes a `component_id` — `__set_name__` wires
            # its `Meta.event` onto the view, and `_make_event_handler`'s
            # view-level alias resolves the instance from that parameter
            # itself. Erroring here would make it unreachable.
            if component_id in getattr(type(view), "_component_descriptors", {}):
                return False

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
        call_args, call_kwargs = validated_call_arguments(validation)

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

        # ADR-032: a bound component's event may patch only its own subtree
        # when the handler changed nothing else — decided from the snapshot,
        # not from the template alone (decision driver 2).
        from .components.base import BoundComponent
        from .websocket import _compute_changed_keys, _snapshot_assigns

        from ._component_subscriptions import ComponentDeclaration

        pre_assigns = (
            _snapshot_assigns(view)
            if isinstance(component, (BoundComponent, ComponentDeclaration))
            else None
        )

        try:
            try:
                await _call_handler(handler, call_kwargs or None, positional_args=call_args)
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

        # New concrete bindings keep their opaque lifetime IDs and state in the
        # native component-session record. They return before the ordinary view
        # event save below, so use the same bounded, opt-in persistence here.
        if (
            isinstance(component, ComponentDeclaration)
            and view is self.view_instance
            and getattr(view, "enable_state_snapshot", False)
        ):
            await self._persist_state_after_event(view, event_name)

        # Propagate the component event to the PARENT view's waiters with the
        # component_id injected (ADR-002 Phase 1b/1c, websocket.py:3456-3479).
        notify_kwargs = dict(coerced_event_data or {})
        notify_kwargs.setdefault("component_id", component_id)
        self._notify_waiters_safely(
            view,
            event_name,
            notify_kwargs,
            log_message="Waiter notification for component event %r on %s failed: %s",
            log_args=(event_name, component_id),
        )

        from ._async_batch import AsyncBatch

        async_batch = AsyncBatch(view)

        # A handler that set ``self._view._skip_render`` asked for no render
        # this turn. ``_resolve_skip_render`` is the single owner of that flag
        # on every route (#2834): it consumes it, and ``_force_full_html`` still
        # wins. Without it here the flag rendered anyway and then leaked into
        # the next view event, which answered ``noop`` (#2924).
        from .websocket import _resolve_skip_render

        if _resolve_skip_render(view):
            await self._send_component_noop(event_name, event_ref, async_batch)
            return True

        # ADR-032 D5: the scoped path first. Same helper as the runtime event
        # path; the frame is the ``patch`` frame that path emits.
        if pre_assigns is not None and not getattr(view, "_force_full_html", False):
            changed = _compute_changed_keys(pre_assigns, _snapshot_assigns(view))
            # A click whose handler changed nothing (close on a closed sheet,
            # a second "accept", a push-only handler) is a ``noop``, as the
            # view route answers — not a page render shipped as a 30 KB
            # ``html_update`` (#2922). ``_flush_all_pending`` below drains any
            # push events before the noop goes out, as on the view route.
            if not changed:
                await self._send_component_noop(event_name, event_ref, async_batch)
                return True
            if _scoped_component_for(view, changed) is component:
                _scoped_start = time.perf_counter()
                scoped = await self._render_scoped_component(view, component)
                if scoped is not None:
                    await self._send_scoped_patch_frame(
                        view,
                        scoped,
                        (time.perf_counter() - _scoped_start) * 1000,
                        event_name=event_name,
                        event_ref=event_ref,
                        async_batch=async_batch,
                    )
                    await self._flush_all_pending()
                    self._dispatch_async_work(event_name, async_batch)
                    return True

        # Component VDOM is separate from the parent's, so re-render the parent
        # to full HTML and emit a ``component_event`` frame (websocket.py:4024-4032).
        from .websocket import _emit_full_html_update

        html, _patches, version = await sync_to_async(view.render_with_diff)()
        # The forced full HTML this render honoured is consumed here, as
        # ``_render_and_send`` consumes it on the view route; left set, every
        # later component_id event would bypass the noop and scoped branches
        # until a view-route turn reset it (#2923 review).
        if getattr(view, "_force_full_html", False):
            view._force_full_html = False
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
        msg.update(async_batch.fields())
        await self._send_render_frame(msg)
        await self._flush_all_pending()

        # Dispatch any background work the component handler scheduled (WS parity).
        self._dispatch_async_work(event_name, async_batch)
        return True

    async def _send_component_noop(
        self, event_name: str, event_ref: Optional[int], async_batch: "AsyncBatch"
    ) -> None:
        """End a ``component_id`` turn that renders nothing: drain the queued
        side effects, answer ``noop`` (advertising the captured background
        batch) and start that batch — the view route's skip shape (#2922,
        #2924)."""
        await self._flush_all_pending()
        noop_msg: Dict[str, Any] = {
            "type": "noop",
            "source": "event",
            "event_name": event_name,
        }
        if event_ref is not None:
            noop_msg["ref"] = event_ref
        noop_msg.update(async_batch.fields())
        await self.transport.send(noop_msg)
        self._dispatch_async_work(event_name, async_batch)
        await self._flush_deferred_activity_events()

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
        view = self.view_instance
        if view is None:
            await self.transport.send_error("View not mounted")
            return
        # URL renders participate in the same mutation order as events and
        # background results. Otherwise their HTML and owner contracts can
        # describe different turns. Navigation may replace the owner while
        # this task waits for the borrowed transport lock.
        from ._exposure import uses_legacy_exposure

        # An explicit route change mutates declared state with no inbound event
        # request: authorize it fresh, like background turns (ADR-038 D-l).
        explicit = not uses_legacy_exposure(view)
        explicit_lock = self._explicit_event_lock if explicit else contextlib.nullcontext()
        async with explicit_lock, self.transport.event_context(view):
            if self.view_instance is not view:
                await self.transport.send_error("View changed. Please reload the page.")
                return
            with _tenant_context(getattr(view, "_tenant", None)):
                if explicit:
                    try:
                        await self.authorize_explicit_turn(view)
                    except Exception:  # noqa: BLE001 — no auth provider values
                        if self.view_instance is view:
                            await self.deny_explicit_turn()
                        return
                try:
                    await self._dispatch_url_change_inner(data)
                finally:
                    if explicit:
                        view.__dict__.pop("_djust_event_request", None)

    async def _dispatch_url_change_inner(self, data: Dict[str, Any]) -> None:
        """URL-change body (see :meth:`dispatch_url_change` for the tenant wrapper)."""
        if not self.view_instance:
            await self.transport.send_error("View not mounted")
            return

        params = data.get("params", {})
        uri = data.get("uri", "")

        # #3125: a view whose object the route selects (ADR-035) never runs
        # handle_params for a URL naming another record; it remounts there, so
        # the object is resolved and authorized for the URL the user sees.
        route_changed = getattr(self.view_instance, "_djust_route_changed", None)
        if callable(route_changed) and isinstance(uri, str) and uri and route_changed(uri):
            from .mixins.navigation import same_origin_target

            self.view_instance.live_redirect(same_origin_target(uri), replace=True)
            await self._flush_navigation()
            return

        try:
            await sync_to_async(self.view_instance.handle_params)(params, uri)

            # Object-permission re-check (ADR-017) after the client-supplied URL
            # params may have changed the access-determining state. Without this,
            # url_change navigates to a denied object and re-renders it
            # (finding #10). No-op for views that don't override get_object.
            from django.core.exceptions import PermissionDenied

            from .auth.core import enforce_object_permission

            # An explicit turn re-checks against its freshly authorized request,
            # never the mount-time one.
            permission_request = getattr(
                self.view_instance,
                "_djust_event_request",
                getattr(self.view_instance, "request", None),
            )
            try:
                await sync_to_async(enforce_object_permission)(
                    self.view_instance, permission_request
                )
            except PermissionDenied:
                await self.transport.send_error(
                    "Access denied for this object.", code="permission_denied"
                )
                return

            # Declared server state changed by handle_params is saved before
            # the render frame; a failed save withholds it (ADR-038 E3).
            if not await self.commit_explicit_turn(self.view_instance, source="url_change"):
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
                await self._send_render_frame(msg)
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
                await self._send_render_frame(msg)

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
        """Import + instantiate the LiveView class. On failure, STASHES the error
        frame on ``self._instantiate_error_frame`` (sent by ``dispatch_mount``) and
        returns ``None``. Override-friendly for tests.

        #1919 (THE MOUNT FLIP): this previously fire-and-forgot the error send via
        ``asyncio.ensure_future`` (it is a sync method and cannot await). Under the
        ``mount_batch`` collector (``_mount_one`` swaps ``send_json`` per view), a
        deferred send from a FAILED view leaked into the NEXT view's collector — a
        bad view's error frame surfaced in a survivor's ``captured[]`` and flipped
        the survivor to ``failed[]``. Stashing the frame and letting the async
        ``dispatch_mount`` AWAIT it (mirroring the bespoke ``handle_mount``, which
        ``await self.send_json(...)``-ed the error inline) keeps the send inside the
        correct ``_mount_one`` window — no cross-entry contamination.
        """
        self._instantiate_error_frame = None

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
            self._instantiate_error_frame = {
                "type": "error",
                "error": _safe_error(resolution.detail, resolution.generic),
            }
            return None
        view_class = resolution.view_class
        if view_class is None:  # pragma: no cover — truthy resolution => view_class set
            # ViewResolution.__bool__ is `view_class is not None`, so a truthy
            # `resolution` (checked above) guarantees this; the guard narrows
            # Optional[type] for the type checker and is behavior-equivalent.
            return None

        try:
            return view_class()
        except Exception as exc:
            # No instance exists yet: the class owns the policy (ADR-038 D-a).
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Failed to instantiate {view_path}",
                expose_details=_diagnostics_policy_allows(view_class),
            )
            self._instantiate_error_frame = response
            return None

    async def _build_request(self, *, page_url: str, params: Dict[str, Any]) -> Any:
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
        from .security.csrf import abind_csrf_cookie  # noqa: PLC0415

        if transport_request is not None:
            # #2998: normally a no-op — CsrfViewMiddleware already bound the real
            # SSE stream request. Binds it when the middleware did not run.
            await abind_csrf_cookie(transport_request)
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
        request_extra: Dict[str, Any] = {}
        if host:
            request_extra["HTTP_HOST"] = host
        if is_secure:
            request_extra["secure"] = True
            request_extra["HTTP_X_FORWARDED_PROTO"] = "https"
        request = factory.get(path_with_query, **request_extra)

        # Wire session if available from WS scope
        try:
            from .utils import build_session_for_request  # noqa: PLC0415

            session_key = None
            if self.scope:
                scope_session = self.scope.get("session")
                session_key = getattr(scope_session, "session_key", None) if scope_session else None
            # Honours settings.SESSION_ENGINE (#2210). Hardcoding the DB store
            # here handed a cache-backed project an empty session — or, if it
            # never ran the sessions migration, an OperationalError on first
            # read.
            session = build_session_for_request(session_key)
            if session is not None:
                request.session = session
        except Exception:
            # Session backend not configured — leave request.session unset.
            pass

        if self.scope and "user" in self.scope:
            request.user = self.scope["user"]

        await sync_to_async(self._attach_socket_tenant)(request)
        # #2998: bind the browser's CSRF cookie so {% csrf_token %} rendered
        # over the socket matches it. After the session, for CSRF_USE_SESSIONS.
        await abind_csrf_cookie(request, self.scope)
        return request

    def _attach_socket_tenant(self, request: Any) -> None:  # noqa: dead-method-allowed (passed to sync_to_async)
        """Resolve the tenant for a synthesized socket request, as HTTP does.

        ``TenantMiddleware`` sets ``request.tenant`` on every HTTP request when
        tenancy is configured; the request synthesized for a WebSocket mount
        never had one. Explicit request binding refuses a configured tenancy
        that silently disappears, so explicit WebSocket mounts failed in every
        tenant-configured project. The resolver sees the handshake's headers
        on a probe copy, so header-based resolution works while the request's
        own ``META`` stays exactly as before. A resolver failure leaves
        ``tenant`` unset, which explicit binding refuses (fail closed).
        """
        from copy import copy

        from django.conf import settings

        config = getattr(settings, "DJUST_CONFIG", None) or {}
        if "TENANT_RESOLVER" not in config and not getattr(settings, "DJUST_TENANTS", None):
            return
        probe = copy(request)
        probe.META = dict(request.META)
        for raw_name, raw_value in (self.scope or {}).get("headers", ()) or ():
            name = raw_name.decode("latin-1").upper().replace("-", "_")
            if name in {"COOKIE", "HOST", "AUTHORIZATION"}:
                continue
            probe.META.setdefault("HTTP_" + name, raw_value.decode("latin-1"))
        try:
            from .tenants.resolvers import get_tenant_resolver

            resolved = get_tenant_resolver().resolve(probe)
        except Exception:  # noqa: BLE001 — unresolved tenancy stays visibly unresolved
            logger.warning("Socket tenant resolution failed")
            return
        tenants = getattr(settings, "DJUST_TENANTS", None) or {}
        if resolved is None and (
            tenants.get("REQUIRED", False) or config.get("TENANT_REQUIRED", False)
        ):
            # Required tenancy that resolves to nothing stays unset, so explicit
            # binding refuses it instead of reading it as "no tenant".
            return
        request.tenant = resolved

    async def _check_auth(self, request: Any) -> Optional[bool]:
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

        auth_view = self.view_instance
        legacy_diagnostics = _diagnostics_policy_allows(auth_view)
        try:
            redirect_url = await sync_to_async(run_pre_mount_auth)(self.view_instance, request)
        except PermissionDenied:
            await self.transport.send({"type": "error", "error": "Permission denied"})
            # ADR-022 Iter 3 Phase 3.3a (#1917, Finding E): the runtime already
            # sent the verdict frame; finalize_mount_auth adds ONLY the
            # transport-level close (WS close(4403) — unconditional for a
            # permission denial, matching websocket.py:2345; SSE no-op). It does
            # NOT re-send the frame. getattr-guarded for duck-typed fakes.
            await self._finalize_mount_auth("permission_denied")
            return True
        except Exception as exc:  # noqa: BLE001 — fail-closed (tenant / auth-hook error)
            response = handle_exception(
                exc,
                error_type="mount",
                view_class=self.view_instance.__class__.__name__,
                logger=logger,
                log_message="Error in pre-mount security sequence for %s"
                % sanitize_for_log(self.view_instance.__class__.__name__),
                expose_details=legacy_diagnostics and _diagnostics_policy_allows(auth_view),
            )
            await self.transport.send(response)
            # No close: the WS bespoke path lets a non-auth-verdict exception
            # (tenant Http404 / buggy custom check_permissions) flow to the outer
            # mount-error envelope WITHOUT a socket close (websocket.py:2305-2313).
            # finalize_mount_auth is NOT called here — parity with WS.
            return True

        if redirect_url:
            await self.transport.send({"type": "navigate", "to": redirect_url})
            # Finding E: the redirect verdict gates the WS close on
            # ``not _mounting_in_batch`` (#291 / #1780) — a batched login-required
            # view reports as ``navigate[]`` and closing would kill sibling mounts.
            # finalize_mount_auth applies that gated close (SSE no-op).
            await self._finalize_mount_auth("redirect")
            return True

        return None

    async def _on_mount_failed(self, view_instance: Any) -> None:
        """Tell the transport that a mount it already set up has failed (#3027).

        ``on_view_mounted`` runs before ``mount()``: on WS it starts the view's
        tick task. When ``mount()``, ``handle_params()``, the actor mount or the
        initial render then raises, the error frame goes out but the
        half-mounted view stays on the runtime (later frames on the socket
        still see it, unchanged here), and the tick kept calling
        ``handle_tick`` on it every beat. ``on_mount_failed`` stops that work.
        getattr-guarded like the other mount hooks, so a transport without it
        (SSE has no tick) and duck-typed test fakes are unaffected. Never
        raises: a cleanup failure must not replace the error frame already
        sent.
        """
        hook = getattr(self.transport, "on_mount_failed", None)
        if hook is None:
            return
        try:
            result = hook(view_instance)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 — cleanup after an error already reported
            from ._exposure_diagnostics import log_failure_for

            log_failure_for(
                logger, (view_instance,), exc, "on_mount_failed hook raised", traceback=True
            )

    async def _finalize_mount_auth(self, verdict: str) -> None:
        """Apply the transport-level finalization of a blocking mount-auth verdict.

        ADR-022 Iter 3 Phase 3.3a (#1917, Finding E). The runtime's auth-block
        paths (``_check_auth`` / the ``run_on_mount_hooks`` redirect in
        ``dispatch_mount``) ALREADY send the verdict frame + clear
        ``view_instance``; this adds ONLY the transport-level close
        (``WSConsumerTransport.finalize_mount_auth`` → ``close(4403)``, gated on
        ``not mounting_in_batch`` for the redirect verdicts). SSE is a no-op.
        getattr-guarded so duck-typed test transport fakes (and the
        default-bearing Protocol) keep working.
        """
        finalize = getattr(self.transport, "finalize_mount_auth", None)
        if finalize is not None:
            await finalize(self.view_instance, verdict)

    def _resolve_url_kwargs(self, page_url: str) -> Dict[str, Any]:
        """Resolve URL-pattern kwargs (e.g. ``pk``, ``slug``) from
        ``page_url``. Returns ``{}`` for unresolvable paths so callers can
        unconditionally ``mount_kwargs.update(...)``.

        ``unquote`` first, and that is load-bearing. ``page_url`` is the
        browser's ``location.pathname``, which is percent-encoded, but
        ``django.urls.resolve`` expects a path that has ALREADY been decoded —
        Django decodes ``request.path`` before it ever reaches the resolver, so
        the two are not interchangeable. Resolving the raw form hands a view the
        encoded value: a category named "Core UI" arrives as ``"Core%20UI"``,
        misses its own lookup, and the page renders fine over HTTP while its
        WebSocket mount dies with ``Http404`` — so the page is visible and
        inert. Any kwarg with a space, a slash or a non-ASCII character is
        affected; ``unquote`` is idempotent for the ones that are not.
        """
        try:
            from urllib.parse import unquote

            from django.urls import resolve

            match = resolve(unquote(page_url))
            return dict(match.kwargs) if match.kwargs else {}
        except Exception:
            return {}

    def _own_route_kwargs(self, view_instance: Any, page_url: str) -> Optional[Dict[str, Any]]:
        """``page_url``'s resolved kwargs if that route serves this view class.

        ``None`` when the URL does not resolve, or resolves to another view: the
        client names both the view and the URL, so a URL routed elsewhere must
        not select this view's object.
        """
        from .mixins.navigation import own_route_kwargs

        return own_route_kwargs(view_instance, page_url)

    def _extract_cache_config(self, view_instance: Any) -> Optional[Dict[str, Any]]:
        """Extract @cache decorator metadata from the view's handlers.

        Sole live copy after the mount flip (#1919/#1920): the bespoke
        ``LiveViewConsumer`` twin was removed as dead code in #1921.
        """
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

    #: Decorator keys the client rate-limit gate consumes (#2656). Kept
    #: narrow on purpose — the mount frame ships only what a client module
    #: actually reads, so a new metadata-only decorator cannot silently
    #: start costing every mount bytes it has no consumer for.
    _CLIENT_RATE_LIMIT_KEYS = ("debounce", "throttle")

    def _extract_handler_config(self, view_instance: Any) -> Optional[Dict[str, Any]]:
        """Extract @debounce / @throttle metadata from the view's handlers.

        Mirrors :meth:`_extract_cache_config`; ships on the mount frame as
        ``handler_config`` and is consumed by ``setHandlerConfig`` in
        ``static/djust/src/05-handler-rate-limit.js`` (#2656).
        """
        try:
            handler_config: Dict[str, Any] = {}
            for attr_name in dir(type(view_instance)):
                if attr_name.startswith("_"):
                    continue
                method = getattr(view_instance, attr_name, None)
                decorators = getattr(method, "_djust_decorators", None)
                if not decorators:
                    continue
                entry = {
                    key: decorators[key]
                    for key in self._CLIENT_RATE_LIMIT_KEYS
                    if decorators.get(key)
                }
                if entry:
                    handler_config[attr_name] = entry
            return handler_config or None
        except Exception:
            return None

    def _extract_optimistic_rules(self, view_instance: Any) -> Dict[str, Any]:
        """Extract optimistic UI rules from descriptor components (DEP-002).

        Ported to the runtime for ADR-022 Iter 3 Phase 3.0 so the converged
        mount frame carries the optimistic-UI rules. Sole live copy after the
        mount flip (#1919/#1920): the bespoke ``LiveViewConsumer`` twin was
        removed as dead code in #1921. Only components with
        ``Meta.tier == "optimistic"`` contribute a rule; the first event wins
        (the ``event not in rules`` guard).
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

    async def _explicit_event_snapshot(self, view: Any) -> Dict[str, Any]:
        """Refresh only declared client persistence after an authorized turn.

        Null explicitly invalidates a previously cached token. Omission is for
        legacy callers, not a fallback to the last successfully captured state.
        Called for the root view only, after a committed foreground event or a
        committed server-originated turn (background result, tick, push,
        NOTIFY), while the turn's authorized request is still attached. Never
        for child-view rendering: the client stores tokens for the primary view.
        """
        from django.conf import settings

        from ._exposure import ExposureError, uses_legacy_exposure
        from ._exposure_sessions import request_binding
        from ._exposure_snapshots import snapshot_codec

        if uses_legacy_exposure(view):
            return {}
        fields: Dict[str, Any] = {
            "view": view._djust_mount_view_path,
            "state_snapshot_signed": None,
        }
        try:
            request = getattr(view, "_djust_event_request", None)
            if request is None or (
                await sync_to_async(request_binding)(request) != self._explicit_mount_binding
            ):
                raise ExposureError("Explicit snapshot requires unchanged authorized identity")
            if getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True):
                codec = await sync_to_async(snapshot_codec)(view, request)
                if codec is not None:
                    fields["state_snapshot_signed"] = await sync_to_async(codec.capture)(view)
        except Exception:
            # Factories and codec errors may contain secrets. Never stringify
            # them or fall back to reflective legacy state.
            logger.warning("Explicit event snapshot unavailable; cached snapshot invalidated")
        return fields

    async def authorize_explicit_turn(self, view: Any) -> Any:
        """Fresh authority for an explicit turn with no inbound event request.

        Background completion, ``url_change`` and server-originated consumer
        turns carry no current POST. Like the child background path, reload
        the supported server session and Django auth instead of trusting the
        mount's principal (ADR-038 D-k/D-l), then rerun object permission. The
        authorized request is stashed where :meth:`commit_explicit_turn` reads
        it; callers pop it when the turn ends. Raises on any failure — callers
        deny with :meth:`deny_explicit_turn` and never stringify the exception.
        """
        from ._exposure import ExposureError
        from ._exposure_auth import authorize_event, fresh_socket_request
        from .auth.core import enforce_object_permission

        binding = self._explicit_mount_binding
        if view is None or view is not self.view_instance or binding is None:
            raise ExposureError("Explicit turn has no mounted owner")

        def _authorize() -> Any:
            request = authorize_event(view, fresh_socket_request(view), binding)
            enforce_object_permission(view, request)
            return request

        request = await sync_to_async(_authorize)()
        # Authorization hooks are application code; their success does not
        # prove the owner was left in place.
        if view is not self.view_instance:
            raise ExposureError("Explicit turn owner replaced during authorization")
        view._djust_event_request = request
        return request

    async def deny_explicit_turn(self) -> None:
        """The foreground event denial, for a turn whose authority was revoked."""
        self.view_instance = None
        await self.transport.send_error(
            "Event authorization failed. Please reload the page.",
            code="permission_denied",
        )
        await self.transport.close(code=4403)

    async def commit_explicit_turn(
        self,
        view: Any,
        *,
        source: str,
        children: bool = True,
        async_batch: Optional[str] = None,
    ) -> bool:
        """Persist an authorized explicit turn before any success frame.

        Saves the root's declared server fields (and, unless the caller saves
        them itself, the child tree) under the turn's authorized request. A
        failed or timed-out save withholds the success frame: the client gets a
        static ``state_error`` and the next update is full HTML, the child
        path's contract (ADR-038 E3, "failed storage without stale delivery").
        Legacy views return True untouched; their saves stay best-effort.
        """
        from ._exposure import ExposureError, uses_legacy_exposure
        from ._exposure_sessions import asave_server_state, request_binding

        if uses_legacy_exposure(view):
            return True
        extra: Dict[str, Any] = {"source": source}
        if async_batch:
            extra["async_batch"] = async_batch
        view_path = getattr(view, "_djust_mount_view_path", None)
        if isinstance(view_path, str) and view_path:
            # The withheld success frame would have refreshed or revoked the
            # client's signed snapshot; the error revokes it instead, so a
            # token captured before this turn cannot outlive the failed save.
            extra["view"] = view_path
            extra["state_snapshot_signed"] = None
        try:
            request = getattr(view, "_djust_event_request", None)
            if request is None or (
                await sync_to_async(request_binding)(request) != self._explicit_mount_binding
            ):
                raise ExposureError("Explicit persistence requires current authorization")
            await asyncio.wait_for(
                asave_server_state(view, request), timeout=EVENT_STATE_SAVE_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 — storage errors can carry server-only values
            from ._exposure_diagnostics import log_failure_for

            view._force_full_html = True
            log_failure_for(
                logger,
                (view,),
                exc,
                "Explicit state save failed; success frame withheld: %s",
                exc,
                level="warning",
                traceback=True,
            )
            await self.transport.send_error(
                "State unavailable. Please reload the page.", code="state_error", **extra
            )
            return False
        if children:
            return await self._persist_explicit_children_after_event(view, async_batch=async_batch)
        return True

    async def _persist_explicit_children_after_event(
        self, view: Any, *, request: Any = None, async_batch: Optional[str] = None
    ) -> bool:
        """Save the authorized child tree before acknowledging a parent event."""
        from ._exposure import ExposureError, uses_legacy_exposure
        from ._exposure_child_persistence import asave_child_states
        from ._exposure_sessions import request_binding

        if uses_legacy_exposure(view):
            return True
        if (
            request is None
            and not getattr(view, "_child_views", None)
            and not getattr(view, "_explicit_child_state_tracked", False)
        ):
            # Mount scans for an existing index under fresh authorization.
            # A view with no children/index has no child persistence work;
            # retain the independent snapshot-invalidation acknowledgement.
            return True
        try:
            if request is None:
                request = getattr(view, "_djust_event_request", None)
            if request is None or (
                await sync_to_async(request_binding)(request) != self._explicit_mount_binding
            ):
                raise ExposureError("Child persistence requires current authorization")
            await asyncio.wait_for(
                asave_child_states(view, request), timeout=EVENT_STATE_SAVE_TIMEOUT_S
            )
        except Exception:  # noqa: BLE001 — no provider values or success frame on failure
            # Rendering may already have advanced the server VDOM. The browser
            # received no matching update, so the next success must send HTML.
            view._force_full_html = True
            await self.transport.send_error(
                "Child state unavailable. Please reload the page.",
                code="state_error",
                **({"source": "async", "async_batch": async_batch} if async_batch else {}),
            )
            return False
        return True

    @_runtime_diagnostic_scope
    async def _render_and_send(
        self,
        *,
        event_name: str,
        cache_request_id: Optional[str] = None,
        has_async: bool = False,
        force_html: bool = False,
        event_ref: Optional[int] = None,
        scoped_component: Optional[Any] = None,
        snapshot_fields: Optional[Dict[str, Any]] = None,
        async_batch: Optional["AsyncBatch"] = None,
    ) -> None:
        """Re-render after an event handler and emit the appropriate frame.

        ``scoped_component`` (ADR-032 D5): the bound component the event
        resolved to — the ONE assign that changed, per
        :func:`_scoped_component_for`. When the remaining gates hold
        (:meth:`_render_scoped_component`) only its subtree is diffed and the
        same ``patch`` frame goes out with ``timing.scope == "component"``;
        otherwise this is today's page render, unchanged.

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
        # Bind the mounted view to a non-None local. _render_and_send is only
        # entered after a view-mounted check (and inside event_context, which holds
        # the render lock), so this guard never fires in practice — it narrows
        # Optional[Any] for the type checker and is behavior-equivalent.
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            await self.transport.send_error("View not mounted. Please reload the page.")
            return

        # Consume the explicit full-HTML request (#560/#700 sibling): reset the
        # flag so it forces exactly one render, mirroring WS
        # (websocket.py:4039-4040 / _dispatch_single_event websocket.py:1489).
        if force_html and getattr(view, "_force_full_html", False):
            view._force_full_html = False
        # Measure render duration for the DEBUG-only top-level ``timing`` field the
        # bespoke ``_send_update`` carried (#1908 item 1). Inert in production: the
        # value is stamped onto the frame as ``_timing_render_ms`` and ``on_event_frame``
        # pops it, only re-attaching a top-level ``timing`` when ``_should_expose_timing()``
        # is true. Cheap (one ``perf_counter`` pair) so it stays unconditional.
        _render_start = time.perf_counter()
        scoped: Optional[Tuple[str, str, int]] = None
        if scoped_component is not None and not force_html:
            scoped = await self._render_scoped_component(view, scoped_component)
        html: str
        patches: Optional[str]
        version: int
        if scoped is not None:
            html, patches, version = scoped
        else:
            try:
                from ._child_rendering import render_view_with_diff

                html, patches, version = await sync_to_async(render_view_with_diff)(view)
            except Exception as exc:
                from ._exposure_diagnostics import restrict_diagnostics

                restrict_diagnostics(view)
                response = handle_exception(
                    exc,
                    error_type="render",
                    view_class=view.__class__.__name__,
                    logger=logger,
                    log_message="Runtime: render error",
                )
                await self.transport.send(response)
                return
        from ._exposure_diagnostics import restrict_diagnostics

        restrict_diagnostics(view)
        _render_ms = (time.perf_counter() - _render_start) * 1000

        if not await self._persist_explicit_children_after_event(view):
            return

        def _send_event_frame(frame: Dict[str, Any]) -> Dict[str, Any]:
            """Stamp the render duration + invoke the DEBUG ``on_event_frame`` fold
            (#1908) in-place, then return the frame for ``transport.send``. The
            internal ``_timing_render_ms`` marker is ALWAYS popped before the frame
            leaves this helper (the WS hook consumes it; SSE / partial test transports
            have a no-op or no hook), so it can never leak onto the wire."""
            if snapshot_fields is not None:
                frame.update(snapshot_fields)
            if async_batch is not None:
                frame.update(async_batch.fields())
            return self._stamp_event_frame(
                view,
                frame,
                event_name=event_name,
                event_ref=event_ref,
                render_ms=_render_ms,
                scope="component" if scoped is not None else None,
            )

        should_reset_form = getattr(view, "_should_reset_form", False)
        if should_reset_form:
            view._should_reset_form = False

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

            # Patch compression (mirror sse.py legacy). A scoped patch is
            # never compressed: its patches are a component's worth, and the
            # fallback would ship the whole page for a subtree change.
            PATCH_THRESHOLD = 100
            _compressed_patch_count: Optional[int] = None
            if scoped is None and patch_list and len(patch_list) > PATCH_THRESHOLD:
                patches_size = len(patches.encode("utf-8")) if isinstance(patches, str) else 0
                html_size = len(html.encode("utf-8")) if html else 0
                if patches_size and html_size < patches_size * 0.7:
                    if hasattr(view, "_rust_view") and view._rust_view:
                        view._rust_view.reset()
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
                await self._send_render_frame(_send_event_frame(msg))
            else:
                # Compression fallback — send full HTML.
                html_stripped = view._strip_comments_and_whitespace(html)
                html_content = view._extract_liveview_content(html_stripped)
                # Observability fold (#1907): the WS bespoke path emitted a
                # ``patch_compression`` ``_emit_full_html_update`` signal here
                # (websocket.py:4129). DJE-053 does NOT fire on this branch (it had
                # patches; compression chose HTML), matching the WS bespoke gate.
                # Called defensively (partial test transports are a no-op).
                _on_render = getattr(self.transport, "on_render_emitted", None)
                if _on_render is not None:
                    _on_render(
                        view,
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
                await self._send_render_frame(_send_event_frame(msg))
        else:
            # No VDOM diff available — send HTML directly.
            if html and hasattr(view, "_strip_comments_and_whitespace"):
                html = view._strip_comments_and_whitespace(html)
            if html and hasattr(view, "_extract_liveview_content"):
                html = view._extract_liveview_content(html)
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
            # #1908 item 2: the bespoke ``no_patches`` ``_emit_full_html_update`` carried
            # a ``context_snapshot`` built from the ``get_context_data()`` dict it had in
            # hand (it called ``get_context_data()`` once and reused the result for both
            # render + snapshot). The runtime render path computes context INSIDE
            # ``render_with_diff`` and does not surface it, so the snapshot was None.
            #
            # Re-capture it ONLY when DEBUG (the only consumer of the signal's snapshot is
            # dev tooling) AND only on the ``no_patches`` reason (the one the bespoke path
            # snapshotted). The DEBUG gate is load-bearing for the PRODUCTION
            # byte-identical guarantee: it confines the extra ``get_context_data()`` call
            # to dev, so production never double-calls it (no behavior change, no cost).
            # Thread it to ``on_render_emitted`` via ``context``.
            from django.conf import settings as _dj_settings

            _ctx_snapshot: Optional[Dict[str, Any]] = None
            if (
                _reason == "no_patches"
                and getattr(_dj_settings, "DEBUG", False)
                and hasattr(view, "get_context_data")
            ):
                try:
                    _ctx_snapshot = await sync_to_async(view.get_context_data)()
                except Exception:  # noqa: BLE001 — snapshot is best-effort DEBUG metadata
                    _ctx_snapshot = None
            _on_render = getattr(self.transport, "on_render_emitted", None)
            if _on_render is not None:
                _on_render(
                    view,
                    reason=_reason,
                    version=version,
                    event_name=event_name,
                    html=html,
                    context=_ctx_snapshot,
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
            await self._send_render_frame(_send_event_frame(msg))

        # Full flush-queue parity with WS (#1885 / #1646): drain ALL 8 queues
        # in canonical order, not just push_events/navigation/deferred.
        await self._flush_all_pending()

    async def _send_render_frame(self, frame: Dict[str, Any]) -> None:
        """Attach a fresh public owner snapshot to this render, not a side frame.

        Receivers must install it only when applying the associated DOM update.
        Mount-path identity is separate from an embedded frame's child view_id.
        No owner objects or mutable manifests are cached between renders.
        """
        from ._parameter_metadata import parameter_contract_manifest

        try:
            manifest = parameter_contract_manifest(self.view_instance)
        except Exception:  # noqa: BLE001 — never send a DOM update with invalid contracts
            logger.warning("Render parameter contracts unavailable")
            await self.transport.send_error(
                "Render parameter contracts unavailable.", code="render_error"
            )
            return
        if manifest is not None or self._parameter_contracts_active:
            frame["parameter_contracts"] = manifest
            frame["parameter_contract_view"] = self._parameter_contract_view
            # Keep emitting explicit clears until remount: omission must not
            # revive a client's pre-clear contracts during deferred delivery.
            self._parameter_contracts_active = True
        await self.transport.send(frame)

    def _stamp_event_frame(
        self,
        view: Any,
        frame: Dict[str, Any],
        *,
        event_name: str,
        event_ref: Optional[int],
        render_ms: float,
        scope: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Stamp the render duration (+ ADR-032 ``scope``) and invoke the DEBUG
        ``on_event_frame`` fold (#1908) in place, then return the frame for
        ``transport.send``. The internal markers are ALWAYS popped before the
        frame leaves (the WS hook consumes them; SSE / partial test transports
        have a no-op or no hook), so they can never leak onto the wire."""
        frame["_timing_render_ms"] = render_ms
        if scope is not None:
            frame["_timing_scope"] = scope
        _hook = getattr(self.transport, "on_event_frame", None)
        if _hook is not None:
            _hook(
                view,
                frame,
                event_name=event_name,
                event_ref=event_ref,
            )
        frame.pop("_timing_render_ms", None)
        frame.pop("_timing_scope", None)
        return frame

    async def _render_scoped_component(
        self, view: Any, component: Any
    ) -> Optional[Tuple[str, str, int]]:
        """ADR-032 D1(d)(e) + D3: render ONE bound component and splice its
        subtree into the view's VDOM.

        Returns the ``render_with_diff`` triple ``(html, patches_json,
        version)`` — ``html`` is the page as the patched tree serialises, so
        recovery is armed with current markup (D4) — or ``None`` when any
        gate fails, in which case the caller takes today's page render in the
        same event (D6: not an error, DEBUG-logged).

        Gates beyond the caller's "only this slot changed":
        * the view's template is component-opaque for it (D2, cached);
        * no pending push events, the default HTML renderer (ADR-019:
          another renderer owns its own frame shape) — the callers have
          already read ``_force_full_html``;
        * the Rust view exists and finds exactly one node for the component.
        """
        rust_view = getattr(view, "_rust_view", None)
        patch = getattr(rust_view, "patch_component_subtree", None)
        if patch is None:
            return None
        # (``_force_full_html`` is read by both callers before they get here.)
        if getattr(view, "_pending_push_events", None):
            return None
        if getattr(view, "_djust_renderer", None) is not None:
            return None
        name = component.component_id
        if not _view_is_component_opaque(view, name):
            return None
        try:
            # Verify, do not predict: the page is exact only if nothing the
            # template can read changed besides the component — including
            # values DERIVED from it elsewhere on the view.
            context = await sync_to_async(view.get_context_data)()
            reason = _context_changed_besides(view, context, name)
            if reason is not None:
                logger.debug("Scoped render of %r: %r changed too; full render", name, reason)
                return None
            html = await sync_to_async(component.render)()
            # Component-sized parse + diff; runs inline on the event loop like
            # the JSON decode of the patches. Route a large component through
            # ``sync_to_async`` before making it larger.
            result = patch(name, html)
        except Exception as exc:  # noqa: BLE001 — D6: fall back to the full render
            from ._exposure_diagnostics import log_failure

            # get_context_data and the component's template are application code.
            log_failure(
                logger,
                exc,
                "Scoped render of component %r failed; full render",
                name,
                level="debug",
                traceback=True,
            )
            return None
        if result is None:
            logger.debug("Scoped render of component %r not exact; full render", name)
            return None
        # The slot is now in step with the Rust state (D3 sets it), so the
        # next sync starts from the same baseline a full render leaves — the
        # component's fingerprint included, so it is not re-sent unchanged.
        view._changed_keys = None
        try:
            from .change_detection import deep_fingerprint

            view._prev_context_fingerprints[name] = deep_fingerprint(context[name])[0]
        except Exception:  # noqa: BLE001 — bookkeeping; a miss only costs a re-sync
            pass
        timing = getattr(rust_view, "get_render_timing", None)
        if timing is not None:
            view._rust_render_timing = timing()
        html_out, patches_json, version = result
        return (str(html_out), str(patches_json), int(version))

    async def _send_scoped_patch_frame(
        self,
        view: Any,
        triple: Tuple[str, str, int],
        render_ms: float,
        *,
        event_name: str,
        event_ref: Optional[int],
        async_batch: Optional["AsyncBatch"] = None,
    ) -> None:
        """Emit the ``patch`` frame for a scoped render on the component
        dispatch path — the same shape ``_render_and_send`` emits (D4), with
        the wire version stamped from the page markup so recovery is current."""
        html, patches, version = triple
        wire_version = self.transport.next_client_version(html, version)
        msg: Dict[str, Any] = {
            "type": "patch",
            "patches": fast_json_loads(patches) if isinstance(patches, str) else patches,
            "version": wire_version,
            "event_name": event_name,
            "source": "event",
        }
        if event_ref is not None:
            msg["ref"] = event_ref
        if async_batch is not None:
            msg.update(async_batch.fields())
        await self._send_render_frame(
            self._stamp_event_frame(
                view,
                msg,
                event_name=event_name,
                event_ref=event_ref,
                render_ms=render_ms,
                scope="component",
            )
        )

    def _flush_push_events(self, view: Optional[Any] = None) -> None:
        """Drain push_events and send via the transport (sync-safe — pushes
        get queued but the actual send is fire-and-forget to keep the
        flush points cheap)."""
        view = self.view_instance if view is None else view
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
            except Exception as exc:
                from ._exposure_diagnostics import log_failure

                log_failure(
                    logger,
                    exc,
                    "[djust runtime] Deferred callback %s on %s raised; continuing",
                    getattr(callback, "__qualname__", repr(callback)),
                    view.__class__.__name__,
                    level="warning",
                    traceback=True,
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
        except Exception as exc:  # noqa: BLE001 — layout errors must not kill the wire
            from ._exposure_diagnostics import log_failure

            log_failure(
                logger,
                exc,
                "set_layout(%r) — template rendering raised; ignoring swap request",
                layout_path,
                traceback=True,
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

    def _dispatch_explicit_child_queues(self, event_name: Optional[str]) -> None:
        """Run explicit child work queued at mount or by a parent turn (E3-3).

        A routed child event drains its own child's queue. Work a child queued
        in ``mount()`` (during the parent's render) or that a parent handler
        queued on a child is otherwise stranded until that child's next routed
        event. Each owner gets its own batch and the owned, re-authorized
        completion path of :func:`djust._child_async.dispatch_child_work`; no
        parent acknowledgement advertises or completes it. Legacy roots and
        legacy children keep their existing behavior.
        """
        from ._exposure import uses_legacy_exposure

        root = self.view_instance
        if (
            root is None
            or uses_legacy_exposure(root)
            or self._explicit_mount_binding is None
            or getattr(root, "_djust_child_disposed", False)
        ):
            return
        # Imported past the legacy early return: this runs after every turn,
        # on the event loop (#3095).
        from ._async_batch import AsyncBatch
        from ._child_async import dispatch_child_work

        pending = [root]
        seen: set = set()
        owners = []
        while pending and len(seen) < 257:
            parent = pending.pop()
            if id(parent) in seen:
                continue
            seen.add(id(parent))
            registry = getattr(parent, "_child_views", None)
            if type(registry) is not dict:
                continue
            for slot, child in tuple(registry.items()):
                if (
                    getattr(child, "_parent_view", None) is not parent
                    or getattr(child, "_view_id", None) != slot
                    or getattr(child, "_djust_child_disposed", False)
                    or uses_legacy_exposure(child)
                ):
                    continue
                owners.append(child)
                pending.append(child)
        for child in owners:
            if not getattr(child, "_async_tasks", None) and not getattr(
                child, "_async_pending", None
            ):
                continue
            batch = AsyncBatch(child)
            if batch.token:
                dispatch_child_work(self, child, event_name, batch)

    def _dispatch_async_work(
        self, event_name: Optional[str], batch: Optional["AsyncBatch"] = None
    ) -> None:
        """Schedule any ``start_async`` callbacks queued during the handler.

        Supports both the named-task dict (``_async_tasks``) and the legacy
        single-task tuple (``_async_pending``) formats, matching
        ``LiveViewConsumer._dispatch_async_work``. Fire-and-forget: each task
        runs in its own ``ensure_future`` so the event POST returns promptly
        and results stream in via the transport when ready.

        Event callers pass the batch already advertised by their acknowledgement.
        Unmigrated lifecycle callers retain their existing queue-drain behavior.
        """
        view = self.view_instance
        if batch is not None:
            if view is not batch.owner:
                batch.discard(self.transport)
                return

            async def run_captured(name: str, callback: Any, args: Any, kwargs: Any) -> None:
                if self.view_instance is not batch.owner:
                    return
                await self._execute_async_task(name, callback, args, kwargs, event_name)

            batch.dispatch(self.transport, run_captured)
            self._dispatch_explicit_child_queues(event_name)
            return
        if not view:
            return
        self._dispatch_explicit_child_queues(event_name)

        from .mixins.async_work import track_async_task, track_running_async_task

        tasks = getattr(view, "_async_tasks", None)
        if tasks:
            for task_name, (callback, args, kwargs) in list(tasks.items()):
                future = asyncio.ensure_future(
                    self._execute_async_task(task_name, callback, args, kwargs, event_name)
                )
                track_async_task(view, future)
                track_running_async_task(view, task_name, future)
            view._async_tasks = {}

        pending = getattr(view, "_async_pending", None)
        if pending:
            view._async_pending = None
            callback, args, kwargs = pending
            future = asyncio.ensure_future(
                self._execute_async_task("_default", callback, args, kwargs, event_name)
            )
            track_async_task(view, future)
            track_running_async_task(view, "_default", future)

    async def _execute_async_task(
        self,
        task_name: str,
        callback: Callable[..., Any],
        args: Any,
        kwargs: Any,
        event_name: Optional[str],
    ) -> None:
        """Run one background task and stream the re-rendered result.

        Mirrors ``sse.py:_sse_execute_async_task``: run the callback off-thread,
        let ``handle_async_result`` observe success/failure, re-sync + re-render,
        and emit a ``patch`` / ``html_update`` frame plus a turn-end flush. Any
        callback failure is logged and routed through ``handle_async_result`` so
        the client is never left stuck in a loading state.

        The handler + render half of each arm runs under the consumer's render
        lock, borrowed via ``transport.event_context`` (#2840/#1646: same
        serialization as the WS twin ``_run_async_work``, which holds
        ``_render_lock`` across handler + render). SSE uses its session render
        lock to prevent late results from crossing a page-replacement boundary.
        """
        view = self.view_instance
        if not view:
            return
        from ._exposure import uses_legacy_exposure

        legacy_diagnostics = uses_legacy_exposure(view)
        if not legacy_diagnostics:
            await self._execute_explicit_async_task(
                view, task_name, callback, args, kwargs, event_name
            )
            return

        # cancel_async() / cancel_async_all() before the task started: skip it.
        # The WS twin ``_run_async_work`` has always checked this; the runtime
        # copy, which serves WS events since the ADR-022 flip, did not (#2969).
        if _consume_async_cancel(view, task_name):
            logger.debug("Runtime: async task %s was cancelled, skipping execution", task_name)
            await self._settle_cancelled_async(view, event_name)
            return

        try:
            # Dispatch through the ONE shared helper so the sync/async handling
            # can never drift from the consumer twin (#2020, #2016 / #1646).
            # Without the coroutine check the helper encapsulates, an async
            # callback would hit ``sync_to_async`` and raise ``TypeError:
            # sync_to_async can only be applied to sync functions``, silently
            # failing every async background task on the converged WS-event path
            # (#2001, the parallel-path drift vs ``websocket.py:_run_async_work``).
            from .mixins.async_work import run_async_callback

            result = await run_async_callback(callback, args, kwargs, owner=view)

            # Teardown identity-guard (#1940 — mirror of the WS twin's
            # pre-mutation guard): the callback above is the FIRST await in
            # this detached task; during that window a disconnect (→
            # ``view_instance = None``) or a re-mount (→ a NEW view) can run.
            # Writing the stale view — handler state OR render — contaminates
            # a torn-down / replaced view.
            if self.view_instance is not view:
                if legacy_diagnostics and uses_legacy_exposure(view):
                    logger.debug(
                        "Runtime: async task %s completed after view teardown/re-mount; "
                        "dropping stale re-render",
                        task_name,
                    )
                else:
                    logger.debug("Explicit background result discarded after owner replacement")
                return

            # Cancelled while running: skip the result handler and re-render,
            # as the WS twin does (#2969).
            if _consume_async_cancel(view, task_name):
                logger.debug("Runtime: async task %s was cancelled, skipping re-render", task_name)
                await self._settle_cancelled_async(view, event_name)
                return

            # Serialise handler + render on the consumer's render lock via
            # ``transport.event_context`` (the ADR-022 mechanism): after the
            # ADR-022 flip THIS helper serves WS events' ``start_async`` work,
            # so an unlocked handler mutation / render could interleave with a
            # concurrent tick / server_push / db_notify render — the PyO3
            # VDOM baseline is not thread-safe (#2830). ``event_context`` also
            # sets ``_processing_user_event`` (ticks yield during the frame)
            # and the observability scopes — the same per-event posture a live
            # event turn gets. Ordering mirrors the WS twin: handler first,
            # THEN the in-lock identity re-check (a view torn down mid-handler
            # still gets its user callback — only the stale re-render is
            # dropped), then the render.
            async with self.transport.event_context(view):
                if hasattr(view, "handle_async_result"):
                    await sync_to_async(view.handle_async_result)(
                        task_name, result=result, error=None
                    )
                # Identity re-check INSIDE the lock (#1940): the handler await
                # above is an unbounded wait during which the view can be torn
                # down or replaced. Rendering the stale view would emit a frame
                # the connection's version/recovery state does not own.
                if self.view_instance is not view:
                    return
                await self._render_async_result(event_name)

        except Exception as exc:
            if legacy_diagnostics and uses_legacy_exposure(view):
                logger.exception(
                    "Runtime: error in start_async callback '%s' on %s",
                    task_name,
                    view.__class__.__name__ if view else "?",
                )
            else:
                logger.warning("Explicit background callback failed")
            try:
                # Same locked shape as the success arm (#2840 twin): the
                # error-state mutation + re-render must not interleave with
                # a concurrent lock-holding render. Ordering preserved:
                # handler → in-lock identity re-check → render. The result
                # frame is sent even without a ``handle_async_result``: the
                # turn announced ``async_pending`` (#2963), and this frame is
                # what ends the client's loading state.
                async with self.transport.event_context(view):
                    if hasattr(view, "handle_async_result"):
                        await sync_to_async(view.handle_async_result)(
                            task_name, result=None, error=exc
                        )
                    if self.view_instance is not view:
                        return
                    await self._render_async_result(event_name)
            except Exception:
                if legacy_diagnostics and uses_legacy_exposure(view):
                    logger.exception(
                        "Runtime: error in handle_async_result for task '%s'", task_name
                    )
                else:
                    logger.warning("Explicit background result handling failed")

    async def _settle_cancelled_async(self, view: Any, event_name: Optional[str]) -> None:
        """End the loading state a cancelled task's event announced.

        The runtime twin of ``LiveViewConsumer._settle_cancelled_async``
        (#2963): the event's reply carried ``async_pending``, so the client
        keeps the event's loading state until a ``source="async"`` frame
        naming the event arrives. A cancelled task skips its result handler,
        so render the view's current state as that frame instead (#2969).
        Work no event owns (``event_name`` None) announced nothing.
        """
        if event_name is None:
            return
        from ._exposure import uses_legacy_exposure

        try:
            async with self.transport.event_context(view):
                if self.view_instance is not view:
                    return
                await self._render_async_result(event_name)
        except Exception:  # noqa: BLE001 — a settle frame must never raise out of a task
            if uses_legacy_exposure(view):
                logger.exception(
                    "Runtime: error settling cancelled async task for %s",
                    sanitize_for_log(event_name),
                )
            else:
                logger.warning("Explicit cancelled background settle failed")

    async def _execute_explicit_async_task(
        self,
        view: Any,
        task_name: str,
        callback: Callable[..., Any],
        args: Any,
        kwargs: Any,
        event_name: Optional[str],
    ) -> None:
        """Run a nonlegacy root's background task under fresh authority.

        The legacy runner trusts the mount-time principal and never saves. Here
        (ADR-038 E3, D-k/D-l) the callback starts only after a fresh
        authorization, the result is handled only after another one, declared
        server state is committed before the result frame, and a revoked or
        failed turn is dropped with a static error. Nothing here logs or sends
        exception values; application result handlers still get their own
        exception object.
        """
        from .mixins.async_work import run_async_callback

        # cancel_async() / cancel_async_all() before the task started (#2969).
        # No settle frame on this path: an event turn's batch ends the client's
        # loading state with its ``async_complete`` token, and unbatched
        # (server-originated) work carries ``event_name=None``, so no loading
        # state was announced.
        if _consume_async_cancel(view, task_name):
            return

        async with self._explicit_event_lock, self.transport.event_context(view):
            try:
                await self.authorize_explicit_turn(view)
            except Exception:  # noqa: BLE001 — no auth provider values on the wire/log
                if self.view_instance is view:
                    await self.deny_explicit_turn()
                return
            finally:
                view.__dict__.pop("_djust_event_request", None)

        result = error = None
        try:
            result = await run_async_callback(callback, args, kwargs, owner=view)
        except Exception as exc:  # noqa: BLE001 — only the owning application sees it
            from ._exposure_diagnostics import log_failure_for

            error = exc
            log_failure_for(
                logger,
                (view,),
                exc,
                "Runtime: error in start_async callback '%s' on %s",
                task_name,
                type(view).__name__,
                level="warning",
                traceback=True,
            )

        if self.view_instance is not view:
            logger.debug("Explicit background result discarded after owner replacement")
            return
        # Cancelled while running: skip the result handler and render (#2969).
        if _consume_async_cancel(view, task_name):
            return

        async with self._explicit_event_lock, self.transport.event_context(view):
            try:
                try:
                    await self.authorize_explicit_turn(view)
                except Exception:  # noqa: BLE001
                    if self.view_instance is view:
                        await self.deny_explicit_turn()
                    return
                handler = getattr(view, "handle_async_result", None)
                if callable(handler):
                    try:
                        await sync_to_async(handler)(task_name, result=result, error=error)
                    except Exception as exc:  # noqa: BLE001
                        from ._exposure_diagnostics import log_failure_for

                        log_failure_for(
                            logger,
                            (view,),
                            exc,
                            "Runtime: error in handle_async_result for task '%s'",
                            task_name,
                            level="warning",
                            traceback=True,
                        )
                        return
                elif error is not None:
                    # Legacy parity: an unhandled failure renders nothing.
                    return
                if self.view_instance is not view:
                    return
                if not await self.commit_explicit_turn(view, source="async"):
                    return
                snapshot_fields = await self._explicit_event_snapshot(view)
                await self._render_async_result(event_name, snapshot_fields=snapshot_fields)
            finally:
                view.__dict__.pop("_djust_event_request", None)

    async def _render_async_result(
        self, event_name: Optional[str], snapshot_fields: Optional[Dict[str, Any]] = None
    ) -> None:
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
        if snapshot_fields:
            # An explicit root's refreshed signed snapshot (ADR-038 E3).
            msg.update(snapshot_fields)
        await self._send_render_frame(msg)
        await self._flush_all_pending()
