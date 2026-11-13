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
import re

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


#: The demo project's own apps. ``djust.checks.utils._is_framework_internal_class``
#: classes every ``djust_*`` module (other than test/example ones) as djust's own
#: code, so ``manage.py djust_check`` skips the routed ``djust_demos``,
#: ``djust_forms``, ``djust_rentals``, ``djust_docs`` and ``djust_homepage`` views
#: entirely; the checks below are run with them classed as user code.
DEMO_APPS = (
    "demo_app",
    "demo_project",
    "djust_demos",
    "djust_docs",
    "djust_forms",
    "djust_homepage",
    "djust_rentals",
    "djust_shared",
    "djust_tests",
    "sticky_demo",
)


def _is_demo(cls):
    return (getattr(cls, "__module__", "") or "").split(".")[0] in DEMO_APPS


@pytest.fixture
def demo_views_as_user_code(monkeypatch):
    """Import every demo view module and let the checks see the djust_* apps."""
    import importlib
    import pkgutil

    import djust.checks.components as components
    import djust.checks.parameters as parameters
    import djust.checks.utils as utils

    original = utils._is_framework_internal_class

    def internal(cls):
        return False if _is_demo(cls) else original(cls)

    for module in (utils, components, parameters):
        monkeypatch.setattr(module, "_is_framework_internal_class", internal)
    for app in DEMO_APPS:
        package = importlib.import_module(app)
        for info in pkgutil.walk_packages(package.__path__, app + "."):
            if ".tests" in info.name or ".migrations" in info.name:
                continue
            try:
                importlib.import_module(info.name)
            except ImportError:
                continue  # a module whose optional dependency is not installed
    return internal


def test_the_demo_checks_report_nothing_on_any_demo_app(demo_views_as_user_code):
    """T019 (a handler the strict mode refuses), T020 (parameters it does not
    accept) and V029 (a change the HTTP fallback loses) over every demo view,
    the routed djust_* apps included (#4086)."""
    from djust.checks.bindings import check_event_bindings
    from djust.checks.components import check_undeclared_handler_state

    found = [
        "%s %s" % (m.id, m.msg)
        for m in list(check_event_bindings(None)) + list(check_undeclared_handler_state(None))
        if m.id in ("djust.T019", "djust.T020", "djust.V029")
    ]
    # A finding belongs to a demo app when it names a demo class (``<app>.<module>.<Class>``),
    # not merely a demo template: a LiveView defined elsewhere in the same process (a doc snippet
    # run by another test, for instance) can render a demo template under its own class.
    demo_class = re.compile(r"(?<![\w.])(?:%s)\.[\w.]+" % "|".join(map(re.escape, DEMO_APPS)))
    demo = [line for line in found if demo_class.search(line)]
    assert demo == []


