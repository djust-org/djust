"""How a component tag's ARGUMENTS reach its handler, and the data table's rows (#3870, #3871, #3872).

Found while resolving #3669. The Python tag is the reference (maintainer decision on #3669): the
native handler (every render after the socket opens) must give what the Django tag gives, and
where the reference is wrong (#3870) the reference is fixed.

* #3871 - a native BLOCK handler (``nav_menu``, ``nav_item``, ``card`` ...) was handed the NAME of
  an unresolved variable where the Django tag resolves it to ``""``. The engine resolves a block
  tag's arguments through a channel that keeps the raw token when a name does not resolve; an
  INLINE tag's channel gives ``""``. Both text channels cannot tell a name that did not resolve
  from a value that happens to read ``nope``, so the block wrapper asks the engine for the one
  fact the text lacks (``TYPED_ARGS``: which operands resolved).
* #3872 - ``_parse_args`` stripped the whitespace of a resolved value. The Django tag keeps it.
* #3870 - ``table.html`` read a cell as ``{{ row|dictsort:col.key|first }}``: over a dict row that
  is the first KEY, resolved against the key, i.e. ``""``. Every cell was empty, the clickable
  row's ``data-value`` was empty, and ``sortable: False`` was read as True.

Each case renders the same template through BOTH engines.
"""

from __future__ import annotations

import html as _html
import re
from collections import Counter
from html.parser import HTMLParser

import pytest

from djust.tests.test_component_dj_attr_parity_3669 import _django, dj_attrs
from djust.tests.test_components_csp_aria import _rust


