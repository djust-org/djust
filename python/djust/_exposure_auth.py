"""Fresh request authorization for the staged explicit runtime policy.

Transport adapters supply trusted requests, never client event parameters.
Django's get_user verifies the session backend and authentication hash:
https://docs.djangoproject.com/en/5.2/ref/contrib/auth/#django.contrib.auth.get_user
"""

from copy import copy
from typing import Any

from ._exposure import ExposureError
from ._exposure_sessions import StateBinding, _SERVER_SESSION_TYPES, request_binding


def fresh_socket_request(view: Any) -> Any:
    """Reload server session/auth instead of reusing socket-scope caches."""
    from django.contrib.auth import get_user

    mounted = view._djust_mount_request
    session = mounted.session
    if type(session) not in _SERVER_SESSION_TYPES or not session.session_key:
        raise ExposureError("Explicit events require a supported live server session")
    request = copy(mounted)
    request.session = type(session)(session.session_key)
    # Force a storage read even for anonymous users. Missing/expired sessions
    # clear the key and may not silently become a new anonymous identity.
    request.session.get("_auth_user_id")
    if request.session.session_key != session.session_key:
        raise ExposureError("Explicit event session is no longer valid")
    request.user = get_user(request)
    return request


def authorize_event(view: Any, request: Any, binding: StateBinding) -> Any:
    """Authorize a fresh request against immutable mounted identity.

    The SSE endpoint path is not the view route. Copy routing metadata from the
    trusted mount, while retaining the current request's principal/session/host.
    TenantMixin resolvers run again instead of using their mount-time cache.
    Exceptions deliberately propagate to the runtime's static denial envelope.
    """
    from .auth.core import check_view_auth

    if request is None or binding is None:
        raise ExposureError("Explicit events require a fresh trusted request")
    request = copy(request)
    session = getattr(request, "session", None)
    if type(session) not in _SERVER_SESSION_TYPES:
        raise ExposureError("Explicit events require a supported server session")
    session.get("_auth_user_id")
    mounted = view._djust_mount_request
    request.path = mounted.path
    request.path_info = mounted.path_info
    request.resolver_match = mounted.resolver_match
    resolver = getattr(view, "resolve_tenant", None)
    if callable(resolver):
        request.tenant = resolver(request)
    if request_binding(request) != binding:
        raise ExposureError("Explicit event identity changed; remount required")
    # Django AccessMixin hooks consult self.request, not only their argument.
    previous = view.request
    view.request = request
    try:
        if check_view_auth(view, request) is not None:
            raise ExposureError("Explicit event authorization denied")
    except Exception:
        view.request = previous
        raise
    return request
