"""The framework's own markup passes event arguments as ``dj-value-*`` (#4186).

A strict handler (ADR-036) receives only ``dj-value-*`` arguments: the client
never collects ``data-*`` for it. Under the legacy policy the click-style
binders collect ``data-*`` AND ``dj-value-*`` (``extractTypedParams``,
``08-event-parsing.js``), while the form-field binders (input, change, blur,
focus, keydown, keyup) collect only ``dj-value-*`` under either policy
(``buildFormEventParams``, ``09-event-binding.js``). So a ``dj-value-x`` sends
the same ``x`` under both policies, and a ``data-x`` reaches no strict handler.

What this file proves:

* ``TestNoDataArguments`` DERIVES every ``data-*`` attribute the framework puts
  on an element that carries an event directive, from rendered markup (every
  gallery example under both template engines, plus the event-heavy tags with
  every event set) and from the framework's own source (templates and the
  Python that builds markup), and asserts each one is a listed non-argument.
  A new ``data-*`` argument on a framework event element fails here.
* ``TestConvertedSites`` pins one converted element per component family
  (``tests/js/fixtures/framework_dj_value_4186.json``) to what the framework
  renders now, and binds the arguments the client sends for it to the handler
  under the strict and the legacy policy. ``tests/js/framework_dj_value_4186.test.js``
  proves the client sends exactly those arguments from that element.
* ``test_data_table_events_over_websocket`` sends those arguments through a real
  ``WebsocketCommunicator`` to the data table mixin's handlers under both
  policies (the table and tabs components' own WebSocket tests are in
  ``test_table_sort_handler_2776.py``).

Regenerate the fixture with ``DJUST_REGEN_4186_FIXTURE=1``.
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.template import engines

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler
from djust.validation import validate_handler_params, validated_call_arguments

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "python" / "djust"
FIXTURE = ROOT / "tests" / "js" / "fixtures" / "framework_dj_value_4186.json"
_MODULE = "djust.tests.test_framework_dj_value_4186"

# Every client binder an element can carry (09-event-binding.js, 30-infinite-scroll.js).
EVENT_ATTR = re.compile(
    r"dj-(click|change|input|submit|blur|focus|keydown|keyup|paste|click-away|"
    r"mouseenter|mouseleave|mounted|poll|shortcut|window-[a-z-]+|document-[a-z-]+)(\.[a-z.]+)?"
)
# The binders that collect ``data-*`` under the legacy policy; the rest are form-field binders.
FORM_FIELD_EVENTS = {"change", "input", "submit", "blur", "focus", "keydown", "keyup", "paste"}

# ``data-*`` attributes a framework event element may carry, and why each is not an
# argument. ``files`` scopes a name to the source files allowed to put it on an event
# element (``None``: anywhere). Everything else must be a ``dj-value-*``.
NON_ARGUMENTS: Dict[str, Tuple[str, Optional[Tuple[str, ...]]]] = {
    # Never collected: the client strips them (extractTypedParams, 08-event-parsing.js).
    "data-component-id": ("routing: names the component the event is sent to", None),
    "data-dj-close": ("marks a dialog's close control (51-keyboard-nav.js); stripped", None),
    "data-loading": ("loading-state marker; stripped", None),
    # Read by other code.
    "data-field": (
        "on a form-field binding it names the generated `field` (getFieldName, "
        "09-event-binding.js); never on a click",
        None,
    ),
    "data-debounce": ("rate-limit setting read by 09-event-binding.js", None),
    "data-throttle": ("rate-limit setting read by 09-event-binding.js", None),
    "data-placeholder": ("CSS attr() placeholder of the rich text editor", None),
    "data-open": ("open state the component CSS keys on", None),
    "data-density": (
        "read by data-table.js to restyle at once; the argument is dj-value-value",
        ("components/rust_handlers.py",),
    ),
    "data-theme-mode": (
        "read by theme-toggle.js; the argument is dj-value-value",
        ("components/templatetags/djust_components.py",),
    ),
    "data-theme-modal-close": (
        "the theming modal's close control, which theming components.js closes on the "
        "client when no server event does; its argument is dj-value-value",
        ("theming/templates/djust_theming/components/modal.html",),
    ),
    "data-col-key": (
        "column key read by data-table.js for reorder and visibility; a sortable "
        "header's argument is dj-value-value",
        ("components/rust_handlers.py",),
    ),
    "data-href": (
        "row URL read by data-table-row-click.js; the table renders it only on a row "
        "without dj-click",
        ("components/rust_handlers.py",),
    ),
    "data-date": (
        "calendar day marker; the day is sent as dj-value-value",
        ("components/templatetags/_advanced.py", "components/components/calendar_view.py"),
    ),
    # On a form-field binding, which collects no data-* under either policy.
    "data-parent": (
        "dependent select: a dj-change element, never collected",
        ("components/templatetags/_forms.py", "components/components/dependent_select.py"),
    ),
    "data-source-event": (
        "dependent select: a dj-change element, never collected",
        ("components/templatetags/_forms.py", "components/components/dependent_select.py"),
    ),
    "data-currency": (
        "currency input: a dj-input element, never collected",
        ("components/templatetags/_forms.py", "components/components/currency_input.py"),
    ),
}
# Framework markers the client strips by prefix (extractTypedParams).
STRIPPED_PREFIXES = ("data-djust", "data-liveview", "data-live-")


def _violations(attrs: List[Tuple[str, Optional[str]]], source: Optional[str]) -> List[str]:
    names = [name for name, _ in attrs]
    events = [m.group(1) for name in names if (m := EVENT_ATTR.fullmatch(name))]
    if not events:
        return []
    bad = []
    twins = {
        name[len("dj-value-") :].split(":")[0] for name in names if name.startswith("dj-value-")
    }
    for name in names:
        if not name.startswith("data-") or name.startswith(STRIPPED_PREFIXES):
            continue
        if name[len("data-") :] in twins:
            continue  # kept beside its dj-value-* twin, which carries the argument
        entry = NON_ARGUMENTS.get(name.split(":")[0])
        if entry is None:
            bad.append(name)
        elif name == "data-field" and not set(events) <= FORM_FIELD_EVENTS:
            bad.append(name)  # on a click, data-field is an argument
        elif source is not None and entry[1] is not None and source not in entry[1]:
            bad.append(name)
    return bad


class _Collector(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.found: List[Tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        for name in _violations(attrs, None):
            self.found.append((self.source, tag, name))


# --------------------------------------------------------------------------- #
# Derivation: rendered markup
# --------------------------------------------------------------------------- #

_ROWS = [
    {"id": 1, "name": "Ann", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"},
]
_COLUMNS = [
    {"key": "name", "label": "Name", "sortable": True, "filterable": True},
    {
        "key": "role",
        "label": "Role",
        "sortable": True,
        "filterable": True,
        "filter_type": "select",
        "filter_options": ["admin", "user"],
    },
]
# Every event of the event-heavy tags switched on: the gallery examples set few of them.
_EVENT_HEAVY = [
    (
        "data_table",
        "{% data_table rows=rows columns=columns selectable=True search=True paginate=True "
        'page=2 total_pages=3 row_click_event="open_row" density_toggle=True exportable=True '
        "export_formats=formats bulk_actions=actions editable_rows=True editing_rows=editing "
        'expandable=True group_by="role" collapsible_groups=True column_expressions=expressions '
        "importable=True import_pending=True import_preview_data=rows reorderable=True "
        "column_visibility=True editable_columns=editable copyable=True row_drag=True %}",
    ),
    (
        "data_table row_url",
        '{% data_table rows=link_rows columns=columns row_url="url" %}',
    ),
    ("data_table pager", "{% data_table rows=rows columns=columns total_pages=3 page=2 %}"),
    ("pagination", '{% pagination page=3 total_pages=10 prev_event="prev" next_event="go" %}'),
    ("toast_container", '{% toast_container toasts=toasts dismiss_event="dismiss" %}'),
    (
        "combobox",
        '{% combobox name="color" options=options value="a" field="color" event="validate_field" %}',
    ),
    (
        "rich_select",
        '{% rich_select name="color" options=options value="a" field="color" event="validate_field" %}',
    ),
    ("inline_edit", '{% inline_edit value="Hi" field="title" event="save_title" %}'),
    (
        "inline_edit editing",
        '{% inline_edit value="Hi" field="title" event="save_title" editing=True %}',
    ),
    ("markdown_textarea", '{% markdown_textarea name="body" toggle_event="set_mode" %}'),
    (
        "notification_popover",
        '{% notification_popover notifications=notes mark_read_event="mark_read" open=True %}',
    ),
    (
        "toggle_group",
        '{% toggle_group name="grp" options=options value="a" event="pick" %}',
    ),
    ("file_tree", '{% file_tree nodes=nodes event="open_file" %}'),
    ("theme_toggle", '{% theme_toggle event="set_mode" %}'),
    ("dependent_select", '{% dependent_select name="city" parent="country" options=options %}'),
]
_EVENT_HEAVY_CONTEXT: Dict[str, Any] = {
    "rows": _ROWS,
    "link_rows": [dict(r, url=f"/u/{r['id']}/") for r in _ROWS],
    "columns": _COLUMNS,
    "formats": ["csv"],
    "actions": ["delete"],
    "editing": ["1"],
    "expressions": {"name": "Expression..."},
    "editable": ["name"],
    "toasts": [{"id": "t1", "type": "info", "message": "Saved"}],
    "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
    "notes": [{"id": "n1", "title": "Hello", "body": "World", "read": False}],
    "nodes": [{"name": "a.py", "type": "file"}],
    "cards": [{"title": "Alpha", "category": "news"}],
}


def _render_django(src: str, ctx: Dict[str, Any]) -> str:
    template = engines["django"].from_string("{% load djust_components %}" + src)
    return str(template.render(dict(ctx)))


def _render_rust(src: str, ctx: Dict[str, Any]) -> str:
    from djust import _rust
    from djust.components import rust_handlers

    rust_handlers.register_with_rust_engine()
    return str(_rust.render_template(src, dict(ctx)))


def _rendered_sources() -> Iterator[Tuple[str, str]]:
    from djust.components.gallery.examples import CLASS_EXAMPLES, EXAMPLES

    for name, example in EXAMPLES.items():
        for variant in example["variants"]:
            ctx = dict(variant.get("context", {}))
            yield f"{name} (django)", _render_django(variant["template"], ctx)
            yield f"{name} (rust)", _render_rust(variant["template"], ctx)
    for name, example in CLASS_EXAMPLES.items():
        for variant in example["variants"]:
            yield f"{name} (class)", str(variant["render"]())
    for name, src in _EVENT_HEAVY:
        yield f"{name} (django)", _render_django(src, _EVENT_HEAVY_CONTEXT)
        yield f"{name} (rust)", _render_rust(src, _EVENT_HEAVY_CONTEXT)
    yield from _rendered_component_instances()
    yield from _rendered_theming()


def _rendered_theming() -> Iterator[Tuple[str, str]]:
    """The theming components: every catalogue example through its own tag, and the
    tags with their events switched on (the examples leave most of them off)."""
    from django.template import RequestContext

    from djust.theming.contracts import COMPONENT_CONTRACTS
    from djust.theming.gallery.catalogue import _render_template_examples
    from djust.theming.gallery.context import _EXAMPLE_BUILDERS
    from djust.theming.templatetags import theme_components as tc

    for name in COMPONENT_CONTRACTS:
        builder = _EXAMPLE_BUILDERS.get(name)
        for example in _render_template_examples(name, builder() if builder else []):
            yield f"theme_{name}", example["html"]
    ctx = RequestContext({})
    yield (
        "theme_modal",
        str(tc.theme_modal(ctx, id="m1", title="T", is_open=True, component_id="m")),
    )
    yield "theme_dropdown", str(tc.theme_dropdown(ctx, id="d1", label="L", component_id="d"))
    yield (
        "theme_tabs",
        str(tc.theme_tabs(ctx, id="t1", tabs=[{"label": "A"}, {"label": "B"}], component_id="t")),
    )
    yield (
        "theme_nav_group",
        str(
            tc.theme_nav_group(
                ctx, label="G", items=[{"label": "a", "url": "/a/"}], toggle_event="tog"
            )
        ),
    )


def _rendered_component_instances() -> Iterator[Tuple[str, str]]:
    """Components that build an argument's attribute name at render time."""
    from djust.components.components import Button, Card
    from djust.components.data.pagination import PaginationComponent
    from djust.components.data.table import TableComponent
    from djust.components.layout.tabs import TabsComponent
    from djust.components.ui.dropdown import DropdownComponent

    yield "Button", str(Button(label="Save", action="save", data={"form_id": "1"}).render())
    yield "Card", str(Card(content="x", action="open", data={"card_id": "1"}).render())
    from djust.components.components import DataCardGrid

    yield (
        "DataCardGrid",
        str(DataCardGrid(items=[{"title": "A", "category": "news"}], event="open").render()),
    )
    yield (
        "PaginationComponent",
        str(PaginationComponent(component_id="p1", current_page=2, total_pages=5).render()),
    )
    yield (
        "TableComponent",
        str(
            TableComponent(
                component_id="t1",
                columns=[{"key": "name", "label": "Name", "sortable": True, "filterable": True}],
                rows=[{"id": 1, "name": "Ann"}],
                selectable=True,
            ).render()
        ),
    )
    yield (
        "TabsComponent",
        str(TabsComponent(component_id="tb1", tabs=[{"id": "a", "label": "A"}]).render()),
    )
    yield (
        "DropdownComponent",
        str(
            DropdownComponent(
                items=[{"text": "Delete", "action": "delete", "data": {"item_id": 7}}]
            ).render()
        ),
    )


