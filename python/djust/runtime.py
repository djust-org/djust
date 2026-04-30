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
    def session_id(self) -> str: ...

    @property
    def client_ip(self) -> Optional[str]: ...

    async def send(self, data: Dict[str, Any]) -> None: ...

    async def send_error(self, error: str, **kwargs: Any) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


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
        self._render_lock = asyncio.Lock()

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
        page_url = data.get("url", "/")
        client_timezone = data.get("client_timezone")

        if not view_path:
            await self.transport.send_error("Missing view path in mount request")
            return

        # ---- Security: module allowlist ----
        from django.conf import settings

        allowed_modules = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", [])
        if allowed_modules and not any(view_path.startswith(m) for m in allowed_modules):
            logger.warning(
                "Blocked attempt to mount view from unauthorized module: %s",
                sanitize_for_log(view_path),
            )
            await self.transport.send_error(
                _safe_error(f"View {view_path} is not in allowed modules", "View not found")
            )
            return

        # ---- Instantiate view (extension hook for tests) ----
        view_instance = self._instantiate_view(view_path)
        if view_instance is None:
            return  # _instantiate_view already pushed an error

        self.view_instance = view_instance

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

        # ---- Auth check ----
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
        """
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
        """
        if not self.view_instance:
            await self.transport.send_error("View not mounted")
            return

        params = data.get("params", {})
        uri = data.get("uri", "")

        try:
            await sync_to_async(self.view_instance.handle_params)(params, uri)

            if hasattr(self.view_instance, "_sync_state_to_rust"):
                await sync_to_async(self.view_instance._sync_state_to_rust)()

            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

            # Allow views to force full HTML by setting _force_full_html = True
            if getattr(self.view_instance, "_force_full_html", False):
                self.view_instance._force_full_html = False
                patches = None

            if patches is not None:
                if isinstance(patches, str):
                    patches = fast_json_loads(patches)
                msg: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patches,
                    "version": version,
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
                    "version": version,
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

        try:
            module_path, class_name = view_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            view_class = getattr(module, class_name)
        except (ValueError, ImportError, AttributeError) as exc:
            error_msg = f"Failed to load view {view_path}: {exc}"
            logger.error("Failed to load view %s: %s", sanitize_for_log(view_path), exc)
            asyncio.ensure_future(
                self.transport.send(
                    {"type": "error", "error": _safe_error(error_msg, "View not found")}
                )
            )
            return None

        # Must be a LiveView subclass.
        from .live_view import LiveView

        if not (isinstance(view_class, type) and issubclass(view_class, LiveView)):
            error_msg = f"Security: {view_path} is not a LiveView subclass."
            logger.error("Security: %s is not a LiveView subclass.", sanitize_for_log(view_path))
            asyncio.ensure_future(
                self.transport.send(
                    {"type": "error", "error": _safe_error(error_msg, "Invalid view class")}
                )
            )
            return None

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

        factory = RequestFactory()
        query_string = urlencode(params) if params else ""
        path_with_query = f"{page_url}?{query_string}" if query_string else page_url
        request = factory.get(path_with_query)

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
        """Run auth check. Returns:
        - ``None`` if mount may proceed.
        - Truthy value if auth blocked (and a navigate/error frame was sent).
        """
        try:
            from .auth import check_view_auth
            from django.core.exceptions import PermissionDenied

            try:
                redirect_url = await sync_to_async(check_view_auth)(self.view_instance, request)
            except PermissionDenied:
                await self.transport.send({"type": "error", "error": "Permission denied"})
                return True

            if redirect_url:
                await self.transport.send({"type": "navigate", "to": redirect_url})
                return True
        except Exception as exc:
            logger.warning("Runtime: auth check error: %s", exc)
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
                    "version": version,
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
                    "version": version,
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
                "version": version,
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
