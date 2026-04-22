"""Regression tests for CodeQL real-bug fixes.

Covers three bugs fixed together:

1. py/stack-trace-exposure — gallery views interpolated exception repr into
   HTML output shown to the user. Fix: log via ``logger.exception`` and
   return a generic message.
2. py/call-to-non-callable — ``BuildTimeGenerator.__init__`` assigned the
   ``generate_manifest`` bool argument onto ``self.generate_manifest``,
   shadowing the method of the same name. Fix: rename the attribute to
   ``self._generate_manifest``.
3. py/str-format/missing-named-argument — ``AccessibilityValidator``'s HTML
   report passed literal CSS braces through ``str.format`` where they were
   (mis)interpreted as format placeholders. Fix: keep CSS separate from
   the formatted template.
"""

import pytest


def test_gallery_render_error_hides_exception_details():
    """Gallery render error path must NOT leak exception message."""
    from djust.components.gallery import views as v

    broken_comp_tag = {
        "name": "broken",
        "label": "Broken",
        "type": "tag",
        "variants": [
            {
                "name": "v1",
                "template": "{% SUPER_SECRET_DETAILS_FROM_EXC %}",
                "context": {},
            }
        ],
    }
    html = v._render_component_cards([broken_comp_tag])
    assert "Render error" in html
    # The exception message / traceback must not be in the HTML output.
    assert "SUPER_SECRET_DETAILS_FROM_EXC" not in html
    assert "Traceback" not in html

    # Class render path — exception raised by variant["render"]().
    def _boom():
        raise RuntimeError("SUPER_SECRET_CLASS_DETAILS")

    broken_comp_class = {
        "name": "broken-class",
        "label": "BrokenClass",
        "type": "class",
        "variants": [{"name": "v1", "render": _boom}],
    }
    html2 = v._render_component_cards([broken_comp_class])
    assert "Render error" in html2
    assert "SUPER_SECRET_CLASS_DETAILS" not in html2


def test_build_themes_generate_manifest_flag_true_calls_method(tmp_path):
    """With generate_manifest=True, calling the method must not TypeError."""
    from djust.theming.build_themes import BuildTimeGenerator

    g = BuildTimeGenerator(
        output_dir=str(tmp_path),
        minify=False,
        include_source_maps=False,
        generate_manifest=True,
    )
    # Attribute must not shadow the method.
    assert callable(g.generate_manifest)
    try:
        result = g.generate_manifest([])
    except TypeError as e:
        if "not callable" in str(e):
            pytest.fail("generate_manifest is shadowed by the bool attribute: %s" % e)
        raise
    # With an empty input the method still writes a manifest; result is a
    # path string.
    assert isinstance(result, str)


def test_build_themes_generate_manifest_flag_false_skips(tmp_path):
    """With generate_manifest=False, the method short-circuits to ''."""
    from djust.theming.build_themes import BuildTimeGenerator

    g = BuildTimeGenerator(
        output_dir=str(tmp_path),
        generate_manifest=False,
    )
    assert callable(g.generate_manifest)
    result = g.generate_manifest([("file.css", str(tmp_path / "file.css"))])
    assert result == ""


def test_accessibility_report_html_renders_without_keyerror():
    """HTML report must render without KeyError on literal CSS braces."""
    from djust.theming.accessibility import AccessibilityValidator

    validator = AccessibilityValidator()
    # Empty reports dict is a valid minimal input.
    html = validator.generate_accessibility_report_html({})
    assert "<!DOCTYPE html>" in html
    assert "font-family" in html  # CSS survived the formatting step
    assert "djust-theming Accessibility Report" in html
