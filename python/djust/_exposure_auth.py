"""Fresh request authorization for the staged explicit runtime policy.

Transport adapters supply trusted requests, never client event parameters.
Django's get_user verifies the session backend and authentication hash:
https://docs.djangoproject.com/en/5.2/ref/contrib/auth/#django.contrib.auth.get_user
"""

from copy import copy
from typing import Any

from ._exposure import ExposureError
from ._exposure_sessions import StateBinding, _SERVER_SESSION_TYPES, request_binding


def establish_mount_session(request: Any, replacement: str | None = None) -> str | None:
    """Mount fresh when the presented session no longer exists (#3201).

    The browser can present a session cookie whose session the store has lost:
    a cache flush or restart, eviction, expiry or ``clearsessions``. The first
    storage read clears such a key, and the mount's state binding then has no
    session to bind to. Here the mount gets a replacement server session and
    runs as anonymous, since a lost session cannot vouch for the user the
    socket authenticated as at connect time.

    Scope, deliberately narrow:

    - Only a key that was PRESENT and is now missing is replaced. A request
      with no session key at all is left exactly as before (the binding
      refuses it), so a cookieless socket cannot mint sessions.
    - A session the store still has keeps its identity, whoever it belongs
      to. The replacement key is server-generated and never sent to the
      browser, so a client cannot fix it.
    - ``replacement`` is the key this connection already created for the same
      vanished key. It is reused while it still exists, so repeated mount
      frames on one socket do not create one row each.
    - The replacement expires after ``DJUST_SERVER_STATE_MAX_AGE`` (capped by
      ``SESSION_COOKIE_AGE``): no cookie points at it, so it is only useful
      for the life of the socket.

    With cache sessions, Django's ``load()`` also treats a failed cache READ
    as a missing session, so a transient backend read error takes this path
    too. The user's real session survives; this socket mounts anonymous.

    Returns the replacement key when one is in use, otherwise ``None``.
    """
    from django.conf import settings
    from django.contrib.auth import get_user

    from ._exposure_sessions import server_state_max_age

    session = getattr(request, "session", None)
    if type(session) not in _SERVER_SESSION_TYPES:
        return None
    if not session.session_key:
        return None
    # Force the storage read. A missing/expired session clears its key here.
    session.get("_auth_user_id")
    if session.session_key:
        return None
    if replacement:
        reused = type(session)(replacement)
        reused.get("_auth_user_id")
        if reused.session_key == replacement:
            request.session = reused
            request.user = get_user(request)
            return replacement
    session.create()
    session.set_expiry(min(server_state_max_age(), settings.SESSION_COOKIE_AGE))
    session.save()
    request.user = get_user(request)
    key: str | None = session.session_key
    return key


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
