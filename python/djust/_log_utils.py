"""Logging utilities.

Helpers to sanitize user-controlled values before they reach logger calls so
an attacker-controlled CR/LF/control character cannot forge log lines, bypass
SIEM parsers, or flood the log stream.

Applied at call sites flagged by CodeQL `py/log-injection`.
"""

from __future__ import annotations

from typing import Any

_MAX_LOG_FIELD_CHARS = 200


def sanitize_for_log(value: Any) -> str:
    """Return a log-safe string representation of *value*.

    Strips CR, LF, and other control chars (anything < 0x20 other than space),
    truncates to _MAX_LOG_FIELD_CHARS, and always returns a string — None and
    non-string inputs become their ``repr``.

    Use at every log call where a value originates from HTTP request data
    (path params, POST body, query string). Safe to call on already-safe
    strings; the cost is a single translate + a length check.
    """
    if value is None:
        return "None"
    if not isinstance(value, str):
        value = repr(value)
    # Replace control chars (C0 range, excluding space) with a visible marker.
    cleaned_chars = []
    for ch in value:
        if ch.isprintable():
            cleaned_chars.append(ch)
        else:
            cleaned_chars.append("?")
    cleaned = "".join(cleaned_chars)
    if len(cleaned) > _MAX_LOG_FIELD_CHARS:
        cleaned = cleaned[: _MAX_LOG_FIELD_CHARS - 3] + "..."
    # Explicit CR/LF removal as the RETURNED value. The ``isprintable()`` loop
    # above already maps line breaks (CR, LF, and the unicode LINE/PARAGRAPH
    # SEPARATORS) to "?", so at runtime this is a no-op — but it is the form
    # CodeQL `py/log-injection` recognizes as a sanitizer barrier, and applying
    # it to the function's return value lets
    # CodeQL clear every ``sanitize_for_log(<remote source>)`` call site (the
    # ``isprintable`` comprehension alone is not a modeled barrier). Do NOT remove
    # this line: it is load-bearing for static analysis, pinned by
    # test_log_sanitizer_barrier_pin in python/djust/tests/test_log_sanitization.py.
    return cleaned.replace("\r", "").replace("\n", "")
