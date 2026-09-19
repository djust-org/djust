"""What a catalogue preview shows, and whether it is what a developer gets.

Six defects reported against the catalogue on 2026-09-17 shared one property:
the preview rendered *something*, so nothing looked broken. `progress` printed
`style="width: %"`; `dropdown`'s menu was an empty div; `switch` could not
toggle; `segmented_progress` had nothing to click; `loading_overlay` was an
empty wrapper; `model_selector` could not open. An empty string is
indistinguishable from a correctly-rendered empty value, and a component that
renders nothing looks the same as one with nothing to render.

Each class is pinned here:

* ``TestTemplatePreviewGoesThroughTheTag`` — the preview is the tag's own
  output, not the raw template's. Routing around the tag silently drops every
  context name the tag computes.
* ``TestExamplesAreNotCollapsedByDemoState`` — each example renders its own
  kwargs until a reader moves something.
* ``TestSlotsReachTheTemplate`` — a caller-supplied ``slot_*`` renders.
* ``TestUsageSnippetIsRealCode`` — the documented snippet compiles and renders.
* ``TestPreviewHostsItsInteractions`` — every event a preview emits has a
  handler on the view.
"""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import re
from pathlib import Path

import pytest
from django.template import Context, Engine, RequestContext

from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.gallery.context import _EXAMPLE_BUILDERS
from djust.theming.gallery.catalogue import build_catalogue_detail_context
from djust.theming.templatetags import theme_components

pytestmark = pytest.mark.theming


def _preview(component_name: str) -> str:
    """Every example's preview HTML, concatenated."""
    ctx = build_catalogue_detail_context(component_name)
    rendered = ctx.get("template_examples_html") or ctx.get("python_examples_html") or []
    return "".join(ex["html"] for ex in rendered)


# ---------------------------------------------------------------------------
# A. The preview is the tag's render
# ---------------------------------------------------------------------------


class TestTemplatePreviewGoesThroughTheTag:
    """The catalogue hands a template component's example to its **tag**.

    The template alone is not the component. `progress.html` reads
    ``percentage``, which ``{% theme_progress %}`` computes; handing the raw
    template only the example's kwargs leaves that name unfilled, and Django's
    default ``string_if_invalid=""`` turns an unfilled name into an empty
    string rather than an error. The page read ``style="width: %"`` for every
    example while the same tag rendered a real width for any actual caller.
    """

    def test_progress_preview_carries_the_computed_width(self):
        html = _preview("progress")
        assert "width: %" not in html, (
            "the preview is rendering `progress.html` without `{% theme_progress %}`, "
            "so `percentage` is unfilled"
        )
        # The first example is value=25/max=100.
        assert 'style="width: 25.0%"' in html

    def test_progress_examples_each_show_their_own_value(self):
        """Four value examples and one indeterminate — five distinct bars.

        Ordered, not just distinct: the widths must follow the examples
        (25/50/75/100), which is what proves each example renders its own
        kwargs rather than the first example's repeated.
        """
        html = _preview("progress")
        widths = [float(w) for w in re.findall(r'style="width: ([\d.]+)%"', html)]
        assert widths == [25.0, 50.0, 75.0, 100.0]
        assert "progress-indeterminate" in html

    def test_a_preview_matches_a_direct_tag_render(self):
        """The preview is byte-for-byte what the tag produces.

        Weaker assertions than this pass while the preview is produced a
        different way; this one fails the moment the two paths diverge.
        """
        for name in ("progress", "alert", "badge", "button", "card"):
            examples = _EXAMPLE_BUILDERS[name]()
            expected = str(
                getattr(theme_components, f"theme_{name}")(RequestContext({}), **examples[0])
            )
            ctx = build_catalogue_detail_context(name)
            preview = ctx["template_examples_html"][0]["html"]
            assert preview == expected, f"{name}: preview is not the tag's own render"

    def test_no_tag_leaves_a_name_its_template_reads_unfilled(self):
        """The general form of the `progress` bug, across every template tag.

        Records the exact context each tag builds — by wrapping the template's
        ``render`` — then renders that template with a sentinel
        ``string_if_invalid``. A sentinel in the output is a name the template
        reads that the tag never supplied, which is precisely what left
        ``{{ percentage }}`` empty and what Django's default ``""`` hides.
        """
        sentinel = "\x00MISSING\x00"
        sentinel_engine = Engine(
            libraries={"theme_components": "djust.theming.templatetags.theme_components"},
            string_if_invalid=sentinel,
        )

        import djust.theming.templatetags.theme_components as tc
        from djust.theming.gallery.catalogue import get_component_template_source

        original = tc.resolve_component_template
        offenders = []
        try:
            for name in sorted(COMPONENT_CONTRACTS):
                if name not in _EXAMPLE_BUILDERS:
                    continue
                source = get_component_template_source(name)
                examples = _EXAMPLE_BUILDERS[name]()
                if not source or not examples:
                    continue

                captured: dict = {}

                class _Recorder:
                    def __init__(self, template):
                        self._template = template

                    def render(self, ctx):
                        captured.update(ctx)
                        return self._template.render(Context(ctx))

                tc.resolve_component_template = lambda _r, component, _t=source: _Recorder(
                    sentinel_engine.from_string(_t)
                )
                str(getattr(tc, f"theme_{name}")(RequestContext({}), **examples[0]))

                out = sentinel_engine.from_string(source).render(Context(dict(captured)))
                if sentinel in out:
                    offenders.append(name)
        finally:
            tc.resolve_component_template = original

        assert offenders == [], (
            "these tags do not supply a name their template reads: " + ", ".join(offenders)
        )


