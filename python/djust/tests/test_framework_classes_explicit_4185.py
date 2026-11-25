"""#4185: djust's own classes state no legacy mode.

The admin change list and change form, and the ttyd terminal base class, use
explicit exposure; the component ``Meta.event`` alias is no longer pinned to
the legacy parameter policy. Each is driven here through the real transports
(WebSocket, SSE and the HTTP fallback POST) as the explicit class it now is.

The alias names its target with ``component_id``, which strict parameters
reserve as a routing name. It declares that name framework-supplied, and
strict validation binds it only when it names a component the view class
declares (``validation.bind_declared_component_name``), so the alias works under a
project-wide strict policy on every transport.
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView
from djust._parameter_contract import ContractError, ParameterError
from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.components.descriptors import Dropdown, Modal, Tabs
from djust.tests.test_admin_page_render import _Socket, _session_cookie
from djust.validation import (
    bind_declared_component_name,
    declared_framework_arguments,
    get_strict_handler_contract,
    validate_handler_params,
)

pytestmark = pytest.mark.admin

MODULE = __name__
PACKAGE = Path(__file__).resolve().parents[1]

#: Framework files still stating a legacy mode: none since #4183 moved the
#: ``@live_view`` decorator's per-request class.
STILL_LEGACY: set = set()


# --------------------------------------------------------------------------- #
# The package states no legacy mode
# --------------------------------------------------------------------------- #


def _legacy_statements(package: Path = PACKAGE) -> dict[str, list[int]]:
    """Package files (tests excluded) whose class body assigns the legacy
    exposure policy, or that pass the legacy parameter policy to a call."""
    found: dict[str, list[int]] = {}
    for source in sorted(package.rglob("*.py")):
        relative = source.relative_to(package).as_posix()
        if relative.startswith("tests/") or "/tests/" in relative:
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            legacy = False
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                legacy = (
                    any(isinstance(t, ast.Name) and t.id == "exposure_policy" for t in targets)
                    and isinstance(node.value, ast.Constant)
                    and node.value.value == "legacy"
                )
            elif isinstance(node, ast.Call):
                legacy = any(
                    keyword.arg == "parameter_policy"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value == "legacy"
                    for keyword in node.keywords
                )
            if legacy:
                found.setdefault(relative, []).append(node.lineno)
    return found


def test_the_package_states_no_legacy_mode():
    found = _legacy_statements()
    assert set(found) == STILL_LEGACY, found


def test_the_scan_finds_both_statement_forms(tmp_path):
    """Not vacuous: with nothing left in the package, a stand-in package that
    states both forms is reported."""
    # Built from parts: this module states no legacy policy itself.
    statement = "exposure_policy = %r" % "legacy"
    (tmp_path / "views.py").write_text("class V:\n    %s\n" % statement)
    (tmp_path / "handlers.py").write_text('x = event_handler(parameter_policy="legacy")\n')
    assert _legacy_statements(tmp_path) == {"views.py": [2], "handlers.py": [1]}


# --------------------------------------------------------------------------- #
# Admin change list / change form (explicit), over every transport
# --------------------------------------------------------------------------- #


class ExplicitWidget(LiveView):
    exposure_policy = "explicit"
    template = '<p class="widget-body">Widget for {{ object_id }}</p>'
    label = "Explicit widget"

    def mount(self, request, object_id=None, **kwargs):
        self.object_id = object_id

    def get_context_data(self, **kwargs):
        return {"object_id": self.object_id}


setattr(sys.modules[__name__], "ExplicitWidget", ExplicitWidget)


class GroupAdmin(DjustModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]
    change_list_widgets = [ExplicitWidget]
    change_form_widgets = [ExplicitWidget]


SITE = DjustAdminSite(name="explicit_4185_admin")
SITE.register(Group, GroupAdmin)


class AliasPage(LiveView):
    """Components reached only through their view-level ``Meta.event`` aliases."""

    exposure_policy = "explicit"
    login_required = False
    template = (
        "<div dj-root><p>menu={{ menu.is_open }}</p><p>dialog={{ dialog.is_open }}</p>"
        "<p>tab={{ tabs.active }}</p></div>"
    )
    menu = Dropdown()
    dialog = Modal()
    tabs = Tabs()

    def mount(self, request, **kwargs):
        pass


urlpatterns = [
    path("x4185-admin/", SITE.urls),
    path("alias/", AliasPage.as_view()),
]

LIST = "/x4185-admin/auth/group/"


@pytest.fixture
def site_urls(settings):
    from djust.routing import _reset_route_map_cache

    settings.ROOT_URLCONF = MODULE
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MODULE]
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


def test_the_admin_and_terminal_views_are_explicit():
    from djust.admin_ext.views import ModelCreateView, ModelDetailView, ModelListView
    from djust.components.ttyd.terminal import TtydTerminalView

    for view_class in (ModelListView, ModelDetailView, ModelCreateView, TtydTerminalView):
        assert view_class.exposure_policy == "explicit", view_class


async def _staff(django_user_model) -> Client:
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)
    return browser


def _tab(page) -> str:
    """The tab id the page GET minted; each fallback POST names it (#4106)."""
    found = re.search(r'<meta name="djust-tab" content="([^"]+)"', page.content.decode())
    assert found, "the page names no tab"
    return found.group(1)


def _post(browser: Client, url: str, event: str, params: dict, tab: str | None) -> dict:
    headers = {"HTTP_X_DJUST_EVENT": event}
    if tab is not None:
        headers["HTTP_X_DJUST_TAB"] = tab
    response = browser.post(
        url, data=json.dumps(params), content_type="application/json", **headers
    )
    assert response.status_code == 200, response.content[:500]
    return json.loads(response.content)


def _sse_request(method: str, url: str, body, key: str):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


async def _sse_frame(stream, *types):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if chunk.startswith("data:"):
            frame = json.loads(chunk[len("data:") :])
            if frame.get("type") in types:
                return frame


_ANSWERS = ("patch", "html_update", "noop", "error", "permission_denied", "navigate")


async def _sse(view: str, url: str, key: str, events: list) -> tuple[dict, list]:
    """Open an SSE stream for ``view`` and post ``events``: (mount, answers)."""
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    sid = str(uuid.uuid4())
    opened = await sync_to_async(_sse_request)(
        "GET", f"/djust/sse/{sid}/", {"view": view, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(opened, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    answers = []
    try:
        await _sse_frame(stream, "sse_connect")
        mounted = await _sse_frame(stream, "mount", "error")
        for name, params in events:
            posted = await sync_to_async(_sse_request)(
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "event", "event": name, "params": params},
                key,
            )
            answer = await DjustSSEMessageView().post(posted, session_id=sid)
            assert answer.status_code in (200, 202, 204), answer.content
            answers.append(await _sse_frame(stream, *_ANSWERS))
    finally:
        _sse_sessions.pop(sid, None)
        await stream.aclose()
    return mounted, answers


async def _ws(browser: Client, view: str, url: str, events: list) -> tuple[dict, list]:
    answers = []
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, url)
        for name, params in events:
            await ws.send({"type": "event", "event": name, "params": params})
            answers.append(await ws.until(*_ANSWERS))
    return mounted, answers


def _row_order(html: str) -> list[str]:
    return [name for name in re.findall(r"\b(alpha|beta|gamma)\b", html)]


async def _groups() -> list[int]:
    for name in ("alpha", "beta", "gamma"):
        await sync_to_async(Group.objects.create)(name=name)
    return await sync_to_async(
        lambda: list(Group.objects.order_by("pk").values_list("pk", flat=True))
    )()


LIST_VIEW = "djust.admin_ext.views.ModelListView"
DETAIL_VIEW = "djust.admin_ext.views.ModelDetailView"


@pytest.mark.django_db(transaction=True)
async def test_change_list_over_the_websocket(site_urls, django_user_model):
    pks = await _groups()
    browser = await _staff(django_user_model)
    mounted, answers = await _ws(
        browser,
        LIST_VIEW,
        LIST,
        [("sort_by", {"field": "name"}), ("toggle_select", {"pk": pks[0]})],
    )
    assert "Widget for" in mounted["html"]  # the explicit widget is embedded
    assert {"alpha", "beta", "gamma"} <= set(_row_order(mounted["html"]))
    assert all(answer["type"] in ("patch", "html_update") for answer in answers), answers
    assert "1 selected" in json.dumps(answers[1])


@pytest.mark.django_db(transaction=True)
async def test_change_list_over_the_http_fallback(site_urls, django_user_model):
    await _groups()
    browser = await _staff(django_user_model)
    page = await sync_to_async(browser.get)(LIST)
    assert page.status_code == 200 and "Widget for" in page.content.decode()
    tab = _tab(page)
    answer = await sync_to_async(_post)(browser, LIST, "search", {"query": "bet"}, tab)
    assert _row_order(answer["html"]) == ["beta"], answer["html"][:300]
    answer = await sync_to_async(_post)(browser, LIST, "sort_by", {"field": "name"}, tab)
    assert _row_order(answer["html"]) == ["alpha", "beta", "gamma"]


@pytest.mark.django_db(transaction=True)
async def test_change_list_over_sse(site_urls, django_user_model):
    pks = await _groups()
    browser = await _staff(django_user_model)
    key = browser.cookies[settings.SESSION_COOKIE_NAME].value
    mounted, answers = await _sse(
        LIST_VIEW, LIST, key, [("sort_by", {"field": "name"}), ("toggle_select", {"pk": pks[0]})]
    )
    assert mounted["type"] == "mount" and "Widget for" in mounted["html"]
    assert [answer["type"] for answer in answers] == ["patch", "patch"], answers
    assert "1 selected" in json.dumps(answers[1])


# Save carries the form's values (#4218): a Save without them saves nothing.
_EDITS = [
    ("validate_field", {"field": "name", "value": "edited"}),
    ("save_and_continue", {"name": "edited"}),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", ["websocket", "sse"])
async def test_change_form_saves(site_urls, django_user_model, transport):
    group = await sync_to_async(Group.objects.create)(name="edit-me")
    url = "%s%d/change/" % (LIST, group.pk)
    browser = await _staff(django_user_model)
    if transport == "websocket":
        mounted, answers = await _ws(browser, DETAIL_VIEW, url, _EDITS)
    else:
        key = browser.cookies[settings.SESSION_COOKIE_NAME].value
        mounted, answers = await _sse(DETAIL_VIEW, url, key, _EDITS)
    assert "edit-me" in mounted["html"] and "Widget for" in mounted["html"]
    assert all(answer["type"] in ("patch", "html_update") for answer in answers), answers
    assert await sync_to_async(lambda: Group.objects.get(pk=group.pk).name)() == "edited"


@pytest.mark.django_db(transaction=True)
async def test_change_form_over_the_http_fallback(site_urls, django_user_model):
    group = await sync_to_async(Group.objects.create)(name="edit-me")
    url = "%s%d/change/" % (LIST, group.pk)
    browser = await _staff(django_user_model)
    page = await sync_to_async(browser.get)(url)
    assert page.status_code == 200 and "Widget for" in page.content.decode()
    answer = await sync_to_async(_post)(
        browser, url, "update_field", {"field": "name", "value": "x"}, _tab(page)
    )
    assert 'value="x"' in answer["html"]


@pytest.mark.django_db(transaction=True)
async def test_add_form_renders(site_urls, django_user_model):
    browser = await _staff(django_user_model)
    page = await sync_to_async(browser.get)(LIST + "add/")
    assert page.status_code == 200 and "Add group" in page.content.decode()


# --------------------------------------------------------------------------- #
# The component Meta.event alias under the strict parameter policy
# --------------------------------------------------------------------------- #


def test_the_alias_declares_component_id_framework_supplied():
    assert declared_framework_arguments(AliasPage.toggle_dropdown) == {"component_id"}
    metadata = AliasPage.toggle_dropdown._djust_decorators["event_handler"]
    # It follows the project's policy; nothing pins it.
    assert metadata["parameter_policy"] is None
    # The marker is server-only: it is not part of the published metadata.
    assert "framework_arguments" not in json.dumps(metadata, default=str)


def test_the_alias_compiles_as_a_strict_contract_without_publishing_component_id():
    contract = get_strict_handler_contract(AliasPage().toggle_dropdown)
    assert contract.trusted == {"component_id"}
    assert [p["name"] for p in contract.metadata()] == ["value", "kwargs"]


def test_the_marker_makes_only_component_id_framework_supplied():
    """Any other name in the server-side marker is ignored (review G7): a
    framework-owned handler cannot widen the trusted set by listing more."""

    def handler(self, component_id: str = "", view_id: str = "", value=None, **kwargs):
        pass

    handler._djust_framework_arguments = frozenset({"component_id", "view_id", "_secret"})
    assert declared_framework_arguments(handler) == {"component_id"}
    # Not a frozenset: ignored entirely.
    handler._djust_framework_arguments = {"component_id"}
    assert declared_framework_arguments(handler) == frozenset()
    # The contract the default trusts follows: view_id stays reserved, so the
    # handler cannot compile as strict.
    handler._djust_framework_arguments = frozenset({"component_id", "view_id"})
    with pytest.raises(ContractError):
        get_strict_handler_contract(handler)


def test_an_application_handler_cannot_declare_component_id():
    """Only the alias's server-side marker makes the name framework-supplied."""
    from djust.decorators import event_handler

    class Page(LiveView):
        exposure_policy = "explicit"

        @event_handler(parameter_policy="strict")
        def pick(self, component_id: str = "", **kwargs):
            pass

    assert declared_framework_arguments(Page().pick) == frozenset()
    with pytest.raises(ContractError):
        get_strict_handler_contract(Page().pick)


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"component_id": "menu", "value": "1"}, ({"value": "1"}, "menu")),
        ({"component_id": "", "value": "1"}, ({"value": "1"}, "")),
        ({"value": "1"}, ({"value": "1"}, "")),
        ({"component_id": None}, ({}, "")),
    ],
)
def test_a_declared_or_empty_component_id_is_bound(params, expected):
    remaining, trusted = bind_declared_component_name(AliasPage().toggle_dropdown, params)
    assert (remaining, trusted["component_id"]) == expected


