"""A component built during a render gets the same default id on every render (#4114).

``LiveComponent._generate_id`` drew ``<class>_<random hex>``. A view that builds a
component inside ``get_context_data`` (the demo project's navbar) therefore rendered a
different ``id="..."`` on every request, the page-POST fallback's render digest never
matched, and the answer was always the whole page (#4108). A component built during a
render (or ``mount()``) of a view now derives its id from the view, the phase, its own
class and its position in that run; one built anywhere else keeps the random id. Review
fixes: an id never repeats one the view still holds (B1), a mount-built id is the same on
every request (S1), and two children of one class are told apart (S2).
"""

from __future__ import annotations

import contextvars
import json
import re
import threading
import time

import pytest
from django.test import Client, override_settings
from django.urls import path

from djust import LiveView
from djust import _component_ids
from djust._held_rows import reading, recorded_render
from djust.decorators import event_handler
from djust.components.layout.navbar import NavbarComponent
from djust.sync_turn import run_sync_step, sync_step
from djust.tests.http_fallback_matrix import PAGE, meta_digest, post_event

pytestmark = [pytest.mark.django_db(transaction=True)]


class WithNavbars(LiveView):
    """Builds two navbars on every render, as the demo's base view builds one."""

    exposure_policy = "explicit"
    template = PAGE % "{{ first.render }}{{ second.render }}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["first"] = NavbarComponent(brand_name="one", items=[])
        context["second"] = NavbarComponent(brand_name="two", items=[])
        return context


class CountsWithNavbar(LiveView):
    """The demo's shape: a navbar built in ``get_context_data``, and a handler."""

    exposure_policy = "explicit"
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
    exposure_policy = "legacy"  # legacy subject: a component assigned in mount() / on the instance is held by legacy views only (explicit views declare components at class level)
    template = PAGE % "{{ nav.render }}"

    def mount(self, request, **kwargs):
        self.nav = NavbarComponent(brand_name="m", items=[])


class BuiltInMountAndRender(LiveView):
    exposure_policy = "legacy"  # legacy subject: a component assigned in mount() / on the instance is held by legacy views only (explicit views declare components at class level)
    template = PAGE % "{{ nav.render }}{{ other.render }}"

    def mount(self, request, **kwargs):
        self.nav = NavbarComponent(brand_name="m", items=[])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["other"] = NavbarComponent(brand_name="r", items=[])
        return context


class BuiltTwiceInMount(LiveView):
    exposure_policy = "legacy"  # legacy subject: a component assigned in mount() / on the instance is held by legacy views only (explicit views declare components at class level)
    template = PAGE % "{{ first.render }}{{ second.render }}"

    def mount(self, request, **kwargs):
        self.first = NavbarComponent(brand_name="1", items=[])
        self.second = NavbarComponent(brand_name="2", items=[])


class BuiltInHandler(LiveView):
    exposure_policy = "explicit"
    template = PAGE % "x"

    def mount(self, request=None, **kwargs):
        self.built = []

    @event_handler
    def build(self, **kwargs):
        self.__dict__.setdefault("built", []).append(NavbarComponent(items=[]))


class LazyAttrs(LiveView):
    """A component built on a later render and held beside the first (B1)."""

    exposure_policy = "legacy"  # legacy subject: a component assigned on the instance is held by legacy views only (explicit views declare components at class level)
    template = PAGE % "x"
    show_b = False

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "a" not in self.__dict__:
            self.a = NavbarComponent(items=[])
        if self.show_b and "b" not in self.__dict__:
            self.b = NavbarComponent(items=[])
        return ctx


class LazyCache(LiveView):
    exposure_policy = "explicit"
    template = PAGE % "{{ nav0.render }}{% if nav1 %}{{ nav1.render }}{% endif %}"

    def mount(self, request=None, **kwargs):
        self.count = 1

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cache = self.__dict__.setdefault("_navs", {})
        for i in range(self.count):
            if i not in cache:
                cache[i] = NavbarComponent(brand_name=str(i), items=[])
        for i in range(self.count):
            ctx["nav%d" % i] = cache[i]
        return ctx


class Twin(LiveView):
    exposure_policy = "explicit"
    template = PAGE % "{{ nav.render }}"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["nav"] = NavbarComponent(items=[])
        return ctx


