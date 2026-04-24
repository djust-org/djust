"""
WebSocket consumer for LiveView real-time updates
"""

import asyncio
import inspect
import json
import logging
import msgpack
from typing import Any, Dict, Optional
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from .serialization import DjangoJSONEncoder, fast_json_loads
from .validation import validate_handler_params
from .profiler import profiler
from .security import handle_exception, sanitize_for_log
from .config import config as djust_config
from .rate_limit import ConnectionRateLimiter, ip_tracker
from .websocket_utils import (
    _call_handler,
    _check_event_security,  # noqa: F401 - re-exported for tests
    _ensure_handler_rate_limit,  # noqa: F401 - re-exported for tests
    _safe_error,
    _validate_event_security,
    get_handler_coerce_setting,
)
from .presence import PresenceManager
from .signals import full_html_update, liveview_server_error

logger = logging.getLogger(__name__)
hotreload_logger = logging.getLogger("djust.hotreload")

__all__ = [
    "LiveViewConsumer",
    "_check_event_security",
    "_ensure_handler_rate_limit",
]

try:
    from ._rust import create_session_actor, SessionActorHandle
except ImportError:
    create_session_actor = None
    SessionActorHandle = None


_IMMUTABLE_TYPES = (str, int, float, bool, type(None), bytes, tuple, frozenset)


def _is_allowed_origin(origin: Optional[bytes]) -> bool:
    """
    Check whether a WebSocket Origin header is allowed under settings.ALLOWED_HOSTS.

    Policy:
      * Missing/empty Origin header -> ALLOW. Non-browser clients (curl, Python
        WebsocketCommunicator, native mobile) do not send an Origin header, and
        blocking them would break legitimate integrations and every existing
        test that uses WebsocketCommunicator without explicit headers.
        Browsers always send Origin on cross-origin WS handshakes, so a CSWSH
        attacker cannot forge a missing header from a victim's browser.
      * Non-ASCII / malformed Origin -> REJECT. Malformed headers are never
        legitimate.
      * Otherwise extract the host (stripping scheme, port, brackets, path) and
        compare against settings.ALLOWED_HOSTS using Django's own
        django.http.request.validate_host() so wildcard (".example.com", "*")
        semantics match Django's HTTP layer exactly.

    This helper is defense-in-depth: DjustMiddlewareStack also wraps routers
    in channels.security.websocket.AllowedHostsOriginValidator by default, but
    the consumer-level check still protects apps that route directly to
    LiveViewConsumer without going through DjustMiddlewareStack.

    Note on userinfo smuggling: a hostile string like
    ``https://evil.example@target.com/`` parses via ``urlparse`` to
    ``hostname="target.com"`` (RFC 3986 — "evil.example" is the userinfo).
    This is safe because RFC 6454 §7 explicitly forbids browsers from
    serializing userinfo in the ``Origin`` header, so a real victim browser
    will never actually send such a string. Even if an attacker constructed
    one by hand outside a browser, the extracted host is "target.com" — the
    attacker's claim is effectively "I'm on target.com", and that's what we
    check against ALLOWED_HOSTS. There's no cross-host authority gain.

    Similarly, a hostile string like ``https://target.com.evil.com/`` parses
    to ``hostname="target.com.evil.com"``, which Django's ``validate_host``
    correctly rejects when ``target.com`` is listed as an exact entry in
    ALLOWED_HOSTS.

    See #653 (CSWSH pentest finding, 2026-04-10).
    """
    if not origin:
        return True  # non-browser client
    try:
        origin_str = origin.decode("ascii")
    except (UnicodeDecodeError, AttributeError):
        return False

    # Parse the origin into a host (no scheme, no port, no path).
    from urllib.parse import urlparse

    try:
        parsed = urlparse(origin_str)
    except ValueError:
        return False

    host = parsed.hostname  # urlparse strips scheme, port, brackets, and path
    if host is None:
        # "null" origin (sandboxed iframes, file://) or an unparseable value.
        # Reject conservatively: a browser that sends "null" is not on an
        # allowed host by any definition.
        return False

    # urlparse strips the brackets from IPv6 literals, but Django's
    # ALLOWED_HOSTS / get_host() stores IPv6 addresses WITH brackets
    # (e.g. "[::1]"). Re-add them so validate_host() matches correctly.
    if ":" in host:
        match_host = f"[{host.lower()}]"
    else:
        match_host = host.lower()

    from django.conf import settings
    from django.http.request import validate_host

    allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    if not allowed_hosts:
        # Match Django's HTTP layer: in DEBUG, fall back to localhost variants.
        # In production, refuse rather than fail-open.
        if getattr(settings, "DEBUG", False):
            allowed_hosts = [".localhost", "127.0.0.1", "[::1]"]
        else:
            return False

    return validate_host(match_host, allowed_hosts)


def _should_expose_timing() -> bool:
    """
    Whether VDOM patch responses may include server-side timing/performance data.

    Returns True if either:
      * ``settings.DEBUG`` is True (development), OR
      * ``settings.DJUST_EXPOSE_TIMING`` is True (opt-in for staging/profiling).

    Returns False in production by default. The gating is load-bearing:
    timing/performance metadata enables side-channel attacks (code-path
    differentiation by handler duration, internal handler/phase name
    disclosure, load-based DoS scheduling) when combined with CSWSH (#653).
    In debug mode the browser debug panel still receives timing via the
    ``_attach_debug_payload`` helper, which has its own DEBUG gate — this
    check only controls the *top-level* ``response["timing"]`` /
    ``response["performance"]`` fields that are visible to every client.

    Helper form (not a module constant) so ``django.test.override_settings``
    works at runtime — the function reads settings each call.

    See #654 (pentest finding 2026-04-10).
    """
    from django.conf import settings

    return bool(
        getattr(settings, "DEBUG", False) or getattr(settings, "DJUST_EXPOSE_TIMING", False)
    )


def _snapshot_assigns(view_instance):
    """Fast identity+hash snapshot of public assigns for change detection.

    Uses id() for all values plus a shallow fingerprint for mutable
    containers (list length, dict length+keys, set length) to detect
    common in-place mutations without the cost of copy.deepcopy().

    This is ~100x faster than deep copy for views with many attributes.
    Trade-off: deep nested mutations (e.g., items[0]['name'] = 'x')
    are NOT detected. For those, use self._changed_keys or @event_handler
    which explicitly marks state as dirty.
    """
    # #762: Filter framework-internal attrs so change detection doesn't fire
    # on attrs like ``template_name`` / ``http_method_names`` that the user
    # never touches.
    from .live_view import _FRAMEWORK_INTERNAL_ATTRS

    _static_skip = set(getattr(view_instance, "static_assigns", []))
    snapshot = {}
    for k, v in view_instance.__dict__.items():
        if k.startswith("_") or k in _static_skip or k in _FRAMEWORK_INTERNAL_ATTRS:
            continue
        # Identity + shallow fingerprint for mutable containers
        vid = id(v)
        if isinstance(v, list):
            # Include a content fingerprint to catch in-place mutations
            # inside the list (e.g., todo['completed'] = True, matrix[0].append(5)).
            if v and len(v) < 100:
                try:
                    content_fp = hash(
                        tuple(
                            (
                                id(item),
                                tuple(item.values())
                                if isinstance(item, dict) and len(item) < 10
                                else id(item),
                            )
                            for item in v
                        )
                    )
                    snapshot[k] = (vid, len(v), content_fp)
                except TypeError:
                    # Unhashable values — fall back to id+length only
                    snapshot[k] = (vid, len(v))
            else:
                snapshot[k] = (vid, len(v))
        elif isinstance(v, dict):
            snapshot[k] = (vid, len(v), tuple(v.keys()) if len(v) < 50 else len(v))
        elif isinstance(v, set):
            snapshot[k] = (vid, len(v))
        elif isinstance(v, _IMMUTABLE_TYPES):
            snapshot[k] = v
        else:
            # For other objects, just use id — reassignment is detected,
            # in-place mutation is not (same as Phoenix LiveView).
            snapshot[k] = vid
    return snapshot


def _compute_changed_keys(pre, post):
    """Return set of keys that differ between two snapshots.

    Detects added, removed, and modified keys (by identity or fingerprint).
    """
    changed = set()
    for k in set(pre) | set(post):
        if k not in pre or k not in post:
            changed.add(k)
        elif pre[k] != post[k]:
            changed.add(k)
    return changed


def _build_context_snapshot(context, max_value_len=100):
    """Build a JSON-safe snapshot of template context for diagnostics.

    Truncates long values, converts non-serializable types to repr strings,
    and limits to 20 keys to keep the payload small.
    """
    snapshot = {}
    for key, value in list(context.items())[:20]:
        if isinstance(value, (str, int, float, bool, type(None))):
            if isinstance(value, str) and len(value) > max_value_len:
                snapshot[key] = value[:max_value_len] + "..."
            else:
                snapshot[key] = value
        elif isinstance(value, (list, tuple)):
            snapshot[key] = f"[{type(value).__name__}, len={len(value)}]"
        elif isinstance(value, dict):
            snapshot[key] = f"[dict, {len(value)} keys]"
        else:
            snapshot[key] = f"[{type(value).__name__}]"
    return snapshot


def _emit_liveview_server_error(view_instance, error: str, context: dict) -> None:
    """Emit the liveview_server_error signal from send_error()."""
    if view_instance is not None:
        view_cls = view_instance.__class__
        view_name = f"{view_cls.__module__}.{view_cls.__qualname__}"
    else:
        view_cls = None
        view_name = ""
    liveview_server_error.send(
        sender=view_cls,
        error=error,
        view_name=view_name,
        context=context,
    )


def _emit_full_html_update(
    view_instance,
    reason,
    event_name,
    html,
    version,
    patch_count=None,
    context_snapshot=None,
    html_snippet=None,
    previous_html_snippet=None,
):
    """Emit the full_html_update signal with context about why patches weren't used."""
    view_cls = view_instance.__class__
    view_name = f"{view_cls.__module__}.{view_cls.__qualname__}"
    html_size = len(html.encode("utf-8")) if html else 0
    previous_html_size = getattr(view_instance, "_previous_html_size", None)
    full_html_update.send(
        sender=view_cls,
        reason=reason,
        event_name=event_name,
        view_name=view_name,
        html_size=html_size,
        previous_html_size=previous_html_size,
        patch_count=patch_count,
        version=version,
        context_snapshot=context_snapshot,
        html_snippet=html_snippet,
        previous_html_snippet=previous_html_snippet,
    )


