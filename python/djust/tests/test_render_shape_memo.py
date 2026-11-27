"""``render_shape`` remembers what it found for HTML it has already walked.

The walk is a pure function of the HTML and costs about 0.2 ms per KB in
Python. A page-POST fallback turn walked its baseline twice and the next turn's
baseline is the answer it walked, so the memo halves the walks of a fallback
turn and makes repeated GETs of one page walk once (``make benchmark-components``:
``turn/table/http`` -31%, ``page/big/get`` -41%).
"""

from __future__ import annotations

from djust import _render_digest as rd


def _page(n: int) -> str:
    return '<div dj-root><ul>%s</ul><p dj-update="ignore">x</p></div>' % "".join(
        "<li id='r%d'>row %d &amp; more</li>" % (i, i) for i in range(n)
    )


def test_a_second_walk_of_the_same_html_is_served_from_the_memo(monkeypatch):
    calls = []
    real = rd._walk_shape
    monkeypatch.setattr(rd, "_walk_shape", lambda html: calls.append(html) or real(html))
    monkeypatch.setattr(rd, "_shape_memo", type(rd._shape_memo)())
    html = _page(5)
    first = rd.render_shape(html)
    second = rd.render_shape("".join(list(html)))  # equal text, another object
    assert len(calls) == 1
    assert second is first
    assert first.shape == real(html).shape
    assert first.opaque_paths == real(html).opaque_paths == [(1,)]
    # dom_shape and tokens_match go through the same memo.
    assert rd.dom_shape(html) == first.shape and len(calls) == 1


def test_changed_html_is_walked_again_and_an_unbalanced_one_is_remembered_as_none(monkeypatch):
    calls = []
    real = rd._walk_shape
    monkeypatch.setattr(rd, "_walk_shape", lambda html: calls.append(html) or real(html))
    monkeypatch.setattr(rd, "_shape_memo", type(rd._shape_memo)())
    assert rd.render_shape(_page(5)).shape != rd.render_shape(_page(6)).shape
    assert len(calls) == 2
    broken = "<div dj-root><p></div>"
    assert rd.render_shape(broken) is None
    assert rd.render_shape(broken) is None
    assert len(calls) == 3
    assert rd.render_shape(None) is None


def test_the_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(rd, "_shape_memo", type(rd._shape_memo)())
    monkeypatch.setattr(rd, "_SHAPE_MEMO_SIZE", 4)
    pages = [_page(n) for n in range(6)]
    for html in pages:
        rd.render_shape(html)
    assert len(rd._shape_memo) == 4
    calls = []
    real = rd._walk_shape
    monkeypatch.setattr(rd, "_walk_shape", lambda html: calls.append(html) or real(html))
    rd.render_shape(pages[-1])  # recent: kept
    rd.render_shape(pages[0])  # oldest: evicted, walked again
    assert calls == [pages[0]]