# --------------------------------------------------------------------------- #
# Derivation: source
# --------------------------------------------------------------------------- #

_TAG = re.compile(r"<[a-zA-Z][\w-]*\b[^<>]*>", re.S)
# An attribute name, with or without a value (``data-x="..."`` or a bare ``data-x``).
# Values are blanked out first (``_VALUES``), so a class like ``data-table-row`` is not
# read as an attribute.
_ATTR_NAME = re.compile(r"(?<![\w-])((?:dj|data)-[a-zA-Z0-9_:.-]*[a-zA-Z0-9])(?=[\s=>/'\"{]|$)")
_VALUES = re.compile(r"""=\s*(?:"[^"]*"|'[^']*')""")


def _framework_sources() -> List[Path]:
    keep = []
    for path in sorted(PACKAGE.rglob("*")):
        rel = path.relative_to(PACKAGE).parts
        if path.suffix not in (".py", ".html") or "tests" in rel or "static" in rel:
            continue
        keep.append(path)
    return keep


def _source_spans(text: str) -> Iterator[Tuple[int, str]]:
    """Each start tag, and each line that writes attributes outside a tag literal
    (``click_attr = f' dj-click=... data-x=...'``)."""
    for m in _TAG.finditer(text):
        yield text.count("\n", 0, m.start()) + 1, m.group(0)
    for number, line in enumerate(text.split("\n"), 1):
        yield number, line


