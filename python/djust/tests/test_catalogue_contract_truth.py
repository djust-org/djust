"""What the catalogue and the generated reference say about a component must
be true of the component.

Both djust.org/components/ and the docs site's component reference render
``describe_component``. Each test below pins a statement those pages made that
the component did not bear out.
"""

from __future__ import annotations

import pytest

from djust.theming.gallery.component_registry import describe_component

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _urlconf():
    """The pages reverse `djust_theming:*` URLs, as in test_catalogue_ux."""
    from django.test import override_settings

    from djust.tests.test_catalogue_ux import _SETTINGS

    with override_settings(**_SETTINGS):
        yield


def _detail(component_name: str):
    from django.test import RequestFactory

    from djust.theming.gallery.live_views import ComponentsDetailView

    request = RequestFactory().get(f"/theme/components/{component_name}/")
    view = ComponentsDetailView()
    view.request = request
    view.mount(request, component_name=component_name)
    return view


class TestParametersAreNotMisreportedAsRequired:
    def test_var_keyword_is_never_required(self):
        """``**kwargs`` has no default, which read as "required": every
        reference entry told readers they must pass something called kwargs."""
        params = {p["name"]: p for p in describe_component("accordion")["params"]}
        assert params["kwargs"]["kind"] == "VAR_KEYWORD"
        assert params["kwargs"]["required"] is False

    def test_a_parameter_without_a_default_is_still_required(self):
        params = {p["name"]: p for p in describe_component("alert")["python_class"]["params"]}
        assert params["message"]["required"] is True
        assert params["kwargs"]["required"] is False


class TestEventsAreWhatTheComponentSends:
    def test_an_event_emitted_only_by_the_examples_own_markup_is_not_listed(self):
        """`loading_overlay`'s example wraps a demo button that clicks
        `toggle_loading`; the overlay itself sends nothing."""
        assert "toggle_loading" not in describe_component("loading_overlay")["events"]

    def test_per_item_events_the_caller_names_are_not_the_components(self):
        """`dropdown_menu` renders each item's own `event`; what it sends of its
        own accord is the toggle."""
        events = describe_component("dropdown_menu")["events"]
        assert "toggle_menu" in events
        assert "edit_item" not in events

    def test_an_event_emitted_in_a_state_the_example_does_not_show_is_listed(self):
        """Scraping the first example saw `kanban_add_card` only; the board
        also sends `kanban_move` (from its drag hook), and a host that never
        answers it loses every move."""
        events = describe_component("kanban_board")["events"]
        assert "kanban_move" in events and "kanban_add_card" in events

    def test_events_behind_an_empty_example_are_still_listed(self):
        """`notification_center`'s example has no notifications, so its
        mark-read and clear buttons never rendered and were never listed."""
        events = describe_component("notification_center")["events"]
        assert {"toggle_notifications", "mark_notification_read", "clear_notifications"} <= set(
            events
        )

    def test_a_template_tag_lists_only_what_the_tag_sends(self):
        """`{% theme_pagination %}` is link-based; `page_prev`/`page_next`
        belong to the separate `Pagination` class, not to the tag."""
        assert describe_component("pagination")["events"] == []

    def test_an_event_named_after_the_field_is_still_listed(self):
        """A component that falls back to its `name` as the event (multi_select,
        combobox) emits that name: it is the host's event, not example data."""
        from djust.theming.gallery.catalogue import contract_events

        html = '<div dj-change="frameworks"><input type="checkbox" value="django"></div>'
        params = [{"name": "event", "default": "''"}]
        assert contract_events(params, {"name": "frameworks"}, html) == ["frameworks"]

    def test_an_example_that_renames_the_event_lists_the_new_name(self):
        described = describe_component("color_picker")
        assert described["examples"][0]["event"] in described["events"]

    def test_the_detail_page_lists_the_same_events(self):
        view = _detail("kanban_board")
        assert view._base_ctx["events"] == describe_component("kanban_board")["events"]


