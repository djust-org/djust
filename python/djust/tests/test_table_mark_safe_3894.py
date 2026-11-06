"""A marked-safe row value is data, not a URL or an attribute (#3894).

``table.html`` reads a row's URL with ``table_href`` and a link column's class from the column.
``mark_safe`` on a CELL is how a table keeps markup in a TEXT cell; it never meant "and use it as a
URL" or "and write it into an attribute". ``url_attr`` honours a ``SafeData`` value by default
(a developer's own tag argument), so a marked-safe row value skipped both the scheme allowlist and
the escaping in ``data-href`` / ``href``, and a marked-safe ``link_class`` or row id left its
attribute. The rule of the table is now: a row value that reaches an ATTRIBUTE is checked and
escaped whatever it is marked as; a row value that reaches TEXT stays as marked.

Every case is rendered with each way a value can claim to be safe: ``mark_safe`` (``SafeString``),
a ``SafeString`` subclass, a lazy string whose result is marked safe, and ``markupsafe.Markup``
(an ``__html__`` object, which Django's ``conditional_escape`` also honours).
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.template import Context, Engine
from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe
from markupsafe import Markup

import djust.components
from djust.tests.test_components_csp_aria import _rust
from scripts.lib.template_library_parity import url_sweep
from pathlib import Path

_ENGINE = Engine(
    libraries={"djust_components": "djust.components.templatetags.djust_components"},
    dirs=[str(Path(djust.components.__file__).parent / "templates")],
)


def _django(src: str, ctx: dict) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + src).render(Context(ctx))


class _SafeSub(SafeString):
    """A subclass of the safe string type: ``isinstance(x, SafeData)`` is still true."""


def _lazy_safe(text: str) -> object:
    return lazy(lambda: mark_safe(text), str)()


#: How a value claims to be safe. Each takes the text and returns the claim.
CLAIMS = {
    "mark_safe": mark_safe,
    "safestring-subclass": _SafeSub,
    "lazy-mark_safe": _lazy_safe,
    "markupsafe-Markup": Markup,
}

#: The payloads: a script scheme (three spellings) and an attribute breakout.
SCHEMES = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "data:text/html,<b>"]
BREAKOUTS = [
    '/x" onmouseover="alert(1)" x="',
    "/x' onmouseover='alert(1)",
    "/x><script>alert(1)</script>",
    '/x"><img src=x onerror=alert(1)>',
]


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.anchors: list[dict[str, str | None]] = []
        self.handlers: list[tuple[str, str]] = []
        self.checkboxes: list[dict[str, str]] = []
        self.sortable: list[dict[str, str]] = []
        self._body = False

    def handle_starttag(self, tag, attrs):
        a = {n: (v or "") for n, v in attrs}
        self.handlers += [(tag, n) for n in a if n.startswith("on")]
        if tag == "th" and "dj-click" in a:
            self.sortable.append(a)
        if tag == "tbody":
            self._body = True
        elif tag == "tr" and self._body:
            self.rows.append(a)
        elif tag == "a":
            self.anchors.append(a)
        elif tag == "input" and a.get("type") == "checkbox" and self._body:
            self.checkboxes.append(a)

    def handle_endtag(self, tag):
        if tag == "tbody":
            self._body = False


def page(markup: str) -> _Page:
    p = _Page()
    p.feed(markup)
    p.close()
    return p


COLS = [{"key": "name", "label": "N", "link": "u", "link_class": "lk"}]


def render(row: dict, cols: list | None = None, args: str = 'row_url="u"') -> str:
    return _django(
        "{% data_table rows=rows columns=cols " + args + " %}",
        {"rows": [row], "cols": cols or COLS},
    )


# --- the URL: row_url (data-href) and a link column (href) --------------------------------------


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", SCHEMES)
def test_a_marked_safe_script_url_is_neutralised(claim: str, payload: str) -> None:
    html = render({"id": 1, "name": "N", "u": CLAIMS[claim](payload)})
    p = page(html)
    assert p.rows[0]["data-href"] == "#", html
    assert [a["href"] for a in p.anchors] == ["#"], html
    assert url_sweep.findings(html) == [], html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", BREAKOUTS)
def test_a_marked_safe_breakout_stays_inside_the_url_attribute(claim: str, payload: str) -> None:
    html = render({"id": 1, "name": "N", "u": CLAIMS[claim](payload)})
    p = page(html)
    assert p.handlers == [], html
    assert p.rows[0]["data-href"] == payload, html
    assert [a["href"] for a in p.anchors] == [payload], html
    assert "<script>" not in html and "<img" not in html, html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
def test_a_marked_safe_ordinary_url_is_kept(claim: str) -> None:
    """The fix does not turn every marked-safe value into ``#``: a good URL is a good URL."""
    url = "https://example.com/a?b=1&c=2"
    html = render({"id": 1, "name": "N", "u": CLAIMS[claim](url)})
    p = page(html)
    assert p.rows[0]["data-href"] == url, html
    assert [a["href"] for a in p.anchors] == [url], html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", SCHEMES + BREAKOUTS)