# ---------------------------------------------------------------------------
# B. Examples keep their own values
# ---------------------------------------------------------------------------


class TestExamplesAreNotCollapsedByDemoState:
    """Each example renders its own kwargs until the reader moves something.

    `_render_examples` merges the demo state into *every* example, so seeding
    that state from `examples[0]` made the whole page show the first example
    repeated: a two-state switch showed two switches both on, and five progress
    bars all read the same value.
    """

    def test_mount_leaves_the_demo_state_empty(self):
        """The state starts empty; the renderer merges it into *every* example.

        This is the mechanism, so it is pinned where it lives: seed this dict
        and each switch preview renders the first example's `checked`, each
        progress bar the first example's value.
        """
        from django.test import RequestFactory

        from djust.theming.gallery.live_views import ComponentsDetailView

        view = ComponentsDetailView()
        view.mount(RequestFactory().get("/"), component_name="switch")
        assert view.preview.state.values == {}

        rendered = view._render_examples()
        assert rendered[0]["html"] != rendered[1]["html"], (
            "both switch examples rendered identically — the demo state is "
            "overriding the examples instead of starting empty"
        )

    def test_switch_examples_show_both_states(self):
        html = _preview("switch")
        assert "dj-switch dj-switch-checked" in html
        assert re.search(
            r'class="dj-switch"[^>]*>\s*<input[^>]*class="dj-switch-input"[^>]*>', html
        ), "the unchecked example rendered checked — demo state is collapsing the examples"

    def test_switch_examples_name_an_action_so_the_input_can_dispatch(self):
        """`.dj-switch-checked` is server-rendered, so a switch needs a handler."""
        ctx = build_catalogue_detail_context("switch")
        for example in ctx["examples"] if "examples" in ctx else []:
            assert example.get("action")

    def test_switch_input_carries_dj_change(self):
        assert 'dj-change="toggle_switch"' in _preview("switch")


# ---------------------------------------------------------------------------
# C. Slots reach the template
# ---------------------------------------------------------------------------