def test_every_routed_binding_names_a_decorated_handler(demo_views_as_user_code):
    """Independent of the checks: every LiveView the root URLconf reaches, and every
    LiveView in a demo app, has an @event_handler for each literal dj-* event its
    template (and the templates it includes or extends) names."""
    import re

    from django.template.loader import get_template
    from django.urls import URLResolver, get_resolver

    from djust.decorators import is_event_handler
    from djust.live_view import LiveView

    routed = set()

    def walk(resolver):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern)
            else:
                view = getattr(pattern.callback, "view_class", None)
                if isinstance(view, type) and issubclass(view, LiveView):
                    routed.add(view)

    walk(get_resolver())

    def subclasses(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from subclasses(sub)

    views = routed | {cls for cls in subclasses(LiveView) if _is_demo(cls)}
    assert len(routed) > 50, len(routed)  # the walk reached the app URLconfs
    # A binding inside a real tag (an escaped code sample, &lt;button ...&gt;, is not one).
    event = re.compile(
        r"<[a-zA-Z][^<>]*?\bdj-(?:click|input|submit|change|keydown|keyup|blur|focus|scroll)"
        r"(?:\.[\w.-]+)?\s*=\s*\"([A-Za-z_]\w*)\""
    )
    nested = re.compile(r"{%\s*(?:include|extends)\s+[\"']([^\"']+)[\"']")

    def source(name, seen):
        if name in seen:
            return ""
        seen.add(name)
        try:
            template = get_template(name)
        except Exception:  # noqa: BLE001 - a name built at runtime
            return ""
        text = getattr(getattr(template, "template", template), "source", "") or ""
        return text + "".join("\n" + source(n, seen) for n in nested.findall(text))

    missing = []
    for view in views:
        text = view.template if isinstance(getattr(view, "template", None), str) else ""
        text += "".join("\n" + source(n, set()) for n in nested.findall(text))
        if isinstance(getattr(view, "template_name", None), str):
            text += "\n" + source(view.template_name, set())
        for name in set(event.findall(text)):
            handler = getattr(view, name, None)
            if handler is None or not is_event_handler(handler):
                missing.append("%s.%s.%s" % (view.__module__, view.__qualname__, name))
    assert sorted(missing) == []


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


CAPPED = [
    ("demo_app.views.homepage.HomeTodoDemo", "add_todo", {"text": "t"}, "todos", "MAX_TODOS"),
    (
        "demo_app.views_old.IndexView",
        "add_todo",
        {"todo_text": "t"},
        "demo_todos",
        "MAX_DEMO_TODOS",
    ),
    ("djust_homepage.views.IndexView", "add_todo", {"todo_text": "t"}, "demo_todos", None),
    (
        "sticky_demo.views.NotificationCenterView",
        "add_demo",
        {},
        "notifications",
        "MAX_NOTIFICATIONS",
    ),
    ("demo_app.views.no_template_demo.NoTemplateDemo", "add_item", {}, "items", "MAX_ITEMS"),
    ("djust_demos.views.no_template_demo.NoTemplateDemo", "add_item", {}, "items", "MAX_ITEMS"),
]


@pytest.mark.parametrize(
    "path, handler, params, field, cap_name", CAPPED, ids=["%s.%s" % (c[0], c[1]) for c in CAPPED]
)
def test_a_persisted_list_a_client_grows_is_capped(path, handler, params, field, cap_name):
    """Persisted state is re-saved on every event and bounded per view (#4086): the
    demo drops the oldest entries rather than let a client grow it forever."""
    import sys

    from django.utils.module_loading import import_string

    from djust.testing import LiveViewTestClient

    view = import_string(path)
    cap = getattr(sys.modules[view.__module__], cap_name or "MAX_DEMO_TODOS", None)
    if cap is None:  # a subclass reusing its base's handler
        cap = getattr(sys.modules[view.__mro__[1].__module__], "MAX_DEMO_TODOS")
    client = LiveViewTestClient(view).mount()
    for _ in range(cap + 5):
        assert client.send_event(handler, **params)["success"]
    assert len(getattr(client.view_instance, field)) == cap


# A user on a routed page clicks a button whose handler had no @event_handler
# (#4086): the page's own URL, a real GET, then the HTTP fallback POST the
# client sends for the click. The default strict mode refused each of these.
CLICKS = [
    ("/demos/optimistic-counter/", "increment", {}, "6"),
    ("/docs/components/", "increment_demo_count", {}, None),
    ("/rentals/financials/", "change_period", {"period": "quarter"}, "quarter"),
    # The demos index pages named handlers their view did not have.
    ("/demos/", "increment", {}, None),
    ("/demos/design2/", "toggle_dropdown", {}, None),
    ("/demos/design3/", "reset", {}, None),
    ("/demos/hybrid/", "decrement", {}, None),
]


@pytest.mark.parametrize("url, handler, params, shown", CLICKS, ids=[c[0] for c in CLICKS])
def test_a_routed_page_answers_a_click_on_a_once_refused_handler(
    client, url, handler, params, shown
):
    assert client.get(url).status_code == 200
    response = client.post(
        url, data=json.dumps(params), content_type="application/json", HTTP_X_DJUST_EVENT=handler
    )
    body = response.content.decode("utf-8")
    assert response.status_code == 200, body[:500]
    assert '"error"' not in body, body[:500]
    if shown is not None:
        assert shown in body, body[:500]