def _source_violations(path: Path, rel: Optional[str] = None) -> List[str]:
    rel = rel or str(path.relative_to(PACKAGE))
    found = []
    for line, span in _source_spans(path.read_text()):
        attrs = [(name, None) for name in _ATTR_NAME.findall(_VALUES.sub("=", span))]
        for name in _violations(attrs, rel):
            found.append(f"{rel}:{line} {name}")
    return sorted(set(found))


class TestNoDataArguments:
    def test_rendered_framework_markup_has_no_data_arguments(self):
        found = []
        sources = 0
        for source, html in _rendered_sources():
            sources += 1
            collector = _Collector(source)
            collector.feed(html)
            found += collector.found
        # The derivation ran over every gallery example and the event-heavy tags.
        assert sources > 300, sources
        assert found == [], found

    def test_framework_source_has_no_data_arguments(self):
        paths = _framework_sources()
        assert len(paths) > 200, len(paths)
        found = [v for path in paths for v in _source_violations(path)]
        assert found == [], found

    def test_no_framework_code_computes_a_data_attribute_name(self):
        """An attribute name built at render time (``"data-" + key``) is invisible to
        the source scan; only these keep one, beside the ``dj-value-*`` twin the
        rendered check allows (a user-supplied ``data=`` dict on a Button or Card)."""
        computed = re.compile(r"""["']data-["']\s*\+|["' ]data-\{""")
        allowed = {
            "components/components/button.py",
            "components/components/card.py",
            # Hint text of the legacy policy's type errors, not markup; that path
            # goes with the legacy parameter policy (#4205).
            "validation.py",
        }
        found = sorted(
            f"{path.relative_to(PACKAGE)}:{n}"
            for path in _framework_sources()
            for n, line in enumerate(path.read_text().split("\n"), 1)
            if computed.search(line)
        )
        assert {f.split(":")[0] for f in found} <= allowed, found

    def test_the_event_heavy_renders_reach_the_converted_elements(self):
        """The derivation is not vacuous: it sees the converted dj-value-* elements."""
        seen = set()
        for _source, html in _rendered_sources():
            seen |= set(re.findall(r'(dj-value-[a-z-]+)(?::[a-z]+)?="', html))
        assert {
            "dj-value-value",
            "dj-value-column",
            "dj-value-field",
            "dj-value-mode",
            "dj-value-id",
            "dj-value-name",
            "dj-value-type",
        } <= seen, sorted(seen)

    @pytest.mark.parametrize(
        "markup",
        [
            '<button dj-click="pick" data-value="1">x</button>',
            '<input dj-input="filter" data-column="name">',
            '<span dj-click="start" data-field="title">x</span>',
            '<tr dj-click="pick" data-row-id="7">x</tr>',
            '<button dj-click="pick" data-qux>x</button>',
        ],
    )
    def test_the_rendered_check_flags_a_data_argument(self, markup):
        collector = _Collector("probe")
        collector.feed(markup)
        assert collector.found, markup

    def test_the_source_check_flags_a_data_argument(self, tmp_path):
        probe = tmp_path / "probe.py"
        text = (
            'attr = f\' dj-click="{e}" data-value="{v}"\'\n'
            'x = \'<th dj-click="s"\n data-column="k">\'\n'
            'y = \'<b dj-click="t" class="data-table-x" data-qux>\'\n'
        )
        probe.write_text(text)
        found = _source_violations(probe, "components/templatetags/probe.py")
        # A valueless name is caught; a class value that merely looks like one is not.
        assert [v.split()[-1] for v in found] == ["data-value", "data-column", "data-qux"], found


