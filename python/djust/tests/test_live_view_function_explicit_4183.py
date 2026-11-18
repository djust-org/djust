"""#4183: ``@live_view`` function views run without the legacy policy.

The decorator used to build a ``DynamicLiveView`` stating
the legacy exposure policy and to set the function's returned locals as
view attributes for the attribute walk. It now builds an explicit view whose
``get_context_data()`` returns those values; the callables stay attributes, as
the function's event handlers. The decorated function does not change.

What a function view can do, on each transport (unchanged by this port):

* the page GET renders the function's returned values;
* the HTTP POST fallback runs one of the function's ``@event_handler``
  callables, strict or not, and renders the values again. The view is built
  per request and the function runs again for every request, so nothing is
  kept between requests;
* a WebSocket or SSE mount is refused: the class is built inside the request,
  so the ``dj-view`` name the page carries names no importable class.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import textwrap
import uuid
import warnings
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

import importlib
from djust import LiveView, live_view, sse
from djust._exposure import uses_legacy_exposure
from djust.decorators import event_handler
from djust.sse import DjustSSEStreamView, _sse_sessions

MOD = __name__
REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "liveview_stub_demo.py"

#: The views the decorator built, recorded from ``LiveView.get`` / ``post``.
BUILT: list = []


@live_view(
    template=(
        "<div dj-root>count={{ count }} items={{ items|length }} label={{ label }}"
        " picked={{ picked.0 }} helper={{ helper }}"
        " pk={{ pk }}</div>"
    )
)
def counter(request, pk=0):
    count = 3
    label = "hi"
    items: list = []
    picked: list = ["-"]
    _hidden = "secret"

    def helper():
        return "never rendered"

    @event_handler
    def add(**kwargs):
        items.append(1)

    @event_handler(parameter_policy="strict")
    def pick(choice: int):
        picked[0] = choice * 2

    def increment():  # not an event handler: never callable from the client
        nonlocal count
        count += 1

    return locals()


@live_view(template="<div dj-root>members c={{ c }} lr={{ login_required }}</div>")
def members(request):
    login_required = True
    c = 1

    @event_handler
    def bump(**kwargs):
        BUMPED.append("members")

    return locals()


@live_view(template="<div dj-root>admins pr={{ permission_required }}</div>")
def admins(request):
    permission_required = "auth.delete_user"

    @event_handler
    def bump(**kwargs):
        BUMPED.append("admins")

    return locals()


@live_view(template="<div dj-root>decorator's template</div>")
def own_template(request):
    template = "<div dj-root>the function's template n={{ n }}</div>"
    n = 5
    return locals()


@live_view(
    template=(
        "<div dj-root>reserved ok={{ ok }} view={{ view.x }} csrf={{ csrf_token|length }}"
        " date={{ DATE_FORMAT }} time={{ TIME_FORMAT }}</div>"
    )
)
def shadows_framework_names(request):
    ok = "yes"
    view = {"x": "local"}
    csrf_token = "not-the-token"
    DATE_FORMAT = "local date format"
    TIME_FORMAT = "local time format"
    return locals()


#: Spelled through a name: this module defines no legacy view (the ledger in
#: tests/legacy_exposure_ledger.py lists files that do).
LEGACY_POLICY = "legacy"


@live_view(template="<div dj-root>policy</div>")
def states_a_policy(request):
    exposure_policy = LEGACY_POLICY
    return locals()


@live_view(
    template=(
        '<div dj-root><button dj-click="shout" dj-value-times="3">Shout</button>'
        "{% for word in shouted %}<p>{{ word }}</p>{% endfor %}</div>"
    )
)
def greeting(request):
    """The example in docs/website/state/explicit-exposure.md, template inlined."""
    name = request.GET.get("name", "world")
    shouted = []

    @event_handler
    def shout(times: int = 1):
        shouted.extend([name.upper()] * times)

    return locals()


#: Handlers of the gated views that ran.
BUMPED: list = []

urlpatterns = [
    path("fn/", counter),
    path("fn/<int:pk>/", counter),
    path("members/", members),
    path("admins/", admins),
    path("own-template/", own_template),
    path("reserved/", shadows_framework_names),
    path("policy/", states_a_policy),
    path("greeting/", greeting),
    # Defined from the example's source on each request (below).
    path("example/", lambda request: _example_counter_view()(request)),
]


@pytest.fixture
def fn_urls(monkeypatch):
    BUILT.clear()
    for name in ("get", "post"):
        original = getattr(LiveView, name)

        def recording(self, request, *args, _original=original, **kwargs):
            BUILT.append(self)
            return _original(self, request, *args, **kwargs)

        monkeypatch.setattr(LiveView, name, recording)
    with override_settings(
        ROOT_URLCONF=MOD,
        ALLOWED_HOSTS=["testserver"],
        LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
        DEBUG=False,
    ):
        yield
    BUILT.clear()


def _post(browser, url, event, **params):
    return browser.post(
        url, data=json.dumps(params), content_type="application/json", HTTP_X_DJUST_EVENT=event
    )


# --------------------------------------------------------------------------
# The class the decorator builds
# --------------------------------------------------------------------------


def test_no_class_in_live_view_py_states_the_legacy_policy():
    # ``djust.live_view`` is the decorator once a lazy name resolved; the module:
    source = Path(importlib.import_module("djust.live_view").__file__).read_text()
    policies = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(getattr(t, "id", None) == "exposure_policy" for t in statement.targets)
                    and isinstance(statement.value, ast.Constant)
                ):
                    policies[node.name] = statement.value.value
    assert policies["DynamicLiveView"] == "explicit"
    assert "legacy" not in policies.values(), policies


@pytest.mark.django_db
def test_the_view_is_explicit_and_states_no_deprecated_policy(fn_urls):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        response = Client().get("/fn/")
    assert response.status_code == 200
    (view,) = BUILT
    assert type(view).exposure_policy == "explicit"
    assert not uses_legacy_exposure(view)


# --------------------------------------------------------------------------
# The page GET
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_get_renders_the_functions_values(fn_urls):
    html = Client().get("/fn/").content.decode()
    assert "count=3 items=0 label=hi picked=- helper= pk=0" in html, html
    context = type(BUILT[-1])._djust_function_context
    assert "_hidden" not in context and "helper" not in context and "request" not in context


@pytest.mark.django_db
def test_a_route_argument_reaches_the_function_and_the_template(fn_urls):
    html = Client().get("/fn/7/").content.decode()
    assert "pk=7" in html, html


@pytest.mark.django_db
def test_each_request_builds_its_own_class(fn_urls):
    browser = Client()
    browser.get("/fn/")
    browser.get("/fn/7/")
    first, second = BUILT
    assert type(first) is not type(second)
    assert type(first)._djust_function_context["pk"] == 0
    assert type(second)._djust_function_context["pk"] == 7


# --------------------------------------------------------------------------
# The HTTP POST fallback
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_event_handler_runs_over_the_http_fallback(fn_urls):
    browser = Client()
    assert browser.get("/fn/").status_code == 200
    answer = _post(browser, "/fn/", "add")
    assert answer.status_code == 200, answer.content[:300]
    assert "items=1" in answer.json()["html"]
    # The function runs again for the next request: nothing is kept.
    again = _post(browser, "/fn/", "add")
    assert "items=1" in again.json()["html"]


@pytest.mark.django_db
def test_a_strict_handler_runs_with_its_typed_parameter(fn_urls):
    browser = Client()
    browser.get("/fn/")
    answer = _post(browser, "/fn/", "pick", choice="21")
    assert answer.status_code == 200, answer.content[:300]
    assert "picked=42" in answer.json()["html"]


@pytest.mark.django_db
def test_a_strict_handler_refuses_a_parameter_it_does_not_declare(fn_urls):
    browser = Client()
    browser.get("/fn/")
    answer = _post(browser, "/fn/", "pick", choice="1", extra="x")
    assert answer.status_code == 400, answer.content[:300]
    assert answer.json()["validation_details"]["expected_params"] == ["choice"]
    assert "picked=2" not in answer.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize("event", ["increment", "helper"])
def test_a_callable_that_is_not_an_event_handler_is_refused(fn_urls, event):
    browser = Client()
    browser.get("/fn/")
    answer = _post(browser, "/fn/", event)
    assert answer.status_code == 400, answer.content[:300]


# --------------------------------------------------------------------------
# WebSocket and SSE: no class to mount
# --------------------------------------------------------------------------


def _page_view_name(html: str) -> str:
    match = re.search(r'dj-view="([^"]+)"', html)
    assert match, html
    return match[1]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_websocket_mount_is_refused(fn_urls):
    from channels.testing import WebsocketCommunicator

    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    page = await sync_to_async(Client().get)("/fn/")
    name = _page_view_name(page.content.decode())
    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=None, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    try:
        await socket.send_json_to({"type": "mount", "view": name, "url": "/fn/", "params": {}})
        frame = await socket.receive_json_from(timeout=10)
        assert frame["type"] == "error", frame
        assert frame["error"] == "View not found", frame
    finally:
        await socket.disconnect()


def _sse_request(url, body, key):
    request = RequestFactory().get(url, data=body)
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


async def _sse_frame(stream, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if chunk.startswith("data:"):
            frame = json.loads(chunk[len("data:") :])
            if frame.get("type") in types:
                return frame


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_sse_mount_is_refused(fn_urls, monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    _sse_sessions.clear()
    page = await sync_to_async(Client().get)("/fn/")
    name = _page_view_name(page.content.decode())
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_sse_request)(
        f"/djust/sse/{sid}/", {"view": name, "_djust_url": "/fn/"}, key
    )
    stream = (await DjustSSEStreamView().get(request, session_id=sid))._iterator
    try:
        await _sse_frame(stream, "sse_connect")
        frame = await _sse_frame(stream, "mount", "error")
        assert frame["type"] == "error", frame
        assert frame["error"] == "View not found", frame
        assert _sse_sessions.get(sid) is None
    finally:
        await stream.aclose()
        _sse_sessions.clear()


# --------------------------------------------------------------------------
# Returned LiveView settings configure the view, before its access checks
# --------------------------------------------------------------------------


@pytest.fixture
def people(django_user_model):
    from django.contrib.auth.models import Permission

    plain = django_user_model.objects.create_user("plain", password="pw")
    admin = django_user_model.objects.create_user("admin", password="pw")
    admin.user_permissions.add(Permission.objects.get(codename="delete_user"))
    BUMPED.clear()
    yield {"anonymous": None, "plain": plain, "admin": admin}
    BUMPED.clear()


def _browser(people, who):
    browser = Client()
    if people[who] is not None:
        browser.force_login(people[who])
    return browser


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url, who, page, event",
    [
        ("/members/", "anonymous", 302, 403),
        ("/members/", "plain", 200, 200),
        ("/admins/", "anonymous", 302, 403),
        ("/admins/", "plain", 403, 403),
        ("/admins/", "admin", 200, 200),
    ],
)
def test_a_returned_login_or_permission_requirement_is_enforced(
    fn_urls, people, url, who, page, event
):
    browser = _browser(people, who)
    response = browser.get(url)
    assert response.status_code == page, response.content[:300]
    if page == 302:
        assert "login" in response["Location"]
    answer = _post(browser, url, "bump")
    assert answer.status_code == event, answer.content[:300]
    assert BUMPED == ([url.strip("/")] if event == 200 else [])


@pytest.mark.django_db
def test_a_setting_is_configuration_not_a_template_name(fn_urls, people):
    html = _browser(people, "plain").get("/members/").content.decode()
    assert "members c=1 lr=</div>" in html, html
    context = type(BUILT[-1])._djust_function_context
    assert "login_required" not in context and context["c"] == 1


@pytest.mark.django_db
def test_a_returned_template_replaces_the_decorators(fn_urls):
    html = Client().get("/own-template/").content.decode()
    assert "the function's template n=5" in html, html
    assert "decorator's template" not in html


@pytest.mark.django_db
def test_names_djust_supplies_keep_the_framework_value(fn_urls):
    response = Client().get("/reserved/")
    assert response.status_code == 200, response.content[:300]
    html = response.content.decode()
    assert "reserved ok=yes view= " in html, html
    assert "not-the-token" not in html and "local date format" not in html
    assert "local time format" not in html
    answer = _post(Client(), "/reserved/", "missing")
    assert answer.status_code != 500


@pytest.mark.django_db
def test_a_returned_exposure_policy_is_not_applied(fn_urls):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert Client().get("/policy/").status_code == 200
    assert not uses_legacy_exposure(BUILT[-1])


@pytest.mark.django_db
@pytest.mark.parametrize("policy", ["legacy", "strict"])
def test_the_documented_example_answers_its_dj_value_argument(fn_urls, settings, policy):
    settings.LIVEVIEW_CONFIG = {**settings.LIVEVIEW_CONFIG, "event_parameter_policy": policy}
    browser = Client()
    assert browser.get("/greeting/?name=ann").status_code == 200
    # What the button sends: dj-value-times="3" as the ``times`` argument.
    answer = _post(browser, "/greeting/?name=ann", "shout", times="3")
    assert answer.status_code == 200, answer.content[:300]
    assert answer.json()["html"].count(">ANN</p>") == 3


# --------------------------------------------------------------------------
# The shipped example
# --------------------------------------------------------------------------


def _example_counter_view():
    """``counter_view`` from examples/liveview_stub_demo.py, defined as written."""
    tree = ast.parse(EXAMPLE.read_text())
    (demo,) = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "demo_function_based_view"
    ]
    (view,) = [
        node
        for node in demo.body
        if isinstance(node, ast.FunctionDef) and node.name == "counter_view"
    ]
    # From the decorator line: the FunctionDef node starts at ``def``.
    lines = EXAMPLE.read_text().splitlines()[view.decorator_list[0].lineno - 1 : view.end_lineno]
    namespace = {"live_view": live_view}
    code = compile(textwrap.dedent("\n".join(lines)), str(EXAMPLE), "exec")
    exec(code, namespace)  # noqa: S102 - the repository's own example source
    return namespace["counter_view"]


@pytest.mark.django_db
def test_the_shipped_example_renders_without_the_legacy_policy(fn_urls, tmp_path, settings):
    (tmp_path / "counter.html").write_text("<div dj-root>count={{ count }}</div>")
    templates = [dict(entry) for entry in settings.TEMPLATES]
    templates[0]["DIRS"] = [tmp_path, *templates[0].get("DIRS", [])]
    settings.TEMPLATES = templates
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        response = Client().get("/example/")
    assert response.status_code == 200
    assert "count=0" in response.content.decode()
    assert type(BUILT[-1]).exposure_policy == "explicit"
