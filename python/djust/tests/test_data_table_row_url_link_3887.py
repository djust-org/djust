"""The data table's ``row_url`` and link columns, on both engines (#3887).

``table.html`` (the Python tag, the first render under Django's engine) draws two URL features the
native ``data_table`` handler (every render after the socket opens) did not:

* ``row_url="key"``: each ``<tr>`` carries ``data-href`` (the row's URL), ``role="button"``,
  ``tabindex="0"``, ``style="cursor:pointer"`` and the ``data-table-row-clickable`` class, and the
  table loads ``data-table-row-click.js``, which does the navigating (it reads ``data-href``);
* ``{"key": "name", "link": "url_key", "link_class": "c"}``: the cell is an ``<a href>``.

So a page whose first render went through the Django engine had working links and clickable rows,
and they vanished the moment the socket opened. The Python tag is the reference (maintainer
decision on #3669), and every URL goes through ``url_attr`` (the URL-scheme allowlist).

Each case renders the same template through BOTH engines and compares what a browser acts on: per
body row its navigation attributes and its ``<a>`` elements (href, class, text), and the script
tags. Only the attributes and elements that are navigation are compared; classes, text of other
cells, ids and ARIA are the markup-parity issue (#3663).

Since 2.0 the Rust engine renders the Python tag's own node (one render path per tag), so the
cases the native handler could not match (a ``mark_safe`` URL or ``link_class`` handed over as
text, a row that is not a dict, a callable inside a column dict, #3894 / #3895) render the same on
both engines and are pinned as such. A link whose row value is ``None`` or missing is still
``<a href="None">`` / ``<a href="">``: a decision for the reference (#3895).
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django
from djust.tests.test_components_csp_aria import _rust, assert_strict_csp_safe
from scripts.lib.template_library_parity import url_sweep

SCRIPT_SRC = "/static/djust_components/data-table-row-click.js"

#: The attributes of a ``<tr>`` that make it navigate or look navigable. ``dj-click`` and
#: ``data-value`` are the other row navigation (``row_click_event``), which takes precedence.
ROW_ATTRS = ("class", "data-href", "role", "tabindex", "style", "dj-click", "data-value")


class _Page(HTMLParser):
    """The body rows of a table (navigation attributes, ``<a>`` elements) and its scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self.scripts: list[tuple[str, bool]] = []
        #: ``(tag, name)`` of every attribute named ``on...``: an inline handler, whatever its value.
        self.handlers: list[tuple[str, str]] = []
        self._body = False
        self._row: dict | None = None
        self._anchor: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {n: (v or "") for n, v in attrs}
        self.handlers += [(tag, n) for n in a if n.startswith("on")]
        if tag == "script":
            self.scripts.append((a.get("src", ""), "defer" in a))
        elif tag == "tbody":
            self._body = True
        elif tag == "tr" and self._body:
            self._row = {
                "attrs": {k: a[k] for k in ROW_ATTRS if k in a},
                "anchors": [],
            }
            self.rows.append(self._row)
        elif tag == "a" and self._row is not None:
            self._anchor = {"href": a.get("href"), "class": a.get("class"), "text": ""}
            self._row["anchors"].append(self._anchor)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._anchor = None
        elif tag == "tr":
            self._row = None
        elif tag == "tbody":
            self._body = False

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor["text"] += data


def view(markup: str) -> _Page:
    page = _Page()
    page.feed(markup)
    page.close()
    return page


def nav(markup: str) -> dict:
    """What the two engines must agree on."""
    page = view(markup)
    return {"rows": page.rows, "scripts": page.scripts}


def render_both(src: str, ctx: dict) -> tuple[str, str]:
    return _django(src, ctx), _rust(src, ctx)


def assert_same(src: str, ctx: dict, *, csp: bool = True) -> tuple[str, str]:
    """Both engines draw the same navigation, and neither draws an inline handler.

    ``csp=False`` for a hostile payload: ``assert_strict_csp_safe`` is a regex over the markup, and
    an ESCAPED ``onmouseover=`` inside an attribute value matches it. The parsed attributes are
    what a browser acts on, so the check there is on the parse (``_Page.handlers``).
    """
    py, rs = render_both(src, ctx)
    assert nav(py) == nav(rs), f"{src}\n  python: {nav(py)}\n  rust:   {nav(rs)}\n{py}\n{rs}"
    for html in (py, rs):
        assert view(html).handlers == [], html
        if csp:
            assert_strict_csp_safe(html)
    return py, rs


