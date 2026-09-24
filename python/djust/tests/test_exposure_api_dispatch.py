"""ADR-038 E1: the ADR-008 HTTP API's ``assigns`` diff is an automatic sink.

``dispatch_api`` answers ``{"result": <return>, "assigns": <diff>}``. ``result``
is the handler's own explicit response (outside the automatic export contract,
ADR-038 D6). ``assigns`` is automatic: ``_public_assigns_snapshot_diff`` returned
every public attribute the handler changed, with no policy check — so an
explicit view's undeclared or server-only values reached the HTTP client. For a
nonlegacy view it must carry only declared ``client=True`` fields, the same
projection ``get_state`` uses.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust.api.dispatch import dispatch_api
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler, state
from djust.live_view import LiveView

pytestmark = pytest.mark.django_db


class APIExposureView(LiveView):
    api_name = "exposure.api"
    exposure_policy = "legacy"
    template = "<div dj-root>{{ visible }}</div>"
    visible = state(0, client=True)
    server_note = state("initial", persist="server")

    @event_handler(expose_api=True)
    def go(self, **kwargs):
        self.visible = 1
        self.server_note = "SERVER_ONLY_SENTINEL"
        # An ordinary attribute: available to the application, never exported.
        self.api_token = "UNDECLARED_ASSIGN_SENTINEL"
        return {"ok": True}


@pytest.fixture(autouse=True)
def _registry():
    reset_registry()
    register_api_view("exposure.api", APIExposureView)
    yield
    reset_registry()


def _request():
    request = RequestFactory().post(
        "/djust/api/exposure.api/go/", data=b"{}", content_type="application/json"
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = get_user_model().objects.create_user(username="api-exposure", password="pw")
    request._dont_enforce_csrf_checks = True
    return request


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_api_assigns_diff_carries_only_client_fields_for_explicit_views(monkeypatch, policy):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(APIExposureView, "exposure_policy", policy)

    response = dispatch_api(_request(), "exposure.api", "go")
    body = json.loads(response.content)
    assert response.status_code == 200, body
    assert body["result"] == {"ok": True}  # the handler's own response (D6)

    if policy == "legacy":
        # Unchanged legacy behaviour: every changed public attribute.
        assert body["assigns"]["api_token"] == "UNDECLARED_ASSIGN_SENTINEL"
    else:
        assert body["assigns"] == {"visible": 1}
        assert "SENTINEL" not in response.content.decode()


class APIFailureView(LiveView):
    api_name = "exposure.api.fail"
    exposure_policy = "legacy"
    template = "<div dj-root></div>"

    @event_handler(expose_api=True)
    def boom(self, **kwargs):
        raise ValueError("API_HANDLER_SENTINEL")


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_api_handler_failure_log_is_value_free_for_explicit_views(monkeypatch, caplog, policy):
    """The client already gets a generic 500; the log carried the handler's
    exception and traceback for any policy."""
    import logging

    register_api_view("exposure.api.fail", APIFailureView)
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(APIFailureView, "exposure_policy", policy)
    with caplog.at_level(logging.DEBUG):
        response = dispatch_api(_request(), "exposure.api.fail", "boom")
    assert response.status_code == 500
    assert "API_HANDLER_SENTINEL" not in response.content.decode()
    if policy == "legacy":
        assert "djust API handler raised" in caplog.text
        assert "API_HANDLER_SENTINEL" in caplog.text
    else:
        assert "API_HANDLER_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
