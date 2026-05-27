"""Tests for #1621 — dj_button confirm= kwarg.

Verifies that ``dj_button`` accepts a ``confirm`` kwarg and emits the
standard ``dj-confirm`` attribute consumed by djust's client.js
(``python/djust/static/djust/src/09-event-binding.js:7``).
"""

from djust.components.templatetags.djust_components import dj_button


class TestDjButtonConfirm1621:
    """#1621: dj_button exposes the existing dj-confirm primitive."""

    def test_confirm_emits_dj_confirm_attribute(self):
        """confirm='Wipe?' produces dj-confirm=\"Wipe?\" attribute."""
        html = dj_button(label="Reset", event="reset_demo", confirm="Wipe?")
        assert 'dj-confirm="Wipe?"' in html, (
            "Expected dj-confirm attribute in output; got: %s" % html
        )

    def test_no_confirm_no_attribute(self):
        """Default confirm='' emits no dj-confirm attribute."""
        html = dj_button(label="Reset", event="reset_demo")
        assert "dj-confirm" not in html, (
            "Expected NO dj-confirm attribute when confirm is empty; got: %s" % html
        )

    def test_confirm_message_is_html_escaped(self):
        """User-supplied confirm string flows through conditional_escape (XSS guard)."""
        html = dj_button(label="X", event="x", confirm="<script>alert(1)</script>")
        # Raw <script> must NOT appear.
        assert "<script>" not in html
        # Escaped form must appear inside the dj-confirm attribute.
        assert "&lt;script&gt;" in html or "&lt;/script&gt;" in html

    def test_confirm_emitted_without_event(self):
        """confirm= is independent of event= — emit dj-confirm even when no dj-click."""
        html = dj_button(label="X", confirm="Sure?")
        # No dj-click (no event provided)
        assert "dj-click" not in html
        # But dj-confirm is still present
        assert 'dj-confirm="Sure?"' in html

    def test_explicit_confirm_overrides_preset(self):
        """Explicit confirm= kwarg wins over preset-provided confirm."""
        # Register a temporary preset that supplies a confirm message.
        from djust.components.presets import register_preset

        register_preset(
            "dj_button",
            "test_1621_preset",
            {"confirm": "PRESET MESSAGE"},
        )
        try:
            # Explicit confirm should override preset.
            html = dj_button(
                label="X",
                event="x",
                preset="test_1621_preset",
                confirm="EXPLICIT",
            )
            assert 'dj-confirm="EXPLICIT"' in html
            assert "PRESET MESSAGE" not in html

            # Without explicit confirm, preset value flows through.
            html2 = dj_button(label="Y", event="y", preset="test_1621_preset")
            assert 'dj-confirm="PRESET MESSAGE"' in html2
        finally:
            # Tests run in registration order; the preset persists in the
            # process-wide registry. Best-effort cleanup — registry may
            # not expose a remove API; leaving the entry behind under a
            # test-only name is acceptable.
            pass