class TestSlotsReachTheTemplate:
    """A `slot_*` keyword is context, not an attribute.

    Eight tags read `slot_*` in their templates without ever calling
    `_extract_slots`, so those keywords stayed in `attrs` — which templates
    only read as `attrs.class` / `attrs.id`. A caller-supplied slot rendered as
    nothing at all: the dropdown's menu was an empty div.
    """

    SLOT_TAGS = ["alert", "badge", "button", "dropdown", "input", "modal", "pagination", "table"]

    @pytest.mark.parametrize("name", SLOT_TAGS)
    def test_tag_extracts_slots(self, name: str):
        """Rendering with a slot must not put it in `attrs`."""
        tag = getattr(theme_components, f"theme_{name}")
        examples = _EXAMPLE_BUILDERS.get(name)
        marker = "<em>slot-content</em>"

        # Give whichever slot this template reads a value.
        from djust.theming.gallery.catalogue import get_component_template_source

        source = get_component_template_source(name) or ""
        slots = set(re.findall(r"{%\s*if\s+(slot_\w+)", source))
        assert slots, f"{name}'s template reads no slot — drop it from SLOT_TAGS"
        slot_name = sorted(slots)[0]

        kwargs = dict(examples()[0]) if examples else {}
        kwargs[slot_name] = marker
        html = str(tag(RequestContext({}), **kwargs))
        assert marker in html, f"{name}: `{slot_name}` was accepted and dropped"

    def test_dropdown_preview_has_menu_items(self):
        html = _preview("dropdown")
        assert "dropdown-item" in html
        assert len(re.findall(r'class="dropdown-item"', html)) >= 3
        assert 'style="display:none;"' not in html
        assert 'aria-expanded="true"' in html
        assert re.search(
            r'class="dropdown-menu dropdown-left"[\s\S]*role="menu"[\s\S]*data-open="true"',
            html,
        )

    def test_checkbox_preview_has_readable_layout_hooks(self):
        html = _preview("checkbox")
        assert 'class="checkbox-group' in html
        assert 'class="checkbox-label"' in html
        assert 'class="checkbox-description"' in html

    def test_live_dropdown_preview_starts_open(self):
        from django.test import RequestFactory

        from djust.theming.gallery.live_views import ComponentsDetailView

        view = ComponentsDetailView()
        view.mount(RequestFactory().get("/"), component_name="dropdown")
        assert view.preview.state.values["is_open"] is True
        html = "".join(example["html"] for example in view._render_examples())
        assert 'style="display:none;"' not in html
        assert "Edit" in html and "Archive" in html


# ---------------------------------------------------------------------------
# D. The documented snippet is real code
# ---------------------------------------------------------------------------


class TestUsageSnippetIsRealCode:
    """USAGE shows the two files a developer writes, and both must work.

    `repr()` of a list of dicts is not valid Django template syntax — inlining
    it produced `{% theme_nav items=[{'label': 'Home'}] %}`, a
    TemplateSyntaxError in the one block whose job is to be copy-pasteable.
    """

    def _engine(self) -> Engine:
        return Engine(
            libraries={
                "theme_components": "djust.theming.templatetags.theme_components",
                "theme_tags": "djust.theming.templatetags.theme_tags",
            }
        )

    def test_snippet_shows_a_liveview_and_a_template(self):
        snippet = build_catalogue_detail_context("progress")["usage_snippet"]
        assert "class MyView(LiveView)" in snippet
        assert "{% theme_progress" in snippet

    def test_python_snippet_interpolates_the_component(self):
        snippet = build_catalogue_detail_context("switch")["usage_snippet"]
        assert "{{ component }}" in snippet  # render() marks it safe; no filter needed
        assert "self.component = Switch(" in snippet

    @pytest.mark.parametrize(
        "name", sorted(n for n in COMPONENT_CONTRACTS if n in _EXAMPLE_BUILDERS)
    )
    def test_template_snippet_compiles_and_renders(self, name: str):
        ctx = build_catalogue_detail_context(name)
        if ctx["component_type"] != "template":
            pytest.skip("python component")
        view_src, template_src = ctx["usage_snippet"].split("# my_template.html")

        # Rebuild the state the snippet's `mount` assigns, then render the tag
        # it documents. A snippet that raises here is a snippet nobody can use.
        state: dict = {}
        for line in view_src.split("def mount(self, request, **kwargs):")[1].splitlines():
            stripped = line.strip()
            if stripped.startswith("self."):
                key, value = stripped[len("self.") :].split(" = ", 1)
                state[key] = eval(value, {})  # noqa: S307 — our own generated literal

        self._engine().from_string(template_src).render(Context(state))


# ---------------------------------------------------------------------------
# E. Source and styling are documented
# ---------------------------------------------------------------------------