class TestDemoHandlersMoveThePreview:
    """A preview click that reaches the server but changes nothing reads as a
    broken component."""

    def test_every_listed_event_of_a_previewed_component_has_a_handler(self):
        from djust.theming.gallery.live_views import Preview

        view = _detail("dropdown_menu")
        for event in view._base_ctx["events"]:
            assert hasattr(Preview, event), f"no preview handler for {event!r}"

    def test_the_dropdown_menu_opens_and_closes(self):
        from djust.theming.gallery.live_views import render_preview_examples

        described = describe_component("dropdown_menu")
        examples = [dict(described["examples"][0], open=False)]
        closed = render_preview_examples("dropdown_menu", "python", examples, {})[0]["html"]
        view = _detail("dropdown_menu")
        view.preview.state.examples = examples
        view.preview.toggle_menu()
        opened = render_preview_examples(
            "dropdown_menu", "python", examples, view.preview.state.values
        )[0]["html"]
        assert opened != closed

    def test_the_date_picker_steps_back_from_the_current_month(self):
        """`month=0` means "this month"; stepping from it produced `None`."""
        from djust.theming.gallery.live_views import _DEMO_EVENTS

        _key, step = _DEMO_EVENTS["date_prev_month"]
        moved = step(0, "")
        assert isinstance(moved, int) and 1 <= moved <= 12

    def test_add_row_adds_a_row_to_what_is_shown(self):
        """`rows=None` renders `min` rows; appending to an empty list rendered
        the same single row again."""
        from djust.theming.gallery.live_views import render_preview_examples, _DEMO_EVENTS

        described = describe_component("form_array")
        example = dict(described["examples"][0]) if described["examples"] else {}
        before = render_preview_examples("form_array", "python", [example], {})[0]["html"]
        key, grow = _DEMO_EVENTS["add_row"]
        after = render_preview_examples(
            "form_array", "python", [example], {key: grow(example.get(key), "")}
        )[0]["html"]
        assert after.count("dj-form-array__row") > before.count("dj-form-array__row")


class TestUsageSnippetsAreCodeThatWorks:
    def test_a_handler_never_writes_a_kwarg_the_component_lacks(self):
        """Every `self.component.<kwarg> = …` a snippet writes is a parameter
        of that component (`self.component.dismissed` was not)."""
        import re

        from djust.theming.gallery.component_registry import COMPONENT_CATEGORIES

        names = sorted({n for group in COMPONENT_CATEGORIES.values() for n in group})
        wrong = []
        for name in names:
            described = describe_component(name)
            if described["component_type"] != "python":
                continue
            params = {p["name"] for p in described["params"]}
            snippet = _detail(name)._base_ctx["usage_parts"]["view"]
            for attr in re.findall(r"self\.component\.(\w+) =", snippet):
                if attr not in params:
                    wrong.append(f"{name}.{attr}")
        assert not wrong, wrong

    def test_the_notification_bell_opens(self):
        from djust.components import NotificationCenter

        assert "notif-center--open" in str(NotificationCenter(is_open=True).render())
        assert "notif-center--open" not in str(NotificationCenter().render())
        snippet = _detail("notification_center")._base_ctx["usage_parts"]["view"]
        assert "self.component.is_open = not self.component.is_open" in snippet

    def test_the_dismiss_handler_does_not_invent_a_dismissed_attribute(self):
        view = _detail("page_alert")
        assert "self.component.dismissed" not in view._base_ctx["usage_parts"]["view"]

    def test_the_copied_example_answers_the_event_its_own_markup_emits(self):
        """The overlay's example carries a demo button; copying the example
        without a `toggle_loading` handler failed on the first click."""
        view = _detail("loading_overlay")
        assert "toggle_loading" not in view._base_ctx["events"]
        assert "def toggle_loading(self" in view._base_ctx["usage_parts"]["view"]

    def test_the_month_arrows_show_a_month_expression_not_a_date_dependent_number(self):
        view = _detail("date_picker")
        snippet = view._base_ctx["usage_parts"]["view"]
        assert "self.component.month % 12 + 1" in snippet
        assert "(self.component.month - 2) % 12 + 1" in snippet


class TestComponentsRenderWhatTheirParametersSay:
    def test_prompt_editor_renders_the_editor_not_the_prompt(self):
        """`template` is the prompt text; the base class took it for the
        component's own Django template and rendered "Summarise  for ."."""
        from djust.components import PromptEditor

        html = str(
            PromptEditor(
                template="Summarise {{topic}} for {{audience}}.", variables={"topic": "x"}
            ).render()
        )
        assert 'class="dj-prompt-editor' in html
        assert "Summarise {{topic}} for {{audience}}." in html