class _Texts(HTMLParser):
    """The text nodes of a rendering, exactly as written (no stripping)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[str] = []

    def handle_data(self, data: str) -> None:
        self.nodes.append(data)


def texts(markup: str) -> list[str]:
    parser = _Texts()
    parser.feed(markup)
    parser.close()
    return parser.nodes


CTX = {
    "go": "go",
    "name": "n",
    "same_name": "name",  # a VALUE that is also the name of another context variable
    "shadow": "nope",  # a VALUE that reads like a variable that does not exist
    "obj": {"k": "dotted", "n": 7},
    "zero": 0,
    "empty": "",
    "none_v": None,
    "false_v": False,
    "true_v": True,
    "ev": "x ",
    "lst": [1, 2],
}

# (id, the argument as the template writes it)
SHAPES = [
    ("unresolved-name", "nope"),
    ("unresolved-dotted", "obj.zz"),
    ("unresolved-deep", "nope.a.b"),
    ("resolved-name", "go"),
    ("resolved-dotted", "obj.k"),
    ("resolved-value-that-reads-unresolved", "shadow"),
    ("quoted-literal-that-looks-like-a-name", '"nope"'),
    ("quoted-literal-naming-a-variable", '"name"'),
    ("single-quoted-literal", "'go'"),
    ("integer", "5"),
    ("float", "1.5"),
    ("number-from-context", "obj.n"),
    ("True", "True"),
    ("False", "False"),
    ("None", "None"),
    ("falsy-zero", "zero"),
    ("falsy-empty", "empty"),
    ("falsy-None", "none_v"),
    ("falsy-False", "false_v"),
    ("truthy-True", "true_v"),
    ("list", "lst"),
]

#: Filters are a feature of the Python tags that are ``simple_tag`` (every INLINE tag below); a raw
#: ``@register.tag`` such as ``nav_item`` or ``card`` parses its own ``key=value`` and takes none.
FILTERED = [
    ("unresolved-with-filter", "nope|upper"),
    ("filter-on-a-resolved-name", "go|upper"),
]

#: ``(tag, template with ARG, what to compare)``. ``attrs`` compares the ``dj-*`` attributes,
#: ``text`` compares the text nodes the argument can reach.
BLOCK_TAGS = [
    ("nav_menu", "{% nav_menu toggle_event=ARG %}{% endnav_menu %}", "attrs"),
    (
        "nav_item",
        '{% nav_menu %}{% nav_item label="Home" event=ARG %}{% endnav_item %}{% endnav_menu %}',
        "attrs",
    ),
    (
        "filter_bar",
        '{% filter_bar event=ARG clear_event=ARG %}{% filter_search name="q" value="x" %}'
        "{% endfilter_bar %}",
        "attrs",
    ),
    ("card", "{% card title=ARG subtitle=ARG %}body{% endcard %}", "text"),
]
INLINE_TAGS = [
    ("dj_button", '{% dj_button label="L" event=ARG confirm=ARG %}', "attrs"),
    ("badge", "{% badge label=ARG %}", "text"),
]


def _seen(markup: str, how: str) -> object:
    if how == "attrs":
        return dj_attrs(markup)
    # Text: the nodes the argument could have produced, whitespace exactly as written.
    return Counter(t for t in texts(markup) if t.strip() and t.strip() not in {"body"})


def _case_id(tag: str, shape: str) -> str:
    return f"{tag}::{shape}"


GRID = [
    (tag, src.replace("ARG", expr), how, shape_id)
    for tag, src, how in BLOCK_TAGS + INLINE_TAGS
    for shape_id, expr in SHAPES
] + [
    (tag, src.replace("ARG", expr), how, shape_id)
    for tag, src, how in INLINE_TAGS
    for shape_id, expr in FILTERED
]


# --- #3871: an argument that names an unresolved variable --------------------------------------


@pytest.mark.parametrize(
    ("tag", "src", "how", "shape"), GRID, ids=[_case_id(g[0], g[3]) for g in GRID]
)
def test_every_argument_shape_reads_the_same_on_both_engines(
    tag: str, src: str, how: str, shape: str
) -> None:
    """The grid: every tag whose arguments are read by ``_parse_args``, every shape an argument
    can take (unquoted, dotted, quoted, number, ``True``/``False``/``None``, falsy, filtered)."""
    py_html, rs_html = _django(src, CTX), _rust(src, CTX)
    py, rs = _seen(py_html, how), _seen(rs_html, how)
    assert py == rs, f"{src}\n  python: {py_html}\n  rust:   {rs_html}"


@pytest.mark.parametrize(
    "src",
    [
        "{% nav_menu toggle_event=nope %}{% endnav_menu %}",
        '{% nav_menu %}{% nav_item label="Home" event=nope %}{% endnav_item %}{% endnav_menu %}',
        "{% nav_menu toggle_event=obj.zz %}{% endnav_menu %}",
    ],
)
def test_an_unresolved_name_never_becomes_an_event_name(src: str) -> None:
    """Absolute, so the grid cannot be satisfied by both engines being wrong: the variable's NAME
    never reaches the page (it used to become ``dj-click="nope"``, an event the view may have)."""
    for html in (_django(src, CTX), _rust(src, CTX)):
        values = {v for (_t, n, v) in dj_attrs(html) if n == "dj-click"}
        assert "nope" not in values and "obj.zz" not in values, html


def test_a_value_that_reads_like_an_unresolved_name_is_kept() -> None:
    """The other half: ``shadow`` RESOLVES, to the text ``nope``. The block handler is handed
    ``toggle_event=nope`` for both this and an unresolved ``nope``; only the engine knows which."""
    src = "{% nav_menu toggle_event=shadow %}{% endnav_menu %}"
    for html in (_django(src, CTX), _rust(src, CTX)):
        assert ("button", "dj-click", "nope") in dj_attrs(html), html


def test_a_value_that_is_also_a_variable_name_is_not_resolved_a_second_time() -> None:
    """#3882, fixed by one render path per tag: ``same_name`` resolves to the text ``name``, which
    is ALSO a context variable (``"n"``). The native handlers' ``context.get(val, val)`` looked the
    VALUE up again and printed ``n``; the Rust engine now renders the Python tag's node, which
    prints ``name`` as Django does."""
    for src in (
        "{% nav_menu toggle_event=same_name %}{% endnav_menu %}",
        '{% dj_button label="L" event=same_name %}',
    ):
        assert ("button", "dj-click", "name") in dj_attrs(_django(src, CTX)), src
        assert dj_attrs(_rust(src, CTX)) == dj_attrs(_django(src, CTX)), src


def test_a_literal_naming_a_variable_stays_a_literal() -> None:
    src = '{% nav_menu toggle_event="name" %}{% endnav_menu %}'
    for html in (_django(src, CTX), _rust(src, CTX)):
        assert ("button", "dj-click", "name") in dj_attrs(html), html


def test_an_unresolved_name_on_an_inline_tag_is_empty_on_both_engines() -> None:
    """Already true before this change; pinned as the reference the block tags now match."""
    src = '{% dj_button label="L" event=nope %}'
    assert dj_attrs(_django(src, CTX)) == dj_attrs(_rust(src, CTX)) == Counter()


def test_an_argument_inside_a_loop_resolves_on_the_block_path() -> None:
    """A loop variable and a ``{% with %}`` binding are context the typed channel must see."""
    src = (
        '{% for e in lst %}{% with w="inner" %}'
        "{% nav_menu toggle_event=w %}{% endnav_menu %}{% endwith %}{% endfor %}"
    )
    py, rs = dj_attrs(_django(src, CTX)), dj_attrs(_rust(src, CTX))
    assert py == rs == Counter({("button", "dj-click", "inner"): 2})


# --- #3872: whitespace of a resolved value ---------------------------------------------------------

PADDED = ["x ", " x", " x ", "x\n", "\tx", "x ", " x", " ", "  ", "\n", " 5 ", " True ", "a  b"]


@pytest.mark.parametrize("value", PADDED, ids=[repr(v) for v in PADDED])
@pytest.mark.parametrize(
    "src",
    [
        '{% dj_button label="L" event=v %}',
        '{% dj_button label="L" confirm=v %}',
        "{% nav_menu toggle_event=v %}{% endnav_menu %}",
        '{% nav_menu %}{% nav_item label="Home" event=v %}{% endnav_item %}{% endnav_menu %}',
        "{% badge label=v %}",
        "{% card title=v %}b{% endcard %}",
        "{% pagination page=2 total_pages=4 prev_event=v next_event=v %}",
    ],
)
def test_a_resolved_value_keeps_its_whitespace_on_both_engines(src: str, value: str) -> None:
    ctx = dict(CTX, v=value)
    py_html, rs_html = _django(src, ctx), _rust(src, ctx)
    assert dj_attrs(py_html) == dj_attrs(rs_html), (
        f"{src}\n  python: {py_html}\n  rust:   {rs_html}"
    )
    # A text node of nothing but whitespace is also what the Python tag's own markup is made of.
    assert (not value.strip()) or (value in texts(py_html)) == (value in texts(rs_html)), (
        f"{src}\n  python: {py_html}\n  rust:   {rs_html}"
    )


def test_a_padded_event_name_reaches_the_attribute_unchanged() -> None:
    """Absolute: ``dj-click="x "`` on both (an event name does not need the space, a label does,
    and the Django tag never decided which: it keeps what it was given)."""
    src = '{% dj_button label="L" event=v %}'
    for value in ("x ", "x ", " x"):
        for html in (_django(src, dict(CTX, v=value)), _rust(src, dict(CTX, v=value))):
            assert ("button", "dj-click", value) in dj_attrs(html), (value, html)


# --- #3870: the data table ---------------------------------------------------------------------------

ROWS = [{"id": 1, "name": "A", "age": 3}, {"id": 2, "name": "B", "age": 4}]
COLS = [{"key": "name", "label": "Name"}, {"key": "age", "label": "Age"}]
TABLE_CTX = {"rows": ROWS, "cols": COLS}


def cells(markup: str) -> list[list[str]]:
    """The ``<td>`` texts of each body row."""
    body = markup[markup.find("<tbody") : markup.find("</tbody>")]
    return [
        [
            _html.unescape(re.sub(r"<[^>]+>", "", td)).strip()
            for td in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)
        ]
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body, re.S)
    ]


def row_values(markup: str) -> list[str]:
    return re.findall(r'<tr[^>]*\bdata-value="([^"]*)"', markup)


def test_a_dict_row_renders_its_cells_on_both_engines() -> None:
    src = "{% data_table rows=rows columns=cols %}"
    assert cells(_django(src, TABLE_CTX)) == [["A", "3"], ["B", "4"]]
    assert cells(_rust(src, TABLE_CTX)) == [["A", "3"], ["B", "4"]]


def test_the_clickable_row_carries_the_rows_value_on_both_engines() -> None:
    src = '{% data_table rows=rows columns=cols row_click_event="rc" %}'
    assert row_values(_django(src, TABLE_CTX)) == ["1", "2"]
    assert row_values(_rust(src, TABLE_CTX)) == ["1", "2"]
    keyed = (
        '{% data_table rows=rows columns=cols row_click_event="rc" row_click_value_key="name" %}'
    )
    assert row_values(_django(keyed, TABLE_CTX)) == ["A", "B"]
    assert row_values(_rust(keyed, TABLE_CTX)) == ["A", "B"]


@pytest.mark.parametrize("sortable", [False, True, 0, 1, "", None, "no"], ids=repr)
def test_sortable_is_honoured_the_same_way_on_both_engines(sortable: object) -> None:
    cols = [{"key": "name", "label": "Name", "sortable": sortable}, {"key": "age", "label": "Age"}]
    src = "{% data_table rows=rows columns=cols %}"
    ctx = dict(TABLE_CTX, cols=cols)
    py = dj_attrs(_django(src, ctx))[("th", "dj-click", "on_table_sort")]
    rs = dj_attrs(_rust(src, ctx))[("th", "dj-click", "on_table_sort")]
    assert py == rs, f"sortable={sortable!r}: python {py} sortable headers, native {rs}"


def test_an_unsortable_column_has_no_sort_click() -> None:
    """Absolute: ``sortable: False`` used to be read as True (``False|default:True``)."""
    cols = [{"key": "name", "label": "Name", "sortable": False}, {"key": "age", "label": "Age"}]
    ctx = dict(TABLE_CTX, cols=cols)
    src = "{% data_table rows=rows columns=cols %}"
    for html in (_django(src, ctx), _rust(src, ctx)):
        # Only the Age column sorts; the Name header is a plain column header.
        assert dj_attrs(html)[("th", "dj-click", "on_table_sort")] == 1, html


#: Rows a table is given besides plain dicts with every key.
TABLE_SHAPES = {
    "missing-key": [{"id": 1, "name": "A"}, {"id": 2, "age": 4}],
    "none-value": [{"id": 1, "name": None, "age": None}],
    "falsy-values": [{"id": 0, "name": "", "age": 0}, {"id": False, "name": 0.0, "age": []}],
    "a-key-called-items": [{"id": 1, "name": "A", "age": 3, "items": 9, "keys": 8, "get": 7}],
    "extra-keys": [{"id": 1, "name": "A", "age": 3, "other": "z"}],
    "numbers": [{"id": 1000, "name": 1.5, "age": 10**6}],
    "no-rows": [],
}


@pytest.mark.parametrize("shape", sorted(TABLE_SHAPES))
def test_dict_rows_of_every_shape_agree_between_the_engines(shape: str) -> None:
    ctx = dict(TABLE_CTX, rows=TABLE_SHAPES[shape])
    src = '{% data_table rows=rows columns=cols row_click_event="rc" %}'
    py_html, rs_html = _django(src, ctx), _rust(src, ctx)
    assert cells(py_html) == cells(rs_html), f"python: {py_html}\n rust: {rs_html}"
    assert row_values(py_html) == row_values(rs_html), f"python: {py_html}\n rust: {rs_html}"


#: Every character that matters in text, in an attribute and in a script context.
PAYLOADS = [
    "<b>x</b>",
    '"><img src=x onerror=alert(1)>',
    "' onmouseover='alert(1)",
    "a&b &amp; &lt;",
    "</script><script>alert(1)</script>",
    "x y",
    "{{ 7*7 }}",
    "{% load x %}",
]


@pytest.mark.parametrize("payload", PAYLOADS, ids=repr)
def test_a_hostile_cell_and_row_value_are_escaped_the_same_on_both_engines(payload: str) -> None:
    rows = [{"id": payload, "name": payload, "age": payload}]
    ctx = dict(TABLE_CTX, rows=rows)
    src = '{% data_table rows=rows columns=cols row_click_event="rc" %}'
    py_html, rs_html = _django(src, ctx), _rust(src, ctx)
    for html in (py_html, rs_html):
        assert "<b>x</b>" not in html and "<img src=x" not in html, html
        assert "<script>alert" not in html, html
        assert "49" not in html, html
    assert cells(py_html) == cells(rs_html) == [[payload.strip()] * 2], (py_html, rs_html)
    assert row_values(py_html) == row_values(rs_html)


# --- the two URL sinks the fix made live: row_url (data-href) and a link column -----------------------


class _Attrs(HTMLParser):
    """Every ``(tag, name, value)`` attribute of a rendering."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.found.extend((tag, n, v or "") for n, v in attrs)


