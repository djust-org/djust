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
