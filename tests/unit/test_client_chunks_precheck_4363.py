"""The chunk pre-check is a substring filter, with the same result as the regex it replaced (#4363).

``chunks_in_markup`` ran one alternation regex over the whole page, about
0.25 us per character (20 ms on an 82 KB page) on every page GET. It now checks
each chunk's marker words as plain substrings of the lower-cased page; the tag
scan stays the final check, so a page finds the same chunks as before.
"""

from __future__ import annotations

import re
from typing import FrozenSet

import pytest

from djust import _client_chunks as cc


def _reference(masked: str) -> FrozenSet[str]:
    """The pre-#4363 ``chunks_in_markup``: each marker's whole-word regex, then the tag scan."""
    candidates = {
        name for name, (src, _) in cc._MARKERS.items() if re.search(src, masked, re.IGNORECASE)
    }
    markup = cc._INERT_RE.sub(" ", masked)
    found = set()
    for m in cc._TAG_RE.finditer(markup):
        tag, attrs = m.group(1).lower(), m.group(2)
        for name in candidates:
            if cc._MARKERS[name][1](tag, attrs):
                found.add(name)
    return frozenset(found)


PAGES = [
    '<div dj-upload="avatar"></div>',
    '<DIV DJ-UPLOAD-DROP="avatar"></DIV>',
    '<div data-x="dj-uploader"></div>',
    '<ul dj-virtual="rows"></ul>',
    '<div dj-hook="CursorOverlay"></div>',
    '<div dj-hook="cursoroverlay"></div>',
    '<div id="dj-tutorial-bubble"></div>',
    '<div class="rich-select open"></div>',
    '<div class="not-rich-select"></div>',
    '<div role="dialog"></div>',
    '<div role="tablist"></div>',
    '<div class="dj-modal"></div>',
    "<p>a dialog about tablist</p>",
    "<li dj-flip></li>",
    '<div dj-transition="fade"></div>',
    "<div dj-transition-group></div>",
    '<div dj-remove="fade"></div>',
    '<div dj-viewport-bottom="more"></div>',
    '<a href="/x" dj-prefetch>x</a>',
    "<span dj-prefetch>x</span>",
    '<script src="/a.js" dj-track-static></script>',
    "<dialog dj-dialog open></dialog>",
    "<div dj-dialog></div>",
    '<template><div dj-upload="a"></div></template>',
    "<p>plain text with no markers at all</p>",
    '<span class="rte-toolbar"></span><div class="combobox-option"></div>',
    "<p>dialogs and tablists</p>",
    '<div class="dropzone kanban split-pane" dj-flip dj-virtual="r"></div>',
]


@pytest.mark.parametrize("page", PAGES)
def test_same_chunks_as_the_regex_pre_check(page):
    assert cc.chunks_in_markup(page) == _reference(page)


def test_every_chunk_has_pre_check_words():
    assert set(cc._NEEDLES) == set(cc._MARKERS)
    for words in cc._NEEDLES.values():
        assert words and all(w == w.lower() for w in words)
