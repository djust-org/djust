"""
WebSocket consumer for LiveView real-time updates
"""

import asyncio
import inspect
import json
import logging
import msgpack
from typing import Any, Awaitable, Callable, ContextManager, Dict, List, Optional
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
from .signals import full_html_update, liveview_server_error

logger = logging.getLogger(__name__)
hotreload_logger = logging.getLogger("djust.hotreload")


def _tenant_context(tenant: Any) -> ContextManager[Any]:
    """Bind *tenant* as the current tenant for a live dispatch (Finding #6).

    Lazily imports ``djust.tenants.middleware.tenant_context`` so the WS path
    establishes the tenant ContextVar around mount + every event/url dispatch.
    ``TenantMiddleware`` only runs on the HTTP path, so without this the
    tenant-scoped managers see ``None`` on the live path and (fail-closed)
    return empty querysets — or, pre-fix, disclosed every tenant's rows.

    Falls back to a no-op context if the tenants module is unavailable, so the
    consumer keeps working for non-tenant deployments.
    """
    try:
        from .tenants.middleware import tenant_context

        return tenant_context(tenant)
    except Exception:  # noqa: BLE001 — tenants is optional; never break the live path
        from contextlib import nullcontext

        return nullcontext()


def _bind_tenant(tenant: Any) -> None:
    """Set the current tenant ContextVar for the live path (Finding #6).

    Used on the mount path: after the view resolves its tenant via
    ``_ensure_tenant``, bind it so ``mount()`` and the initial render — which
    run later in the same consumer task — see the correct tenant in the
    tenant-scoped managers. Cleared in :meth:`disconnect`. No-op when tenants
    is unavailable.
    """
    try:
        from .tenants.middleware import set_current_tenant

        set_current_tenant(tenant)
    except Exception:  # noqa: BLE001 — tenants is optional; never break the live path
        pass


__all__ = [
    "LiveViewConsumer",
    "_check_event_security",
    "_ensure_handler_rate_limit",
]

# Optional PyO3 actor surface (typed by _rust.pyi). When the compiled extension
# lacks the actor build, both names fall back to None — annotate as Optional so
# the import and the None fallback are type-compatible. (The runtime variable
# shadows the _rust class name, so the annotation uses the structural
# Callable/type rather than a self-referential forward ref.)
create_session_actor: Optional[Callable[[str], Awaitable[Any]]]
SessionActorHandle: Optional[type]
try:
    from ._rust import create_session_actor, SessionActorHandle  # noqa: F811
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

    return _host_in_allowed_hosts(host)


def _host_in_allowed_hosts(host: str) -> bool:
    """Validate a bare hostname against ``settings.ALLOWED_HOSTS``.

    This is the single ALLOWED_HOSTS check shared by the CSWSH Origin gate
    (:func:`_is_allowed_origin`) and the WS/runtime reconstructed-request Host
    propagation (:func:`validated_host_from_scope`), so the two cannot drift
    (#1646). The policy mirrors Django's HTTP layer exactly:

      * Re-add brackets around IPv6 literals (Django stores them with brackets).
      * Empty ALLOWED_HOSTS -> localhost variants in DEBUG, REJECT in prod.
      * Otherwise defer to ``django.http.request.validate_host`` so wildcard
        (".example.com", "*") semantics match HTTP verbatim.

    ``host`` must already be stripped of scheme/port/path/userinfo (e.g. the
    output of ``urlparse(...).hostname`` or a Host header split on ":").
    """
    if not host:
        return False

    # urlparse strips the brackets from IPv6 literals, but Django's
    # ALLOWED_HOSTS / get_host() stores IPv6 addresses WITH brackets
    # (e.g. "[::1]"). Re-add them so validate_host() matches correctly.
    if ":" in host and not host.startswith("["):
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

    return bool(validate_host(match_host, allowed_hosts))


def validated_host_from_scope(
    scope: Optional[Dict[str, Any]],
) -> "tuple[Optional[str], bool]":
    """Extract the validated client Host (and secure flag) from an ASGI scope.

    Finding #26 (WS/runtime reconstructed-request host omission): the WebSocket
    ``handle_mount`` and ``ViewRuntime._build_request`` rebuild an ``HttpRequest``
    via ``RequestFactory().get(...)`` with NO ``HTTP_HOST``, so
    ``request.get_host()`` defaults to ``RequestFactory``'s ``"testserver"`` on
    the live path. Host/subdomain ``TenantResolver``\\ s then misresolve the
    tenant (None) — cross-tenant disclosure with ``STRICT_MODE=False`` or broken
    tenancy with the default. The HTTP (SSR) path uses the real request and is
    unaffected; this restores parity for the live path.

    Returns ``(host, is_secure)`` where:

      * ``host`` is the validated bare Host header value (no port stripped — a
        ``host:port`` value is passed through to ``HTTP_HOST`` so Django's
        ``get_host()`` handles it the same way it does for a real request), or
        ``None`` if the scope has no Host header OR the Host fails
        ALLOWED_HOSTS validation. ``None`` means "fall back to the current
        ``RequestFactory`` default" — so non-browser clients (curl, the Python
        ``WebsocketCommunicator``) that send no Host, and spoofed Hosts outside
        ALLOWED_HOSTS, do not break and do not gain tenant-resolution authority
        beyond what the HTTP layer grants.
      * ``is_secure`` is True when the handshake was over TLS (``scope["scheme"]``
        is ``"wss"`` / ``"https"``), so a propagated request reports
        ``request.is_secure()`` correctly too.

    Validation reuses :func:`_host_in_allowed_hosts` — the SAME ALLOWED_HOSTS
    logic the CSWSH Origin check uses — so the WS host bound here is no weaker
    and no stronger than the HTTP layer. A browser victim cannot spoof the
    handshake Host (the browser sets it); a non-browser client is bounded by
    ALLOWED_HOSTS exactly as the HTTP request would be.
    """
    if not scope:
        return None, False

    headers = dict(scope.get("headers", []) or [])
    raw_host = headers.get(b"host")
    host: Optional[str] = None
    if raw_host:
        try:
            host_str = raw_host.decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            host_str = ""
        # Extract the bare domain with Django's own ``split_domain_port`` — the
        # exact parser ``HttpRequest.get_host()`` runs — so this boundary rejects
        # everything ``get_host()`` would reject (userinfo ``user@host``,
        # leading/trailing whitespace, control chars, bad characters):
        # ``split_domain_port`` returns ``("", "")`` for any host its strict
        # ``host_validation_re`` doesn't match. We must parse-then-validate
        # because ``validate_host`` alone does NOT format-validate — e.g.
        # ``"evil.com@acme.example.com"`` ``endswith(".example.com")`` and would
        # wrongly match a wildcard ALLOWED_HOSTS entry. The FULL header (incl.
        # port) is passed through to HTTP_HOST when valid so ``get_host()``
        # behaves identically to a real request (#F26 review hardening).
        if host_str:
            from django.http.request import split_domain_port

            domain, _port = split_domain_port(host_str)
            if domain and _host_in_allowed_hosts(domain):
                host = host_str

    scheme = (scope.get("scheme") or "").lower()
    is_secure = scheme in ("wss", "https")
    return host, is_secure


# F23 (#1819 traversal fix) is now implemented once in
# ``djust.security.mount.validate_mount_url`` so the WebSocket, SSE, and
# ``ViewRuntime`` mount paths share a single validator and cannot drift
# (#1646). ``_validate_mount_url`` is kept as a module-level alias because
# existing tests and call sites reference it by this name.
from .security.mount import validate_mount_url as _validate_mount_url  # noqa: E402


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


def _snapshot_assigns(view_instance: Any) -> Dict[str, Any]:
    """Fast identity+hash snapshot of public assigns for change detection.

    Uses id() for all values plus a shallow fingerprint for mutable
    containers (list length, dict length+keys, set length) to detect
    common in-place mutations without the cost of copy.deepcopy().

    This is ~100x faster than deep copy for views with many attributes.
    Trade-off: deep nested in-place mutations (e.g., items[0]['name'] = 'x')
    are NOT detected. For those, call ``self.set_changed_keys('items')`` after
    the mutation to force a re-render, or assign a new value built immutably
    (which djust diffs efficiently). Setting ``self._changed_keys`` directly does
    NOT help: it is excluded from this snapshot (``_FRAMEWORK_INTERNAL_ATTRS``),
    so the pre/post skip still fires — the render-forcing mechanism is the
    ``_force_full_html`` flag that ``set_changed_keys()`` sets.
    """
    # #762: Filter framework-internal attrs so change detection doesn't fire
    # on attrs like ``template_name`` / ``http_method_names`` that the user
    # never touches.
    from .live_view import _FRAMEWORK_INTERNAL_ATTRS

    _static_skip = set(getattr(view_instance, "static_assigns", []))
    _fw_attrs: frozenset[str] = getattr(view_instance, "_framework_attrs", frozenset())
    snapshot: Dict[str, Any] = {}
    for k, v in view_instance.__dict__.items():
        if k in _fw_attrs or k in _static_skip or k in _FRAMEWORK_INTERNAL_ATTRS:
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
                if v and len(v) >= 100:
                    from .utils import emit_one_shot_class_warning

                    _cls = type(view_instance)
                    emit_one_shot_class_warning(
                        _cls,
                        "snapshot_list_truncated",
                        "[djust] %s: list '%s' has %d items — content "
                        "fingerprint truncated. In-place mutations inside "
                        "list elements will NOT be detected by auto-diff. "
                        "Use self.set_changed_keys({'%s'}) or assign a "
                        "new list reference.",
                        _cls.__qualname__,
                        k,
                        len(v),
                        k,
                    )
        elif isinstance(v, dict):
            snapshot[k] = (vid, len(v), tuple(v.keys()) if len(v) < 50 else len(v))
            if len(v) >= 50:
                from .utils import emit_one_shot_class_warning

                _cls = type(view_instance)
                emit_one_shot_class_warning(
                    _cls,
                    "snapshot_dict_truncated",
                    "[djust] %s: dict '%s' has %d keys — key fingerprint "
                    "truncated. Key additions/removals will NOT be detected "
                    "by auto-diff. Use self.set_changed_keys({'%s'}) or "
                    "assign a new dict reference.",
                    _cls.__qualname__,
                    k,
                    len(v),
                    k,
                )
        elif isinstance(v, set):
            snapshot[k] = (vid, len(v))
        elif isinstance(v, _IMMUTABLE_TYPES):
            snapshot[k] = v
        else:
            # For other objects, just use id — reassignment is detected,
            # in-place mutation is not (same as Phoenix LiveView).
            snapshot[k] = vid
    return snapshot