class TestSourceAndStylesAreReported:
    """ "What file is the template in, and what CSS styles it?" has an answer."""

    def test_template_component_names_its_template(self):
        ctx = build_catalogue_detail_context("card")
        assert ctx["template_path"] == "djust_theming/components/card.html"

    def test_python_component_names_its_module(self):
        ctx = build_catalogue_detail_context("switch")
        assert ctx["module_path"] == "djust.components.components.switch"

    def test_styles_point_at_real_rules(self):
        ctx = build_catalogue_detail_context("switch")
        assert ctx["styles"], "no stylesheet rule reported for a styled component"
        top = ctx["styles"][0]
        assert top["lines"] and top["classes"]
        # Every reported line must be a distinct, positive line number.
        assert all(isinstance(n, int) and n > 0 for n in top["lines"])
        assert len(top["lines"]) == len(set(top["lines"]))

    def test_style_paths_resolve_to_files_on_disk(self):
        from pathlib import Path

        from djust.theming.gallery.catalogue import _CSS_TREES

        roots = {label: tree for label, tree in _CSS_TREES}
        for name in ("switch", "card", "progress"):
            for sheet in build_catalogue_detail_context(name)["styles"]:
                label, _, relative = sheet["path"].partition("/")
                assert label in roots
                assert Path(roots[label], relative).is_file(), sheet["path"]


# ---------------------------------------------------------------------------
# F. The preview hosts its own interactions
# ---------------------------------------------------------------------------


class TestPreviewHostsItsInteractions:
    """Every event a preview emits resolves on the view that renders it.

    A component renders `dj-click="X"` because a host is expected to answer it.
    On a catalogue page this view is the host, and an unanswered event is a
    server error rather than a no-op.
    """

    REPORTED = {
        "switch": "toggle_switch",
        "segmented_progress": "set_segment",
        "loading_overlay": "toggle_loading",
        "model_selector": "toggle_model_selector",
        "dropdown": "toggle_dropdown",
    }

    @pytest.mark.parametrize("component,event", sorted(REPORTED.items()))
    def test_view_answers_the_event(self, component: str, event: str):
        from djust.theming.gallery.live_views import ComponentsDetailView

        handler = getattr(ComponentsDetailView, event, None)
        assert handler is not None, f"{component}'s preview emits `{event}`, which nothing answers"
        assert hasattr(handler, "_djust_decorators"), f"`{event}` is not an event handler"

    @pytest.mark.parametrize("component,event", sorted(REPORTED.items()))
    def test_preview_emits_the_event(self, component: str, event: str):
        html = _preview(component)
        assert event in html, f"{component}'s preview no longer emits `{event}`"

    def test_switch_handler_accepts_a_bool(self):
        """A checkbox's `dj-change` sends a bool, not a string.

        Annotating the demo handler `value: str` made the framework reject the
        event before it ran — "expected str, got bool (True)" — so the switch
        stayed stuck with its input wired to a handler that could not fire.
        """
        from djust.theming.gallery.live_views import ComponentsDetailView

        handler = ComponentsDetailView.toggle_switch
        assert handler.__annotations__.get("value") is not str


class TestComponentsAssetsAreCacheBusted:
    """Django's static server sends no `Cache-Control`, so a bare link is cached.

    The catalogue linked `djust_components/components.css` a second time,
    unversioned, after `theme_head` had already linked it versioned — and the
    bare copy, coming second, won. An edit to that stylesheet was then
    invisible on the page it was made for, which reads as "the fix didn't
    work". Every link the catalogue adds of its own has to carry the token;
    they all live in the one `_assets.html` partial a host site includes.
    """

    BASE = (
        Path(__file__).resolve().parent.parent
        / "theming"
        / "templates"
        / "djust_theming"
        / "catalogue"
        / "_assets.html"
    )

    def test_every_stylesheet_link_carries_the_version_token(self):
        source = self.BASE.read_text()
        links = re.findall(r"<link[^>]+rel=\"stylesheet\"[^>]*>", source)
        assert links, "no stylesheet links found — is this the right template?"
        unversioned = [link for link in links if "?v={% theme_asset_version %}" not in link]
        assert unversioned == [], f"these links will go stale on edit: {unversioned}"

    def test_components_css_is_not_linked_twice(self):
        """`theme_head` links it; a second bare link is the stale-wins case.

        Checked over the `<link>` tags rather than the whole file, so the
        comment explaining this can name the file it is about.
        """
        source = self.BASE.read_text()
        links = re.findall(r"<link[^>]*>", source)
        offenders = [link for link in links if "djust_components/components.css" in link]
        assert offenders == [], f"linked again outside theme_head: {offenders}"
        assert "{% theme_head %}" in source

    def test_the_version_tag_reports_a_token(self):
        from djust.theming.templatetags.theme_tags import theme_asset_version

        assert theme_asset_version()


