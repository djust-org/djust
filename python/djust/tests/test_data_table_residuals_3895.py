"""#3895: the ``data_table`` cases where the two engines drew a different table, now one table.

``{% data_table %}`` has two implementations: the Python tag (``table.html``, every render through
Django's engine) and the native handler (``DataTableHandler``, every render through djust's
engine, so every render after a LiveView event). The native handler is handed ``rows`` and
``columns`` as JSON text, which cannot carry four things the Python tag sees; each drew a
different table, and the first page load (one engine) changed on the first event (the other):

* a developer's ``mark_safe`` cell (item 3): the text is plain, so the markup was escaped;
* a model instance or another object as a row (item 4): the Python tag read nothing from it;
* ``NaN`` / an infinity (item 5): the text is ``null`` (drawn ``None``), and the Python tag linked
  to the relative URL ``nan``;
* a function inside a column dict (item 6), and a column given as an object or a namedtuple
  (item 7): the text is the function's or the object's ``str()``, or a list.

The engine now hands a list or dict operand holding such a member to the handler as well
(``text_loses_a_member``, ``crates/djust_templates/src/renderer.rs``), and both implementations
read rows and columns through the same functions (``table_row`` / ``table_column``,
``djust.components.utils``).

Every case below is compared on the header and the body, whitespace collapsed, with the native
``data-row-key`` and the Python ``tabindex`` (#3663, #3869) left out; and each feature is also
asserted on each engine by itself, since two engines that agree on an empty cell prove nothing.
"""

from __future__ import annotations

import collections
import logging
import re

import pytest
from django.contrib.auth.models import User
from django.utils.functional import lazy
from django.utils.safestring import mark_safe

from djust.components import utils as table_utils
from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust
from djust.tests.test_data_table_row_url_link_3887 import view
from scripts.lib.template_library_parity import url_sweep

SRC = "{% data_table rows=rows columns=cols %}"
LINKED = [{"key": "name", "label": "Name", "link": "u"}, {"key": "age", "label": "Age"}]
COLS = [{"key": "name", "label": "Name"}, {"key": "age", "label": "Age"}]


def _part(html: str, tag: str) -> str:
    """``<thead>`` / ``<tbody>`` with whitespace at tags removed and #3663/#3869 attrs dropped."""
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S)
    assert match, html
    text = re.sub(r"\s+", " ", match.group(1))
    text = re.sub(r"\s*(<|>)\s*", r"\1", text)
    return re.sub(r' (?:data-row-key|tabindex)="[^"]*"', "", text)


def _table(html: str) -> tuple[str, str]:
    return _part(html, "thead"), _part(html, "tbody")


def _both(ctx: dict, src: str = SRC) -> tuple[str, str]:
    return _django(src, ctx), _rust(src, ctx)


# --- the objects of items 4 and 7 -------------------------------------------------------------------


class TableAccount3895(User):
    """A model with a field excluded on top of the floor: ``email`` is never exposed."""

    djust_exclude_fields = ["email"]

    class Meta:
        proxy = True
        app_label = "auth"


def _account() -> TableAccount3895:
    return TableAccount3895(id=7, username="ann", email="ann@example.com", password="pbkdf2$secret")


class Person:
    """A plain object row: an attribute, a property, a method, and what a template never reads."""

    def __init__(self) -> None:
        self.name = "Pat"
        self._secret = "hidden"
        self.calls: list[str] = []

    @property
    def age(self) -> int:
        return 41

    def title(self) -> str:
        return "Dr"

    def wipe(self) -> str:
        self.calls.append("wipe")
        return "wiped"

    wipe.alters_data = True  # type: ignore[attr-defined]


class ColumnObject:
    def __init__(self, key: str, label: str, width: str = "") -> None:
        self.key, self.label, self.width = key, label, width


ColumnTuple = collections.namedtuple("ColumnTuple", "key label width")


