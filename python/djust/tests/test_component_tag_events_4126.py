"""#4126: the events component tags send by default have an answer, or a check says so.

Three parts:

- Each tag whose default event is UI state (close, toggle, navigate, step) has
  a descriptor in ``djust.components.descriptors`` that answers it. Every
  ``dj-click`` / ``dj-input`` the tag draws is sent to a view that declares
  only that descriptor, on both template engines, and moves the state.
- ``djust.T019`` reports a tag whose event nothing on the view answers: a
  ``{% modal %}`` with ``open=`` and no ``Modal()``, a ``{% cookie_consent %}``
  without an ``accept_cookies`` handler. The table it reads
  (``djust.components.tag_events.TAG_EVENTS``) is pinned against what each tag
  renders.
- A ``Carousel()`` without ``total`` logs a warning when a click cannot move it.
"""

from __future__ import annotations

import logging
import re

import pytest

from djust import LiveView
from djust.components import descriptors as D
from djust.components.tag_events import TAG_EVENTS
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient
from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

_ENGINES = {"django": _django, "rust": _rust}

_EVENT = re.compile(r'dj-(?:click|input)="([^"]+)"')


def _events(html: str) -> list:
    return _EVENT.findall(html)


# --- descriptors answer the tags' default events ----------------------------

#: (descriptor, its state, template rendering the tag with every control drawn,
#:  context, [(event sent, the params the markup carries, attribute, expected)])
CASES = [
    (
        D.ExpandableText,
        {},
        "{% expandable_text max_lines=1 %}" + "word " * 200 + "{% endexpandable_text %}",
        {},
        [("toggle_expand", {}, "expanded", True), ("toggle_expand", {}, "expanded", False)],
    ),
    (
        D.TruncatedList,
        {},
        "{% truncated_list items=items max=1 %}",
        {"items": ["a", "b", "c"]},
        [("toggle_list", {}, "expanded", True)],
    ),
    (
        D.NavMenu,
        {},
        '{% nav_menu brand="B" %}{% endnav_menu %}',
        {},
        [("toggle_nav", {}, "mobile_open", True), ("toggle_nav", {}, "mobile_open", False)],
    ),
    (
        D.AnnouncementBar,
        {},
        "{% announcement_bar dismissible=True %}Hi{% endannouncement_bar %}",
        {},
        [("dismiss_announcement", {}, "dismissed", True)],
    ),
    (
        D.MarkdownTextarea,
        {},
        '{% markdown_textarea name="body" %}',
        {},
        [
            ("toggle_preview", {"mode": "preview"}, "preview", True),
            ("toggle_preview", {"mode": "preview"}, "preview", True),
            ("toggle_preview", {"mode": "write"}, "preview", False),
        ],
    ),
    (
        D.Stepper,
        {"active": 0},
        "{% stepper steps=steps active=0 %}",
        {"steps": ["One", "Two", "Three"]},
        [("set_step", {"value": "2"}, "active", 2)],
    ),
    (
        D.Stepper,
        {"active": "a"},
        '{% wizard steps=steps active="a" %}{% endwizard %}',
        {"steps": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]},
        [("set_step", {"value": "b"}, "active", "b")],
    ),
    (
        D.Lightbox,
        {"is_open": True, "active": 0},
        "{% lightbox images=images open=True active=0 %}",
        {"images": ["/a.png", "/b.png", "/c.png"]},
        [
            ("lightbox_navigate", {"value": "1"}, "active", 1),
            ("lightbox_navigate", {"value": "-1"}, "active", 0),
            ("close_lightbox", {}, "is_open", False),
            ("open_lightbox", {"value": "2"}, "active", 2),
        ],
    ),
    (
        D.CommandPalette,
        {"is_open": True},
        "{% command_palette open=True %}{% endcommand_palette %}",
        {},
        [("close_palette", {}, "is_open", False), ("open_palette", {}, "is_open", True)],
    ),
    (
        D.ExportDialog,
        {"is_open": True},
        "{% export_dialog open=True formats=formats %}",
        {"formats": ["csv"]},
        [("close_export", {}, "is_open", False), ("open_export", {}, "is_open", True)],
    ),
]

#: Events a tag above draws that stay the application's (TAG_EVENTS has no
#: component for them).
APP_EVENTS = {"palette_search", "export"}


def _page(descriptor, state):
    return type(
        "Page_%s" % descriptor.__name__,
        (LiveView,),
        {
            "exposure_policy": "legacy",
            "widget": descriptor(**state),
            "mount": lambda self, request, **kwargs: None,
        },
    )


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize(
    "descriptor, state, src, ctx, steps", CASES, ids=[c[0].__name__ + c[2][3:16] for c in CASES]
)
def test_descriptor_answers_every_event_the_tag_draws(engine, descriptor, state, src, ctx, steps):
    drawn = set(_events(_ENGINES[engine](src, ctx)))
    framework = drawn - APP_EVENTS
    assert framework, drawn
    # Every UI-state event the tag draws is one the descriptor answers.
    assert framework <= set(descriptor.Meta.events), (drawn, descriptor.Meta.events)
    assert {s[0] for s in steps} >= framework

    client = LiveViewTestClient(_page(descriptor, state)).mount()
    for event, params, attr, expected in steps:
        assert client.send_event(event, **params)["success"], event
        assert getattr(client.view_instance.widget, attr) == expected, (event, params)


