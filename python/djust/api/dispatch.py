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
    """
    instance = view_cls()
    instance.request = request
    api_mount = getattr(instance, "api_mount", None)
    if callable(api_mount) and api_mount is not getattr(view_cls, "mount", None):
        _call_possibly_async(api_mount, request)
    else:
        mount = getattr(instance, "mount", None)
        if callable(mount):
            _call_possibly_async(mount, request)
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
    try:
        view = _instantiate_view(view_cls, request)
    except PermissionDenied as exc:
        return api_error(403, "permission_denied", str(exc) or "Permission denied")
    except Exception:
        logger.exception("djust API: view instantiation failed for %s", view_slug)
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
        logger.exception("djust API handler raised: slug=%s handler=%s", view_slug, handler_name)
        return api_error(500, "handler_error", "Handler raised an unexpected error")

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