COLS = [{"key": "name", "label": "Name"}, {"key": "age", "label": "Age"}]
LINKED = [
    {"key": "name", "label": "Name", "link": "u", "link_class": "lk"},
    {"key": "age", "label": "Age"},
]
ROW = {"id": 1, "name": "N", "age": 3, "u": "/claims/1"}


def ctx(rows=None, cols=None, **extra: object) -> dict:
    return dict({"rows": [ROW] if rows is None else rows, "cols": cols or COLS}, **extra)


# --- absolute: the feature is on both engines (a parity of two empty tables proves nothing) ---------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_row_url_makes_a_navigable_row_with_the_script(engine) -> None:
    html = engine('{% data_table rows=rows columns=cols row_url="u" %}', ctx())
    page = view(html)
    assert len(page.rows) == 1, html
    row = page.rows[0]["attrs"]
    assert row["data-href"] == "/claims/1", html
    assert row["class"] == "data-table-row-clickable", html
    assert (row["role"], row["tabindex"], row["style"]) == ("button", "0", "cursor:pointer"), html
    assert page.scripts == [(SCRIPT_SRC, True)], html


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_link_column_makes_an_anchor(engine) -> None:
    html = engine("{% data_table rows=rows columns=cols %}", ctx(cols=LINKED))
    page = view(html)
    assert page.rows[0]["anchors"] == [{"href": "/claims/1", "class": "lk", "text": "N"}], html
    assert page.rows[0]["attrs"] == {}, html  # a link column alone does not make the row navigate
    assert page.scripts == [], html


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_the_cell_of_a_link_column_is_the_rows_text_not_the_url(engine) -> None:
    """``<a href="/claims/1">N</a>``: the text is ``col.key``'s value, ``col.link`` names the URL."""
    html = engine("{% data_table rows=rows columns=cols %}", ctx(cols=LINKED))
    assert '<a href="/claims/1" class="lk">N</a>' in html.replace("\n", "").replace("  ", ""), html


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_no_row_url_no_navigation(engine) -> None:
    html = engine("{% data_table rows=rows columns=cols %}", ctx())
    page = view(html)
    assert page.rows[0]["attrs"] == {} and page.scripts == [], html
    assert "data-href" not in html, html


# --- the parameter's shapes: a key, a variable, a falsy value, a bad key ------------------------------

PARAMS = [
    ('row_url="u"', {}),
    ("row_url=ru", {"ru": "u"}),  # a template variable holding the key
    ('row_url="nokey"', {}),  # a key no row has: an empty data-href
    ('row_url="name"', {}),  # a key whose value is text, not a URL: relative
    ('row_url="id"', {}),  # a number
    ("row_url=None", {}),
    ('row_url=""', {}),
    ("row_url=0", {}),
    ("row_url=False", {}),
    ("row_url=ru_none", {"ru_none": None}),
    ("row_url=ru_empty", {"ru_empty": ""}),
    ("row_url=ru_zero", {"ru_zero": 0}),
    ("row_url=ru_false", {"ru_false": False}),
    ("row_url=ru_list", {"ru_list": ["u"]}),  # unhashable: Python reads it as ""
    ("row_url=undefined_var", {}),
    ('row_url="u" row_click_event="rc"', {}),  # a row click wins; no data-href
    ('row_url="u" row_click_event=None', {}),
    ('row_url="u" row_click_event=""', {}),
    ('row_url=None row_click_event="rc"', {}),
    ('row_click_event="rc"', {}),
    ('row_click_event="rc" row_click_value_key="name"', {}),
    ('row_url="u" selectable=True', {}),
    ('row_url="u" paginate=True page=2 total_pages=4', {}),
    ('row_url="u" striped=True compact=True', {}),
    ('row_url="u" loading=True', {}),  # the script is emitted while the table loads
    ('row_click_event="rc" loading=True', {}),
    ('row_url="u" search=True', {}),
]