def test_an_app_handler_of_the_same_name_wins():
    calls = []

    class Own(LiveView):
        exposure_policy = "legacy"
        bio = D.ExpandableText()

        @event_handler
        def toggle_expand(self, **kwargs):
            calls.append("own")

    view = Own()
    view.toggle_expand()
    assert calls == ["own"]
    assert view.bio.expanded is False


# --- the table the check reads matches what the tags draw --------------------

#: For each tag in TAG_EVENTS: a template drawing every control, with no
#: event argument given.
DRAW_ALL = {
    "modal": ("{% modal open=True %}x{% endmodal %}", {}),
    "sheet": ("{% sheet open=True %}x{% endsheet %}", {}),
    "lightbox": ("{% lightbox images=i open=True %}", {"i": ["/a.png", "/b.png"]}),
    "command_palette": ("{% command_palette open=True %}{% endcommand_palette %}", {}),
    "export_dialog": ("{% export_dialog open=True formats=f %}", {"f": ["csv"]}),
    "cookie_consent": ("{% cookie_consent %}{% endcookie_consent %}", {}),
    "announcement_bar": (
        "{% announcement_bar dismissible=True %}x{% endannouncement_bar %}",
        {},
    ),
    "nav_menu": ("{% nav_menu %}{% endnav_menu %}", {}),
    "expandable_text": ("{% expandable_text %}" + "w " * 400 + "{% endexpandable_text %}", {}),
    "truncated_list": ("{% truncated_list items=i max=1 %}", {"i": ["a", "b"]}),
    "markdown_textarea": ("{% markdown_textarea %}", {}),
    "stepper": ("{% stepper steps=s %}", {"s": ["a", "b"]}),
    "wizard": ("{% wizard steps=s %}{% endwizard %}", {"s": [{"id": "a", "label": "A"}]}),
}


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("tag", sorted(TAG_EVENTS))
def test_tag_events_table_matches_what_the_tag_draws(engine, tag):
    assert set(DRAW_ALL) == set(TAG_EVENTS)
    src, ctx = DRAW_ALL[tag]
    drawn = set(_events(_ENGINES[engine](src, ctx)))
    assert drawn == {e.default for e in TAG_EVENTS[tag]}, (tag, drawn)


def test_every_component_named_in_the_table_answers_its_event():
    for tag, events in TAG_EVENTS.items():
        for event in events:
            if event.component is None:
                continue
            descriptor = getattr(D, event.component)
            meta = descriptor.Meta
            answered = set(getattr(meta, "events", ())) | {getattr(meta, "event", None)}
            assert event.default in answered, (tag, event)


def test_announcement_bar_draws_no_event_unless_dismissible():
    assert _events(_django("{% announcement_bar %}x{% endannouncement_bar %}")) == []


# --- djust.T019 for a tag event nothing answers -----------------------------


def _findings(cls):
    from djust._template_bindings import django_engine
    from djust.checks.bindings import scan_owner

    report = scan_owner(cls, "view", django_engine(), {})
    assert not report.error, report.error
    return [
        (r.binding.name, f.check_id, f.message, f.hint) for r in report.results for f in r.findings
    ], report


def _view(template, **attrs):
    return type(
        "TagView",
        (LiveView,),
        {
            "exposure_policy": "legacy",
            "template": "{% load djust_components %}<div dj-root>" + template + "</div>",
            **attrs,
        },
    )


def test_t019_reports_a_modal_without_a_descriptor_or_handler():
    findings, _ = _findings(_view("{% modal open=show %}x{% endmodal %}"))
    assert [(f[0], f[1]) for f in findings] == [("close_modal", "T019")]
    _, _, message, hint = findings[0]
    assert "{% modal %} sends 'close_modal' (its close_event)" in message
    assert "Modal()" in hint and "@event_handler named 'close_modal'" in hint


def test_t019_reports_a_sheet_without_a_descriptor_or_handler():
    findings, _ = _findings(_view("{% sheet open=show %}x{% endsheet %}"))
    assert [(f[0], f[1]) for f in findings] == [("close_sheet", "T019")]
    assert "Sheet()" in findings[0][3]


def test_t019_quiet_when_the_descriptor_is_declared():
    findings, report = _findings(
        _view("{% modal open=dialog.is_open %}x{% endmodal %}", dialog=D.Modal())
    )
    assert findings == []
    tag_results = [r for r in report.results if r.binding.tag]
    assert [(r.binding.name, r.status) for r in tag_results] == [("close_modal", "checked")]


