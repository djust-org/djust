"""The injected client bootstrap carries the CSRF cookie name and token.

The HTTP event fallback and ``djust.call`` read these through
``window.djust.csrfToken()`` so they work when a project renames the CSRF
cookie (``CSRF_COOKIE_NAME``) or keeps it out of JavaScript's reach
(``CSRF_COOKIE_HTTPONLY``, ``CSRF_USE_SESSIONS``). Before this, the fallback
sent an empty ``X-CSRFToken`` and Django rejected the POST with 403.
"""

from __future__ import annotations

import re

from django.middleware.csrf import CsrfViewMiddleware, _check_token_format, _unmask_cipher_token
from django.conf import settings
from django.test import RequestFactory, override_settings

from djust.mixins.post_processing import PostProcessingMixin


class _FakeView(PostProcessingMixin):
    def get_debug_info(self):
        return {}


def _inject(html: str, request=None) -> str:
    view = _FakeView()
    if request is not None:
        view.request = request
    return view._inject_client_script(html)


def _meta(html: str, name: str) -> str | None:
    match = re.search(rf'<meta name="{name}" content="([^"]*)">', html)
    return match.group(1) if match else None


def test_meta_tags_go_in_head_with_configured_cookie_name():
    request = RequestFactory().get("/")
    html = _inject("<html><head></head><body></body></html>", request)

    head = html.split("</head>")[0]
    assert _meta(head, "djust-csrf-cookie") == settings.CSRF_COOKIE_NAME
    token = _meta(head, "djust-csrf-token")
    assert token
    _check_token_format(token)  # a well-formed (masked) Django token


@override_settings(CSRF_COOKIE_NAME="djust_demo_csrftoken")
def test_meta_carries_renamed_cookie():
    request = RequestFactory().get("/")
    html = _inject("<html><head></head><body></body></html>", request)
    assert _meta(html, "djust-csrf-cookie") == "djust_demo_csrftoken"


def test_meta_token_matches_the_request_secret():
    request = RequestFactory().get("/")
    html = _inject("<html><head></head><body></body></html>", request)
    token = _meta(html, "djust-csrf-token")

    # get_token() stored the secret on the request; the emitted token must
    # unmask to it, i.e. a POST carrying it would pass CsrfViewMiddleware.
    secret = request.META["CSRF_COOKIE"]
    assert _unmask_cipher_token(token) == secret


def test_emitted_token_is_accepted_by_csrf_middleware():
    factory = RequestFactory()
    get = factory.get("/")
    html = _inject("<html><head></head><body></body></html>", get)
    token = _meta(html, "djust-csrf-token")

    post = factory.post("/", data="{}", content_type="application/json", HTTP_X_CSRFTOKEN=token)
    post.COOKIES[settings.CSRF_COOKIE_NAME] = get.META["CSRF_COOKIE"]
    middleware = CsrfViewMiddleware(lambda r: None)
    assert middleware.process_view(post, lambda r: None, (), {}) is None


def test_no_head_puts_meta_before_the_scripts():
    request = RequestFactory().get("/")
    html = _inject("<html><body></body></html>", request)
    assert html.index('name="djust-csrf-token"') < html.index("<script")


def test_no_request_emits_no_meta():
    html = _inject("<html><head></head><body></body></html>")
    assert "djust-csrf-token" not in html
    assert "djust-csrf-cookie" not in html
