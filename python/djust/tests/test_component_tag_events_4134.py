"""#4134: the last UI-state tag events, ``component_id=`` on every listed tag, and an off state.

Follows #4126 (``test_component_tag_events_4126.py``), whose table pin
(``test_tag_events_table_matches_what_the_tag_draws``) now covers the tags added here.

- The nine UI-state events ``scripts/check-handler-contracts.py`` still listed as
  application actions each have a component in ``djust.components.descriptors``:
  ``Dropdown`` (``toggle_menu``, ``toggle_split_menu``), ``Sidebar``
  (``toggle_sidebar``), ``NotificationCenter`` (``toggle_notifications``),
  ``TreeView`` (``tree_expand``), ``Alert`` (``dismiss_alert``), ``Toasts``
  (``dismiss_toast``) and ``DatePicker`` (``date_prev_month``,
  ``date_next_month``). Each tag is rendered on both engines and every event it
  draws is sent to a view that declares only the component.
- Every tag in ``TAG_EVENTS`` takes ``component_id=``: the controls whose event a
  component answers carry it (on both engines), the controls sending the
  application's events do not, and with two components of one type the event
  reaches the one named.
- A close or dismiss control has an off state: ``None``, ``False`` or ``""``
  leaves it out on both engines and T019 does not report it. A control the tag is
  for (a toggle, a step) has none, and T019 still reports it.
"""

from __future__ import annotations

import datetime
import logging
import re

import pytest

from djust import LiveView
from djust.components import descriptors as D
from djust.components.tag_events import TAG_EVENTS
from djust.testing import LiveViewTestClient
from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust
from djust.tests.test_component_tag_events_4126 import DRAW_ALL, _events, _findings, _view

_ENGINES = {"django": _django, "rust": _rust}

NODES = [
    {"id": "a", "label": "A", "children": [{"id": "a1", "label": "A1"}]},
    {"id": "b", "label": "B", "children": [{"id": "b1", "label": "B1"}]},
]
NOTIFS = [{"id": 1, "message": "Hi", "title": "Hi", "unread": True}]


# --- descriptors answer the tags' default events ----------------------------

#: (descriptor, its state, template drawing every control, context,
#:  [(event, params the markup carries, attribute, expected)])
CASES = [
    (
        D.Dropdown,
        {},
        '{% dropdown_menu label="Menu" %}{% enddropdown_menu %}',
        {},
        [("toggle_menu", {}, "is_open", True), ("toggle_menu", {}, "is_open", False)],
    ),
    (
        D.Dropdown,
        {},
        '{% split_button label="Go" event="go" options=o %}',
        {"o": [{"label": "Other", "event": "other"}]},
        [("toggle_split_menu", {}, "is_open", True), ("toggle_split_menu", {}, "is_open", False)],
    ),
    (
        D.Sidebar,
        {},
        '{% sidebar title="Menu" %}{% endsidebar %}',
        {},
        [("toggle_sidebar", {}, "collapsed", True), ("toggle_sidebar", {}, "collapsed", False)],
    ),
    (
        D.NotificationCenter,
        {},
        "{% notification_center notifications=n unread_count=1 %}",
        {"n": NOTIFS},
        [("toggle_notifications", {}, "is_open", True)],
    ),
    (
        D.NotificationCenter,
        {},
        "{% notification_popover notifications=n %}",
        {"n": NOTIFS},
        [("toggle_notifications", {}, "is_open", True)],
    ),
    (
        D.TreeView,
        {},
        "{% tree_view nodes=n %}",
        {"n": NODES},
        [
            ("tree_expand", {"value": "a"}, "expanded", ["a"]),
            ("tree_expand", {"value": "b"}, "expanded", ["a", "b"]),
            ("tree_expand", {"value": "a"}, "expanded", ["b"]),
        ],
    ),
    (
        D.Alert,
        {},
        "{% alert dismissible=True %}x{% endalert %}",
        {},
        [("dismiss_alert", {}, "dismissed", True)],
    ),
    (
        D.Alert,
        {},
        "{% page_alert dismissible=True %}x{% endpage_alert %}",
        {},
        [("dismiss_alert", {}, "dismissed", True)],
    ),
    (
        D.Toasts,
        {"toasts": [{"id": "t1", "type": "info", "message": "a"}, {"id": "t2", "message": "b"}]},
        "{% toast_container toasts %}",
        {"toasts": [{"id": "t1", "type": "info", "message": "a"}]},
        [("dismiss_toast", {"value": "t1"}, "toasts", [{"id": "t2", "message": "b"}])],
    ),
    (
        D.DatePicker,
        {"year": 2026, "month": 1},
        "{% date_picker year=2026 month=1 %}",
        {},
        [
            ("date_prev_month", {}, "month", 12),
            ("date_next_month", {}, "month", 1),
            ("date_next_month", {}, "month", 2),
        ],
    ),
]