@pytest.mark.parametrize("target", ["secret", "template", "__class__", ["menu"], 7, {"a": 1}])
def test_any_other_component_id_is_refused(target):
    with pytest.raises(ParameterError):
        bind_declared_component_name(AliasPage().toggle_dropdown, {"component_id": target})


def test_strict_validation_routes_component_id_to_the_alias():
    view = AliasPage()
    ok = validate_handler_params(view.set_tab, {"component_id": "tabs", "value": "2"}, "set_tab")
    assert ok["valid"], ok
    refused = validate_handler_params(view.set_tab, {"component_id": "nope"}, "set_tab")
    assert not refused["valid"]


@pytest.mark.django_db(transaction=True)
async def test_the_alias_dispatches_under_strict_over_the_websocket(site_urls):
    """Events without a component_id reach the view-level alias, which
    resolves the single component of its type."""
    browser = Client()
    await sync_to_async(browser.get)("/alias/")
    _mounted, answers = await _ws(
        browser,
        MODULE + ".AliasPage",
        "/alias/",
        [("toggle_modal", {}), ("set_tab", {"value": "third"}), ("toggle_dropdown", {})],
    )
    assert "error" not in [answer["type"] for answer in answers], answers
    text = json.dumps(answers)
    assert "dialog=True" in text and "tab=third" in text and "menu=True" in text


