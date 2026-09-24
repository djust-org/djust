"""Actual HTTP pipeline with only the staged construction gate bypassed.

No persistence, context, auth or rendering helpers are mocked. Explicit views
remain unavailable to applications until all exporter integration gates pass.
"""

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust._exposure import ExposureError
from djust._exposure_sessions import request_binding, server_state_adapter
from djust.decorators import state


class HTTPView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")
    server_note = state("SERVER_SENTINEL", persist="server")
    transient = state("TRANSIENT_SENTINEL")

    def mount(self, request, **kwargs):
        self.count = 5
        self.service = object()
        self.public_note = "PUBLIC_SENTINEL"
        self._private_note = "PRIVATE_SENTINEL"

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, render_only="RENDER_SENTINEL", **kwargs)

    @event_handler()
    def increment(self):
        assert self.service is not None
        self.count += 1


@pytest.fixture
def staged(monkeypatch, db):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def request(rf, session, method="get", path="/explicit/", user=None):
    if method == "post":
        result = rf.post(
            path, {"event": "increment", "params": {}}, content_type="application/json"
        )
    else:
        result = rf.get(path)
    result.session = session
    result.user = user if user is not None else AnonymousUser()
    result.tenant = None
    return result


def test_get_then_post_uses_server_projection_not_render_context(staged, rf):
    session = SessionStore()
    first = request(rf, session)
    response = HTTPView.as_view()(first)
    assert response.status_code == 200
    assert b"SENTINEL" not in response.content
    adapter = server_state_adapter(HTTPView(), first)
    stored = SessionStore(session.session_key).load()
    assert stored[adapter.key]["state"]["values"] == {"count": 5, "server_note": "SERVER_SENTINEL"}
    encoded = json.dumps(stored)
    for name in ("PUBLIC", "PRIVATE", "RENDER", "TRANSIENT"):
        assert name + "_SENTINEL" not in encoded
    assert "liveview_/explicit/" not in stored
    assert "liveview_/explicit/__private" not in stored

    second = request(rf, SessionStore(session.session_key), "post")
    response = HTTPView.as_view()(second)
    assert response.status_code == 200, response.content
    assert b"SENTINEL" not in response.content
    assert server_state_adapter(HTTPView(), second).load()["count"] == 6
    # A second freshly constructed HTTP view must use 6, not mount's default 5.
    # The first POST alone would also pass if restoration were accidentally absent.
    third = request(rf, SessionStore(session.session_key), "post")
    response = HTTPView.as_view()(third)
    assert response.status_code == 200, response.content
    assert server_state_adapter(HTTPView(), third).load()["count"] == 7


@pytest.mark.parametrize("mutation", ["schema", "extra", "user", "tenant", "legacy"])
def test_invalid_stored_state_remounts_without_hydrating_unknown_fields(
    staged, rf, mutation, django_user_model
):
    session = SessionStore()
    first = request(rf, session)
    assert HTTPView.as_view()(first).status_code == 200
    adapter = server_state_adapter(HTTPView(), first)
    payload = session[adapter.key]
    payload["state"]["values"]["count"] = 90
    if mutation == "schema":
        payload["state"]["schema"] = "old"
    elif mutation == "extra":
        payload["state"]["values"]["public_note"] = "RESTORE_SENTINEL"
    elif mutation == "legacy":
        del session[adapter.key]
        session["liveview_/explicit/"] = {"count": 90, "public_note": "RESTORE_SENTINEL"}
        session["liveview_/explicit/__private"] = {"_private_note": "RESTORE_SENTINEL"}
    session.save()
    second = request(rf, SessionStore(session.session_key), "post")
    if mutation == "user":
        second.user = django_user_model.objects.create_user(username="new-user")
    elif mutation == "tenant":
        from djust.tenants.resolvers import TenantInfo

        second.tenant = TenantInfo("new-tenant")
    response = HTTPView.as_view()(second)
    assert response.status_code == 200, response.content
    assert b"RESTORE_SENTINEL" not in response.content
    assert server_state_adapter(HTTPView(), second).load()["count"] == 6


def test_denied_request_never_loads_or_mutates_state(staged, rf, monkeypatch):
    session = SessionStore()
    first = request(rf, session)
    assert HTTPView.as_view()(first).status_code == 200
    before = json.dumps(session.load(), sort_keys=True)
    monkeypatch.setattr(HTTPView, "check_permissions", lambda self, request: False, raising=False)
    second = request(rf, SessionStore(session.session_key), "post")
    response = HTTPView.as_view()(second)
    assert response.status_code == 403
    assert json.dumps(session.load(), sort_keys=True) == before


def test_cookie_backend_rejects_server_persistence(staged, rf):
    from django.contrib.sessions.backends.signed_cookies import SessionStore as CookieStore

    session = CookieStore()
    with pytest.raises(ExposureError, match="server-side"):
        HTTPView.as_view()(request(rf, session))
    assert "SENTINEL" not in json.dumps(dict(session.items()))


def test_binding_uses_middleware_identity_not_request_values(staged, rf, django_user_model):
    from djust.tenants.resolvers import TenantInfo

    user = django_user_model.objects.create_user(username="one")
    session = SessionStore()
    session.create()
    req = request(rf, session, user=user)
    req.tenant = TenantInfo("tenant-a")
    binding = request_binding(req)
    assert binding.user == "user:int:" + str(user.pk)
    assert binding.tenant == "tenant:str:tenant-a"
    assert binding.view == "/explicit/"
    assert binding.session == session.session_key
    req.tenant = TenantInfo("tenant-b")
    assert request_binding(req).digest != binding.digest
    req.user = AnonymousUser()
    assert request_binding(req).digest != binding.digest


def test_missing_auth_or_configured_tenant_resolution_fails_closed(staged, rf, settings):
    req = request(rf, SessionStore())
    req.session.create()
    del req.user
    with pytest.raises(ExposureError, match="authentication"):
        request_binding(req)
    req.user = AnonymousUser()
    del req.tenant
    settings.DJUST_TENANTS = {"REQUIRED": True}
    with pytest.raises(ExposureError, match="tenant resolution"):
        request_binding(req)