def _lazy(text: str) -> object:
    return lazy(lambda: text, str)()


# --- the cases: every one draws the same header and body on both engines ---------------------------

ROW = {"id": 1, "name": "N", "age": 3, "u": "/a"}

CASES = {
    # item 3
    "mark_safe-cell": {"rows": [{**ROW, "name": mark_safe("<i>N</i>")}], "cols": LINKED},
    "mark_safe-cell-plain-column": {"rows": [{**ROW, "age": mark_safe("<b>3</b>")}], "cols": COLS},
    "plain-markup-cell": {"rows": [{**ROW, "name": "<i>N</i>"}], "cols": LINKED},
    "lazy-cell": {"rows": [{**ROW, "name": _lazy("<i>N</i>")}], "cols": COLS},
    "mark_safe-url": {"rows": [{**ROW, "u": mark_safe("javascript:alert(1)")}], "cols": LINKED},
    "mark_safe-label": {
        "rows": [ROW],
        "cols": [{"key": "name", "label": mark_safe("<b>Name</b>")}, COLS[1]],
    },
    "mark_safe-key": {
        "rows": [{**ROW, '"><b>k': "v"}],
        "cols": [{"key": mark_safe('"><b>k'), "label": "K"}],
    },
    # item 4
    "model-row": {
        "rows": [_account()],
        "cols": [
            {"key": "username", "label": "User"},
            {"key": "email", "label": "Email"},
            {"key": "password", "label": "Password"},
        ],
    },
    "object-row": {
        "rows": [Person()],
        "cols": [
            {"key": "name", "label": "Name"},
            {"key": "age", "label": "Age"},
            {"key": "title", "label": "Title"},
            {"key": "wipe", "label": "Wipe"},
            {"key": "_secret", "label": "Secret"},
        ],
    },
    # item 5
    "nan": {"rows": [{**ROW, "name": float("nan"), "u": float("nan")}], "cols": LINKED},
    "inf": {"rows": [{**ROW, "age": float("inf"), "u": float("-inf")}], "cols": LINKED},
    # item 6
    "callable-link": {
        "rows": [ROW],
        "cols": [{"key": "name", "label": "Name", "link": lambda: "u"}, COLS[1]],
    },
    "callable-link-class": {
        "rows": [ROW],
        "cols": [{"key": "name", "label": "Name", "link": "u", "link_class": lambda: "c"}],
    },
    # item 7
    "object-columns": {
        "rows": [ROW],
        "cols": [ColumnObject("name", "Name", "10px"), ColumnObject("age", "Age")],
    },
    "namedtuple-columns": {
        "rows": [ROW],
        "cols": [ColumnTuple("name", "Name", "10px"), ColumnTuple("age", "Age", "")],
    },
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_both_engines_draw_the_same_header_and_body(name: str) -> None:
    py, rs = _both(CASES[name])
    assert _table(py) == _table(rs), f"{name}\n  python: {_table(py)}\n  rust:   {_table(rs)}"


@pytest.mark.parametrize("name", sorted(CASES))
def test_both_engines_agree_under_a_row_url(name: str) -> None:
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    py, rs = _both(CASES[name], src)
    assert _table(py) == _table(rs), f"{name}\n  python: {_table(py)}\n  rust:   {_table(rs)}"
    for html in (py, rs):
        assert view(html).handlers == [] and url_sweep.findings(html) == [], html


# --- item 3: a developer's mark_safe in a cell --------------------------------------------------------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_marked_safe_cell_keeps_its_markup(engine) -> None:
    html = engine(SRC, CASES["mark_safe-cell"])
    assert '<a href="/a"><i>N</i></a>' in html, html
    body = _part(engine(SRC, CASES["mark_safe-cell-plain-column"]), "tbody")
    assert "<td><b>3</b></td>" in body, body


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize("case", ["plain-markup-cell", "lazy-cell"])
def test_a_cell_that_is_not_marked_safe_is_escaped(engine, case: str) -> None:
    html = engine(SRC, CASES[case])
    assert "<i>N</i>" not in html and "&lt;i&gt;N&lt;/i&gt;" in html, html


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_marked_safe_url_is_still_checked(engine) -> None:
    """URL attributes are never safe (#3894): the cell keeps its text, the URL becomes ``#``."""
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    page = view(engine(src, CASES["mark_safe-url"]))
    assert page.rows[0]["attrs"]["data-href"] == "#"
    assert page.rows[0]["anchors"][0]["href"] == "#"


#: A marked-safe value that would leave its attribute if it were honoured there.
BREAKOUT = 'x" onmouseover="alert(1)" y="'


@pytest.mark.parametrize(
    "args",
    [
        "",
        "selectable=True",
        'row_click_event="go"',
        'row_click_event="go" row_click_value_key="name"',
        'group_by="name"',
        "expandable=True expanded_rows=ex",
        "editable_rows=True editing_rows=ex",
        "editable_columns=ec",
        "responsive_cards=True",
    ],
)
def test_a_marked_safe_row_value_never_leaves_an_attribute_natively(args: str) -> None:
    """The cell's TEXT keeps a ``mark_safe`` value; every attribute built from a row value (the
    row key, a click value, a group, an edit input, a card label) is escaped whatever it is
    marked, as ``table.html`` writes them with ``force_escape``."""
    marked = mark_safe(BREAKOUT)
    rows = [{"id": marked, "name": marked, "age": 3}]
    cols = [
        {"key": "name", "label": marked, "cell_template": marked, "cell_class": marked},
        {"key": "age", "label": "A", "type": marked},
    ]
    ctx = {"rows": rows, "cols": cols, "ex": [BREAKOUT], "ec": ["name"]}
    html = _rust("{% data_table rows=rows columns=cols " + args + " %}", ctx)
    assert view(html).handlers == [], html
    assert "onmouseover" in html  # the value is there, as text


def _event_view(rows_factory):
    """A real LiveView whose ``swap`` handler replaces the rows (the event the reviewer sent)."""
    from djust import LiveView
    from djust.decorators import event_handler

    def mount(self, request, **kwargs):
        self.rows = rows_factory()
        self.cols = COLS

    @event_handler
    def swap(self, **kwargs):
        self.rows = [{**self.rows[0], "name": "<i>typed</i>", "age": 4}]

    @event_handler
    def bump(self, **kwargs):
        self.rows = [{**self.rows[0], "age": self.rows[0]["age"] + 1}]

    return type(
        "TableView",
        (LiveView,),
        {
            "exposure_policy": "legacy",
            "template": "<div dj-root>" + SRC + "</div>",
            "mount": mount,
            "swap": swap,
            "bump": bump,
        },
    )


def _live(rows_factory):
    from djust.testing import LiveViewTestClient

    client = LiveViewTestClient(_event_view(rows_factory))
    client.mount()
    return client


def test_a_marked_safe_cell_keeps_its_markup_after_an_event() -> None:
    """The reviewer's report: markup on the first load turned into escaped text on the first
    event. The first load (Django's engine) and every render of the live view agree."""
    rows = lambda: [{"id": 1, "name": mark_safe("<i>N</i>"), "age": 3}]  # noqa: E731
    first = _django(SRC, {"rows": rows(), "cols": COLS})
    client = _live(rows)
    assert _part(client.render(), "tbody") == _part(first, "tbody")
    client.send_event("bump")
    after = client.render()
    assert "<td><i>N</i></td>" in _part(after, "tbody"), after
    assert "<td>4</td>" in _part(after, "tbody"), after


def test_a_plain_string_with_markup_is_escaped_before_and_after_an_event() -> None:
    client = _live(lambda: [{"id": 1, "name": "<i>N</i>", "age": 3}])
    for _ in range(2):
        body = _part(client.render(), "tbody")
        assert "&lt;i&gt;N&lt;/i&gt;" in body and "<i>N</i>" not in body, body
        client.send_event("bump")


def test_a_cell_that_stops_being_marked_safe_is_escaped_after_the_event() -> None:
    """State must not keep a safe flag its value no longer has: ``swap`` writes a plain string
    with markup where a ``mark_safe`` one was."""
    client = _live(lambda: [{"id": 1, "name": mark_safe("<i>N</i>"), "age": 3}])
    assert "<td><i>N</i></td>" in _part(client.render(), "tbody")
    client.send_event("swap")
    body = _part(client.render(), "tbody")
    assert "&lt;i&gt;typed&lt;/i&gt;" in body and "<i>" not in body, body


# --- item 4: model instances and other objects as rows ------------------------------------------------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_model_row_is_read_with_the_exposure_floor(engine) -> None:
    """``username`` is read; ``email`` (``djust_exclude_fields``) and ``password`` (the floor)
    are empty cells on both engines."""
    body = _part(engine(SRC, CASES["model-row"]), "tbody")
    assert body == "<tr><td>ann</td><td></td><td></td></tr>", body


def test_a_model_row_under_explicit_exposure() -> None:
    """The live render path, with ``exposure_policy = "explicit"`` and a declared field."""
    from djust import LiveView, state
    from djust.testing import LiveViewTestClient

    class AccountsView(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>" + SRC + "</div>"
        rows = state(default_factory=lambda: [_account()])
        cols = state(default_factory=lambda: CASES["model-row"]["cols"])

    client = LiveViewTestClient(AccountsView)
    client.mount()
    body = _part(client.render(), "tbody")
    assert body == "<tr><td>ann</td><td></td><td></td></tr>", body
    assert "secret" not in client.render() and "ann@example.com" not in client.render()


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_an_object_row_is_read_through_its_attributes(engine) -> None:
    """An attribute, a property and a method are read; a method that alters data is not called
    and a ``_`` name is not read, as in any template."""
    person = Person()
    body = _part(engine(SRC, {**CASES["object-row"], "rows": [person]}), "tbody")
    assert body == "<tr><td>Pat</td><td>41</td><td>Dr</td><td></td><td></td></tr>", body
    assert person.calls == []


def test_table_row_keeps_a_mapping_and_a_dict_table_as_they_are() -> None:
    rows = [{"id": 1}, {"id": 2}]
    assert table_utils.table_rows(rows, ["id"]) is rows
    assert table_utils.table_row("text", ["id"]) == "text"


# --- item 5: NaN and the infinities ---------------------------------------------------------------------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_nan_is_its_text_and_never_a_link(engine) -> None:
    src = '{% data_table rows=rows columns=cols row_url="u" %}'
    page = view(engine(src, CASES["nan"]))
    assert page.rows[0]["attrs"] == {} and page.rows[0]["anchors"] == []
    assert "<td>nan</td>" in _part(engine(src, CASES["nan"]), "tbody")
    body = _part(engine(src, CASES["inf"]), "tbody")
    assert "<td>inf</td>" in body and "href" not in body, body


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), None, "", "  "])
def test_a_value_that_is_no_url(value: object) -> None:
    assert table_utils.table_value_is_url(value) is False