class TestModelSelectorOpens:
    """It reported as "doesn't do anything": nothing could open it.

    The option list carried `display: none` and no rule anywhere turned it back
    on, and the trigger dispatched nothing — so the selector could be populated
    and still never open.
    """

    OPTIONS = [
        {"value": "a", "label": "Model A", "description": "First", "tier": "free"},
        {"value": "b", "label": "Model B", "description": "Second", "tier": "premium"},
    ]

    def test_a_closed_selector_marks_itself_closed(self):
        from djust.components.components.model_selector import ModelSelector

        html = ModelSelector(name="model", options=self.OPTIONS)._render_custom()
        assert "data-open" not in html
        assert 'aria-expanded="false"' in html

    def test_an_open_selector_marks_itself_open(self):
        from djust.components.components.model_selector import ModelSelector

        html = ModelSelector(name="model", options=self.OPTIONS, is_open=True)._render_custom()
        assert 'data-open="true"' in html
        assert 'aria-expanded="true"' in html

    def test_the_css_reveals_the_list_for_the_open_marker(self):
        """`data-open` is only worth emitting if a rule keys on it."""
        from pathlib import Path

        css = (
            Path(__file__).resolve().parent.parent
            / "components"
            / "static"
            / "djust_components"
            / "components-classes.css"
        ).read_text()
        assert '.dj-model-sel[data-open="true"] .dj-model-sel__dropdown' in css
        assert "display: block" in css.split('.dj-model-sel[data-open="true"]')[1][:120]

    def test_the_trigger_dispatches_an_open_event(self):
        from djust.components.components.model_selector import ModelSelector

        html = ModelSelector(name="model", options=self.OPTIONS)._render_custom()
        assert 'dj-click="toggle_model_selector"' in html

    def test_preview_shows_options_and_a_trigger(self):
        """The example must carry options, not render an empty list."""
        html = _preview("model_selector")
        assert 'dj-click="toggle_model_selector"' in html
        assert html.count('class="dj-model-sel__opt') >= 2
        assert "dj-model-sel__name" in html


class TestSegmentedProgressIsClickable:
    """It reported as "no integration" — nothing could move it."""

    def test_steps_are_buttons_carrying_their_number(self):
        html = _preview("segmented_progress")
        assert "<button" in html
        assert 'dj-click="set_segment"' in html
        assert 'dj-value-value:int="3"' in html

    def test_steps_are_plain_divs_without_an_event(self):
        """A progress indicator wired to nothing should not look clickable."""
        from djust.components.components.segmented_progress import SegmentedProgress

        html = SegmentedProgress(steps=["A", "B"], current=1, event="")._render_custom()
        assert "<button" not in html
        assert "dj-click" not in html


class TestPreviewOwnsTheDescriptorState:
    """ADR-032: the page's live preview is ONE bound component whose State
    holds the descriptor state (accordion, tabs, modal, …) and the demo values.

    Before, eight descriptor slots lived on the view and `_descriptor_state`
    unwrapped the current one's `BoundComponent` (ADR-031) into the example
    kwargs. Now the descriptor's defaults seed `preview.state.values` at mount,
    its `_handle_event` runs against a State rebuilt from those values, and the
    page reads the preview as the bare `{{ preview }}` — which is what lets an
    event that changes only the preview patch the preview alone.
    """

    def _view(self, component_name: str):
        from django.test import RequestFactory

        from djust.theming.gallery.live_views import ComponentsDetailView

        view = ComponentsDetailView()
        view.mount(RequestFactory().get("/"), component_name=component_name)
        return view

    def test_the_preview_is_a_bound_component(self):
        from djust.components.base import BoundComponent

        bound = self._view("accordion").preview
        assert isinstance(bound, BoundComponent)
        assert bound.template, (
            "the preview must declare a template for `{{ preview }}` to render it"
        )

    def test_the_descriptor_fields_seed_the_preview_values(self):
        from djust.components.descriptors import Accordion

        values = self._view("accordion").preview.state.values
        assert values == dict(Accordion.State())
        assert "active" in values

    def test_a_descriptor_event_moves_the_values_and_the_render(self):
        """The forwarder on the view and the handler on the preview move the
        same state, and the change reaches the markup."""
        view = self._view("accordion")
        before = view._render_examples()[0]["html"]
        view.accordion_toggle(value="2")
        after = view._render_examples()[0]["html"]
        assert view.preview.state.values["active"] == "2"
        assert before != after
        assert "accordion-item--open" in after
        # The component route reaches the same handler on the preview itself.
        view.preview.accordion_toggle(value="2")
        assert view.preview.state.values["active"] == ""

    def test_the_page_reads_only_the_bare_preview(self):
        """D2: any other read of the component would force a page render."""
        from pathlib import Path

        import djust.theming as theming

        source = (
            Path(theming.__file__).parent / "templates/djust_theming/catalogue/detail.html"
        ).read_text()
        assert "{{ preview }}" in source
        assert "preview." not in source and "preview|" not in source
        assert "examples_html" not in source


