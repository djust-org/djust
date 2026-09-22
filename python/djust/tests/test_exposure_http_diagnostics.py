"""ADR-038 E1: the HTTP-POST event path's failure response and log.

``RequestMixin.post`` built ``f"...: {type(e).__name__}: {str(e)}"``, logged it
with ``exc_info``, and under ``DEBUG`` returned it to the client together with
``traceback.format_exc()`` and the posted ``params``. The HTTP path serves
explicit views (it has ``legacy_exposure`` branches throughout), so an explicit
view's exception text reached both the log and the response.
"""

import json
import logging

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore

from djust import LiveView, event_handler
from djust.decorators import state


class HTTPFailureView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ count }}</span></div>"
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def explode(self):
        raise ValueError("HTTP_EVENT_SENTINEL")


def _request(rf, session, method="get"):
    if method == "post":
        result = rf.post(
            "/fail/", {"event": "explode", "params": {}}, content_type="application/json"
        )
    else:
        result = rf.get("/fail/")
    result.session = session
    result.user = AnonymousUser()
    result.tenant = None
    return result


@pytest.mark.django_db
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
def test_http_event_failure_is_value_free_for_explicit_views(
    monkeypatch, rf, settings, caplog, policy
):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(HTTPFailureView, "exposure_policy", policy)
    settings.DEBUG = True
    session = SessionStore()
    assert HTTPFailureView.as_view()(_request(rf, session)).status_code == 200

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        response = HTTPFailureView.as_view()(
            _request(rf, SessionStore(session.session_key), "post")
        )
    body = response.content.decode()
    assert response.status_code == 500, body

    if policy == "legacy":
        # Unchanged legacy behaviour: DEBUG detail in the response, detail in the log.
        assert "HTTP_EVENT_SENTINEL" in json.loads(body)["error"]
        assert "HTTP_EVENT_SENTINEL" in caplog.text
    else:
        assert "HTTP_EVENT_SENTINEL" not in body
        assert "traceback" not in json.loads(body)
        assert "HTTP_EVENT_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
