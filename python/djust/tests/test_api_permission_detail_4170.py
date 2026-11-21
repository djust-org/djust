"""#4170: the HTTP API and ``@server_function`` answer ``PermissionDenied``
with the fixed message "Permission denied", never the exception's text.

WebSocket and SSE already send a fixed message; this pins the HTTP transport
to the same rule (owner decision on #4046: no exception detail reaches any
client, under any policy, DEBUG included). The text goes to the server log
only, through ``log_failure_for`` (so it is value-free in production for a
nonlegacy view, and present under DEBUG).
"""

from __future__ import annotations

import json
import logging

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.api.dispatch import dispatch_api, dispatch_server_function, reset_rate_buckets
from djust.api.registry import register_api_view, reset_registry
from djust.decorators import event_handler, server_function

pytestmark = pytest.mark.django_db

SECRET = "secret-4170"


@pytest.fixture(autouse=True)
def _clean_api_state():
    reset_registry()
    reset_rate_buckets()
    yield
    reset_registry()
    reset_rate_buckets()


_counter = {"n": 0}


def _api_request(body=None):
    _counter["n"] += 1
    rf = RequestFactory(enforce_csrf_checks=False)
    user = get_user_model().objects.create_user(username=f"u4170_{_counter['n']}", password="pw")
    request = rf.post(
        "/djust/api/x/y/",
        data=json.dumps(body or {}).encode("utf-8"),
        content_type="application/json",
    )
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    request._dont_enforce_csrf_checks = True
    return request


class _MountDenied(LiveView):
    exposure_policy = "explicit"
    template_name = None
    api_name = "p4170.mount"

    def mount(self, request, **kwargs):
        raise PermissionDenied(SECRET)

    @event_handler(expose_api=True)
    def ping(self, **kwargs):
        return "pong"

    @server_function
    def call(self, **kwargs):
        return "pong"


class _HandlerDenied(LiveView):
    exposure_policy = "explicit"
    template_name = None
    api_name = "p4170.handler"

    @event_handler(expose_api=True)
    def ping(self, **kwargs):
        raise PermissionDenied(SECRET)

    @server_function
    def call(self, **kwargs):
        raise PermissionDenied(SECRET)


def _assert_no_detail(response):
    assert response.status_code == 403
    assert SECRET.encode() not in response.content
    payload = json.loads(response.content)
    assert payload["error"] == "permission_denied"
    assert payload["message"] == "Permission denied"


@pytest.mark.parametrize("debug", [False, True])
def test_api_mount_permission_denied_sends_no_detail(debug):
    register_api_view("p4170.mount", _MountDenied)
    with override_settings(DEBUG=debug):
        _assert_no_detail(dispatch_api(_api_request(), "p4170.mount", "ping"))


@pytest.mark.parametrize("debug", [False, True])
def test_api_handler_permission_denied_sends_no_detail(debug):
    register_api_view("p4170.handler", _HandlerDenied)
    with override_settings(DEBUG=debug):
        _assert_no_detail(dispatch_api(_api_request(), "p4170.handler", "ping"))


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("slug", ["p4170.mount", "p4170.handler"])
def test_server_function_permission_denied_sends_no_detail(debug, slug):
    register_api_view("p4170.mount", _MountDenied)
    register_api_view("p4170.handler", _HandlerDenied)
    with override_settings(DEBUG=debug):
        _assert_no_detail(dispatch_server_function(_api_request({"params": {}}), slug, "call"))


@pytest.mark.parametrize("debug", [False, True])
def test_api_permission_denied_text_is_logged_under_debug(debug, caplog):
    register_api_view("p4170.handler", _HandlerDenied)
    with override_settings(DEBUG=debug), caplog.at_level(logging.INFO, logger="djust.api.dispatch"):
        _assert_no_detail(dispatch_api(_api_request(), "p4170.handler", "ping"))
    assert (SECRET in caplog.text) is debug
