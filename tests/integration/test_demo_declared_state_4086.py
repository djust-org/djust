"""The demo project's counters and toggles keep their value over the HTTP fallback (#4086).

``djust.V029`` (#4053) reported seven demo views whose handlers read a ``state()``
field and change it, declared without ``persist``. Over a WebSocket the view
lives between events, but the HTTP page-POST fallback builds a new view for
each event and runs ``mount()`` again, so every fallback event answered from
the initial value. Each case below drives the real ``LiveView.get`` and three
``LiveView.post`` calls with one session, the way the fallback does, and reads
the field from the view that answered each POST.

Gate-off: drop ``persist="server"`` from a field below and its case sees the
mount() value three times.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

pytestmark = pytest.mark.django_db

PATH = "/demo-4086/"

# (view, handler, params, read the value, the three expected values)
CASES = [
    (
        "demo_app.views.cache_test.CacheTestView",
        "search",
        {"query": "q", "total_searches": 1},
        lambda v: v.server_calls,
        [1, 2, 3],
    ),
    (
        "djust_tests.views.cache_test.CacheTestView",
        "search",
        {"query": "q", "total_searches": 1},
        lambda v: v.server_calls,
        [1, 2, 3],
    ),
    (
        "demo_app.views.draft_mode_test.DraftModeTestView",
        "save_article",
        {"title": "t", "content": "c", "author": "a"},
        lambda v: v.save_count,
        [1, 2, 3],
    ),
    (
        "djust_tests.views.draft_mode_test.DraftModeTestView",
        "save_article",
        {"title": "t", "content": "c", "author": "a"},
        lambda v: v.save_count,
        [1, 2, 3],
    ),
    (
        "demo_app.views.loading_test.LoadingTestView",
        "fast_operation",
        {},
        lambda v: v.fast_call_count,
        [1, 2, 3],
    ),
    (
        "djust_tests.views.loading_test.LoadingTestView",
        "slow_operation",
        {},
        lambda v: v.slow_call_count,
        [1, 2, 3],
    ),
    (
        "djust_tests.views.virtual_keyed_demo.VirtualKeyedDemoView",
        "insert_middle",
        {},
        lambda v: len(v.items),
        [61, 62, 63],
    ),
    (
        "sticky_demo.views.AudioPlayerView",
        "toggle_play",
        {},
        lambda v: v.is_playing,
        [True, False, True],
    ),
    (
        "sticky_demo.views.AudioPlayerView",
        "next_track",
        {},
        lambda v: v.elapsed_seconds,
        [1, 2, 3],
    ),
    (
        "sticky_demo.views.DashboardView",
        "refresh_metrics",
        {},
        lambda v: (v.metric_visitors, v.metric_orders),
        [(45, 8), (48, 9), (51, 10)],
    ),
    (
        "sticky_demo.views.NotificationCenterView",
        "add_demo",
        {},
        lambda v: len(v.notifications),
        [4, 5, 6],
    ),
]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """The loading demo sleeps to show its spinner; the count is what is tested."""
    import time

    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def _three_fallback_events(view_cls, handler, params, read):
    get = RequestFactory().get(PATH)
    get.user = AnonymousUser()
    get.tenant = None
    get.session = SessionStore()
    get.session.create()
    page = view_cls()
    page.setup(get)  # as View.as_view() does; a sticky child reads the parent's request
    response = page.get(get)
    assert response.status_code == 200, response.content[:500]
    seen = []
    for _ in range(3):
        request = RequestFactory().post(
            PATH,
            data=json.dumps(params),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=handler,
        )
        request.user, request.session, request.tenant = get.user, get.session, None
        view = view_cls()
        view.setup(request)
        response = view.post(request)
        assert response.status_code == 200, response.content[:500]
        seen.append(read(view))
    return seen


@pytest.mark.parametrize(
    "path, handler, params, read, expected",
    CASES,
    ids=["%s.%s" % (c[0], c[1]) for c in CASES],
)
def test_the_value_survives_three_fallback_events(path, handler, params, read, expected):
    from django.utils.module_loading import import_string

    assert _three_fallback_events(import_string(path), handler, params, read) == expected


def test_v029_reports_nothing_on_the_demo_project():
    """The check that found these keeps the demo at zero findings."""
    from djust.checks.components import check_undeclared_handler_state

    # Import every demo view module the check walks.
    import demo_app.views  # noqa: F401
    import djust_tests.views  # noqa: F401
    import djust_tests.views.virtual_keyed_demo  # noqa: F401
    import sticky_demo.views  # noqa: F401

    demo_prefixes = ("demo_app.", "djust_tests.", "sticky_demo.", "djust_demos.", "djust_homepage.")
    found = [m.msg for m in check_undeclared_handler_state(None) if m.msg.startswith(demo_prefixes)]
    assert found == []


# Handlers that had no @event_handler (the default strict mode refused them) and,
# once decorated, change state a later event reads: declared persist="server" too.
# Each event must see the previous one's change: three different values in a row.
CHANGING = [
    ("demo_app.views.cache_demo.CacheDemoView", "search", {"query": "q"}, "server_calls"),
    ("demo_app.views.component_showcase.ComponentShowcaseView", "increment", {}, "counter"),
    (
        "demo_app.views.component_showcase.ComponentShowcaseView",
        "toggle_switch",
        {},
        "switch_enabled",
    ),
    ("demo_app.views.components_demo.ComponentsDemoView", "increment", {}, "count"),
    (
        "demo_app.views.components_guide.ComponentsGuideView",
        "increment_demo_count",
        {},
        "demo_count",
    ),
    ("demo_app.views.debounce_demo.DebounceSearchView", "search", {"value": "q"}, "search_count"),
    ("demo_app.views.homepage.HomeCounterDemo", "increment", {}, "count"),
    ("demo_app.views.homepage.HomeTodoDemo", "add_todo", {"text": "t"}, "todos"),
    ("demo_app.views.no_template_demo.NoTemplateDemo", "increment", {}, "counter"),
    ("demo_app.views.no_template_demo.NoTemplateDemo", "add_item", {}, "items"),
    ("demo_app.views.no_template_demo.NoTemplateDemo", "increase_progress", {}, "progress"),
    ("demo_app.views.optimistic_counter_demo.OptimisticCounterView", "increment", {}, "count"),
    ("demo_app.views.rust_components_demo.RustComponentsDemo", "increment_counter", {}, "counter"),
    (
        "demo_app.views.throttle_demo.ThrottleScrollView",
        "on_scroll",
        {"scroll_y": 5},
        "update_count",
    ),
    ("demo_app.views_old.IndexView", "increment_counter", {}, "demo_counter"),
    ("demo_app.views_old.IndexView", "increment_notifications", {}, "notification_count"),
    ("demo_app.views_old.IndexView", "add_todo", {"todo_text": "t"}, "demo_todos"),
    ("demo_app.views_old.NavbarBadgeDemo", "increment_notifications", {}, "notification_count"),
    ("djust_homepage.views.IndexView", "increment_counter", {}, "demo_counter"),
]


@pytest.mark.parametrize(
    "path, handler, params, field", CHANGING, ids=["%s.%s" % (c[0], c[1]) for c in CHANGING]
)
def test_a_newly_callable_handler_keeps_its_change(path, handler, params, field):
    import copy

    from django.utils.module_loading import import_string

    seen = _three_fallback_events(
        import_string(path), handler, params, lambda v: copy.deepcopy(getattr(v, field))
    )
    assert seen[0] != seen[1] and seen[1] != seen[2], seen


# The homepage app's views moved from legacy to explicit exposure (#4086). Each
# page must still render what its template reads.
HOMEPAGE = [
    ("/", ["Python", "Kotlin"]),
    ("/home-demos/counter/", [">0<"]),
    ("/home-demos/search/", ["Phoenix LiveView", "Showing 6 of 6 frameworks"]),
    ("/home-demos/data/", ["Memory Usage"]),
    ("/home-demos/todo/", ["Read the documentation", "2 active, 0 completed"]),
    ("/home-demos/navbar-badge/", ["Demos"]),
]


@pytest.mark.parametrize("url, expected", HOMEPAGE, ids=[u for u, _ in HOMEPAGE])
def test_the_homepage_views_render_under_explicit_exposure(client, url, expected):
    from django.urls import resolve

    assert resolve(url).func.view_class.exposure_policy == "explicit"
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode("utf-8")
    for text in expected:
        assert text in body, (url, text)


def test_the_custom_navbar_example_supplies_its_names_under_explicit_exposure():
    """An unrouted example (its template is not in the demo): its context still
    carries what it supplied under legacy exposure (#4086)."""
    from demo_app.views.navbar_example import CustomNavbarView

    assert CustomNavbarView.exposure_policy == "explicit"
    view = CustomNavbarView()
    view.mount(None)
    context = view.get_context_data()
    assert context["title"] == "Custom Navbar Example"
    assert "My App" in context["navbar"].render()