#: Events the tags above draw that stay the application's.
APP_EVENTS = {
    "go",
    "other",
    "mark_notification_read",
    "clear_notifications",
    "mark_read",
    "tree_select",
    "date_select",
}


def _page(descriptor, state, policy="legacy", **more):
    return type(
        "Page_%s" % descriptor.__name__,
        (LiveView,),
        {
            "exposure_policy": policy,
            "template": "<div dj-root>x</div>",
            "widget": descriptor(**state),
            "mount": lambda self, request, **kwargs: None,
            **more,
        },
    )


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize(
    "descriptor, state, src, ctx, steps", CASES, ids=[c[2][3:22] for c in CASES]
)
def test_descriptor_answers_every_event_the_tag_draws(
    policy, engine, descriptor, state, src, ctx, steps
):
    drawn = set(_events(_ENGINES[engine](src, ctx)))
    framework = drawn - APP_EVENTS
    assert framework, drawn
    assert framework <= set(descriptor.Meta.events), (drawn, descriptor.Meta.events)
    assert {s[0] for s in steps} >= framework

    client = LiveViewTestClient(_page(descriptor, state, policy)).mount()
    for event, params, attr, expected in steps:
        assert client.send_event(event, **params)["success"], event
        assert getattr(client.view_instance.widget, attr) == expected, (event, params)