def _compute_changed_keys(pre: Dict[str, Any], post: Dict[str, Any]) -> set[str]:
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


def _build_context_snapshot(context: Dict[str, Any], max_value_len: int = 100) -> Dict[str, Any]:
    """Build a JSON-safe snapshot of template context for diagnostics.

    Truncates long values, converts non-serializable types to repr strings,
    and limits to 20 keys to keep the payload small.
    """
    snapshot: Dict[str, Any] = {}
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


def _emit_liveview_server_error(view_instance: Any, error: str, context: Dict[str, Any]) -> None:
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
    view_instance: Any,
    reason: str,
    event_name: Optional[str],
    html: Optional[str],
    version: int,
    patch_count: Optional[int] = None,
    context_snapshot: Optional[Dict[str, Any]] = None,
    html_snippet: Optional[str] = None,
    previous_html_snippet: Optional[str] = None,
) -> None:
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


def render_embedded_child_html(child_view: Any) -> str:
    """Render an embedded child view's template and return its inner HTML.

    Transport-agnostic render core for the embedded-child subsystem. Re-renders
    just the child's template via Django's template engine, bypassing the
    parent's VDOM entirely. Single-sourced (ADR-022 Iter 2 Phase 2.1, the #1646
    cure) so the WS consumer (:meth:`LiveViewConsumer._render_embedded_child`)
    and :class:`~djust.runtime.ViewRuntime` share ONE implementation — including
    the security-hardened error path below — with no parallel copy to drift.
    """
    try:
        context = child_view.get_context_data()
        from django.template import engines

        template_str = child_view.get_template()
        engine = engines["django"] if "django" in engines else list(engines.all())[0]
        tmpl = engine.from_string(template_str)
        html = tmpl.render(context)
        # Record the child's dj-model auto-allowlist from ITS own TEMPLATE
        # SOURCE — child update_model events gate against the child's
        # _dj_model_fields, and this is the child's only render path (it
        # bypasses render_with_diff). Derived from the Rust template AST
        # (Text-node literals), immune to rendered-output poisoning
        # (#3 review #1646).
        if hasattr(child_view, "_record_dj_model_fields_from_source"):
            from .utils import get_template_dirs

            child_view._record_dj_model_fields_from_source(template_str, get_template_dirs())
        return str(html)
    except Exception as e:
        logger.error("Failed to render embedded child %s: %s", child_view.__class__.__name__, e)
        # SECURITY (#1646 parallel-path drift): this site bypassed the
        # central handle_exception / create_safe_error_response path, which
        # is DEBUG-gated and generic in production. Returning the raw str(e)
        # here (a) leaked exception detail into the live page in production
        # (CWE-209) and (b) was unescaped, so an attacker-influenced message
        # containing ``-->`` broke out of the HTML comment into live DOM
        # (CWE-79 DOM XSS). escape() neutralises the comment-breakout and any
        # tag injection in BOTH modes; production additionally emits no
        # detail. Mirrors the DEBUG gate in simple_live_view.render_template.
        from django.conf import settings
        from django.utils.html import escape

        if getattr(settings, "DEBUG", False):
            return f"<!-- Error rendering embedded child: {escape(str(e))} -->"
        return "<!-- Error rendering embedded child -->"


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
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.ids: set[str] = set()

        def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
            for name, value in attrs:
                if name == "dj-sticky-slot" and value:
                    self.ids.add(value)

        def handle_startendtag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.view_instance: Optional[Any] = None
        # SessionActorHandle is an optional PyO3 type bound at module load (None
        # when the actor build is absent); annotate the handle as Any since the
        # name is a runtime variable, not usable as a static type.
        self.actor_handle: Any = None
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
        # Consumer-owned monotonic VDOM wire version (#1788). This is the
        # SINGLE SOURCE OF TRUTH for the ``version`` field on every
        # client-checked outbound frame (patch / html_update / mount /
        # html_recovery). It is decoupled from the Rust view's internal
        # ``self.version`` (which resets to 0 on baseline loss — e.g. a
        # patch-compression ``_rust_view.reset()``). Stamping the consumer
        # counter keeps the wire sequence strictly monotonic per-CONNECTION,
        # so a post-baseline-loss ``html_update`` still satisfies the client's
        # ``clientVdomVersion === data.version - 1`` check
        # (``static/djust/src/02-response-handler.js:58``) and the client
        # accepts it directly without a ``request_html`` recovery round-trip.
        # SEPARATE from ``_hvr_version`` above (telemetry for ``hvr-applied``).
        self._last_sent_version: int = 0
        # Sticky LiveViews (Phase B): per-connection stash of preserved
        # sticky children staged in handle_live_redirect_mount BEFORE the
        # old view is torn down. Re-registered on the new parent after
        # its mount completes.
        self._sticky_preserved: Dict[str, Any] = {}
        # Sticky auto-detect (ADR-014): IDs that ``{% live_render sticky=True %}``
        # already re-registered onto the new parent during template render.
        # The post-render slot-scan reads this set and skips the second
        # ``_register_child`` call (which would ``ValueError``) while still
        # including the ID in the ``sticky_hold`` survivor list. Reset at
        # every ``handle_mount`` and ``handle_live_redirect_mount`` entry.
        self._sticky_auto_reattached: set[str] = set()
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

    async def _flush_deferred(self) -> None:
        """Drain and execute callbacks queued via :meth:`LiveView.defer`.

        Wire-in pattern: this method is called from two locations —
        (a) inside :meth:`_send_update` itself (alongside the other
        ``_flush_*`` methods), and (b) at every per-handler post-render
        site that already calls ``_flush_pending_layout`` (10 sites).
        The (b) sites are technically redundant when (a) preceded — the
        drain is idempotent on an empty queue — but they preserve
        symmetry with the existing ``_flush_*`` family which has the same
        redundancy. Removing only ``_flush_deferred``'s (b) wiring would
        create asymmetry that future contributors would re-introduce.
        See post-merge follow-up Action #163 for a milestone-level
        cleanup that drops all redundant ``_flush_*`` calls together.

        Runs **after** every other post-render flush (push events, flash,
        page metadata, layout) so deferred callbacks observe the
        post-patch state. Phoenix-style ``send(self(), :foo)`` semantics —
        useful for telemetry, post-render cleanup, or follow-up side
        effects that should fire after the user sees the change.

        Each callback is invoked in a try/except. Sync callbacks run
        directly; async callbacks (``async def`` or coroutine-returning)
        are awaited inline. A failing deferred callback logs at WARN with
        full traceback and continues to the next — a deferred callback's
        failure must not break the WebSocket connection or the user's
        interactive flow.

        Does NOT trigger a re-render after callbacks complete; if a
        callback needs to re-render, the caller should use
        :meth:`AsyncWorkMixin.start_async` instead.
        """
        if not self.view_instance:
            return
        if not hasattr(self.view_instance, "_drain_deferred"):
            return
        callbacks = self.view_instance._drain_deferred()
        # Defensive: same shape as ``_flush_flash`` — guard against test
        # mocks (a ``Mock`` ``view_instance`` returns a ``Mock``, not a
        # list) and any legacy view that overrode ``_drain_deferred`` to
        # return non-list.
        if not isinstance(callbacks, list) or not callbacks:
            return
        for callback, args, kwargs in callbacks:
            try:
                result = callback(*args, **kwargs)
                # Async callbacks: await inline. Mirrors the inspect-based
                # detection used elsewhere (e.g. async event handlers).
                if inspect.iscoroutine(result):
                    await result
            except Exception:
                logger.warning(
                    "[djust] Deferred callback %s on %s raised; continuing to next",
                    getattr(callback, "__qualname__", repr(callback)),
                    self.view_instance.__class__.__name__,
                    exc_info=True,
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

    async def _flush_all_pending(self) -> None:
        """Flush every queued client side-effect at the end of a WS turn, in
        canonical order. Single source of truth: every turn-end path (event,
        skip-render noop, broadcast, db-notify, async completion) calls this so
        no path can silently drop a queued command. Each ``_flush_*`` drains and
        clears its own queue, so calling this twice in one turn is a harmless
        no-op. Regression context: skip-render and broadcast paths used to flush
        only push_events/flash/page_metadata/pending_layout/deferred and dropped
        queued navigation/accessibility/i18n — so ``live_redirect()`` from a
        state-unchanging handler never reached the client (#1643)."""
        await self._flush_push_events()
        await self._flush_flash()
        await self._flush_page_metadata()
        await self._flush_pending_layout()
        await self._flush_deferred()
        await self._flush_navigation()
        await self._flush_accessibility()
        await self._flush_i18n()

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

    async def send_error(self, error: str, **context: Any) -> None:
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
        self,
        task_name: str,
        callback: Callable[..., Any],
        args: Any,
        kwargs: Any,
        event_name: Optional[str] = None,
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
        # Bind the mounted view to a non-None local. Background work runs after
        # mount; if the view was torn down (view_instance nulled) there is nothing
        # to re-render — return early (behavior-equivalent to the existing
        # hasattr(None, ...) == False short-circuits, and the unguarded
        # render_with_diff below would otherwise require a non-None view).
        view = self.view_instance
        if view is None:
            return

        # Check if task was cancelled before starting
        if hasattr(view, "_async_cancelled"):
            if task_name in view._async_cancelled:
                view._async_cancelled.discard(task_name)
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

            # Teardown identity-guard (#1940, #245/#1198 commit-or-rollback /
            # identity-guard class). The callback above is the FIRST await in
            # this detached ``ensure_future`` task; during that await window
            # ``disconnect`` (-> ``view_instance = None``) and
            # ``handle_live_redirect_mount`` / a re-mount (-> ``view_instance``
            # reassigned to a NEW view) can run. ``view`` was captured BEFORE the
            # await (line ~1122), so once control returns here the mount this
            # task was rendering for may be gone or replaced. Re-validate that the
            # consumer's LIVE view is still the captured one before any state
            # mutation (``handle_async_result``) or render-send. If it changed,
            # drop the completed work's re-render — every alternative is wrong:
            # origin/main re-read ``self.view_instance`` LIVE (AttributeError on
            # disconnect, or NEW-view contamination on re-mount); capturing and
            # blindly writing the OLD view contaminates a torn-down/replaced view
            # (#1939). The only correct action post-teardown is to stop. Cheap
            # identity check; no new consumer state. NOTE: cancellation mid-thread
            # cannot stop the worker (``sync_to_async`` runs in a thread pool), so
            # an identity-guard here — not task cancellation — is the right cure.
            if self.view_instance is not view:
                logger.debug(
                    "Async task %s completed after view teardown/re-mount; "
                    "dropping stale re-render",
                    task_name,
                )
                return

            # Check if task was cancelled during execution
            if hasattr(view, "_async_cancelled"):
                if task_name in view._async_cancelled:
                    view._async_cancelled.discard(task_name)
                    logger.debug("Async task %s was cancelled, skipping re-render", task_name)
                    return

            # Call handle_async_result if defined (success path)
            if hasattr(view, "handle_async_result"):
                await sync_to_async(view.handle_async_result)(task_name, result=result, error=None)

            # Re-render and send patches (mirrors the server_push path)
            if hasattr(view, "_sync_state_to_rust"):
                await sync_to_async(view._sync_state_to_rust)()

            html, patches, version = await sync_to_async(view.render_with_diff)()

            if patches is not None:
                patch_list = fast_json_loads(patches) if patches else []
                # Refresh the recovery baseline so a later request_html (e.g.
                # an async-triggered patch that fails on the client) has fresh
                # HTML to serve. Mirrors handle_event and server_push (#1202).
                # Without this, an html_recovery that already consumed
                # _recovery_html leaves it None, the next request_html returns
                # "Recovery HTML unavailable", and the client freezes at the
                # transitional state even though the backend advanced (#1636).
                # Stamp the consumer-owned wire version (#1788), discarding the
                # Rust ``version`` for the wire, AND arm recovery in one step so
                # _recovery_version == this frame's version (#1817).
                version = self._next_version_armed(html)
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
                        view._strip_comments_and_whitespace(h),
                        view._extract_liveview_content(view._strip_comments_and_whitespace(h)),
                    )
                )(html)
                # The fallback sends the full render to the client, so the
                # recovery baseline must track it too (#1636). Consumer-owned
                # wire version + recovery arm in one step (#1788, #1817).
                version = self._next_version_armed(html)
                await self._send_update(
                    html=html_content,
                    version=version,
                    event_name=event_name,
                    source="async",
                )

            await self._flush_all_pending()

        except Exception as e:
            error = e
            logger.exception(
                "[djust] Error in start_async callback '%s' on %s",
                task_name,
                view.__class__.__name__ if view else "?",
            )

            # Teardown identity-guard on the ERROR path too (#1940). A callback
            # that RAISES jumps straight here, skipping the success-path guard
            # above — but the same await-window teardown applies: the callback's
            # own await(s) (or the ``sync_to_async`` dispatch) can interleave a
            # disconnect / re-mount. The error-state ``handle_async_result`` +
            # re-render below must not write against a torn-down / replaced view.
            if self.view_instance is not view:
                logger.debug(
                    "Async task %s errored after view teardown/re-mount; "
                    "dropping stale error re-render",
                    task_name,
                )
                return

            # Call handle_async_result if defined (error path)
            if hasattr(view, "handle_async_result"):
                try:
                    await sync_to_async(view.handle_async_result)(
                        task_name, result=None, error=error
                    )

                    # Re-render to show error state
                    if hasattr(view, "_sync_state_to_rust"):
                        await sync_to_async(view._sync_state_to_rust)()

                    html, patches, version = await sync_to_async(view.render_with_diff)()

                    if patches is not None:
                        patch_list = fast_json_loads(patches) if patches else []
                        # Render-send: arm recovery so _recovery_version tracks
                        # this error re-render's version (#1817). ``html`` is the
                        # pre-strip render from render_with_diff() above.
                        await self._send_update(
                            patches=patch_list,
                            version=self._next_version_armed(html),
                            event_name=event_name,
                            source="async",
                        )
                    else:
                        html_stripped, html_content = await sync_to_async(
                            lambda h: (
                                view._strip_comments_and_whitespace(h),
                                view._extract_liveview_content(
                                    view._strip_comments_and_whitespace(h)
                                ),
                            )
                        )(html)
                        await self._send_update(
                            html=html_content,
                            version=self._next_version_armed(html),
                            event_name=event_name,
                            source="async",
                        )

                except Exception:
                    logger.exception(
                        "[djust] Error in handle_async_result for task '%s'", task_name
                    )

    def _next_version(self) -> int:
        """Single source of truth for the outbound VDOM wire version (#1788).

        Monotonic per-CONNECTION; decoupled from the Rust view's ``self.version``
        (which resets to 0 on baseline loss — e.g. a patch-compression
        ``_rust_view.reset()``). Every client-checked frame stamps THIS so the
        wire sequence stays strictly monotonic across a Rust baseline reset, and
        a post-baseline-loss ``html_update`` still satisfies the client's
        ``clientVdomVersion === data.version - 1`` check — no ``request_html``
        recovery round-trip.

        Uses ``getattr`` for the read so consumers built via a partial
        constructor (test fakes that override ``__init__``, or any edge path
        that bypasses the base ``__init__``) still get a valid monotonic
        sequence starting at 1.
        """
        self._last_sent_version = getattr(self, "_last_sent_version", 0) + 1
        return self._last_sent_version

    def _arm_recovery(self, html: str) -> None:
        """Arm the on-demand VDOM recovery baseline.

        Single source of truth for the ``request_html`` recovery state
        (``_recovery_html`` / ``_recovery_version``). Every render-send path —
        ``handle_event``, ``server_push``, ``_run_async_work`` — calls this after
        rendering so the baseline can never drift between paths. Hand-copying the
        two-line assignment is exactly how the async path was missed in #1639;
        centralizing it here (#1645) makes a new send path inherit correct arming
        by calling one method. The one-time clear (``_recovery_html = None`` in
        ``handle_request_html``) is the only other writer.

        The recovery version is the consumer's CURRENT ``_last_sent_version``
        (#1788) — NOT a Rust version. Recovery (``html_recovery``) sets
        ``clientVdomVersion = data.version`` directly on the client
        (``static/djust/src/03-websocket.js:727``), so the recovery frame MUST
        carry the consumer version of the frame it replaces. The canonical call
        ordering at every arming site is therefore: allocate ``v`` from
        ``_next_version()`` FIRST, THEN arm recovery (which captures
        ``_last_sent_version == v``), THEN send the frame with ``version=v``.
        """
        # Optional: cleared to None on one-time use (the recovery clear at the
        # request_html path), so the attribute is str | None across its lifetime.
        self._recovery_html: Optional[str] = html
        self._recovery_version = getattr(self, "_last_sent_version", 0)

    def _next_version_armed(self, html: str) -> int:
        """Advance the wire version AND refresh the recovery baseline in one step.

        This is the canonical primitive for every RENDER-SEND path — any frame
        that ships a freshly-rendered patch/HTML the client applies as new
        display state (and that WRITES ``clientVdomVersion = data.version``,
        ``static/djust/src/02-response-handler.js:77``). It folds the
        ``_next_version()`` allocation and the ``_arm_recovery(html)`` capture
        into a single call so the two can never drift apart.

        Why this exists (#1817): before #1816 (#1788) several render-send paths —
        the async-result error arms, the deferred-activity render, the hotreload
        frame, the time-travel jumps, and the tick / db_notify broadcasts —
        advanced ``_next_version()`` WITHOUT arming recovery. After such a frame
        the client's applied version was ahead of ``_recovery_version``, so a
        later ``request_html`` returned an ``html_recovery`` stamped with the
        STALE ``_recovery_version`` (``handle_request_html`` uses
        ``self._recovery_version`` for the wire). The client then reset
        ``clientVdomVersion`` backwards (``03-websocket.js:727``) and the NEXT
        successful diff's ``data.version - 1`` no longer matched — forcing an
        extra recovery round-trip. Routing every render-send path through this
        helper keeps ``_recovery_version == _last_sent_version`` after each
        applied frame, so recovery always resets the client to the version it is
        actually on (#1646 parallel-path discipline: one helper, not N hand-copied
        two-line pairs).

        ``html`` MUST be the full PRE-STRIP HTML returned by
        ``render_with_diff()`` (before ``_strip_comments_and_whitespace`` /
        ``_extract_liveview_content``) — ``handle_request_html`` strips and
        extracts the cached ``_recovery_html`` on demand, so arming with the
        already-stripped/extracted content would double-process it.

        NON-render frames (the mount baseline, ``navigate`` / ``reload`` /
        error-only frames with no new render HTML) must stay on the bare
        ``_next_version()`` — they advance the wire sequence but have no
        client-applied display HTML to recover to.
        """
        version = self._next_version()
        self._arm_recovery(html)
        return version

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
                await self._flush_all_pending()
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
            await self._flush_all_pending()

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
        # Bind the mounted view to a non-None local for the direct-attribute
        # accesses below. The caller (the activity-deferral flush) only reaches
        # this method with a mounted view + held render lock; the guard narrows
        # Optional[Any] and is behavior-equivalent.
        view = self.view_instance
        if view is None:  # pragma: no cover — caller guarantees a mounted view
            return
        # Auto-skip when no public assigns changed (same rule as
        # handle_event). This keeps a deferred side-effect-only handler
        # from triggering an unnecessary render frame on the client.
        skip_render = getattr(view, "_skip_render", False)
        force_html = getattr(view, "_force_full_html", False)
        if not skip_render and not force_html:
            post_assigns = _snapshot_assigns(view)
            if pre_assigns == post_assigns:
                skip_render = True
            else:
                view._changed_keys = _compute_changed_keys(pre_assigns, post_assigns)

        if skip_render:
            view._skip_render = False
            has_async = getattr(view, "_async_pending", None) is not None
            await self._flush_all_pending()
            await self._send_noop(async_pending=has_async, ref=event_ref)
            if has_async:
                await self._dispatch_async_work()
            return

        # Render + diff (mirrors the simpler arm of handle_event).
        _gcd = view.get_context_data
        _skip_thread = inspect.iscoroutinefunction(_gcd) or getattr(view, "sync_safe", False)
        t0 = time.perf_counter()
        try:
            if _skip_thread:
                if inspect.iscoroutinefunction(_gcd):
                    await _gcd()
                else:
                    _gcd()
                with profiler.profile(profiler.OP_RENDER):
                    html, patches, version = view.render_with_diff()
            else:

                def _sync_context_and_render() -> Any:
                    _gcd()
                    with profiler.profile(profiler.OP_RENDER):
                        return view.render_with_diff()

                html, patches, version = await sync_to_async(_sync_context_and_render)()
        except Exception:  # noqa: BLE001
            logger.exception("Deferred-activity render failed for %s", event_name)
            return
        # Consume the force flag (one render per set_changed_keys()/_force_full_html,
        # #1981) — mirrors the runtime's reset in _render_and_send; without it the
        # flag leaks and every subsequent turn force-renders.
        if getattr(view, "_force_full_html", False):
            view._force_full_html = False
        _render_ms = (time.perf_counter() - t0) * 1000

        patch_list = None
        if patches is not None:
            patch_list = fast_json_loads(patches) if patches else []

        has_async = getattr(view, "_async_pending", None) is not None
        if patch_list is not None:
            # Render-send: arm recovery so _recovery_version tracks this deferred
            # render's version (#1817). ``html`` is the pre-strip render.
            await self._send_update(
                patches=patch_list,
                version=self._next_version_armed(html),
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
            # Capture the PRE-STRIP html for recovery arming BEFORE the
            # strip/extract reassigns ``html`` (#1817 — _arm_recovery expects
            # the unstripped render, which handle_request_html strips on demand).
            recovery_html = html
            try:

                def _sync_strip_and_extract(raw_html: str) -> tuple[Any, Any]:
                    stripped = view._strip_comments_and_whitespace(raw_html)
                    content = view._extract_liveview_content(stripped)
                    return stripped, content

                html, html_content = await sync_to_async(_sync_strip_and_extract)(html)
            except Exception:  # noqa: BLE001
                logger.exception("Deferred-activity HTML strip/extract failed for %s", event_name)
                return
            await self._send_update(
                html=html_content,
                version=self._next_version_armed(recovery_html),
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
        """Extract the trustworthy client IP from the ASGI scope.

        Defaults to the real socket peer; ``X-Forwarded-For`` is honored only
        when ``DJUST_TRUSTED_PROXY_COUNT`` is set (peeled from the right). See
        :func:`djust._client_ip.resolve_client_ip` — this keeps a client from
        spoofing XFF to bypass per-IP rate limiting or poison a cooldown.
        """
        from ._client_ip import resolve_client_ip

        headers = dict(self.scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for")
        fwd = forwarded.decode("utf-8") if forwarded else None
        client = self.scope.get("client")
        peer = client[0] if client else None
        return resolve_client_ip(fwd, peer)

    async def connect(self) -> None:
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
            upload_rate=rl_cfg.get("upload_rate", 200),
            upload_burst=rl_cfg.get("upload_burst", 400),
        )

        # Send connection acknowledgment
        await self.send_json(
            {
                "type": "connect",
                "session_id": self.session_id,
            }
        )

    async def disconnect(self, close_code: int) -> None:
        """Handle WebSocket disconnection"""
        # Clear the tenant ContextVar bound at mount (Finding #6) so the
        # consumer task doesn't carry a stale tenant if the executor/context is
        # reused. No-op when tenants is unavailable.
        _bind_tenant(None)

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
        # (#1919, Finding A) Also null the shared runtime's view so a later
        # re-mount on a reused consumer/runtime is never silently no-op'd by
        # ``dispatch_mount``'s ``if view_instance is not None`` idempotency guard.
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            runtime.view_instance = None

    async def receive(
        self, text_data: Optional[str] = None, bytes_data: Optional[bytes] = None
    ) -> None:
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
                    # Rate-account upload frames BEFORE dispatch (#F17). The
                    # global gate below "applies to ALL message types (#107)",
                    # but binary upload frames used to early-return here without
                    # passing it — leaving the highest-volume message class
                    # unthrottled and never tripping the abuse-disconnect. Use a
                    # dedicated higher-ceiling upload bucket so legitimate
                    # high-volume uploads aren't throttled, while a flood still
                    # depletes the bucket → should_disconnect() → close(4429).
                    if not self._rate_limiter.check_upload():
                        if self._rate_limiter.should_disconnect():
                            logger.warning("Upload-frame rate limit exceeded, disconnecting client")
                            client_ip = getattr(self, "_client_ip", None)
                            if client_ip:
                                _rl = djust_config.get("rate_limit", {})
                                cooldown = (
                                    _rl.get("reconnect_cooldown", 5) if isinstance(_rl, dict) else 5
                                )
                                ip_tracker.add_cooldown(client_ip, cooldown)
                            await self.close(code=4429)
                            return
                        await self.send_json(
                            {
                                "type": "rate_limit_exceeded",
                                "message": "Too many upload frames, some are being dropped",
                            }
                        )
                        return
                    await self._handle_upload_frame(bytes_data)
                    return

            # Decode message
            if bytes_data:
                data = msgpack.unpackb(bytes_data, raw=False)
            else:
                # text_data is Optional[str] per the Channels signature; in the
                # no-binary branch a real frame always carries text. Behavior is
                # preserved verbatim (a None here raises TypeError, caught by the
                # surrounding handler, exactly as before this annotation).
                data = json.loads(text_data)  # type: ignore[arg-type]

            msg_type = data.get("type")

            # Global rate limit check — applies to ALL message types (#107)
            if not self._rate_limiter.check(msg_type or "unknown"):
                if self._rate_limiter.should_disconnect():
                    logger.warning("Rate limit exceeded, disconnecting client")
                    client_ip = getattr(self, "_client_ip", None)
                    if client_ip:
                        _rl = djust_config.get("rate_limit", {})
                        cooldown = _rl.get("reconnect_cooldown", 5) if isinstance(_rl, dict) else 5
                        ip_tracker.add_cooldown(client_ip, cooldown)
                    await self.close(code=4429)
                    return
                await self.send_json(
                    {
                        "type": "rate_limit_exceeded",
                        "message": "Too many messages, some events are being dropped",
                    }
                )
                return

            # ---- Frame routing (#1852) -------------------------------------
            # Runtime-owned verbs are routed through the SINGLE
            # ``ViewRuntime.dispatch_message`` chokepoint (runtime.py:246) so a
            # future security/policy control added there auto-applies to the WS
            # transport (the SSE transport already routes every inbound frame
            # through ``dispatch_message``). Everything else is an explicit,
            # documented WS-only extension set delegated to bespoke consumer
            # handlers.
            #
            # ROUTED via dispatch_message (RUNTIME_OWNED_VERBS):
            #   * url_change  — wire-blind, fully shared with SSE since #1237.
            #   * event       — flipped onto the runtime in ADR-022 Iter 2 Phase
            #     2.3b (#1907, THE FLIP). Phase 2.3a grew ``dispatch_event`` into a
            #     functional SUPERSET of the (now deleted) bespoke
            #     ``_handle_event_inner``: the ``event_context`` render-lock +
            #     #1677 origin + PerformanceTracker/SQL observability, the actor
            #     hook, ``view_id`` sticky-child + ``component_id`` LiveComponent
            #     routing, ``dj_activity`` gate + flush, time-travel recording +
            #     #1466 session state-save + ADR-018 sticky-child save, #1777
            #     reauth, #700 identity push-only auto-skip, patch compression,
            #     ``_force_full_html`` + ``embedded_update`` framing, the #1788/
            #     #1858 consumer-owned wire version + recovery arming (via the
            #     ``next_client_version`` hook), and the per-render observability
            #     (DJE-053 warning + ``record_handler_timing`` +
            #     ``_emit_full_html_update`` signal, via the ``on_render_emitted``
            #     / ``on_handler_timing`` hooks). One event path, not two (#1646).
            #
            #   * mount       — flipped onto the runtime in ADR-022 Iter 3 Phase
            #     3.3b (#1919, THE MOUNT FLIP). Phases 3.0-3.3a grew
            #     ``dispatch_mount`` into a functional SUPERSET of the (now
            #     deleted) bespoke ``handle_mount`` body: the F22 view resolver,
            #     pre-mount auth+tenant sequence (``run_pre_mount_auth`` via
            #     ``_check_auth``), ``on_mount`` hooks, session/signed-snapshot
            #     state restore (#1466/#1552), post-mount object-permission
            #     (ADR-017), ``handle_params``, the actor mount hook
            #     (``dispatch_actor_mount``, #1915 Finding D), the no-arm mount
            #     wire version (``next_mount_version``, Finding C), the sticky_hold
            #     pre-mount frame (``on_mount_render_ready``, Finding B residual),
            #     the auth verdict→close finalize (``finalize_mount_auth``, Finding
            #     E), the WS post-mount channel-layer wiring + tick + flags
            #     (``on_view_mounted``, Finding B residual), and the 2-queue
            #     mount-time drain. One mount path, not two — the #1646 mount
            #     convergence COMPLETE.
            #
            # WS-ONLY EXTENSION SET (no runtime equivalent; binary upload
            # frames 0x01/0x02/0x03 are handled above before decode):
            #   mount_batch, ping, live_redirect_mount, upload_register,
            #   upload_resume, presence_heartbeat, cursor_move, request_html,
            #   debug_panel_open, debug_panel_close, time_travel_jump,
            #   time_travel_component_jump, forward_replay.
            if msg_type in self.RUNTIME_OWNED_VERBS:
                # Runtime-owned: route through the dispatch_message chokepoint
                # rather than calling the bespoke handler directly, so future
                # chokepoint-level controls cover this verb on the WS path. The
                # membership check (not a hardcoded ``== "url_change"``) makes
                # ``RUNTIME_OWNED_VERBS`` LOAD-BEARING (#1852): adding a verb to
                # the set automatically routes it here, and the contract test
                # (``TestRuntimeOwnedVerbsContract``) fails if the set and this
                # arm ever drift. This is the FIRST arm, so ``event`` (#1907) AND
                # ``mount`` (#1919, THE MOUNT FLIP) both land here — NOT the deleted
                # ``elif`` arms that used to call the bespoke ``handle_event`` /
                # ``handle_mount``.
                await self._dispatch_runtime_owned(data)
            elif msg_type == "mount_batch":
                await self.handle_mount_batch(data)
            elif msg_type == "ping":
                await self.send_json({"type": "pong"})
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
            elif msg_type == "time_travel_component_jump":
                await self.handle_time_travel_component_jump(data)
            elif msg_type == "forward_replay":
                await self.handle_forward_replay(data)
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
    ) -> None:
        """Handle view mounting by routing through :class:`ViewRuntime`.

        ADR-022 Iter 3 Phase 3.3b (#1919, THE MOUNT FLIP). Until this phase,
        ``mount`` was handled by a ~870-line bespoke WS-only body (a twin of the
        runtime's mount path). Phases 3.0-3.3a grew
        :meth:`ViewRuntime.dispatch_mount` into a functional SUPERSET of that
        bespoke handler (F22 view resolver, ``run_pre_mount_auth`` pre-mount
        auth+tenant sequence via ``_check_auth``, ``on_mount`` hooks, session +
        signed-snapshot state restore, post-mount object-permission, ``handle_params``,
        actor mount + render, the no-arm mount wire version, the ``sticky_hold``
        pre-mount frame, the auth verdict→``close(4403)`` finalize, the WS
        post-mount channel-layer wiring + tick + flags, and the 2-queue mount-time
        drain), all wired through ``WSConsumerTransport`` hooks. Phase 3.3b adds
        ``"mount"`` to :attr:`RUNTIME_OWNED_VERBS` so ``receive()`` now routes mount
        frames through the single ``dispatch_message`` chokepoint (the #1646 mount
        convergence — one mount path, not two). This method is therefore a THIN SHIM
        mirroring :meth:`handle_url_change` / :meth:`handle_event`.

        ``receive()`` no longer calls this directly for the ``mount`` verb (that
        goes via :meth:`_dispatch_runtime_owned` → ``dispatch_message`` →
        ``dispatch_mount``), but it is retained as a stable public entry point /
        backward-compatible shim for the WS-only callers that invoke it directly
        with the extra kwargs: :meth:`handle_live_redirect_mount` (passes
        ``sticky_preserved`` so the ``sticky_hold`` frame precedes the mount frame)
        and :meth:`_mount_one` (the ``mount_batch`` collector).

        Three flip findings (ADR-022 Iter 3) handled here / by the runtime:

        * **(A) Idempotency reset** — ``dispatch_mount`` early-returns when
          ``runtime.view_instance is not None`` (the legacy GET-mount double-fire
          guard). The consumer nulls ``self.view_instance`` on disconnect /
          auth-fail / ``live_redirect`` teardown but NEVER ``runtime.view_instance``,
          so a re-mount on a runtime that already mounted once would silently
          no-op (the #560-class landmine). We null ``runtime.view_instance`` BEFORE
          dispatching so every (re-)mount actually runs.
        * **(B) Ownership inverts** — ``url_change`` / ``event`` mirror
          consumer→runtime; ``mount`` CREATES the view (runtime→consumer), so we
          read back ``self.view_instance = runtime.view_instance`` after dispatch.
          The WS post-mount consumer attrs (``_view_group`` / ``_tick_task`` /
          ``use_actors`` / presence + db_notify groups / ``_sticky_preserved``)
          are written onto the consumer by the ``on_view_mounted`` /
          ``on_mount_render_ready`` transport hooks during the runtime mount.
        * **(C) Mount wire version** — the ``next_mount_version`` hook stamps the
          consumer-owned monotonic ``_next_version()`` WITHOUT arming request_html
          recovery (mount establishes the baseline; it has no prior frame to
          recover to).
        """
        runtime = self._get_runtime()
        # (A) Null the runtime's view BEFORE dispatch so a reconnect / live_redirect
        # re-mount is never silently no-op'd by the idempotency early-return.
        runtime.view_instance = None

        # Thread the WS-only direct-caller kwargs into the shape dispatch_mount +
        # its hooks read:
        #   * ``sticky_preserved`` — the ``on_mount_render_ready`` hook reads
        #     ``consumer._sticky_preserved`` (set by handle_live_redirect_mount
        #     already; set it here too for any direct caller passing the kwarg).
        #   * ``state_snapshot`` — ``dispatch_mount`` reads ``data.get("state_snapshot")``
        #     (handle_live_redirect_mount already puts it in ``data``; thread the
        #     kwarg in for direct callers without mutating the caller's dict).
        if sticky_preserved is not None:
            self._sticky_preserved = sticky_preserved
        if state_snapshot is not None and data.get("state_snapshot") is None:
            data = {**data, "state_snapshot": state_snapshot}

        await runtime.dispatch_mount(data)

        # (B) Mount creates the view on the runtime — read it back onto the consumer
        # so disconnect cleanup, request_html recovery, upload/presence handlers,
        # and the batch collector all see the freshly-mounted view (or ``None`` on
        # an auth/hook block, which dispatch_mount cleared on the runtime).
        self.view_instance = runtime.view_instance

    async def _mount_one(
        self, data_view: Dict[str, Any]
    ) -> tuple[bool, Dict[str, Any], Optional[str], Optional[Dict[str, Any]], List[Any]]:
        """Mount + render a single view and return a payload WITHOUT sending.

        Collector seam for :meth:`handle_mount_batch`. Delegates to
        ``handle_mount`` but replaces ``send_json`` on this consumer with
        a capture list so no frames actually flow to the client. The
        caller aggregates captured frames into a single ``mount_batch``
        frame.

        Returns a ``(success: bool, payload: dict, error: Optional[str],
        navigate_frame: Optional[dict], push_events: list)`` tuple:

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
        - ``push_events`` (Fix #1295): ``push_event`` frames captured
          during mount. When ``mount()`` calls ``push_event()``,
          ``_flush_push_events`` fires with ``send_json`` swapped for the
          collector, so push events land in ``captured[]``.  The caller
          flushes them after the batch response so they reach the client.

        Isolation: errors in one view MUST NOT propagate and kill the
        batch (see plan §2.3 "atomicity relaxed").
        """
        target_id = data_view.get("target_id") or ""
        view_path = data_view.get("view") or ""

        # Temporarily swap send_json with a collector so handle_mount's
        # frame-sending becomes a frame-collecting call. Restore on exit.
        captured: list = []
        orig_send_json = self.send_json

        async def _collect(payload: Dict[str, Any]) -> None:
            captured.append(payload)

        self.send_json = _collect  # type: ignore[assignment]
        # Signal to handle_mount that it runs inside a multiplexed batch on a
        # shared socket: an auth/hook redirect must NOT close() the socket here
        # (that would kill sibling mounts + the collected navigate[] and
        # reconnect-storm the client). handle_mount still clears view_instance,
        # and the redirect's navigate frame is collected into navigate[] — so a
        # batched login-required view is reported as a redirect, not a bypass.
        self._mounting_in_batch = True
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
            self._mounting_in_batch = False
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
            return False, {"target_id": target_id, "view": view_path}, safe_err, None, []
        finally:
            # Only restore if the try-block didn't already restore (else
            # we'd double-restore harmlessly). Idempotent.
            self.send_json = orig_send_json  # type: ignore[assignment]
            self._mounting_in_batch = False

        # Extract the successful mount frame; any "error" frame means failure.
        # Fix #4: capture "navigate" frames too — those are emitted when
        # auth or on_mount hooks redirect instead of mounting, and were
        # previously silently dropped.
        # Fix #1295: also capture "push_event" frames. When mount() calls
        # push_event(), _flush_push_events runs with send_json swapped for
        # _collect — so push events land in captured[]. Extract them and
        # return them so handle_mount_batch can flush them after the batch
        # response (they'd otherwise be silently dropped).
        mount_frame = None
        error_frame = None
        navigate_frame = None
        push_events: list = []
        for frame in captured:
            ftype = frame.get("type")
            if ftype == "mount":
                mount_frame = frame
            elif ftype == "error":
                error_frame = frame
            elif ftype == "navigate":
                navigate_frame = frame
            elif ftype == "push_event":
                push_events.append(frame)

        if navigate_frame is not None:
            # Redirect — surface through the batch response so the
            # client dispatcher can navigate. target_id is included so
            # the client can associate the redirect with the originating
            # lazy element.
            nav_payload = dict(navigate_frame)
            nav_payload["target_id"] = target_id
            nav_payload["view"] = view_path
            return (
                False,
                {"target_id": target_id, "view": view_path},
                None,
                nav_payload,
                push_events,
            )

        if error_frame is not None:
            err_msg = error_frame.get("message", "mount failed")
            return False, {"target_id": target_id, "view": view_path}, err_msg, None, push_events

        if mount_frame is None:
            return (
                False,
                {"target_id": target_id, "view": view_path},
                "mount produced no frame",
                None,
                push_events,
            )

        # Inject target_id for client-side per-view DOM targeting.
        mount_frame["target_id"] = target_id
        return True, mount_frame, None, None, push_events

    async def handle_mount_batch(self, data: Dict[str, Any]) -> None:
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
        all_push_events: list = []
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
            ok, payload, err, nav, push_events = await self._mount_one(view_data)
            if push_events:
                all_push_events.extend(push_events)
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

        # Fix #1295: flush push events that were captured during mount.
        # When mount() calls push_event(), _flush_push_events fires with
        # send_json swapped for _collect in _mount_one — so push events
        # land in captured[] instead of being sent. We extract them in
        # _mount_one and flush them here after the batch response.
        for frame in all_push_events:
            await self.send_json(frame)

    async def handle_event(self, data: Dict[str, Any]) -> None:
        """Handle a client event by routing through :class:`ViewRuntime`.

        ADR-022 Iter 2 Phase 2.3b (#1907, THE FLIP). Until this phase, ``event``
        was handled by the bespoke ``_handle_event_inner`` (a ~1170-line WS-only
        twin of the runtime's event path). Phase 2.3a grew
        :meth:`ViewRuntime.dispatch_event` into a functional SUPERSET of that
        bespoke handler (event spine, component/sticky/embedded routing,
        time-travel + #1466 state-save, ``event_context`` render-lock + origin +
        observability, the actor hook, ``dj_activity`` gate + flush, #1777 reauth,
        async dispatch, wire-version stamping), and Phase 2.3b adds ``"event"`` to
        :attr:`RUNTIME_OWNED_VERBS` so ``receive()`` now routes events through the
        single ``dispatch_message`` chokepoint (the #1646 convergence — one event
        path, not two). This method is therefore a THIN SHIM mirroring
        :meth:`handle_url_change`: ``receive()`` no longer calls it directly for the
        ``event`` verb (that goes via :meth:`_dispatch_runtime_owned` →
        ``dispatch_message``), but it is retained as a stable public entry point /
        backward-compatible shim for callers that invoke it directly. The runtime
        owns the tenant context (``dispatch_event`` wraps the turn in
        ``_tenant_context``), the render lock, time-travel, state-save, and the
        per-render observability (DJE-053 warning + handler timing + full-HTML-update
        signal) via the ``on_render_emitted`` / ``on_handler_timing`` transport hooks.

        Calls ``runtime.dispatch_event(data)`` DIRECTLY (mirroring
        :meth:`handle_url_change`, which calls ``dispatch_url_change`` directly) —
        NOT ``dispatch_message``. Direct callers of ``handle_event`` pass the EVENT
        payload (``{"event": ..., "params": ...}``) and may omit the outer
        ``{"type": "event"}`` wire envelope; ``dispatch_event`` reads ``event`` /
        ``params`` and does not require ``type``, whereas ``dispatch_message`` keys
        on ``type`` and would emit "Unknown message type" for a type-less payload.
        The ``receive()`` wire path still routes through ``dispatch_message`` (the
        frame there always carries ``type: event``).
        """
        if not self.view_instance:
            await self.send_error("View not mounted. Please reload the page.")
            return

        runtime = self._get_runtime()
        runtime.view_instance = self.view_instance
        await runtime.dispatch_event(data)

    # ========================================================================
    # Embedded LiveView Rendering
    # ========================================================================

    def _render_embedded_child(self, child_view: Any) -> str:
        """
        Render an embedded child view's template and return the inner HTML.

        This re-renders just the child's template using Django's template engine,
        without going through the parent's VDOM at all.

        Thin shim over the module-level :func:`render_embedded_child_html` so the
        WS consumer and :class:`~djust.runtime.ViewRuntime` (ADR-022 Iter 2
        Phase 2.1) render embedded children through ONE implementation — the
        #1646 cure for the embedded-render subsystem. The pure (transport-blind)
        render core, including the security-hardened error path, lives in that
        function so there is no parallel copy to drift.
        """
        return render_embedded_child_html(child_view)

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

        # Snapshot the current ``active`` value into the ref-check callable
        # (mypy cannot infer a defaulted-param lambda, so use a typed nested def;
        # the default-arg capture preserves the original snapshot-at-build intent).
        def _active_ref(_uid: str, _a: bool = active) -> bool:
            return _a

        payload = resolve_resume_request(
            upload_id=upload_id,
            session_key=session_key,
            active_refs=_active_ref,
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

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Send JSON message to client with Django type support"""
        await self.send(text_data=json.dumps(data, cls=DjangoJSONEncoder))

    @staticmethod
    def _clear_template_caches() -> int:
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

    async def hotreload(self, event: Dict[str, Any]) -> None:
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

                # Send the patches to the client.
                # HIDDEN #1 (#1788): the hotreload patch frame is EXEMPT from the
                # client version *check* (``!data.hotreload`` in
                # ``02-response-handler.js:58``) but it still WRITES
                # ``clientVdomVersion = data.version`` (line 77). So it MUST stamp
                # the consumer counter — otherwise the NEXT normal event would be
                # rejected against a stale client version. The separate
                # ``_hvr_version`` (``hvr-applied`` telemetry frame) is untouched.
                # Render-send: the hotreload frame is exempt from the client
                # version CHECK but it still WRITES clientVdomVersion (#1788,
                # HIDDEN #1), so it advances the client past _recovery_version.
                # Arm recovery so a later request_html serves the post-HVR HTML,
                # not a stale pre-HVR baseline (#1817). ``html`` is the pre-strip
                # render from render_with_diff() above.
                await self._send_update(
                    patches=patches,
                    version=self._next_version_armed(html),
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

    def _get_runtime(self) -> Any:
        """Lazy-construct the shared :class:`ViewRuntime` for this consumer.

        Returns the same runtime instance across calls so per-runtime state
        (e.g., ``view_instance``) survives. Introduced in #1237 for the
        ``handle_url_change`` migration; subsequent PRs will route more WS
        verbs through the runtime.
        """
        if getattr(self, "_runtime", None) is None:
            from .runtime import WSConsumerTransport, ViewRuntime

            self._runtime = ViewRuntime(
                WSConsumerTransport(self),
                scope=self.scope,
                rate_limiter=self._rate_limiter,
            )
        return self._runtime

    #: Inbound frame verbs that ``receive()`` routes through the single
    #: :meth:`ViewRuntime.dispatch_message` chokepoint (#1852) rather than a
    #: bespoke consumer handler. Keeping this as an explicit set lets a
    #: regression test pin exactly which verbs go through the chokepoint, so a
    #: future addition that forgets the routing is caught.
    #:
    #: ``event`` was added in ADR-022 Iter 2 Phase 2.3b (#1907, THE FLIP): the
    #: runtime ``dispatch_event`` is now a functional superset of the deleted
    #: bespoke ``_handle_event_inner`` (Phase 2.3a grew the event_context render
    #: lock + origin + observability, the actor hook, the per-render
    #: ``on_render_emitted`` / ``on_handler_timing`` folds, time-travel, #1466
    #: state-save, ``dj_activity``, #1777 reauth), so every WS event now flows
    #: through the single chokepoint — the #1646 convergence.
    #:
    #: ``mount`` was added in ADR-022 Iter 3 Phase 3.3b (#1919, THE MOUNT FLIP):
    #: Phases 3.0-3.3a grew ``dispatch_mount`` into a functional superset of the
    #: deleted bespoke ``handle_mount`` body (F22 resolver, ``run_pre_mount_auth``
    #: pre-mount auth+tenant via ``_check_auth``, ``on_mount`` hooks, session +
    #: signed-snapshot restore, post-mount object-perm, ``handle_params``, actor
    #: mount, the no-arm mount wire version, the sticky_hold pre-mount frame, the
    #: auth verdict→close finalize, the WS post-mount channel-layer wiring + tick +
    #: flags, the 2-queue mount-time drain) via ``WSConsumerTransport`` hooks, so
    #: every WS mount now flows through the single chokepoint — the #1646 mount
    #: convergence COMPLETE. See the routing comment in :meth:`receive`.
    RUNTIME_OWNED_VERBS = frozenset({"url_change", "event", "mount"})

    async def _dispatch_runtime_owned(self, data: Dict[str, Any]) -> None:
        """Route a runtime-owned frame through :meth:`ViewRuntime.dispatch_message`.

        This is the WS-side seam for #1852: the runtime-owned subset of
        ``receive()``'s verbs flows through the SINGLE ``dispatch_message``
        chokepoint so a future security/policy control added there auto-applies
        to the WebSocket transport. The consumer's ``view_instance`` is mirrored
        onto the shared runtime before dispatch (the runtime is the source of
        truth for ``view_instance`` once mount migrated in #1919).

        ``view_instance`` ownership INVERTS for ``mount`` (#1919, Finding B):
        ``url_change`` / ``event`` mirror consumer→runtime, but ``mount`` CREATES
        the view, and ``dispatch_mount`` early-returns when ``runtime.view_instance
        is not None`` (the legacy GET-mount double-fire guard, runtime.py). So for
        a ``mount`` frame we NULL the runtime's view first — otherwise a
        reconnect / re-mount on a runtime that already mounted once would silently
        no-op (the #560-class idempotency landmine, Finding A). The shim path
        (``handle_mount`` / ``handle_live_redirect_mount`` / ``_mount_one``) does
        the same null+readback; this is the wire-frame twin.
        """
        runtime = self._get_runtime()
        if data.get("type") == "mount":
            runtime.view_instance = None
        else:
            runtime.view_instance = self.view_instance
        await runtime.dispatch_message(data)
        # Mount CREATES the view on the runtime (runtime→consumer read-back,
        # Finding B): mirror it back so the consumer's ``view_instance`` (read by
        # disconnect cleanup, request_html recovery, upload/presence handlers, the
        # batch collector, etc.) reflects the freshly-mounted view. On an
        # auth/hook block ``dispatch_mount`` cleared ``runtime.view_instance`` so
        # this reads back ``None`` — matching the bespoke "clear on auth fail".
        if data.get("type") == "mount":
            self.view_instance = runtime.view_instance

    async def handle_url_change(self, data: Dict[str, Any]) -> None:
        """
        Handle URL change from browser back/forward (popstate) or dj-patch clicks.

        #1237: this is now a thin shim over :meth:`ViewRuntime.dispatch_url_change`
        so the WS and SSE transports share one code path. The runtime owns the
        handle_params + re-render + send_update orchestration.

        #1852: ``receive()`` no longer calls this directly — it routes
        ``url_change`` through :meth:`_dispatch_runtime_owned` →
        :meth:`ViewRuntime.dispatch_message` so the verb passes the shared
        chokepoint. This method is retained as a stable public entry point /
        backward-compatible shim for callers that dispatch ``url_change``
        directly; it remains behaviorally identical (``dispatch_message`` routes
        ``url_change`` straight to ``dispatch_url_change``).
        """
        if not self.view_instance:
            await self.send_error("View not mounted")
            return

        runtime = self._get_runtime()
        runtime.view_instance = self.view_instance
        await runtime.dispatch_url_change(data)

    async def handle_live_redirect_mount(self, data: Dict[str, Any]) -> None:
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
        # Reset auto-reattach tracker (ADR-014): a redirect mount starts
        # a fresh template render; any IDs the tag claims should be tracked
        # against this navigation only.
        self._sticky_auto_reattached = set()
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
        # (#1919, Finding A) Null the shared runtime's view too BEFORE the
        # re-mount below. ``handle_mount`` (the shim) also nulls it, but doing it
        # here keeps the teardown self-consistent: a re-mount on this connection
        # must never be no-op'd by ``dispatch_mount``'s idempotency early-return —
        # this is the live_redirect re-mount landmine the Finding-A net guards.
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            runtime.view_instance = None

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
        #
        # #1647: trust the DESTINATION URL over the client-supplied `view`.
        # The client's resolveViewPath() falls back to the current container's
        # dj-view (the SOURCE view) when its route map is empty — which is the
        # default for apps using plain Django path() URLconfs (no
        # live_session()). Mounting the source class against the new URL's
        # request raises. Resolve the target view server-side from the URL; only
        # override when it maps to a djust LiveView, so the live_session
        # route-map path (client resolved correctly) is unaffected.
        #
        # NOT for back-navigation: a `state_snapshot` carries the authoritative
        # `view_slug` for the restored view, and its `url` may be generic (e.g.
        # "/") and resolve to an unrelated view. Skip the URL-override whenever a
        # snapshot is present so back-nav restores the snapshot's view, not
        # whatever the URL happens to map to.
        resolved_view = (
            None
            if data.get("state_snapshot")
            else self._resolve_view_path_from_url(data.get("url", ""))
        )
        if resolved_view and resolved_view != data.get("view"):
            logger.debug(
                "live_redirect_mount: server-resolved target view %s from URL %s (client sent %s)",
                resolved_view,
                sanitize_for_log(data.get("url", "")),
                sanitize_for_log(str(data.get("view"))),
            )
            data = {**data, "view": resolved_view}
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

    def _resolve_view_path_from_url(self, url: str) -> Optional[str]:
        """Resolve a ``live_redirect`` destination URL to its djust LiveView
        dotted path, server-side, via Django's URL resolver (#1647).

        Returns the ``module.QualName`` of the view class wired to ``url`` in the
        URLconf when (and only when) it is a :class:`djust.LiveView` subclass.
        Returns ``None`` when the URL doesn't resolve, or maps to a non-LiveView
        (e.g. a plain Django view) — the caller then keeps the client-supplied
        ``view`` (preserving the ``live_session`` route-map path, where the
        client already resolved the target correctly).
        """
        if not url:
            return None
        from django.urls import Resolver404, resolve

        from .live_view import LiveView

        try:
            match = resolve(url)
        except Resolver404:
            return None
        except Exception:  # noqa: BLE001 — never let URL resolution break mount
            logger.debug("live_redirect view resolution raised for %s", sanitize_for_log(url))
            return None
        # View.as_view() stamps the class onto the returned callable.
        view_class = getattr(match.func, "view_class", None)
        if not (isinstance(view_class, type) and issubclass(view_class, LiveView)):
            return None
        return f"{view_class.__module__}.{view_class.__qualname__}"

    def _build_live_redirect_request(self, data: Dict[str, Any]) -> Any:
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
        # The client-supplied URL is attacker-controlled — validate it against
        # path traversal / CRLF / absolute-URL injection before it reaches
        # RequestFactory.get(), resolve(), and the log statements below (#1819).
        page_url = _validate_mount_url(data.get("url", "/"))
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

    async def handle_presence_heartbeat(self, data: Dict[str, Any]) -> None:
        """Handle presence heartbeat from client."""
        if not self.view_instance or not hasattr(self.view_instance, "update_presence_heartbeat"):
            return

        try:
            await sync_to_async(self.view_instance.update_presence_heartbeat)()
        except Exception as e:
            logger.error("Error updating presence heartbeat: %s", e)

    async def handle_cursor_move(self, data: Dict[str, Any]) -> None:
        """Handle cursor movement for live cursors."""
        if not self.view_instance or not hasattr(self.view_instance, "handle_cursor_move"):
            return

        try:
            x = data.get("x", 0)
            y = data.get("y", 0)
            await sync_to_async(self.view_instance.handle_cursor_move)(x, y)
        except Exception as e:
            logger.error("Error handling cursor move: %s", e)

    def _has_live_sticky_children(self) -> bool:
        """True if the parent view currently holds at least one registered
        sticky child (a ``{% live_render sticky=True %}`` embed).

        Used by :meth:`handle_request_html` to decide whether the cached
        ``_recovery_html`` is trustworthy. The cached snapshot is taken on the
        last PARENT render-send (mount / parent event); embedded-child events
        deliberately do NOT re-arm it (they send a scoped ``embedded_update``,
        not a full parent render). So after a child interaction the cached HTML
        holds an OLD child state — replaying it would reset the sticky child to
        that stale state (#1813). For pages WITH live sticky children we
        re-render the parent FRESH at recovery time instead; the (b1)
        live-instance-reuse hatch in ``live_tags.py`` makes that re-render
        faithful to the child's current state.
        """
        view = self.view_instance
        if view is None:
            return False
        get_all = getattr(view, "_get_all_child_views", None)
        if not callable(get_all):
            return False
        try:
            children = get_all()
        except Exception:  # noqa: BLE001 — defensive: never break recovery
            return False
        return any(getattr(child, "sticky_id", None) for child in children.values())

    async def handle_request_html(self, data: Dict[str, Any]) -> None:
        """
        Handle client request for full HTML when VDOM patches fail.

        The client sends {"type": "request_html"} when applyPatches() returns
        false (e.g., due to {% if %} blocks shifting DOM structure). Server
        responds with the last rendered HTML for client-side DOM morphing.

        #1813 (b2)(ii): when the parent has live sticky children, the cached
        ``_recovery_html`` may be stale (it is NOT re-armed on embedded-child
        events, which send scoped ``embedded_update`` frames rather than a full
        parent render). Replaying it would reset the sticky child to mount /
        pre-interaction state — the data-loss bug. For such pages we re-render
        the parent FRESH here; the (b1) live-instance-reuse hatch in
        ``live_tags.py`` makes the fresh render faithful to the live child's
        current state. Recovery is rare and child events frequent, so paying
        the re-render cost on recovery (not on every child event) is also the
        lowest-overhead choice. Non-sticky pages keep the cached-replay path
        unchanged.
        """
        if not self.view_instance:
            await self.send_error("View not mounted")
            return
        # Non-None handle for the nested render closure below (mypy doesn't carry
        # the guard's narrowing into the closure; the view is mounted here).
        view = self.view_instance

        # The html_recovery frame carries the CONSUMER version of the frame it
        # replaces (#1788): the client sets ``clientVdomVersion = data.version``
        # directly on html_recovery (``static/djust/src/03-websocket.js:727``),
        # so it MUST equal ``_recovery_version`` (captured by _arm_recovery from
        # _last_sent_version). The fresh re-render below produces a NEW Rust
        # version which is DISCARDED for the wire — sending it would desync the
        # client against the consumer counter.
        version = getattr(self, "_recovery_version", 0)

        if self._has_live_sticky_children():
            # Re-render the parent fresh so the recovery HTML reflects the live
            # sticky child's CURRENT state (#1813). Mirrors the sync/render
            # sequence used by the async-result path (sync state to Rust, then
            # render_with_diff for the full raw HTML). The fresh Rust version is
            # DISCARDED (#1788) — only the HTML is taken; ``version`` stays the
            # consumer-owned ``_recovery_version``.
            def _sync_and_render() -> Any:
                if hasattr(view, "_sync_state_to_rust"):
                    view._sync_state_to_rust()
                fresh_html, _patches, _fresh_version = view.render_with_diff()
                return fresh_html

            try:
                html = await sync_to_async(_sync_and_render)()
            except Exception:  # noqa: BLE001 — fall back to cached snapshot
                logger.exception(
                    "[djust] request_html fresh re-render failed; falling back "
                    "to cached recovery HTML"
                )
                html = getattr(self, "_recovery_html", None)
        else:
            html = getattr(self, "_recovery_html", None)

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
            # Surface __components__ at the top level too (#1151, v0.9.4)
            # so the client doesn't have to dig into entry.state_after to
            # find per-component state. Mirrors the existing additive-
            # field pattern from mount-batch frames.
            components_mirror = None
            if not entry.get("_truncated"):
                state_after = entry.get("state_after") or {}
                components_mirror = state_after.get("__components__")
            await self.send_json(
                {
                    "type": "time_travel_event",
                    "entry": entry,
                    "history_len": len(buffer),
                    "branch_id": getattr(view, "_time_travel_branch_id", "main"),
                    "components": components_mirror,
                }
            )
        except Exception:  # noqa: BLE001 — dev-only, degrade silently
            logger.exception("time_travel: failed to push event frame")

    def _build_time_travel_state(
        self,
        view: Any,
        buffer: Any,
        cursor: int,
        which: str,
    ) -> Dict[str, Any]:
        """Build the ``time_travel_state`` ack frame (#1151, v0.9.4).

        Augments the v0.6.1 ack shape (cursor / which / history_len) with
        ``branch_id``, ``forward_replay_enabled``, and ``max_events`` so
        the debug panel UI can render the per-component scrubber, the
        forward-replay button, and the max-events indicator without a
        second request.

        ``forward_replay_enabled`` is true iff replaying from the current
        cursor would produce a meaningful branch — i.e., the cursor is
        not at the canonical tip of the buffer. Tip semantics depend on
        ``which``: with ``which="after"`` the tip is the last index;
        with ``which="before"`` the tip is the index PAST the last (the
        baseline before any future event would land), so a cursor at
        ``len-1`` with ``which="before"`` is still pre-tip.
        """
        from djust.config import config as _djust_config

        history_len = len(buffer)
        if which == "after":
            forward_replay_enabled = cursor < history_len - 1
        else:  # which == "before"
            forward_replay_enabled = cursor < history_len
        return {
            "type": "time_travel_state",
            "cursor": cursor,
            "which": which,
            "history_len": history_len,
            "branch_id": getattr(view, "_time_travel_branch_id", "main"),
            "forward_replay_enabled": forward_replay_enabled,
            "max_events": getattr(
                buffer, "max_events", _djust_config.get("time_travel_max_events", 100)
            ),
        }

    async def handle_time_travel_jump(self, data: Dict[str, Any]) -> None:
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
                # Render-send: arm recovery so _recovery_version tracks this
                # jump's version (#1817). ``html`` is the pre-strip render.
                version=self._next_version_armed(html),
                event_name="__time_travel_jump__",
            )
        except Exception as exc:  # noqa: BLE001 — dev-only, log + report
            logger.exception("time_travel_jump: re-render failed")
            await self.send_error("time_travel_jump: re-render failed: %s" % exc)
            return

        await self.send_json(
            self._build_time_travel_state(self.view_instance, buffer, index, which)
        )

    async def handle_time_travel_component_jump(self, data: Dict[str, Any]) -> None:
        """Scrub a SINGLE component's state (#1151, v0.9.4).

        Dev-only. Mirrors :meth:`handle_time_travel_jump` but restores
        only ``view._components[component_id]`` from the snapshot at
        ``index``, leaving the parent view and other components alone.
        Used by the debug panel's per-component scrubber.

        Frame: ``{"type": "time_travel_component_jump", "index": N,
        "component_id": "<id>", "which": "before"|"after"}``.
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
        component_id = data.get("component_id")
        which = data.get("which", "before")
        if not isinstance(index, int):
            await self.send_error("time_travel_component_jump: index must be int")
            return
        if not isinstance(component_id, str) or not component_id:
            await self.send_error(
                "time_travel_component_jump: component_id must be a non-empty string"
            )
            return
        if which not in ("before", "after"):
            await self.send_error("time_travel_component_jump: which must be 'before' or 'after'")
            return

        snapshot = buffer.jump(index)
        if snapshot is None:
            await self.send_error("time_travel_component_jump: no snapshot at index %d" % index)
            return

        from djust.time_travel import restore_component_snapshot

        ok = await sync_to_async(restore_component_snapshot)(
            self.view_instance, snapshot, component_id, which
        )
        if not ok:
            await self.send_error("time_travel_component_jump: restore failed")
            return

        try:
            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()
            patch_list = None
            if patches is not None:
                patch_list = fast_json_loads(patches) if patches else []
            await self._send_update(
                patches=patch_list,
                html=html,
                # Render-send: arm recovery so _recovery_version tracks this
                # component-jump's version (#1817). ``html`` is the pre-strip render.
                version=self._next_version_armed(html),
                event_name="__time_travel_component_jump__",
            )
        except Exception as exc:  # noqa: BLE001 — dev-only, log + report
            logger.exception("time_travel_component_jump: re-render failed")
            await self.send_error("time_travel_component_jump: re-render failed: %s" % exc)
            return

        await self.send_json(
            self._build_time_travel_state(self.view_instance, buffer, index, which)
        )

    async def handle_forward_replay(self, data: Dict[str, Any]) -> None:
        """Forward-replay a recorded event with optional override params (#1151, v0.9.4).

        Dev-only. Restores the view to ``state_before`` of the snapshot at
        ``from_index`` and re-invokes the recorded event handler with
        either the original params or caller-supplied ``override_params``.
        When the cursor is not at the buffer tip, allocates a new
        ``branch_id`` for the resulting branched timeline.

        Frame: ``{"type": "forward_replay", "from_index": N,
        "override_params": {...}}`` (override_params is optional).
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

        from_index = data.get("from_index")
        override_params = data.get("override_params")
        if not isinstance(from_index, int):
            await self.send_error("forward_replay: from_index must be int")
            return
        if override_params is not None and not isinstance(override_params, dict):
            await self.send_error("forward_replay: override_params must be dict or null")
            return

        snapshot = buffer.jump(from_index)
        if snapshot is None:
            await self.send_error("forward_replay: no snapshot at index %d" % from_index)
            return

        # Decide whether this replay forks the timeline. Two conditions
        # warrant a new branch_id (either is sufficient):
        #   1. ``from_index`` is not the buffer tip — replaying a past
        #      event diverges from the recorded successor.
        #   2. ``override_params`` is non-None — replay runs with
        #      different inputs than originally recorded, so it diverges
        #      even when from the tip. (Caught by Stage 11 review:
        #      replaying the LAST entry with override_params silently
        #      merged into "main" before this fix.)
        # Replay from the tip with no overrides just re-records to
        # "main". Branch-id is allocated AFTER ``replay_event`` succeeds
        # — otherwise a missing / un-decorated handler would leak a
        # counter bump and a stale branch_id with no recorded events.
        from djust.time_travel import next_branch_id, replay_event

        history_len_before = len(buffer)
        forks_timeline = from_index < history_len_before - 1 or override_params is not None

        replayed = await sync_to_async(replay_event)(
            self.view_instance, snapshot, override_params, True
        )
        if replayed is None:
            await self.send_error("forward_replay: replay handler missing or refused")
            return

        # Replay succeeded — commit the branch_id mutation now.
        if forks_timeline:
            new_branch = next_branch_id(self.view_instance)
            try:
                self.view_instance._time_travel_branch_id = new_branch
            except Exception:  # noqa: BLE001 — slot/descriptor readonly
                logger.exception("forward_replay: failed to set branch_id")

        try:
            html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()
            patch_list = None
            if patches is not None:
                patch_list = fast_json_loads(patches) if patches else []
            await self._send_update(
                patches=patch_list,
                html=html,
                # Render-send: arm recovery so _recovery_version tracks this
                # forward-replay's version (#1817). ``html`` is the pre-strip render.
                version=self._next_version_armed(html),
                event_name="__forward_replay__",
            )
        except Exception as exc:  # noqa: BLE001 — dev-only, log + report
            logger.exception("forward_replay: re-render failed")
            await self.send_error("forward_replay: re-render failed: %s" % exc)
            return

        # Cursor lands at the new tip after the replay's recorded snapshot.
        new_cursor = len(buffer) - 1
        await self.send_json(
            self._build_time_travel_state(self.view_instance, buffer, new_cursor, "after")
        )

    async def presence_event(self, event: Dict[str, Any]) -> None:
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

    async def server_push(self, event: Dict[str, Any]) -> None:
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
            # Skip our OWN self-broadcast (#1677): when a handler on THIS
            # session pushed to its own view, the originating session already
            # got the state via its direct event response. Re-rendering for the
            # redundant self-broadcast bumps the VDOM version, which under rapid
            # event bursts arrives non-sequentially at the client and triggers a
            # full-HTML recovery storm + intermittent reconnect. Other sessions
            # (sender_channel != ours) and external pushes (sender_channel is
            # None — Celery, cross-view, etc.) are unaffected.
            sender_channel = event.get("sender_channel")
            if sender_channel and sender_channel == self.channel_name:
                logger.debug(
                    "[djust] server_push on %s skipped — own self-broadcast (#1677)",
                    self.view_instance.__class__.__name__,
                )
                return

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
                    # Apply via safe_setattr — the same guard every other
                    # state-restore sink uses (snapshot restore at ~:2311,
                    # time_travel.py:276, mixins/request.py). A channel-layer
                    # attacker (the framework's own stated threat model, see the
                    # restricted handler path just below) must NOT be able to
                    # overwrite dunders (__class__/__init__), framework internals
                    # (_framework_attrs/_components/_rust_view), or private `_`
                    # state via mass assignment (#F21, CWE-915/CWE-913).
                    from .security import safe_setattr

                    for key, value in state.items():
                        safe_setattr(self.view_instance, key, value, allow_private=False)

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
                    await self._flush_all_pending()
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
                    # Store rendered HTML for on-demand recovery, mirroring
                    # handle_event. Without this, request_html after a failed
                    # broadcast-triggered patch finds _recovery_html=None and
                    # forces a page reload. See #1202.
                    # Consumer-owned wire version + recovery arm in one step
                    # (#1788, #1817): _recovery_version == this frame's version.
                    wire_version = self._next_version_armed(html)
                    await self._send_update(
                        patches=patches,
                        version=wire_version,
                        broadcast=True,
                        source="broadcast",
                    )
                else:
                    # Even if no patches, flush any push_events and flash messages
                    await self._flush_all_pending()
            finally:
                self._render_lock.release()

        except Exception as e:
            logger.exception("Error in server_push: %s", e)

    async def client_push_event(self, event: Dict[str, Any]) -> None:
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

    async def db_notify(self, event: Dict[str, Any]) -> None:
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
                    await self._flush_all_pending()
                    await self._send_noop()
                    return

                if hasattr(self.view_instance, "_sync_state_to_rust"):
                    await sync_to_async(self.view_instance._sync_state_to_rust)()

                html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

                if patches is not None:
                    if isinstance(patches, str):
                        patches = fast_json_loads(patches)
                    # Render-send: arm recovery so _recovery_version tracks this
                    # db_notify broadcast's version (#1817), mirroring server_push.
                    # ``html`` is the pre-strip render from render_with_diff() above.
                    await self._send_update(
                        patches=patches,
                        version=self._next_version_armed(html),
                        broadcast=True,
                        source="broadcast",
                    )
                else:
                    await self._flush_all_pending()

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

    async def _run_tick(self, interval_ms: int) -> None:
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
                        # Honor _force_full_html (set by set_changed_keys(), #1981)
                        # like the event paths do (runtime.py / handle_event) — an
                        # in-place mutation inside handle_tick is invisible to the
                        # snapshot, so without this guard the hatch would be
                        # silently dropped on the tick path (#1646 parallel-path).
                        post_assigns = _snapshot_assigns(self.view_instance)
                        if pre_assigns == post_assigns and not getattr(
                            self.view_instance, "_force_full_html", False
                        ):
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

                        # Consume the force flag (one render per
                        # set_changed_keys()/_force_full_html, #1981) — mirrors
                        # the runtime's reset in _render_and_send.
                        if getattr(self.view_instance, "_force_full_html", False):
                            self.view_instance._force_full_html = False

                        if patches is not None:
                            if isinstance(patches, str):
                                patches = fast_json_loads(patches)
                            # Render-send: arm recovery so _recovery_version
                            # tracks this tick's version (#1817). ``html`` is the
                            # pre-strip render from render_with_diff() above.
                            await self._send_update(
                                patches=patches,
                                version=self._next_version_armed(html),
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
    async def broadcast_reload(cls, file_path: str) -> None:
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
    def register(cls, path: str, view_class: type) -> None:
        """Register a LiveView route"""
        cls._routes[path] = view_class

    @classmethod
    def get_view(cls, path: str) -> Optional[type]:
        """Get the view class for a path"""
        return cls._routes.get(path)