urlpatterns = [
    path("n/mount-and-render/", BuiltInMountAndRender.as_view()),
    path("n/mounted-twice/", BuiltTwiceInMount.as_view()),
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


def test_a_component_built_in_mount_has_the_same_id_on_every_request():
    """S1: ``mount()`` runs on every request, so its id is as stable as a render's, and it
    can never equal the id a render builds for a component of the same class."""
    first, second = _get("/n/mounted/"), _get("/n/mounted/")
    assert len(_navbar_ids(first)) == 1
    assert _navbar_ids(first) == _navbar_ids(second)
    both = _get("/n/mount-and-render/")
    mounted, rendered = _navbar_ids(both)
    assert mounted != rendered
    assert _navbar_ids(_get("/n/mount-and-render/")) == [mounted, rendered]


def test_two_components_built_in_one_mount_get_distinct_ids():
    first = _get("/n/mounted-twice/")
    ids = _navbar_ids(first)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert _navbar_ids(_get("/n/mounted-twice/")) == ids


def test_a_component_built_in_a_handler_keeps_a_random_id():
    view = BuiltInHandler()
    view.build()
    run_sync_step(view, "event", view.build)
    run_sync_step(view, "event", view.build)
    assert len({c.component_id for c in view.built}) == len(view.built) == 3


def test_a_mount_through_sync_step_is_numbered_without_a_wrapper():
    """The websocket reaches mount through ``sync_step`` (``call_hook``); with no
    ``sync_turn_wrapper`` it used to be a bare attribution wrapper."""
    a, b = BuiltInMount(), BuiltInMount()
    sync_step(a, "mount", a.mount)(None)
    sync_step(b, "mount", b.mount)(None)
    assert a.nav.component_id == b.nav.component_id
    assert re.fullmatch(r"navbarcomponent_[0-9a-f]{8}", a.nav.component_id)


def test_an_embedded_childs_mount_keeps_random_ids():
    """A child has no ``view_id`` when it mounts, so two of one class would number alike."""
    from djust.auth._login_required_middleware import mark_embedded

    a, b = BuiltInMount(), BuiltInMount()
    mark_embedded(a)
    mark_embedded(b)
    run_sync_step(a, "mount", a.mount, None)
    run_sync_step(b, "mount", b.mount, None)
    assert a.nav.component_id != b.nav.component_id


def test_an_explicit_component_id_is_used_as_given():
    class Explicit(LiveView):
        exposure_policy = "explicit"
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


# ------------------------------------------------------------------ B1: held components


def test_a_component_built_on_a_later_render_does_not_take_a_held_components_id():
    view = LazyAttrs()
    view.render_full_template()
    view.show_b = True
    view.render_full_template()
    assert view.a.component_id != view.b.component_id
    registry = getattr(view, "_components", None) or {}
    assert len({view.a.component_id, view.b.component_id} | set(registry)) >= 2


def test_lazily_cached_components_get_distinct_ids():
    view = LazyCache()
    view.mount()
    view.render_full_template()
    view.count = 2
    html = view.render_full_template()
    ids = re.findall(r'id="(navbarcomponent_[0-9a-f]{8})"', html)
    assert len(ids) == 2 and ids[0] != ids[1], ids


def test_the_numbering_of_the_render_step_continues_in_the_page_render():
    """The GET runs ``get_context_data`` as its own ``"render"`` step and the page render
    after it: a component built in each is not numbered 1 twice."""
    view = Twin()
    with reading(view):
        first = NavbarComponent(items=[])
    with _component_ids.render_ids(view, carry_over=True):
        second = NavbarComponent(items=[])
    assert first.component_id != second.component_id
    # ... and a later, standalone render starts again, so the ids repeat from render to render.
    with _component_ids.render_ids(view, carry_over=True):
        third = NavbarComponent(items=[])
    assert third.component_id == first.component_id


def test_the_render_method_continues_the_render_steps_numbering():
    """The wiring the test above stands in for: ``recorded_render`` takes the carry."""

    class Page:
        @recorded_render
        def render_full_template(self):
            return NavbarComponent(items=[])

    view = Page()
    with reading(view):
        first = NavbarComponent(items=[])
    second = view.render_full_template()
    assert first.component_id != second.component_id


def test_a_render_that_ran_inside_the_step_leaves_nothing_to_carry():
    view = Twin()
    with reading(view):
        view.render_full_template()  # the render method runs inside the step: one scope
    with _component_ids.render_ids(view, carry_over=True):
        built = NavbarComponent(items=[])
    with reading(view):
        again = NavbarComponent(items=[])
    assert built.component_id == again.component_id  # both start at 1


# ------------------------------------------------------------------ S2: two children of one class


def test_two_children_of_one_view_class_get_distinct_component_ids():
    a, b, a2 = Twin(), Twin(), Twin()
    a._view_id, b._view_id, a2._view_id = "child_a", "child_b", "child_a"
    ids = [_navbar_ids(v.render_full_template()) for v in (a, b, a2)]
    assert ids[0] != ids[1]
    assert ids[0] == ids[2]  # the same child (same view_id) keeps its ids


# ------------------------------------------------------------------ N2: threads


def test_components_built_on_parallel_threads_get_distinct_ids():
    """A scope is shared by the threads that carry its context: the counter is atomic."""

    class SlowCounts(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            time.sleep(0.002)  # a thread switch between the read and the write
            return value

    view = Twin()
    _component_ids._carried[view] = SlowCounts()
    built = []
    with _component_ids.render_ids(view, carry_over=True):
        context = contextvars.copy_context()

        def build():
            built.append(context.copy().run(lambda: NavbarComponent(items=[]).component_id))

        threads = [threading.Thread(target=build) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert len(built) == 8 and len(set(built)) == 8, built
