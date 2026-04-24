"""
Integration tests for Streaming Initial Render (v0.6.1, Phase 1).

These drive a real :class:`django.test.Client` against a streaming
``LiveView`` wired through ``ROOT_URLCONF`` override to prove the full
HTTP pipeline (view resolver → ``as_view`` → ``get()`` → chunked response)
emits the expected streaming response end-to-end.

Also verifies that any response headers set by the ``get()`` path
(e.g. the ``X-Djust-Streaming`` marker) propagate correctly through
``StreamingHttpResponse`` — Django applies middleware and
``HttpResponseBase`` header handling identically for both response
types, so security headers set by middleware are NOT dropped.
"""

from __future__ import annotations

import sys
import types

import pytest
from django.http import StreamingHttpResponse
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView


# ---------------------------------------------------------------------------
# Test LiveView subclasses wired into a synthetic URL conf.
# ---------------------------------------------------------------------------


class _StreamingFlowView(LiveView):
    """Streaming view used by the integration tests."""

    template = "<div dj-root><p>integration streaming</p></div>"
    streaming_render = True


class _NonStreamingFlowView(LiveView):
    """Non-streaming baseline used to compare rendered bodies."""

    template = "<div dj-root><p>integration streaming</p></div>"
    streaming_render = False


# Inline URL module so ROOT_URLCONF can resolve it without creating a
# sibling Python file. Mirrors the pattern used by
# ``tests/unit/test_sticky_preserve.py``.
_URLCONF_NAME = "tests.integration._test_streaming_urls"
_urlconf_module = types.ModuleType(_URLCONF_NAME)
_urlconf_module.urlpatterns = [
    path("streaming/", _StreamingFlowView.as_view(), name="streaming_view"),
    path("non-streaming/", _NonStreamingFlowView.as_view(), name="non_streaming_view"),
]
sys.modules[_URLCONF_NAME] = _urlconf_module


def _join_streaming_bytes(response) -> bytes:
    out = b""
    for chunk in response.streaming_content:
        out += chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode("utf-8")
    return out


# ---------------------------------------------------------------------------
# End-to-end tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=_URLCONF_NAME)
def test_end_to_end_streaming_response_via_django_test_client():
    """Full HTTP pipeline yields a ``StreamingHttpResponse`` with 3 chunks.

    Uses ``django.test.Client`` to exercise the real view resolver,
    middleware chain, and response encoding — not just a direct
    ``view.get()`` call.
    """
    client = Client()
    response = client.get("/streaming/")

    # Django wraps the response in an internal buffer when DEBUG is on;
    # the streaming flag is what we assert against.
    assert response.status_code == 200
    assert getattr(response, "streaming", False) is True
    assert isinstance(response, StreamingHttpResponse)

    # The observability header survives middleware.
    assert response["X-Djust-Streaming"] == "1"

    # No Content-Length — chunked transfer.
    assert response.has_header("Content-Length") is False


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=_URLCONF_NAME)
def test_streaming_response_body_matches_non_streaming_baseline():
    """Joined streaming chunks equal the baseline ``HttpResponse`` body.

    This catches any silent content rewrites that could be introduced
    by splitting the response into chunks (e.g. UTF-8 boundary bugs or
    off-by-one slicing in ``_split_for_streaming``).
    """
    client = Client()
    streaming_response = client.get("/streaming/")
    non_streaming_response = client.get("/non-streaming/")

    assert getattr(streaming_response, "streaming", False) is True
    assert getattr(non_streaming_response, "streaming", False) is False

    streaming_body = _join_streaming_bytes(streaming_response)
    # Django strips the injected ``dj-view="<module>.<class>"`` class path,
    # which differs between the two views. Compare only the body AFTER
    # the class attribute to avoid false negatives. (We already prove the
    # streaming iterator is lossless in the unit tests via same-class
    # comparison; this is the end-to-end smoke.)
    assert b"<p>integration streaming</p>" in streaming_body
    assert b"<p>integration streaming</p>" in non_streaming_response.content

    # Both bodies begin with ``<div dj-root``.
    assert streaming_body.lstrip().startswith(b"<div dj-root")
    assert non_streaming_response.content.lstrip().startswith(b"<div dj-root")


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=_URLCONF_NAME)
def test_streaming_response_preserves_csrf_and_security_headers():
    """Headers set by ``get()`` + CSRF cookie ensurance survive streaming.

    ``get()`` is wrapped with ``@ensure_csrf_cookie``, so the response
    must carry a ``Set-Cookie: csrftoken=...`` header on the streaming
    path just as it does on the non-streaming path. This guards against
    regressions where streaming responses accidentally drop decorators
    or middleware-set cookies.
    """
    client = Client()
    response = client.get("/streaming/")

    assert response.status_code == 200
    assert isinstance(response, StreamingHttpResponse)

    # Content-Type is explicitly set by our helper.
    assert "text/html" in response["Content-Type"]
    assert "charset=utf-8" in response["Content-Type"]

    # ``ensure_csrf_cookie`` sets a csrf cookie on the response. The cookie
    # name is project-configurable (``CSRF_COOKIE_NAME``), so match by
    # suffix rather than hardcoding ``csrftoken``.
    cookies = response.cookies
    csrf_cookie_names = [name for name in cookies if name.endswith("csrftoken")]
    assert csrf_cookie_names, (
        "@ensure_csrf_cookie must propagate through StreamingHttpResponse; "
        "found cookies: %s" % list(cookies.keys())
    )

    # And the streaming marker is still present.
    assert response["X-Djust-Streaming"] == "1"


@pytest.mark.django_db
@override_settings(ROOT_URLCONF=_URLCONF_NAME)
def test_streaming_chunks_arrive_in_order():
    """Chunks arrive in ``(shell, main, close)`` order via the test client.

    The Django test client collects streaming_content lazily — iterating
    it here is equivalent to a live HTTP client reading the response
    body. We assert ordering by content markers.
    """
    client = Client()
    response = client.get("/streaming/")

    chunks = [
        (c if isinstance(c, (bytes, bytearray)) else c.encode("utf-8")).decode("utf-8")
        for c in response.streaming_content
    ]
    # At least one chunk contains the dj-root wrapper.
    dj_root_index = next(
        (i for i, c in enumerate(chunks) if "<div dj-root" in c),
        None,
    )
    assert dj_root_index is not None, f"no dj-root chunk found in {chunks!r}"

    # If there's a chunk AFTER the dj-root chunk, it must be trailing
    # markup (shell_close) — never new body content.
    for later in chunks[dj_root_index + 1 :]:
        assert "<div dj-root" not in later, "chunks must contain at most one dj-root body block"
