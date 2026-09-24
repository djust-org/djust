"""Regression tests for the observability localhost gate + eval_handler allowlist (#9).

The `_djust/observability/` endpoints expose live cross-session state, tracebacks,
logs, and a method-invocation endpoint. Before the fix the localhost check lived
ONLY in the opt-in `LocalhostOnlyObservabilityMiddleware` (omitted from the docs,
not auto-installed), so with `DEBUG=True` + the middleware absent they were
reachable from any host (e.g. a 0.0.0.0-bound staging box). The fix enforces
localhost IN every view (`views._gate`) and restricts `eval_handler` to
@event_handler-decorated methods.
"""

import pytest
from django.test import RequestFactory, override_settings
from django.urls import include, path

from djust.observability import views
from djust.observability.middleware import (
    TOKEN_ENV_VAR,
    TOKEN_HEADER,
    TOKEN_META_KEY,
    LocalhostOnlyObservabilityMiddleware,
    get_observability_token,
)

from .conftest import observability_request_factory

# Self-contained urlconf so the A031 tests don't depend on the ambient
# ROOT_URLCONF (which other tests override) — `reverse("djust_observability:health")`
# must resolve deterministically regardless of test ordering.
urlpatterns = [path("_djust/observability/", include("djust.observability.urls"))]
_OBS_URLCONF = "djust.tests.test_observability_localhost_gate"


def _req(remote_addr, **extra):
    return observability_request_factory().get(
        "/_djust/observability/health/", REMOTE_ADDR=remote_addr, **extra
    )


# --- in-view localhost gate (the core fix; middleware NOT installed here) ---


@override_settings(DEBUG=True)
def test_non_localhost_blocked_in_view_without_middleware():
    """With DEBUG on and NO middleware, a non-localhost request must be refused
    by the in-view gate (was: 200, served live state)."""
    resp = views.health(_req("8.8.8.8"))
    assert resp.status_code == 404, "non-localhost served by observability without middleware"


@override_settings(DEBUG=True)
def test_localhost_allowed():
    resp = views.health(_req("127.0.0.1"))
    assert resp.status_code == 200
    resp6 = views.health(_req("::1"))
    assert resp6.status_code == 200


@override_settings(DEBUG=False)
def test_debug_off_blocked_even_from_localhost():
    resp = views.health(_req("127.0.0.1"))
    assert resp.status_code == 404


@override_settings(DEBUG=True)
def test_all_endpoints_gate_non_localhost():
    """Every endpoint (not just health) must refuse non-localhost."""
    req = _req("203.0.113.9")
    for fn in (
        views.view_assigns,
        views.last_traceback,
        views.log_tail,
        views.handler_timings,
        views.sql_queries,
        views.reset_view_state,
        views.eval_handler,
    ):
        resp = fn(req)
        assert resp.status_code == 404, f"{fn.__name__} served non-localhost"


# --- eval_handler @event_handler allowlist ---


@override_settings(DEBUG=True)
def test_eval_handler_rejects_non_event_handler(monkeypatch):
    """A public method that is NOT @event_handler must not be invocable, even
    from localhost — eval_handler is introspection, not general RPC."""
    import json

    from djust.decorators import event_handler

    class _V:
        def delete_account(self):  # public, NOT decorated
            self.deleted = True
            return "deleted"

        @event_handler
        def increment(self):
            self.n = getattr(self, "n", 0) + 1

    v = _V()
    monkeypatch.setattr(views, "get_view_for_session", lambda sid: v)

    def _post(handler):
        return observability_request_factory().post(
            "/_djust/observability/eval_handler/?session_id=s1",
            data=json.dumps({"handler_name": handler}),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
        )

    resp = views.eval_handler(_post("delete_account"))
    assert resp.status_code == 403, "eval_handler invoked a non-@event_handler method"
    assert not getattr(v, "deleted", False)

    # a real @event_handler still works
    resp2 = views.eval_handler(_post("increment"))
    assert resp2.status_code == 200
    assert v.n == 1


# --- system check A031 (defense-in-depth nudge for the outer middleware) ---


def _run_a031():
    """Run the configuration checks and return the A031 messages (if any)."""
    from djust.checks.configuration import check_configuration

    return [e for e in check_configuration(None) if getattr(e, "id", "") == "djust.A031"]