@pytest.mark.parametrize(("args", "extra"), PARAMS, ids=[p[0] for p in PARAMS])
def test_row_url_shapes_agree_between_the_engines(args: str, extra: dict) -> None:
    assert_same("{% data_table rows=rows columns=cols " + args + " %}", ctx(**extra))


@pytest.mark.parametrize(("args", "extra"), PARAMS, ids=[p[0] for p in PARAMS])
def test_row_url_shapes_agree_with_a_link_column(args: str, extra: dict) -> None:
    assert_same("{% data_table rows=rows columns=cols " + args + " %}", ctx(cols=LINKED, **extra))


@pytest.mark.parametrize("flag", ["loading=True", "paginate=True page=1 total_pages=1"])
def test_the_script_is_emitted_when_there_are_no_rows(flag: str) -> None:
    for src in (
        '{% data_table rows=rows columns=cols row_url="u" ' + flag + " %}",
        '{% data_table rows=rows columns=cols row_click_event="rc" ' + flag + " %}",
    ):
        py, rs = assert_same(src, ctx(rows=[]))
        assert view(py).scripts == view(rs).scripts == [(SCRIPT_SRC, True)], (py, rs)


def test_falsy_row_url_wires_nothing_and_the_script_is_not_emitted() -> None:
    """Decided on the VALUE: ``None`` / ``""`` / ``0`` / ``False`` are not the key ``"None"``."""
    for falsy in ("None", '""', "0", "False", "ru_none", "ru_zero"):
        for engine in (_django, _rust):
            html = engine(
                "{% data_table rows=rows columns=cols row_url=" + falsy + " %}",
                ctx(ru_none=None, ru_zero=0),
            )
            assert "data-href" not in html and SCRIPT_SRC not in html, (falsy, html)
            assert "data-table-row-clickable" not in html, (falsy, html)


# --- link column forms ------------------------------------------------------------------------------

LINKS = [
    {"key": "name", "label": "N", "link": "u"},
    {"key": "name", "label": "N", "link": "u", "link_class": "lk"},
    {"key": "name", "label": "N", "link": "u", "link_class": ""},
    {"key": "name", "label": "N", "link": "u", "link_class": None},
    {"key": "name", "label": "N", "link": "u", "link_class": 0},
    {"key": "name", "label": "N", "link": "u", "link_class": 7},
    {"key": "name", "label": "N", "link": "u", "link_class": 'a" onmouseover="alert(1)" x="'},
    {"key": "name", "label": "N", "link": "u", "link_class": "<b>x</b> a&b"},
    {"key": "name", "label": "N", "link": "u", "link_class": ["a", "b"]},
    {"key": "name", "label": "N", "link": "nokey"},
    {"key": "name", "label": "N", "link": "name"},
    {"key": "name", "label": "N", "link": "id"},
    {"key": "name", "label": "N", "link": ""},
    {"key": "name", "label": "N", "link": None},
    {"key": "name", "label": "N", "link": 0},
    {"key": "name", "label": "N", "link": False},
    {"key": "name", "label": "N", "link": True},
    {"key": "name", "label": "N", "link": ["u"]},
    {"key": "name", "label": "N", "link": {"u": 1}},
    {"key": "name", "label": "N", "link": "a.b"},  # no dotted lookup: a key named "a.b"
    {"key": "name", "label": "N", "link_class": "lk"},  # a class with no link
    {"key": "name", "label": "N"},
    {"key": "missing", "label": "N", "link": "u"},  # the text key is not in the row
    {"key": "u", "label": "N", "link": "u"},  # the link is its own text
    {"key": "name", "label": "N", "link": "u", "sortable": False},
    {"key": "name", "label": "N", "link": "u", "width": "10px"},
    {"key": "name", "label": "N", "link": "u", "pinned": "left"},
]


@pytest.mark.parametrize("col", LINKS, ids=[repr(c)[:90] for c in LINKS])
def test_link_column_forms_agree_between_the_engines(col: dict) -> None:
    assert_same("{% data_table rows=rows columns=cols %}", ctx(cols=[col, COLS[1]]), csp=False)


