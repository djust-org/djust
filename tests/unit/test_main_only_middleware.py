"""
Tests for ``djust.middleware.DjustMainOnlyMiddleware``.

The middleware honors the ``X-Djust-Main-Only: 1`` request header (sent by
the service-worker instant-shell client) and trims the response body to the
inner HTML of the first ``<main>`` element.
"""

from __future__ import annotations

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.test import RequestFactory

from djust.middleware import DjustMainOnlyMiddleware


def _run(middleware, request, inner_response):
    """Helper that invokes the middleware around a canned inner response."""
    return DjustMainOnlyMiddleware(lambda _req: inner_response)(request)


def test_header_present_html_response_extracts_main_inner():
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = (
        "<!DOCTYPE html><html><head><title>t</title></head>"
        "<body><nav>n</nav><main>HELLO</main><footer>f</footer></body></html>"
    )
    inner = HttpResponse(html, content_type="text/html; charset=utf-8")
    out = _run(None, req, inner)
    assert out.content == b"HELLO"
    assert out["X-Djust-Main-Only-Response"] == "1"


def test_header_absent_passes_through_unchanged():
    rf = RequestFactory()
    req = rf.get("/")
    html = "<html><body><main>only</main></body></html>"
    inner = HttpResponse(html, content_type="text/html")
    out = _run(None, req, inner)
    assert out.content == html.encode("utf-8")
    assert "X-Djust-Main-Only-Response" not in out


def test_non_html_response_passes_through():
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    inner = JsonResponse({"foo": "bar"})
    out = _run(None, req, inner)
    assert b'"foo"' in out.content
    assert "X-Djust-Main-Only-Response" not in out


def test_missing_main_tag_returns_empty_body():
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = "<html><body><div>no main here</div></body></html>"
    inner = HttpResponse(html, content_type="text/html")
    out = _run(None, req, inner)
    assert out.content == b""
    assert out["Content-Length"] == "0"
    assert out["X-Djust-Main-Only-Response"] == "1"


def test_main_with_attributes_still_extracted():
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = (
        '<html><body><main id="djust-main" class="container" role="main">'
        "<h1>Title</h1><p>body</p></main></body></html>"
    )
    inner = HttpResponse(html, content_type="text/html")
    out = _run(None, req, inner)
    assert out.content == b"<h1>Title</h1><p>body</p>"


def test_content_length_updated_correctly():
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    inner_html = "abc" * 100  # 300 chars, all ASCII
    html = f"<html><body><main>{inner_html}</main></body></html>"
    inner = HttpResponse(html, content_type="text/html")
    out = _run(None, req, inner)
    assert out.content == inner_html.encode("utf-8")
    assert out["Content-Length"] == str(len(inner_html))


def test_streaming_response_passes_through():
    """Streaming responses have no ``.content``; middleware must not touch them."""
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")

    def _gen():
        yield b"<html><body><main>streamed</main></body></html>"

    inner = StreamingHttpResponse(_gen(), content_type="text/html")
    out = _run(None, req, inner)
    assert "X-Djust-Main-Only-Response" not in out