# --------------------------------------------------------------------------- #
# Converted sites: the fixture shared with tests/js/framework_dj_value_4186.test.js
# --------------------------------------------------------------------------- #

# id, template, the event the element fires, the arguments the client sends for it.
# ``dj-value-*`` without a type suffix are strings under both policies.
CASES: List[Dict[str, Any]] = [
    {
        "id": "data_table.sort",
        "template": "{% data_table rows=rows columns=columns %}",
        "event": "on_table_sort",
        "params": {"value": "name"},
    },
    {
        "id": "data_table.row_click",
        # ``value`` is the row_click_value_key column; ``row_key`` is the row's key.
        "template": (
            '{% data_table rows=rows columns=columns row_click_event="open_row" '
            'row_click_value_key="name" %}'
        ),
        "event": "open_row",
        "params": {"value": "Ann", "row_key": "1"},
    },
    {
        "id": "data_table.select",
        "template": "{% data_table rows=rows columns=columns selectable=True %}",
        "event": "on_table_select",
        "params": {"value": "__all__"},
    },
    {
        "id": "data_table.page",
        "template": "{% data_table rows=rows columns=columns paginate=True page=2 total_pages=3 %}",
        "event": "on_table_page",
        "params": {"value": "1"},
    },
    {
        "id": "data_table.filter",
        "template": "{% data_table rows=rows columns=columns %}",
        "event": "on_table_filter",
        "form_value": "Ann",
        "params": {"column": "name"},
    },
    {
        "id": "pagination",
        "template": '{% pagination page=3 total_pages=10 next_event="go" %}',
        "event": "go",
        "params": {"value": "1"},
    },
    {
        "id": "toast_container",
        "template": '{% toast_container toasts=toasts dismiss_event="dismiss" %}',
        "event": "dismiss",
        "params": {"value": "t1"},
    },
    {
        "id": "combobox",
        "template": '{% combobox name="color" options=options event="validate_field" %}',
        "event": "validate_field",
        "params": {"value": "a", "field": "color"},
    },
    {
        "id": "inline_edit",
        "template": '{% inline_edit value="Hi" field="title" event="save_title" %}',
        "event": "inline_edit_start",
        "params": {"field": "title"},
    },
    {
        "id": "markdown_textarea",
        "template": '{% markdown_textarea name="body" toggle_event="set_mode" %}',
        "event": "set_mode",
        "params": {"mode": "write"},
    },
    {
        "id": "notification_popover",
        "template": '{% notification_popover notifications=notes mark_read_event="mark_read" open=True %}',
        "event": "mark_read",
        "params": {"id": "n1"},
    },
    {
        "id": "toggle_group",
        "template": '{% toggle_group name="grp" options=options value="a" event="pick" %}',
        "event": "pick",
        "params": {"value": "a", "name": "grp"},
    },
    {
        "id": "file_tree",
        "template": '{% file_tree nodes=nodes event="open_file" %}',
        "event": "open_file",
        "params": {"name": "a.py", "type": "file"},
    },
    {
        "id": "data_card_grid",
        "template": '{% data_card_grid items=cards event="open_card" %}',
        "event": "open_card",
        "params": {"title": "Alpha", "category": "news"},
    },
    {
        "id": "tour",
        "template": '{% tour steps=steps active=True step=1 event="tour" %}',
        "event": "tour",
        "params": {"value": "prev"},
    },
]
_CASE_CONTEXT = dict(
    _EVENT_HEAVY_CONTEXT,
    steps=[{"target": "#a", "title": "A"}, {"target": "#b", "title": "B"}],
)


