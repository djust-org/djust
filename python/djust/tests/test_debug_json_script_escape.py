"""Regression tests for </script>-breakout XSS in JSON-in-<script> sinks (#8, CWE-79).

`json.dumps` does not escape `<`, `>`, `&`, so interpolating its output into an
inline `<script>` block lets a value containing `</script>` break out. Two
sinks were affected: the DEBUG-only debug-panel injection
(`post_processing._inject_client_script`) and `JSChain.__html__`. Both now route
JSON through `security.escape_json_for_script`.
"""

import json

from djust.security import escape_json_for_script

PAYLOAD = "</script><script>alert(document.cookie)</script>"


# --- helper ---


def test_escape_neutralizes_script_breakout():
    out = escape_json_for_script(json.dumps({"x": PAYLOAD}))
    assert "</script>" not in out, "raw </script> survived escaping"
    assert "\\u003c" in out and "\\u003e" in out


def test_escape_preserves_json_structure_and_roundtrips():
    obj = {"a": 1, "b": "</script>", "c": ["x", "y"], "amp": "a & b"}
    out = escape_json_for_script(json.dumps(obj))
    assert "<" not in out and ">" not in out and "&" not in out
    # The browser un-escapes \uXXXX in a JS string literal → JSON.parse recovers it.
    assert json.loads(out) == obj


def test_escape_handles_line_separators():
    out = escape_json_for_script('{"x": "  "}')
    assert "\\u2028" in out and "\\u2029" in out


# --- debug-panel sink (finding #8 primary) ---


def test_debug_panel_does_not_break_out_of_script(settings):
    """A user-controlled public attribute containing </script> must not break
    out of the DEBUG debug-info <script> block."""
    from djust import LiveView

    settings.DEBUG = True

    class _V(LiveView):
        template_name = None
        comment = PAYLOAD  # user-controlled public attribute

        def get_context_data(self, **kwargs):
            return {"comment": self.comment}

    view = _V()
    # Build only the debug script the way _inject_client_script does, to avoid a
    # full Rust render — exercises get_debug_info() (which includes repr(comment))
    # through the same escape path.
    debug_json = escape_json_for_script(json.dumps(view.get_debug_info()))
    script = f"<script>\n window.DJUST_DEBUG_INFO = {debug_json};\n</script>"
    assert "</script><script>alert" not in script, "debug panel broke out of <script>"
    # The payload is present but neutralized.
    assert PAYLOAD not in script


def test_inject_client_script_escapes_debug_info(settings):
    """End-to-end through _inject_client_script: the rendered HTML must not
    contain a raw </script> breakout from a user-controlled attribute."""
    from djust import LiveView

    settings.DEBUG = True

    class _V(LiveView):
        template_name = None
        note = PAYLOAD

        def get_context_data(self, **kwargs):
            return {"note": self.note}

    view = _V()
    html = view._inject_client_script("<div dj-root></div>")
    assert "</script><script>alert" not in html, "_inject_client_script breakout"


# --- JSChain.__html__ sink ---


def test_jschain_html_escapes_script_breakout():
    from djust.js import JS

    cmd = JS.dispatch("evt", detail={"x": PAYLOAD})
    out = str(cmd.__html__())
    assert "</script>" not in out, "JSChain.__html__ emitted raw </script>"