def attrs_of(markup: str) -> list[tuple[str, str, str]]:
    parser = _Attrs()
    parser.feed(markup)
    parser.close()
    return parser.found


def attr_values(markup: str, tag: str, attr: str) -> list[str]:
    return [v for t, n, v in attrs_of(markup) if t == tag and n == attr]


HREFS = [
    ("/claims/1", "/claims/1"),
    ("https://example.com/x?a=1&b=2", "https://example.com/x?a=1&b=2"),
    ("javascript:alert(1)", "#"),
    ("JaVa\tScRiPt:alert(1)", "#"),
    ("data:text/html,<script>1</script>", "#"),
    ("vbscript:x", "#"),
    ('/x"onmouseover="alert(1)', '/x"onmouseover="alert(1)'),
]


@pytest.mark.parametrize(("url", "expected"), HREFS, ids=[h[0] for h in HREFS])
def test_a_row_url_and_a_link_column_write_the_rows_url_and_refuse_a_dangerous_scheme(
    url: str, expected: str
) -> None:
    """``row_url`` and ``col.link`` are ``""`` for every dict row before #3870; reading the row's
    value made them navigation sinks, so the value goes through ``url_attr`` like every other URL
    of a component. The attribute round-trips to the URL (a quote cannot end it early)."""
    rows = [{"id": 1, "name": "N", "u": url}]
    ctx = {"rows": rows, "cols": [{"key": "name", "label": "N", "link": "u"}]}
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    html = _django(src, ctx)
    assert attr_values(html, "tr", "data-href") == [expected], html
    assert attr_values(html, "a", "href") == [expected], html
    # The quote did not open a new attribute.
    assert not [n for _t, n, _v in attrs_of(html) if n.startswith("onmouseover")], html


