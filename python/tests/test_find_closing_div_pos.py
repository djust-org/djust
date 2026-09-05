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
            "</div>"  # closes div a OR div b
            "</div>"  # closes dj-root
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
            "</div>"  # closes one of the three branches
            "</div>"  # closes dj-root
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
            "</div>"  # closes the if/else div
            "</div>"  # closes dj-root
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
            "</div>"  # closes whichever branch was taken
            "</div>"  # closes dj-root
        )
        mixin = TemplateMixin()
        result = mixin._extract_liveview_root_with_wrapper(template)
        assert result == template


class TestRawTextBodiesAreNotMarkup:
    """#2663 — a tag-like string inside ``<script>``/``<style>`` or an HTML
    comment is raw text, not markup, and must not move the div depth.

    Symptom-up: ``get_template()`` → ``_extract_liveview_root_with_wrapper``
    → this scanner. A JavaScript comment reading ``<div dj-root>`` inside a
    ``<script>`` after the real root counted as an open, the depth never
    returned to 0, ``(None, None)`` came back, and the WHOLE resolved
    document became the liveview template — nested inside the shell's
    dj-root, so the page rendered its shell twice (two ``<footer>``s).
    """

    def _find(self, template, marker="<div dj-root>"):
        m = re.search(re.escape(marker), template)
        assert m
        return TemplateMixin._find_closing_div_pos(template, m.end())

    def test_div_open_in_script_comment_after_root(self):
        t = (
            "<div dj-root><p>hello</p></div>"
            "<script>\n  // base.html wraps the content block in <div dj-root>\n</script>"
            "<footer></footer>"
        )
        close, end = self._find(t)
        assert close is not None, "script raw text counted as a real <div> (#2663)"
        assert t[close:end] == "</div>"
        assert t[end:].startswith("<script>")

    def test_div_open_in_script_inside_root(self):
        t = "<div dj-root><script>var s = '<div class=\"x\">';</script><p>a</p></div><b>after</b>"
        close, end = self._find(t)
        assert t[end:] == "<b>after</b>"

    def test_div_close_in_script_inside_root_does_not_close_early(self):
        t = "<div dj-root><script>var s = '</div>';</script><p>a</p></div><b>after</b>"
        close, end = self._find(t)
        assert t[end:] == "<b>after</b>"

    def test_style_body_is_raw_text(self):
        t = "<div dj-root><style>/* <div> */ .x{}</style></div><b>after</b>"
        close, end = self._find(t)
        assert t[end:] == "<b>after</b>"

    def test_html_comment_is_not_markup(self):
        t = "<div dj-root><!-- <div dj-root> --><p>a</p></div><b>after</b>"
        close, end = self._find(t)
        assert t[end:] == "<b>after</b>"

    def test_script_tag_case_and_attributes(self):
        t = '<div dj-root><SCRIPT type="module">// <div>\n</SCRIPT></div><b>after</b>'
        close, end = self._find(t)
        assert t[end:] == "<b>after</b>"

    def test_positions_are_reported_against_the_original_string(self):
        """Masking must be length-preserving so the returned offsets index the
        caller's string, not a transformed copy."""
        t = "<div dj-root><script>// <div></script><span>x</span></div>tail"
        close, end = self._find(t)
        assert t[close:end] == "</div>"
        assert t[end:] == "tail"

    def test_extract_wrapper_returns_only_the_root(self):
        """The caller that produced the doubled page: extraction must return
        the root element, never the whole document."""
        from djust.mixins.template import TemplateMixin as TM

        doc = (
            "<!DOCTYPE html><html><body><nav></nav>"
            "<div dj-root><p>hello</p></div>"
            "<footer></footer>"
            "<script>// this comment mentions <div dj-root></script>"
            "</body></html>"
        )
        got = TM._extract_liveview_root_with_wrapper(TM.__new__(TM), doc)
        assert got == "<div dj-root><p>hello</p></div>", got


class TestEveryRootLocatorUsesTheMaskedSearch:
    """#2663 count-pin (#1125): every dj-root locating sink in
    ``mixins/template.py`` goes through ``_search_dj_root_open`` (which
    searches a raw-text-masked copy). A new sink written with a bare
    ``_DJ_ROOT_RE.search(...)`` / ``re.search(r"<div\\s+...dj-root...")``
    re-opens the class; this pins the caller SET, not a floor."""

    @staticmethod
    def _source():
        import inspect

        import djust.mixins.template as mod

        return inspect.getsource(mod)

    def test_no_bare_root_regex_search_outside_the_helper(self):
        src = self._source()
        # Strip the helper's own body, then look for any direct search.
        helper_start = src.index("def _search_dj_root_open(")
        helper_end = src.index("\n\n\n", helper_start)
        outside = src[:helper_start] + src[helper_end:]
        for needle in (
            "_DJ_ROOT_RE.search(",
            "_DJ_VIEW_RE.search(",
            "_LOOSE_DJ_ROOT_RE.search(",
            "_LOOSE_DJ_VIEW_RE.search(",
            're.search(r"<div\\s+',
        ):
            assert needle not in outside, (
                f"{needle} used outside _search_dj_root_open — a dj-root locator "
                "that does not mask <script>/<style>/<!-- --> raw text (#2663)"
            )

    def test_the_helper_is_called_at_every_sink(self):
        src = self._source()
        calls = src.count("_search_dj_root_open(") - src.count("def _search_dj_root_open(")
        # get_template's source pick, arender_chunks, the streaming splitter,
        # render_full_template step 3, and the four extraction helpers.
        assert calls == 8, calls

    def test_the_depth_walk_scans_the_masked_copy(self):
        src = self._source()
        walker_start = src.index("def _find_closing_div_pos(")
        walker_end = src.index("\n    def ", walker_start + 10)
        assert "template = _mask_raw_text(template)" in src[walker_start:walker_end]