@pytest.mark.parametrize("col", LINKS, ids=[repr(c)[:90] for c in LINKS])
def test_link_column_forms_agree_under_a_row_url(col: dict) -> None:
    assert_same(
        '{% data_table rows=rows columns=cols row_url="u" %}',
        ctx(cols=[col, COLS[1]]),
        csp=False,
    )


@pytest.mark.parametrize("key", [["u"], {"u": 1}], ids=["list", "dict"])
def test_a_key_that_cannot_be_a_row_key_reads_nothing_even_if_its_text_is_one(key: object) -> None:
    """Python: an unhashable key is ``""``. A text lookup of ``str(key)`` would find a row key that
    happens to be spelled like the list; a row is data, so it may be."""
    rows = [{"id": 1, "name": "N", "age": 3, str(key): "/spelled-like-the-key"}]
    for src_cols, src in (
        (
            [{"key": "name", "label": "N", "link": key}, COLS[1]],
            "{% data_table rows=rows columns=cols %}",
        ),
        (COLS, "{% data_table rows=rows columns=cols row_url=k %}"),
    ):
        py, rs = assert_same(src, ctx(rows=rows, cols=src_cols, k=key))
        assert "spelled-like-the-key" not in py + rs


def test_a_numeric_key_is_the_text_its_row_key_became() -> None:
    """The native handler reads the row after a JSON round trip: a row key ``5`` is ``"5"``."""
    rows = [{"id": 1, "name": "N", "age": 3, 5: "/five"}]
    cols = [{"key": "name", "label": "N", "link": 5}, COLS[1]]
    py, rs = assert_same(
        "{% data_table rows=rows columns=cols row_url=5 %}", ctx(rows=rows, cols=cols)
    )
    assert view(rs).rows[0]["attrs"]["data-href"] == "/five"
    assert view(rs).rows[0]["anchors"][0]["href"] == "/five"


def test_two_link_columns_and_a_plain_one_in_one_row() -> None:
    cols = [
        {"key": "name", "label": "N", "link": "u", "link_class": "a"},
        {"key": "age", "label": "A", "link": "v"},
        {"key": "id", "label": "I"},
    ]
    rows = [{"id": 1, "name": "N", "age": 3, "u": "/u", "v": "https://e.x/v?a=1&b=2"}]
    py, rs = assert_same("{% data_table rows=rows columns=cols %}", ctx(rows=rows, cols=cols))
    assert [a["href"] for a in view(rs).rows[0]["anchors"]] == ["/u", "https://e.x/v?a=1&b=2"]


# --- the row's value: every shape a row can hold ------------------------------------------------------

VALUES = {
    "text": "/claims/1",
    "none": None,
    "empty": "",
    "zero": 0,
    "false": False,
    "true": True,
    "int": 7,
    "float": 1.5,
    "list": ["/a"],
    "dict": {"a": "/b"},
    "spaces": "  /padded  ",
    "unicode": "/café?q=☃",
    "ampersand": "/x?a=1&b=2&amp;c=3",
    "quote": "/x\"y'z",
    "angle": "/x<b>y</b>",
}


@pytest.mark.parametrize("name", sorted(VALUES))
def test_every_value_shape_agrees_between_the_engines(name: str) -> None:
    rows = [{"id": 1, "name": "N", "age": 3, "u": VALUES[name]}]
    assert_same('{% data_table rows=rows columns=cols row_url="u" %}', ctx(rows=rows, cols=LINKED))


def test_missing_keys_and_many_rows_agree() -> None:
    rows = [
        {"id": 1, "name": "A", "u": "/1"},
        {"id": 2, "name": "B"},
        {"id": 3, "name": "C", "u": None},
        {"id": 4, "name": "D", "u": "javascript:alert(1)"},
        {"id": 5, "name": "E", "u": "/5"},
    ]
    py, rs = assert_same(
        '{% data_table rows=rows columns=cols row_url="u" %}', ctx(rows=rows, cols=LINKED)
    )
    assert [r["attrs"]["data-href"] for r in view(rs).rows] == ["/1", "", "None", "#", "/5"]