class _First(HTMLParser):
    """The first element that fires ``event`` and carries an argument."""

    def __init__(self, event: str) -> None:
        super().__init__(convert_charrefs=True)
        self.event = event
        self.element: Optional[Dict[str, Any]] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if self.element is not None:
            return
        fires = [n for n, v in attrs if EVENT_ATTR.fullmatch(n) and v == self.event]
        if fires and any(n.startswith("dj-value-") for n, _ in attrs):
            keep = [
                [n, v or ""] for n, v in attrs if n in fires or n.startswith(("dj-value-", "data-"))
            ]
            self.element = {"tag": tag, "attrs": keep}


def _element(case: Dict[str, Any], engine: str) -> Dict[str, Any]:
    render = _render_rust if engine == "rust" else _render_django
    parser = _First(case["event"])
    parser.feed(render(case["template"], _CASE_CONTEXT))
    assert parser.element is not None, (case["id"], engine)
    return parser.element


def _arguments(element: Dict[str, Any]) -> List[List[str]]:
    return [a for a in element["attrs"] if not a[0].startswith("data-")]


def _current_fixture() -> List[Dict[str, Any]]:
    entries = []
    for case in CASES:
        engines_ = case.get("engines", ["django", "rust"])
        element = _element(case, engines_[0])
        for other in engines_[1:]:
            # Both render paths emit the same event and arguments (#4186 parity); the
            # native table's own markers (data-row-key) are not arguments.
            assert _arguments(_element(case, other)) == _arguments(element), (case["id"], other)
        entry = {k: case[k] for k in ("id", "event", "params")}
        if "form_value" in case:
            entry["form_value"] = case["form_value"]
        entry.update(element)
        entries.append(entry)
    return entries