def test_a_row_or_a_key_that_is_not_there_gives_an_empty_attribute() -> None:
    ctx = {"rows": [{"id": 1, "name": "N"}, "not a dict", None], "cols": COLS}
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    html = _django(src, ctx)
    assert attr_values(html, "tr", "data-href") == ["", "", ""], html


def test_a_number_is_written_unlocalised(settings) -> None:
    """``{{ 1000 }}`` is ``1,000`` with a thousands separator: not a row id (the native table
    writes ``str()``)."""
    settings.USE_THOUSAND_SEPARATOR = True
    ctx = {"rows": [{"id": 1000, "name": "N", "age": 1234567}], "cols": COLS}
    src = '{% data_table rows=rows columns=cols row_click_event="rc" %}'
    html = _django(src, ctx)
    assert row_values(html) == ["1000"], html
    assert cells(html) == [["N", "1234567"]], html


def test_a_marked_safe_cell_is_kept_on_both_engines() -> None:
    """The Python tag honours ``mark_safe`` in a cell (a text slot); the native handler was handed
    text and escaped it (#3870). One render path: both engines keep it."""
    from django.utils.safestring import mark_safe

    ctx = {"rows": [{"id": 1, "name": mark_safe("<i>a</i>"), "age": 1}], "cols": COLS}
    src = "{% data_table rows=rows columns=cols %}"
    assert "<i>a</i>" in _django(src, ctx)
    assert "<i>a</i>" in _rust(src, ctx)


