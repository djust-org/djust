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
import inspect
import logging
import time
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

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
        renderer_factory: Optional[Any] = None,
    ) -> None:
        self.transport = transport
        self.view_instance: Optional[Any] = None
        self.scope = scope
        self._rate_limiter = rate_limiter or ConnectionRateLimiter()
        self._render_lock = asyncio.Lock()
        # ADR-019 LVN-I PR-2: Renderer factory plumbed through. Stored
        # for PR-3 (handshake) to set based on ``?platform=`` selection;
        # ``None`` means "use the default ``HtmlRenderer``" which the
        # dispatch site (``TemplateMixin.render_with_diff:942``) already
        # constructs inline. Type kept ``Any`` to avoid circular import
        # with ``djust.renderers``; runtime use-site will cast.
        self.renderer_factory = renderer_factory

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

        # ADR-019 LVN-I: if the handshake selected a renderer (factory
        # passed in via __init__), bind it to the view so
        # TemplateMixin.render_with_diff can dispatch through it
        # instead of always constructing HtmlRenderer inline.
        if self.renderer_factory is not None:
            view_instance._djust_renderer = self.renderer_factory(view_instance)

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

        # ---- Mount kwargs ----
        mount_kwargs = dict(params)
        url_kwargs = self._resolve_url_kwargs(page_url)
        if url_kwargs:
            mount_kwargs.update(url_kwargs)

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
            "html": html,
            "has_ids": "dj-id=" in (html or ""),
        }

        # Optional cache_config (mirrors WS consumer)
        cache_config = self._extract_cache_config(view_instance)
        if cache_config:
            mount_msg["cache_config"] = cache_config

        await self.transport.send(mount_msg)
        logger.info(
            "Runtime: mounted view %s (session %s)",
            sanitize_for_log(view_path),
            sanitize_for_log(self.session_id),
        )

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
        """Event dispatch body (see :meth:`dispatch_event` for the tenant wrapper)."""
        if not self.view_instance:
            await self.transport.send_error("View not mounted. Please reload the page.")
            return

        event_name = data.get("event")
        params: Dict[str, Any] = dict(data.get("params") or {})

        if not event_name:
            await self.transport.send_error("Missing 'event' field")
            return

        start_time = time.perf_counter()

        # Strip internal params (matches WS handler)
        cache_request_id = params.pop("_cacheRequestId", None)
        positional_args = params.pop("_args", [])

        # Security
        handler = await _validate_event_security(
            self.transport, event_name, self.view_instance, self._rate_limiter
        )
        if handler is None:
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

        # Call handler
        try:
            await _call_handler(handler, coerced_params if coerced_params else None)
        except Exception as exc:
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

        # Auto-detect unchanged state
        skip_render = getattr(self.view_instance, "_skip_render", False)
        if not skip_render:
            post_assigns = _snapshot_assigns(self.view_instance)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                self.view_instance._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        has_async = getattr(self.view_instance, "_async_pending", None) is not None

        if skip_render:
            self.view_instance._skip_render = False
            self._flush_push_events()
            noop_msg: Dict[str, Any] = {"type": "noop"}
            if has_async:
                noop_msg["async_pending"] = True
            await self.transport.send(noop_msg)
            return

        # Render
        await self._render_and_send(
            event_name=event_name,
            cache_request_id=cache_request_id,
            has_async=has_async,
        )

        logger.debug(
            "Runtime: event '%s' handled in %.1fms",
            sanitize_for_log(event_name),
            (time.perf_counter() - start_time) * 1000,
        )

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

            self._flush_push_events()
            self._flush_navigation()
            await self._flush_deferred()
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

        For SSE transports we don't have a real ASGI request; we synthesize
        one via RequestFactory. WS-backed runtimes already have a real
        request object on the consumer; we still synthesize here for
        symmetry — handlers should never assume the request came from a
        socket.
        """
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

    async def _render_and_send(
        self,
        *,
        event_name: str,
        cache_request_id: Optional[str] = None,
        has_async: bool = False,
    ) -> None:
        """Re-render after an event handler and emit the appropriate frame.

        Decides between ``patch`` (VDOM diff available) and ``html_update``
        (no diff or compression fallback). Mirrors the legacy
        ``_sse_handle_event`` render branch.
        """
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
            if patch_list and len(patch_list) > PATCH_THRESHOLD:
                patches_size = len(patches.encode("utf-8")) if isinstance(patches, str) else 0
                html_size = len(html.encode("utf-8")) if html else 0
                if patches_size and html_size < patches_size * 0.7:
                    if hasattr(self.view_instance, "_rust_view") and self.view_instance._rust_view:
                        self.view_instance._rust_view.reset()
                    patch_list = None

            if patch_list is not None:
                msg: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patch_list,
                    "version": wire_version,
                    "event_name": event_name,
                }
                if cache_request_id:
                    msg["cache_request_id"] = cache_request_id
                if should_reset_form:
                    msg["reset_form"] = True
                if has_async:
                    msg["async_pending"] = True
                await self.transport.send(msg)
            else:
                # Compression fallback — send full HTML.
                html_stripped = self.view_instance._strip_comments_and_whitespace(html)
                html_content = self.view_instance._extract_liveview_content(html_stripped)
                msg = {
                    "type": "html_update",
                    "html": html_content,
                    "version": wire_version,
                    "event_name": event_name,
                }
                if cache_request_id:
                    msg["cache_request_id"] = cache_request_id
                if should_reset_form:
                    msg["reset_form"] = True
                if has_async:
                    msg["async_pending"] = True
                await self.transport.send(msg)
        else:
            # No VDOM diff available — send HTML directly.
            if html and hasattr(self.view_instance, "_strip_comments_and_whitespace"):
                html = self.view_instance._strip_comments_and_whitespace(html)
            if html and hasattr(self.view_instance, "_extract_liveview_content"):
                html = self.view_instance._extract_liveview_content(html)
            msg = {
                "type": "html_update",
                "html": html,
                "version": wire_version,
                "event_name": event_name,
            }
            if cache_request_id:
                msg["cache_request_id"] = cache_request_id
            if should_reset_form:
                msg["reset_form"] = True
            if has_async:
                msg["async_pending"] = True
            await self.transport.send(msg)

        self._flush_push_events()
        self._flush_navigation()
        await self._flush_deferred()

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

    def _flush_navigation(self) -> None:
        """Drain navigation commands and send via the transport."""
        view = self.view_instance
        if not view or not hasattr(view, "_drain_navigation"):
            return
        for cmd in view._drain_navigation():
            action = cmd.get("type")
            payload = {k: v for k, v in cmd.items() if k != "type"}
            asyncio.ensure_future(
                self.transport.send({"type": "navigation", "action": action, **payload})
            )

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