def test_grouped_rows_and_selection_keep_the_links() -> None:
    rows = [
        {"id": 1, "name": "A", "g": "x", "u": "/1"},
        {"id": 2, "name": "B", "g": "y", "u": "/2"},
    ]
    src = '{% data_table rows=rows columns=cols row_url="u" selectable=True selected_rows=sel %}'
    py, rs = render_both(src, ctx(rows=rows, cols=LINKED, sel=[1]))
    assert [r["attrs"]["data-href"] for r in view(py).rows] == ["/1", "/2"]
    assert [r["attrs"]["data-href"] for r in view(rs).rows] == ["/1", "/2"]
    assert nav(py) == nav(rs)


# --- adversarial URLs: the scheme allowlist, through both engines -----------------------------------

#: The sweep's corpus (every payload it drives: plain, case-folded, whitespace- and control-
#: character-obfuscated, entity- and percent-encoded, ``vbscript:``, ``data:``, non-navigation
#: schemes) and the forms a URL reaches a browser that the corpus does not spell: protocol-relative,
#: backslash, tabs inside a scheme, a fullwidth look-alike, a double-encoded colon.
EXTRA = (
    ("protocol-relative", "//evil.example/x"),
    ("backslashes", "\\\\evil.example\\x"),
    ("slash-backslash", "/\\evil.example"),
    ("js-backslash-colon", "javascript\\:alert(1)"),
    ("js-slash", "/javascript:alert(1)"),
    ("js-fullwidth", "ｊavascript:alert(1)"),
    ("js-double-encoded", "javascript%253Aalert(1)"),
    ("js-colon-entity-decimal", "javascript&#58;alert(1)"),
    ("js-tab-after-scheme", "javascript\t:alert(1)"),
    ("js-vt", "\x0bjavascript:alert(1)"),
    ("js-nbsp", " javascript:alert(1)"),
    ("js-only", "javascript:"),
    ("data-only", "data:"),
    ("quote-breakout", '/x" onmouseover="alert(1)'),
    ("angle", "/x><script>alert(1)</script>"),
    ("apostrophe", "/x' onmouseover='alert(1)"),
    ("long", "/" + "a" * 5000),
)
CORPUS = tuple(url_sweep.PAYLOADS) + EXTRA
IDS = [label for label, _p in CORPUS]
PLACEMENTS = {
    "row_url": ('{% data_table rows=rows columns=cols row_url="u" %}', COLS),
    "link": ("{% data_table rows=rows columns=cols %}", LINKED),
    "both": ('{% data_table rows=rows columns=cols row_url="u" %}', LINKED),
}


@pytest.mark.parametrize("placement", sorted(PLACEMENTS))
@pytest.mark.parametrize(("label", "payload"), CORPUS, ids=IDS)
def test_a_hostile_url_is_treated_the_same_and_safely_by_both_engines(
    placement: str, label: str, payload: str
) -> None:
    src, cols = PLACEMENTS[placement]
    rows = [{"id": 1, "name": "N", "age": 3, "u": payload}]
    # The attribute is closed by its own quote: ``assert_same`` finds no ``on...`` attribute.
    py, rs = assert_same(src, ctx(rows=rows, cols=cols), csp=False)
    for engine, html in (("python", py), ("rust", rs)):
        assert url_sweep.findings(html) == [], (engine, label, html)
    # Absolute: an attribute that is present round-trips to the URL or is neutralised, never both.
    got = [r["attrs"].get("data-href") for r in view(rs).rows if "data-href" in r["attrs"]]
    got += [a["href"] for r in view(rs).rows for a in r["anchors"]]
    assert got, (label, rs)
    for value in got:
        assert value in ("#", payload.strip()), (label, value)


