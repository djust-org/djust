"""Regression tests for #1619 — dj_button variant→class mapping.

Verifies that ``variant="danger"`` is aliased to the ``btn-destructive``
CSS class (which djust-theming's components.css actually defines),
while ``variant="destructive"`` is the canonical name. Unknown variants
pass through with conditional_escape preserved.
"""

from djust.components.templatetags.djust_components import dj_button


class TestDjButtonVariantClass1619:
    """#1619: dj_button variants must map to CSS classes that exist."""

    def test_danger_alias_renders_btn_destructive(self):
        """variant='danger' → class='btn btn-destructive' (back-compat alias)."""
        html = dj_button(label="Delete", variant="danger")
        assert "btn-destructive" in html, (
            "Expected 'btn-destructive' (the actual CSS class) in output; got: %s" % html
        )
        assert "btn-danger" not in html, (
            "Output still contains the unmapped 'btn-danger' class — alias not applied: %s" % html
        )

    def test_destructive_canonical_renders_btn_destructive(self):
        """variant='destructive' → class='btn btn-destructive' (canonical name)."""
        html = dj_button(label="Delete", variant="destructive")
        assert "btn-destructive" in html

    def test_primary_unchanged(self):
        """Regression: variant='primary' still maps to 'btn-primary' (no behavior change)."""
        html = dj_button(label="Save", variant="primary")
        assert "btn-primary" in html
        assert "btn-destructive" not in html

    def test_unknown_variant_passes_through(self):
        """Unknown variants pass through as btn-<variant> so user theme classes work."""
        html = dj_button(label="X", variant="my-custom")
        assert "btn-my-custom" in html

    def test_unknown_variant_is_escaped(self):
        """Passthrough path applies conditional_escape to user-supplied variant."""
        # An injection-shaped variant must NOT appear unescaped in the output.
        html = dj_button(label="X", variant='"><script>alert(1)</script>')
        # The literal `<script>` tag must NOT be present unescaped.
        assert "<script>" not in html
        # Escaped form should appear in the class attribute.
        assert "&lt;script&gt;" in html or "&lt;/script&gt;" in html