@pytest.mark.django_db(transaction=True)
async def test_the_alias_dispatches_under_strict_over_the_http_fallback(site_urls):
    browser = Client()
    page = await sync_to_async(browser.get)("/alias/")
    assert page.status_code == 200
    # No tab: nothing saved is put back, so the components are not
    # materialised and the event reaches the view-level alias with its
    # component_id (``resolve_component_target`` answers PASS_THROUGH).
    answer = await sync_to_async(_post)(
        browser, "/alias/", "set_tab", {"component_id": "tabs", "value": "second"}, None
    )
    assert "tab=second" in answer["html"]
    # The page's tab: no component_id, so the alias resolves the single
    # component of its type.
    tab = _tab(page)
    answer = await sync_to_async(_post)(browser, "/alias/", "toggle_dropdown", {}, tab)
    assert "menu=True" in answer["html"]
    answer = await sync_to_async(_post)(browser, "/alias/", "set_tab", {"value": "third"}, tab)
    assert "tab=third" in answer["html"] and "menu=True" in answer["html"]


@pytest.mark.django_db(transaction=True)
async def test_the_alias_dispatches_under_strict_over_sse(site_urls):
    browser = Client()
    await sync_to_async(browser.get)("/alias/")
    key = browser.cookies[settings.SESSION_COOKIE_NAME].value
    _mounted, answers = await _sse(
        MODULE + ".AliasPage",
        "/alias/",
        key,
        [("toggle_modal", {}), ("set_tab", {"value": "third"})],
    )
    assert [answer["type"] for answer in answers] != ["error", "error"], answers
    assert "tab=third" in json.dumps(answers[-1]) and "dialog=True" in json.dumps(answers)
