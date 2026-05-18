"""Tests for the accessibility (Y0xx) system checks (djust/checks.py).

Mirrors the TestT004* pattern in python/tests/test_checks.py — a regex /
heuristic-unit class plus an integration class that writes fixture
templates into tmp_path, points settings.TEMPLATES['DIRS'] at them,
calls check_accessibility(None) directly, and filters results by e.id.
"""

import re

from djust import checks as _checks_mod
from djust.checks import (
    _content_is_icon_only,
    _IMG_HAS_ALT_RE,
    _IMG_TAG_RE,
    check_accessibility,
)


def _settings_with_tpl_dir(settings, tpl_dir):
    """Point settings.TEMPLATES at *tpl_dir* only (DIRS-based, no APP_DIRS)."""
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]


# ---------------------------------------------------------------------------
# Y001 -- icon-only-content heuristic (_content_is_icon_only)
# ---------------------------------------------------------------------------


class TestY001IconHeuristic:
    """_content_is_icon_only() decides whether button content needs a label."""

    def test_html_entity_only_is_icon_only(self):
        assert _content_is_icon_only("&times;") is True

    def test_numeric_entity_is_icon_only(self):
        assert _content_is_icon_only("&#9662;") is True

    def test_svg_only_is_icon_only(self):
        assert _content_is_icon_only('<svg viewBox="0 0 1 1"><path d="M0 0"/></svg>') is True

    def test_self_closing_icon_tag_is_icon_only(self):
        assert _content_is_icon_only('<i class="icon-close" />') is True

    def test_span_wrapped_entity_is_icon_only(self):
        assert _content_is_icon_only('<span class="icon">&times;</span>') is True

    def test_nested_wrapper_with_svg_is_icon_only(self):
        assert _content_is_icon_only('<span><svg><path d="M0"/></svg></span>') is True

    def test_whitespace_only_is_icon_only(self):
        assert _content_is_icon_only("   \n  ") is True

    def test_template_comment_only_is_icon_only(self):
        assert _content_is_icon_only("{# just a comment #}") is True

    def test_plain_text_is_not_icon_only(self):
        assert _content_is_icon_only("Close") is False

    def test_text_alongside_icon_is_not_icon_only(self):
        assert _content_is_icon_only('<span class="icon">&times;</span> Close') is False

    def test_variable_interpolation_is_not_icon_only(self):
        """A {{ var }} could resolve to a label -> not flagged (FP discipline)."""
        assert _content_is_icon_only("{{ button_label }}") is False


# ---------------------------------------------------------------------------
# Y002 -- <img> tag / alt-attribute regex
# ---------------------------------------------------------------------------


class TestY002Regex:
    """Y002 -- <img> tag matching + alt-attribute detection."""

    def test_img_tag_matches(self):
        assert _IMG_TAG_RE.search('<img src="logo.png">') is not None

    def test_img_self_closing_matches(self):
        assert _IMG_TAG_RE.search('<img src="logo.png" />') is not None

    def test_alt_attr_detected(self):
        assert _IMG_HAS_ALT_RE.search('<img src="x.png" alt="A logo">') is not None

    def test_empty_alt_attr_detected(self):
        """alt="" is the WCAG-correct decorative marker -> counts as present."""
        assert _IMG_HAS_ALT_RE.search('<img src="x.png" alt="">') is not None

    def test_no_alt_not_detected(self):
        assert _IMG_HAS_ALT_RE.search('<img src="x.png">') is None

    def test_data_alt_not_detected_as_real_alt(self):
        """`data-alt=` must NOT register as the image's real alt (#1514)."""
        assert _IMG_HAS_ALT_RE.search("<img src='x.png' data-alt='x'>") is None


# ---------------------------------------------------------------------------
# Y001 -- check integration
# ---------------------------------------------------------------------------