@pytest.mark.parametrize("value", ['"', "'", '""x', "x'"], ids=repr)
def test_a_lone_quote_character_is_a_value_not_an_empty_literal(value: str) -> None:
    """``'"'`` opens AND closes with a quote, so it was read as the empty literal and the event
    vanished natively. A literal needs an opening and a closing quote, which is two characters."""
    src = '{% dj_button label="L" event=v %}'
    ctx = dict(CTX, v=value)
    assert dj_attrs(_django(src, ctx)) == dj_attrs(_rust(src, ctx))
    assert ("button", "dj-click", value) in dj_attrs(_rust(src, ctx))


class _Live:
    """An object operand: an attribute, a method (auto-called) and a property."""

    attr = "from_attr"

    def meth(self) -> str:
        return "from_meth"

    @property
    def prop(self) -> str:
        return "from_prop"


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("o.attr", "from_attr"),
        ("o.meth", "from_meth"),
        ("o.prop", "from_prop"),
        ("o.nope", ""),
        ("lst.1", "2"),
        ("t.1", "t1"),
    ],
)
def test_an_operand_the_engine_resolves_through_an_object_is_not_emptied(
    expr: str, expected: str
) -> None:
    """The block wrapper empties what the typed channel lacks; the channel must have every
    operand the block resolver resolves, whatever walks it (attribute, call, property, index)."""
    ctx = dict(CTX, o=_Live(), t=("t0", "t1"))
    src = "{% nav_menu toggle_event=" + expr + " %}{% endnav_menu %}"
    for html in (_django(src, ctx), _rust(src, ctx)):
        assert ("button", "dj-click", expected) in dj_attrs(html), (expr, html)