class TestEveryDemoEventResolvesOnThePreview:
    """#2921 review 🔴1: the demo handlers run with ``self`` bound to the
    preview's ``BoundComponent``, which refuses ``_``-prefixed lookups — a
    private helper on the component class raised ``AttributeError`` for 42 of
    the 53 events the previews emit. Every ``_DEMO_EVENTS`` key is dispatched
    here through the component itself (the route a real click takes)."""

    @staticmethod
    def _events() -> list:
        from djust.theming.gallery.live_views import _DEMO_EVENTS

        return sorted(_DEMO_EVENTS)

    @pytest.mark.parametrize("event", _events.__func__())
    def test_dispatches_through_the_preview(self, event: str):
        from django.test import RequestFactory

        from djust.theming.gallery.live_views import ComponentsDetailView

        view = ComponentsDetailView()
        view.mount(RequestFactory().get("/"), component_name="rating")
        getattr(view.preview, event)(value="4")
        assert isinstance(view.preview.state.values, dict)

    def test_a_demo_value_seeds_from_the_first_example_and_moves(self):
        from django.test import RequestFactory

        from djust.theming.gallery.live_views import ComponentsDetailView

        view = ComponentsDetailView()
        view.mount(RequestFactory().get("/"), component_name="rating")
        before = view._render_examples()[0]["html"]
        view.preview.set_rating(value=2)  # the example starts at 4; typed wire (ADR-033)
        assert view.preview.state.values["value"] == 2
        assert view._render_examples()[0]["html"] != before

    def test_the_get_renders_the_examples_once(self):
        """#2921 review 🟡3: the static context used to render every example,
        and the preview rendered them again."""
        from django.test import RequestFactory

        from djust.theming.gallery import live_views
        from djust.theming.gallery.live_views import ComponentsDetailView

        live_views._PREVIEW_RENDER_CACHE.clear()
        calls: list = []
        real = live_views._render_preview_examples

        def counting(*args, **kwargs):
            # (name, how many examples) — the playground renders ONE example
            # under its own key; the examples list must render once.
            calls.append((args[0], len(args[2])))
            return real(*args, **kwargs)

        live_views._render_preview_examples = counting
        try:
            view = ComponentsDetailView()
            view.mount(RequestFactory().get("/"), component_name="switch")
            assert not view._base_ctx.get("python_examples_html")
            from django.test import override_settings

            with override_settings(ROOT_URLCONF="djust.tests.urls_theming"):
                ctx = view.get_context_data()
            assert ctx["styles"], "styles are derived from the preview's render"
            str(view.preview)
        finally:
            live_views._render_preview_examples = real
        assert calls.count(("switch", 2)) == 1, calls


class TestPreviewTagNeedsNoDjangoTemplatesBackend:
    """A `djust new` project configures only `DjustTemplateBackend`; the
    preview tag's markup must compile without a `DjangoTemplates` engine
    (a module-level `django.template.Template(...)` broke the import of every
    theme tag there — found by serving the gallery from such a project)."""

    def test_component_preview_renders_with_only_the_djust_backend(self):
        from django.test import override_settings

        from djust.theming.templatetags.theme_tags import component_preview

        with override_settings(
            TEMPLATES=[{"BACKEND": "djust.template_backend.DjustTemplateBackend"}]
        ):
            html = component_preview("badge", "python", [{"text": "New"}], {})
        assert "dc-preview" in html and "New" in html