def test_an_unmarked_value_is_the_reference_for_the_marked_one(claim: str, payload: str) -> None:
    """A value that says it is safe gets exactly what the same text gets when it does not."""
    marked = page(render({"id": 1, "name": "N", "u": CLAIMS[claim](payload)}))
    plain = page(render({"id": 1, "name": "N", "u": payload}))
    assert marked.rows[0]["data-href"] == plain.rows[0]["data-href"]
    assert [a["href"] for a in marked.anchors] == [a["href"] for a in plain.anchors]


# --- the link class -----------------------------------------------------------------------------


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", BREAKOUTS + ["<b>x</b> a&b"])
def test_a_marked_safe_link_class_stays_inside_the_class_attribute(claim: str, payload: str) -> None:
    cols = [{"key": "name", "label": "N", "link": "u", "link_class": CLAIMS[claim](payload)}]
    html = render({"id": 1, "name": "N", "u": "/x"}, cols)
    p = page(html)
    assert p.handlers == [], html
    assert [a["class"] for a in p.anchors] == [payload], html
    assert "<b>" not in html and "<script>" not in html and "<img" not in html, html


# --- the row id written into the selection checkbox's data-value -------------------------------


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", BREAKOUTS)
def test_a_marked_safe_row_id_stays_inside_the_checkbox_value(claim: str, payload: str) -> None:
    html = render(
        {"id": CLAIMS[claim](payload), "name": "N", "u": "/x"},
        args="selectable=True selected_rows=sel",
    )
    p = page(html)
    assert p.handlers == [], html
    assert [c["data-value"] for c in p.checkboxes] == [payload], html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", BREAKOUTS)
def test_a_marked_safe_row_click_value_stays_inside_data_value(claim: str, payload: str) -> None:
    """Already escaped by #3884; pinned for every kind of claim."""
    html = render(
        {"id": CLAIMS[claim](payload), "name": "N", "u": "/x"},
        args='row_click_event="rc"',
    )
    p = page(html)
    assert p.handlers == [], html
    assert p.rows[0]["data-value"] == payload, html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", BREAKOUTS)
def test_a_marked_safe_column_key_stays_inside_the_sort_value(claim: str, payload: str) -> None:
    """The header's ``data-value`` is the column key: the same attribute rule (a folded-in sink)."""
    cols = [{"key": CLAIMS[claim](payload), "label": "N"}]
    html = render({"id": 1}, cols, args="")
    p = page(html)
    assert p.handlers == [], html
    assert [t["data-value"] for t in p.sortable] == [payload], html


# --- the TEXT cell keeps what the developer marked safe (the legitimate use) -------------------


@pytest.mark.parametrize("claim", sorted(CLAIMS))
def test_a_marked_safe_text_cell_still_renders_its_markup(claim: str) -> None:
    plain_cols = [{"key": "name", "label": "N"}]  # no link: the plain-text branch of the cell
    html = render({"id": 1, "name": CLAIMS[claim]("<i>italic</i>")}, plain_cols, args="")
    assert "<td><i>italic</i></td>" in html.replace("\n", "").replace("  ", ""), html
    assert "<a " not in html, html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
def test_a_marked_safe_text_cell_inside_a_link_still_renders_its_markup(claim: str) -> None:
    html = render({"id": 1, "name": CLAIMS[claim]("<i>italic</i>"), "u": "/x"})
    assert "<i>italic</i></a>" in html.replace("\n", "").replace("  ", ""), html


def test_an_unmarked_text_cell_is_escaped() -> None:
    plain_cols = [{"key": "name", "label": "N"}]
    html = render({"id": 1, "name": "<i>italic</i>"}, plain_cols, args="")
    assert "<i>" not in html and "&lt;i&gt;" in html, html


# --- the native engine: a marked-safe value is text there, so it was already safe --------------


def _native_draws_row_urls() -> bool:
    html = _rust(
        '{% data_table rows=rows columns=cols row_url="u" %}',
        {"rows": [{"id": 1, "name": "N", "u": "/x"}], "cols": COLS},
    )
    return "data-href" in html


@pytest.mark.skipif(
    not _native_draws_row_urls(), reason="the native row_url / link column land with #3887"
)
@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", SCHEMES + BREAKOUTS)
def test_both_engines_agree_on_a_marked_safe_url(claim: str, payload: str) -> None:
    ctx = {"rows": [{"id": 1, "name": "N", "u": CLAIMS[claim](payload)}], "cols": COLS}
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    py, rs = page(_django(src, ctx)), page(_rust(src, ctx))
    assert py.rows[0]["data-href"] == rs.rows[0]["data-href"]
    assert [a["href"] for a in py.anchors] == [a["href"] for a in rs.anchors]
    assert [a["class"] for a in py.anchors] == [a["class"] for a in rs.anchors]
    assert py.handlers == rs.handlers == []


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", BREAKOUTS)
def test_both_engines_agree_on_a_marked_safe_row_id_in_the_checkbox(
    claim: str, payload: str
) -> None:
    ctx = {"rows": [{"id": CLAIMS[claim](payload), "name": "N"}], "cols": COLS, "sel": []}
    src = "{% data_table rows=rows columns=cols selectable=True selected_rows=sel %}"
    py, rs = page(_django(src, ctx)), page(_rust(src, ctx))
    assert [c["data-value"] for c in py.checkboxes] == [c["data-value"] for c in rs.checkboxes]
    assert py.handlers == rs.handlers == []