# -- the #3884 review: the row's data-value is escaped even for a mark_safe value; the block wrapper ----


def test_a_marked_safe_row_value_cannot_leave_the_data_value_attribute() -> None:
    """``table_cell`` returns a ``mark_safe`` string as it is (a cell may carry markup); the row's
    ``data-value`` is an ATTRIBUTE, so the template force-escapes that read (the review of #3884:
    a row id of ``mark_safe('a" onmouseover="alert(1)" x="')`` rendered a live handler)."""
    from django.utils.safestring import mark_safe

    hostile = mark_safe('a" onmouseover="alert(1)" x="')
    src = '{% data_table rows=rows columns=cols row_click_event="rc" %}'
    ctx = {"rows": [{"id": hostile, "name": "A"}], "cols": [{"key": "name", "label": "Name"}]}
    out = _django(src, ctx)
    assert 'onmouseover="alert' not in out, out
    assert "data-value=" in out and "&quot;" in out, out


def test_a_plain_string_row_value_is_escaped_once() -> None:
    src = '{% data_table rows=rows columns=cols row_click_event="rc" %}'
    ctx = {"rows": [{"id": 'a"<b>&', "name": "A"}], "cols": [{"key": "name", "label": "Name"}]}
    out = _django(src, ctx)
    assert 'data-value="a&quot;&lt;b&gt;&amp;"' in out, out


class _Recorder:
    TYPED_ARGS = False

    def render(self, args, content, context):
        return (list(args), content)


def test_the_block_wrapper_takes_its_arguments_by_position_or_by_name() -> None:
    from djust.components.rust_handlers import _TypedBlockOperands

    wrapped = _TypedBlockOperands(_Recorder())
    by_position = wrapped.render(["k=v"], "body", {})
    by_name = wrapped.render(args=["k=v"], content="body", context={})
    mixed = wrapped.render(["k=v"], content="body", context={})
    assert by_position == by_name == mixed == (["k="], "body")


def test_the_block_wrapper_names_a_missing_or_extra_argument() -> None:
    from djust.components.rust_handlers import _TypedBlockOperands

    wrapped = _TypedBlockOperands(_Recorder())
    with pytest.raises(TypeError, match="missing"):
        wrapped.render(["k=v"], "body")
    with pytest.raises(TypeError, match="takes 3"):
        wrapped.render(["k=v"], "body", {}, {})