def test_t019_quiet_when_the_app_handles_the_event():
    def close_modal(self, **kwargs):
        pass

    findings, _ = _findings(
        _view("{% modal open=show %}x{% endmodal %}", close_modal=event_handler(close_modal))
    )
    assert findings == []


def test_t019_follows_a_renamed_and_a_turned_off_event():
    findings, _ = _findings(_view('{% modal open=show close_event="shut" %}x{% endmodal %}'))
    assert [(f[0], f[1]) for f in findings] == [("shut", "T019")]
    findings, _ = _findings(_view("{% modal open=show close_event=None %}x{% endmodal %}"))
    assert findings == []
    _, report = _findings(_view("{% modal open=show close_event=name %}x{% endmodal %}"))
    assert [r.status for r in report.results if r.binding.tag] == ["dynamic"]


def test_t019_app_action_hint_says_the_application_answers_it():
    findings, _ = _findings(_view("{% cookie_consent %}{% endcookie_consent %}"))
    assert [(f[0], f[1]) for f in findings] == [("accept_cookies", "T019")]
    assert "the application's to answer" in findings[0][3]


def test_t019_announcement_bar_only_when_dismissible():
    findings, _ = _findings(_view("{% announcement_bar %}x{% endannouncement_bar %}"))
    assert findings == []
    findings, _ = _findings(
        _view("{% announcement_bar dismissible=True %}x{% endannouncement_bar %}")
    )
    assert [f[0] for f in findings] == ["dismiss_announcement"]


@pytest.mark.parametrize(
    "src",
    [
        "{% stepper steps=s %}",
        "{% wizard steps=s %}{% endwizard %}",
        "{% nav_menu %}{% endnav_menu %}",
        "{% expandable_text %}x{% endexpandable_text %}",
        "{% truncated_list items=i %}",
        "{% markdown_textarea %}",
        "{% lightbox images=i open=o %}",
        "{% command_palette open=o %}{% endcommand_palette %}",
        "{% export_dialog open=o %}",
    ],
)
def test_t019_reports_each_ui_state_tag_without_its_descriptor(src):
    tag = src.split()[1]
    findings, _ = _findings(_view(src))
    reported = {f[0] for f in findings}
    assert reported == {e.default for e in TAG_EVENTS[tag]}, (tag, findings)
    for event in TAG_EVENTS[tag]:
        if event.component:
            hint = next(f[3] for f in findings if f[0] == event.default)
            assert "%s()" % event.component in hint, hint


# --- Carousel without total --------------------------------------------------


def test_carousel_without_total_warns_once_and_does_not_move(caplog):
    class Slides(LiveView):
        exposure_policy = "legacy"
        slides = D.Carousel()

    view = Slides()
    with caplog.at_level(logging.WARNING, logger="djust.components"):
        view.carousel_next()
        view.carousel_prev()
        view.carousel_go(value="1")
    warnings = [r.getMessage() for r in caplog.records if r.name == "djust.components"]
    assert len(warnings) == 1, warnings
    assert "Carousel 'slides' has no total" in warnings[0]
    assert view.slides.active == 0


def test_carousel_with_total_does_not_warn(caplog):
    class Slides(LiveView):
        exposure_policy = "legacy"
        slides = D.Carousel(total=3)

    view = Slides()
    with caplog.at_level(logging.WARNING, logger="djust.components"):
        view.carousel_next()
    assert view.slides.active == 1
    assert not [r for r in caplog.records if r.name == "djust.components"]


def test_t019_does_not_check_the_arguments_a_tag_writes_itself():
    """``{% command_palette %}``'s input sends ``value`` as you type; the scan
    sees no ``value`` in the template, so it must not report it missing."""

    def palette_search(self, value: str):
        pass

    findings, report = _findings(
        _view(
            "{% command_palette open=o %}{% endcommand_palette %}",
            event_parameter_policy="strict",
            palette=D.CommandPalette(),
            palette_search=event_handler(palette_search),
        )
    )
    assert findings == []
    assert {r.binding.name: r.status for r in report.results if r.binding.tag} == {
        "close_palette": "checked",
        "palette_search": "checked",
    }


# --- events that never reach the view ---------------------------------------


def test_scroll_spy_event_is_a_browser_event_not_a_view_event():
    """``{% scroll_spy %}`` marks the active link in the browser and dispatches
    ``djust:section_changed`` on its element; its markup sends nothing to the
    view, and the catalogue does not ask a host to answer it."""
    from djust.theming.gallery.catalogue import contract_events

    html = _django("{% scroll_spy sections=s %}", {"s": ["intro", "pricing"]})
    assert 'data-event="section_changed"' in html
    assert _events(html) == []
    params = [{"name": "active_event", "default": "'section_changed'"}]
    assert contract_events(params, {}, html) == []
    # A server-bound event parameter still counts.
    params.append({"name": "toggle_event", "default": "'toggle_nav'"})
    assert contract_events(params, {}, html) == ["toggle_nav"]
