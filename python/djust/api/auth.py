"""Pluggable authentication contract for the djust HTTP API dispatch view.

The dispatch view tries each class in ``view.api_auth_classes`` (or
``[SessionAuth]`` by default) in order. The first class whose
``authenticate(request)`` returns a non-None user wins. CSRF enforcement is
skipped only if the winning auth class sets ``csrf_exempt = True``; session-cookie
auth keeps CSRF on, header/token auth can opt out.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class BaseAuth(Protocol):
    """Authentication class contract.

    Implementations must set ``csrf_exempt`` (True disables Django CSRF when
    this auth class is the one that accepted the request) and implement
    ``authenticate(request)`` returning the Django user or None.
    """

    csrf_exempt: bool

    def authenticate(self, request) -> Optional[object]:  # pragma: no cover - protocol
        ...


class SessionAuth:
    """Django session auth — accepts an already-authenticated request.

    The session middleware must have run before the dispatch view (which is
    true for any standard Django URL). This class does NOT set CSRF-exempt;
    POSTs with a session cookie must present a valid CSRF token.
    """

    csrf_exempt = False

    def authenticate(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return user
        return None


def resolve_auth_classes(view_cls) -> list:
    """Return instantiated auth classes for a view class.

    Reads ``view_cls.api_auth_classes`` if set, else defaults to ``[SessionAuth]``.
    Each entry may be a class (instantiated here with no args) or an instance.
    """
    raw = getattr(view_cls, "api_auth_classes", None) or [SessionAuth]
    result = []
    for item in raw:
        if isinstance(item, type):
            result.append(item())
        else:
            result.append(item)
    return result