@override_settings(DEBUG=True, MIDDLEWARE=[], ROOT_URLCONF=_OBS_URLCONF)
def test_a031_fires_when_obs_wired_without_middleware():
    """URLs wired + DEBUG + middleware absent → A031 WARNING."""
    msgs = _run_a031()
    assert len(msgs) == 1, "A031 did not fire for wired-without-middleware"
    assert msgs[0].id == "djust.A031"


@override_settings(
    DEBUG=True,
    MIDDLEWARE=["djust.observability.middleware.LocalhostOnlyObservabilityMiddleware"],
    ROOT_URLCONF=_OBS_URLCONF,
)
def test_a031_silent_when_middleware_present():
    assert _run_a031() == []


@override_settings(DEBUG=False, MIDDLEWARE=[], ROOT_URLCONF=_OBS_URLCONF)
def test_a031_silent_when_debug_off():
    assert _run_a031() == []


# --- proxied requests and the observability token ---

_PROXY_HEADER_CASES = [
    {"HTTP_X_FORWARDED_FOR": "203.0.113.7"},
    {"HTTP_FORWARDED": "for=203.0.113.7"},
    {"HTTP_X_REAL_IP": "203.0.113.7"},
    {"HTTP_X_FORWARDED_HOST": "example.com"},
    {"HTTP_X_FORWARDED_PROTO": "https"},
]

_ALL_ENDPOINTS = (
    views.health,
    views.view_assigns,
    views.last_traceback,
    views.log_tail,
    views.handler_timings,
    views.sql_queries,
    views.reset_view_state,
    views.eval_handler,
)


@override_settings(DEBUG=True)
@pytest.mark.parametrize("headers", _PROXY_HEADER_CASES)
def test_loopback_peer_with_proxy_header_is_refused(headers):
    """A loopback REMOTE_ADDR with reverse-proxy headers is a relayed request,
    not a local one, even when it carries the token."""
    for fn in _ALL_ENDPOINTS:
        resp = fn(_req("127.0.0.1", **headers))
        assert resp.status_code == 404, f"{fn.__name__} served a proxied request"


@override_settings(DEBUG=True)
def test_loopback_peer_without_token_is_refused():
    """A proxy that adds no headers looks like a local client by address; the
    token is what it cannot supply."""
    req = RequestFactory().get("/_djust/observability/health/", REMOTE_ADDR="127.0.0.1")
    for fn in _ALL_ENDPOINTS:
        assert fn(req).status_code == 404, f"{fn.__name__} served a request without token"


@override_settings(DEBUG=True)
def test_loopback_peer_with_wrong_token_is_refused():
    req = RequestFactory().get(
        "/_djust/observability/health/", REMOTE_ADDR="127.0.0.1", **{TOKEN_META_KEY: "nope"}
    )
    assert views.health(req).status_code == 404


@override_settings(DEBUG=True)
def test_token_follows_secret_key():
    token = get_observability_token()
    assert len(token) == 64
    with override_settings(SECRET_KEY="another-project-secret-key-value-0123456789"):
        assert get_observability_token() != token
        req = RequestFactory().get(
            "/_djust/observability/health/", REMOTE_ADDR="127.0.0.1", **{TOKEN_META_KEY: token}
        )
        assert views.health(req).status_code == 404


@override_settings(DEBUG=True)
def test_token_from_environment(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV_VAR, "local-tooling-token")
    assert get_observability_token() == "local-tooling-token"
    ok = RequestFactory().get(
        "/_djust/observability/health/",
        REMOTE_ADDR="127.0.0.1",
        **{TOKEN_META_KEY: "local-tooling-token"},
    )
    assert views.health(ok).status_code == 200


def test_middleware_refuses_loopback_peer_with_proxy_header():
    from django.http import HttpResponse

    mw = LocalhostOnlyObservabilityMiddleware(lambda r: HttpResponse("ok"))
    req = _req("127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.7")
    assert mw(req).status_code == 403
    assert mw(_req("127.0.0.1")).status_code == 200


def test_mcp_server_sends_the_token():
    from djust.mcp.server import _observability_headers

    assert _observability_headers() == {TOKEN_HEADER: get_observability_token()}


def test_mcp_server_sends_environment_token(monkeypatch):
    from djust.mcp.server import _observability_headers

    monkeypatch.setenv(TOKEN_ENV_VAR, "local-tooling-token")
    assert _observability_headers() == {TOKEN_HEADER: "local-tooling-token"}


def test_token_management_command_prints_token():
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("djust_observability_token", stdout=out)
    assert out.getvalue().strip() == get_observability_token()
