"""A component built during a render gets the same default id on every render (#4114).

``LiveComponent._generate_id`` drew ``<class>_<random hex>``. A view that builds a
component inside ``get_context_data`` (the demo project's navbar) therefore rendered a
different ``id="..."`` on every request, the page-POST fallback's render digest never
matched, and the answer was always the whole page (#4108). A component built during a
render of a view now derives its id from the view, its own class and its position in
that render; one built anywhere else keeps the random id.
"""

from __future__ import annotations

import json
import re

import pytest
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler
from djust.components.layout.navbar import NavbarComponent
from djust.tests.http_fallback_matrix import PAGE, meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]


class WithNavbars(LiveView):
    """Builds two navbars on every render, as the demo's base view builds one."""

    exposure_policy = "legacy"
    template = PAGE % "{{ first.render }}{{ second.render }}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["first"] = NavbarComponent(brand_name="one", items=[])
        context["second"] = NavbarComponent(brand_name="two", items=[])
        return context


class CountsWithNavbar(LiveView):
    """The demo's shape: a navbar built in ``get_context_data``, and a handler."""

    exposure_policy = "legacy"
    template = PAGE % "{{ nav.render }}<p>Value: <b>{{ count }}</b></p>"
    count = 0

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nav"] = NavbarComponent(brand_name="n", items=[])
        context["count"] = self.count
        return context

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class BuiltInMount(LiveView):
    exposure_policy = "legacy"
    template = PAGE % "{{ nav.render }}"

    def mount(self, request, **kwargs):
        self.nav = NavbarComponent(brand_name="m", items=[])


urlpatterns = [
    path("n/navbars/", WithNavbars.as_view()),
    path("n/mounted/", BuiltInMount.as_view()),
    path("n/counts/", CountsWithNavbar.as_view()),
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


def _navbar_ids(html: str) -> list[str]:
    return re.findall(r'<nav [^>]*id="(navbarcomponent_[0-9a-f]{8})"', html)


def _get(url: str) -> str:
    response = Client().get(url)
    assert response.status_code == 200
    return response.content.decode()


def test_a_navbar_built_in_get_context_data_keeps_its_id_across_requests():
    first, second = _get("/n/navbars/"), _get("/n/navbars/")
    ids = _navbar_ids(first)
    assert len(ids) == 2, first
    assert _navbar_ids(second) == ids
    # Two of one class in one render are told apart, and the ids keep the old shape.
    assert ids[0] != ids[1]
    # So the page the client holds is the page a later render reproduces: the token is
    # ``<nonce>.<digest of the render>``, and the digest half is what the POST compares.
    digest = lambda page: meta_digest(page).split(".")[1]  # noqa: E731
    assert digest(first) == digest(second)


def test_components_built_outside_a_render_keep_random_ids():
    a, b = NavbarComponent(items=[]), NavbarComponent(items=[])
    assert a.component_id != b.component_id
    assert re.fullmatch(r"navbarcomponent_[0-9a-f]{8}", a.component_id)


def test_a_component_built_in_mount_is_not_numbered_as_part_of_a_render():
    first, second = _get("/n/mounted/"), _get("/n/mounted/")
    assert len(_navbar_ids(first)) == 1
    assert _navbar_ids(first) != _navbar_ids(second)  # each request mounts a new one


def test_an_explicit_component_id_is_used_as_given():
    class Explicit(LiveView):
        exposure_policy = "legacy"
        template = PAGE % "{{ nav.render }}"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["nav"] = NavbarComponent(items=[], component_id="mine")
            return context

    view = Explicit()
    view.render_full_template()
    assert 'id="mine"' in view.render_full_template()


def test_the_fallback_answers_with_patches_for_a_page_that_builds_a_navbar_per_render():
    """The point of it (#4108/#4114): the fresh render before the handler is the page the
    client holds, so the answer is patches, not the whole page."""
    client = Client()
    page = client.get("/n/counts/")
    assert page.status_code == 200
    digest = meta_digest(page.content.decode())
    assert digest
    response = post_event(client, "/n/counts/", "increment", {}, digest)
    assert response.status_code == 200
    answer = json.loads(response.content)
    assert answer.get("patches"), answer
    assert "html" not in answer
