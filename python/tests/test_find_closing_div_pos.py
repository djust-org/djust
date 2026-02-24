"""
Tests for TemplateMixin._find_closing_div_pos() — the branch-aware div-close finder.

Regression tests for Issue #365: div depth counter miscounts when
{% if/else %} branches both open a <div> sharing a single </div>.
"""

import re

from djust.mixins.template import TemplateMixin


class TestFindClosingDivPos:
    """Tests for the branch-aware div-close finder."""

    def _find(self, template, marker="<div dj-root>"):
        """Helper: find the closing </div> for the first dj-root div."""
        m = re.search(re.escape(marker), template)
        assert m, f"{marker!r} not found in template"
        result = TemplateMixin._find_closing_div_pos(template, m.end())
        return result

    def test_simple_div(self):
        t = "<div dj-root><p>hello</p></div>"
        close, end = self._find(t)
        assert t[close:end] == "</div>"
        assert t[end:] == ""

    def test_nested_div(self):
        t = "<div dj-root><div class='inner'>x</div></div>"
        close, end = self._find(t)
        assert t[:end] == t  # end is at the very end

    def test_if_else_shared_close(self):
        """Regression: if/else each open a div but share a single close."""
        t = (
            "<div dj-root>"
            "{% if a %}<div class='a'>{% else %}<div class='b'>{% endif %}"
            "content"
            "</div>"   # closes div a OR div b
            "</div>"   # closes dj-root
        )
        close, end = self._find(t)
        assert t[end:] == ""  # matched the outermost close

    def test_balanced_if_else(self):
        """if/else where each branch has its own open AND close."""
        t = (
            "<div dj-root>"
            "{% if a %}<div class='a'>x</div>{% else %}<div class='b'>y</div>{% endif %}"
            "</div>"
        )
        close, end = self._find(t)
        assert t[end:] == ""

    def test_no_match_returns_none(self):
        t = "<div dj-root><p>unclosed"
        close, end = self._find(t)
        assert close is None and end is None

    def test_trailing_content_after_close(self):
        """Content after the closing </div> should not be included."""
        t = "<div dj-root><p>hi</p></div><footer>footer</footer>"
        close, end = self._find(t)
        assert t[end:] == "<footer>footer</footer>"

    def test_elif_branch(self):
        """elif branch resets depth correctly like else."""
        t = (
            "<div dj-root>"
            "{% if a %}<div class='a'>"
            "{% elif b %}<div class='b'>"
            "{% else %}<div class='c'>"
            "{% endif %}"
            "content"
            "</div>"   # closes one of the three branches
            "</div>"   # closes dj-root
        )
        close, end = self._find(t)
        assert t[end:] == ""

    def test_nested_if_inside_div(self):
        """if/else inside a nested div should not affect outer depth counting."""
        t = (
            "<div dj-root>"
            "<div class='wrapper'>"
            "{% if x %}<span>yes</span>{% else %}<span>no</span>{% endif %}"
            "</div>"
            "</div>"
        )
        close, end = self._find(t)
        assert t[end:] == ""


class TestExtractLiveviewRootWithWrapper:
    """Caller-level regression tests for _extract_liveview_root_with_wrapper.

    These tests exercise the full extraction path through the caller so a
    future refactor that misuses the _find_closing_div_pos return tuple
    would be caught here, not just in the unit tests above.
    """

    def test_if_else_shared_close_extracts_correctly(self):
        """Regression #365: if/else sharing a single </div> must not return full template."""
        template = (
            "<div dj-root>"
            "{% if priority %}<div class='high'>{% else %}<div class='normal'>{% endif %}"
            "content"
            "</div>"    # closes the if/else div
            "</div>"    # closes dj-root
        )
        mixin = TemplateMixin()
        result = mixin._extract_liveview_root_with_wrapper(template)
        assert result == template
        assert result.startswith("<div dj-root>")
        # Must NOT return the entire string unchanged due to depth miscount
        # (if it does, the fallback `return template` path was hit instead)
        assert result.endswith("</div>")

    def test_simple_root_extraction(self):
        """Baseline: simple dj-root div is extracted correctly."""
        template = "<header>nav</header><div dj-root><p>hello</p></div><footer>f</footer>"
        mixin = TemplateMixin()
        result = mixin._extract_liveview_root_with_wrapper(template)
        assert result == "<div dj-root><p>hello</p></div>"

    def test_elif_shared_close_extracts_correctly(self):
        """elif variant of the #365 bug — three-way branch sharing one close."""
        template = (
            "<div dj-root>"
            "{% if a %}<div class='a'>"
            "{% elif b %}<div class='b'>"
            "{% else %}<div class='c'>"
            "{% endif %}"
            "content"
            "</div>"    # closes whichever branch was taken
            "</div>"    # closes dj-root
        )
        mixin = TemplateMixin()
        result = mixin._extract_liveview_root_with_wrapper(template)
        assert result == template
