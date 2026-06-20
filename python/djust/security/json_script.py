"""Script-safe JSON escaping for embedding ``json.dumps`` output in HTML.

``json.dumps`` does NOT escape ``<``, ``>``, or ``&`` (and, with
``ensure_ascii=False``, ``U+2028`` / ``U+2029``). Interpolating its output
directly into an inline ``<script>…</script>`` block therefore lets a string
value containing ``</script>`` close the element and inject markup
(``</script><script>…`` breakout). This mirrors why Django ships
``django.utils.html.json_script``; we need the same escaping for the cases that
build the script tag by hand (the debug panel, ``JSChain.__html__``). See
finding #8 (CWE-79).
"""

from __future__ import annotations

# Map the HTML-significant characters json.dumps leaves raw to their \uXXXX
# forms. ``<``/``>``/``&`` match django.utils.html._json_script_escapes; the
# line/paragraph separators are belt-and-suspenders for ``ensure_ascii=False``
# callers (with the default ensure_ascii=True they are already \u-escaped).
_JSON_SCRIPT_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    0x2028: "\\u2028",
    0x2029: "\\u2029",
}


def escape_json_for_script(json_str: str) -> str:
    """Escape an already-serialized JSON string for safe embedding in an inline
    ``<script>`` block (or a double-quoted HTML attribute).

    The result is safe to drop between ``<script>`` and ``</script>`` and inside
    a double-quoted attribute value: ``<``, ``>``, ``&`` (and ``U+2028``/
    ``U+2029``) become ``\\uXXXX``, so ``</script>`` can no longer break out.
    It does NOT escape ``'`` — for a single-quoted attribute context use double
    quotes around the value, since ``json.dumps`` escapes ``"`` but not ``'``.
    """
    return json_str.translate(_JSON_SCRIPT_ESCAPES)