# Whole tables whose own script (data-table.js) fills a hidden trigger's argument
# before clicking it; the JS test drives the script.
PAGES: List[Dict[str, Any]] = [
    {
        "id": "data_table.visibility",
        "template": "{% data_table rows=rows columns=columns column_visibility=True %}",
        "event": "on_table_visibility",
    },
]


def _fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def _elements() -> Dict[str, Dict[str, Any]]:
    return {e["id"]: e for e in _fixture()["elements"]}


class TestConvertedSites:
    def test_fixture_is_what_the_framework_renders(self):
        current = {
            "elements": _current_fixture(),
            "pages": [
                dict(page, html=_render_rust(page["template"], _CASE_CONTEXT)) for page in PAGES
            ],
        }
        if os.environ.get("DJUST_REGEN_4186_FIXTURE"):
            FIXTURE.write_text(json.dumps(current, indent=2) + "\n")
        assert _fixture() == current

    def test_fixture_elements_carry_no_data_arguments(self):
        for entry in _fixture()["elements"]:
            attrs = [(n, v) for n, v in entry["attrs"]]
            assert _violations(attrs, None) == [], entry
            names = {
                n.split(":")[0][len("dj-value-") :].replace("-", "_")
                for n, _ in attrs
                if n.startswith("dj-value-")
            }
            assert names == set(entry["params"]), entry


