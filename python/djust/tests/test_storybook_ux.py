"""The storybook's docs-first navigation and preview chrome.

Descriptions and thumbnails on the cards, the playground over the preview
component (ADR-032: a chip is a scoped patch), previous/next in sidebar
order, and the session-backed "Recently viewed" group.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from djust.theming.gallery.storybook import component_description, playground_options

pytestmark = pytest.mark.django_db

_BASE = override_settings(
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
        from djust.theming.gallery.storybook import build_storybook_detail_context

        examples = build_storybook_detail_context("button", render_examples=False)["examples"]
        assert [o["key"] for o in playground_options(examples)] == ["variant", "size"]


class TestDescriptions:
    def test_python_component_docstring_first_line(self):
        assert component_description("accordion").startswith("Accordion")

    def test_a_component_without_a_python_class_has_no_prose_yet(self):
        """A template-only component has no docstring to read — "" is the
        honest answer, not a generated sentence."""
        assert component_description("no_such_component") == ""
        assert isinstance(component_description("button"), str)

    def test_index_cards_carry_it(self):
        from djust.theming.gallery.storybook import build_storybook_index_context

        by_name = {c["name"]: c for c in build_storybook_index_context()["components"]}
        assert by_name["accordion"]["description"]
        assert "description" in by_name["button"]


class TestThumbnails:
    def test_template_component_thumbnail_is_its_first_example(self):
        from djust.theming.templatetags.theme_tags import storybook_thumbnail

        html = storybook_thumbnail("button")
        assert "Primary" in html and "btn" in html

    def test_python_component_has_none(self):
        from djust.theming.templatetags.theme_tags import storybook_thumbnail

        assert storybook_thumbnail("accordion") == ""


# ---------------------------------------------------------------------------
# The detail view: playground state, neighbours, recents
# ---------------------------------------------------------------------------


def _detail(component_name: str, session: dict | None = None):
    from django.test import RequestFactory

    from djust.theming.gallery.live_views import StorybookDetailView

    request = RequestFactory().get(f"/theme/gallery/storybook/{component_name}/")
    if session is not None:
        request.session = session  # type: ignore[attr-defined]
    view = StorybookDetailView()
    view.request = request
    view.mount(request, component_name=component_name)
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
        assert 'id="sb-playground"' in html
        playground = html.split('id="sb-playground"')[1].split("</section>")[0]
        assert "btn-ghost" in playground
        assert "variant=&#x27;ghost&#x27;" in playground  # the (escaped) call under the playground

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
        assert session["djust_storybook_recent"] == ["button"]
        assert session.modified

    def test_the_sidebar_lists_the_others_most_recent_first(self):
        session = _Session()
        _detail("button", session)
        _detail("alert", session)
        view = _detail("badge", session)
        assert session["djust_storybook_recent"] == ["badge", "alert", "button"]
        assert [c["name"] for c in view.recent_components] == ["alert", "button"]

    def test_capped_at_five_and_deduplicated(self):
        session = _Session()
        for name in ["button", "alert", "badge", "avatar", "card", "kbd", "button"]:
            _detail(name, session)
        assert session["djust_storybook_recent"] == ["button", "kbd", "card", "avatar", "badge"]

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
            "view": "djust.theming.gallery.live_views.StorybookDetailView",
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


@_BASE
def test_the_pages_render_over_http(client):
    for url in (
        "/theme/gallery/storybook/",
        "/theme/gallery/storybook/button/",
        "/theme/gallery/storybook/accordion/",
    ):
        response = client.get(url)
        assert response.status_code == 200, url
        body = response.content.decode()
        assert "storybook.css" in body and "storybook.js" in body
    body = client.get("/theme/gallery/storybook/button/").content.decode()
    assert 'id="sb-playground"' in body and 'class="sb-rail"' in body and "sb-pager" in body
    assert 'Props <span class="sb-badge sb-badge-count">8</span>' in body
