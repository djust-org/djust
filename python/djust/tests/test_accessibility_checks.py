"""Tests for the accessibility (Y0xx) system checks (djust/checks.py).

Mirrors the TestT004* pattern in python/tests/test_checks.py — a regex /
heuristic-unit class plus an integration class that writes fixture
templates into tmp_path, points settings.TEMPLATES['DIRS'] at them,
calls check_accessibility(None) directly, and filters results by e.id.
"""

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