@pytest.fixture
def policy(request):
    old = config.get("event_parameter_policy", "strict")
    config.set("event_parameter_policy", request.param)
    try:
        yield request.param
    finally:
        config.set("event_parameter_policy", old)


class _UserHandlers(LiveView):
    """The handlers an application declares for the framework tags' events, the
    strict way: each declares the arguments the tag sends, nothing more."""

    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @event_handler()
    def open_row(self, value: str, row_key: int) -> Any:
        return (value, row_key)

    @event_handler()
    def go(self, value: int) -> Any:
        return value

    @event_handler()
    def dismiss(self, value: str) -> Any:
        return value

    @event_handler()
    def inline_edit_start(self, field: str) -> Any:
        return field

    @event_handler()
    def set_mode(self, mode: str) -> Any:
        return mode

    @event_handler()
    def mark_read(self, id: str) -> Any:  # noqa: A002 - the tag's argument name
        return id

    @event_handler()
    def pick(self, value: str, name: str) -> Any:
        return (value, name)

    @event_handler()
    def open_file(self, name: str, type: str) -> Any:  # noqa: A002
        return (name, type)

    @event_handler()
    def tour(self, value: str) -> Any:
        return value

    @event_handler()
    def open_card(self, title: str, category: str) -> Any:
        return (title, category)


_EXPECTED_USER_RESULTS = {
    "data_table.row_click": ("Ann", 1),
    "pagination": 1,
    "toast_container": "t1",
    "inline_edit": "title",
    "markdown_textarea": "write",
    "notification_popover": "n1",
    "toggle_group": ("a", "grp"),
    "file_tree": ("a.py", "file"),
    "tour": "prev",
    "data_card_grid": ("Alpha", "news"),
}