class TestEveryPreviewShowsSomethingOrSaysWhy:
    def test_a_component_without_examples_has_a_specific_reason(self):
        from djust.theming.gallery.component_registry import (
            COMPONENT_CATEGORIES,
            EMPTY_PREVIEW_REASONS,
        )

        names = {n for group in COMPONENT_CATEGORIES.values() for n in group}
        without = {n for n in names if not describe_component(n)["examples"]}
        assert without <= set(EMPTY_PREVIEW_REASONS), sorted(without - set(EMPTY_PREVIEW_REASONS))

    @pytest.mark.parametrize(
        "name",
        ["aspect_ratio", "error_boundary", "presence_avatars", "prompt_editor", "sticky_header"],
    )
    def test_an_example_that_rendered_an_empty_box_now_shows_content(self, name):
        import re

        from djust.theming.gallery.live_views import render_preview_examples

        html = render_preview_examples(name, "python", describe_component(name)["examples"], {})[0][
            "html"
        ]
        assert re.sub(r"<[^>]+>", "", html).strip(), f"{name} preview has no visible text"


class TestClientNeedsAreStated:
    """A component whose markup needs JavaScript djust does not load says so."""

    def test_a_shipped_script_is_named_and_found(self):
        from djust.theming.gallery.component_registry import component_client

        client = component_client("countdown")
        assert client == {
            "hook": "Countdown",
            "script": "djust_components/countdown.js",
            "hook_shipped": True,
        }

    def test_a_hook_nothing_ships_is_reported_as_such(self):
        from djust.theming.gallery.component_registry import component_client

        client = component_client("sortable_list")
        assert client["hook"] == "SortableList"
        assert client["script"] == "" and client["hook_shipped"] is False

    def test_the_hook_is_found_even_when_no_example_renders_it(self):
        """`image_lightbox` renders nothing while closed; its hook is in the
        class source all the same."""
        assert describe_component("image_lightbox")["client"]["hook"] == "ImageLightbox"

    def test_the_detail_page_loads_the_script_and_says_so(self):
        view = _detail("countdown")
        ctx = view._base_ctx
        assert ctx["client_script"] == "djust_components/countdown.js"
        assert ctx["client_script_url"].endswith("djust_components/countdown.js")
        assert ctx["client_hook"] == ""

    def test_the_detail_page_warns_about_an_unshipped_hook(self, client):
        assert _detail("signature_pad")._base_ctx["client_hook"] == "SignaturePad"
        body = client.get("/theme/components/signature_pad/").content.decode()
        assert "Needs a client hook djust does not ship" in body
        assert "window.djust.hooks.SignaturePad" in body

    def test_the_page_includes_the_script_it_names(self, client):
        body = client.get("/theme/components/countdown/").content.decode()
        assert 'djust_components/countdown.js" defer></script>' in body
        assert "Needs its script on the page" in body


class TestParameterNotesAreTrue:
    def _types(self, name):
        view = _detail(name)
        ctx = view.get_context_data()
        return {row[0]: row[1] for row in ctx["params_rows"]}

    def test_identity_note_only_where_name_is_the_identity(self):
        assert "identity is `name`" in self._types("rating")["event"]
        # SignaturePad declares `name` (the form field), so it is no identity.
        assert "identity is `name`" not in self._types("signature_pad")["save_event"]

    def test_a_push_event_is_not_described_as_one_the_view_receives(self):
        note = self._types("conversation_thread")["stream_event"]
        assert "pushes" in note and "renames" not in note


def test_an_accent_background_always_carries_the_accent_foreground():
    """`--accent` is a surface token paired with `--accent-foreground`. Forty-
    nine rules drew the accent with the page's `--foreground` (or none) on
    it; with a vivid accent (djust.org's pale green) hovered text fell to
    1.45:1 contrast."""
    import re
    from pathlib import Path

    import djust

    root = Path(djust.__file__).parent
    offenders = []
    for css in root.rglob("*.css"):
        text = css.read_text(errors="ignore")
        for block in re.findall(r"[^{}]*\{[^{}]*\}", text):
            body = block.split("{", 1)[1]
            if re.search(r"background(?:-color)?:\s*hsl\(var\(--accent\)\)", body):
                if "--accent-foreground" not in body:
                    offenders.append(f"{css.name}: {block.strip()[:90]}")
    assert not offenders, "\n".join(offenders)


def test_a_descriptor_preview_starts_in_the_state_its_example_documents():
    from djust.theming.gallery.live_views import render_preview_examples

    view = _detail("accordion")
    state = view.preview.state
    assert state.values.get("active") == "1"
    html = render_preview_examples("accordion", "python", state.examples, state.values)[0]["html"]
    assert "dj-accordion-item--open" in html


