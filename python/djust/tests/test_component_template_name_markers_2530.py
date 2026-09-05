"""#2530 — a ``LiveComponent`` with ``template_name`` renders through the
LiveView engine entry, so its ``{% if %}`` blocks carry ``<!--dj-if id=…-->``
boundary markers inside the parent's VDOM.

Before: ``render()``'s ``template_name`` branch went through Django's
``render_to_string`` → the project ``TEMPLATES`` backend, which (post-#2519,
and always on a ``DjangoTemplates`` project) emits no markers, so a component
``{% if %}`` toggle fell back to positional matching in the differ.

Reproduced through the real path: the parent LiveView's own render
(``LiveViewTestClient.render()`` → ``_sync_state_to_rust`` → the component's
``render()`` via ``normalize_django_value``), over a real template file.
"""

from __future__ import annotations

import re

import pytest
from django.test import override_settings

from djust import LiveView
from djust.utils import clear_template_dirs_cache
from djust.components.base import LiveComponent
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient

_MARKER = re.compile(r'<!--dj-if id="(if-[0-9a-f]+-\d+)"-->(.*?)<!--/dj-if-->', re.S)


class Card(LiveComponent):
    template_name = "comp_2530/card.html"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.show = False

    def get_context_data(self):
        return {"show": self.show}


class Parent(LiveView):
    template = "<div dj-root>{{ card }}</div>"

    def mount(self, request, **kwargs):
        self.card = Card()

    @event_handler()
    def toggle(self, **kwargs):
        self.card.show = not self.card.show
        self.set_changed_keys("card")


@pytest.fixture
def templates(tmp_path):
    d = tmp_path / "templates" / "comp_2530"
    d.mkdir(parents=True)
    (d / "card.html").write_text('<p class="card">{% if show %}<b>on</b>{% endif %}</p>')
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ]
    ):
        # `get_template_dirs()` is lru-cached (#1801): drop the cache on both
        # sides so this DIRS override neither sees nor leaves a stale value.
        clear_template_dirs_cache()
        try:
            yield
        finally:
            clear_template_dirs_cache()


@pytest.mark.django_db
class TestTemplateNameComponentCarriesMarkers:
    def test_the_component_if_block_has_a_boundary_in_the_parent_render(self, templates):
        client = LiveViewTestClient(Parent)
        client.mount()
        html = client.render()
        found = _MARKER.findall(html)
        assert found, "no dj-if boundary inside the template_name component: " + html
        marker_id, body = found[0]
        assert body == "", (marker_id, body)

    def test_a_toggle_keeps_the_same_boundary_identity(self, templates):
        client = LiveViewTestClient(Parent)
        client.mount()
        before = _MARKER.findall(client.render())
        client.send_event("toggle")
        after = _MARKER.findall(client.render())
        assert before and after, (before, after)
        assert before[0][0] == after[0][0], "boundary identity changed across the toggle"
        assert "<b>on</b>" in after[0][1], after

    def test_the_parent_dj_root_is_unaffected(self, templates):
        # Non-vacuity: the marker lives INSIDE the component's <p>, not on the
        # parent's own template (which has no {% if %}).
        client = LiveViewTestClient(Parent)
        client.mount()
        html = client.render()
        assert re.search(r'<p class="card">\s*<!--dj-if id="if-', html), html
