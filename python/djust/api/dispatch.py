"""HTTP API dispatch view for ``@event_handler(expose_api=True)`` handlers (ADR-008).

The dispatch view is a transport adapter over the existing handler pipeline. It
reuses ``validate_handler_params``, ``check_view_auth``, ``check_handler_permission``,
``_snapshot_assigns``/``_compute_changed_keys``, and ``DjangoJSONEncoder`` — the
exact same safety checks and serialization the WebSocket path runs.

Responses:
  200: ``{"result": <return>, "assigns": {<changed public attrs>}}``
  400: validation or JSON parse error
  401: unauthenticated (no auth class accepted the request)
  403: CSRF failed or permission denied
  404: unknown view slug, unknown handler, or handler not ``expose_api=True``
  429: rate limit exceeded
  500: handler raised an unexpected exception (logged server-side; no leak)
"""

from __future__ import annotations

import inspect
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, Tuple

from asgiref.sync import async_to_sync
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, HttpResponse, HttpResponseBase, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from djust._log_utils import sanitize_for_log
from djust.api.auth import resolve_auth_classes
from djust.api.errors import api_error
from djust.api.registry import resolve_api_view
from djust.auth.core import check_handler_permission, check_view_auth
from djust.rate_limit import TokenBucket, get_rate_limit_settings
from djust.validation import validate_handler_params
from djust.websocket import _compute_changed_keys, _snapshot_assigns

logger = logging.getLogger(__name__)

_RATE_BUCKET_CAP = 10_000  # LRU cap: evict oldest entry when full to bound memory.
_rate_buckets: "OrderedDict[Tuple[str, str], TokenBucket]" = OrderedDict()
_rate_buckets_lock = threading.Lock()


def _caller_key(request: HttpRequest) -> str:
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.pk}"
    # Fall back to remote addr. Note: this is coarse — behind a proxy the framework
    # user's ``REMOTE_ADDR`` should already reflect the caller via their proxy middleware.
    return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"


def _rate_limit_check(request: HttpRequest, handler_name: str, handler) -> bool:
    """Token-bucket check honoring the handler's ``@rate_limit`` settings.

    Buckets are process-level and keyed on ``(caller, handler_name)``. The
    caller key is the authenticated user's PK when available, otherwise the
    remote IP.

    Note on transport parity: the HTTP and WebSocket transports **use the same
    ``@rate_limit`` settings but separate bucket storage** — the WS path's
    ``ConnectionRateLimiter`` is per-connection, this dict is process-level.
    A caller using both transports consumes from both independently. If you
    need a unified budget across transports, set a stricter ``rate`` — or wait
    for the shared-bucket refactor tracked alongside ADR-008.

    Returns True if allowed, False if rate-limited.
    """
    settings = get_rate_limit_settings(handler)
    if settings is None:
        return True
    key = (_caller_key(request), handler_name)
    with _rate_buckets_lock:
        bucket = _rate_buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(rate=settings["rate"], burst=settings["burst"])
            _rate_buckets[key] = bucket
            # LRU eviction: cap the dict so a hostile caller cycling identities
            # cannot inflate memory without bound.
            while len(_rate_buckets) > _RATE_BUCKET_CAP:
                _rate_buckets.popitem(last=False)
        else:
            # Touch for LRU order.
            _rate_buckets.move_to_end(key)
    return bucket.consume()


def reset_rate_buckets() -> None:
    """Clear rate-limit state — used by tests."""
    with _rate_buckets_lock:
        _rate_buckets.clear()


def _is_exposed(handler) -> bool:
    meta = getattr(handler, "_djust_decorators", None)
    return bool(meta and meta.get("event_handler", {}).get("expose_api"))