@pytest.mark.parametrize("value", [0, 1.5, "/a", "nan-page"])
def test_a_value_that_is_a_url(value: object) -> None:
    assert table_utils.table_value_is_url(value) is True


# --- item 6: a function in a column dict ----------------------------------------------------------------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize("attr", ["link", "link_class", "label", "sortable", "width"])
def test_a_function_in_a_column_is_as_if_the_key_were_absent(engine, attr: str) -> None:
    called: list[str] = []

    def fn() -> str:
        called.append(attr)
        return "u"

    col = {"key": "name", "label": "Name", "link": "u", "link_class": "c", "width": "9px"}
    without = {k: v for k, v in col.items() if k != attr}
    got = engine(SRC, {"rows": [ROW], "cols": [{**col, attr: fn}]})
    assert _table(got) == _table(engine(SRC, {"rows": [ROW], "cols": [without]})), got
    assert called == []


def test_a_function_in_a_column_is_warned_about_once(caplog) -> None:
    table_utils._WARNED_CALLABLE_COLUMNS.clear()
    cols = [{"key": "who", "label": "Who", "link": lambda: "u"}]
    with caplog.at_level(logging.WARNING, logger="djust.components.utils"):
        for engine in (_django, _rust, _django):
            engine(SRC, {"rows": [ROW], "cols": cols})
    warnings = [r.getMessage() for r in caplog.records if "function" in r.getMessage()]
    assert len(warnings) == 1, warnings
    assert "'who'" in warnings[0] and "'link'" in warnings[0], warnings