class TestY001CheckIntegration:
    """Integration tests for Y001 using check_accessibility() directly."""

    def test_y001_flags_icon_only_button(self, tmp_path, settings):
        """An icon-only <button> with no aria-label is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<button class='close'>&times;</button>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 1
        assert "accessible name" in y001[0].msg

    def test_y001_passes_button_with_text(self, tmp_path, settings):
        """A <button> with visible text content is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text("<button class='close'>Close</button>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 0

    def test_y001_passes_icon_button_with_aria_label(self, tmp_path, settings):
        """An icon-only <button> WITH aria-label is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text(
            "<button class='close' aria-label='Close'>&times;</button>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 0

    def test_y001_flags_icon_only_anchor_with_href(self, tmp_path, settings):
        """An icon-only <a href> with no accessible name is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<a href='/x'>&rarr;</a>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 1

    def test_y001_ignores_anchor_without_href(self, tmp_path, settings):
        """A bare <a> (no href) is an anchor target, not a control -> ignored."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<a name='top'>&times;</a>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 0

    def test_y001_no_false_positive_on_plain_template(self, tmp_path, settings):
        """A template with no interactive elements yields zero Y001 (no tautology)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "plain.html").write_text("<div><p>Hello world</p></div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 0

    def test_y001_suppressed(self, tmp_path, settings):
        """DJUST_CONFIG suppress_checks=['Y001'] silences Y001."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<button class='close'>&times;</button>")
        _settings_with_tpl_dir(settings, tpl_dir)
        settings.DJUST_CONFIG = {"suppress_checks": ["Y001"]}

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 0

    def test_y001_reports_line_number(self, tmp_path, settings):
        """Y001 result carries an accurate line_number."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<div>\n<p>line two</p>\n<button>&times;</button>\n</div>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 1
        assert y001[0].line_number == 3

    def test_y001_data_href_not_treated_as_real_href(self, tmp_path, settings):
        """An <a> with only `data-href` (no real href) is NOT interactive.

        Regression (#1514): the `_HREF_ATTR_RE` `\\b` word boundary
        false-matched inside `data-href='/x'` (a `-` is a non-word char),
        so a bare <a> carrying only a `data-href` JS hook was wrongly
        treated as a linked, interactive control and false-flagged Y001.
        The `(?<![\\w-])` lookbehind blocks a match preceded by a hyphen,
        so the non-linked <a> is correctly ignored (an anchor target is
        not a control).
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<a data-href='/x'>&rarr;</a>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 0

    def test_y001_data_aria_label_does_not_silence(self, tmp_path, settings):
        """A `data-aria-label` custom attr must NOT silence a real Y001.

        Regression (#1514): the `_ACCESSIBLE_NAME_ATTR_RE` `\\b` word
        boundary false-matched inside `data-aria-label='x'` (a `-` is a
        non-word char), so an icon-only <button> carrying only a
        `data-aria-label` JS hook (and no real accessible name) was
        wrongly treated as named and Y001 silently skipped it (false
        negative). The `(?<![\\w-])` lookbehind blocks a match preceded
        by a hyphen, so the unnamed icon-only button is still flagged.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<button class='close' data-aria-label='Close'>&times;</button>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y001 = [e for e in errors if e.id == "djust.Y001"]
        assert len(y001) == 1
        assert "accessible name" in y001[0].msg


# ---------------------------------------------------------------------------
# Y002 -- check integration
# ---------------------------------------------------------------------------


class TestY002CheckIntegration:
    """Integration tests for Y002 using check_accessibility() directly."""

    def test_y002_flags_img_without_alt(self, tmp_path, settings):
        """An <img> with no alt attribute is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<img src='/static/logo.png'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 1
        assert "alt" in y002[0].msg

    def test_y002_passes_img_with_alt(self, tmp_path, settings):
        """An <img> with a descriptive alt is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text("<img src='/static/logo.png' alt='Company logo'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 0

    def test_y002_passes_img_with_empty_alt(self, tmp_path, settings):
        """An <img alt=""> (decorative, WCAG-correct) is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text("<img src='/static/spacer.png' alt=''>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 0

    def test_y002_ignores_img_with_dynamic_attrs(self, tmp_path, settings):
        """An <img> with {% ... %}/{{ ... }} may carry alt dynamically -> not flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "dyn.html").write_text("<img src='{{ photo.url }}' {{ extra_attrs }}>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 0

    def test_y002_no_false_positive_on_plain_template(self, tmp_path, settings):
        """A template with no <img> tags yields zero Y002 (no tautology)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "plain.html").write_text("<div><p>No images here</p></div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 0

    def test_y002_suppressed(self, tmp_path, settings):
        """DJUST_CONFIG suppress_checks=['Y002'] silences Y002."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<img src='/static/logo.png'>")
        _settings_with_tpl_dir(settings, tpl_dir)
        settings.DJUST_CONFIG = {"suppress_checks": ["Y002"]}

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 0

    def test_y002_data_alt_not_mistaken_for_real_alt(self, tmp_path, settings):
        """A `data-alt='...'` custom attr must NOT shadow a missing real `alt`.

        Regression (#1514): the `_IMG_HAS_ALT_RE` `\\b` word boundary
        false-matched inside `data-alt='decorative'` (a `-` is a non-word
        char), so an <img> carrying only `data-alt` was wrongly treated as
        having an alt attribute and Y002 silently skipped it (false
        negative). The `(?<![\\w-])` lookbehind blocks a match preceded by a
        hyphen, so the genuinely alt-less <img> is still flagged.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<img src='x.png' data-alt='decorative'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y002 = [e for e in errors if e.id == "djust.Y002"]
        assert len(y002) == 1
        assert "alt" in y002[0].msg


# ---------------------------------------------------------------------------
# Y003 -- form control missing an associated label
# ---------------------------------------------------------------------------


class TestY003CheckIntegration:
    """Integration tests for Y003 using check_accessibility() directly."""

    def test_y003_flags_input_without_label(self, tmp_path, settings):
        """A bare <input> with no label association is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<form><input type='text' name='q'></form>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert "label" in y003[0].msg

    def test_y003_flags_select_without_label(self, tmp_path, settings):
        """A bare <select> with no label association is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<select name='country'><option>US</option></select>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert "select" in y003[0].msg

    def test_y003_flags_textarea_without_label(self, tmp_path, settings):
        """A bare <textarea> with no label association is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<textarea name='bio'></textarea>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert "textarea" in y003[0].msg

    def test_y003_passes_input_with_aria_label(self, tmp_path, settings):
        """An <input> with aria-label is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<input type='text' aria-label='Search'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_passes_input_with_aria_labelledby(self, tmp_path, settings):
        """An <input> with aria-labelledby is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text(
            "<span id='lbl'>Query</span><input type='text' aria-labelledby='lbl'>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_passes_input_with_label_for(self, tmp_path, settings):
        """An <input id='q'> matched by a <label for='q'> is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<label for='q'>Query</label><input type='text' id='q'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_passes_input_wrapped_by_label(self, tmp_path, settings):
        """An <input> wrapped by a <label> element is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<label>Query <input type='text' name='q'></label>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_ignores_hidden_and_button_inputs(self, tmp_path, settings):
        """hidden / submit inputs are not user-named text controls -> ignored."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text(
            "<input type='hidden' name='csrf'><input type='submit' value='Go'>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_ignores_button_reset_image_inputs(self, tmp_path, settings):
        """button / reset / image inputs get their name from value/alt -> ignored."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text(
            "<input type='button' value='X'>"
            "<input type='reset' value='Clear'>"
            "<input type='image' alt='Search' src='/s.png'>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_ignores_input_with_dynamic_attrs(self, tmp_path, settings):
        """An <input> with {% ... %}/{{ ... }} may carry id/aria dynamically -> not flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "dyn.html").write_text("<input type='text' {{ field_attrs }}>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_no_false_positive_on_plain_template(self, tmp_path, settings):
        """A template with no form controls yields zero Y003 (no tautology)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "plain.html").write_text("<div><p>No form here</p></div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_suppressed(self, tmp_path, settings):
        """DJUST_CONFIG suppress_checks=['Y003'] silences Y003."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<input type='text' name='q'>")
        _settings_with_tpl_dir(settings, tpl_dir)
        settings.DJUST_CONFIG = {"suppress_checks": ["Y003"]}

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_reports_line_number(self, tmp_path, settings):
        """Y003 result carries an accurate line_number."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<div>\n<p>line two</p>\n<input type='text' name='q'>\n</div>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert y003[0].line_number == 3

    def test_y003_verbatim_block_not_flagged(self, tmp_path, settings):
        """A bare <input> inside {% verbatim %} (a docs example) is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "docs.html").write_text(
            "{% verbatim %}<input type='text' name='q'>{% endverbatim %}"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 0

    def test_y003_data_type_before_real_type_not_skipped(self, tmp_path, settings):
        """A `data-type='hidden'` attr must NOT shadow the real `type`.

        Regression: the `_INPUT_TYPE_RE` `\\b` word boundary false-matched
        inside `data-type='hidden'`, so the regex picked `hidden` first and
        the input was wrongly skipped (false negative). With the
        `(?<![\\w-])` lookbehind, the real `type='text'` is selected and the
        unlabelled text input is still flagged.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<input data-type='hidden' type='text' name='q'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert "label" in y003[0].msg

    def test_y003_data_id_not_paired_with_label_for(self, tmp_path, settings):
        """A `data-id` custom attr must NOT pair with a <label for=...>.

        Regression (#1514): the `_CONTROL_ID_RE` `\\b` word boundary
        false-matched inside `data-id='q'` (a `-` is a non-word char), so
        an unlabelled <input> carrying only a `data-id` JS hook had its
        `data-id` value extracted as the control's real `id`; if a
        <label for='q'> existed elsewhere on the page, the genuinely
        unlabelled control was wrongly silenced (false negative). The
        `(?<![\\w-])` lookbehind blocks a match preceded by a hyphen, so
        the control's `data-id` is not read as its real `id` and the
        unlabelled input is still flagged Y003.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<label for='q'>Query</label><input type='text' data-id='q'>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert "label" in y003[0].msg

    def test_y003_data_type_only_treated_as_text_input(self, tmp_path, settings):
        """An <input> with only `data-type` (no real type) is a text control.

        `data-type='hidden'` must not be read as the control's `type`, so
        the input defaults to a user-named text control and an unlabelled
        one is flagged.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<input data-type='hidden' name='q'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y003 = [e for e in errors if e.id == "djust.Y003"]
        assert len(y003) == 1
        assert "label" in y003[0].msg


# ---------------------------------------------------------------------------
# Y004 -- positive tabindex
# ---------------------------------------------------------------------------


class TestY004CheckIntegration:
    """Integration tests for Y004 using check_accessibility() directly."""

    def test_y004_flags_positive_tabindex(self, tmp_path, settings):
        """An element with tabindex='3' is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<div tabindex='3'>panel</div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 1
        assert "tabindex" in y004[0].msg
        assert y004[0].line_number == 1

    def test_y004_flags_multi_digit_positive_tabindex(self, tmp_path, settings):
        """A multi-digit positive tabindex (tabindex='10') is flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text('<div tabindex="10">panel</div>')
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 1

    def test_y004_passes_tabindex_zero(self, tmp_path, settings):
        """tabindex='0' (valid 'add to natural order') is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<div tabindex='0'>panel</div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0

    def test_y004_passes_tabindex_negative(self, tmp_path, settings):
        """tabindex='-1' (valid 'focusable but not tabbable') is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<div tabindex='-1'>panel</div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0

    def test_y004_passes_dynamic_tabindex(self, tmp_path, settings):
        """A {{ }}-interpolated tabindex value is unknowable -> NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "dyn.html").write_text("<div tabindex='{{ idx }}'>panel</div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0

    def test_y004_no_false_positive_on_plain_template(self, tmp_path, settings):
        """A template with no positive tabindex yields zero Y004 (no tautology)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "plain.html").write_text("<div><p>No tabindex here</p></div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0

    def test_y004_suppressed(self, tmp_path, settings):
        """DJUST_CONFIG suppress_checks=['Y004'] silences Y004."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<div tabindex='3'>panel</div>")
        _settings_with_tpl_dir(settings, tpl_dir)
        settings.DJUST_CONFIG = {"suppress_checks": ["Y004"]}

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0

    def test_y004_reports_line_number(self, tmp_path, settings):
        """Y004 result carries an accurate line_number."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<div>\n<p>line two</p>\n<span tabindex='5'>x</span>\n</div>"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 1
        assert y004[0].line_number == 3

    def test_y004_verbatim_block_not_flagged(self, tmp_path, settings):
        """A positive tabindex inside {% verbatim %} (a docs example) is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "docs.html").write_text(
            "{% verbatim %}<div tabindex='3'>panel</div>{% endverbatim %}"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0

    def test_y004_data_tabindex_not_flagged(self, tmp_path, settings):
        """A `data-tabindex='5'` custom attribute must NOT be flagged.

        Regression: the `_POSITIVE_TABINDEX_RE` `\\b` word boundary sat
        between `data-` and `tabindex` (a `-` is a non-word char), so a
        JS-driven custom attribute like `data-tabindex='5'` false-matched
        as a Y004 positive-tabindex defect. The `(?<![\\w-])` lookbehind
        blocks a match preceded by a hyphen, silencing the false positive.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text("<div data-tabindex='5'>panel</div>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        y004 = [e for e in errors if e.id == "djust.Y004"]
        assert len(y004) == 0


# ---------------------------------------------------------------------------
# check_accessibility -- general behaviour
# ---------------------------------------------------------------------------


class TestCheckAccessibilityGeneral:
    """Cross-cutting behaviour of check_accessibility()."""

    def test_returns_empty_when_no_template_dirs(self, settings):
        """No configured template dirs -> empty result, no crash."""
        settings.TEMPLATES = []
        assert check_accessibility(None) == []

    def test_verbatim_block_not_flagged(self, tmp_path, settings):
        """An icon-only button inside {% verbatim %} (a docs example) is NOT flagged."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "docs.html").write_text(
            "{% verbatim %}<button>&times;</button>{% endverbatim %}"
        )
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        assert [e for e in errors if e.id in ("djust.Y001", "djust.Y002")] == []

    def test_results_are_warnings(self, tmp_path, settings):
        """Y001/Y002 emit DjustWarning (not Error) so check doesn't fail hard."""
        from djust.checks import DjustWarning

        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text("<button>&times;</button><img src='/static/logo.png'>")
        _settings_with_tpl_dir(settings, tpl_dir)

        errors = check_accessibility(None)
        relevant = [e for e in errors if e.id in ("djust.Y001", "djust.Y002")]
        assert len(relevant) == 2
        assert all(isinstance(e, DjustWarning) for e in relevant)


# ---------------------------------------------------------------------------
# Meta-check (#1517) -- no bare `\b`-anchored HTML-attribute regexes
# ---------------------------------------------------------------------------

# A compiled-regex pattern that LEADS with a literal `\b` then an
# attribute-name-like literal (word chars / hyphens) then `\s*=` or `=`
# is the #1514 / #1512 bug shape: `\b` matches the `data-`/name boundary
# (a `-` is a non-word char), so `\balt=` false-matches `data-alt=`. The
# correct anchor is the `(?<![\w-])` negative lookbehind.
#
# Note: `pattern.pattern` is the regex *source string*, so a leading
# backslash-b is the two LITERAL characters `\` + `b`. In this detection
# regex `\\b` matches that literal escape sequence; `[A-Za-z][\w-]*`
# matches the attribute name; `(?:\\s\*)?` optionally matches a literal
# `\s*` (whitespace-tolerance) before the `=`.
_BARE_B_ATTR_RE = re.compile(r"^\\b\(?[A-Za-z][\w-]*(?:\|[\w-]+)*\)?(?:\\s\*)?=")

# The four `_LIVE_RENDER_*` regexes also lead with a literal `\b`, but
# they scan the body of a `{% live_render ... %}` template tag, NOT HTML
# attributes. Django template-tag kwargs are space-separated `name=value`
# tokens with no `data-`/`aria-` prefix convention, so `\bsticky` cannot
# false-match a `data-sticky`-style token (no such thing in tag syntax).
# They are NOT the #1514 bug class — allowlisted with this explanation.
_BARE_B_KWARG_ALLOWLIST = frozenset(
    {
        "_LIVE_RENDER_STICKY_TRUTHY_RE",
        "_LIVE_RENDER_LAZY_TRUTHY_RE",
        "_LIVE_RENDER_STICKY_FALSY_RE",
        "_LIVE_RENDER_LAZY_FALSY_RE",
    }
)


class TestChecksRegexHardening:
    """Meta-check (#1517): no bare `\\b`-anchored HTML-attribute regexes."""

    def test_detection_regex_matches_pre_fix_1514_shape(self):
        """The meta-check's detection regex matches the pre-#1514 `\\balt\\s*=`.

        Doc-claim pin (Action #1046): without reverting the source, this
        asserts the meta-check would have caught the #1514 bug shape —
        the literal pre-fix pattern string of `_IMG_HAS_ALT_RE`.
        """
        assert _BARE_B_ATTR_RE.match(r"\balt\s*=") is not None
        # The sibling pre-fix shapes (#1512 / the other 3 #1514 regexes).
        assert _BARE_B_ATTR_RE.match(r"\bhref\s*=") is not None
        assert _BARE_B_ATTR_RE.match(r"\bid\s*=") is not None
        assert _BARE_B_ATTR_RE.match(r"\b(aria-label|aria-labelledby|title)\s*=") is not None
        # The fixed `(?<![\w-])` anchor must NOT match.
        assert _BARE_B_ATTR_RE.match(r"(?<![\w-])alt\s*=") is None

    def test_live_render_kwarg_regexes_are_allowlisted(self):
        """The `_LIVE_RENDER_*` kwarg regexes share the bare-`\\b` shape but
        are the documented allowlist, not the #1514 bug class.

        The `_LIVE_RENDER_*` regexes lead with `\\bsticky=` / `\\blazy=`,
        so the detection regex DOES match them — by design they look like
        the bug shape. The load-bearing discriminator is the explicit
        4-name allowlist (`_BARE_B_KWARG_ALLOWLIST`), which documents that
        they scan `{% live_render %}` template-tag kwargs (no `data-*`
        prefix convention exists in `{% %}` syntax), not HTML attributes.
        This asserts that contract: the regex matches, the allowlist
        names the four constants, and the four exist in checks.py.
        """
        kwarg_pattern = r"""\bsticky\s*=\s*(?:True|"[^"]+"|'[^']+'|[A-Za-z_]\w*)"""
        # The detection regex flags the kwarg shape too (by design).
        assert _BARE_B_ATTR_RE.match(kwarg_pattern) is not None
        # The allowlist is the discriminator — exactly the 4 kwarg regexes.
        assert _BARE_B_KWARG_ALLOWLIST == frozenset(
            {
                "_LIVE_RENDER_STICKY_TRUTHY_RE",
                "_LIVE_RENDER_LAZY_TRUTHY_RE",
                "_LIVE_RENDER_STICKY_FALSY_RE",
                "_LIVE_RENDER_LAZY_FALSY_RE",
            }
        )
        # Every allowlisted name must be a real compiled regex in checks.py
        # (a stale allowlist entry would silently weaken the meta-check).
        for name in _BARE_B_KWARG_ALLOWLIST:
            val = getattr(_checks_mod, name, None)
            assert isinstance(val, re.Pattern), f"{name} is not a compiled regex"
            assert _BARE_B_ATTR_RE.match(val.pattern), (
                f"{name} no longer matches the bare-`\\b` shape — remove it from the allowlist"
            )

    def test_no_bare_b_anchor_before_attribute_name_in_checks_regexes(self):
        """Meta-check (#1517): no compiled regex in checks.py may anchor an
        attribute-name match with a bare `\\b`.

        `\\b` matches the `data-`/name boundary because `-` is a non-word
        char, so `\\balt=` false-matches `data-alt=`. This bug class has
        appeared multiple times (`_IMG_HAS_ALT_RE` + 3 siblings #1514,
        `_INPUT_TYPE_RE` / `_POSITIVE_TABINDEX_RE` fixed in #1512). The
        correct anchor is the `(?<![\\w-])` negative lookbehind. This test
        fails fast on any future regression.

        The four `_LIVE_RENDER_*` regexes are allowlisted: they scan
        `{% live_render %}` template-tag kwargs, not HTML attributes, and
        so are not the #1514 bug class.
        """
        offenders = []
        for name, val in vars(_checks_mod).items():
            if not isinstance(val, re.Pattern):
                continue
            if name in _BARE_B_KWARG_ALLOWLIST:
                continue
            if _BARE_B_ATTR_RE.match(val.pattern):
                offenders.append((name, val.pattern))
        assert not offenders, (
            "checks.py regex(es) anchor an attribute name with a bare `\\b` "
            "(false-matches data-* / custom attributes — use `(?<![\\w-])`): "
            + ", ".join(f"{n}={p!r}" for n, p in offenders)
        )
