"""The ``dj-*`` event-wiring attributes of twelve component tags agree across the two engines (#3669).

``{% dj_button %}`` and eleven more tags have two implementations: the Python tag (the first,
HTTP render under the Django engine) and a native handler (every render after the socket opens).
The native handler used to drop, rename or invent event attributes the Python tag emits. That is
functional, not cosmetic: a click wired under one engine did nothing under the other.

The Python tag is the reference (maintainer decision on #3669). Each case below renders the
same template through BOTH engines and compares the multiset of ``(element, dj-attribute,
value)`` triples. Only the ``dj-*`` attributes are compared: classes, text, ids and ARIA are
the markup-parity issue (#3663) and are deliberately not asserted here.

What is NOT compared, and why (each measured, none guessed):

* ``tree_view`` / ``kanban_board`` differ ONLY when an operand is a ``SafeString``: the Python
  tag keeps a marked-safe value verbatim in the attribute, the native handler is handed text
  and escapes it. That is the escaping policy of every native handler (the ``escaping`` aspect
  of ``scripts/template-library-parity-baseline.csv``), not event wiring, and the native side
  is the safe one. ``test_a_marked_safe_operand_is_never_emitted_raw_by_the_native_handler``
  pins that direction.
* ``data_table``: the native handler also wires ~13 features the Python template never renders
  (expand, export, reorder, ...). Removing them would delete working features from the live
  render path, which is a product decision, not a parity fix (#3869); see
  ``test_data_table_native_extras_only_add_attributes``. The Python template's own quirks
  (empty cells for dict rows, ``sortable: False`` ignored) are #3870.
* ``nav_item`` nested three levels deep: a block handler is handed already-rendered children
  and cannot know its own depth (#3873, ``test_nav_item_three_levels_is_a_declined_case``).
* A block tag's argument naming an unresolved variable (#3871) and leading/trailing whitespace
  of a resolved argument (#3872) are engine-level and affect every native handler.
"""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import Context, Engine

import djust.components
from djust.tests.test_components_csp_aria import _rust, assert_strict_csp_safe

# ``test_components_csp_aria._django`` has no template directory, and four of these tags
# (``pagination``, ``data_table``, ``toast_container`` ...) are inclusion tags.
_ENGINE = Engine(
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "live_tags": "djust.templatetags.live_tags",
    },
    dirs=[str(Path(djust.components.__file__).parent / "templates")],
)


def _django(src: str, ctx: dict | None = None) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + src).render(Context(ctx or {}))


# A value that needs every escape the attribute context has: a quote, an angle bracket, an
# ampersand, an apostrophe, a closing script tag, and U+2028 in the MIDDLE (a leading or
# trailing one is stripped by the engine's argument parser for every tag alike, ASCII space
# included; that is not event wiring).
EVIL = "x<b>\"&'</script> y"

