"""
djust framework middleware.

Currently provides:

- :class:`DjustMainOnlyMiddleware` — honors the ``X-Djust-Main-Only: 1``
  request header (sent by the service-worker "instant shell" client) by
  trimming the response body to the inner HTML of the first ``<main>``
  element. All other responses pass through unchanged.

Users register it explicitly in ``MIDDLEWARE``; it is intentionally NOT
added automatically so existing projects are unaffected.
"""

from __future__ import annotations

import logging
import re
from typing import Callable

logger = logging.getLogger(__name__)


# Non-greedy match of the first <main>...</main>. Attributes on the opening
# tag are preserved in group 0 but ignored here — we only extract group 1
# (the inner HTML). Case-insensitive to tolerate <MAIN>.
_MAIN_RE = re.compile(r"<main\b[^>]*>([\s\S]*?)</main>", re.IGNORECASE)


def _is_html_response(response) -> bool:
    """Return True when the response's Content-Type starts with text/html."""
    content_type = response.get("Content-Type", "") or ""
    return content_type.lower().split(";", 1)[0].strip() == "text/html"


def _extract_main_inner(html: str) -> str:
    """Return the inner HTML of the first <main>…</main>, or ``""``.

    Uses a simple regex — this is a deliberate v0.5.0 limitation. Pages
    with nested ``<!-- <main> -->`` comments or CDATA sections containing
    the literal token ``</main>`` inside ``<main>`` will confuse the
    extractor; a full HTML parser is out of scope for this release.
    """
    match = _MAIN_RE.search(html)
    if match is None:
        return ""
    return match.group(1)


class DjustMainOnlyMiddleware:
    """Middleware that honors ``X-Djust-Main-Only: 1``.

    When a request carries that header and the response is HTML, the
    response body is replaced with just the inner HTML of the first
    ``<main>`` element. ``Content-Length`` is updated and the response
    gains an ``X-Djust-Main-Only-Response: 1`` marker header so clients
    can distinguish transformed responses.

    Otherwise, the response is returned unchanged.

    The middleware is ordering-safe — it only inspects the rendered
    ``response.content`` and the ``X-Djust-Main-Only`` request header,
    so it can sit anywhere in ``MIDDLEWARE`` that sees both.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only act on opt-in requests.
        if request.META.get("HTTP_X_DJUST_MAIN_ONLY") != "1":
            return response

        # Only touch HTML responses; pass JSON / binary through verbatim.
        if not _is_html_response(response):
            return response

        # Streaming responses don't have .content; skip for safety.
        if getattr(response, "streaming", False):
            return response

        raw = response.content
        try:
            html = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(
                "DjustMainOnlyMiddleware: could not decode response as UTF-8; "
                "passing through unchanged for %s",
                request.path,
            )
            return response

        inner = _extract_main_inner(html)
        encoded = inner.encode("utf-8")
        response.content = encoded
        response["Content-Length"] = str(len(encoded))
        response["X-Djust-Main-Only-Response"] = "1"
        return response