# --- item 7: a column given as an object or a namedtuple ----------------------------------------------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize("case", ["object-columns", "namedtuple-columns"])
def test_an_object_column_is_read_for_its_key_label_and_width(engine, case: str) -> None:
    head, body = _table(engine(SRC, CASES[case]))
    assert re.findall(r"<th\b[^>]*>(.*?)</th>", head) == ["Name", "Age"], head
    assert 'data-value="name"' in head and 'style="width:10px"' in head, head
    assert body == "<tr><td>N</td><td>3</td></tr>", body


def test_table_column_reads_a_namedtuple_and_an_object_and_keeps_a_scalar() -> None:
    assert table_utils.table_column(ColumnTuple("k", "L", "")) == {
        "key": "k",
        "label": "L",
        "width": "",
    }
    assert table_utils.table_column(ColumnObject("k", "L")) == {
        "key": "k",
        "label": "L",
        "width": "",
    }
    assert table_utils.table_column("name") == "name"
    plain = {"key": "k"}
    assert table_utils.table_column(plain) is plain


class _LinkedColumn(ColumnObject):
    """An object column whose ``link`` is a method: Django would call it; neither engine does."""

    def link(self) -> str:
        return "u"


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
def test_a_method_on_an_object_column_is_ignored_on_both_engines(engine) -> None:
    cols = [_LinkedColumn("name", "Name"), ColumnObject("age", "Age")]
    page = view(engine(SRC, {"rows": [ROW], "cols": cols}))
    assert page.rows[0]["anchors"] == [], page.rows
    plain = engine(SRC, {"rows": [ROW], "cols": [ColumnObject("name", "Name"), cols[1]]})
    assert _table(engine(SRC, {"rows": [ROW], "cols": cols})) == _table(plain)


# --- a row whose keys are not all text: Django's lookup, on both engines --------------------------


@pytest.mark.parametrize("engine", [_django, _rust], ids=["python", "rust"])
@pytest.mark.parametrize(("key", "expected"), [("1", "B"), (1, "A")], ids=["text-key", "int-key"])
def test_an_int_and_a_text_key_that_collide_in_json_read_like_django(engine, key, expected) -> None:
    """``{1: "A", "1": "B"}``: JSON has one key ``"1"``, Django two. The native handler is handed
    the row itself and reads the key as it is, as ``table_cell`` does."""
    rows = [{"id": 9, 1: "A", "1": "B", "u": "/u"}]
    cols = [{"key": key, "label": "K", "link": "u"}]
    body = _part(engine(SRC, {"rows": rows, "cols": cols}), "tbody")
    assert body == f'<tr><td><a href="/u">{expected}</a></td></tr>', body


def test_the_collision_case_agrees_between_the_engines() -> None:
    for key in ("1", 1):
        ctx = {"rows": [{"id": 9, 1: "A", "1": "B"}], "cols": [{"key": key, "label": "K"}]}
        py, rs = _both(ctx)
        assert _table(py) == _table(rs), (key, _table(py), _table(rs))
