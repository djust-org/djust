"""The interactive DropdownMenu has a catalogue entry that is its tested example."""

import re
from html import unescape

import pytest
import tests.conftest  # noqa: F401
from django.test import Client, override_settings

ENTRY = "interactive_dropdown_menu"


def test_the_entry_is_listed_as_interactive():
    from djust.theming.gallery.component_registry import get_all_components_with_metadata

    (entry,) = [c for c in get_all_components_with_metadata() if c["name"] == ENTRY]
    assert (entry["component_type"], entry["category"]) == ("interactive", "Core UI")


@pytest.mark.django_db
@override_settings(
    DEBUG=True, DJUST_THEMING_GALLERY_PUBLIC=True, ROOT_URLCONF="tests.gallery_test_urls"
)
def test_the_page_renders_the_live_example_and_its_source():
    from django.urls import reverse

    url = reverse("djust_theming:components_detail", args=[ENTRY])
    response = Client().get(url)
    assert response.status_code == 200, response.content[:500]
    html = response.content.decode()
    assert html.count("data-component-id=") == 3  # project menu + two keyed rows
    # The usage section shows the source; highlighting wraps each token in <span>.
    text = unescape(re.sub(r"<[^>]+>", "", html))
    assert "row_menus.sync" in text and "@project_menu.on.selected" in text


def test_the_detail_context_describes_the_entry():
    from djust.theming.gallery.catalogue import build_catalogue_detail_context

    ctx = build_catalogue_detail_context(ENTRY)
    assert ctx["component_type"] == "interactive"
    assert "class DropdownMenuExample(LiveView)" in ctx["usage_parts"]["view"]
    assert "{{ project_menu }}" in ctx["usage_parts"]["template"]


def test_the_index_card_thumbnail_is_markup_not_a_second_live_page():
    # Review of #3134: the card embedded the example's whole GET response,
    # adding a nested dj-root, its scripts and a contracts block that the
    # client read as the index page's own.
    from djust.theming.templatetags.theme_tags import _thumbnail_html

    _thumbnail_html.cache_clear()
    thumb = _thumbnail_html(ENTRY)
    assert "Project" in thumb and "Alpha" in thumb  # it still shows the menus
    for leak in (
        "<script",
        "dj-view",
        "dj-root",
        "data-djust-parameter-contracts",
        "data-component-id",
    ):
        assert leak not in thumb, leak


@pytest.mark.django_db
@override_settings(
    DEBUG=True, DJUST_THEMING_GALLERY_PUBLIC=True, ROOT_URLCONF="tests.gallery_test_urls"
)
def test_the_index_page_carries_only_its_own_view():
    from django.urls import reverse

    from djust.theming.templatetags.theme_tags import _thumbnail_html

    _thumbnail_html.cache_clear()
    html = Client().get(reverse("djust_theming:components")).content.decode()
    assert "interactive_examples.DropdownMenuExample" not in html
    assert html.count("data-djust-parameter-contracts") <= 1


def test_describe_component_agrees_with_the_detail_context():
    # Review of #3134 (#1646): djust-docs builds reference pages from
    # describe_component, which described the entry as empty.
    from djust.theming.gallery.catalogue import build_catalogue_detail_context
    from djust.theming.gallery.component_registry import describe_component

    described = describe_component(ENTRY)
    assert described["events"] == build_catalogue_detail_context(ENTRY)["events"] == ["selected"]
    namespace: dict = {}
    exec(described["import_line"], namespace)
    assert namespace[described["class_name"]].__name__ == "DropdownMenu"
    assert {"label", "items"} <= {p["name"] for p in described["params"]}
    assert described["description"]


@pytest.mark.parametrize(
    "end_tag", ["</script>", "</script >", "</SCRIPT>", '</script data-x="1">']
)
def test_the_preview_fragment_drops_scripts_whatever_their_end_tag(end_tag):
    # CodeQL (py/bad-tag-filter) on #3134: `</script>` alone missed `</script >`.
    from djust.theming.gallery.catalogue import _preview_fragment

    page = '<div dj-root dj-view="x.V"><p>menu</p><script>alert(1)%s</div>' % end_tag
    assert _preview_fragment(page) == "<p>menu</p>"