def _instantiate_view(view_cls, request: HttpRequest):
    """Create a fresh view instance for a single HTTP API call.

    Mirrors the WS consumer's setup: set ``request``, call ``mount()`` (or the
    lighter-weight ``api_mount()`` hook if the view overrides it).

    ``self._api_request`` is set to ``True`` BEFORE ``mount()`` runs so code
    in ``mount`` can branch on transport if needed. WS never sets this flag.
    """
    instance = view_cls()
    instance.request = request
    # Set the transport flag before mount so mount() can branch on it.
    instance._api_request = True
    api_mount = getattr(instance, "api_mount", None)
    if callable(api_mount) and api_mount is not getattr(view_cls, "mount", None):
        _call_possibly_async(api_mount, request)
    else:
        mount = getattr(instance, "mount", None)
        if callable(mount):
            _call_possibly_async(mount, request)
    # Dirty-tracking baseline (v0.5.1) so ``is_dirty`` / ``changed_fields``
    # reflect mutations made during the handler run.
    if hasattr(instance, "_capture_dirty_baseline"):
        instance._capture_dirty_baseline()
    return instance


def _call_possibly_async(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    if inspect.iscoroutine(result):
        return async_to_sync(_await)(result)
    return result


async def _await(coro):
    return await coro


def _public_assigns_snapshot_diff(view_instance, changed_keys):
    """Build the JSON-safe assigns diff from changed keys."""
    diff: Dict[str, Any] = {}
    for key in changed_keys:
        if key.startswith("_"):
            continue
        try:
            diff[key] = getattr(view_instance, key)
        except AttributeError:
            continue
    return diff


def _resolve_serializer(spec, view):
    """Turn a ``serialize=`` spec into a callable; return None if unset.

    Raises ``TypeError`` if ``spec`` is a string that doesn't name a callable
    attribute on ``view``, or if ``spec`` is neither None, callable, nor str.
    """
    if spec is None:
        return None
    if isinstance(spec, str):
        method = getattr(view, spec, None)
        if not callable(method):
            raise TypeError(f"serialize={spec!r} names no callable method on {type(view).__name__}")
        return method
    if callable(spec):
        return spec
    raise TypeError(
        f"serialize must be None, a callable, or a method-name string; got {type(spec).__name__}"
    )


def _call_serializer(fn, view, return_value):
    """Call ``fn`` with arity-appropriate args; await if the result is a coroutine.

    Two shapes are supported:

    - **Bound method** (e.g. ``view.api_response`` or ``view.serialize_claims``
      resolved from a string name): ``self`` is already bound, so the remaining
      positional params are inspected. 0 positional → called as ``fn()``;
      1 positional → called as ``fn(return_value)``.
    - **Plain callable** (lambda / unbound function): 0 positional → ``fn()``;
      1 positional → ``fn(view)``; 2+ positional → ``fn(view, return_value)``.
    """
    sig = inspect.signature(fn)
    positional = [
        p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    n = len(positional)

    if inspect.ismethod(fn):
        # Bound method: self is implicit; first positional maps to return_value.
        if n >= 1:
            call_args: tuple = (return_value,)
        else:
            call_args = ()
    else:
        # Plain callable: first positional maps to view, second to return_value.
        if n >= 2:
            call_args = (view, return_value)
        elif n == 1:
            call_args = (view,)
        else:
            call_args = ()

    result = fn(*call_args)
    if inspect.iscoroutine(result):
        return async_to_sync(_await)(result)
    return result


def _apply_response_transform(view, handler, return_value):
    """Resolve per-handler ``serialize=`` or view-level ``api_response()``; fall through otherwise.

    Resolution order (first match wins):
    1. ``@event_handler(expose_api=True, serialize=...)`` on the handler.
    2. ``api_response()`` method on the view (convention — DRY for shared shapes).
    3. Pass-through: whatever the handler returned.
    """
    metadata = getattr(handler, "_djust_decorators", {}).get("event_handler", {})
    spec = metadata.get("serialize")

    if spec is not None:
        serializer = _resolve_serializer(spec, view)
        return _call_serializer(serializer, view, return_value)

    api_response = getattr(view, "api_response", None)
    if callable(api_response):
        return _call_serializer(api_response, view, return_value)
    if api_response is not None:
        logger.debug(
            "djust API: %s.api_response is not callable; falling through to handler return",
            type(view).__name__,
        )

    return return_value


@method_decorator(csrf_exempt, name="dispatch")
class DjustAPIDispatchView(View):
    """Single dispatch view for every ``@event_handler(expose_api=True)`` endpoint.

    Routed via :func:`djust.api.urls.api_patterns` at
    ``POST /djust/api/<view_slug>/<handler_name>/``. The ``csrf_exempt`` wrapper
    is applied because CSRF is evaluated *inside* dispatch, conditionally on the
    winning auth class's ``csrf_exempt`` flag.
    """

    http_method_names = ["post", "options"]

    def post(self, request: HttpRequest, view_slug: str, handler_name: str) -> HttpResponseBase:
        return dispatch_api(request, view_slug, handler_name)


def dispatch_api(request: HttpRequest, view_slug: str, handler_name: str) -> HttpResponseBase:
    """Functional dispatch entry point (used by both the CBV and tests)."""
    # 1. Resolve view class by slug.
    view_cls = resolve_api_view(view_slug)
    if view_cls is None:
        return api_error(404, "unknown_view", f"No djust API view registered for {view_slug!r}")

    # 2. Authenticate.
    auth_classes = resolve_auth_classes(view_cls)
    user = None
    winning_auth = None
    for auth in auth_classes:
        candidate = auth.authenticate(request)
        if candidate is not None:
            user = candidate
            winning_auth = auth
            break
    if user is None:
        return api_error(401, "unauthenticated", "Authentication required")
    request.user = user

    # 3. CSRF — only when the winning auth class is NOT csrf-exempt.
    if not getattr(winning_auth, "csrf_exempt", False):
        csrf_resp = _enforce_csrf(request)
        if csrf_resp is not None:
            return csrf_resp

    # 4. Parse JSON body.
    body: Dict[str, Any]
    if not request.body:
        body = {}
    else:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return api_error(400, "invalid_json", f"Malformed JSON body: {exc}")
    if not isinstance(body, dict):
        return api_error(400, "invalid_json", "Request body must be a JSON object")

    # 5. Instantiate the view and run mount/api_mount.
    # ``_instantiate_view`` sets ``instance._api_request = True`` before
    # mount so mount() can branch on transport if needed.
    try:
        view = _instantiate_view(view_cls, request)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    except Exception:
        logger.exception("djust API: view instantiation failed for %s", sanitize_for_log(view_slug))
        return api_error(500, "mount_failed", "View initialization failed")

    # 6. View-level auth (login_required + @permission_required on the class).
    try:
        redirect_url = check_view_auth(view, request)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    if redirect_url:
        # View demands login — in HTTP land, this is 401 not a redirect.
        return api_error(401, "login_required", "Authentication required")

    # 7. Look up handler + verify opt-in.
    handler = getattr(view, handler_name, None)
    if handler is None or not callable(handler):
        return api_error(404, "unknown_handler", f"No handler named {handler_name!r}")
    if not _is_exposed(handler):
        return api_error(
            404,
            "handler_not_exposed",
            f"Handler {handler_name!r} is not exposed via HTTP API",
        )

    # 8. Handler-level @permission_required.
    if not check_handler_permission(handler, request):
        return api_error(403, "permission_denied", "Permission denied for this handler")

    # 9. Rate limit (shared bucket key with WS, per caller).
    if not _rate_limit_check(request, handler_name, handler):
        return api_error(429, "rate_limited", "Rate limit exceeded for this handler")

    # 10. Parameter validation + coercion.
    validation = validate_handler_params(handler, body, handler_name)
    if not validation["valid"]:
        return api_error(
            400,
            "invalid_params",
            validation.get("error") or "Parameter validation failed",
            details={
                "expected": validation.get("expected", []),
                "provided": validation.get("provided", []),
                "type_errors": validation.get("type_errors") or [],
            },
        )
    coerced = validation["coerced_params"]

    # 11. Snapshot pre-state.
    pre = _snapshot_assigns(view)

    # 12. Invoke the handler.
    try:
        return_value = _call_possibly_async(handler, **coerced)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    except Exception:
        logger.exception(
            "djust API handler raised: slug=%s handler=%s",
            sanitize_for_log(view_slug),
            sanitize_for_log(handler_name),
        )
        return api_error(500, "handler_error", "Handler raised an unexpected error")

    # 12b. Apply per-handler ``serialize=`` override or ``api_response()``
    # convention. Resolution order: serialize= > api_response() > passthrough.
    # Both only run on the HTTP path — the WS consumer never reaches this code.
    try:
        return_value = _apply_response_transform(view, handler, return_value)
    except PermissionDenied as exc:
        # Mirror the handler-invocation block: a PermissionDenied raised from
        # api_response() / serialize= must surface as 403, not 500.
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    except TypeError:
        logger.exception(
            "djust API serialize= misconfigured: slug=%s handler=%s",
            sanitize_for_log(view_slug),
            sanitize_for_log(handler_name),
        )
        return api_error(
            500,
            "serialize_error",
            "Response transform raised an unexpected error",
        )
    except Exception:
        logger.exception(
            "djust API response transform raised: slug=%s handler=%s",
            sanitize_for_log(view_slug),
            sanitize_for_log(handler_name),
        )
        return api_error(500, "serialize_error", "Response transform raised an unexpected error")

    # 13. Snapshot post-state and compute diff.
    post = _snapshot_assigns(view)
    changed = _compute_changed_keys(pre, post)
    assigns_diff = _public_assigns_snapshot_diff(view, changed)

    return JsonResponse(
        {"result": return_value, "assigns": assigns_diff},
        encoder=DjangoJSONEncoder,
    )


def _enforce_csrf(request: HttpRequest):
    """Run Django's CSRF middleware for this request.

    Returns a 403 JsonResponse on failure, or None on success.
    """
    middleware = CsrfViewMiddleware(lambda r: HttpResponse())
    # ``process_view`` is what CsrfViewMiddleware actually checks inside; it returns
    # None on success and an HttpResponseForbidden on failure.
    response = middleware.process_view(request, None, (), {})
    if response is not None:
        return api_error(403, "csrf_failed", "CSRF verification failed")
    return None


# ---------------------------------------------------------------------------
# @server_function dispatch (v0.7.0) — same-origin browser RPC.
# ---------------------------------------------------------------------------
# Responses:
#   200: ``{"result": <return>}`` — JSON-serialized via DjangoJSONEncoder
#   400: validation or JSON parse error
#   401: request.user is anonymous (session cookie missing / expired)
#   403: CSRF failed or @permission_required denied
#   404: unknown view slug, unknown function name, or function not decorated
#        with @server_function
#   429: rate limit exceeded
#   500: function raised an unexpected exception (logged; not leaked)


def _is_server_function(fn) -> bool:
    meta = getattr(fn, "_djust_decorators", None)
    return bool(meta and meta.get("server_function"))


@method_decorator(csrf_exempt, name="dispatch")
class DjustServerFunctionView(View):
    """Single dispatch view for every ``@server_function`` method.

    Routed at ``POST /djust/api/call/<view_slug>/<function_name>/``. CSRF is
    evaluated *inside* :func:`dispatch_server_function` unconditionally —
    server functions are session-cookie-only; there is no auth-class
    opt-out.
    """

    http_method_names = ["post", "options"]

    def post(self, request: HttpRequest, view_slug: str, function_name: str) -> HttpResponseBase:
        return dispatch_server_function(request, view_slug, function_name)


def dispatch_server_function(
    request: HttpRequest, view_slug: str, function_name: str
) -> HttpResponseBase:
    """Functional dispatch entry point for ``@server_function`` RPC calls."""
    # 1. Resolve view class by slug — reuses the ADR-008 registry which now
    # also indexes views that only expose @server_function methods.
    view_cls = resolve_api_view(view_slug)
    if view_cls is None:
        return api_error(404, "unknown_view", f"No djust view registered for {view_slug!r}")

    # 2. Auth — session cookie only. No auth classes, no anonymous callers.
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return api_error(401, "unauthenticated", "Authentication required")

    # 3. CSRF — always enforced. No opt-out (server functions are same-origin).
    csrf_resp = _enforce_csrf(request)
    if csrf_resp is not None:
        return csrf_resp

    # 4. Parse JSON body. Only the wrapped shape ``{"params": {...}}`` is
    # accepted — the JS client helper at
    # ``static/djust/src/48-server-functions.js`` always sends this form.
    # Rejecting flat bodies and ``{"params": {...}, "other": ...}`` shapes
    # removes the ambiguity where a caller's own field literally named
    # ``params`` would be silently unwrapped (dropping every sibling key).
    if not request.body:
        params: Dict[str, Any] = {}
    else:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return api_error(400, "invalid_json", f"Malformed JSON body: {exc}")
        if not isinstance(body, dict):
            return api_error(400, "invalid_json", "Request body must be a JSON object")
        if not body:
            params = {}
        elif set(body.keys()) == {"params"} and isinstance(body["params"], dict):
            params = body["params"]
        else:
            return api_error(
                400,
                "invalid_body",
                'Request body must be a JSON object of the form {"params": {...}}. '
                "Received body with unexpected keys.",
            )

    # 5. Instantiate the view (runs mount / api_mount).
    try:
        view = _instantiate_view(view_cls, request)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    except Exception:
        logger.exception(
            "djust server_function: view init failed for %s", sanitize_for_log(view_slug)
        )
        return api_error(500, "mount_failed", "View initialization failed")

    # 6. View-level auth (login_required + @permission_required on the class).
    try:
        redirect_url = check_view_auth(view, request)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    if redirect_url:
        return api_error(401, "login_required", "Authentication required")

    # 7. Resolve the attribute and gate on @server_function metadata.
    fn = getattr(view, function_name, None)
    if fn is None or not callable(fn):
        return api_error(404, "unknown_function", f"No function named {function_name!r}")
    if not _is_server_function(fn):
        return api_error(
            404,
            "not_a_server_function",
            f"{function_name!r} is not decorated with @server_function",
        )

    # 8. Handler-level @permission_required + @rate_limit (shared helpers).
    if not check_handler_permission(fn, request):
        return api_error(403, "permission_denied", "Permission denied")
    if not _rate_limit_check(request, function_name, fn):
        return api_error(429, "rate_limited", "Rate limit exceeded")

    # 9. Parameter validation + coercion (reuses ADR-008 validator).
    validation = validate_handler_params(fn, params, function_name)
    if not validation["valid"]:
        return api_error(
            400,
            "invalid_params",
            validation.get("error") or "Parameter validation failed",
            details={
                "expected": validation.get("expected", []),
                "provided": validation.get("provided", []),
                "type_errors": validation.get("type_errors") or [],
            },
        )
    coerced = validation["coerced_params"]

    # 10. Invoke (supports sync + async def via _call_possibly_async).
    try:
        result = _call_possibly_async(fn, **coerced)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    except Exception:
        logger.exception(
            "djust server_function raised: slug=%s fn=%s",
            sanitize_for_log(view_slug),
            sanitize_for_log(function_name),
        )
        return api_error(500, "function_error", "Function raised an unexpected error")

    # 11. Serialize the return value inside its own try/except so non-JSON-
    # serializable returns (e.g. ``return set()``) surface as the documented
    # 500 ``function_error`` envelope instead of escaping as an unhandled 500
    # ``http_error`` from Django's default exception handler.
    try:
        body = json.dumps({"result": result}, cls=DjangoJSONEncoder)
    except (TypeError, ValueError):
        logger.exception(
            "djust server_function return value not JSON-serializable: slug=%s fn=%s",
            sanitize_for_log(view_slug),
            sanitize_for_log(function_name),
        )
        return api_error(500, "function_error", "Return value is not JSON-serializable")

    return HttpResponse(body, content_type="application/json")
