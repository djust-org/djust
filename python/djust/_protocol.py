"""The djust wire-protocol version (#3658, TC9 slice 3).

One integer names the shape of the frames the server and the client exchange.
The client bundle carries the same number as ``window.djust.PROTOCOL``
(``static/djust/src/00-namespace.js``); a test parses the JS source and fails
when the two differ.

**Bump rule.** A change that an older client would misread (a removed or
renamed frame, field or effect kind, or a changed meaning) bumps the integer.
An added optional field or a new effect kind does not, because the client
skips unknown kinds.

The client sends its version in every ``mount``, ``mount_batch`` and
``live_redirect_mount`` frame (``"protocol": N``), as ``&protocol=N`` on the
SSE stream GET, and as the ``X-Djust-Protocol`` header on the HTTP POST and the
SSE message POST. A missing or different value is an outdated (cached) bundle:

* WebSocket: a ``reload`` frame, then close code :data:`PROTOCOL_CLOSE_CODE`;
* SSE stream GET: the same ``reload`` frame, then the stream ends;
* HTTP POST and SSE message POST: status 409 with :func:`mismatch_envelope`.

Old clients already obey ``{"type": "reload"}`` (the hot-reload frame), so the
check needs no code in the old bundle.

``settings.DJUST_ALLOW_MISSING_PROTOCOL = True`` accepts a request that names
no version (a non-browser client, or a test that builds frames by hand). A
version that is present and different is refused regardless.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

#: The protocol this server speaks. ``1`` was the first versioned protocol (the
#: 1.x frames); ``2`` is the one ``render`` frame with ``effects`` (#3658, TC9).
PROTOCOL_VERSION: int = 2

#: Header the client sends on HTTP and SSE-message POSTs, and the server
#: returns on HTTP answers.
PROTOCOL_HEADER = "X-Djust-Protocol"

#: Query parameter the client puts on the SSE stream GET.
PROTOCOL_QUERY_PARAM = "protocol"

#: Field the client sets on the mount verbs, and the server on
#: ``connect`` / ``sse_connect`` / ``mount``.
PROTOCOL_FIELD = "protocol"

#: WebSocket close code for a protocol mismatch (4403 and 4429 are taken).
#: A new client does not reconnect after it.
PROTOCOL_CLOSE_CODE = 4409

#: The inbound WebSocket verbs that carry the client's version.
MOUNT_VERBS = frozenset({"mount", "mount_batch", "live_redirect_mount"})

#: The ``code`` of the HTTP 409 envelope.
MISMATCH_CODE = "protocol_mismatch"


def _allow_missing() -> bool:
    try:
        from django.conf import settings

        return bool(getattr(settings, "DJUST_ALLOW_MISSING_PROTOCOL", False))
    except Exception:  # noqa: BLE001 - settings not configured: the strict default
        return False


def parse_protocol(value: Any) -> Optional[int]:
    """The client's version as an int, or ``None`` when absent or malformed.

    Accepts an int (a JSON frame field) or a decimal string (a header or query
    value). ``bool`` is not an int here.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit() and len(text) <= 9:
            return int(text)
    return None


def client_protocol_ok(value: Any) -> bool:
    """True when a client that sent ``value`` speaks this server's protocol.

    ``value`` is the raw field, header or query value (``None`` when the client
    sent nothing). A missing value is accepted only under
    ``DJUST_ALLOW_MISSING_PROTOCOL``; a present one must equal
    :data:`PROTOCOL_VERSION`.
    """
    if value is None or value == "":
        return _allow_missing()
    return parse_protocol(value) == PROTOCOL_VERSION


def frame_protocol_ok(frame: Mapping[str, Any]) -> bool:
    """:func:`client_protocol_ok` for an inbound mount-verb frame."""
    return client_protocol_ok(frame.get(PROTOCOL_FIELD))


def request_protocol_ok(request: Any) -> bool:
    """:func:`client_protocol_ok` for an HTTP POST's ``X-Djust-Protocol`` header."""
    headers = getattr(request, "headers", None)
    value = headers.get(PROTOCOL_HEADER) if headers is not None else None
    return client_protocol_ok(value)


def reload_frame() -> Dict[str, Any]:
    """The frame that makes an outdated client reload the page."""
    return {"type": "reload", "reason": "protocol", PROTOCOL_FIELD: PROTOCOL_VERSION}


def mismatch_envelope() -> Dict[str, Any]:
    """The HTTP 409 body for a POST from an outdated client."""
    return {
        "type": "error",
        "code": MISMATCH_CODE,
        "recoverable": False,
        "error": "This page is out of date. Reload to continue.",
    }


def mismatch_response() -> Any:
    """The 409 :class:`~django.http.JsonResponse` for an outdated client's POST."""
    from django.http import JsonResponse

    response = JsonResponse(mismatch_envelope(), status=409)
    response[PROTOCOL_HEADER] = str(PROTOCOL_VERSION)
    return response


__all__ = [
    "MISMATCH_CODE",
    "MOUNT_VERBS",
    "PROTOCOL_CLOSE_CODE",
    "PROTOCOL_FIELD",
    "PROTOCOL_HEADER",
    "PROTOCOL_QUERY_PARAM",
    "PROTOCOL_VERSION",
    "client_protocol_ok",
    "frame_protocol_ok",
    "mismatch_envelope",
    "mismatch_response",
    "parse_protocol",
    "reload_frame",
    "request_protocol_ok",
]