@pytest.mark.parametrize("scheme", ["javascript", "JAVASCRIPT", "JaVaScRiPt", "vbscript", "data"])
@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_the_dangerous_schemes_become_a_hash_on_every_sink(engine, scheme: str) -> None:
    """Absolute, named: the cases above compare engines; this one cannot pass if both let it by."""
    payload = f"{scheme}:alert(1)"
    rows = [{"id": 1, "name": "N", "age": 3, "u": payload}]
    html = engine(
        '{% data_table rows=rows columns=cols row_url="u" %}', ctx(rows=rows, cols=LINKED)
    )
    page = view(html)
    assert page.rows[0]["attrs"]["data-href"] == "#", html
    assert page.rows[0]["anchors"][0]["href"] == "#", html
    assert url_sweep.findings(html) == [], html


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_the_value_is_escaped_in_the_attribute_and_the_text(engine) -> None:
    rows = [{"id": 1, "name": '<b>"x"</b>', "age": 3, "u": '/x?a=1&b="2"'}]
    html = engine(
        '{% data_table rows=rows columns=cols row_url="u" %}', ctx(rows=rows, cols=LINKED)
    )
    assert "<b>" not in html, html
    page = view(html)
    assert page.rows[0]["attrs"]["data-href"] == '/x?a=1&b="2"', html
    assert page.rows[0]["anchors"][0] == {
        "href": '/x?a=1&b="2"',
        "class": "lk",
        "text": '<b>"x"</b>',
    }, html


# --- callables: the call guard ------------------------------------------------------------------------


class _Guarded:
    """What a template may and may not call."""

    calls: list[str]

    def __init__(self) -> None:
        self.calls = []

    def plain(self) -> str:
        self.calls.append("plain")
        return "u"

    def altering(self) -> str:
        self.calls.append("altering")
        return "u"

    altering.alters_data = True  # type: ignore[attr-defined]

    def hands_off(self) -> str:
        self.calls.append("hands_off")
        return "u"

    hands_off.do_not_call_in_templates = True  # type: ignore[attr-defined]

    def needs_args(self, x: str) -> str:
        self.calls.append("needs_args")
        return "u"


@pytest.mark.parametrize("member", ["plain", "altering", "hands_off", "needs_args"])
@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_callable_row_url_is_called_only_where_a_template_may_call_it(
    engine, member: str
) -> None:
    guard = _Guarded()
    html = engine("{% data_table rows=rows columns=cols row_url=g." + member + " %}", ctx(g=guard))
    called = member in guard.calls
    assert called == (member == "plain"), (member, guard.calls, html)
    expect_link = member == "plain"
    assert ("data-href" in html) == (expect_link or member == "hands_off"), (member, html)


@pytest.mark.parametrize("member", ["plain", "altering", "hands_off", "needs_args"])
def test_a_callable_row_url_agrees_between_the_engines(member: str) -> None:
    py, rs = assert_same(
        "{% data_table rows=rows columns=cols row_url=g." + member + " %}", ctx(g=_Guarded())
    )
    assert nav(py)["scripts"] == nav(rs)["scripts"]


def test_a_callable_inside_a_column_dict_is_called_on_both_engines() -> None:
    """Django calls ``col.link`` (a callable in a column dict) while reading it. The native
    handler got the column serialised (an empty href, #3895); the Rust engine now renders the
    Python tag's own node (one render path per tag), so both engines call it."""
    cols = [{"key": "name", "label": "N", "link": lambda: "u"}, COLS[1]]
    py = view(_django("{% data_table rows=rows columns=cols %}", ctx(cols=cols)))
    rs = view(_rust("{% data_table rows=rows columns=cols %}", ctx(cols=cols)))
    assert [a["href"] for a in py.rows[0]["anchors"]] == ["/claims/1"]
    assert [a["href"] for a in rs.rows[0]["anchors"]] == ["/claims/1"]


# --- the declined parts, pinned so they cannot silently widen ------------------------------------------


def test_a_marked_safe_url_is_neutralised_by_both_engines() -> None:
    """``mark_safe`` on a row value is data, not a URL (#3894): the Python tag checks and escapes
    it like the native handler does (which is handed text), so both write ``#`` for a script
    scheme. This pinned the divergence until the Python tag was fixed."""
    rows = [{"id": 1, "name": "N", "age": 3, "u": mark_safe("javascript:alert(1)")}]
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    py, rs = render_both(src, ctx(rows=rows, cols=LINKED))
    for page in (view(py), view(rs)):
        assert page.rows[0]["attrs"]["data-href"] == "#"
        assert [a["href"] for a in page.rows[0]["anchors"]] == ["#"]
    assert url_sweep.findings(py) == [] and url_sweep.findings(rs) == []