def _find_sticky_slot_ids(html: str) -> set[str]:
    """Return the set of ``dj-sticky-slot`` attribute values in ``html``.

    Uses ``html.parser.HTMLParser`` (stdlib) — NEVER a regex — so that
    quoted attribute values containing ``>`` and other HTML5 edge cases
    don't derail the scan. The caller is
    :meth:`LiveViewConsumer.handle_live_redirect_mount`, which uses the
    result to decide which preserved sticky children to reattach on the
    new parent.
    """
    if not html:
        return set()
    from html.parser import HTMLParser as _HTMLParser

    class _SlotCollector(_HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.ids: set[str] = set()

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name == "dj-sticky-slot" and value:
                    self.ids.add(value)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

    p = _SlotCollector()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — defensive; malformed HTML must not crash redirect
        logger.warning("sticky-slot parse failed; returning empty set", exc_info=True)
        return set()
    return p.ids


class LiveViewConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for handling LiveView connections.

    This consumer handles:
    - Initial connection and session setup
    - Event dispatching from client
    - Sending DOM patches to client
    - Session state management
    - File uploads via binary WebSocket frames
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view_instance: Optional[Any] = None
        self.actor_handle: Optional[SessionActorHandle] = None
        self.session_id: Optional[str] = None
        self.use_binary = False  # Use JSON for now (MessagePack support TODO)
        self.use_actors = False  # Will be set based on view class
        self._view_group: Optional[str] = None
        self._presence_group: Optional[str] = None
        self._tick_task = None
        # Hot View Replacement (v0.6.1): per-consumer dedup + version
        # counter. ``_hvr_last_reload_id`` drops duplicate broadcasts
        # within a rapid-save burst; ``_hvr_version`` increments on each
        # applied class swap and is echoed to the client for telemetry.
        self._hvr_version: int = 0
        self._hvr_last_reload_id: Optional[str] = None
        # Sticky LiveViews (Phase B): per-connection stash of preserved
        # sticky children staged in handle_live_redirect_mount BEFORE the
        # old view is torn down. Re-registered on the new parent after
        # its mount completes.
        self._sticky_preserved: Dict[str, Any] = {}
        # Render lock: serializes tick and event render operations so they
        # cannot concurrently access view_instance state or increment the
        # VDOM version. This prevents the version mismatch race in #560.
        self._render_lock = asyncio.Lock()
        # Track whether a user event is currently being processed so ticks
        # can yield priority to user interactions.
        self._processing_user_event = False

    async def _flush_push_events(self) -> None:
        """
        Send any pending push_event messages queued by the view during handler execution.

        Called after each _send_update to deliver server-pushed events to the client.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_push_events"):
            return
        events = self.view_instance._drain_push_events()
        for event_name, payload in events:
            await self.send_json(
                {
                    "type": "push_event",
                    "event": event_name,
                    "payload": payload,
                }
            )

    async def _flush_flash(self) -> None:
        """
        Send any pending flash messages queued by the view during handler execution.

        Called after each _send_update to deliver flash notifications to the client.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_flash"):
            return
        commands = self.view_instance._drain_flash()
        if not isinstance(commands, list):
            return
        for cmd in commands:
            await self.send_json(
                {
                    "type": "flash",
                    **cmd,
                }
            )

    async def _flush_page_metadata(self) -> None:
        """
        Send any pending page metadata commands queued by the view.

        Called after each _send_update to deliver title/meta updates to the client.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_page_metadata"):
            return
        commands = self.view_instance._drain_page_metadata()
        if not isinstance(commands, list):
            return
        for cmd in commands:
            await self.send_json(
                {
                    "type": "page_metadata",
                    **cmd,
                }
            )

    async def _flush_pending_layout(self) -> None:
        """Send a pending layout-swap command queued by the view (v0.6.0).

        Called after ``_flush_page_metadata`` so the layout swap
        (replaces ``<body>``) is the last thing the client applies.
        Any pending ``page_title`` / ``page_meta`` frames mutate
        ``<head>`` and are delivered first; they survive the swap.

        If ``view.set_layout(path)`` was called during the handler,
        render ``path`` with the view's current context and emit a
        ``{"type": "layout", "path": ..., "html": ...}`` frame. The
        client swaps the document body while preserving the live
        ``[dj-root]`` element's identity (and therefore all inner
        LiveView state).

        Error handling: ``TemplateDoesNotExist`` always warns and
        skips. In DEBUG mode any other exception is re-raised so
        programmer errors (``TemplateSyntaxError``, ``NoReverseMatch``,
        attribute errors from ``get_context_data``) surface during
        development. In production (``DEBUG=False``) the exception is
        caught and logged so a broken layout template can't crash the
        whole WebSocket consumer — the handler's VDOM patches still
        flush.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_pending_layout"):
            return
        layout_path = self.view_instance._drain_pending_layout()
        if not layout_path:
            return
        from django.conf import settings as django_settings
        from django.template.exceptions import TemplateDoesNotExist
        from django.template.loader import render_to_string

        try:
            context = (
                self.view_instance.get_context_data()
                if hasattr(self.view_instance, "get_context_data")
                else {}
            )
            layout_html = render_to_string(layout_path, context)
        except TemplateDoesNotExist:
            logger.warning(
                "set_layout(%r) — template not found; ignoring swap request",
                layout_path,
            )
            return
        except Exception:  # noqa: BLE001 — layout errors must not kill the WS
            logger.exception(
                "set_layout(%r) — template rendering raised; ignoring swap request",
                layout_path,
            )
            # In DEBUG, re-raise so programmer errors are visible.
            # TemplateSyntaxError / NoReverseMatch / missing-context-key
            # bugs need to surface loudly during iteration.
            if getattr(django_settings, "DEBUG", False):
                raise
            return
        await self.send_json(
            {
                "type": "layout",
                "path": layout_path,
                "html": layout_html,
            }
        )

    async def _send_noop(self, async_pending: bool = False, ref: Optional[int] = None) -> None:
        """
        Send a lightweight noop acknowledgment to the client.

        Tells the client the event was processed but no DOM update is needed.
        The client clears loading state (spinners, disabled buttons) without
        touching the DOM.

        Args:
            async_pending: If True, tells the client to keep loading state active
                because a start_async() callback is running in the background.
            ref: Event reference number echoed back from the client's request (#560).
        """
        msg: Dict[str, Any] = {"type": "noop"}
        if async_pending:
            msg["async_pending"] = True
        if ref is not None:
            msg["ref"] = ref
        await self.send_json(msg)

    async def _send_child_update(
        self,
        view_id: str,
        patches: list,
        version: int,
    ) -> None:
        """Send a VDOM patch frame targeted at a specific child view.

        Phase A of Sticky LiveViews introduces the ``child_update`` wire
        frame: the client's ``45-child-view.js`` module scopes the patches
        to the child's subtree (selector
        ``[dj-view][data-djust-embedded="..."]``) so patch coordinates
        don't collide with the parent view's VDOM.

        Phase B added the sibling ``sticky_update`` frame for sticky
        preservation across live_redirect (see :meth:`_send_sticky_update`).

        Args:
            view_id: The child's ``view_id`` as assigned by
                ``StickyChildRegistry._assign_view_id``.
            patches: VDOM patch list, same shape as ``html_update``.
            version: Child-local VDOM version number.
        """
        await self.send_json(
            {
                "type": "child_update",
                "view_id": view_id,
                "patches": patches,
                "version": version,
            }
        )

    async def _send_sticky_update(
        self,
        view_id: str,
        patches: list,
        version: int,
    ) -> None:
        """Send a VDOM patch frame targeted at a preserved sticky child.

        Phase B of Sticky LiveViews. ``sticky_update`` is the sibling of
        ``child_update`` (Phase A) — same ``{view_id, patches, version}``
        shape, but the client scopes the patches to the sticky subtree
        (``[dj-sticky-view="<id>"]``) via the new
        ``applyPatches(patches, rootEl)`` variant rather than the parent
        view's root. This lets the sticky child re-render without
        colliding with the parent's VDOM coordinates.

        Args:
            view_id: The sticky child's ``sticky_id`` (also its
                ``view_id`` on the parent registry).
            patches: VDOM patch list, same shape as ``html_update``.
            version: Per-child VDOM version number; tracked on the client
                via ``clientVdomVersions: Map<view_id, number>``.
        """
        await self.send_json(
            {
                "type": "sticky_update",
                "view_id": view_id,
                "patches": patches,
                "version": version,
            }
        )

    async def _flush_navigation(self) -> None:
        """
        Send any pending navigation commands (live_patch / live_redirect)
        queued by the view during handler execution.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_navigation"):
            return
        commands = self.view_instance._drain_navigation()
        for cmd in commands:
            # Promote cmd's "type" (e.g. "live_patch") to "action" so it doesn't
            # collide with the outer message "type" key.
            action = cmd.get("type")
            payload = {k: v for k, v in cmd.items() if k != "type"}
            await self.send_json(
                {
                    "type": "navigation",
                    "action": action,
                    **payload,
                }
            )

    async def _flush_i18n(self) -> None:
        """
        Send any pending i18n commands (language changes, etc.)
        queued by the view during handler execution.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_i18n_commands"):
            return
        commands = self.view_instance._drain_i18n_commands()
        for cmd in commands:
            await self.send_json(
                {
                    "type": "i18n",
                    **cmd,
                }
            )

    async def _flush_accessibility(self) -> None:
        """
        Send any pending accessibility commands (announcements, focus)
        queued by the view during handler execution.
        """
        if not self.view_instance:
            return

        # Flush screen reader announcements
        if hasattr(self.view_instance, "_drain_announcements"):
            try:
                announcements = self.view_instance._drain_announcements()
                if announcements and isinstance(announcements, list) and len(announcements) > 0:
                    await self.send_json(
                        {
                            "type": "accessibility",
                            "announcements": announcements,
                        }
                    )
            except Exception:
                logger.warning("Failed to flush accessibility announcements", exc_info=True)

        # Flush focus command
        if hasattr(self.view_instance, "_drain_focus"):
            try:
                focus_cmd = self.view_instance._drain_focus()
                if focus_cmd and isinstance(focus_cmd, tuple) and len(focus_cmd) == 2:
                    selector, options = focus_cmd
                    await self.send_json(
                        {
                            "type": "focus",
                            "selector": selector,
                            "options": options,
                        }
                    )
            except Exception:
                logger.warning("Failed to flush focus command", exc_info=True)

    async def send_error(self, error: str, **context) -> None:
        """
        Send an error response to the client with consistent formatting.
        Also emits the liveview_server_error signal for monitor integrations.

        In DEBUG mode, includes additional fields for developer diagnostics:
        - ``debug_detail``: unsanitized error message
        - ``traceback``: abbreviated traceback (last 3 frames)
        - ``hint``: actionable suggestion when available
        """
        import traceback as tb_module

        from django.conf import settings

        # Keys that are debug-only and should never appear in production
        _debug_keys = {"debug_detail", "hint", "_exc_info"}

        is_debug = getattr(settings, "DEBUG", False)

        # Build base response, excluding debug-only keys from context
        response: Dict[str, Any] = {"type": "error", "error": error}
        for k, v in context.items():
            if k not in _debug_keys:
                response[k] = v

        if is_debug:
            # Include the raw detail if a sanitised version was used
            debug_detail = context.get("debug_detail")
            if debug_detail and debug_detail != error:
                response["debug_detail"] = debug_detail

            # Abbreviated traceback (last 3 frames) from the current exception
            exc_info = context.get("_exc_info")
            if exc_info is None:
                import sys as _sys

                exc_info = _sys.exc_info()
            if exc_info and exc_info[2] is not None:
                frames = tb_module.format_tb(exc_info[2])
                response["traceback"] = "".join(frames[-3:])

            # Actionable hint
            hint = context.get("hint")
            if hint:
                response["hint"] = hint

        await self.send_json(response)
        _emit_liveview_server_error(getattr(self, "view_instance", None), error, context)

    async def _dispatch_async_work(self) -> None:
        """
        Check if the handler scheduled background work via start_async().

        If _async_tasks is set, spawn each callback as an asyncio task
        so they run after the current response is sent to the client.
        When each callback completes, re-render and send updated patches.

        Supports both new multi-task dict format (_async_tasks) and
        legacy single-task tuple format (_async_pending) for backward
        compatibility.
        """
        if not self.view_instance:
            return

        # New format: multiple named tasks
        tasks = getattr(self.view_instance, "_async_tasks", None)
        if tasks:
            event_name = getattr(self, "_current_event_name", None)
            # Spawn all pending tasks
            for task_name, (callback, args, kwargs) in list(tasks.items()):
                asyncio.ensure_future(
                    self._run_async_work(task_name, callback, args, kwargs, event_name=event_name)
                )
            # Clear all scheduled tasks
            self.view_instance._async_tasks = {}

        # Legacy format: single task (_async_pending)
        # This maintains backward compatibility with existing code
        pending = getattr(self.view_instance, "_async_pending", None)
        if pending:
            self.view_instance._async_pending = None
            callback, args, kwargs = pending
            event_name = getattr(self, "_current_event_name", None)
            asyncio.ensure_future(
                self._run_async_work("_default", callback, args, kwargs, event_name=event_name)
            )

    async def _run_async_work(
        self, task_name: str, callback, args, kwargs, event_name=None
    ) -> None:
        """
        Execute a start_async callback in a thread, then re-render the view.

        This runs after the initial response has been sent (with loading state).
        When the callback completes, render_with_diff is called and the result
        is sent to the client, completing the loading cycle.

        Updates are tagged with ``source="async"`` so the client can buffer
        them during pending user event round-trips (event sequencing #560).

        If the task was cancelled via cancel_async(), the re-render is skipped.

        Args:
            task_name: Name of the task being executed (for tracking/cancellation).
            callback: The callback function to execute.
            args: Positional arguments for the callback.
            kwargs: Keyword arguments for the callback.
            event_name: The event that triggered this async work. Included in
                the response so the client can clear the correct loading state.
        """
        # Check if task was cancelled before starting
        if hasattr(self.view_instance, "_async_cancelled"):
            if task_name in self.view_instance._async_cancelled:
                self.view_instance._async_cancelled.discard(task_name)
                logger.debug("Async task %s was cancelled, skipping execution", task_name)
                return

        result = None
        error = None

        try:
            # Async callbacks (from @background on async def handlers)
            # are called directly on the event loop. Sync callbacks are
            # dispatched to a thread via sync_to_async. The legacy
            # inspect.iscoroutine fallback handles callbacks created
            # before the @background decorator gained native async
            # detection (v0.4.2+).
            import asyncio as _asyncio
            import inspect

            if _asyncio.iscoroutinefunction(callback):
                result = await callback(*args, **kwargs)
            else:
                result = await sync_to_async(callback)(*args, **kwargs)
                if inspect.iscoroutine(result):
                    result = await result

            # Check if task was cancelled during execution
            if hasattr(self.view_instance, "_async_cancelled"):
                if task_name in self.view_instance._async_cancelled:
                    self.view_instance._async_cancelled.discard(task_name)
                    logger.debug("Async task %s was cancelled, skipping re-render", task_name)
                    return

            # Call handle_async_result if defined (success path)
            if hasattr(self.view_instance, "handle_async_result"):
                await sync_to_async(self.view_instance.handle_async_result)(
                    task_name, result=result, error=None
                )

            # Re-render and send patches (mirrors the server_push path)
            if hasattr(self.view_instance, "_sync_state_to_rust"):
                await sync_to_async(self.view_instance._sync_state_to_rust)()

            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

            if patches is not None:
                patch_list = fast_json_loads(patches) if patches else []
                await self._send_update(
                    patches=patch_list,
                    version=version,
                    event_name=event_name,
                    source="async",
                )
            else:
                # Full HTML fallback
                html_stripped, html_content = await sync_to_async(
                    lambda h: (
                        self.view_instance._strip_comments_and_whitespace(h),
                        self.view_instance._extract_liveview_content(
                            self.view_instance._strip_comments_and_whitespace(h)
                        ),
                    )
                )(html)
                await self._send_update(
                    html=html_content,
                    version=version,
                    event_name=event_name,
                    source="async",
                )

            await self._flush_push_events()
            await self._flush_flash()
            await self._flush_page_metadata()
            await self._flush_pending_layout()

        except Exception as e:
            error = e
            logger.exception(
                "[djust] Error in start_async callback '%s' on %s",
                task_name,
                self.view_instance.__class__.__name__ if self.view_instance else "?",
            )

            # Call handle_async_result if defined (error path)
            if hasattr(self.view_instance, "handle_async_result"):
                try:
                    await sync_to_async(self.view_instance.handle_async_result)(
                        task_name, result=None, error=error
                    )

                    # Re-render to show error state
                    if hasattr(self.view_instance, "_sync_state_to_rust"):
                        await sync_to_async(self.view_instance._sync_state_to_rust)()

                    html, patches, version = await sync_to_async(
                        self.view_instance.render_with_diff
                    )()

                    if patches is not None:
                        patch_list = fast_json_loads(patches) if patches else []
                        await self._send_update(
                            patches=patch_list,
                            version=version,
                            event_name=event_name,
                            source="async",
                        )
                    else:
                        html_stripped, html_content = await sync_to_async(
                            lambda h: (
                                self.view_instance._strip_comments_and_whitespace(h),
                                self.view_instance._extract_liveview_content(
                                    self.view_instance._strip_comments_and_whitespace(h)
                                ),
                            )
                        )(html)
                        await self._send_update(
                            html=html_content,
                            version=version,
                            event_name=event_name,
                            source="async",
                        )

                except Exception:
                    logger.exception(
                        "[djust] Error in handle_async_result for task '%s'", task_name
                    )

    async def _send_update(
        self,
        patches: Optional[list] = None,
        html: Optional[str] = None,
        version: int = 0,
        cache_request_id: Optional[str] = None,
        reset_form: bool = False,
        timing: Optional[Dict[str, Any]] = None,
        performance: Optional[Dict[str, Any]] = None,
        hotreload: bool = False,
        file_path: Optional[str] = None,
        event_name: Optional[str] = None,
        broadcast: bool = False,
        async_pending: bool = False,
        source: Optional[str] = None,
        ref: Optional[int] = None,
    ) -> None:
        """
        Send a patch or full HTML update to the client.

        Handles both JSON and binary (MessagePack) modes, building the response
        with all optional fields.

        Args:
            patches: VDOM patches to apply (if available, can be empty list)
            html: Full HTML content (fallback when patches is None)
            version: VDOM version for client sync
            cache_request_id: Optional ID for client-side caching (@cache decorator)
            reset_form: Whether to reset form state after update
            timing: Basic timing data for backward compatibility
            performance: Comprehensive performance data
            hotreload: Whether this is a hot reload update
            file_path: File path that triggered hot reload (if hotreload=True)
            event_name: Name of the event that triggered this update (for debug payload)
            source: Update source tag for client-side event sequencing (#560).
                Values: "tick" (periodic ticks), "broadcast" (server_push),
                "async" (start_async completions), "event" (user-initiated).
                The client buffers tick/broadcast/async patches during pending
                user event round-trips to prevent version interleaving.
            ref: Event reference number echoed back from the client's request,
                allowing the client to match responses to sent events (#560).
        """
        # #763: On hot-reload, suppress empty-patch broadcasts. When an
        # unrelated Python file changes, re-rendering often produces zero
        # patches (the file didn't affect the current view's output). The
        # old code still broadcast a full ~14 KB payload including the
        # `_debug` state dump to every connected session. Skip it — the
        # client has nothing to do and the payload is pure noise.
        # NON-hot-reload empty patches are still sent: user events that
        # legitimately produce no diff still need an acknowledgment so
        # the client can clear loading state.
        if hotreload and patches == []:
            hotreload_logger.debug(
                "Suppressing empty-patch hot-reload broadcast (unrelated file: %s)",
                file_path,
            )
            return

        # Note: patches=[] (empty list) is valid and should be sent as "patch" type
        # Only patches=None indicates we should send html_update
        if patches is not None:
            if self.use_binary:
                patches_data = msgpack.packb(patches)
                await self.send(bytes_data=patches_data)
            else:
                response: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patches,
                    "version": version,
                }
                # Include HTML if provided (e.g., patch compression fallback)
                if html:
                    response["html"] = html
                # #654: gate timing/performance on DEBUG or DJUST_EXPOSE_TIMING so
                # production clients (including unauthenticated cross-origin
                # observers under CSWSH) don't see server-side code-path timings.
                # The browser debug panel is unaffected — it receives timing via
                # _attach_debug_payload which has its own DEBUG gate.
                if _should_expose_timing():
                    if timing:
                        response["timing"] = timing
                    if performance:
                        response["performance"] = performance
                if reset_form:
                    response["reset_form"] = True
                if cache_request_id:
                    response["cache_request_id"] = cache_request_id
                if hotreload:
                    response["hotreload"] = True
                    if file_path:
                        response["file"] = file_path
                if broadcast:
                    response["broadcast"] = True
                if async_pending:
                    response["async_pending"] = True
                if event_name:
                    response["event_name"] = event_name
                if source:
                    response["source"] = source
                if ref is not None:
                    response["ref"] = ref
                self._attach_debug_payload(response, event_name, performance)
                await self.send_json(response)
                await self._flush_push_events()
                await self._flush_flash()
                await self._flush_page_metadata()
                await self._flush_pending_layout()
                await self._flush_navigation()
                await self._flush_accessibility()
                await self._flush_i18n()
        else:
            response = {
                "type": "html_update",
                "html": html,
                "version": version,
            }
            if reset_form:
                response["reset_form"] = True
            if cache_request_id:
                response["cache_request_id"] = cache_request_id
            if async_pending:
                response["async_pending"] = True
            if event_name:
                response["event_name"] = event_name
            if source:
                response["source"] = source
            if ref is not None:
                response["ref"] = ref
            self._attach_debug_payload(response, event_name)
            await self.send_json(response)
            await self._flush_push_events()
            await self._flush_flash()
            await self._flush_page_metadata()
            await self._flush_pending_layout()
            await self._flush_navigation()
            await self._flush_accessibility()
            await self._flush_i18n()

    async def _dispatch_single_event(
        self,
        target_view: Any,
        event_name: str,
        params: Dict[str, Any],
        event_ref: Optional[int] = None,
    ) -> None:
        """Minimal event dispatch used by the activity-deferral flush path.

        Invariants vs :meth:`handle_event`:

        * The caller MUST already hold ``self._render_lock``. This method
          does NOT acquire or release it — ``asyncio.Lock`` is non-reentrant
          and the flush is driven from inside the main event handler
          which already owns the lock.
        * The activity-gate check is NOT re-run. Events reach this path
          only because the flush already decided they should dispatch;
          re-triggering the gate would re-queue them indefinitely.
        * ``params`` MUST have any ``_activity`` marker stripped by the
          caller.
        * Exceptions from the handler are logged and swallowed so a single
          bad event cannot break the remainder of the flush.

        The method still runs the standard security validation pipeline
        (unsafe name → reject, missing handler → reject, rate limit,
        permission check) so deferred events have the same auth posture
        as live events. After a successful handler call it re-renders
        the view and emits exactly one patch/HTML frame (matching the
        main ``handle_event`` output shape), then flushes push events /
        flash / metadata / layout so downstream side-effects reach the
        client in the same round-trip.
        """
        import time

        # --- security / validation (shared with handle_event) -----------
        handler = await _validate_event_security(self, event_name, target_view, self._rate_limiter)
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
                "Deferred-activity event %r failed param validation: %s",
                sanitize_for_log(event_name or ""),
                validation["error"],
            )
            return
        coerced_params = validation.get("coerced_params", params)

        # --- handler invocation ----------------------------------------
        pre_assigns = _snapshot_assigns(self.view_instance)
        try:
            await _call_handler(handler, coerced_params if coerced_params else None)
        except Exception:  # noqa: BLE001 — never break the flush
            logger.exception(
                "Deferred-activity event %r on %s raised during dispatch",
                sanitize_for_log(event_name or ""),
                type(target_view).__name__,
            )
            return

        # Waiter notification (ADR-002) — same posture as the main path.
        if hasattr(target_view, "_notify_waiters"):
            try:
                target_view._notify_waiters(event_name, coerced_params or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Waiter notification for deferred %r failed: %s", event_name, exc)

        # --- render + emit one update frame ----------------------------
        # Auto-skip when no public assigns changed (same rule as
        # handle_event). This keeps a deferred side-effect-only handler
        # from triggering an unnecessary render frame on the client.
        skip_render = getattr(self.view_instance, "_skip_render", False)
        force_html = getattr(self.view_instance, "_force_full_html", False)
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(self.view_instance)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                self.view_instance._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        if skip_render:
            self.view_instance._skip_render = False
            has_async = getattr(self.view_instance, "_async_pending", None) is not None
            await self._flush_push_events()
            await self._flush_flash()
            await self._flush_page_metadata()
            await self._flush_pending_layout()
            await self._send_noop(async_pending=has_async, ref=event_ref)
            if has_async:
                await self._dispatch_async_work()
            return

        # Render + diff (mirrors the simpler arm of handle_event).
        _gcd = self.view_instance.get_context_data
        _skip_thread = inspect.iscoroutinefunction(_gcd) or getattr(
            self.view_instance, "sync_safe", False
        )
        t0 = time.perf_counter()
        try:
            if _skip_thread:
                if inspect.iscoroutinefunction(_gcd):
                    await _gcd()
                else:
                    _gcd()
                with profiler.profile(profiler.OP_RENDER):
                    html, patches, version = self.view_instance.render_with_diff()
            else:

                def _sync_context_and_render():
                    _gcd()
                    with profiler.profile(profiler.OP_RENDER):
                        return self.view_instance.render_with_diff()

                html, patches, version = await sync_to_async(_sync_context_and_render)()
        except Exception:  # noqa: BLE001
            logger.exception("Deferred-activity render failed for %s", event_name)
            return
        _render_ms = (time.perf_counter() - t0) * 1000

        patch_list = None
        if patches is not None:
            patch_list = fast_json_loads(patches) if patches else []

        has_async = getattr(self.view_instance, "_async_pending", None) is not None
        if patch_list is not None:
            await self._send_update(
                patches=patch_list,
                version=version,
                event_name=event_name,
                async_pending=has_async,
                source="event",
                ref=event_ref,
                timing={"render": _render_ms},
            )
        else:
            # VDOM diff returned no patches — send full HTML like the
            # main path does. Mirrors the fallback branch so clients
            # behave identically for deferred vs live events.
            try:

                def _sync_strip_and_extract(raw_html):
                    stripped = self.view_instance._strip_comments_and_whitespace(raw_html)
                    content = self.view_instance._extract_liveview_content(stripped)
                    return stripped, content

                html, html_content = await sync_to_async(_sync_strip_and_extract)(html)
            except Exception:  # noqa: BLE001
                logger.exception("Deferred-activity HTML strip/extract failed for %s", event_name)
                return
            await self._send_update(
                html=html_content,
                version=version,
                event_name=event_name,
                async_pending=has_async,
                source="event",
                ref=event_ref,
            )
        if has_async:
            await self._dispatch_async_work()

    def _attach_debug_payload(
        self,
        response: Dict[str, Any],
        event_name: Optional[str] = None,
        performance: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Attach slim _debug payload to a WebSocket response when DEBUG is enabled.

        Only sends variables (which change per event), performance, and patches.
        Handler metadata is static and only sent on initial mount via
        get_debug_info() / window.DJUST_DEBUG_INFO.
        """
        from django.conf import settings

        if not getattr(settings, "DEBUG", False):
            return

        # The debug payload (dir() + getattr + json.dumps per attribute) adds
        # ~2-5ms per event. Skip it when the debug panel is explicitly closed.
        # Default is True (backward compat) — panel sends debug_panel_close
        # to opt out of the overhead.
        if not getattr(self, "_debug_panel_active", True):
            return
        if not self.view_instance:
            return

        try:
            debug_info = self.view_instance.get_debug_update()
            if event_name:
                debug_info["_eventName"] = event_name
            if performance:
                debug_info["performance"] = performance
            if "patches" in response:
                debug_info["patches"] = response["patches"]
            response["_debug"] = debug_info
        except Exception as e:
            logger.debug("Failed to attach debug payload: %s", e)

    def _get_client_ip(self) -> Optional[str]:
        """Extract client IP from scope, with X-Forwarded-For support."""
        headers = dict(self.scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for")
        if forwarded:
            return forwarded.decode("utf-8").split(",")[0].strip()
        client = self.scope.get("client")
        if client:
            return client[0]
        return None

    async def connect(self):
        """Handle WebSocket connection"""
        # CSWSH defense (#653): reject the handshake if the Origin header is
        # not in settings.ALLOWED_HOSTS *before* calling self.accept(). This
        # is defense in depth on top of DjustMiddlewareStack's
        # AllowedHostsOriginValidator wrap — the consumer-level check still
        # protects apps that route directly to LiveViewConsumer.
        headers = dict(self.scope.get("headers", []))
        origin = headers.get(b"origin")
        if not _is_allowed_origin(origin):
            # decode(errors="replace") never raises on bytes, so no try/except
            # is needed — the "if origin else ''" guard covers the None case.
            origin_repr = origin.decode("ascii", errors="replace") if origin else ""
            logger.warning(
                "WebSocket connection rejected: disallowed Origin %s",
                sanitize_for_log(origin_repr),
            )
            await self.close(code=4403)
            return

        await self.accept()

        # Generate session ID
        import uuid

        self.session_id = str(uuid.uuid4())

        # Per-IP connection limit and cooldown check
        self._client_ip = self._get_client_ip()
        rl_cfg = djust_config.get("rate_limit", {})
        if not isinstance(rl_cfg, dict):
            rl_cfg = {}
        if self._client_ip:
            max_per_ip = rl_cfg.get("max_connections_per_ip", 10)
            if not ip_tracker.connect(self._client_ip, max_per_ip):
                logger.warning("Connection rejected for IP %s (limit or cooldown)", self._client_ip)
                await self.close(code=4429)
                return

        # Add to hot reload broadcast group
        await self.channel_layer.group_add("djust_hotreload", self.channel_name)

        # Initialize per-connection rate limiter
        self._rate_limiter = ConnectionRateLimiter(
            rate=rl_cfg.get("rate", 100),
            burst=rl_cfg.get("burst", 20),
            max_warnings=rl_cfg.get("max_warnings", 3),
        )

        # Send connection acknowledgment
        await self.send_json(
            {
                "type": "connect",
                "session_id": self.session_id,
            }
        )

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        # Release observability registry entry first — it's weakly-held
        # anyway but explicit cleanup avoids a brief stale entry window.
        session_id = getattr(self, "session_id", None)
        if session_id:
            try:
                from djust.observability.registry import unregister_view

                unregister_view(session_id)
            except Exception:  # noqa: BLE001
                pass  # Observability never blocks shutdown.

        # Release IP connection slot
        client_ip = getattr(self, "_client_ip", None)
        if client_ip:
            ip_tracker.disconnect(client_ip)

        # Remove from hot reload broadcast group
        await self.channel_layer.group_discard("djust_hotreload", self.channel_name)

        # Leave per-view channel group
        if self._view_group:
            await self.channel_layer.group_discard(self._view_group, self.channel_name)

        # Leave presence group and clean up presence
        if self._presence_group:
            await self.channel_layer.group_discard(self._presence_group, self.channel_name)

        # Leave db_notify groups registered by NotificationMixin.listen()
        db_notify_channels = getattr(self, "_db_notify_channels", None)
        if db_notify_channels:
            for ch in list(db_notify_channels):
                try:
                    await self.channel_layer.group_discard(
                        f"djust_db_notify_{ch}", self.channel_name
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("Error leaving db_notify group for %s: %s", ch, e)

        # Clean up presence tracking if view supports it
        if self.view_instance and hasattr(self.view_instance, "untrack_presence"):
            try:
                await sync_to_async(self.view_instance.untrack_presence)()
            except Exception as e:
                logger.warning("Error cleaning up presence: %s", e)

        # Cancel tick task and wait for it to finish
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass  # Expected when cancelling a running tick task during disconnect
            self._tick_task = None

        # Clean up actor if using actors
        if self.use_actors and self.actor_handle:
            try:
                await self.actor_handle.shutdown()
            except Exception as e:
                logger.warning("Error shutting down actor: %s", e)

        # Clean up uploads
        if self.view_instance and hasattr(self.view_instance, "_cleanup_uploads"):
            try:
                self.view_instance._cleanup_uploads()
            except Exception as e:
                logger.warning("Error cleaning up uploads: %s", e)

        # Cancel any pending wait_for_event waiters (ADR-002 Phase 1b).
        # @background tasks awaiting on a waiter unblock with CancelledError
        # and can clean up themselves — without this they'd leak the Future.
        if self.view_instance and hasattr(self.view_instance, "_cancel_all_waiters"):
            try:
                self.view_instance._cancel_all_waiters(reason="view_disconnect")
            except Exception as e:
                logger.warning("Error cancelling waiters: %s", e)

        # Clean up embedded child views
        if self.view_instance and hasattr(self.view_instance, "_child_views"):
            try:
                for child_id in list(self.view_instance._child_views.keys()):
                    self.view_instance._unregister_child(child_id)
            except Exception as e:
                logger.warning("Error cleaning up embedded children: %s", e)

        # Sticky LiveViews (Phase C Fix F2): drain any sticky children that
        # were staged on the consumer during a live_redirect but for which
        # ``handle_mount`` has not yet completed. Without this, a WS
        # disconnect mid-redirect (rare but reachable) leaves the preserved
        # sticky instances alive with their background tasks still running
        # on a "zombie" consumer whose view is gone.
        if self._sticky_preserved:
            for sticky_id, child in list(self._sticky_preserved.items()):
                hook = getattr(child, "_on_sticky_unmount", None)
                if callable(hook):
                    try:
                        hook()
                    except Exception:  # noqa: BLE001 — cleanup must not raise
                        logger.exception(
                            "sticky %s _on_sticky_unmount during disconnect failed",
                            sanitize_for_log(sticky_id),
                        )
            self._sticky_preserved = {}

        # Clean up session state
        self.view_instance = None
        self.actor_handle = None

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket messages"""
        logger.debug(
            "[WebSocket] receive called: text_data=%s, bytes_data=%s",
            text_data[:100] if text_data else None,
            bytes_data is not None,
        )

        try:
            # Check message size
            max_msg_size = djust_config.get("max_message_size", 65536)
            if bytes_data:
                raw_size = len(bytes_data)
            elif text_data:
                char_len = len(text_data)
                # Only skip encode when even worst-case (4 bytes/char) is under limit
                raw_size = (
                    char_len if char_len * 4 <= max_msg_size else len(text_data.encode("utf-8"))
                )
            else:
                raw_size = 0
            if max_msg_size and raw_size > max_msg_size:
                logger.warning("Message too large (%d bytes, max %d)", raw_size, max_msg_size)
                await self.send_error(f"Message too large ({raw_size} bytes)")
                return

            # Check for binary upload frames
            if bytes_data and len(bytes_data) >= 17:
                # Check if this looks like an upload frame (first byte is 0x01-0x03)
                frame_type = bytes_data[0]
                if frame_type in (0x01, 0x02, 0x03):
                    await self._handle_upload_frame(bytes_data)
                    return

            # Decode message
            if bytes_data:
                data = msgpack.unpackb(bytes_data, raw=False)
            else:
                data = json.loads(text_data)

            msg_type = data.get("type")

            # Global rate limit check — applies to ALL message types (#107)
            if not self._rate_limiter.check(msg_type or "unknown"):
                if self._rate_limiter.should_disconnect():
                    logger.warning("Rate limit exceeded, disconnecting client")
                    if getattr(self, "_client_ip", None):
                        _rl = djust_config.get("rate_limit", {})
                        cooldown = _rl.get("reconnect_cooldown", 5) if isinstance(_rl, dict) else 5
                        ip_tracker.add_cooldown(self._client_ip, cooldown)
                    await self.close(code=4429)
                    return
                await self.send_json(
                    {
                        "type": "rate_limit_exceeded",
                        "message": "Too many messages, some events are being dropped",
                    }
                )
                return

            if msg_type == "event":
                await self.handle_event(data)
            elif msg_type == "mount":
                await self.handle_mount(data)
            elif msg_type == "mount_batch":
                await self.handle_mount_batch(data)
            elif msg_type == "ping":
                await self.send_json({"type": "pong"})
            elif msg_type == "url_change":
                await self.handle_url_change(data)
            elif msg_type == "live_redirect_mount":
                await self.handle_live_redirect_mount(data)
            elif msg_type == "upload_register":
                await self._handle_upload_register(data)
            elif msg_type == "upload_resume":
                await self._handle_upload_resume(data)
            elif msg_type == "presence_heartbeat":
                await self.handle_presence_heartbeat(data)
            elif msg_type == "cursor_move":
                await self.handle_cursor_move(data)
            elif msg_type == "request_html":
                await self.handle_request_html(data)
            elif msg_type == "debug_panel_open":
                self._debug_panel_active = True
            elif msg_type == "debug_panel_close":
                self._debug_panel_active = False
            elif msg_type == "time_travel_jump":
                await self.handle_time_travel_jump(data)
            else:
                logger.warning("Unknown message type: %s", msg_type)
                await self.send_error(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in WebSocket message: {str(e)}"
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "Invalid message format"))
        except Exception as e:
            # Handle exception: logs (with stack trace only in DEBUG) and returns safe response
            response = handle_exception(
                e,
                error_type="default",
                logger=logger,
                log_message="Error in WebSocket receive",
            )
            await self.send_json(response)

    async def handle_mount(
        self,
        data: Dict[str, Any],
        sticky_preserved: Optional[Dict[str, Any]] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
    ):
        """
        Handle view mounting with proper view resolution.

        Dynamically imports and instantiates a LiveView class, creates a request
        context, mounts the view, and returns the initial HTML.

        Sticky LiveViews (Phase B): when ``sticky_preserved`` is provided
        (passed by ``handle_live_redirect_mount``), a ``sticky_hold``
        frame is emitted immediately BEFORE the ``mount`` frame so the
        client can reconcile its stickyStash against the authoritative
        survivor list BEFORE ``reattachStickyAfterMount`` runs inside
        the mount-frame handler. The survivor set is computed by
        slot-scanning the just-rendered HTML; survivors whose
        ``sticky_id`` has no matching ``[dj-sticky-slot]`` get
        ``_on_sticky_unmount()`` called and are dropped from the dict
        (which the caller stores on ``self._sticky_preserved``).

        State snapshot (v0.6.0): when ``state_snapshot`` is provided
        AND the view class opts in via ``enable_state_snapshot = True``
        AND ``view_slug`` matches the view path AND
        ``_should_restore_snapshot(request)`` returns True, the view's
        public state is restored from the snapshot in lieu of calling
        ``mount()``. Authentication and ``on_mount`` hooks still run
        before restoration; malformed JSON or restore errors fall back
        to a fresh ``mount()`` call.
        """
        from django.test import RequestFactory
        from django.conf import settings

        logger = logging.getLogger(__name__)

        view_path = data.get("view")
        params = data.get("params", {})
        has_prerendered = data.get("has_prerendered", False)
        client_timezone = data.get("client_timezone")

        if not view_path:
            await self.send_error("Missing view path in mount request")
            return

        # Security: Check if view is in allowed modules
        allowed_modules = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", [])
        if allowed_modules:
            # Check if view_path starts with any allowed module
            if not any(view_path.startswith(module) for module in allowed_modules):
                logger.warning(
                    "Blocked attempt to mount view from unauthorized module: %s", view_path
                )
                await self.send_error(
                    _safe_error(f"View {view_path} is not in allowed modules", "View not found")
                )
                return

        # Import the view class
        module = None
        module_path = ""
        class_name = ""
        try:
            module_path, class_name = view_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            view_class = getattr(module, class_name)
        except ValueError:
            error_msg = (
                f"Invalid view path format: {view_path}. Expected format: module.path.ClassName"
            )
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "View not found"))
            return
        except ImportError as e:
            error_msg = f"Failed to import module {module_path}: {str(e)}"
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "View not found"))
            return
        except AttributeError:
            error_msg = f"Class {class_name} not found in module {module_path}"
            logger.error(error_msg)
            hint = None
            if getattr(settings, "DEBUG", False):
                try:
                    import inspect as _inspect

                    from .live_view import LiveView as _LV

                    available = [
                        name
                        for name, obj in _inspect.getmembers(module, _inspect.isclass)
                        if issubclass(obj, _LV) and obj is not _LV
                    ]
                    if available:
                        hint = "Available LiveView classes in %s: %s" % (
                            module_path,
                            ", ".join(sorted(available)),
                        )
                except Exception as exc:
                    logger.debug("Could not enumerate LiveView classes: %s", exc)
            await self.send_error(_safe_error(error_msg, "View not found"), hint=hint)
            return

        # Security: Validate that the class is actually a LiveView
        from .live_view import LiveView

        if not (isinstance(view_class, type) and issubclass(view_class, LiveView)):
            error_msg = (
                f"Security: {view_path} is not a LiveView subclass. "
                f"Only LiveView classes can be mounted via WebSocket."
            )
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "Invalid view class"))
            return

        # Instantiate the view
        try:
            self.view_instance = view_class()

            # Store reference to WS consumer for streaming support
            self.view_instance._ws_consumer = self

            # Wire push-event flush callback so @background handlers
            # (like TutorialMixin.start_tutorial) can send push_commands
            # to the client mid-execution without waiting for the handler
            # to return. See PushEventMixin._flush_pending_push_events.
            if hasattr(self.view_instance, "_push_events_flush_callback"):
                self.view_instance._push_events_flush_callback = self._flush_push_events

            # Store client timezone for local time rendering
            self.view_instance.client_timezone = None
            if client_timezone:
                try:
                    from zoneinfo import ZoneInfo

                    ZoneInfo(client_timezone)  # Validate IANA timezone string
                    self.view_instance.client_timezone = client_timezone
                except (KeyError, Exception):
                    logger.warning("Invalid client timezone: %s", client_timezone)

            # Store WebSocket session_id in view for consistent VDOM caching
            # This ensures mount and all subsequent events use the same VDOM instance
            self.view_instance._websocket_session_id = self.session_id
            # Store path and query string for path-aware cache keys
            # This ensures /emails/ and /emails/?sender=1 get separate VDOM caches
            self.view_instance._websocket_path = self.scope.get("path", "/")
            self.view_instance._websocket_query_string = self.scope.get("query_string", b"").decode(
                "utf-8"
            )

            # Register this view in the observability session registry so the
            # djust MCP can introspect live state via its HTTP endpoints.
            # Registry uses weakrefs — no leak if unregister is missed.
            try:
                from djust.observability.registry import register_view

                register_view(self.session_id, self.view_instance)
            except Exception as e:  # noqa: BLE001
                # Observability must never break a WS connection.
                logger.warning("Failed to register view in observability registry: %s", e)

            # Join per-view channel group for server-push
            from .push import view_group_name

            self._view_path = view_path
            self._view_group = view_group_name(view_path)
            await self.channel_layer.group_add(self._view_group, self.channel_name)

            # Join presence group if view supports presence tracking
            self._presence_group = None
            if hasattr(self.view_instance, "get_presence_key"):
                try:
                    presence_key = self.view_instance.get_presence_key()
                    self._presence_group = PresenceManager.presence_group_name(presence_key)
                    await self.channel_layer.group_add(self._presence_group, self.channel_name)
                except Exception as e:
                    logger.warning("Error setting up presence group: %s", e)

            # Join db_notify groups for every channel the view subscribed to
            # via NotificationMixin.listen() during mount(). The groups are
            # addressed per-channel (djust_db_notify_<channel>) so a NOTIFY
            # on one channel never fans out to views listening on another.
            self._db_notify_channels = set()
            listen_channels = getattr(self.view_instance, "_listen_channels", None)
            if listen_channels:
                for ch in listen_channels:
                    try:
                        await self.channel_layer.group_add(
                            f"djust_db_notify_{ch}", self.channel_name
                        )
                        self._db_notify_channels.add(ch)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Error joining db_notify group for %s: %s", ch, e)

            # Start periodic tick if subclass overrides handle_tick
            tick_interval = getattr(view_class, "tick_interval", None)
            if tick_interval:
                from .live_view import LiveView as _LV

                if view_class.handle_tick is not _LV.handle_tick:
                    self._tick_task = asyncio.create_task(self._run_tick(tick_interval))

            # Check if view uses actor-based state management
            self.use_actors = getattr(view_class, "use_actors", False)

            if self.use_actors and create_session_actor:
                # Create SessionActor for this session
                logger.info("Creating SessionActor for %s", view_path)
                self.actor_handle = await create_session_actor(self.session_id)
                logger.info("SessionActor created: %s", self.actor_handle.session_id)

        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Failed to instantiate {view_path}",
            )
            await self.send_json(response)
            return

        # Create request with session
        try:
            from urllib.parse import urlencode

            factory = RequestFactory()
            # Include URL query params (e.g., ?sender=80) in the request
            query_string = urlencode(params) if params else ""
            # Use the actual page URL from the client, not hardcoded "/"
            page_url = data.get("url", "/")
            path_with_query = f"{page_url}?{query_string}" if query_string else page_url
            request = factory.get(path_with_query)

            # Add session from WebSocket scope.
            # session_key is an ATTRIBUTE of the session object, not a dict key.
            # Django Channels' AuthMiddlewareStack wraps scope["session"] in a
            # LazyObject.  hasattr() catches AttributeError if the attribute is
            # absent, but will not suppress other exceptions (e.g. OperationalError
            # from a db-backed session backend).  Use getattr(obj, name, None) for
            # a more robust single-call alternative on LazyObjects.
            # Lazy import: avoids pulling in the db backend when the project
            # uses a different session backend (e.g. cached_db, file, redis).
            from django.contrib.sessions.backends.db import SessionStore  # noqa: PLC0415

            scope_session = self.scope.get("session")
            # getattr(obj, name, default) is used instead of a hasattr() +
            # getattr() pair.  Django Channels' AuthMiddlewareStack wraps
            # scope["session"] in a LazyObject; a two-step hasattr/getattr
            # approach resolves the object twice and can disagree if the
            # LazyObject is in a partially-resolved state.  The single
            # getattr() call resolves the LazyObject once and returns the
            # value (or None) atomically.
            session_key = getattr(scope_session, "session_key", None) if scope_session else None
            if session_key:
                request.session = SessionStore(session_key=session_key)
                self.view_instance._django_session_key = session_key
            else:
                request.session = SessionStore()

            # Add user if available.
            # scope["user"] is a Django Channels LazyObject (set by AuthMiddlewareStack).
            # Assigning it directly to request.user is safe — Django's auth machinery
            # and template context both handle lazy user proxies correctly.
            if "user" in self.scope:
                request.user = self.scope["user"]

        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                logger=logger,
                log_message="Failed to create request context",
            )
            await self.send_json(response)
            return

        # Mount the view (needs sync_to_async for database operations)

        try:
            # Initialize temporary assigns before mount
            await sync_to_async(self.view_instance._initialize_temporary_assigns)()

            # Store request on the view so self.request works in handlers
            # (Django's View.dispatch() does this for HTTP, but WS skips dispatch)
            self.view_instance.request = request

            # --- Auth check (before mount) ---
            from .auth import check_view_auth
            from django.core.exceptions import PermissionDenied

            try:
                redirect_url = await sync_to_async(check_view_auth)(self.view_instance, request)
            except PermissionDenied as exc:
                # Authenticated user lacks permissions → 403 (not a redirect)
                logger.info(
                    "Permission denied for %s: %s", self.view_instance.__class__.__name__, exc
                )
                await self.send_json({"type": "error", "message": "Permission denied"})
                await self.close(code=4403)
                return
            if redirect_url:
                await self.send_json(
                    {
                        "type": "navigate",
                        "to": redirect_url,
                    }
                )
                return
            # --- End auth check ---

            # Call lifecycle hooks that must run on every WS connect regardless
            # of whether state is restored from session or mount() is called.
            #
            # _ensure_tenant() — djust-tenants TenantMixin resolves the tenant.
            #   Without this, self.tenant is always None in the live path even
            #   though the SSR pre-render (HTTP) path works correctly.
            #   See: https://github.com/djust-org/djust/issues/342
            if hasattr(self.view_instance, "_ensure_tenant"):
                await sync_to_async(self.view_instance._ensure_tenant)(request)

            # --- on_mount hooks (after auth, before mount) ---
            from .hooks import run_on_mount_hooks

            hook_redirect = await sync_to_async(run_on_mount_hooks)(
                self.view_instance, request, **params
            )
            if hook_redirect:
                await self.send_json({"type": "navigate", "to": hook_redirect})
                return
            # --- End on_mount hooks ---

            # --- State restoration (skip mount when pre-rendered state exists) ---
            mounted = False
            if has_prerendered:
                view_key = f"liveview_{page_url}"
                saved_state = await request.session.aget(view_key, {})
                if saved_state:
                    from .security import safe_setattr

                    for key, value in saved_state.items():
                        safe_setattr(self.view_instance, key, value, allow_private=False)

                    # Restore user-defined _private attributes (mirrors HTTP POST path)
                    private_state = await request.session.aget(f"{view_key}__private", {})
                    if private_state:
                        await sync_to_async(self.view_instance._restore_private_state)(
                            private_state
                        )

                    # Issues #889, #893, #894 — replay process-wide side
                    # effects that mount() would have re-issued.
                    # Pattern: session round-trip preserves instance
                    # attrs but loses registrations with per-process
                    # singletons (UploadManager, PresenceManager,
                    # PostgresNotifyListener). Each affected mixin
                    # exposes a _restore_* method that reconstructs
                    # its side effects from the restored attrs.
                    if hasattr(self.view_instance, "_restore_upload_configs"):
                        await sync_to_async(self.view_instance._restore_upload_configs)()
                    if hasattr(self.view_instance, "_restore_presence"):
                        await sync_to_async(self.view_instance._restore_presence)()
                    if hasattr(self.view_instance, "_restore_listen_channels"):
                        await sync_to_async(self.view_instance._restore_listen_channels)()

                    await sync_to_async(self.view_instance._initialize_temporary_assigns)()
                    await sync_to_async(self.view_instance._assign_component_ids)()

                    # Restore component state
                    from .components.base import Component, LiveComponent

                    component_state = await request.session.aget(f"{view_key}_components", {})
                    for key, state in component_state.items():
                        component = getattr(self.view_instance, key, None)
                        if component and isinstance(component, (Component, LiveComponent)):
                            await sync_to_async(self.view_instance._restore_component_state)(
                                component, state
                            )

                    mounted = True

            if not mounted:
                # Resolve URL kwargs from the page path (e.g., slug, pk)
                # Django's URL resolver extracts these during HTTP dispatch, but
                # the WebSocket consumer doesn't go through URL routing, so we
                # resolve them here and merge into mount() kwargs.
                mount_kwargs = dict(params)
                try:
                    from django.urls import resolve

                    match = resolve(page_url)
                    if match.kwargs:
                        mount_kwargs.update(match.kwargs)
                except Exception:
                    pass  # URL may not resolve (e.g., root "/") — that's fine

                # State snapshot restore path (v0.6.0) — when the client
                # sends a state_snapshot with live_redirect_mount AND the
                # view opts in, restore the public state dict in lieu of
                # calling mount(). Auth + on_mount hooks already ran above.
                mounted_from_snapshot = False
                # Fix #11 — operator-level master switch. Allows ops to
                # disable snapshot restoration globally (e.g. during an
                # incident) without touching each view class.
                state_master_on = getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
                if (
                    state_master_on
                    and state_snapshot
                    and getattr(self.view_instance, "enable_state_snapshot", False)
                ):
                    snapshot_slug = state_snapshot.get("view_slug", "")
                    if snapshot_slug == view_path:
                        state_dict = None
                        raw_state = state_snapshot.get("state_json", "{}")
                        # Fix #6 — hard server-side size cap on inbound
                        # snapshot JSON. Matches the 64 KB client clamp
                        # and guards against oversized payloads that
                        # bypass the client.
                        if isinstance(raw_state, str) and len(raw_state) > 65536:
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
                            # JSON allows arrays, numbers, strings — any
                            # of which would trip over ``state.items()``
                            # in ``_restore_snapshot``.
                            if state_dict is not None and not isinstance(state_dict, dict):
                                logger.warning(
                                    "state_snapshot state_json is not a dict "
                                    "(got %s) for %s; ignoring",
                                    type(state_dict).__name__,
                                    sanitize_for_log(view_path),
                                )
                                state_dict = None
                            # Fix #7 — keyset DoS cap. Real views have
                            # <20 public attrs; 256 is an absurd upper
                            # bound that still fits legitimate bulk
                            # views while blocking adversarial payloads
                            # that exhaust CPU via safe_setattr calls.
                            if state_dict is not None and len(state_dict) > 256:
                                logger.warning(
                                    "state_snapshot keyset too large "
                                    "(%d keys > 256) for %s; ignoring",
                                    len(state_dict),
                                    sanitize_for_log(view_path),
                                )
                                state_dict = None
                        if state_dict is not None and await sync_to_async(
                            self.view_instance._should_restore_snapshot
                        )(request):
                            try:
                                await sync_to_async(self.view_instance._restore_snapshot)(
                                    state_dict
                                )
                                mounted_from_snapshot = True
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "state_snapshot _restore_snapshot failed "
                                    "for %s; falling back to mount()",
                                    sanitize_for_log(view_path),
                                )
                                mounted_from_snapshot = False

                if not mounted_from_snapshot:
                    # Run synchronous view operations in a thread pool
                    await sync_to_async(self.view_instance.mount)(request, **mount_kwargs)
                # Stash request + kwargs so observability/reset_view_state/
                # can replay mount() without page reload. Minor memory cost
                # (one request ref per live view) in exchange for a cheap
                # reset primitive.
                self.view_instance._djust_mount_request = request
                self.view_instance._djust_mount_kwargs = mount_kwargs
                self.view_instance._snapshot_user_private_attrs()
                # Dirty tracking baseline (v0.5.1) — captures the post-mount
                # state so ``is_dirty`` / ``changed_fields`` reflect changes
                # made by subsequent event handlers.
                if hasattr(self.view_instance, "_capture_dirty_baseline"):
                    self.view_instance._capture_dirty_baseline()
        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.mount()",
            )
            await self.send_json(response)
            return

        # Call handle_params() after mount (Phoenix parity: handle_params is
        # invoked on initial render AND on subsequent URL changes).  Build the
        # URI from the page URL and query params the client sent.
        try:
            uri = path_with_query  # already built above as page_url + query_string
            await sync_to_async(self.view_instance.handle_params)(params, uri)
        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.handle_params()",
            )
            await self.send_json(response)
            return

        # Get initial HTML (skip if client already has pre-rendered content)
        html = None
        version = 1

        try:
            if has_prerendered:
                # Client has pre-rendered HTML but we still need to send hydrated HTML
                # when using ID-based patching (data-dj attributes) for reliable VDOM sync
                logger.info(
                    "Client has pre-rendered content - sending hydrated HTML for ID-based patching"
                )

                if self.use_actors and self.actor_handle:
                    # Initialize actor with empty render (just establish state)
                    context_data = await sync_to_async(self.view_instance.get_context_data)()
                    result = await self.actor_handle.mount(
                        view_path,
                        context_data,
                        self.view_instance,
                    )
                    html = result.get("html")
                    version = result.get("version", 1)
                else:
                    # Initialize Rust view and sync state for future patches
                    await sync_to_async(self.view_instance._initialize_rust_view)(request)
                    await sync_to_async(self.view_instance._sync_state_to_rust)()

                    # Generate hydrated HTML with data-dj attributes for reliable patch targeting
                    html, _, version = await sync_to_async(self.view_instance.render_with_diff)()

                    # Strip comments and normalize whitespace
                    html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(
                        html
                    )

                    # Extract innerHTML of [dj-root]
                    html = await sync_to_async(self.view_instance._extract_liveview_content)(html)

            elif self.use_actors and self.actor_handle:
                # Phase 5: Use actor system for rendering
                logger.info("Mounting %s with actor system", view_path)

                # Get initial state from Python view
                context_data = await sync_to_async(self.view_instance.get_context_data)()

                # Mount with actor system (passes Python view instance)
                result = await self.actor_handle.mount(
                    view_path,
                    context_data,
                    self.view_instance,  # Pass Python view for event handlers!
                )

                html = result["html"]
                logger.info("Actor mount successful, HTML length: %d", len(html))

            else:
                # Non-actor mode: Use traditional flow

                # Initialize Rust view and sync state
                await sync_to_async(self.view_instance._initialize_rust_view)(request)
                await sync_to_async(self.view_instance._sync_state_to_rust)()

                # IMPORTANT: Use render_with_diff() to establish initial VDOM baseline
                # This ensures the first event will be able to generate patches instead of falling back to html_update
                html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

                # Strip comments and normalize whitespace to match Rust VDOM parser
                html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(html)

                # Extract innerHTML of [dj-root] for WebSocket client
                # Client expects just the content to insert into existing container
                html = await sync_to_async(self.view_instance._extract_liveview_content)(html)

        except Exception as e:
            response = handle_exception(
                e,
                error_type="render",
                view_class=view_path,
                logger=logger,
                log_message=f"Error rendering {sanitize_for_log(view_path)}",
            )
            await self.send_json(response)
            return

        # Send success response (HTML only if generated)
        logger.info("Successfully mounted view: %s", view_path)
        response = {
            "type": "mount",
            "session_id": self.session_id,
            "view": view_path,
            "version": version,  # Include VDOM version for client sync
        }

        # Fix #1 — end-to-end wiring for state-snapshot capture.
        # When the view opts in via ``enable_state_snapshot = True`` AND
        # the master switch is enabled, emit the JSON-serializable
        # public state alongside the mount frame so the client can
        # populate ``djust._clientState[<view_slug>]`` for the next
        # before-navigate capture. Non-opt-in views never have their
        # state shipped — matches the security posture of the
        # opt-in-only model.
        try:
            state_master_on = getattr(settings, "DJUST_STATE_SNAPSHOT_ENABLED", True)
            if state_master_on and getattr(self.view_instance, "enable_state_snapshot", False):
                snapshot_fn = getattr(self.view_instance, "_capture_snapshot_state", None)
                if callable(snapshot_fn):
                    public_state = await sync_to_async(snapshot_fn)()
                    if isinstance(public_state, dict) and public_state:
                        response["public_state"] = public_state
        except Exception:  # noqa: BLE001 — snapshot emission must never break mount
            logger.exception(
                "Failed to emit public_state for %s; proceeding without snapshot",
                sanitize_for_log(view_path),
            )

        # Only include HTML if it was generated (not skipped due to pre-rendering)
        if html is not None:
            response["html"] = html
            # Flag indicating HTML has dj-id attributes for ID-based patching.
            # Must match the attribute name emitted by the Rust renderer ("dj-id",
            # not "data-dj-id"). Mirrors the equivalent check in sse.py.
            has_ids = "dj-id=" in html
            response["has_ids"] = has_ids

        # Include cache configuration for handlers with @cache decorator
        cache_config = self._extract_cache_config()
        if cache_config:
            response["cache_config"] = cache_config

        # Include optimistic rules from descriptor components (DEP-002)
        optimistic_rules = self._extract_optimistic_rules()
        if optimistic_rules:
            response["optimistic_rules"] = optimistic_rules

        # Include upload configurations if view uses UploadMixin
        if hasattr(self.view_instance, "_upload_manager") and self.view_instance._upload_manager:
            upload_state = self.view_instance._upload_manager.get_upload_state()
            if upload_state:
                response["upload_configs"] = {
                    name: info["config"] for name, info in upload_state.items()
                }

        # Sticky LiveViews (Phase B): if the caller passed a
        # ``sticky_preserved`` dict (i.e. we're on the live_redirect
        # path), compute the authoritative survivor set by scanning the
        # just-rendered HTML for ``[dj-sticky-slot="<id>"]`` and emit
        # the ``sticky_hold`` frame BEFORE the ``mount`` frame. Ordering
        # matters: the client's mount handler calls
        # reattachStickyAfterMount() which walks stickyStash and
        # replaces matching slot elements — if sticky_hold arrived
        # AFTER the mount frame, auth-revoked stickys would already be
        # reattached and reconcileStickyHold would no-op on them.
        if sticky_preserved:
            try:
                matched_ids = _find_sticky_slot_ids(html or "")
                survivors_final: Dict[str, Any] = {}
                for sticky_id, child in sticky_preserved.items():
                    if sticky_id in matched_ids:
                        # Re-register onto the new parent. _register_child
                        # updates child._parent_view and child._view_id.
                        if hasattr(self.view_instance, "_register_child"):
                            try:
                                self.view_instance._register_child(sticky_id, child)
                                survivors_final[sticky_id] = child
                            except ValueError:
                                # sticky_id collided with a freshly-embedded
                                # (non-preserved) child in the new template —
                                # discard the preserved one.
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
                        hook = getattr(child, "_on_sticky_unmount", None)
                        if callable(hook):
                            try:
                                hook()
                            except Exception:  # noqa: BLE001
                                logger.exception("sticky child _on_sticky_unmount raised")
                # Update caller-visible dict so handle_live_redirect_mount
                # sees the final survivors (it stashes this on
                # self._sticky_preserved for app-level introspection).
                self._sticky_preserved = survivors_final

                # Emit sticky_hold BEFORE the mount frame. Even an empty
                # list is meaningful — tells the client "drop everything
                # in your stash". Only skip when the caller passed an
                # empty dict (pure defensive, no stickys staged).
                await self.send_json(
                    {
                        "type": "sticky_hold",
                        "views": list(survivors_final.keys()),
                    }
                )
            except Exception:  # noqa: BLE001 — defensive: never break mount
                logger.exception("failed to emit sticky_hold frame before mount")

        await self.send_json(response)

    async def _mount_one(self, data_view: Dict[str, Any]):
        """Mount + render a single view and return a payload WITHOUT sending.

        Collector seam for :meth:`handle_mount_batch`. Delegates to
        ``handle_mount`` but replaces ``send_json`` on this consumer with
        a capture list so no frames actually flow to the client. The
        caller aggregates captured frames into a single ``mount_batch``
        frame.

        Returns a ``(success: bool, payload: dict, error: Optional[str],
        navigate_frame: Optional[dict])`` tuple:

        - ``success=True`` and ``payload`` is the captured ``mount`` frame
          merged with the caller-supplied ``target_id``.
        - ``success=False`` and ``error`` is the human-readable error
          message; the caller stashes ``{target_id, view, error}`` in the
          batch frame's ``failed[]`` array.
        - ``navigate_frame`` (Fix #4): when the view's ``on_mount`` hook
          or auth stage redirects, ``handle_mount`` emits a
          ``{"type":"navigate","to":...}`` frame instead of a ``mount``
          frame. The collector preserves it so
          ``handle_mount_batch`` can forward the redirect to the client
          in the batch response — without this, the frame was silently
          dropped and the user was never redirected.

        Isolation: errors in one view MUST NOT propagate and kill the
        batch (see plan §2.3 "atomicity relaxed").
        """
        target_id = data_view.get("target_id") or ""
        view_path = data_view.get("view") or ""

        # Temporarily swap send_json with a collector so handle_mount's
        # frame-sending becomes a frame-collecting call. Restore on exit.
        captured: list = []
        orig_send_json = self.send_json

        async def _collect(payload):
            captured.append(payload)

        self.send_json = _collect  # type: ignore[assignment]
        # Mount-batch is never combined with sticky preservation (sticky
        # only runs through live_redirect_mount which doesn't batch) or
        # state_snapshot (snapshot is for popstate restoration, also
        # live_redirect_mount path). Always pass None for both.
        try:
            await self.handle_mount(
                data_view,
                sticky_preserved=None,
                state_snapshot=None,
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-view failures
            self.send_json = orig_send_json  # type: ignore[assignment]
            logger.exception(
                "mount_batch: _mount_one raised for view %s",
                sanitize_for_log(view_path),
            )
            from django.conf import settings as _settings

            # Fix #12 — do not leak exception text in production. In
            # DEBUG mode we still expose a truncated string to help
            # diagnose template / auth errors.
            safe_err = "mount failed"
            if getattr(_settings, "DEBUG", False):
                safe_err = str(exc)[:200]
            return False, {"target_id": target_id, "view": view_path}, safe_err, None
        finally:
            # Only restore if the try-block didn't already restore (else
            # we'd double-restore harmlessly). Idempotent.
            self.send_json = orig_send_json  # type: ignore[assignment]

        # Extract the successful mount frame; any "error" frame means failure.
        # Fix #4: capture "navigate" frames too — those are emitted when
        # auth or on_mount hooks redirect instead of mounting, and were
        # previously silently dropped.
        mount_frame = None
        error_frame = None
        navigate_frame = None
        for frame in captured:
            ftype = frame.get("type")
            if ftype == "mount":
                mount_frame = frame
            elif ftype == "error":
                error_frame = frame
            elif ftype == "navigate":
                navigate_frame = frame

        if navigate_frame is not None:
            # Redirect — surface through the batch response so the
            # client dispatcher can navigate. target_id is included so
            # the client can associate the redirect with the originating
            # lazy element.
            nav_payload = dict(navigate_frame)
            nav_payload["target_id"] = target_id
            nav_payload["view"] = view_path
            return False, {"target_id": target_id, "view": view_path}, None, nav_payload

        if error_frame is not None:
            err_msg = error_frame.get("message", "mount failed")
            return False, {"target_id": target_id, "view": view_path}, err_msg, None

        if mount_frame is None:
            return (
                False,
                {"target_id": target_id, "view": view_path},
                "mount produced no frame",
                None,
            )

        # Inject target_id for client-side per-view DOM targeting.
        mount_frame["target_id"] = target_id
        return True, mount_frame, None, None

    async def handle_mount_batch(self, data: Dict[str, Any]):
        """Mount multiple views in one frame and reply with one batch frame.

        Wire format:
        - Inbound: ``{"type":"mount_batch", "views":[{view, params, url,
          target_id, has_prerendered}, ...], "client_timezone"}``.
        - Outbound: ``{"type":"mount_batch", "session_id", "views":[...
          per-view payload with target_id...], "failed":[{target_id,
          view, error}...], "navigate":[{target_id, view, to, ...}...]}``.

        The optional ``navigate`` array (Fix #4) carries redirect
        targets for views whose ``on_mount`` or auth stage returned a
        redirect. The client's ``case 'mount_batch':`` iterates
        ``navigate[]`` and dispatches each.

        Atomicity is relaxed: one view's failure does NOT abort the
        batch — survivors ship, failures are isolated in ``failed[]``.
        """
        views_list = data.get("views", [])
        if not isinstance(views_list, list):
            await self.send_error("mount_batch: 'views' must be a list")
            return

        client_timezone = data.get("client_timezone")

        successes: list = []
        failures: list = []
        navigates: list = []
        for view_data in views_list:
            if not isinstance(view_data, dict):
                failures.append(
                    {
                        "target_id": "",
                        "view": "",
                        "error": "mount_batch entry is not a dict",
                    }
                )
                continue
            # Propagate shared client_timezone if not per-view.
            if client_timezone and "client_timezone" not in view_data:
                view_data = dict(view_data)
                view_data["client_timezone"] = client_timezone
            ok, payload, err, nav = await self._mount_one(view_data)
            if ok:
                successes.append(payload)
                continue
            if nav is not None:
                navigates.append(nav)
                continue
            failed = dict(payload)
            failed["error"] = err or "unknown"
            failures.append(failed)

        response: Dict[str, Any] = {
            "type": "mount_batch",
            "session_id": self.session_id,
            "views": successes,
            "failed": failures,
        }
        if navigates:
            response["navigate"] = navigates
        await self.send_json(response)

    async def handle_event(self, data: Dict[str, Any]):
        """Handle client events"""
        import time
        from djust.performance import PerformanceTracker

        # Start comprehensive performance tracking
        tracker = PerformanceTracker()
        PerformanceTracker.set_current(tracker)

        # Start timing
        start_time = time.perf_counter()
        timing = {}  # Keep for backward compatibility

        event_name = data.get("event")
        self._current_event_name = event_name  # For _dispatch_async_work
        params = data.get("params", {})

        # Event ref tracking (#560): client sends a monotonic ref with each
        # event so it can match responses to requests and distinguish event
        # responses from tick pushes. Coerce to int to prevent type confusion.
        raw_ref = data.get("ref")
        event_ref = int(raw_ref) if isinstance(raw_ref, (int, float)) else None
        self._current_event_ref = event_ref

        # Extract cache request ID if present (for @cache decorator)
        cache_request_id = params.get("_cacheRequestId")

        # Extract positional arguments from inline handler syntax
        # e.g., @click="set_period('month')" sends params._args = ['month']
        positional_args = params.pop("_args", [])

        logger.debug("[WebSocket] handle_event called: %s with params: %s", event_name, params)

        if not self.view_instance:
            await self.send_error("View not mounted. Please reload the page.")
            return

        # Route to embedded child view if view_id is specified.
        # The registry is provided by StickyChildRegistry (composed into
        # LiveView since v0.6.0 / Sticky LiveViews Phase A). The hasattr
        # guard stays for one release as defense in depth against custom
        # subclasses that override __init__ without calling super().
        view_id = params.pop("view_id", None)
        target_view = self.view_instance
        if view_id and view_id != getattr(self.view_instance, "_view_id", None):
            all_children = (
                self.view_instance._get_all_child_views()
                if hasattr(self.view_instance, "_get_all_child_views")
                else {}
            )
            target_view = all_children.get(view_id)
            if target_view is None:
                # Security: don't echo a client-supplied view_id into the
                # user-facing error string. The id is already logged via
                # the structured event in callers that need to trace it.
                await self.send_error(
                    "Embedded view not found",
                    extra={"view_id": sanitize_for_log(view_id)},
                )
                return

        # v0.7.0 — Activity gate: if the event was triggered inside a hidden
        # (non-eager) ``{% dj_activity %}`` region, the client already
        # dropped it — but defense-in-depth means the server also checks.
        # The client stamps ``_activity`` on every dispatch so we can both
        # verify the gate AND deliver per-activity deferral when a
        # client-side race slips through.
        _activity_name = params.pop("_activity", None)
        if (
            _activity_name
            and hasattr(target_view, "is_activity_visible")
            and not target_view.is_activity_visible(_activity_name)
            and not target_view._is_activity_eager(_activity_name)
        ):
            logger.debug(
                "[djust] Event %r on hidden activity %r — deferring",
                sanitize_for_log(event_name or ""),
                sanitize_for_log(_activity_name),
            )
            # Queued without permission/rate-limit check (by design:
            # per-handler auth runs on dispatch via _dispatch_single_event).
            # Per-activity cap bounds memory.
            target_view._queue_deferred_activity_event(_activity_name, event_name, params)
            # Send a no-op so the client's loading state clears; the event
            # will replay when the activity is next shown.
            await self._send_noop(ref=event_ref)
            return

        # Handle the event

        # Determine actor eligibility — embedded children do NOT have their
        # own actor in Phase A, so an event targeting a child must take the
        # sync path even when the parent consumer runs in actor mode.
        is_embedded_child_target = target_view is not self.view_instance

        if self.use_actors and self.actor_handle and not is_embedded_child_target:
            # Phase 5: Use actor system for event handling
            # Time-travel debugging (v0.6.1): capture state_before BEFORE
            # the permission check so permission-denied events are also
            # recorded (with no state_after change but a visible error).
            from djust.time_travel import (
                record_event_end as _tt_end,
                record_event_start as _tt_start,
            )

            _tt_snapshot = _tt_start(target_view, event_name, params, event_ref)
            _tt_error: Optional[str] = None
            try:
                logger.info("Handling event '%s' with actor system", event_name)

                # Security checks (shared with non-actor paths) — run on the
                # RESOLVED target (may be an embedded child) so a child's
                # handler is what gets validated/called, not the parent's.
                handler = await _validate_event_security(
                    self, event_name, target_view, self._rate_limiter
                )
                if handler is None:
                    _tt_error = "permission_denied"
                    return

                # Validate parameters before sending to actor
                coerce = get_handler_coerce_setting(handler)
                validation = validate_handler_params(
                    handler, params, event_name, coerce=coerce, positional_args=positional_args
                )
                if not validation["valid"]:
                    logger.error("Parameter validation failed: %s", validation["error"])
                    _tt_error = "validation_failed"
                    await self.send_error(
                        validation["error"],
                        validation_details={
                            "expected_params": validation["expected"],
                            "provided_params": validation["provided"],
                            "type_errors": validation["type_errors"],
                        },
                    )
                    return

                # Call actor event handler (will call Python handler internally)
                result = await self.actor_handle.event(event_name, params)

                # Send patches if available, otherwise full HTML
                patches = result.get("patches")
                html = result.get("html")
                version = result.get("version", 0)

                if patches:
                    # Parse patches JSON string to list
                    if isinstance(patches, str):
                        patches = fast_json_loads(patches)
                else:
                    # No patches - send full HTML update
                    logger.info(
                        "No patches from actor, sending full HTML update (length: %d). "
                        "Run with DJUST_VDOM_TRACE=1 for detailed diff output.",
                        len(html) if html else 0,
                    )

                await self._send_update(
                    patches=patches,
                    html=html,
                    version=version,
                    cache_request_id=cache_request_id,
                    event_name=event_name,
                )

            except Exception as e:
                _tt_error = str(e)[:200]
                view_class_name = (
                    self.view_instance.__class__.__name__ if self.view_instance else "Unknown"
                )
                response = handle_exception(
                    e,
                    error_type="event",
                    event_name=event_name,
                    view_class=view_class_name,
                    logger=logger,
                    log_message=f"Error in actor event handling for {view_class_name}.{sanitize_for_log(event_name)}()",
                )
                await self.send_json(response)
            finally:
                _tt_end(target_view, _tt_snapshot, error=_tt_error)
                await self._maybe_push_tt_event(target_view, _tt_snapshot)
                # v0.7.0 — Drain deferred activity queue in the actor path too.
                # The flush is async and awaited inline so drained events
                # complete in the SAME round-trip as this handler.
                if hasattr(target_view, "_flush_deferred_activity_events"):
                    try:
                        await target_view._flush_deferred_activity_events(self)
                    except Exception:  # noqa: BLE001
                        logger.exception("dj_activity: deferred-event flush raised (actor path)")

        else:
            # Non-actor mode: Use traditional flow
            # Check if this is a component event (Phase 4)
            component_id = params.get("component_id")
            is_embedded_child = target_view is not self.view_instance
            html = None
            patches = None
            version = 0

            # Acquire render lock to serialize with tick renders (#560).
            # This prevents ticks from rendering and incrementing the VDOM
            # version while an event handler is mid-execution.
            await self._render_lock.acquire()
            self._processing_user_event = True
            try:
                if component_id:
                    # Component event: route to component's event handler method
                    # Find the component instance
                    component = self.view_instance._components.get(component_id)
                    if not component:
                        error_msg = f"Component not found: {component_id}"
                        logger.error(error_msg)
                        await self.send_error(error_msg)
                        return

                    # Time-travel debugging (v0.6.1): record against the
                    # PARENT view because components don't have their own
                    # buffer in Phase 1. State mutations the component
                    # pushes into the parent (via send_parent) will show
                    # up in state_before/state_after. See limitations in
                    # docs/website/guides/time-travel-debugging.md —
                    # full component-level time travel is v0.6.2.
                    from djust.time_travel import (
                        record_event_end as _tt_end_c,
                        record_event_start as _tt_start_c,
                    )

                    _tt_c_snapshot = _tt_start_c(self.view_instance, event_name, params, event_ref)
                    _tt_c_error: Optional[str] = None
                    try:
                        # Security checks (shared with actor and view paths)
                        handler = await _validate_event_security(
                            self, event_name, component, self._rate_limiter
                        )
                        if handler is None:
                            _tt_c_error = "permission_denied"
                            return

                        # Extract component_id and remove from params
                        event_data = params.copy()
                        event_data.pop("component_id", None)

                        # Validate parameters before calling handler
                        # Pass positional_args so they can be mapped to named parameters
                        coerce = get_handler_coerce_setting(handler)
                        validation = validate_handler_params(
                            handler,
                            event_data,
                            event_name,
                            coerce=coerce,
                            positional_args=positional_args,
                        )
                        if not validation["valid"]:
                            logger.error("Parameter validation failed: %s", validation["error"])
                            _tt_c_error = "validation_failed"
                            await self.send_error(
                                validation["error"],
                                validation_details={
                                    "expected_params": validation["expected"],
                                    "provided_params": validation["provided"],
                                    "type_errors": validation["type_errors"],
                                },
                            )
                            return

                        # Use coerced params (with positional args merged in)
                        coerced_event_data = validation.get("coerced_params", event_data)

                        # Call component's event handler (supports both sync and async)
                        # This may call send_parent() which triggers handle_component_event()
                        handler_start = time.perf_counter()
                        # Observability: capture SQL queries fired by this handler.
                        from djust.observability.sql import capture_for_event as _dj_sql_capture

                        _sid = getattr(self, "session_id", None)
                        with _dj_sql_capture(
                            session_id=_sid,
                            event_id=f"{_sid}:{handler_start}" if _sid else None,
                            handler_name=event_name,
                        ):
                            try:
                                await _call_handler(
                                    handler,
                                    coerced_event_data if coerced_event_data else None,
                                )
                            except Exception as _tt_c_exc:
                                _tt_c_error = str(_tt_c_exc)[:200]
                                raise
                        timing["handler"] = (
                            time.perf_counter() - handler_start
                        ) * 1000  # Convert to ms
                    finally:
                        _tt_end_c(self.view_instance, _tt_c_snapshot, error=_tt_c_error)
                        await self._maybe_push_tt_event(self.view_instance, _tt_c_snapshot)

                    # Observability: record the component-handler duration
                    # for percentile stats. Best-effort — never disturbs
                    # handler execution.
                    try:
                        from djust.observability.timings import record_handler_timing

                        record_handler_timing(
                            target_view.__class__.__name__, event_name, timing["handler"]
                        )
                    except Exception:  # noqa: BLE001
                        pass

                    # ADR-002 Phase 1b/1c follow-up: propagate component events
                    # to parent LiveView waiters. Without this, a tutorial step
                    # that uses `wait_for="component_handler"` on a LiveView
                    # mixing in TutorialMixin would never advance if the matching
                    # handler lives on an embedded LiveComponent rather than the
                    # view itself. The notify pass runs AFTER the component
                    # handler completes so any waiters the handler itself
                    # created aren't self-resolved. Apps that need to
                    # disambiguate between identically-named events on
                    # different components can use the waiter's `predicate`
                    # argument to filter by `component_id` (which is always
                    # present in the kwargs dict below).
                    notify_kwargs = dict(coerced_event_data or {})
                    notify_kwargs.setdefault("component_id", component_id)
                    if hasattr(self.view_instance, "_notify_waiters"):
                        try:
                            self.view_instance._notify_waiters(event_name, notify_kwargs)
                        except Exception as exc:
                            logger.warning(
                                "Waiter notification for component event %r on %s failed: %s",
                                event_name,
                                component_id,
                                exc,
                            )
                else:
                    # Time-travel debugging (v0.6.1 — dev-only).
                    # Capture state_before BEFORE the permission check so
                    # permission-denied events are also recorded (with
                    # state_after == state_before but error set). The
                    # paired record_event_end runs in the finally at
                    # the end of this branch. No-op when the view didn't
                    # opt in. See djust.time_travel.
                    from djust.time_travel import (
                        record_event_end as _tt_end,
                        record_event_start as _tt_start,
                    )

                    _tt_snapshot = _tt_start(target_view, event_name, params, event_ref)
                    _tt_error: Optional[str] = None

                    # Use target_view for handler lookup (may be an embedded child)
                    # Security checks (shared with actor and component paths)
                    handler = await _validate_event_security(
                        self, event_name, target_view, self._rate_limiter
                    )
                    if handler is None:
                        _tt_error = "permission_denied"
                        _tt_end(target_view, _tt_snapshot, error=_tt_error)
                        await self._maybe_push_tt_event(target_view, _tt_snapshot)
                        return

                    # Validate parameters before calling handler
                    # Pass positional_args so they can be mapped to named parameters
                    coerce = get_handler_coerce_setting(handler)
                    validation = validate_handler_params(
                        handler, params, event_name, coerce=coerce, positional_args=positional_args
                    )
                    if not validation["valid"]:
                        logger.error("Parameter validation failed: %s", validation["error"])
                        _tt_error = "validation_failed"
                        _tt_end(target_view, _tt_snapshot, error=_tt_error)
                        await self._maybe_push_tt_event(target_view, _tt_snapshot)
                        await self.send_error(
                            validation["error"],
                            validation_details={
                                "expected_params": validation["expected"],
                                "provided_params": validation["provided"],
                                "type_errors": validation["type_errors"],
                            },
                        )
                        return

                    # Use coerced params (with positional args merged in)
                    coerced_params = validation.get("coerced_params", params)

                    # Wrap everything in a root "Event Processing" tracker
                    with tracker.track("Event Processing"):
                        # Snapshot public assigns before the handler to detect
                        # unchanged state and auto-skip the render cycle.
                        pre_assigns = _snapshot_assigns(self.view_instance)
                        # Identity snapshot: {attr: id(value)} for the
                        # push_commands-only auto-skip (#700). Immune to
                        # deep-copy sentinel issues on non-copyable objects.
                        pre_identity = {
                            k: id(v)
                            for k, v in self.view_instance.__dict__.items()
                            if not k.startswith("_")
                        }

                        # Call handler with tracking (supports both sync and async handlers)
                        handler_start = time.perf_counter()
                        # Observability: capture SQL queries fired by this handler.
                        from djust.observability.sql import (
                            capture_for_event as _dj_sql_capture,
                        )

                        _sid = getattr(self, "session_id", None)
                        try:
                            with tracker.track(
                                "Event Handler",
                                event_name=event_name,
                                params=coerced_params,
                            ):
                                with profiler.profile(profiler.OP_EVENT_HANDLE):
                                    with _dj_sql_capture(
                                        session_id=_sid,
                                        event_id=f"{_sid}:{handler_start}" if _sid else None,
                                        handler_name=event_name,
                                    ):
                                        await _call_handler(
                                            handler,
                                            coerced_params if coerced_params else None,
                                        )
                        except Exception as _tt_exc:
                            _tt_error = str(_tt_exc)[:200]
                            raise
                        finally:
                            _tt_end(target_view, _tt_snapshot, error=_tt_error)
                            await self._maybe_push_tt_event(target_view, _tt_snapshot)
                        timing["handler"] = (
                            time.perf_counter() - handler_start
                        ) * 1000  # Convert to ms

                        # Observability: record the view-handler duration
                        # for percentile stats. Best-effort.
                        try:
                            from djust.observability.timings import record_handler_timing

                            record_handler_timing(
                                target_view.__class__.__name__, event_name, timing["handler"]
                            )
                        except Exception:  # noqa: BLE001
                            pass

                        # ADR-002 Phase 1b: resolve any pending wait_for_event
                        # waiters on the target view whose event_name matches.
                        # Runs AFTER the handler completes so new waiters
                        # created during this call aren't self-notified.
                        # No-op if the view doesn't have any pending waiters.
                        if hasattr(target_view, "_notify_waiters"):
                            try:
                                target_view._notify_waiters(event_name, coerced_params or {})
                            except Exception as exc:
                                logger.warning(
                                    "Waiter notification for %r failed: %s",
                                    event_name,
                                    exc,
                                )

                        # Auto-detect unchanged state: if no public assigns were
                        # reassigned, auto-skip the render (eliminates DJE-053).
                        # In-place mutations (list.append) are NOT detected and
                        # will still trigger a render — this is the safe default.
                        skip_render = getattr(self.view_instance, "_skip_render", False)
                        # Never skip if the view explicitly requests full HTML
                        force_html = getattr(self.view_instance, "_force_full_html", False)
                        if not skip_render and not force_html:
                            post_assigns = _snapshot_assigns(self.view_instance)
                            if pre_assigns == post_assigns:
                                skip_render = True
                                logger.debug(
                                    "[djust] Auto-skipping render for '%s' — no assigns changed",
                                    event_name,
                                )
                            else:
                                # Phoenix-style: track which keys actually changed
                                # so _sync_state_to_rust can skip unchanged values
                                self.view_instance._changed_keys = _compute_changed_keys(
                                    pre_assigns, post_assigns
                                )

                        # #700: push_commands-only handlers auto-skip render.
                        # When a handler only calls push_commands() / push_event()
                        # without changing real state, a VDOM re-render is wasted
                        # work (and can cause morphdom recovery during tours).
                        # The assigns snapshot above may report false positives
                        # for views with non-copyable public attrs (querysets,
                        # file handles) because the sentinel objects always differ.
                        # When push events are pending, check if the *identity*
                        # of each public attr is unchanged — if so, the handler
                        # didn't touch any state and we can safely skip.
                        if not skip_render and not force_html:
                            pending = getattr(self.view_instance, "_pending_push_events", None)
                            if pending:
                                post_identity = {
                                    k: id(v)
                                    for k, v in self.view_instance.__dict__.items()
                                    if not k.startswith("_")
                                }
                                if pre_identity == post_identity:
                                    skip_render = True
                                    logger.debug(
                                        "[djust] Auto-skipping render for '%s' "
                                        "— push_commands only, no state changed",
                                        event_name,
                                    )

                        if skip_render:
                            self.view_instance._skip_render = False
                            has_async = (
                                getattr(self.view_instance, "_async_pending", None) is not None
                            )
                            await self._flush_push_events()
                            await self._flush_flash()
                            await self._flush_page_metadata()
                            await self._flush_pending_layout()
                            await self._send_noop(async_pending=has_async, ref=event_ref)
                            if has_async:
                                await self._dispatch_async_work()
                            return

                        # Get updated HTML and patches with tracking
                        render_start = time.perf_counter()
                        with tracker.track("Template Render"):
                            if is_embedded_child:
                                # Embedded child: render just the child's template
                                # and send full HTML scoped to the child's container
                                with tracker.track("Embedded Child Render"):
                                    html = await sync_to_async(self._render_embedded_child)(
                                        target_view
                                    )
                                    patches = None  # Send full HTML for the child subtree
                                    version = 0
                                    _emit_full_html_update(
                                        target_view,
                                        "embedded_child",
                                        event_name,
                                        html,
                                        version,
                                    )
                            else:
                                with tracker.track("Context + Render") as batch_node:
                                    # Check if we can skip sync_to_async entirely:
                                    # - async get_context_data → await directly
                                    # - sync_safe = True → no I/O, safe on event loop
                                    _gcd = self.view_instance.get_context_data
                                    _skip_thread = inspect.iscoroutinefunction(_gcd) or getattr(
                                        self.view_instance, "sync_safe", False
                                    )
                                    if _skip_thread:
                                        # Fast path: run everything on the event loop.
                                        t0 = time.perf_counter()
                                        if inspect.iscoroutinefunction(_gcd):
                                            context = await _gcd()
                                        else:
                                            context = _gcd()
                                        ctx_ms = (time.perf_counter() - t0) * 1000
                                        # Rust render is pure CPU — safe on event loop.
                                        t1 = time.perf_counter()
                                        with profiler.profile(profiler.OP_RENDER):
                                            html, patches, version = (
                                                self.view_instance.render_with_diff(
                                                    preloaded_context=context
                                                )
                                            )
                                        diff_ms = (time.perf_counter() - t1) * 1000
                                    else:
                                        # Sync path: batch in a single thread hop.
                                        def _sync_context_and_render():
                                            t0 = time.perf_counter()
                                            ctx = _gcd()
                                            t1 = time.perf_counter()
                                            with profiler.profile(profiler.OP_RENDER):
                                                r_html, r_patches, r_version = (
                                                    self.view_instance.render_with_diff()
                                                )
                                            t2 = time.perf_counter()
                                            return (
                                                ctx,
                                                r_html,
                                                r_patches,
                                                r_version,
                                                (t1 - t0) * 1000,
                                                (t2 - t1) * 1000,
                                            )

                                        (
                                            context,
                                            html,
                                            patches,
                                            version,
                                            ctx_ms,
                                            diff_ms,
                                        ) = await sync_to_async(_sync_context_and_render)()
                                    # Record sub-phase durations so the profiler can
                                    # still distinguish slow context prep from slow
                                    # VDOM diffing, even though they share one thread hop.
                                    batch_node.metadata["context_prep_ms"] = ctx_ms
                                    batch_node.metadata["vdom_diff_ms"] = diff_ms
                                    # Per-phase Rust timing (template render, HTML parse, VDOM diff, serialize)
                                    rust_timing = getattr(
                                        self.view_instance, "_rust_render_timing", None
                                    )
                                    if rust_timing:
                                        batch_node.metadata["rust_timing"] = rust_timing
                                    tracker.track_context_size(context)

                                    patch_list = None  # Initialize for later use
                                    # patches can be: JSON string with patches, "[]" for empty, or None
                                    if patches is not None:
                                        patch_list = fast_json_loads(patches) if patches else []
                                        tracker.track_patches(len(patch_list), patch_list)
                                        profiler.record(profiler.OP_DIFF, 0)  # Mark diff occurred
                        timing["render"] = (
                            time.perf_counter() - render_start
                        ) * 1000  # Convert to ms

                # Check if form reset is requested (FormMixin sets this flag)
                should_reset_form = getattr(self.view_instance, "_should_reset_form", False)
                if should_reset_form:
                    # Clear the flag
                    self.view_instance._should_reset_form = False

                # Detect async work scheduled by start_async() — tell client
                # to keep loading state active until the background callback
                # sends its own update.
                has_async = getattr(self.view_instance, "_async_pending", None) is not None

                # Embedded child view: send scoped HTML update
                if is_embedded_child and view_id:
                    await self.send_json(
                        {
                            "type": "embedded_update",
                            "view_id": view_id,
                            "html": html,
                            "event_name": event_name,
                        }
                    )
                    await self._flush_push_events()
                    await self._flush_flash()
                    await self._flush_page_metadata()
                    await self._flush_pending_layout()
                    await self._flush_navigation()
                    await self._flush_i18n()
                else:
                    # For component events, send full HTML instead of patches
                    # Component VDOM is separate from parent VDOM, causing path mismatches
                    # TODO Phase 4.1: Implement per-component VDOM tracking
                    if component_id:
                        patches = None
                        _emit_full_html_update(
                            self.view_instance,
                            "component_event",
                            event_name,
                            html,
                            version,
                        )

                    # Allow views to force full HTML by setting _force_full_html = True
                    # in their event handler. Useful when the handler changes data that
                    # affects {% for %} loop lengths, which VDOM can't diff correctly (#559).
                    # We discard patches and send html instead, but do NOT reset the Rust
                    # VDOM — the current render already established the new baseline.
                    if getattr(self.view_instance, "_force_full_html", False):
                        self.view_instance._force_full_html = False
                        patches = None
                        patch_list = None
                        _emit_full_html_update(
                            self.view_instance,
                            "force_full_html",
                            event_name,
                            html,
                            version,
                        )

                    # For views with dynamic templates (template as property),
                    # patches may be empty because VDOM state is lost on recreation.
                    # In that case, send full HTML update.

                    # Patch compression: if patch count exceeds threshold and HTML is smaller,
                    # send HTML instead of patches for better performance
                    PATCH_COUNT_THRESHOLD = 100
                    # Note: patch_list was already parsed earlier for performance tracking
                    if patches and patch_list:
                        patch_count = len(patch_list)
                        if patch_count > PATCH_COUNT_THRESHOLD:
                            # Compare sizes to decide whether to send patches or HTML
                            patches_size = len(patches.encode("utf-8"))
                            html_size = len(html.encode("utf-8"))
                            # If HTML is at least 30% smaller, send HTML instead
                            if html_size < patches_size * 0.7:
                                logger.debug(
                                    "Patch compression: %d patches (%dB) -> sending HTML (%dB) instead",
                                    patch_count,
                                    patches_size,
                                    html_size,
                                )
                                # Reset VDOM and send HTML
                                if (
                                    hasattr(self.view_instance, "_rust_view")
                                    and self.view_instance._rust_view
                                ):
                                    self.view_instance._rust_view.reset()
                                patches = None
                                patch_list = None
                                _emit_full_html_update(
                                    self.view_instance,
                                    "patch_compression",
                                    event_name,
                                    html,
                                    version,
                                    patch_count=patch_count,
                                )

                    # Note: patch_list can be [] (empty list) which is valid - means no changes needed
                    # Only send full HTML if patches is None (not just falsy)
                    if patches is not None and patch_list is not None:
                        if len(patch_list) == 0 and version > 1:
                            # Empty diff is normal and expected for idempotent handlers
                            # (e.g. toggle clicked when already in target state, debounced
                            # input with unchanged results, side-effect-only handlers).
                            # Phoenix LiveView silently drops these — we do the same.
                            logger.debug(
                                "[djust] Event '%s' on %s produced no DOM changes (empty diff). "
                                "This is normal for idempotent handlers. "
                                "If state changes are not reflected in the UI, ensure the "
                                "modified variable is rendered inside <div dj-root> and "
                                "run 'python manage.py check --tag djust'.",
                                event_name,
                                self.view_instance.__class__.__name__,
                            )

                        # Calculate timing for JSON mode
                        timing["total"] = (
                            time.perf_counter() - start_time
                        ) * 1000  # Total server time
                        perf_summary = tracker.get_summary()

                        # Store rendered HTML for on-demand recovery.
                        # Client sends request_html when applyPatches() fails
                        # (e.g., {% if %} blocks shifting DOM structure).
                        self._recovery_html = html
                        self._recovery_version = version

                        await self._send_update(
                            patches=patch_list,
                            version=version,
                            cache_request_id=cache_request_id,
                            reset_form=should_reset_form,
                            timing=timing,
                            performance=perf_summary,
                            event_name=event_name,
                            async_pending=has_async,
                            source="event",
                            ref=event_ref,
                        )
                    else:
                        # patches=None means VDOM diff failed or was skipped - send full HTML
                        # Batch strip + extract into a single thread hop
                        # to avoid two separate sync_to_async crossings.
                        def _sync_strip_and_extract(raw_html):
                            stripped = self.view_instance._strip_comments_and_whitespace(raw_html)
                            content = self.view_instance._extract_liveview_content(stripped)
                            return stripped, content

                        html, html_content = await sync_to_async(_sync_strip_and_extract)(html)

                        if version > 1:
                            _template = (
                                getattr(self.view_instance, "template_name", None)
                                or "<inline template>"
                            )
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
                                event_name,
                                self.view_instance.__class__.__name__,
                                _template,
                            )
                        else:
                            logger.debug("[WebSocket] First render, sending full HTML update.")
                        logger.debug(
                            "[WebSocket] html_content length: %d, starts with: %s...",
                            len(html_content),
                            html_content[:150],
                        )

                        # Emit signal — distinguish first render from diff failure
                        if not component_id:
                            reason = "first_render" if version <= 1 else "no_patches"
                            _ctx = context if context else {}
                            _snapshot = (
                                _build_context_snapshot(_ctx) if reason == "no_patches" else None
                            )
                            _prev_html = getattr(self.view_instance, "_previous_html", None)
                            _emit_full_html_update(
                                self.view_instance,
                                reason,
                                event_name,
                                html_content,
                                version,
                                context_snapshot=_snapshot,
                                html_snippet=(
                                    html_content[:500] if reason == "no_patches" else None
                                ),
                                previous_html_snippet=(
                                    _prev_html[:500]
                                    if reason == "no_patches" and _prev_html
                                    else None
                                ),
                            )

                        await self._send_update(
                            html=html_content,
                            version=version,
                            cache_request_id=cache_request_id,
                            reset_form=should_reset_form,
                            event_name=event_name,
                            async_pending=has_async,
                            source="event",
                            ref=event_ref,
                        )

                # Check for async work scheduled by start_async()
                await self._dispatch_async_work()

                # v0.7.0 — If this handler flipped any activity to visible,
                # drain its deferred-event queue now so in-flight events
                # for that panel are delivered in-order in the same
                # round-trip. The flush is async and awaited inline.
                # Safe to call even when no activities exist.
                if hasattr(target_view, "_flush_deferred_activity_events"):
                    try:
                        await target_view._flush_deferred_activity_events(self)
                    except Exception:  # noqa: BLE001 — never fail the event for a drain bug
                        logger.exception("dj_activity: deferred-event flush raised")

            except Exception as e:
                view_class_name = (
                    self.view_instance.__class__.__name__ if self.view_instance else "Unknown"
                )
                event_type = "component event" if component_id else "event"
                response = handle_exception(
                    e,
                    error_type="event",
                    event_name=event_name,
                    view_class=view_class_name,
                    logger=logger,
                    log_message=f"Error in {view_class_name}.{sanitize_for_log(event_name)}() ({event_type})",
                )
                await self.send_json(response)
            finally:
                self._processing_user_event = False
                self._render_lock.release()

    # ========================================================================
    # Embedded LiveView Rendering
    # ========================================================================

    def _render_embedded_child(self, child_view) -> str:
        """
        Render an embedded child view's template and return the inner HTML.

        This re-renders just the child's template using Django's template engine,
        without going through the parent's VDOM at all.
        """
        try:
            context = child_view.get_context_data()
            from django.template import engines

            template_str = child_view.get_template()
            engine = engines["django"] if "django" in engines else list(engines.all())[0]
            tmpl = engine.from_string(template_str)
            html = tmpl.render(context)
            return html
        except Exception as e:
            logger.error("Failed to render embedded child %s: %s", child_view.__class__.__name__, e)
            return f"<!-- Error rendering embedded child: {e} -->"

    # ========================================================================
    # File Upload Handling
    # ========================================================================

    async def _handle_upload_register(self, data: Dict[str, Any]) -> None:
        """Handle upload_register message: client announces a file to upload."""
        if not self.view_instance:
            await self.send_error("View not mounted")
            return

        if (
            not hasattr(self.view_instance, "_upload_manager")
            or not self.view_instance._upload_manager
        ):
            await self.send_error("No uploads configured for this view")
            return

        mgr = self.view_instance._upload_manager
        entry = mgr.register_entry(
            upload_name=data.get("upload_name", ""),
            ref=data.get("ref", ""),
            client_name=data.get("client_name", ""),
            client_type=data.get("client_type", ""),
            client_size=data.get("client_size", 0),
        )

        if entry:
            await self.send_json(
                {
                    "type": "upload_registered",
                    "ref": entry.ref,
                    "upload_name": entry.upload_name,
                }
            )
        else:
            await self.send_error("Upload rejected (check file type, size, or max entries)")

    async def _handle_upload_resume(self, data: Dict[str, Any]) -> None:
        """Handle ``upload_resume`` — client-initiated resume of an
        upload whose state survived a WebSocket disconnect (#821 /
        ADR-010).

        Client payload:

            {"type": "upload_resume", "ref": "<upload_id>"}

        Reply (always a ``upload_resumed`` JSON message with one of
        three ``status`` values):

            {"type": "upload_resumed", "ref": "...",
             "status": "resumed" | "not_found" | "locked",
             "bytes_received": N, "chunks_received": [0, 1, 2, ...]}

        Session-scoped access: the state entry's stored ``session_key``
        must match the current WS session, else we reply ``not_found``
        (same response as missing — prevents existence-probe leak).
        """
        from .uploads.resumable import resolve_resume_request

        upload_id = data.get("ref") or data.get("upload_id")
        if not upload_id or not isinstance(upload_id, str):
            await self.send_error("upload_resume requires a ref")
            return

        session_key = None
        try:
            session = self.scope.get("session") if hasattr(self, "scope") else None
            if session is not None:
                # Session object may need loading — access .session_key
                # synchronously; Channels' SessionMiddlewareStack
                # guarantees the key is available by the time the WS
                # message loop is running.
                session_key = getattr(session, "session_key", None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("upload_resume: failed to read session key: %s", exc)

        # Active-ref check: is another in-flight upload using this id?
        active = False
        try:
            if self.view_instance and hasattr(self.view_instance, "_upload_manager"):
                mgr = self.view_instance._upload_manager
                if mgr is not None:
                    existing = mgr._entries.get(upload_id)
                    if existing and not existing._complete and not existing._error:
                        active = True
        except Exception:  # noqa: BLE001
            # Defensive — resume must never crash the consumer.
            logger.exception("upload_resume: active-ref check failed")

        payload = resolve_resume_request(
            upload_id=upload_id,
            session_key=session_key,
            active_refs=(lambda _uid, _a=active: _a),
        )
        await self.send_json(payload)

    async def _handle_upload_frame(self, data: bytes) -> None:
        """Handle binary upload frame (chunk, complete, cancel)."""
        from .uploads import parse_upload_frame, build_progress_message

        if not self.view_instance or not hasattr(self.view_instance, "_upload_manager"):
            return

        mgr = self.view_instance._upload_manager
        if not mgr:
            return

        frame = parse_upload_frame(data)
        if not frame:
            logger.warning("Invalid upload frame received")
            return

        ref = frame["ref"]

        if frame["type"] == "chunk":
            # add_chunk() internally dispatches to either the disk-buffer
            # path or (when the upload slot was configured with writer=)
            # the writer's write_chunk() — no disk I/O on the writer path.
            progress = mgr.add_chunk(ref, frame["chunk_index"], frame["data"])
            if progress is not None:
                # Send progress update (throttle to every 10%)
                entry = mgr._entries.get(ref)
                if entry and (progress % 10 == 0 or progress >= 100):
                    await self.send_json(build_progress_message(ref, progress))
            else:
                # Surface error details (writer exception, size-limit, etc.)
                err_entry = mgr._entries.get(ref)
                error_msg = err_entry.error if err_entry and err_entry.error else None
                msg = build_progress_message(ref, 0, "error")
                if error_msg:
                    msg["error"] = error_msg
                await self.send_json(msg)

        elif frame["type"] == "complete":
            entry = mgr.complete_upload(ref)
            if entry:
                await self.send_json(build_progress_message(ref, 100, "complete"))
            else:
                err_entry = mgr._entries.get(ref)
                error_msg = err_entry.error if err_entry else "Unknown error"
                await self.send_json(
                    {
                        "type": "upload_progress",
                        "ref": ref,
                        "progress": 0,
                        "status": "error",
                        "error": error_msg,
                    }
                )

        elif frame["type"] == "cancel":
            mgr.cancel_upload(ref)
            await self.send_json(build_progress_message(ref, 0, "cancelled"))

    def _extract_cache_config(self) -> Dict[str, Any]:
        """
        Extract cache configuration from handlers with @cache decorator.

        Returns a dict mapping handler names to their cache config:
        {
            "search": {"ttl": 300, "key_params": ["query"]},
            "get_stats": {"ttl": 60, "key_params": []}
        }
        """
        if not self.view_instance:
            return {}

        cache_config = {}

        # Inspect all methods for @cache decorator metadata
        for name in dir(self.view_instance):
            if name.startswith("_"):
                continue

            try:
                method = getattr(self.view_instance, name)
                if callable(method) and hasattr(method, "_djust_decorators"):
                    decorators = method._djust_decorators
                    if "cache" in decorators:
                        cache_info = decorators["cache"]
                        cache_config[name] = {
                            "ttl": cache_info.get("ttl", 60),
                            "key_params": cache_info.get("key_params", []),
                        }
            except Exception as e:
                # Skip methods that can't be inspected, but log for debugging
                logger.debug("Could not inspect method '%s' for cache config: %s", name, e)

        return cache_config

    def _extract_optimistic_rules(self) -> Dict[str, Any]:
        """Extract optimistic UI rules from descriptor components (DEP-002).

        Returns a dict mapping event names to their optimistic rules:
        {
            "accordion_toggle": {
                "action": "toggle_class",
                "target": "[data-value='{value}']",
                "class": "dj-accordion-item--open"
            }
        }

        Only includes rules for components with tier="optimistic".
        Client-tier components are excluded (no server event at all).
        """
        if not self.view_instance:
            return {}

        descriptors = getattr(type(self.view_instance), "_component_descriptors", None)
        if not descriptors:
            return {}

        rules = {}
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

    async def send_json(self, data: Dict[str, Any]):
        """Send JSON message to client with Django type support"""
        await self.send(text_data=json.dumps(data, cls=DjangoJSONEncoder))

    @staticmethod
    def _clear_template_caches():
        """
        Clear Django's template loader caches.

        This ensures hot reload picks up template changes by clearing:
        - Template loader caches (cached_property on loaders)
        - Engine-level template caches

        Supports Django's built-in template backends:
        - django.template.backends.django.DjangoTemplates
        - django.template.backends.jinja2.Jinja2 (if installed)

        Returns:
            int: Number of caches cleared successfully
        """
        from django.template import engines

        caches_cleared = 0

        for engine in engines.all():
            if hasattr(engine, "engine"):
                try:
                    # Clear cached templates from loaders
                    if hasattr(engine.engine, "template_loaders"):
                        for loader in engine.engine.template_loaders:
                            if hasattr(loader, "reset"):
                                loader.reset()
                                caches_cleared += 1
                except Exception as e:
                    hotreload_logger.warning(
                        "Could not clear template cache for %s: %s", engine.name, e
                    )

        hotreload_logger.debug("Cleared %d template caches", caches_cleared)
        return caches_cleared

    async def hotreload(self, event):
        """
        Handle hot reload broadcast messages from channel layer.

        This is called when a file change is detected and a reload message
        is broadcast to the djust_hotreload group.

        Instead of full page reload, we re-render the view and send a VDOM patch.

        Args:
            event: Channel layer event containing 'file' key

        Raises:
            None - All exceptions are caught and trigger full reload fallback
        """
        import time
        from channels.db import database_sync_to_async
        from django.template import TemplateDoesNotExist

        file_path = event.get("file", "unknown")

        # ------------------------------------------------------------------
        # Hot View Replacement (v0.6.1) — optional pre-step
        # ------------------------------------------------------------------
        # When the watcher reloaded a LiveView module, it attaches
        # ``hvr_meta`` to the broadcast event. Try to swap the view
        # instance's ``__class__`` to the new class so subsequent event
        # handlers dispatch to the new code without losing state.
        #
        # Failure modes:
        #   - dedup hit (same reload_id already applied) → silent no-op.
        #   - module not resolvable / classes gone → fall through to the
        #     legacy template-refresh path below.
        #   - compat check rejects the swap → emit full-reload frame and
        #     return (no point re-rendering old state against new slots).
        hvr_meta = event.get("hvr_meta")
        if hvr_meta and self.view_instance:
            reload_id = hvr_meta.get("reload_id")
            if reload_id and reload_id == self._hvr_last_reload_id:
                # Same reload burst — already handled on this consumer.
                return
            if reload_id:
                self._hvr_last_reload_id = reload_id

            from djust.hot_view_replacement import (
                _resolve_class_pairs,
                apply_class_swap,
            )

            class_pairs = _resolve_class_pairs(
                hvr_meta.get("module", ""),
                hvr_meta.get("class_names", []) or [],
            )
            if class_pairs is not None:
                ok, reason = apply_class_swap(self.view_instance, class_pairs)
                if not ok:
                    hotreload_logger.info(
                        "HVR incompat: %s; falling back to full reload",
                        reason,
                    )
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return
                self._hvr_version += 1
                await self.send_json(
                    {
                        "type": "hvr-applied",
                        "view": getattr(self, "_view_path", ""),
                        "version": self._hvr_version,
                        "file": file_path,
                    }
                )
                # Continue into template-refresh path below so the
                # re-render picks up any template or computed attr
                # changes that also shipped in this burst.

        # If we have an active view, re-render and send patch
        if self.view_instance:
            start_time = time.time()

            try:
                # Clear Django's template cache so we pick up the file changes
                self._clear_template_caches()

                # Force view to reload template by clearing cached template
                if hasattr(self.view_instance, "_template"):
                    delattr(self.view_instance, "_template")

                # Get the new template content
                try:
                    new_template = await database_sync_to_async(self.view_instance.get_template)()
                except TemplateDoesNotExist as e:
                    hotreload_logger.error("Template not found for hot reload: %s", e)
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return

                # Update the RustLiveView with the new template (keeps old VDOM for diffing!)
                if hasattr(self.view_instance, "_rust_view") and self.view_instance._rust_view:
                    hotreload_logger.debug("Updating template in existing RustLiveView")
                    await database_sync_to_async(self.view_instance._rust_view.update_template)(
                        new_template
                    )

                # Re-render the view to get patches (track time)
                render_start = time.time()
                html, patches, version = await database_sync_to_async(
                    self.view_instance.render_with_diff
                )()
                render_time = (time.time() - render_start) * 1000  # Convert to ms

                patch_count = len(patches) if patches else 0
                hotreload_logger.info(
                    "Generated %d patches in %.2fms, version=%d", patch_count, render_time, version
                )

                # Warn if patch generation is slow
                if render_time > 100:
                    hotreload_logger.warning(
                        "Slow patch generation: %.2fms for %s", render_time, file_path
                    )

                # Handle case where no patches are generated
                if not patches:
                    hotreload_logger.info("No patches generated, sending full reload")
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return

                # Parse patches if they're a JSON string
                try:
                    if isinstance(patches, str):
                        patches = fast_json_loads(patches)
                except (json.JSONDecodeError, ValueError) as e:
                    hotreload_logger.error("Failed to parse patches JSON: %s", e)
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return

                # Send the patches to the client
                await self._send_update(
                    patches=patches,
                    version=version,
                    hotreload=True,
                    file_path=file_path,
                )

                total_time = (time.time() - start_time) * 1000
                hotreload_logger.info(
                    "Sent %d patches for %s (total: %.2fms)", patch_count, file_path, total_time
                )

            except Exception as e:
                # Catch-all for unexpected errors
                hotreload_logger.exception("Error generating patches for %s: %s", file_path, e)
                # Fallback to full reload on error
                await self.send_json(
                    {
                        "type": "reload",
                        "file": file_path,
                    }
                )
        else:
            # No active view, just reload the page
            hotreload_logger.debug("No active view, sending full reload for %s", file_path)
            await self.send_json(
                {
                    "type": "reload",
                    "file": file_path,
                }
            )

    async def handle_url_change(self, data: Dict[str, Any]):
        """
        Handle URL change from browser back/forward (popstate) or dj-patch clicks.

        Calls handle_params() on the view so it can update state based on the
        new URL params, then re-renders and sends patches.
        """
        if not self.view_instance:
            await self.send_error("View not mounted")
            return

        params = data.get("params", {})
        uri = data.get("uri", "")

        try:
            # Call handle_params on the view
            await sync_to_async(self.view_instance.handle_params)(params, uri)

            # Sync state and re-render
            if hasattr(self.view_instance, "_sync_state_to_rust"):
                await sync_to_async(self.view_instance._sync_state_to_rust)()

            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

            # Allow views to force full HTML by setting _force_full_html = True
            # in handle_params (e.g. when {% for %} loop lengths change, #559).
            if getattr(self.view_instance, "_force_full_html", False):
                self.view_instance._force_full_html = False
                patches = None

            if patches is not None:
                if isinstance(patches, str):
                    patches = fast_json_loads(patches)
                await self._send_update(patches=patches, version=version, event_name="url_change")
            else:
                html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(html)
                html = await sync_to_async(self.view_instance._extract_liveview_content)(html)
                await self._send_update(html=html, version=version, event_name="url_change")

        except Exception as e:
            response = handle_exception(
                e,
                error_type="event",
                event_name="url_change",
                logger=logger,
                log_message="Error in handle_params()",
            )
            await self.send_json(response)

    async def handle_live_redirect_mount(self, data: Dict[str, Any]):
        """
        Handle mounting a new view via live_redirect (no WS reconnect).

        The client sends this after receiving a live_redirect navigation command.
        We unmount the current view and mount the new one on the same connection.

        Sticky LiveViews (Phase B):

        * Before teardown, stage the old view's sticky children via
          :meth:`LiveView._preserve_sticky_children`, passing a
          reconstructed request for the new URL so auth is re-checked
          against the new posture. Survivors are stashed on
          ``self._sticky_preserved`` — they hold strong references to
          the sticky ``LiveView`` instances so the normal old-view
          cleanup doesn't GC them.
        * After the new view mounts + renders, scan the rendered HTML
          for ``[dj-sticky-slot="<id>"]`` markers. For each match, call
          ``new_parent._register_child(id, child)`` to transplant the
          preserved instance onto the new parent. Unmatched survivors
          get ``_on_sticky_unmount()`` called and are discarded.
        * Emit a ``sticky_hold`` frame BEFORE the ``mount`` frame listing
          the final survivor ids, so the client reconciles its
          stickyStash against an authoritative list.
        """
        # Reuse handle_mount — it already handles everything
        # But first, stage the old view's sticky children (Phase B).
        old_view = self.view_instance
        sticky_preserved: Dict[str, Any] = {}
        if old_view is not None and hasattr(old_view, "_preserve_sticky_children"):
            try:
                new_request = self._build_live_redirect_request(data)
                if new_request is None:
                    # URL resolution failed — treat as "auth cannot be
                    # re-checked" and drop every staged sticky by calling
                    # their unmount hooks. Better to unmount than to
                    # retain a sticky whose auth posture we can't
                    # validate against the new URL.
                    for _vid, child in list(
                        old_view._get_all_child_views().items()
                        if hasattr(old_view, "_get_all_child_views")
                        else []
                    ):
                        if getattr(child, "sticky", False) is True:
                            hook = getattr(child, "_on_sticky_unmount", None)
                            if callable(hook):
                                try:
                                    hook()
                                except Exception:  # noqa: BLE001
                                    logger.exception("sticky child _on_sticky_unmount raised")
                    sticky_preserved = {}
                else:
                    sticky_preserved = await sync_to_async(old_view._preserve_sticky_children)(
                        new_request
                    )
            except Exception:  # noqa: BLE001 — defensive: never break redirect
                logger.exception("sticky children staging failed; proceeding without preservation")
                sticky_preserved = {}
        # Stash on the consumer for post-mount reattachment.
        self._sticky_preserved = sticky_preserved

        # Leave old view's channel group
        if self._view_group:
            await self.channel_layer.group_discard(self._view_group, self.channel_name)
            self._view_group = None

        # Cancel old tick task
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass  # Expected when cancelling a running tick task
            self._tick_task = None

        # Clean up old view
        if old_view:
            # Before cleanup_uploads, drop sticky children from the old
            # view's registry so the normal unregister path doesn't call
            # their _cleanup_on_unregister hook — sticky children SURVIVE
            # this navigation and keep running on their stash refs.
            if hasattr(old_view, "_child_views"):
                for sticky_id, sticky_child in sticky_preserved.items():
                    # Child may have been registered under its auto-
                    # generated view_id OR under its sticky_id. Find by
                    # identity because sticky_id may differ from the
                    # original registered view_id.
                    for vid, c in list(old_view._child_views.items()):
                        if c is sticky_child:
                            # Pop WITHOUT calling _unregister_child (which
                            # would invoke _cleanup_on_unregister). Sticky
                            # children keep running.
                            old_view._child_views.pop(vid, None)
                            break
            if hasattr(old_view, "_cleanup_uploads"):
                try:
                    old_view._cleanup_uploads()
                except Exception:
                    logger.warning("Failed to clean up uploads for old view", exc_info=True)

        self.view_instance = None

        # Parse state_snapshot if the client sent one (v0.6.0) so it can
        # be forwarded to handle_mount for back-nav state restoration.
        # Per-view opt-in is still enforced inside handle_mount via the
        # class-level ``enable_state_snapshot`` flag + slug match.
        state_snapshot = data.get("state_snapshot")
        if state_snapshot is not None and not isinstance(state_snapshot, dict):
            # Malformed payload — ignore and proceed with fresh mount.
            logger.warning("state_snapshot payload is not a dict for live_redirect_mount; ignoring")
            state_snapshot = None

        # Now mount the new view using the standard mount flow. We pass
        # ``sticky_preserved`` so ``handle_mount`` can emit the
        # ``sticky_hold`` frame BEFORE its ``mount`` frame — ordering
        # is load-bearing (see Fix #1 / issue tag). ``handle_mount``
        # mutates ``self._sticky_preserved`` to the final survivor set
        # after the slot scan; if it raises mid-flight we drain any
        # staged children so their background tasks don't leak.
        try:
            await self.handle_mount(
                data,
                sticky_preserved=sticky_preserved,
                state_snapshot=state_snapshot,
            )
        except Exception:
            # Drain any staged stickys so their async tasks / groups
            # clean up. Without this, a render/auth failure on the NEW
            # view would leave preserved sticky instances alive on the
            # consumer with background work still running on a
            # "zombie" instance whose parent is gone.
            for child in list(self._sticky_preserved.values()):
                hook = getattr(child, "_on_sticky_unmount", None)
                if callable(hook):
                    try:
                        hook()
                    except Exception:  # noqa: BLE001 — best-effort cleanup
                        logger.exception(
                            "sticky child _on_sticky_unmount failed during redirect cleanup"
                        )
            self._sticky_preserved = {}
            raise

    def _build_live_redirect_request(self, data: Dict[str, Any]):
        """Reconstruct a minimal Django request for the live_redirect target.

        Used to re-check sticky children's auth against the destination
        URL. Mirrors the request-construction block inside
        :meth:`handle_mount` — session + user come from the WS scope,
        path from the client message. Kept as a distinct helper so the
        sticky staging step can run BEFORE handle_mount destroys the
        old view.

        Returns ``None`` when the destination URL fails to resolve (no
        matching URL pattern) — sticky views whose ``check_permissions``
        relies on ``request.resolver_match.kwargs`` would otherwise
        ``AttributeError`` or silently pass using stale data from the
        old request. The caller treats a ``None`` return as "staging
        impossible, unmount all staged stickys".
        """
        from django.test import RequestFactory
        from django.urls import resolve, Resolver404

        factory = RequestFactory()
        page_url = data.get("url", "/")
        request = factory.get(page_url)
        # Session — same source as handle_mount.
        try:
            from django.contrib.sessions.backends.db import SessionStore
        except Exception:  # noqa: BLE001
            SessionStore = None  # type: ignore[assignment]
        scope_session = self.scope.get("session") if hasattr(self, "scope") else None
        session_key = getattr(scope_session, "session_key", None) if scope_session else None
        if SessionStore is not None:
            request.session = (
                SessionStore(session_key=session_key) if session_key else SessionStore()
            )
        # User — Channels scope user is a LazyObject; assign directly.
        if hasattr(self, "scope") and "user" in self.scope:
            request.user = self.scope["user"]
        # Resolve the destination URL so ``request.resolver_match`` is
        # populated. Sticky views using ``check_permissions(request)``
        # may reference ``request.resolver_match.kwargs`` (e.g.
        # permissions keyed by the PK from the NEW URL). Without this,
        # they'd either ``AttributeError`` or read stale data from the
        # old request.
        try:
            request.resolver_match = resolve(page_url)
        except Resolver404:
            logger.warning(
                "resolve() failed for live_redirect URL %s; sticky auth cannot be re-checked",
                sanitize_for_log(page_url),
            )
            return None
        return request

    async def handle_presence_heartbeat(self, data: Dict[str, Any]):
        """Handle presence heartbeat from client."""
        if not self.view_instance or not hasattr(self.view_instance, "update_presence_heartbeat"):
            return

        try:
            await sync_to_async(self.view_instance.update_presence_heartbeat)()
        except Exception as e:
            logger.error("Error updating presence heartbeat: %s", e)

    async def handle_cursor_move(self, data: Dict[str, Any]):
        """Handle cursor movement for live cursors."""
        if not self.view_instance or not hasattr(self.view_instance, "handle_cursor_move"):
            return

        try:
            x = data.get("x", 0)
            y = data.get("y", 0)
            await sync_to_async(self.view_instance.handle_cursor_move)(x, y)
        except Exception as e:
            logger.error("Error handling cursor move: %s", e)

    async def handle_request_html(self, data: Dict[str, Any]):
        """
        Handle client request for full HTML when VDOM patches fail.

        The client sends {"type": "request_html"} when applyPatches() returns
        false (e.g., due to {% if %} blocks shifting DOM structure). Server
        responds with the last rendered HTML for client-side DOM morphing.
        """
        if not self.view_instance:
            await self.send_error("View not mounted")
            return

        html = getattr(self, "_recovery_html", None)
        version = getattr(self, "_recovery_version", 0)

        if not html:
            await self.send_error(
                "Recovery HTML unavailable — the server may have restarted. "
                "A page reload will fix this.",
                recoverable=False,
            )
            return

        html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(html)
        html_content = await sync_to_async(self.view_instance._extract_liveview_content)(html)

        # Clear recovery state (one-time use)
        self._recovery_html = None

        await self.send_json(
            {
                "type": "html_recovery",
                "html": html_content,
                "version": version,
            }
        )

    # Per-frame size cap for time_travel_event frames. Set to 16 KiB —
    # a quarter of the conventional 64 KiB WebSocket frame limit — so a
    # view with large state (e.g. a 1000-row list) cannot flood the
    # channel on every event. When exceeded, state_before / state_after
    # are replaced with a truncation placeholder and the frame carries
    # ``_truncated: True``. See Stage 11 Fix C.
    _TT_EVENT_SIZE_CAP = 16 * 1024

    async def _maybe_push_tt_event(self, view: Any, snapshot: Any) -> None:
        """Push a ``time_travel_event`` frame to the client after a record.

        Dev-only. Fan out freshly-captured :class:`EventSnapshot` entries
        over the main djust WebSocket so the debug panel's Time Travel
        tab can incrementally populate its history — without re-sending
        the entire buffer on every event.

        No-op when:
            * ``snapshot`` is ``None`` (time-travel disabled on the view
              or a guard returned early)
            * ``DEBUG`` is off (production gate)
            * The send itself fails (best-effort)

        When the serialized entry exceeds :attr:`_TT_EVENT_SIZE_CAP`,
        ``state_before`` / ``state_after`` are replaced with a truncation
        placeholder so spammy handlers on large-state views can't bloat
        the WS channel. The full state is still available server-side
        via the ring buffer for ``time_travel_jump``.
        """
        if snapshot is None:
            return
        from django.conf import settings

        if not getattr(settings, "DEBUG", False):
            return
        buffer = getattr(view, "_time_travel_buffer", None)
        if buffer is None:
            return
        try:
            entry = snapshot.to_dict()
            # Serialized size check — default=str tolerates non-JSON-
            # native types (datetimes, Decimals) the same way the rest
            # of the debug-frame plumbing does.
            serialized = json.dumps(entry, default=str)
            if len(serialized) > self._TT_EVENT_SIZE_CAP:
                state_before = entry.get("state_before") or {}
                state_after = entry.get("state_after") or {}
                entry["state_before"] = {
                    "_truncated": True,
                    "_size": len(json.dumps(state_before, default=str)),
                }
                entry["state_after"] = {
                    "_truncated": True,
                    "_size": len(json.dumps(state_after, default=str)),
                }
                entry["_truncated"] = True
            await self.send_json(
                {
                    "type": "time_travel_event",
                    "entry": entry,
                    "history_len": len(buffer),
                }
            )
        except Exception:  # noqa: BLE001 — dev-only, degrade silently
            logger.exception("time_travel: failed to push event frame")

    async def handle_time_travel_jump(self, data: Dict[str, Any]):
        """Jump the view to a past :class:`EventSnapshot`.

        Dev-only. The debug panel's Time Travel tab emits
        ``{"type": "time_travel_jump", "index": N, "which": "before"|"after"}``;
        the server restores the captured state onto the view and
        re-renders via the normal patch pipeline. A ``time_travel_state``
        frame is sent back so the client can update its cursor.

        Rejected in production (``DEBUG=False``) and when the view
        hasn't opted in via ``time_travel_enabled``.
        """
        from django.conf import settings

        if not getattr(settings, "DEBUG", False):
            await self.send_error("time_travel requires DEBUG=True")
            return
        if not self.view_instance:
            await self.send_error("View not mounted")
            return
        buffer = getattr(self.view_instance, "_time_travel_buffer", None)
        if buffer is None:
            await self.send_error("time_travel not enabled on this view")
            return

        index = data.get("index")
        which = data.get("which", "before")
        if not isinstance(index, int):
            await self.send_error("time_travel_jump: index must be int")
            return
        if which not in ("before", "after"):
            await self.send_error("time_travel_jump: which must be 'before' or 'after'")
            return

        snapshot = buffer.jump(index)
        if snapshot is None:
            await self.send_error("time_travel_jump: no snapshot at index %d" % index)
            return

        from djust.time_travel import restore_snapshot

        ok = await sync_to_async(restore_snapshot)(self.view_instance, snapshot, which)
        if not ok:
            await self.send_error("time_travel_jump: restore failed")
            return

        # Re-render via the existing patch pipeline so the client sees
        # the restored state without a full mount. Use render_with_diff
        # directly (mirrors the hotreload / broadcast paths).
        try:
            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()
            patch_list = None
            if patches is not None:
                patch_list = fast_json_loads(patches) if patches else []
            await self._send_update(
                patches=patch_list,
                html=html,
                version=version,
                event_name="__time_travel_jump__",
            )
        except Exception as exc:  # noqa: BLE001 — dev-only, log + report
            logger.exception("time_travel_jump: re-render failed")
            await self.send_error("time_travel_jump: re-render failed: %s" % exc)
            return

        await self.send_json(
            {
                "type": "time_travel_state",
                "cursor": index,
                "which": which,
                "history_len": len(buffer),
            }
        )

    async def presence_event(self, event):
        """
        Handle presence-related events from the channel layer.

        These events are broadcasted to all users in a presence group.
        """
        await self.send_json(
            {
                "type": "presence_event",
                "event": event.get("event", ""),
                "payload": event.get("payload", {}),
            }
        )

    async def server_push(self, event):
        """
        Handle a server-push message from the channel layer.

        Called when external code (Celery tasks, management commands, etc.)
        sends an update via push_to_view().

        Event sequencing: acquires _render_lock to serialize with tick and
        event handlers. Yields to user events — if a user event is being
        processed, the broadcast is skipped to avoid version interleaving.
        Tags updates with source="broadcast" so the client can buffer them.

        Args:
            event: Channel layer event with optional 'state', 'handler', 'payload'
        """
        if not self.view_instance:
            return

        try:
            # Yield to user events: if a user event is being processed,
            # skip this broadcast to avoid version interleaving (#560).
            if self._processing_user_event:
                logger.debug(
                    "[djust] server_push on %s skipped — user event in progress",
                    self.view_instance.__class__.__name__,
                )
                return

            # Acquire render lock with timeout to serialize with tick/event
            # renders. Use same 0.1s timeout as tick loop.
            try:
                await asyncio.wait_for(self._render_lock.acquire(), timeout=0.1)
            except asyncio.TimeoutError:
                logger.debug(
                    "[djust] server_push on %s skipped — render lock held",
                    self.view_instance.__class__.__name__,
                )
                return

            try:
                # Apply state updates before handler call so the handler can read
                # the new values. _sync_state_to_rust runs after both to push the
                # final Python state to Rust for rendering.
                state = event.get("state")
                if state and isinstance(state, dict):
                    for key, value in state.items():
                        setattr(self.view_instance, key, value)

                # Call handler if specified — restricted to handle_* prefixed or
                # @event_handler-decorated methods to prevent arbitrary method calls
                # if an attacker gains access to the channel layer backend.
                handler_name = event.get("handler")
                if handler_name:
                    handler_fn = getattr(self.view_instance, handler_name, None)
                    if handler_fn and callable(handler_fn):
                        from .decorators import is_event_handler

                        if not (handler_name.startswith("handle_") or is_event_handler(handler_fn)):
                            logger.warning(
                                "server_push: blocked handler %r"
                                " — must be handle_* or @event_handler",
                                handler_name,
                            )
                        else:
                            payload = event.get("payload") or {}
                            await sync_to_async(handler_fn)(**payload)

                # Views can set _skip_render = True in a handler to
                # suppress the re-render cycle (e.g. sender ignoring its own broadcast).
                if getattr(self.view_instance, "_skip_render", False):
                    self.view_instance._skip_render = False
                    await self._flush_push_events()
                    await self._flush_flash()
                    await self._flush_page_metadata()
                    await self._flush_pending_layout()
                    await self._send_noop()
                    return

                # Sync state and re-render
                # TODO: add patch compression (PATCH_COUNT_THRESHOLD) matching handle_event
                if hasattr(self.view_instance, "_sync_state_to_rust"):
                    await sync_to_async(self.view_instance._sync_state_to_rust)()

                html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

                if patches is not None:
                    if isinstance(patches, str):
                        patches = fast_json_loads(patches)
                    await self._send_update(
                        patches=patches,
                        version=version,
                        broadcast=True,
                        source="broadcast",
                    )
                else:
                    # Even if no patches, flush any push_events and flash messages
                    await self._flush_push_events()
                    await self._flush_flash()
                    await self._flush_page_metadata()
                    await self._flush_pending_layout()
            finally:
                self._render_lock.release()

        except Exception as e:
            logger.exception("Error in server_push: %s", e)

    async def client_push_event(self, event):
        """
        Handle a direct push_event from the channel layer (via push_event_to_view).

        Sends the event directly to the client without re-rendering.
        """
        await self.send_json(
            {
                "type": "push_event",
                "event": event.get("event", ""),
                "payload": event.get("payload", {}),
            }
        )

    async def db_notify(self, event):
        """Handle a PostgreSQL NOTIFY forwarded by ``PostgresNotifyListener``.

        The listener calls ``group_send("djust_db_notify_<channel>", ...)``
        when a NOTIFY arrives on the wire. Every consumer whose view
        subscribed via ``self.listen(<channel>)`` receives this event.

        Flow:
          1. Dispatch a ``handle_info({"type": "db_notify", ...})`` call on
             the view (runs under the render lock to serialize with
             ticks and user events).
          2. Re-sync state to Rust and emit VDOM patches via the same
             ``source="broadcast"`` path as ``server_push``.

        **Best-effort under contention (#813).** The render lock is acquired
        with a 100ms timeout; if a user event or earlier notification is
        still holding the lock, this db_notify is **silently dropped**
        (debug-logged). The dropped notification does NOT queue — under
        bursty notification streams, some re-renders will be skipped. If
        strict "every notify causes a render" semantics are required,
        de-dupe by primary key in your ``handle_info`` and use
        ``self.server_push`` / ``self.live_patch`` from a handler that
        owns the lock. The tradeoff here is deliberate: NOTIFY messages
        carry no delivery guarantee anyway (Postgres drops them on
        connection failure), and silently dropping contended renders is
        preferable to deadlock or unbounded queue growth.
        """
        if not self.view_instance:
            return

        channel = event.get("channel", "")
        payload = event.get("payload", {})
        message = {"type": "db_notify", "channel": channel, "payload": payload}

        try:
            # Yield to user events: version interleaving is the same risk
            # as server_push (#560).
            if self._processing_user_event:
                logger.debug(
                    "[djust] db_notify on %s skipped — user event in progress",
                    self.view_instance.__class__.__name__,
                )
                return

            try:
                await asyncio.wait_for(self._render_lock.acquire(), timeout=0.1)
            except asyncio.TimeoutError:
                logger.debug(
                    "[djust] db_notify on %s skipped — render lock held",
                    self.view_instance.__class__.__name__,
                )
                return

            try:
                handler = getattr(self.view_instance, "handle_info", None)
                if handler and callable(handler):
                    try:
                        await sync_to_async(handler)(message)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "db_notify: handle_info raised on %s: %s",
                            self.view_instance.__class__.__name__,
                            exc,
                        )
                        return

                if getattr(self.view_instance, "_skip_render", False):
                    self.view_instance._skip_render = False
                    await self._flush_push_events()
                    await self._flush_flash()
                    await self._flush_page_metadata()
                    await self._flush_pending_layout()
                    await self._send_noop()
                    return

                if hasattr(self.view_instance, "_sync_state_to_rust"):
                    await sync_to_async(self.view_instance._sync_state_to_rust)()

                html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

                if patches is not None:
                    if isinstance(patches, str):
                        patches = fast_json_loads(patches)
                    await self._send_update(
                        patches=patches,
                        version=version,
                        broadcast=True,
                        source="broadcast",
                    )
                else:
                    await self._flush_push_events()
                    await self._flush_flash()
                    await self._flush_page_metadata()
                    await self._flush_pending_layout()

                # v0.7.0 — If handle_info flipped an activity to visible,
                # drain its queue in the same round-trip. The flush is
                # async and awaited inline. Safe no-op when no deferred
                # events exist.
                if hasattr(self.view_instance, "_flush_deferred_activity_events"):
                    try:
                        await self.view_instance._flush_deferred_activity_events(self)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "dj_activity: deferred-event flush raised (db_notify path)"
                        )
            finally:
                self._render_lock.release()
        except Exception as e:  # noqa: BLE001
            logger.exception("Error in db_notify: %s", e)

    async def _run_tick(self, interval_ms):
        """
        Periodic tick loop. Calls handle_tick() on the view instance every
        interval_ms milliseconds, then re-renders and sends patches.

        Event sequencing (#560):
        - Skips render when handle_tick() doesn't change any public assigns
        - Acquires _render_lock to serialize with event handlers
        - Yields to user events: if a user event is being processed, the
          tick is deferred to the next interval instead of blocking
        - Tags tick updates with source="tick" so the client can buffer
          them during pending user event round-trips
        """
        interval_s = interval_ms / 1000.0
        try:
            while True:
                await asyncio.sleep(interval_s)
                if not self.view_instance:
                    break
                try:
                    # User events take priority over ticks (#560). If a user
                    # event is currently being processed, skip this tick
                    # entirely — the next tick interval will pick up any
                    # changes. This prevents version interleaving.
                    if self._processing_user_event:
                        logger.debug(
                            "[djust] Tick on %s deferred — user event in progress",
                            self.view_instance.__class__.__name__,
                        )
                        continue

                    # Acquire render lock to serialize with event handlers.
                    # Use a short timeout so ticks don't block indefinitely
                    # if an event handler is slow.
                    try:
                        await asyncio.wait_for(self._render_lock.acquire(), timeout=0.1)
                    except asyncio.TimeoutError:
                        logger.debug(
                            "[djust] Tick on %s skipped — render lock held",
                            self.view_instance.__class__.__name__,
                        )
                        continue

                    try:
                        # Snapshot state before tick to detect changes
                        pre_assigns = _snapshot_assigns(self.view_instance)

                        await sync_to_async(self.view_instance.handle_tick)()

                        # Skip render if tick handler didn't change any state.
                        post_assigns = _snapshot_assigns(self.view_instance)
                        if pre_assigns == post_assigns:
                            logger.debug(
                                "[djust] Tick on %s produced no state changes, skipping render",
                                self.view_instance.__class__.__name__,
                            )
                            continue

                        if hasattr(self.view_instance, "_sync_state_to_rust"):
                            await sync_to_async(self.view_instance._sync_state_to_rust)()

                        html, patches, version = await sync_to_async(
                            self.view_instance.render_with_diff
                        )()

                        if patches is not None:
                            if isinstance(patches, str):
                                patches = fast_json_loads(patches)
                            await self._send_update(
                                patches=patches,
                                version=version,
                                event_name="tick",
                                source="tick",
                            )
                    finally:
                        self._render_lock.release()
                except Exception as e:
                    logger.exception("Error in tick handler: %s", e)
        except asyncio.CancelledError:
            pass  # Normal shutdown path when tick loop is cancelled

    @classmethod
    async def broadcast_reload(cls, file_path: str):
        """
        Broadcast a reload message to all connected clients.

        This is called by the hot reload file watcher when files change.

        Args:
            file_path: Path of the file that changed
        """
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer:
            await channel_layer.group_send(
                "djust_hotreload",
                {
                    "type": "hotreload",
                    "file": file_path,
                },
            )


class LiveViewRouter:
    """
    Router for LiveView WebSocket connections.

    Maps URL patterns to LiveView classes.
    """

    _routes: Dict[str, type] = {}

    @classmethod
    def register(cls, path: str, view_class: type):
        """Register a LiveView route"""
        cls._routes[path] = view_class

    @classmethod
    def get_view(cls, path: str) -> Optional[type]:
        """Get the view class for a path"""
        return cls._routes.get(path)
