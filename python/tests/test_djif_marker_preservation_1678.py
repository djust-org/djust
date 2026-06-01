"""#1678 — `_strip_comments_and_whitespace` must PRESERVE dj-if boundary markers.

The hydrated-mount path (and SSE / html_recovery) runs the rendered HTML through
`_strip_comments_and_whitespace` before sending it to the client. It stripped
ALL comments, including `<!--dj-if …-->` / `<!--/dj-if-->` boundary markers —
which the Rust VDOM parser keeps as significant children and the client differ
resolves patch paths against. So the client DOM lost every dj-if marker while
the server's `last_vdom` kept them; on a multi-`{% if %}` container (a tabbed
dashboard: one `{% if active_tab == X %}` block per tab) the server's positional
patch paths over-counted the client's children → every event fell back to
`html_recovery` (observed live: `Path traversal failed at index 8, only 3
children`).

Gate-off: revert the regex to `<!--.*?-->` → the dj-if markers are stripped and
these assertions fail.
"""

import pytest
from djust import LiveView


class _V(LiveView):
    template = "<div>x</div>"

    def mount(self, request):
        pass


def _strip(html):
    return _V()._strip_comments_and_whitespace(html)


def test_preserves_dj_if_open_and_close_markers():
    html = '<div><!--dj-if id="if-388b9d73-57"--><section>k</section><!--/dj-if--></div>'
    out = _strip(html)
    assert '<!--dj-if id="if-388b9d73-57"-->' in out
    assert "<!--/dj-if-->" in out
    assert "<section>k</section>" in out


def test_preserves_empty_dj_if_pair():
    # An inactive tab's false {% if %} renders an empty marker pair — it MUST
    # survive so the client child count matches the server vdom.
    out = _strip('<div class="tabs-content"><!--dj-if id="a"--><!--/dj-if--></div>')
    assert out.count("<!--dj-if") == 1
    assert "<!--/dj-if-->" in out


def test_still_strips_regular_comments():
    out = _strip("<div><!-- regular comment -->Content<!--djbug capture-->more</div>")
    assert "regular comment" not in out
    assert "djbug capture" not in out
    assert "Content" in out and "more" in out


def test_multi_tab_marker_count_preserved_1678():
    # The exact failing shape: 8 tab conditionals = 8 dj-if pairs (7 empty + 1
    # active). All 16 markers must survive the strip (none did before the fix).
    blocks = "".join(f'<!--dj-if id="if-x-{i}"--><!--/dj-if-->' for i in range(7))
    active = '<!--dj-if id="if-x-7"--><div class="kanban">cards</div><!--/dj-if-->'
    html = f'<div class="tabs-content">{blocks}{active}</div>'
    out = _strip(html)
    assert out.count("<!--dj-if id=") == 8  # 8 open markers
    assert out.count("<!--/dj-if-->") == 8  # 8 close markers
    assert '<div class="kanban">cards</div>' in out


@pytest.mark.parametrize("preserve_markers", [True, False])
def test_gate_off_self_check(preserve_markers):
    """Documents the gate-off: with the dj-if-preserving regex the marker
    survives; the legacy strip-all regex removes it (this branch shows the bug
    the fix prevents)."""
    import re

    html = '<!--dj-if id="x"--><b>k</b><!--/dj-if-->'
    if preserve_markers:
        out = re.sub(r"<!--(?!\s*/?dj-if\b).*?-->", "", html, flags=re.DOTALL)
        assert "<!--dj-if" in out  # fixed behavior
    else:
        out = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        assert "<!--dj-if" not in out  # the bug (legacy regex)
