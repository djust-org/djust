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


def test_3xx_redirect_is_still_trimmed():
    """3xx responses (redirects) are below the >=400 gate and still get trimmed.

    A 302 HTML body is rarely meaningful content, but if a shell-navigation
    client requested main-only on a page that happens to return a 301/302
    with a <main>, we still honor the opt-in — the status gate is specifically
    for 4xx/5xx error pages.
    """
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = "<html><body><main>redirect body</main></body></html>"
    inner = HttpResponse(html, content_type="text/html", status=302)
    inner["Location"] = "/new/"
    out = _run(None, req, inner)
    assert out.status_code == 302
    assert out.content == b"redirect body"
    assert out["X-Djust-Main-Only-Response"] == "1"


def test_error_response_4xx_is_not_trimmed():
    """Issue #828: 4xx error pages render full-page layouts, not main-area fragments.

    Trimming them strips the error context (status message, "go back" link, etc.)
    a shell-navigation client wouldn't otherwise see. Error responses must pass
    through untouched.
    """
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = "<html><body><main>404 error page</main></body></html>"
    inner = HttpResponse(html, content_type="text/html", status=404)
    out = _run(None, req, inner)
    assert out.status_code == 404
    assert out.content == html.encode("utf-8")
    assert "X-Djust-Main-Only-Response" not in out


def test_error_response_5xx_is_not_trimmed():
    """Issue #828: same as 4xx — 5xx server errors pass through unchanged."""
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = "<html><body><main>500 error page</main></body></html>"
    inner = HttpResponse(html, content_type="text/html", status=500)
    out = _run(None, req, inner)
    assert out.status_code == 500
    assert out.content == html.encode("utf-8")
    assert "X-Djust-Main-Only-Response" not in out


def test_xhtml_content_type_is_treated_as_html():
    """Issue #830: application/xhtml+xml also carries HTML shell content."""
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = "<html><body><main>xhtml body</main></body></html>"
    inner = HttpResponse(html, content_type="application/xhtml+xml")
    out = _run(None, req, inner)
    assert out.content == b"xhtml body"
    assert out["X-Djust-Main-Only-Response"] == "1"


def test_html_content_type_with_charset_and_boundary_suffix():
    """Issue #830: charset/boundary suffix must not prevent HTML detection."""
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    html = "<html><body><main>hi</main></body></html>"
    inner = HttpResponse(html, content_type="text/html; charset=utf-8; boundary=xyz")
    out = _run(None, req, inner)
    assert out.content == b"hi"


def test_rss_feed_content_type_passes_through():
    """Defensive: non-HTML XML dialects (RSS, Atom) are NOT treated as HTML shells."""
    rf = RequestFactory()
    req = rf.get("/", HTTP_X_DJUST_MAIN_ONLY="1")
    inner = HttpResponse(
        "<?xml version='1.0'?><rss><channel><title>feed</title></channel></rss>",
        content_type="application/rss+xml",
    )
    out = _run(None, req, inner)
    assert "X-Djust-Main-Only-Response" not in out