class TestPreviewFeedback:
    """An event the component has no state for still visibly answers."""

    def _values_after(self, name, event, **kw):
        view = _detail(name)
        getattr(view.preview, event)(**kw)
        return view.preview.state

    def test_dismiss_replaces_the_preview_with_a_way_back(self):
        from djust.theming.gallery.live_views import render_preview_examples

        state = self._values_after("page_alert", "dismiss_alert")
        html = render_preview_examples("page_alert", "python", state.examples, state.values)[0][
            "html"
        ]
        assert "Dismissed" in html and 'dj-click="reset_preview"' in html
        view = _detail("page_alert")
        view.preview.dismiss_alert()
        view.preview.reset_preview()
        html = render_preview_examples(
            "page_alert", "python", view.preview.state.examples, view.preview.state.values
        )[0]["html"]
        assert "Your trial expires" in html

    def test_a_host_acted_event_is_acknowledged(self):
        from djust.theming.gallery.live_views import render_preview_examples

        state = self._values_after("approval_gate", "approve")
        html = render_preview_examples("approval_gate", "python", state.examples, state.values)[0][
            "html"
        ]
        assert "Your view received <code>approve</code>" in html

    @pytest.mark.parametrize(
        "name,event",
        [
            ("combobox", "language_search"),
            ("multi_select", "set_frameworks"),
            ("rich_text_editor", "update_content"),
            ("otp_input", "verify_code"),
        ],
    )
    def test_the_events_that_errored_are_answered(self, name, event):
        from djust.theming.gallery.live_views import Preview

        assert event in _detail(name)._base_ctx["events"]
        assert hasattr(Preview, event)

    @pytest.mark.parametrize(
        "name", ["sheet", "bottom_sheet", "export_dialog", "image_lightbox", "command_palette"]
    )
    def test_overlays_preview_open(self, name):
        import re

        from djust.theming.gallery.live_views import render_preview_examples

        html = render_preview_examples(name, "python", describe_component(name)["examples"], {})[0][
            "html"
        ]
        assert re.sub(r"<[^>]+>", "", html).strip(), f"{name} renders nothing"

    def test_otp_input_ships_its_script(self):
        from djust.theming.gallery.component_registry import component_client

        assert component_client("otp_input")["script"] == "djust_components/otp-input.js"


class TestOverlayPanelsDoNotSwallowClicks:
    """djust delegates clicks from the root. A panel with
    `onclick="event.stopPropagation()"` (there to stop inside clicks from
    reaching a backdrop's close) swallowed every dj-click inside it: the
    close ×, Export, Cancel. The close now rides a scrim behind the panel."""

    def test_no_shipped_python_or_template_source_stops_propagation_inline(self):
        from pathlib import Path

        import djust

        root = Path(djust.__file__).parent
        offenders = [
            str(p.relative_to(root))
            for p in list((root / "components").rglob("*.py")) + list(root.rglob("*.html"))
            if "tests" not in p.parts
            and 'onclick="event.stopPropagation()"' in p.read_text(errors="ignore")
        ]
        assert not offenders, offenders

    @pytest.mark.parametrize(
        "cls,kwargs,close",
        [
            ("ExportDialog", {"open": True, "formats": ["csv"]}, "close_export"),
            ("BottomSheet", {"open": True, "title": "t"}, "close_sheet"),
        ],
    )
    def test_the_close_target_is_not_an_ancestor_of_the_panel(self, cls, kwargs, close):
        from html.parser import HTMLParser

        import djust.components as components

        html = str(getattr(components, cls)(**kwargs).render())

        class Walk(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack, self.buttons_under_close = [], 0

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "button" and any(
                    close == s.get("dj-click") for s in self.stack[: -0 or None]
                ):
                    self.buttons_under_close += 1
                if tag not in ("input", "br", "img"):
                    self.stack.append(a)

            def handle_endtag(self, tag):
                if self.stack:
                    self.stack.pop()

        w = Walk()
        w.feed(html)
        assert 'class="dj-scrim"' in html
        assert w.buttons_under_close == 0

    def test_the_modal_tag_closes_from_its_scrim(self):
        from django.template import Context, Template

        html = Template(
            '{% load djust_components %}{% modal title="T" open=True close_event="close_modal" %}'
            '<button dj-click="save">Save</button>{% endmodal %}'
        ).render(Context({}))
        assert "stopPropagation" not in html
        assert '<div class="dj-scrim" dj-click="close_modal"' in html
