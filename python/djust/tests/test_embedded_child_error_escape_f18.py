"""Security regression (finding #18): ``_render_embedded_child`` must NOT emit a
raw, unescaped exception message into the live page HTML.

Vulnerability: ``LiveViewConsumer._render_embedded_child`` rolled its own error
string in its ``except`` block and returned it as the embedded child's subtree
HTML (sent to the client via ``_emit_full_html_update(..., "embedded_child", ...)``).
The old shape was::

    except Exception as e:
        ...
        return f"<!-- Error rendering embedded child: {e} -->"

This was (a) NOT DEBUG-gated — raw ``str(e)`` leaked into the production page
(CWE-209), and (b) NOT escaped — an attacker-influenced exception message
containing ``-->`` (e.g. ``int("--><img src=x onerror=alert(1)>")`` →
``ValueError: invalid literal for int() with base 10: '--><img ...>'``) broke
out of the HTML comment into live DOM (CWE-79 DOM XSS).

The fix escapes AND DEBUG-gates the detail (mirroring ``simple_live_view``'s
``render_template`` DEBUG gate + central ``create_safe_error_response``
discipline): ``escape()`` turns ``-->`` into ``--&gt;`` and any ``<script>`` /
``<img`` into inert entities in BOTH modes, and production emits no detail at all.
"""

from __future__ import annotations

from django.test import override_settings

from djust.websocket import LiveViewConsumer

# An attacker-influenced exception message that, unescaped, would break out of
# the surrounding HTML comment (the `-->`) and inject a live element. Mirrors the
# `ValueError` text that `int("--><img ...>")` produces verbatim.
_ATTACK_PAYLOAD = "--><img src=x onerror=alert(document.domain)><script>alert(1)</script>"
_RAISING_MESSAGE = f"invalid literal for int() with base 10: '{_ATTACK_PAYLOAD}'"


class _RaisingChildView:
    """Minimal stand-in for an embedded child LiveView whose render raises with
    an attacker-influenced message. ``_render_embedded_child`` calls
    ``get_context_data()`` first, so raising there exercises the except path."""

    def get_context_data(self):  # noqa: D401 - test stub
        raise ValueError(_RAISING_MESSAGE)

    def get_template(self):  # pragma: no cover - never reached (raise is earlier)
        return "<div>unused</div>"


def _render_error_html():
    """Drive the real ``_render_embedded_child`` except path on a bare consumer.

    The method only uses ``child_view`` — a no-arg ``LiveViewConsumer()`` is
    sufficient to call it (same construction pattern as
    ``tests/unit/test_arm_recovery_helper_1645.py``)."""
    consumer = LiveViewConsumer()
    return consumer._render_embedded_child(_RaisingChildView())


class TestEmbeddedChildErrorEscapeF18:
    """Finding #18: embedded-child render errors must be escaped + DEBUG-gated."""

    @override_settings(DEBUG=True)
    def test_debug_no_comment_breakout(self):
        html = _render_error_html()
        # Only the legitimate comment terminator may appear — no breakout.
        assert html.count("-->") == 1, html
        # The injected tags must be neutralised (escaped to entities).
        assert "<img" not in html, html
        assert "<script" not in html, html

    @override_settings(DEBUG=True)
    def test_debug_payload_is_escaped_not_omitted(self):
        # In DEBUG we keep the (now-escaped) detail for developer triage: the
        # dangerous characters survive only as HTML entities.
        html = _render_error_html()
        assert "&lt;img" in html, html
        assert "&gt;" in html, html

    @override_settings(DEBUG=False)
    def test_prod_no_comment_breakout(self):
        html = _render_error_html()
        assert html.count("-->") == 1, html
        assert "<img" not in html, html
        assert "<script" not in html, html

    @override_settings(DEBUG=False)
    def test_prod_leaks_no_exception_detail(self):
        # Production must disclose NONE of the exception detail (CWE-209) — not
        # the payload, not the "invalid literal" message text, escaped or not.
        html = _render_error_html()
        assert "onerror" not in html, html
        assert "invalid literal" not in html, html
        assert "img" not in html, html
        assert "script" not in html, html

    def test_returns_html_comment_shape(self):
        # The contract is still "return an HTML comment" so the client subtree
        # update remains well-formed in both modes.
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                html = _render_error_html()
                assert html.startswith("<!--"), (debug, html)
                assert html.rstrip().endswith("-->"), (debug, html)

    def test_gate_off_sentinel_breakout_is_neutralised(self):
        """GATE-OFF (#1468): this sentinel encodes exactly what the fix prevents.

        It asserts the breakout substring (``-->`` followed by a live ``<img``)
        is absent from the returned HTML in BOTH modes. Reverting the fix to the
        raw ``f"<!-- Error rendering embedded child: {e} -->"`` makes the raw
        payload re-appear, so ``"--><img"`` becomes a substring of the output
        and this assertion FAILS — proving the test exercises the change."""
        breakout = "--><img"
        for debug in (True, False):
            with override_settings(DEBUG=debug):
                html = _render_error_html()
                assert breakout not in html, (debug, html)