def _client_params(entry: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(entry["params"])
    if "form_value" in entry:
        # A form-field binding also generates `value` and `field` (09-event-binding.js).
        params["value"] = entry["form_value"]
    return params


def _bind_and_call(handler: Any, params: Dict[str, Any]) -> Any:
    validation = validate_handler_params(handler, params, handler.__name__)
    assert validation["valid"], validation
    args, kwargs = validated_call_arguments(validation)
    return handler(*args, **kwargs)


@pytest.mark.parametrize("policy", ["strict", "legacy"], indirect=True)
class TestHandlersReceiveTheArguments:
    def test_user_handlers(self, policy):
        view = _UserHandlers()
        fixture = _elements()
        for case_id, expected in _EXPECTED_USER_RESULTS.items():
            entry = fixture[case_id]
            handler = getattr(view, entry["event"])
            assert _bind_and_call(handler, _client_params(entry)) == expected, case_id

    def test_data_table_mixin_handlers(self, policy):
        view = _TableView()
        view.mount(None)
        fixture = _elements()

        _bind_and_call(view.on_table_sort, _client_params(fixture["data_table.sort"]))
        assert (view.table_sort_by, view.table_sort_desc) == ("name", False)
        _bind_and_call(view.on_table_page, _client_params(fixture["data_table.page"]))
        assert view.table_page == 1
        _bind_and_call(view.on_table_select, _client_params(fixture["data_table.select"]))
        assert set(map(str, view.table_selected_rows)) == {"1", "2"}
        _bind_and_call(view.on_table_filter, _client_params(fixture["data_table.filter"]))
        assert view.table_filters == {"name": "Ann"}

    def test_expression_filter_takes_the_column(self, policy):
        view = _TableView()
        view.mount(None)
        _bind_and_call(view.on_table_expression, {"value": ">1", "column": "name"})
        assert view.table_active_expressions == {"name": ">1"}
        _bind_and_call(
            view.on_table_expression, {"value": json.dumps({"column": "name", "expression": ""})}
        )
        assert view.table_active_expressions == {}

    def test_validate_field_from_a_combobox_option(self, policy):
        fixture = _elements()
        view = _FormView()
        _bind_and_call(view.validate_field, _client_params(fixture["combobox"]))
        assert view.form_data["color"] == "a"
        assert "color" not in view.field_errors


# --------------------------------------------------------------------------- #
# Real WebSocket: the framework-owned handlers
# --------------------------------------------------------------------------- #

from django import forms  # noqa: E402

from djust.components.mixins.data_table import DataTableMixin  # noqa: E402
from djust.forms import FormMixin  # noqa: E402


class _TableView(DataTableMixin, LiveView):
    exposure_policy = "explicit"
    template = (
        f'<div dj-root dj-view="{_MODULE}._TableView">'
        "<p>{{ table_sort_by }}|{{ table_page }}|{{ table_filters.name }}</p></div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.init_table_state()
        self.table_server_mode = True  # the rows below are the data; no queryset
        self.table_rows = [dict(r) for r in _ROWS]
        self.table_columns = [dict(c) for c in _COLUMNS]
        self.table_total_pages = 3
        self.table_page = 2


class _ColorForm(forms.Form):
    color = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])


class _FormView(FormMixin, LiveView):
    """``FormMixin.validate_field``: what a combobox option fires for a form field."""

    exposure_policy = "explicit"
    form_class = _ColorForm
    template = "<div dj-root></div>"


async def _mount(view_path: str):
    from channels.testing import WebsocketCommunicator

    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=request.user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to({"type": "mount", "view": view_path, "url": request.path})
    mounted = await socket.receive_json_from()
    assert mounted["type"] == "mount", mounted
    return socket, mounted


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("policy", ["strict", "legacy"], indirect=True)
async def test_data_table_events_over_websocket(policy):
    from django.test import override_settings

    fixture = _elements()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        socket, mounted = await _mount(f"{_MODULE}._TableView")
        try:
            contracts = mounted.get("parameter_contracts")
            if policy == "strict":
                handlers = contracts["owners"][0]["handlers"]
                assert handlers["on_table_sort"]["policy"] == "strict"
            else:
                # A legacy mount publishes no strict contract.
                assert contracts is None or "strict" not in json.dumps(contracts), contracts
            steps = [
                ("data_table.sort", "name|2|"),
                ("data_table.page", "name|1|"),
                ("data_table.filter", "name|1|Ann"),
            ]
            for ref, (case_id, shown) in enumerate(steps, 1):
                entry = fixture[case_id]
                await socket.send_json_to(
                    {
                        "type": "event",
                        "event": entry["event"],
                        "params": _client_params(entry),
                        "ref": ref,
                    }
                )
                frame = await socket.receive_json_from(timeout=10)
                assert frame["type"] in ("patch", "update", "html_update"), frame
                assert shown in json.dumps(frame), (case_id, frame)
        finally:
            await socket.disconnect()
