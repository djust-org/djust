"""
Authorization on the HTTP POST fallback transport.

``RequestMixin.post()`` enforced only the event-name format and the
``@event_handler`` decorator policy — none of the three authorization layers
that ``get()`` and every WS/SSE event path enforce: view-level
``login_required`` / ``permission_required``, handler-level
``@permission_required``, and the ADR-017 object-level check. An
unauthenticated client could therefore invoke a state-mutating
``@event_handler`` on a ``login_required = True`` view with a plain HTTP POST.

``test_http_fallback_auth.py`` is the sibling file and covers the *context*
half of auth (does ``{% if user.is_authenticated %}`` render correctly in the
POST response). It passes whether or not the caller was allowed to invoke the
handler, which is why the hole stayed invisible: the subsystem had tests, and
they tested the other half.

Every denial case below is paired with a control that must still succeed, so an
implementation that simply blocks all POSTs cannot pass this file.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler

# Aliased deliberately: a view that sets the documented
# `permission_required = "app.perm"` class attribute shadows the same-named
# decorator for the whole class body, so `@require_permission(...)` inside such
# a class resolves to the string and raises TypeError. See AuthorizedView.
from djust.decorators import permission_required as require_permission

# Side effects that prove whether a handler body actually executed.
RAN: list = []


# ---------------------------------------------------------------------------
# Test views — one per authorization layer, plus the controls
# ---------------------------------------------------------------------------


class LoginRequiredView(LiveView):
    """Layer 1: view-level ``login_required``."""

    template = "<div dj-root>{{ status }}</div>"
    login_required = True

    @event_handler()
    def flip(self, **kwargs):
        RAN.append("login_required")
        self.status = "flipped"


class ViewPermissionView(LiveView):
    """Layer 1, authenticated but missing the view's permission.

    This is the branch where ``check_view_auth`` *raises* rather than
    returning a redirect URL, so it is asserted separately: an uncaught
    ``PermissionDenied`` would be reported by ``post()``'s broad handler as a
    500 rather than a denial.
    """

    template = "<div dj-root>{{ status }}</div>"
    login_required = True
    permission_required = "auth.view_user"

    @event_handler()
    def flip(self, **kwargs):
        RAN.append("view_permission")
        self.status = "flipped"


class HandlerPermissionView(LiveView):
    """Layer 2: ``@permission_required`` on the handler itself."""

    template = "<div dj-root>{{ status }}</div>"

    @require_permission("auth.view_user")
    @event_handler()
    def flip(self, **kwargs):
        RAN.append("handler_permission")
        self.status = "flipped"


class ObjectPermissionView(LiveView):
    """Layer 3: ADR-017 object-level denial (``has_object_permission`` False)."""

    template = "<div dj-root>{{ status }}</div>"

    def get_object(self):
        return {"secret": True}

    def has_object_permission(self, request, obj):
        return False  # denied for every caller

    @event_handler()
    def flip(self, **kwargs):
        RAN.append("object_permission")
        self.status = "flipped"


class AuthorizedView(LiveView):
    """Control: a caller who satisfies every layer must still get through."""

    template = "<div dj-root>{{ status }}</div>"
    login_required = True
    permission_required = "auth.view_user"

    def get_object(self):
        return {"secret": True}

    def has_object_permission(self, request, obj):
        return True

    @require_permission("auth.view_user")
    @event_handler()
    def flip(self, **kwargs):
        RAN.append("authorized")
        self.status = "flipped"


class OpenView(LiveView):
    """Control: a view with no auth requirements keeps accepting anonymous POSTs.

    This is the documented HTTP-fallback behaviour that
    ``test_http_fallback_auth.py::test_post_anonymous_renders_logged_out_content``
    depends on — the fix must not change it.
    """

    template = "<div dj-root>{{ status }}</div>"

    @event_handler()
    def flip(self, **kwargs):
        RAN.append("open")
        self.status = "flipped"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(path, body, user=None):
    """Build a POST request (JSON body) with a session, as the client sends it."""
    factory = RequestFactory()
    request = factory.post(path, data=json.dumps(body), content_type="application/json")
    request.user = user
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def _anonymous_post(path, body=None):
    from django.contrib.auth.models import AnonymousUser

    return _post(path, body or {"event": "flip", "params": {}}, AnonymousUser())


def _user_with(username, *, permission=None):
    User = get_user_model()
    user, _ = User.objects.get_or_create(username=username, defaults={"is_active": True})
    if permission:
        app, codename = permission.split(".")
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label=app, codename=codename)
        )
    return user


@pytest.fixture(autouse=True)
def _clear_ran():
    RAN.clear()
    yield
    RAN.clear()


# ---------------------------------------------------------------------------
# Denials — each of these executed the handler before the fix
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_post_to_login_required_view_is_denied():
    request = _anonymous_post("/secret/")
    response = LoginRequiredView().post(request)
    assert response.status_code == 403
    assert RAN == [], "the handler executed for an unauthenticated caller"


@pytest.mark.django_db
def test_authenticated_post_missing_view_permission_is_denied_not_500():
    user = _user_with("no-perm", permission=None)
    response = ViewPermissionView().post(_post("/secret/", {"event": "flip", "params": {}}, user))
    assert response.status_code == 403, (
        "check_view_auth raises for an authenticated user lacking the view's "
        "permission; it must surface as a denial, not be reported as a 500"
    )
    assert RAN == []


@pytest.mark.django_db
def test_post_to_handler_requiring_a_permission_is_denied():
    request = _anonymous_post("/secret/")
    response = HandlerPermissionView().post(request)
    assert response.status_code == 403
    assert RAN == [], "the handler executed without holding its @permission_required"


@pytest.mark.django_db
def test_post_denied_by_object_level_permission():
    request = _anonymous_post("/secret/")
    response = ObjectPermissionView().post(request)
    assert response.status_code == 403
    assert RAN == [], "the handler executed for an object the caller may not access"


# ---------------------------------------------------------------------------
# Controls — the fix must not over-block
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_authorized_post_still_runs_the_handler():
    """Every layer satisfied: login, view permission, handler permission, object."""
    user = _user_with("authorized", permission="auth.view_user")
    response = AuthorizedView().post(_post("/ok/", {"event": "flip", "params": {}}, user))
    assert response.status_code == 200, (
        "a fully-authorized caller must still be able to invoke the handler"
    )
    assert RAN == ["authorized"]


@pytest.mark.django_db
def test_anonymous_post_to_a_view_without_auth_requirements_is_still_allowed():
    """The documented HTTP-fallback behaviour for unprotected views is unchanged."""
    request = _anonymous_post("/open/")
    response = OpenView().post(request)
    assert response.status_code == 200
    assert RAN == ["open"]


# ---------------------------------------------------------------------------
# The secondary finding in the same function
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_malformed_json_body_returns_a_controlled_error():
    """A body that is not JSON must not raise UnboundLocalError in the handler.

    ``post()`` referenced ``event_name`` / ``params`` in its ``except`` block,
    but both are assigned inside the ``try`` — so a failure *before* their
    assignment (malformed JSON is the easy one) raised a second exception from
    the error path itself, masking the real cause in the logs. A controlled
    failure returns a JSON error body; an unhandled exception would surface as
    Django's HTML 500 page, so parsing the body as JSON is the discriminator.
    """
    factory = RequestFactory()
    request = factory.post("/secret/", data="{not json", content_type="application/json")
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    response = OpenView().post(request)
    assert response.status_code == 500
    payload = json.loads(response.content)  # raises if we got an HTML error page
    assert "error" in payload