class _SafeSub(SafeString):
    """``isinstance(x, SafeData)`` is true for a subclass too."""


class _HtmlStr(str):
    """Any ``str`` with ``__html__`` (what ``markupsafe.Markup`` is; no third-party package)."""

    def __html__(self) -> str:
        return str(self)


def _lazy_safe(text: str) -> object:
    return lazy(lambda: mark_safe(text), str)()


#: How a value claims to be safe.
CLAIMS = {
    "mark_safe": mark_safe,
    "safestring-subclass": _SafeSub,
    "lazy-mark_safe": _lazy_safe,
    "html-object": _HtmlStr,
}
MARKED_PAYLOADS = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "java\tscript:alert(1)",
    "data:text/html,<b>",
    '/x" onmouseover="alert(1)" y="',
    "/x' onmouseover='alert(1)",
]


def _direct(rows: list, cols: list, args: tuple[str, ...] = ()) -> str:
    """The tag through the Rust engine with the Python objects: since the Rust engine renders
    the Python tag's own node (one render path per tag), a ``SafeString`` reaches it as one."""
    return _rust(
        "{% data_table rows=rows columns=cols " + " ".join(('row_url="u"', *args)) + " %}",
        {"rows": rows, "cols": cols},
    )


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", MARKED_PAYLOADS)
def test_the_tag_checks_a_marked_safe_row_url(claim: str, payload: str) -> None:
    """Neutralisation must not depend on the engine turning values into text (#3894)."""
    rows = [{"id": 1, "name": "N", "age": 3, "u": CLAIMS[claim](payload)}]
    html = _direct(rows, LINKED)
    page = view(html)
    plain = view(_direct([{**rows[0], "u": payload}], LINKED))
    assert page.rows[0]["attrs"]["data-href"] == plain.rows[0]["attrs"]["data-href"], html
    assert page.rows[0]["anchors"][0]["href"] == plain.rows[0]["anchors"][0]["href"], html
    assert page.handlers == [], html
    assert url_sweep.findings(html) == [], html


@pytest.mark.parametrize("claim", sorted(CLAIMS))
@pytest.mark.parametrize("payload", MARKED_PAYLOADS[4:] + ["<b>x</b> a&b"])
def test_a_marked_safe_link_class_stays_inside_the_class_attribute(
    claim: str, payload: str
) -> None:
    cols = [{"key": "name", "label": "N", "link": "u", "link_class": CLAIMS[claim](payload)}]
    rows = [{"id": 1, "name": "N", "age": 3, "u": "/x"}]
    for html in (
        _direct(rows, cols),  # called with the objects
        _rust(
            '{% data_table rows=rows columns=cols row_url="u" %}', {"rows": rows, "cols": cols}
        ),  # through the engine
    ):
        page = view(html)
        assert page.handlers == [], html
        assert page.rows[0]["anchors"][0]["class"] == payload, html
        assert "<b>" not in html, html


def test_a_row_that_is_not_a_dict_is_an_empty_row_on_both_engines() -> None:
    """The Python tag draws an empty row (``data-href=""``); the native handler omitted it
    (#3895). One render path: both engines draw the Python tag's rows."""
    rows = [{"id": 1, "name": "N", "age": 3, "u": "/1"}, "not a dict", None]
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    py, rs = render_both(src, ctx(rows=rows, cols=LINKED))
    assert [r["attrs"]["data-href"] for r in view(py).rows] == ["/1", "", ""]
    assert [r["attrs"]["data-href"] for r in view(rs).rows] == ["/1", "", ""]


def test_the_matrix_drives_both_features_and_every_variant_of_the_value() -> None:
    """The corpus is not accidentally empty or small (a parametrize over ``[]`` passes)."""
    assert len(CORPUS) > 60 and len(url_sweep.DANGEROUS) > 40
    assert {"js", "js-mixed", "js-embedded-tab", "vbs", "data-html"} <= {
        label for label, _p in CORPUS
    }
    assert len(VALUES) >= 15 and len(LINKS) >= 25 and len(PARAMS) >= 25