TREE = [
    {
        "id": "a",
        "label": "A",
        "expanded": True,
        "children": [{"id": "a1", "label": "A1"}, {"id": "a2", "label": "A2", "children": []}],
    },
    {"id": "b", "label": "B"},
]
COLUMNS_KANBAN = [
    {"id": "todo", "title": "Todo", "cards": [{"id": "c1", "title": "T1"}]},
    {"id": "done", "title": "Done", "cards": []},
]
TABLE_COLS = [
    {"key": "name", "label": "Name", "sortable": True},
    {"key": "age", "label": "Age"},
]
TABLE_ROWS = [{"id": 1, "name": "A", "age": 3}, {"id": 2, "name": "B", "age": 4}]
TOASTS = [
    {"id": "1", "message": "hi", "type": "info"},
    {"id": "2", "message": "bad", "type": "error", "dismissible": True},
]
OPTIONS = [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
CTX = {
    "ev": EVIL,
    "tree": TREE,
    "cols": COLUMNS_KANBAN,
    "tcols": TABLE_COLS,
    "rows": TABLE_ROWS,
    "toasts": TOASTS,
    "opts": OPTIONS,
    "sw": ["#fff", "#000"],
    "sel": ["a"],
}


class _DjAttrs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: Counter[tuple[str, str, str]] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.startswith("dj-"):
                self.found[(tag, name, value or "")] += 1


def dj_attrs(markup: str) -> Counter[tuple[str, str, str]]:
    parser = _DjAttrs()
    parser.feed(markup)
    parser.close()
    return parser.found


def _diff(py: Counter, rs: Counter) -> str:
    return (
        f"\n  python only: {sorted((py - rs).elements())}"
        f"\n  rust   only: {sorted((rs - py).elements())}"
    )


def _case(src: str, **extra: object) -> tuple[str, dict]:
    return src, dict(CTX, **extra)


# --- the matrix: (id, template) -----------------------------------------------------------------

BUTTON = [
    '{% dj_button label="L" event="go" %}',
    '{% dj_button label="L" event="go" confirm="Sure?" %}',
    '{% dj_button label="L" event="go" disabled=True %}',
    '{% dj_button label="L" event="go" loading=True %}',
    '{% dj_button label="L" event="go" disabled=True loading=True confirm="x" %}',
    '{% dj_button label="L" confirm="x" %}',
    '{% dj_button label="L" disabled=True confirm="x" %}',
    '{% dj_button label="L" event="go" disabled=False loading=False %}',
    '{% dj_button label="L" event="go" disabled="false" %}',
    '{% dj_button label="L" event="go" disabled="0" %}',
    '{% dj_button label="L" event="go" disabled="yes" %}',
    '{% dj_button label="L" event=ev confirm=ev %}',
    '{% dj_button label="L" event=ev confirm=ev disabled=True loading=True %}',
    '{% dj_button label="L" preset="loading" event="go" %}',
    '{% dj_button label="L" preset="danger-confirm" event="go" confirm="c" %}',
    '{% dj_button preset="x-3669" %}',
    '{% dj_button preset="x-3669" event="mine" %}',
    '{% dj_button preset="x-3669" disabled=True %}',
]
COLOR = [
    '{% color_picker name="c" %}',
    '{% color_picker name="c" event="pick" label="L" %}',
    '{% color_picker name="c" event="pick" swatches=sw %}',
    '{% color_picker name="c" swatches=sw %}',
    '{% color_picker name="c" value="#fff" event="pick" swatches=sw %}',
    '{% color_picker name="c" event=ev swatches=sw %}',
]
COMBOBOX = [
    '{% combobox name="n" options=opts %}',
    '{% combobox name="n" options=opts event="pick" %}',
    '{% combobox name="n" options=opts event="pick" search_event="srch" %}',
    '{% combobox name="n" options=opts event="pick" multiple=True selected=sel %}',
    '{% combobox name="n" options=opts event=ev search_event=ev multiple=True selected=sel %}',
    '{% combobox name="n" options=opts event="validate_field" %}',
]
DATA_TABLE = [
    "{% data_table rows=rows columns=tcols %}",
    '{% data_table rows=rows columns=tcols sort_by="name" sort_event="s" %}',
    '{% data_table rows=rows columns=tcols sort_by="name" sort_desc=True sort_event=ev %}',
    "{% data_table rows=rows columns=tcols selectable=True selected_rows=sel %}",
    '{% data_table rows=rows columns=tcols selectable=True select_event="sl" %}',
    '{% data_table rows=rows columns=tcols search=True search_query="q" search_event="se" %}',
    "{% data_table rows=rows columns=tcols search=True search_event=ev search_debounce=50 %}",
    "{% data_table rows=rows columns=tcols paginate=True page=2 total_pages=5 %}",
    '{% data_table rows=rows columns=tcols paginate=True page=2 total_pages=5 page_event="pg" %}',
    "{% data_table rows=rows columns=tcols page=2 total_pages=5 %}",
    '{% data_table rows=rows columns=tcols page=1 total_pages=3 prev_event="pv" next_event="nx" %}',
    '{% data_table rows=rows columns=tcols page=3 total_pages=3 prev_event="pv" next_event="nx" %}',
    "{% data_table rows=rows columns=tcols page=2 total_pages=4 prev_event=ev next_event=ev %}",
    "{% data_table rows=rows columns=tcols page=1 total_pages=1 %}",
    '{% data_table rows=rows columns=tcols row_click_event="rc" %}',
    '{% data_table rows=rows columns=tcols row_click_event="rc" row_click_value_key="name" %}',
    "{% data_table rows=rows columns=tcols row_click_event=ev %}",
    '{% data_table rows=rows columns=tcols row_url="name" %}',
    '{% data_table rows=rows columns=tcols row_click_event="rc" selectable=True '
    "paginate=True page=2 total_pages=4 %}",
    '{% data_table rows=rows columns=tcols row_click_event="rc" loading=True %}',
]
FILTER_BAR = [
    '{% filter_bar %}{% filter_select name="s" label="S" options=opts %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_select name="s" options=opts value="a" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_select name="s" options=opts value="zzz" %}{% endfilter_bar %}',
    '{% filter_bar event="fc" clear_event="cl" %}'
    '{% filter_select name="s" options=opts value="a" %}{% endfilter_bar %}',
    "{% filter_bar event=ev clear_event=ev %}"
    '{% filter_select name="s" options=opts value="a" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_select name="s" options=opts value="a" event="own" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_date_range name="d" label="D" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_date_range name="d" start="2024-01-01" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_date_range name="d" end="2024-01-01" %}{% endfilter_bar %}',
    '{% filter_bar event="fc" clear_event="cl" %}'
    '{% filter_date_range name="d" start="2024-01-01" end="2024-02-01" %}{% endfilter_bar %}',
    '{% filter_bar event=ev %}{% filter_date_range name="d" event="own" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_search name="q" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_search name="q" value="x" debounce=500 %}{% endfilter_bar %}',
    '{% filter_bar event="fc" clear_event="cl" %}{% filter_search name="q" value="x" %}{% endfilter_bar %}',
    '{% filter_bar event=ev clear_event=ev %}{% filter_search name="q" value="x" event="own" %}{% endfilter_bar %}',
    '{% filter_bar %}{% filter_search name="q" value="x" %}'
    '{% filter_select name="s" options=opts %}{% filter_date_range name="d" %}{% endfilter_bar %}',
    '{% filter_bar event="fc" %}{% filter_search name="q" %}{% filter_select name="s" options=opts %}'
    '{% filter_date_range name="d" end="2024-02-01" %}{% endfilter_bar %}',
    "{% filter_bar %}{% endfilter_bar %}",
]
KANBAN = [
    "{% kanban_board columns=cols %}",
    '{% kanban_board columns=cols move_event="m" add_card_event="ac" add_col_event="aco" %}',
    "{% kanban_board columns=cols move_event=ev add_card_event=ev add_col_event=ev %}",
    "{% kanban_board %}",
]
NAV = [
    '{% nav_menu %}{% nav_item label="Home" href="/" %}{% endnav_item %}{% endnav_menu %}',
    '{% nav_menu %}{% nav_item label="Home" event="go" %}{% endnav_item %}{% endnav_menu %}',
    '{% nav_menu toggle_event="tg" %}{% nav_item label="Home" event="go" %}'
    "{% endnav_item %}{% endnav_menu %}",
    '{% nav_menu %}{% nav_item label="Home" event="go" %}some text{% endnav_item %}{% endnav_menu %}',
    '{% nav_menu %}{% nav_item label="Home" event="go" %}\n  {% endnav_item %}{% endnav_menu %}',
    '{% nav_menu %}{% nav_item label="Menu" event="top" %}'
    '{% nav_item label="Sub" event="s" description="d" %}{% endnav_item %}'
    '{% nav_item label="Sub2" href="/x" %}{% endnav_item %}{% endnav_item %}{% endnav_menu %}',
    '{% nav_menu %}{% nav_item label="Menu" mega=True %}'
    '{% nav_item label="Sub" event=ev %}{% endnav_item %}{% endnav_item %}{% endnav_menu %}',
    '{% nav_menu toggle_event=ev %}{% nav_item label="Home" event=ev %}'
    "{% endnav_item %}{% endnav_menu %}",
    '{% nav_menu %}{% nav_item label="Menu" %}text'
    '{% nav_item label="Sub" event="s" %}{% endnav_item %}{% endnav_item %}{% endnav_menu %}',
]
PAGINATION = [
    "{% pagination page=2 total_pages=5 %}",
    "{% pagination page=1 total_pages=5 %}",
    "{% pagination page=5 total_pages=5 %}",
    "{% pagination page=1 total_pages=1 %}",
    "{% pagination page=3 total_pages=20 %}",
    '{% pagination page=3 total_pages=10 prev_event="pp" next_event="nn" %}',
    "{% pagination page=3 total_pages=10 prev_event=ev next_event=ev %}",
]
TOAST = [
    "{% toast_container toasts=toasts %}",
    '{% toast_container toasts=toasts dismiss_event="bye" %}',
    "{% toast_container toasts=toasts dismiss_event=ev %}",
]
TREE_VIEW = [
    "{% tree_view nodes=tree %}",
    '{% tree_view nodes=tree expand_event="ex" select_event="se" selected="a1" %}',
    "{% tree_view nodes=tree expand_event=ev select_event=ev %}",
    "{% tree_view %}",
]

MATRIX = {
    "dj_button": BUTTON,
    "color_picker": COLOR,
    "combobox": COMBOBOX,
    "data_table": DATA_TABLE,
    "filter_bar": FILTER_BAR,
    "kanban_board": KANBAN,
    "nav_item": NAV,
    "pagination": PAGINATION,
    "toast_container": TOAST,
    "tree_view": TREE_VIEW,
}
CASES = [(tag, src) for tag, sources in MATRIX.items() for src in sources]


@pytest.fixture(autouse=True, scope="module")
def _preset():
    from djust.components.presets import register_preset

    # A preset may carry the event wiring itself; the native handler used to ignore presets.
    register_preset(
        "dj_button",
        "x-3669",
        {"label": "P", "event": "from_preset", "confirm": "from preset?", "disabled": False},
    )
    yield


@pytest.mark.parametrize(("tag", "src"), CASES, ids=[f"{t}::{s[:70]}" for t, s in CASES])
def test_dj_attributes_match_the_python_tag(tag: str, src: str) -> None:
    py_html, rs_html = _django(src, CTX), _rust(src, CTX)
    py, rs = dj_attrs(py_html), dj_attrs(rs_html)
    assert py == rs, f"{tag}: {src}{_diff(py, rs)}\n  python: {py_html}\n  rust:   {rs_html}"


@pytest.mark.parametrize(("tag", "src"), CASES, ids=[f"{t}::{s[:70]}" for t, s in CASES])
def test_both_renderings_hold_to_a_strict_csp(tag: str, src: str) -> None:
    for html in (_django(src, CTX), _rust(src, CTX)):
        assert_strict_csp_safe(html)


def test_the_matrix_covers_every_tag_of_the_issue() -> None:
    assert (set(MATRIX) - {"filter_bar"}) | {
        "filter_select",
        "filter_date_range",
        "filter_search",
    } == {
        "color_picker",
        "combobox",
        "data_table",
        "dj_button",
        "filter_date_range",
        "filter_search",
        "filter_select",
        "kanban_board",
        "nav_item",
        "pagination",
        "toast_container",
        "tree_view",
    }
    covered = " ".join(src for _t, src in CASES)
    for child in ("filter_select", "filter_date_range", "filter_search"):
        assert "{% " + child in covered


# --- payloads: the event name and the confirm text are escaped the same way ----------------------


@pytest.mark.parametrize(
    "src",
    [
        '{% dj_button label="L" event=ev confirm=ev %}',
        "{% pagination page=2 total_pages=4 prev_event=ev next_event=ev %}",
        '{% color_picker name="c" event=ev swatches=sw %}',
        "{% data_table rows=rows columns=tcols row_click_event=ev page=2 total_pages=4 "
        "prev_event=ev next_event=ev %}",
        '{% filter_bar event=ev clear_event=ev %}{% filter_search name="q" value="x" %}'
        "{% endfilter_bar %}",
    ],
)
def test_a_hostile_event_name_is_escaped_in_the_attribute_on_both_engines(src: str) -> None:
    for html in (_django(src, CTX), _rust(src, CTX)):
        # The raw payload never reaches the markup...
        assert "<b>" not in html and EVIL not in html and "&'</script>" not in html, html
        # ...and the attribute value round-trips to exactly the payload.
        values = {v for (_t, n, v) in dj_attrs(html) if n in {"dj-click", "dj-confirm"}}
        assert values & {EVIL}, html
        assert_strict_csp_safe(html)


def test_a_marked_safe_operand_is_never_emitted_raw_by_the_native_handler() -> None:
    from django.utils.safestring import mark_safe

    ctx = dict(CTX, ev=mark_safe("<b>safe</b>"))
    for src in (
        "{% tree_view nodes=tree select_event=ev %}",
        "{% kanban_board columns=cols add_card_event=ev %}",
        '{% dj_button label="L" event=ev %}',
    ):
        assert "<b>safe</b>" not in _rust(src, ctx), src


# --- the two declined parts, pinned so they cannot silently widen -------------------------------

_EXTRAS = [
    "expandable=True",
    "editable_columns=sel",
    "exportable=True",
    "reorderable=True",
    "column_visibility=True",
    "density_toggle=True",
    'group_by="name"',
    "copyable=True",
    "importable=True",
    "row_drag=True",
    "editable_rows=True",
]


@pytest.mark.parametrize("flag", _EXTRAS)
def test_data_table_native_extras_only_add_attributes(flag: str) -> None:
    """The native table wires features the Python template never renders; it never LACKS one."""
    src = "{% data_table rows=rows columns=tcols " + flag + " %}"
    py, rs = dj_attrs(_django(src, CTX)), dj_attrs(_rust(src, CTX))
    assert not (py - rs), f"native table lost a Python attribute: {_diff(py, rs)}"
    assert rs - py, f"{flag} no longer differs; move it into DATA_TABLE and delete this entry"


def test_nav_item_three_levels_is_a_declined_case() -> None:
    src = (
        '{% nav_menu %}{% nav_item label="Top" %}{% nav_item label="Mid" event="mid" %}'
        '{% nav_item label="Deep" event="deep" %}{% endnav_item %}{% endnav_item %}'
        "{% endnav_item %}{% endnav_menu %}"
    )
    py, rs = dj_attrs(_django(src, CTX)), dj_attrs(_rust(src, CTX))
    # Python draws two levels and drops the third (including its dj-click); the native handler
    # is handed rendered children and cannot know its depth, so it still draws "Deep".
    assert ("button", "dj-click", "deep") not in py
    assert ("button", "dj-click", "deep") in rs


def test_data_table_python_template_marks_an_unsortable_column_sortable() -> None:
    """Declined: the reference is wrong here, so the native table is NOT made to match it.

    ``{% if col.sortable|default:True %}`` turns an explicit ``sortable: False`` into True
    (``False|default:True`` is ``True``), so the Python template wires ``dj-click`` onto a
    column the caller marked unsortable. The native table honours ``sortable: False``.
    When the template is fixed, this test fails: move the case into ``DATA_TABLE``.
    """
    cols = [{"key": "name", "label": "Name", "sortable": False}]
    src = "{% data_table rows=rows columns=cols %}"
    ctx = dict(CTX, cols=cols)
    sort = ("th", "dj-click", "on_table_sort")
    assert dj_attrs(_django(src, ctx))[sort] == 1
    assert dj_attrs(_rust(src, ctx))[sort] == 0


def test_a_block_tag_argument_naming_an_unresolved_variable_is_a_declined_case() -> None:
    """Declined (engine level, every block handler): the native block path hands a handler the
    NAME of a variable it could not resolve, where the Django tag resolves it to ``""``.

    ``{% nav_menu toggle_event=nope %}``: Python wires ``dj-click=""``, native ``dj-click="nope"``.
    Inline handlers (``{% dj_button event=nope %}``) resolve it to ``""`` on both engines.
    """
    src = "{% nav_menu toggle_event=nope %}{% endnav_menu %}"
    assert dj_attrs(_django(src, CTX)) == Counter({("button", "dj-click", ""): 1})
    assert dj_attrs(_rust(src, CTX)) == Counter({("button", "dj-click", "nope"): 1})
    inline = '{% dj_button label="L" event=nope %}'
    assert dj_attrs(_django(inline, CTX)) == dj_attrs(_rust(inline, CTX))


def test_a_preset_supplies_the_event_and_the_confirmation_on_both_engines() -> None:
    """Absolute, not a comparison: both engines share ``resolve_dj_button_args``, so a parity
    test alone cannot see the preset stop working (gate-off: mutate the helper and every pair
    still agrees, both empty)."""
    want = Counter(
        {("button", "dj-click", "from_preset"): 1, ("button", "dj-confirm", "from preset?"): 1}
    )
    src = '{% dj_button preset="x-3669" %}'
    assert dj_attrs(_django(src, CTX)) == want
    assert dj_attrs(_rust(src, CTX)) == want
    # An explicit argument still wins over the preset.
    own = '{% dj_button preset="x-3669" event="mine" confirm="mine?" %}'
    want_own = Counter({("button", "dj-click", "mine"): 1, ("button", "dj-confirm", "mine?"): 1})
    assert dj_attrs(_django(own, CTX)) == want_own
    assert dj_attrs(_rust(own, CTX)) == want_own
