"""``{% dj_flash %}`` and other ``dj-update="ignore"`` regions keep the patch answer (#4283).

Since #4108 a page-POST fallback event is answered with patches when the client
proves which render it shows. A render that held the TEXT ``dj-update`` anywhere
was answered with the whole page, and ``{% dj_flash %}``'s container carries
``dj-update="ignore"``: every fallback event of a page with flash messages got
the whole page.

An ignored element's children are the client's (flash messages it adds), so the
shape on both sides leaves them out, and a patch elsewhere addresses the same
node in both trees. A patch at or under the element still selects the whole
page, and so do the regions a path cannot follow (``append``, ``prepend``,
``replace``, ``dj-virtual``), now found by attribute instead of by text.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state
from djust.tests.http_fallback_matrix import PAGE, meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]

BODY = '<button dj-click="toggle">Toggle</button>{% if open %}<p>Details</p>{% endif %}'


def _page(name: str, template: str, doc: str = "") -> type:
    """A toggling page with ``template`` (an explicit view declares its own fields)."""

    class Page(LiveView):
        exposure_policy = "explicit"
        open = state(False, persist="server")

        @event_handler
        def toggle(self, **kwargs):
            self.open = not self.open

    Page.template = template
    Page.__name__ = Page.__qualname__ = name
    Page.__doc__ = doc or None
    return Page


Plain = _page("Plain", PAGE % BODY)
WithFlash = _page(
    "WithFlash",
    "{% load djust_flash %}" + PAGE % (BODY + "{% dj_flash %}"),
    "The issue's page: the flash container after the toggled paragraph.",
)
ShowsTheAttributeAsText = _page(
    "ShowsTheAttributeAsText",
    PAGE
    % (
        BODY + "<pre><code>&lt;ul dj-update=&quot;append&quot;&gt;</code></pre>"
        "<p>dj-update and dj-virtual, in prose</p>"
    ),
    "Documentation of ``dj-update`` in the page's content, escaped as text.",
)
StatefulIgnored = _page(
    "StatefulIgnored",
    PAGE
    % (
        BODY + '<div id="owned" dj-update="ignore" data-state="{{ open }}">'
        "<span>{{ open }}</span></div>"
    ),
    "An ignored region whose own attribute depends on state. (The differ emits"
    " nothing under it: it splices the previous render's children in.)",
)
Virtual = _page(
    "Virtual",
    PAGE % (BODY + '<ul dj-virtual dj-virtual-item-height="20"><li>a</li></ul>'),
)


urlpatterns = [
    path("f/plain/", Plain.as_view()),
    path("f/flash/", WithFlash.as_view()),
    path("f/text/", ShowsTheAttributeAsText.as_view()),
    path("f/owned/", StatefulIgnored.as_view()),
    path("f/virtual/", Virtual.as_view()),
]


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
    ):
        yield


def _toggle(url: str) -> dict:
    client = Client()
    response = client.get(url)
    assert response.status_code == 200
    digest = meta_digest(response.content.decode())
    assert digest
    answer = post_event(client, url, "toggle", {}, digest)
    assert answer.status_code == 200, answer.content[:300]
    return json.loads(answer.content)


def test_a_page_without_flash_answers_with_patches():
    assert "patches" in _toggle("/f/plain/")


def test_a_page_with_dj_flash_answers_with_patches():
    answer = _toggle("/f/flash/")
    assert "html" not in answer, sorted(answer)
    assert answer["patches"], answer
    assert all(patch["path"][:1] != [2] for patch in answer["patches"]), answer


def test_the_text_dj_update_in_page_content_does_not_turn_patches_off():
    answer = _toggle("/f/text/")
    assert "patches" in answer, sorted(answer)


def test_a_patch_at_an_ignored_region_selects_the_whole_page():
    answer = _toggle("/f/owned/")
    assert "patches" not in answer and answer["version"] == 1
    assert ">Details</p>" in answer["html"]


def test_a_virtual_list_still_selects_the_whole_page():
    answer = _toggle("/f/virtual/")
    assert "patches" not in answer and answer["version"] == 1


def test_patches_touch_opaque_matches_the_path_and_everything_under_it():
    from djust._http_fallback_baseline import OPAQUE_PATHS_ATTR, patches_touch_opaque

    class View:
        pass

    view = View()
    assert not patches_touch_opaque(view, [{"type": "SetText", "path": [2, 0]}])
    view.__dict__[OPAQUE_PATHS_ATTR] = ((2,),)
    assert patches_touch_opaque(view, [{"type": "SetAttr", "path": [2]}])
    assert patches_touch_opaque(view, [{"type": "SetText", "path": [2, 0, 1]}])
    assert not patches_touch_opaque(view, [{"type": "SetText", "path": [1, 0]}])
    # The parent's child list: an insert next to the region does not enter it.
    assert not patches_touch_opaque(view, [{"type": "InsertChild", "path": [], "index": 2}])
    assert patches_touch_opaque(view, [{"type": "Unknown"}])
    # A Replace of an ancestor sends the server's children of the region.
    assert patches_touch_opaque(view, [{"type": "Replace", "path": []}])
    assert not patches_touch_opaque(view, [{"type": "Replace", "path": [1]}])
    assert not patches_touch_opaque(view, [{"type": "SetAttr", "path": []}])


def test_the_render_after_the_handler_supplies_the_regions_path_too():
    """The client resolves a path against the DOM the earlier patches left: an
    insert before the region moves it, so its path after the handler counts."""
    from djust._http_fallback_baseline import OPAQUE_PATHS_ATTR, patches_touch_opaque

    class View:
        pass

    view = View()
    view.__dict__[OPAQUE_PATHS_ATTR] = ((1,),)
    after = (
        '<div dj-root=""><p>new</p><button>b</button><div id="w" dj-update="ignore"></div></div>'
    )
    patches = [{"type": "SetAttr", "path": [2], "key": "data-x", "value": "1"}]
    assert not patches_touch_opaque(view, patches)
    assert patches_touch_opaque(view, patches, after)
    assert patches_touch_opaque(view, patches, "<div><p></div>")  # unreadable: whole page
