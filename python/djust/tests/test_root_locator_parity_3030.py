"""The Python and Rust root locators agree on quoted attribute values (#3030).

``_search_dj_root_open`` ran its regexes over the whole page, and
``re.search`` can start at any ``<``, including one inside a quoted attribute
value: ``<div data-h="<section dj-root>">`` made Python pick the ``<section>``
in the value (and the dj-view stamp's ``"`` then closed ``data-h`` early),
while the Rust locator, which walks tag by tag and skips quoted values, picked
the next real tag. Python now masks those values on the same tag walk
(``_mask_for_root_search``). Both walkers treat ``<`` as a tag start only
before a letter, ``/`` or ``!``.

The Rust half is pinned in ``crates/djust_live/src/lib.rs``
(``dj_root_content_range_2663::*_3030``).

(#3031, dj-root vs dj-view precedence, is NOT changed here: aligning it moves
the VDOM root of a dj-view-only parent that embeds a ``{% live_render %}``
child with its own ``dj-root``. It is a 1.3 item.)
"""

from __future__ import annotations

import time

import pytest

from djust.mixins.template import (
    _DJ_ROOT_RE,
    _DJ_VIEW_RE,
    _mask_for_root_search,
    _search_dj_root_open,
)


def _python_root(html: str) -> str:
    m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert m is not None
    return html[m.start() : m.end()]


class TestQuotedValues3030:
    def test_root_markup_inside_a_quoted_value_is_not_the_root(self):
        html = '<div data-h="<section dj-root>"><main dj-root><p>r</p></main></div>'
        assert _python_root(html) == "<main dj-root>"

    def test_single_quotes_and_a_gt_inside_the_value(self):
        html = "<p title='a > <b dj-root>'>x</p><section dj-root>y</section>"
        assert _python_root(html) == "<section dj-root>"

    def test_lt_that_does_not_start_a_tag_is_text(self):
        # `a < b "…"` is text; the quote there opens no value (the HTML
        # tokenizer's rule, and the Rust walker's since #3030).
        html = '<p>a < b "<main dj-root>"</p>'
        assert _python_root(html) == "<main dj-root>"

    def test_raw_text_regions_are_still_skipped(self):
        html = (
            "<script>var s = '<div dj-root>';</script><!-- <div dj-root> -->"
            "<style>/* <div dj-root> */</style><div dj-root>ok</div>"
        )
        assert _python_root(html) == "<div dj-root>"

    def test_script_inside_a_quoted_value_does_not_start_a_raw_region(self):
        html = '<div title="<script>"><div dj-root>ok</div></div>'
        assert _python_root(html) == "<div dj-root>"

    def test_the_mask_preserves_length(self):
        html = '<div data-h="<x>" t=\'<y\'>a<script>b</script><!--c--></div><p x="<'
        masked = _mask_for_root_search(html)
        assert len(masked) == len(html)

    def test_after_a_tag_that_never_ends_raw_text_is_still_masked(self):
        html = "<p title=\"never closed><script>'<div dj-root>'</script>"
        assert _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE) is None

    @pytest.mark.parametrize(
        "html",
        [
            '<a "' * 20000,
            "<a " * 40000,
            "<script" * 40000,
            '<div x="<"' * 20000,
            "<" * 80000,
            '<a b="c" ' * 30000,
        ],
    )
    def test_the_walk_is_linear_on_tag_soup(self, html):
        start = time.perf_counter()
        _mask_for_root_search(html)
        assert time.perf_counter() - start < 1.0
