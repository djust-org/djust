"""The catalogue's docs-first navigation and preview chrome.

Descriptions and thumbnails on the cards, the playground over the preview
component (ADR-032: a chip is a scoped patch), previous/next in sidebar
order, and the session-backed "Recently viewed" group.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from djust.theming.gallery.catalogue import (
    component_description,
    component_preview_note,
    playground_options,
)

pytestmark = pytest.mark.django_db

_SETTINGS = dict(
    LIVEVIEW_ALLOWED_MODULES=None,
    ROOT_URLCONF="djust.tests.urls_theming",
    DJUST_THEMING_GALLERY_PUBLIC=True,
)


# ---------------------------------------------------------------------------
# Playground options — derived from the examples
# ---------------------------------------------------------------------------


class TestPlaygroundOptions:
    def test_token_kwargs_with_two_or_more_values_become_options(self):
        examples = [
            {"variant": "primary", "size": "md", "text": "Primary"},
            {"variant": "ghost", "size": "md", "text": "Ghost"},
            {"variant": "link", "size": "lg", "text": "A link"},
        ]
        assert playground_options(examples) == [
            {"key": "variant", "values": ["primary", "ghost", "link"]},
            {"key": "size", "values": ["md", "lg"]},
        ]

    def test_labels_and_single_values_are_not_options(self):
        """`text` varies too, but its values are prose, not tokens; a kwarg
        with one value has nothing to flip."""
        keys = [o["key"] for o in playground_options([{"variant": "a", "text": "Hello"}])]
        assert keys == []
        keys = [
            o["key"]
            for o in playground_options([{"text": "Save"}, {"text": "Cancel"}, {"text": "Ok"}])
        ]
        assert keys == []

    def test_bool_kwargs_toggle(self):
        assert playground_options([{"dismissible": True}]) == [
            {"key": "dismissible", "values": [False, True]}
        ]

    def test_slots_and_structures_are_ignored(self):
        assert (
            playground_options([{"slot_icon": "<i>", "items": [{"a": 1}]}, {"slot_icon": "<b>"}])
            == []
        )

    def test_the_shipped_button_exposes_variant_and_size(self):
        from djust.theming.gallery.catalogue import build_catalogue_detail_context

        examples = build_catalogue_detail_context("button", render_examples=False)["examples"]
        assert [o["key"] for o in playground_options(examples)] == ["variant", "size"]


class TestDescriptions:
    def test_python_component_docstring_first_line(self):
        assert component_description("accordion").startswith("Accordion")

    def test_unknown_component_has_no_prose_but_template_only_entries_do(self):
        """Known template-only entries have explicit prose; unknown names do
        not get a made-up description."""
        assert component_description("no_such_component") == ""
        assert component_description("checkbox")
        assert component_description("nav_item")
        assert component_description("server_event_toast")
        assert isinstance(component_description("button"), str)

    def test_index_cards_carry_it(self):
        from djust.theming.gallery.catalogue import build_catalogue_index_context

        by_name = {c["name"]: c for c in build_catalogue_index_context()["components"]}
        assert by_name["accordion"]["description"]
        assert "description" in by_name["button"]
        assert all(c["description"] for c in by_name.values())

    def test_components_without_standalone_previews_explain_the_gap(self):
        assert component_preview_note("server_event_toast").startswith(
            "This is a server-event helper"
        )
        assert component_preview_note("button") == ""


class TestThumbnails:
    def test_template_component_thumbnail_is_its_first_example(self):
        from djust.theming.templatetags.theme_tags import component_thumbnail

        html = component_thumbnail("button")
        assert "Primary" in html and "btn" in html

    def test_python_component_has_one_too(self):
        """This asserted `== ""`, from when the registry carried examples for
        the contracted components only. It carries them for the python ones
        now, and a card that shows the component is the point of the page."""
        from djust.theming.templatetags.theme_tags import component_thumbnail

        assert "dj-accordion" in component_thumbnail("accordion")

    def test_a_component_that_opens_on_demand_stays_blank(self):
        """A preview of a closed modal is a preview of nothing."""
        from djust.theming.templatetags.theme_tags import component_thumbnail

        assert component_thumbnail("tour") == ""


# ---------------------------------------------------------------------------
# The detail view: playground state, neighbours, recents
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _urlconf():
    """The views reverse `djust_theming:*` URLs (breadcrumb, sidebar, pager)."""
    with override_settings(**_SETTINGS):
        yield


def _detail(component_name: str, session: dict | None = None):
    from django.test import RequestFactory

    from djust.theming.gallery.live_views import ComponentsDetailView

    request = RequestFactory().get(f"/theme/components/{component_name}/")
    if session is not None:
        request.session = session  # type: ignore[attr-defined]
    view = ComponentsDetailView()
    view.request = request
    view.mount(request, component_name=component_name)
    return view


def _index():
    from django.test import RequestFactory

    from djust.theming.gallery.live_views import ComponentsIndexView

    request = RequestFactory().get("/theme/components/")
    view = ComponentsIndexView()
    view.request = request
    view.mount(request)
    return view


class _Session(dict):
    """The parts of a Django session the sidebar reads and writes."""

    modified = False

    def __setitem__(self, key, value):
        self.modified = True
        super().__setitem__(key, value)


class TestPlaygroundState:
    def test_a_chip_sets_one_override_and_re_renders_the_playground(self):
        view = _detail("button")
        view.preview.set_option(value="variant:ghost")
        assert view.preview.state.playground == {"variant": "ghost"}
        html = str(view.preview)
        assert 'id="dc-preview"' in html
        playground = html.split('id="dc-preview"')[1].split("</section>")[0]
        assert "btn-ghost" in playground
        assert (
            "ghost" in playground and 'class="hl-' in playground
        )  # the highlighted call under the playground

    def test_bools_are_parsed(self):
        view = _detail("alert")
        view.preview.set_option(value="dismissible:true")
        assert view.preview.state.playground == {"dismissible": True}

    def test_the_examples_are_untouched_by_the_playground(self):
        view = _detail("button")
        view.preview.set_option(value="variant:ghost")
        rendered = view._render_examples()
        assert "btn-primary" in rendered[0]["html"]

    def test_malformed_values_are_ignored(self):
        view = _detail("button")
        view.preview.set_option(value="novalue")
        view.preview.set_option(value=":x")
        assert view.preview.state.playground == {}


class TestNeighbours:
    def test_prev_and_next_follow_sidebar_order(self):
        view = _detail("alert")
        order = [c["name"] for c in view._all_components]
        i = order.index("alert")
        prev_c, next_c = view._neighbours()
        assert prev_c["name"] == order[i - 1] and next_c["name"] == order[i + 1]

    def test_the_first_component_has_no_previous(self):
        first = _detail("alert")._all_components[0]["name"]
        prev_c, next_c = _detail(first)._neighbours()
        assert prev_c is None and next_c is not None

    def test_the_context_carries_them(self):
        ctx = _detail("alert").get_context_data()
        assert ctx["prev_component"]["name"] and ctx["next_component"]["name"]


class TestRecentlyViewed:
    def test_a_get_records_the_visit_in_the_session(self):
        session = _Session()
        _detail("button", session)
        assert session["djust_components_recent"] == ["button"]
        assert session.modified

    def test_the_sidebar_lists_the_others_most_recent_first(self):
        session = _Session()
        _detail("button", session)
        _detail("alert", session)
        view = _detail("badge", session)
        assert session["djust_components_recent"] == ["badge", "alert", "button"]
        assert [c["name"] for c in view.recent_components] == ["alert", "button"]

    def test_capped_at_five_and_deduplicated(self):
        session = _Session()
        for name in ["button", "alert", "badge", "avatar", "card", "kbd", "button"]:
            _detail(name, session)
        assert session["djust_components_recent"] == ["button", "kbd", "card", "avatar", "badge"]

    def test_without_a_session_nothing_breaks(self):
        view = _detail("button")
        assert view.recent_components == []


# ---------------------------------------------------------------------------
# Over the wire: a chip is a scoped patch
# ---------------------------------------------------------------------------


async def _mount(component_name: str):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await communicator.connect()
    assert connected
    try:
        await communicator.receive_json_from(timeout=3)
    except Exception:  # noqa: BLE001
        pass
    await communicator.send_json_to(
        {
            "type": "mount",
            "view": "djust.theming.gallery.live_views.ComponentsDetailView",
            "params": {"component_name": component_name},
        }
    )
    return communicator, await communicator.receive_json_from(timeout=5)


@override_settings(
    LIVEVIEW_ALLOWED_MODULES=None,
    ROOT_URLCONF="djust.tests.urls_theming",
    DJUST_EXPOSE_TIMING=True,
)
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_playground_chip_patches_only_the_preview():
    communicator, mounted = await _mount("button")
    try:
        assert 'data-value="variant:ghost"' in mounted.get("html", "")
        await communicator.send_json_to(
            {
                "type": "event",
                "event": "set_option",
                "params": {"value": "variant:ghost", "component_id": "preview"},
                "ref": 1,
            }
        )
        updated = await communicator.receive_json_from(timeout=5)
    finally:
        await communicator.disconnect()
    assert updated.get("type") == "patch", updated
    assert updated.get("timing", {}).get("scope") == "component", updated.get("timing")
    assert "btn-ghost" in str(updated["patches"])


def test_the_pages_render_over_http(client):
    for url in (
        "/theme/components/",
        "/theme/components/button/",
        "/theme/components/accordion/",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        body = response.content.decode()
        assert "catalogue.css" in body and "catalogue.js" in body
    body = client.get("/theme/components/button/").content.decode()
    assert 'id="dc-preview"' in body and 'class="dc-rail"' in body and "dc-pager" in body
    assert 'class="toc-item' in body and "toggle-group-btn" in body and "dj-code-snippet" in body


# ---------------------------------------------------------------------------
# Usage: events, the class-level form, no |safe; highlighted code
# ---------------------------------------------------------------------------


class TestDemoEventsTakeTypedValues:
    def test_set_rating_with_the_typed_wire_value_renders(self):
        """The star emits `dj-value-value:int="4"` (ADR-033 D4), so the click
        arrives as 4 and the preview re-renders with it — no coercion."""
        view = _detail("rating")
        view.preview.set_rating(value=4)
        assert view.preview.state.values["value"] == 4
        html = str(view.preview)
        assert "failed to render" not in html and "rating-star" in html
        assert 'dj-value-value:int="4"' in html


class TestUsageWithEvents:
    def test_a_descriptor_backed_component_shows_the_plain_form(self):
        """#2926 review 🔴1: the preview renders the plain ``Accordion``; the
        class-level descriptor of the same name is state-only and rendered
        its state dict as text. The snippet is the plain form with the
        handler the descriptor would have run."""
        snippet = _detail("accordion")._base_ctx["usage_snippet"]
        assert "self.component = Accordion(items=" in snippet
        assert "def accordion_toggle(self, value, **kwargs):" in snippet
        assert 'self.component.active = "" if self.component.active == value else value' in snippet
        assert "component = Accordion()" not in snippet
        assert "{{ component }}" in snippet and "|safe" not in snippet

    def test_every_python_usage_snippet_compiles(self):
        """The served ``views.py`` must be Python (a removed ``def mount(``
        line once left its body behind: IndentationError)."""
        from djust.theming.gallery.component_registry import PYTHON_COMPONENT_EXAMPLES

        for name in sorted(PYTHON_COMPONENT_EXAMPLES):
            try:
                view = _detail(name)
            except Exception:  # noqa: BLE001 — a registry example that does not build
                continue
            src = view._base_ctx["usage_parts"]["view"]
            compile(src, f"{name}.views.py", "exec")

    def test_set_option_only_accepts_offered_keys_and_values(self):
        """#2926 review 🟡3: the chip wire is client-controlled."""
        view = _detail("alert")
        before = dict(view.preview.state.playground)
        view.preview.set_option(value="slot_icon:<img onerror=x>")
        view.preview.set_option(value="dismissible:maybe")
        view.preview.set_option(value="nope:1")
        assert dict(view.preview.state.playground) == before
        view.preview.set_option(value="dismissible:true")
        assert view.preview.state.playground["dismissible"] is True

    def test_the_playground_code_is_highlighted_and_copyable(self):
        html = str(_detail("alert").preview)
        assert 'class="hl-' in html and "dj-copy=" in html

    def test_the_index_state_holds_names_not_component_dicts(self):
        """#2926 review 🟡5: ~380 KB of state per event before."""
        view = _index()
        state = view.get_state()
        assert "visible_components" not in state and "components_by_category" not in state
        assert isinstance(state["visible_names"][0], str)
        assert view.get_context_data()["visible_components"][0]["name"] == state["visible_names"][0]

    def test_a_demo_event_gets_a_handler_stub_with_the_kwarg_it_drives(self):
        view = _detail("rating")
        snippet = view._base_ctx["usage_snippet"]
        assert view._base_ctx["events"] == ["set_rating"]
        assert "from djust.decorators import event_handler" in snippet
        assert "@event_handler()" in snippet
        assert "def set_rating(self, value, **kwargs):" in snippet
        # ADR-033: the component is held in mount() and the handler writes
        # to it; the value arrives typed, so no int(), no rebuild.
        assert "        self.component = Rating(value=4, max_stars=5)" in snippet
        assert "        self.component.value = value" in snippet
        assert "int(value)" not in snippet and "get_context_data" not in snippet

    def test_a_component_without_events_is_unchanged(self):
        view = _detail("button")
        assert view._base_ctx["events"] == []
        assert "@event_handler" not in view._base_ctx["usage_snippet"]

    def test_the_component_renders_without_safe(self):
        """`render()` marks the HTML safe and the LiveView path keeps the
        mark, so the snippet's `{{ component }}` is the whole story."""
        from djust import LiveView
        from djust.components import Rating

        class V(LiveView):
            template = "<div dj-root>{{ component }}</div>"

            def mount(self, request: object, **kwargs: object) -> None:
                self.component = Rating(value=4, max_stars=5)

        v = V()
        v.mount(None)
        html = v.render_with_diff()[0]
        assert '<div class="rating"' in html and "&lt;div" not in html

    def test_usage_is_split_and_highlighted(self):
        ctx = _detail("rating")._base_ctx
        assert ctx["usage_parts"]["view"].startswith("from djust import LiveView")
        assert ctx["usage_parts"]["template"] == "{{ component }}"
        assert 'class="hl-k"' in ctx["usage_html"]["view"]  # `class`, `def`, `import`
        assert "dj-copy=" in ctx["usage_html"]["view"]


class TestCodeHighlighting:
    def test_code_snippet_highlights_known_languages(self):
        from djust.components.components.code_snippet import CodeSnippet, highlight_code

        out = highlight_code("def f():\n    return '<b>'", "python")
        assert 'class="hl-k"' in out and "&lt;b&gt;" in out
        html = CodeSnippet(code="x = 1", language="python").render()
        assert 'class="hl-' in html and "dj-copy=" in html

    def test_whitespace_between_tokens_survives_the_pipeline(self):
        """The VDOM pipeline drops a lone space between elements, which turned
        `from djust` into `fromdjust`; the space is folded into the next token."""
        import re

        from djust.components.components.code_snippet import highlight_code

        out = highlight_code("from djust import LiveView", "python")
        assert not re.search(r">[ \t]+<", out), out  # no lone space, bare or spanned
        assert '<span class="hl-nn"> djust</span>' in out, out
        assert re.sub(r"<[^>]+>", "", out) == "from djust import LiveView"  # what dj-copy copies

    def test_whitespace_before_plain_shell_tokens_survives_the_pipeline(self):
        import re

        from djust.components.components.code_snippet import highlight_code

        out = highlight_code("pip install djust", "bash")
        assert re.sub(r"<[^>]+>", "", out) == "pip install djust"
        assert "pipinstall" not in out and "installdjust" not in out

    def test_parameter_types_are_names_not_reprs(self):
        rows = _detail("rating")._base_ctx and _detail("rating").get_context_data()["params_rows"]
        value_row = next(row for row in rows if row[0] == "value")
        assert value_row[:3] == ["value", "float", "0"]
        assert "current rating value" in value_row[3]

    def test_empty_accessibility_is_explicit_for_template_components(self):
        ctx = _detail("button").get_context_data()
        assert "Accessibility" in {item["label"] for item in ctx["toc_items"]}

    def test_unknown_language_and_no_language_stay_plain(self):
        from djust.components.components.code_snippet import highlight_code

        assert highlight_code("<b>", "no-such-lexer") == "&lt;b&gt;"
        assert highlight_code("<b>", "") == "&lt;b&gt;"

    def test_django_alias_resolves(self):
        from djust.components.components.code_snippet import highlight_code

        assert 'class="hl-' in highlight_code("{% load x %}{{ y }}", "django")