def test_the_check_script_no_longer_lists_the_nine_as_app_actions():
    """Pinned against ``scripts/check-handler-contracts.py`` itself: each of the nine
    is answered by a descriptor and absent from the application list."""
    import importlib.util
    from pathlib import Path

    import djust

    root = Path(djust.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "check_handler_contracts", root / "scripts/check-handler-contracts.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    answered = set()
    for name in dir(D):
        meta = getattr(getattr(D, name), "Meta", None)
        answered |= set(getattr(meta, "events", ()) or ())
    for name in (
        "toggle_menu",
        "toggle_sidebar",
        "toggle_notifications",
        "toggle_split_menu",
        "tree_expand",
        "dismiss_alert",
        "dismiss_toast",
        "date_prev_month",
        "date_next_month",
    ):
        assert name not in mod._APP_LEVEL_EVENTS, name
        assert name in answered, name


# --- argument validation ------------------------------------------------------


def _one(descriptor, **state):
    page = type(
        "One",
        (LiveView,),
        {
            "exposure_policy": "explicit",
            "template": "<div dj-root></div>",
            "w": descriptor(**state),
        },
    )
    return page()


@pytest.mark.parametrize("bad", [True, False, None, "", "x" * 257, 1.5, ["a"], {"a": 1}])
def test_tree_view_refuses_what_cannot_be_a_node_id(caplog, bad):
    view = _one(D.TreeView, expanded=["a"])
    with caplog.at_level(logging.WARNING, logger="djust.components"):
        view.tree_expand(value=bad)
        view.tree_expand(value=bad)
    assert list(view.w.expanded) == ["a"]
    warnings = [r.getMessage() for r in caplog.records if r.name == "djust.components"]
    assert len(warnings) == 1 and "TreeView 'w' refused" in warnings[0], warnings


def test_tree_view_takes_an_integer_id_as_its_text_and_caps_the_count(monkeypatch):
    from djust.components.descriptors import tree_view

    view = _one(D.TreeView)
    view.tree_expand(value=7)
    assert list(view.w.expanded) == ["7"]
    monkeypatch.setattr(tree_view, "MAX_EXPANDED", 2)
    view.tree_expand(value="b")
    view.tree_expand(value="c")
    assert list(view.w.expanded) == ["7", "b"]
    view.tree_expand(value="7")  # collapsing is always allowed
    assert list(view.w.expanded) == ["b"]


def test_tree_view_never_mutates_the_declared_default():
    class Two(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        w = D.TreeView(expanded=["a"])

    first, second = Two(), Two()
    first.tree_expand(value="b")
    assert list(first.w.expanded) == ["a", "b"]
    assert list(second.w.expanded) == ["a"]


@pytest.mark.parametrize("engine", ["django", "rust"])
def test_tree_view_expanded_argument_decides_which_nodes_are_open(engine):
    nodes = [{"id": "a", "label": "A", "expanded": True, "children": [{"id": "a1", "label": "A1"}]}]
    render = _ENGINES[engine]
    # Left out: the node's own key decides, as before #4134.
    assert "A1" in render("{% tree_view nodes=n %}", {"n": nodes})
    # Given: the list decides.
    assert "A1" not in render("{% tree_view nodes=n expanded=e %}", {"n": nodes, "e": []})
    plain = [{**nodes[0], "expanded": False}]
    assert "A1" in render("{% tree_view nodes=n expanded=e %}", {"n": plain, "e": ["a"]})


@pytest.mark.parametrize("bad", [True, None, ["t1"], 1.5, "nope"])
def test_toasts_ignore_what_names_no_toast(bad):
    view = _one(D.Toasts, toasts=[{"id": "t1", "message": "a"}])
    view.dismiss_toast(value=bad)
    assert [t["id"] for t in view.w.toasts] == ["t1"]


def test_toasts_show_adds_caps_and_cleans_the_type(monkeypatch):
    from djust.components.descriptors import toasts

    view = _one(D.Toasts)
    first = view.w.show("Saved", type="success")
    second = view.w.show("<b>x</b>", type="<script>")
    assert [(t["id"], t["type"]) for t in view.w.toasts] == [(first, "success"), (second, "info")]
    monkeypatch.setattr(toasts, "MAX_TOASTS", 2)
    third = view.w.show("again")
    assert [t["id"] for t in view.w.toasts] == [second, third]
    view.dismiss_toast(value=second)
    assert [t["id"] for t in view.w.toasts] == [third]
    html = _django("{% toast_container t %}", {"t": view.w.toasts})
    assert "<b>" not in html


def test_toasts_show_is_not_an_event_and_not_called_by_a_template():
    from djust.decorators import is_event_handler

    assert not is_event_handler(D.Toasts.show)
    assert D.Toasts.show.alters_data is True


def test_date_picker_steps_across_years_and_stops_at_the_calendar_ends():
    view = _one(D.DatePicker, year=2026, month=12)
    view.date_next_month()
    assert (view.w.year, view.w.month) == (2027, 1)
    view.date_prev_month()
    view.date_prev_month()
    assert (view.w.year, view.w.month) == (2026, 11)

    view = _one(D.DatePicker, year=datetime.MINYEAR, month=1)
    view.date_prev_month()
    assert (view.w.year, view.w.month) == (datetime.MINYEAR, 1)
    view = _one(D.DatePicker, year=datetime.MAXYEAR, month=12)
    view.date_next_month()
    assert (view.w.year, view.w.month) == (datetime.MAXYEAR, 12)


_TODAY = datetime.date.today()


@pytest.mark.parametrize(
    "year, month, shown",
    [
        (0, 0, (_TODAY.year, _TODAY.month)),
        (2030, 0, (2030, _TODAY.month)),  # each field on its own (#4134 review)
        (0, 3, (_TODAY.year, 3)),
        (True, 3, (_TODAY.year, 3)),
        (2026, 13, (2026, _TODAY.month)),
        (2026, -1, (2026, _TODAY.month)),
        (10**9, 1, (_TODAY.year, 1)),
        ("2026", "1", (2026, 1)),
        ("x", None, (_TODAY.year, _TODAY.month)),
    ],
)
def test_date_picker_defaults_each_field_on_its_own_as_the_tag_does(year, month, shown):
    from djust.components.utils import calendar_month

    assert calendar_month(year, month) == shown
    view = _one(D.DatePicker, year=year, month=month)
    view.date_next_month()
    index = shown[0] * 12 + shown[1]  # the month after ``shown``, zero-based
    assert (view.w.year, view.w.month) == divmod(index, 12)[0:1] + (index % 12 + 1,)


@pytest.mark.parametrize("engine", ["django", "rust"])
def test_the_tag_shows_the_month_the_component_steps_from(engine):
    import calendar

    html = _ENGINES[engine]("{% date_picker year=2030 %}")
    assert "%s 2030" % calendar.month_name[_TODAY.month] in html
    view = _one(D.DatePicker, year=2030)
    view.date_next_month()
    expected = (2030 + (_TODAY.month == 12), _TODAY.month % 12 + 1)
    assert (view.w.year, view.w.month) == expected


# --- component_id= on every listed tag -----------------------------------------


def _with_cid(tag: str, cid: str) -> tuple[str, dict]:
    src, ctx = DRAW_ALL[tag]
    opening = "{% " + tag
    assert src.startswith(opening), src
    return opening + ' component_id="%s"' % cid + src[len(opening) :], ctx


_CONTROL = re.compile(r'<[^>]*\bdj-(?:click|input)="([^"]*)"[^>]*>')
_CID = re.compile(r'\bdata-component-id="([^"]*)"')


def _controls(html: str) -> list:
    """(event, data-component-id or None) per control the markup draws."""
    out = []
    for match in _CONTROL.finditer(html):
        cid = _CID.search(match.group(0))
        out.append((match.group(1), cid.group(1) if cid else None))
    return out


_COMPONENT_TAGS = sorted(t for t, events in TAG_EVENTS.items() if any(e.component for e in events))


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("tag", _COMPONENT_TAGS)
def test_each_tag_takes_component_id_on_the_controls_its_component_answers(engine, tag):
    answered = {e.default for e in TAG_EVENTS[tag] if e.component}
    for cid in ("first", "second"):
        src, ctx = _with_cid(tag, cid)
        controls = _controls(_ENGINES[engine](src, ctx))
        assert {event for event, _ in controls} >= answered, (tag, controls)
        for event, found in controls:
            if event in answered:
                assert found == cid, (tag, event, found)
            else:
                # An application's event never carries the component's id: the
                # component has no handler of that name.
                assert found is None, (tag, event, found)
    src, ctx = DRAW_ALL[tag]
    assert all(found is None for _, found in _controls(_ENGINES[engine](src, ctx)))


def test_component_id_is_escaped_as_an_attribute():
    html = _django('{% sidebar title="M" component_id=c %}{% endsidebar %}', {"c": '"><x y="'})
    assert '"><x' not in html
    assert 'data-component-id="&quot;&gt;&lt;x y=&quot;"' in html


#: tag -> (descriptor, event, attribute, state before, after) for two of a type.
TWO = {
    "dropdown_menu": (D.Dropdown, "toggle_menu", "is_open", False, True),
    "split_button": (D.Dropdown, "toggle_split_menu", "is_open", False, True),
    "sidebar": (D.Sidebar, "toggle_sidebar", "collapsed", False, True),
    "notification_center": (D.NotificationCenter, "toggle_notifications", "is_open", False, True),
    "tree_view": (D.TreeView, "tree_expand", "expanded", [], ["a"]),
    "alert": (D.Alert, "dismiss_alert", "dismissed", False, True),
    "toast_container": (D.Toasts, "dismiss_toast", "toasts", [{"id": "a"}], []),
    "date_picker": (D.DatePicker, "date_next_month", "month", 1, 2),
    "lightbox": (D.Lightbox, "close_lightbox", "is_open", True, False),
    "command_palette": (D.CommandPalette, "close_palette", "is_open", True, False),
    "export_dialog": (D.ExportDialog, "close_export", "is_open", True, False),
    "announcement_bar": (D.AnnouncementBar, "dismiss_announcement", "dismissed", False, True),
    "nav_menu": (D.NavMenu, "toggle_nav", "mobile_open", False, True),
    "expandable_text": (D.ExpandableText, "toggle_expand", "expanded", False, True),
    "truncated_list": (D.TruncatedList, "toggle_list", "expanded", False, True),
    "markdown_textarea": (D.MarkdownTextarea, "toggle_preview", "preview", False, True),
    "stepper": (D.Stepper, "set_step", "active", 0, 1),
    "bottom_sheet": (D.Sheet, "close_sheet", "is_open", True, False),
}

_PARAMS = {"tree_view": {"value": "a"}, "toast_container": {"value": "a"}, "stepper": {"value": 1}}
_PARAMS["markdown_textarea"] = {"mode": "preview"}


def _state(descriptor, attr, value):
    state = {attr: value}
    if descriptor is D.DatePicker:
        state["year"] = 2026
    return state


@pytest.mark.parametrize("tag", sorted(TWO))
def test_two_of_a_type_each_answer_only_their_own_tag(tag):
    descriptor, event, attr, before, after = TWO[tag]
    page = type(
        "Two_%s" % tag,
        (LiveView,),
        {
            "exposure_policy": "legacy",
            "template": "<div dj-root>x</div>",
            "first": descriptor(**_state(descriptor, attr, before)),
            "second": descriptor(**_state(descriptor, attr, before)),
            "mount": lambda self, request, **kwargs: None,
        },
    )
    client = LiveViewTestClient(page).mount()
    params = dict(_PARAMS.get(tag, {}), component_id="second")
    assert client.send_event(event, **params)["success"], tag
    view = client.view_instance
    assert getattr(view.second, attr) == after, tag
    assert getattr(view.first, attr) == before, tag


class _RuntimeTwoSidebars(LiveView):
    exposure_policy = "legacy"
    template = '<div dj-root dj-id="0"><p>{{ left.collapsed }}{{ right.collapsed }}</p></div>'
    left = D.Sidebar()
    right = D.Sidebar()

    def mount(self, request, **kwargs):
        pass


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_the_runtime_routes_component_id_to_the_named_component():
    """Through ``ViewRuntime.dispatch_event``, the path the socket and SSE take: the
    event reaches the bound component's own ``@event_handler`` (ADR-031 D4)."""
    from djust.runtime import ViewRuntime
    from djust.tests.test_bound_component_skip_gate_2900 import MockTransport

    view = _RuntimeTwoSidebars()
    view.mount(None)
    view.render_with_diff()
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    await runtime.dispatch_event(
        {
            "type": "event",
            "event": "toggle_sidebar",
            "params": {"component_id": "right"},
            "ref": 1,
        }
    )
    assert "error" not in [f.get("type") for f in transport.sent], transport.sent
    assert (view.left.collapsed, view.right.collapsed) == (False, True)


# --- the off state -------------------------------------------------------------

_OPTIONAL = sorted(
    (tag, e.argument, e.default) for tag, events in TAG_EVENTS.items() for e in events if e.optional
)


def test_the_optional_controls_are_the_close_and_dismiss_buttons():
    assert {(t, a) for t, a, _ in _OPTIONAL} == {
        ("modal", "close_event"),
        ("sheet", "close_event"),
        ("bottom_sheet", "close_event"),
        ("lightbox", "close_event"),
        ("command_palette", "close_event"),
        ("export_dialog", "close_event"),
        ("announcement_bar", "dismiss_event"),
        ("alert", "event"),
        ("page_alert", "dismiss_event"),
        ("toast_container", "dismiss_event"),
    }


def _with_arg(tag: str, argument: str, literal: str) -> tuple[str, dict]:
    src, ctx = DRAW_ALL[tag]
    opening = "{% " + tag
    return opening + " %s=%s" % (argument, literal) + src[len(opening) :], ctx


@pytest.mark.parametrize("literal", ["None", "False", '""'])
@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("tag, argument, default", _OPTIONAL)
def test_an_off_event_leaves_its_control_out(engine, tag, argument, default, literal):
    render = _ENGINES[engine]
    on = _events(render(*DRAW_ALL[tag]))
    assert default in on, on
    off = _events(render(*_with_arg(tag, argument, literal)))
    assert default not in off and "None" not in off and "" not in off, off
    # The tag's other controls are still drawn.
    assert set(off) == set(on) - {default}, (on, off)


def test_an_off_modal_drops_the_native_dialog_close_event_too():
    html = _django("{% modal open=True native=True close_event=None %}x{% endmodal %}")
    assert "dj-dialog-close-event" not in html and "dj-click" not in html
    # The client then refuses Escape and the backdrop click (35-dj-dialog.js).
    assert "dj-dialog-no-close" in html
    assert "dj-dialog-no-close" not in _django("{% modal open=True native=True %}x{% endmodal %}")
    assert "dj-dialog-close-event" in _django("{% modal open=True native=True %}x{% endmodal %}")


def test_an_off_lightbox_keeps_navigation_and_drops_the_hooks_close_event():
    ctx = {"i": ["/a.png", "/b.png"]}
    html = _django("{% lightbox images=i open=True close_event=None %}", ctx)
    assert "data-close-event" not in html
    assert set(_events(html)) == {"lightbox_navigate"}


def test_an_unchanged_template_renders_the_same_markup():
    """No behaviour change for a template that works today: the default (no event
    argument, no component_id) draws exactly what it drew before #4134."""
    html = _django("{% modal open=True %}x{% endmodal %}")
    assert '<div class="dj-scrim" dj-click="close_modal"></div>' in html
    html = _django("{% lightbox images=i open=True %}", {"i": ["/a.png"]})
    assert 'dj-hook="ImageLightbox" data-close-event="close_lightbox" data-navigate-event' in html


@pytest.mark.parametrize("literal", ["None", "False", '""'])
@pytest.mark.parametrize("tag, argument, default", _OPTIONAL)
def test_t019_quiet_for_an_optional_control_turned_off(tag, argument, default, literal):
    src, _ctx = _with_arg(tag, argument, literal)
    findings, _ = _findings(_view(src))
    assert default not in {f[0] for f in findings}, findings
    assert "T019" not in {f[1] for f in findings if "off state" in f[2]}, findings


@pytest.mark.parametrize("literal", ["None", "False", '""'])
@pytest.mark.parametrize(
    "src",
    [
        "{%% nav_menu toggle_event=%s %%}{%% endnav_menu %%}",
        "{%% sidebar toggle_event=%s %%}{%% endsidebar %%}",
        "{%% tree_view nodes=n expand_event=%s %%}",
    ],
)
def test_t019_still_reports_an_off_value_where_the_control_is_the_tags_purpose(src, literal):
    findings, _ = _findings(_view(src % literal))
    off = [f for f in findings if "has no off state" in f[2]]
    assert len(off) == 1, findings


# --- T019 for the tags added here ----------------------------------------------


@pytest.mark.parametrize(
    "src, component",
    [
        ("{% dropdown_menu %}{% enddropdown_menu %}", "Dropdown()"),
        ('{% split_button label="x" %}', "Dropdown()"),
        ("{% sidebar %}{% endsidebar %}", "Sidebar()"),
        ("{% notification_center %}", "NotificationCenter()"),
        ("{% notification_popover %}", "NotificationCenter()"),
        ("{% tree_view nodes=n %}", "TreeView()"),
        ("{% alert dismissible=True %}x{% endalert %}", "Alert()"),
        ("{% page_alert dismissible=True %}x{% endpage_alert %}", "Alert()"),
        ("{% toast_container t %}", "Toasts()"),
        ("{% date_picker %}", "DatePicker()"),
    ],
)
def test_t019_reports_each_new_tag_without_its_component(src, component):
    tag = src.split()[1]
    findings, _ = _findings(_view(src))
    assert {f[0] for f in findings} == {e.default for e in TAG_EVENTS[tag]}, findings
    for event in TAG_EVENTS[tag]:
        hint = next(f[3] for f in findings if f[0] == event.default)
        if event.component:
            assert component in hint, hint
            # The full import: ``djust.components.Alert`` is another class (#4134 review).
            assert "from djust.components.descriptors import %s" % event.component in hint, hint
        else:
            assert "the application's to answer" in hint, hint


def test_t019_quiet_when_the_new_component_is_declared():
    findings, _ = _findings(
        _view(
            "{% sidebar collapsed=side.collapsed %}{% endsidebar %}"
            "{% date_picker year=cal.year month=cal.month %}",
            side=D.Sidebar(),
            cal=D.DatePicker(),
        )
    )
    assert [f[0] for f in findings] == ["date_select"], findings


def test_t019_skips_a_dismiss_button_that_is_not_drawn():
    findings, _ = _findings(_view("{% alert %}x{% endalert %}{% page_alert %}x{% endpage_alert %}"))
    assert findings == []


@pytest.mark.parametrize("engine", ["django", "rust"])
def test_two_accordions_each_mark_their_own_triggers(engine):
    """The native ``{% accordion %}`` dropped ``component_id=`` (the Django tag kept
    it): its items render before it. Each accordion now marks its own triggers."""
    item = '{%% accordion_item id="%s" title="T" %%}x{%% endaccordion_item %%}'
    src = (
        '{% accordion component_id="left" %}' + item % "a" + item % "b" + "{% endaccordion %}"
        '{% accordion component_id="right" %}' + item % "c" + "{% endaccordion %}"
        "{% accordion %}" + item % "d" + "{% endaccordion %}"
    )
    html = _ENGINES[engine](src)
    assert _controls(html) == [
        ("accordion_toggle", "left"),
        ("accordion_toggle", "left"),
        ("accordion_toggle", "right"),
        ("accordion_toggle", None),
    ]
    assert "djust-accordion-cid" not in html


def test_a_nested_native_accordion_keeps_its_own_component_id():
    """Natively an inner accordion renders (and consumes its items' markers) before
    the outer one, so its triggers keep the inner id."""
    src = (
        '{% accordion component_id="outer" %}'
        '{% accordion_item id="o" title="O" %}'
        '{% accordion component_id="inner" %}'
        '{% accordion_item id="i" title="I" %}x{% endaccordion_item %}'
        "{% endaccordion %}"
        "{% endaccordion_item %}"
        "{% endaccordion %}"
    )
    html = _rust(src)
    assert _controls(html) == [("accordion_toggle", "outer"), ("accordion_toggle", "inner")]
    assert "djust-accordion-cid" not in html


#: ``{% accordion_item id="q" title="Q" %}x{% endaccordion_item %}`` on the Rust engine, as
#: ``main`` renders it (cf89695e5): nothing of #4134 may reach a standalone item.
_STANDALONE_ITEM = (
    '<div class="accordion-item "><button class="accordion-trigger" aria-expanded="false" '
    'dj-click="accordion_toggle" dj-value-value="q"><span class="accordion-title">Q</span>'
    '<svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline>'
    '</svg></button><div class="accordion-panel" hidden>x</div></div>'
)


def test_a_standalone_native_accordion_item_is_byte_identical_to_main():
    html = _rust('{% accordion_item id="q" title="Q" %}x{% endaccordion_item %}')
    assert html == _STANDALONE_ITEM
    assert "<!--" not in html


def test_an_accordion_without_component_id_renders_its_items_unchanged():
    item = '{% accordion_item id="q" title="Q" %}x{% endaccordion_item %}'
    html = _rust("{% accordion %}" + item + "{% endaccordion %}")
    assert html == '<div class="accordion" id="accordion">' + _STANDALONE_ITEM + "</div>"


def test_a_nested_accordion_without_component_id_is_not_stamped_by_the_outer_one():
    item = '{%% accordion_item id="%s" title="T" %%}%s{%% endaccordion_item %%}'
    src = (
        '{% accordion component_id="outer" %}'
        + item
        % ("o", "{% accordion %}" + item % ("i", "<div><div>x</div></div>") + "{% endaccordion %}")
        + item % ("p", "y")
        + "{% endaccordion %}"
    )
    assert _controls(_rust(src)) == [
        ("accordion_toggle", "outer"),
        ("accordion_toggle", None),
        ("accordion_toggle", "outer"),
    ]


# --- a component_id that cannot be one (#4134 review) --------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [["right"], {"a": 1}, 7, True, "x" * 257])
async def test_the_runtime_refuses_a_component_id_that_is_not_a_short_string(bad):
    """A list ``component_id`` raised an uncaught ``TypeError`` from the lookup
    (unhashable). It is now refused before it, as "Event rejected", and nothing moves."""
    from djust.runtime import ViewRuntime
    from djust.tests.test_bound_component_skip_gate_2900 import MockTransport

    view = _RuntimeTwoSidebars()
    view.mount(None)
    view.render_with_diff()
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    await runtime.dispatch_event(
        {"type": "event", "event": "toggle_sidebar", "params": {"component_id": bad}, "ref": 1}
    )
    errors = [f for f in transport.sent if f.get("type") == "error"]
    # The refusal itself, not just any error: the lookup answers "Component not
    # found" for some of these, which would pass with the check removed.
    assert [f.get("error") for f in errors] == ["Event rejected"], transport.sent
    assert (view.left.collapsed, view.right.collapsed) == (False, False)


@pytest.mark.parametrize("value", [None, "", "right"])
def test_an_absent_or_ordinary_component_id_is_not_refused(value):
    from djust.websocket_utils import component_id_refusal

    assert component_id_refusal({"component_id": value}) is None
    assert component_id_refusal({}) is None


# --- add_col_event is deprecated (owner decision on #4134) ----------------------

_COLS = [{"id": "todo", "title": "To do", "cards": [{"id": "c1", "title": "A"}]}]


def test_add_col_event_warns_once_and_still_draws_nothing(monkeypatch):
    import warnings

    from djust.components.templatetags import djust_components as tags

    monkeypatch.setattr(tags, "_ADD_COL_EVENT_WARNED", False)
    plain = _django("{% kanban_board columns=c %}", {"c": _COLS})
    with pytest.warns(DeprecationWarning, match="add_col_event .*never drawn a control") as got:
        first = _django('{% kanban_board columns=c add_col_event="add_col" %}', {"c": _COLS})
    assert "no earlier than 3.0.0" in str(got[0].message)
    assert first == plain and "add_col" not in first
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _django('{% kanban_board columns=c add_col_event="add_col" %}', {"c": _COLS})


def test_leaving_add_col_event_out_does_not_warn(monkeypatch):
    import warnings

    from djust.components.templatetags import djust_components as tags

    monkeypatch.setattr(tags, "_ADD_COL_EVENT_WARNED", False)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _django("{% kanban_board columns=c %}", {"c": _COLS})
        _rust("{% kanban_board columns=c %}", {"c": _COLS})
