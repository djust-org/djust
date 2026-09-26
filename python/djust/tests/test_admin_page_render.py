"""Every djust admin page renders, carries a LiveView root, and mounts.

#3139 — ``base.html``, ``model_list.html`` and ``model_detail.html`` used the
``concat`` filter without loading ``djust_admin_tags``, so every page except
the delete confirmation raised ``TemplateSyntaxError`` (a 500). No test
rendered an admin page, which is how it shipped.

#3140 — ``base.html`` and ``login.html`` marked their root with the pre-1.0
``data-djust-root`` attribute, which the client does not mount, so no admin
page ever opened a WebSocket and the login button did nothing. A WebSocket
mount also builds the view with no ``as_view()`` kwargs, so the admin views
must find their site registration from the page URL.

#3142 — with no ``dj-root`` in the shell, the page-shell render fell back to
the first ``dj-view`` element, which was the embedded change-form widget's
wrapper, and spliced the whole page into it: the page appeared twice, with
the second copy's fixed sidebar and heading drawn inside the widget card.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import path

from djust import LiveView
from djust.admin_ext import DjustAdminSite, DjustModelAdmin

pytestmark = pytest.mark.admin

SITE_NAME = "render_pages_admin"
PREFIX = "/render-admin/"
ADMIN_TEMPLATES = Path(__file__).resolve().parents[1] / "admin_ext" / "templates" / "djust_admin"


class ChangeSummaryWidget(LiveView):
    """A change-form widget, as ``examples/demo_project/demo_project/djust_admin.py`` has."""

    template = '<p class="widget-body">Editing group {{ object_id }}</p>'
    label = "Change summary"

    def mount(self, request, object_id=None, **kwargs):
        self.object_id = object_id

    def get_context_data(self, **kwargs):
        return {"object_id": self.object_id}


setattr(sys.modules[__name__], "ChangeSummaryWidget", ChangeSummaryWidget)


class GroupAdmin(DjustModelAdmin):
    change_form_widgets = [ChangeSummaryWidget]
    change_list_widgets = [ChangeSummaryWidget]


site = DjustAdminSite(name=SITE_NAME)
site.site_header = "Render-pages administration"
site.register(Group, GroupAdmin)

# A second site, deployed after the first. Both share the ``djust_admin`` app
# namespace, so a view that lost its registration and reversed
# ``djust_admin:index`` would land on this site, not its own.
other_site = DjustAdminSite(name="render_pages_other_admin")

urlpatterns = [
    path(PREFIX.strip("/") + "/", site.urls),
    path("render-admin-other/", other_site.urls),
]

MODEL = PREFIX + "auth/group/"


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", __name__]


@pytest.fixture
def group(db):
    return Group.objects.create(name="editors")


@pytest.fixture
def admin_client(db, django_user_model):
    user = django_user_model.objects.create_superuser("root", password="pw")
    client = Client()
    client.force_login(user)
    return client


def _pages(group):
    return {
        "index": PREFIX,
        "changelist": MODEL,
        "add": MODEL + "add/",
        "change": f"{MODEL}{group.pk}/change/",
        "delete": f"{MODEL}{group.pk}/delete/",
        # An unknown job renders "Job not found" and starts no polling thread.
        "progress": PREFIX + "djust-progress/no-such-job/",
    }


# The view each page renders, keyed like ``_pages``.
_PAGE_VIEWS = {
    "index": "djust.admin_ext.views.AdminIndexView",
    "changelist": "djust.admin_ext.views.ModelListView",
    "add": "djust.admin_ext.views.ModelCreateView",
    "change": "djust.admin_ext.views.ModelDetailView",
    "delete": "djust.admin_ext.views.ModelDeleteView",
    "progress": "djust.admin_ext.progress.BulkActionProgressView",
}


# --------------------------------------------------------------------------- #
# #3139: every page renders
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("page", sorted(_PAGE_VIEWS))
def test_admin_page_renders(admin_client, group, page):
    response = admin_client.get(_pages(group)[page])
    assert response.status_code == 200, response.content[:500]


@pytest.mark.parametrize("page", ["login", "logout"])
def test_auth_page_renders(db, page):
    response = Client().get(f"{PREFIX}{page}/")
    assert response.status_code == 200, response.content[:500]


def test_every_template_using_admin_tags_loads_them():
    """``{% load %}`` does not cross ``{% extends %}``, so each file must load
    the library it uses. The render tests alone cannot pin this: once any
    template in the process has loaded ``djust_admin_tags``, the Rust engine
    resolves its filters for every later render, so a page that forgot the
    load renders fine whenever a page that has it rendered first."""
    from djust.admin_ext.templatetags.djust_admin_tags import register

    names = sorted(set(register.filters) | set(register.tags))
    use = re.compile(r"\|\s*(?:%s)\b|\{%%\s*(?:%s)\b" % ("|".join(names), "|".join(names)))
    load = re.compile(r"\{%\s*load\s[^%]*\bdjust_admin_tags\b")
    offenders = [
        p.name
        for p in sorted(ADMIN_TEMPLATES.glob("*.html"))
        if use.search(p.read_text()) and not load.search(p.read_text())
    ]
    assert names and offenders == []


# --------------------------------------------------------------------------- #
# #3140: every LiveView page carries the root the client mounts
# --------------------------------------------------------------------------- #

_ROOT = re.compile(r"<div dj-root dj-view=\"([^\"]+)\">")


@pytest.mark.parametrize("page", sorted(_PAGE_VIEWS))
def test_admin_page_root_names_its_view(admin_client, group, page):
    html = admin_client.get(_pages(group)[page]).content.decode()
    assert _ROOT.findall(html) == [_PAGE_VIEWS[page]]


def test_login_page_root_names_the_login_view(db):
    html = Client().get(PREFIX + "login/").content.decode()
    assert _ROOT.findall(html) == ["djust.admin_ext.views.LoginView"]


def test_no_admin_template_uses_the_legacy_root_attribute():
    offenders = [
        p.name
        for p in ADMIN_TEMPLATES.glob("*.html")
        if re.search(r"data-djust-(root|view)(?![\w-])", p.read_text())
    ]
    assert offenders == []


def _view_at(view_cls, url):
    from django.test import RequestFactory

    view = view_cls()
    view.request = RequestFactory().get(url)
    return view


def test_registration_is_recovered_only_from_a_route_serving_the_view():
    """A socket mount names both the view and the URL, so the URL's route
    must serve that view's class before its registration is used."""
    from djust.admin_ext.views import AdminIndexView, ModelListView

    listing = _view_at(ModelListView, MODEL)
    assert listing._admin_site is site
    assert listing._model is Group

    # The changelist URL routes to ModelListView, not AdminIndexView.
    assert _view_at(AdminIndexView, MODEL)._admin_site is None
    assert _view_at(ModelListView, "/no-such-page/")._admin_site is None


# --------------------------------------------------------------------------- #
# #3142: a page widget renders once, inside the page
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("page", ["change", "add", "changelist"])
def test_page_widget_renders_once(admin_client, group, page):
    html = admin_client.get(_pages(group)[page]).content.decode()
    assert html.count("<!DOCTYPE") == 1
    assert html.count("<aside") == 1
    assert html.count("Change summary") == 1
    assert html.count('class="widget-body"') == 1
    # The widget's embedded wrapper holds only the widget's own markup.
    wrapper = re.search(r'<div dj-view data-djust-embedded="[^"]+">(.*?)</div>', html, re.S)
    assert wrapper is not None
    assert wrapper.group(1).startswith('<p class="widget-body">')


# --------------------------------------------------------------------------- #
# #3140: the pages mount over a real WebSocket, and the login submit works
# --------------------------------------------------------------------------- #


def _socket_app():
    from channels.auth import AuthMiddlewareStack

    from djust.websocket import LiveViewConsumer

    return AuthMiddlewareStack(LiveViewConsumer.as_asgi())


class _Socket:
    def __init__(self, cookie_header: str = ""):
        from channels.testing import WebsocketCommunicator

        headers = [(b"host", b"testserver"), (b"origin", b"http://testserver")]
        if cookie_header:
            headers.append((b"cookie", cookie_header.encode()))
        self.comm = WebsocketCommunicator(_socket_app(), "/ws/live/", headers=headers)

    async def __aenter__(self):
        connected, _ = await self.comm.connect()
        assert connected
        return self

    async def __aexit__(self, *exc):
        await self.comm.disconnect()

    async def send(self, frame):
        await self.comm.send_json_to(frame)

    async def until(self, *types):
        for _ in range(30):
            frame = await self.comm.receive_json_from(timeout=10)
            assert frame.get("type") != "error", frame
            if frame.get("type") in types:
                return frame
        raise AssertionError("no %s frame" % (types,))

    async def mount(self, view, url, params=None):
        await self.send({"type": "mount", "view": view, "url": url, "params": params or {}})
        return await self.until("mount")


def _session_cookie(client: Client) -> str:
    return "%s=%s" % (
        settings.SESSION_COOKIE_NAME,
        client.cookies[settings.SESSION_COOKIE_NAME].value,
    )


async def _socket_login(django_user_model, params=None):
    """Load the login page, then sign in over the socket the page opens.

    Returns the pushed ``redirect`` URL and the browser holding the session."""
    await sync_to_async(django_user_model.objects.create_user)(
        "staff", password="s3cret-pw", is_staff=True
    )
    browser = Client()
    page = await sync_to_async(browser.get)(PREFIX + "login/")
    assert page.status_code == 200

    async with _Socket(_session_cookie(browser)) as ws:
        # The client sends the page's query string as the mount params.
        mounted = await ws.mount("djust.admin_ext.views.LoginView", PREFIX + "login/", params)
        assert "Render-pages administration" in mounted["html"]
        for name, value in (("update_username", "staff"), ("update_password", "s3cret-pw")):
            await ws.send({"type": "event", "event": name, "params": {"value": value}})
            await ws.until("patch", "html_update", "noop")
        await ws.send({"type": "event", "event": "do_login", "params": {}})
        pushed = await ws.until("push_event")
    assert pushed["event"] == "redirect"
    return pushed["payload"]["url"], browser


@pytest.mark.django_db(transaction=True)
async def test_login_view_mounts_and_its_submit_logs_in(django_user_model):
    url, browser = await _socket_login(django_user_model)

    # The redirect names THIS site's index: the view found its registration
    # from the page URL, although a socket mount passes no as_view() kwargs.
    assert url == PREFIX
    # The session the browser holds is now logged in.
    response = await sync_to_async(browser.get)(PREFIX)
    assert response.status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "next_url, expected",
    [
        (MODEL, MODEL),
        ("https://other.example/", PREFIX),
        ("//other.example/", PREFIX),
        ("http://testserver.other.example/", PREFIX),
        ("/\\other.example", PREFIX),
        ("https:other.example", PREFIX),
        ("javascript:alert(1)", PREFIX),
        (" javascript:alert(1)", PREFIX),
        ("///other.example", PREFIX),
    ],
)
async def test_login_redirect_honors_only_a_same_host_next(django_user_model, next_url, expected):
    """``?next=`` reaches the socket mount, so the sign-in that now works must
    not redirect to another host."""
    url, _ = await _socket_login(django_user_model, {"next": next_url})
    assert url == expected


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("page", sorted(_PAGE_VIEWS))
async def test_admin_page_mounts_over_websocket(django_user_model, page):
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    grp = await sync_to_async(Group.objects.create)(name="editors")
    browser = Client()
    await sync_to_async(browser.force_login)(user)

    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(_PAGE_VIEWS[page], _pages(grp)[page])
    # The chrome comes from this site's registration.
    assert "Render-pages administration" in mounted["html"]


@pytest.mark.django_db(transaction=True)
async def test_admin_view_on_an_unrouted_url_is_refused_cleanly(django_user_model, caplog):
    """A socket mount whose URL routes nowhere (or to another view) has no
    site registration: it is refused as a permission failure, not a crash."""
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)

    async with _Socket(_session_cookie(browser)) as ws:
        await ws.send(
            {
                "type": "mount",
                "view": "djust.admin_ext.views.AdminIndexView",
                "url": "/no-such-page/",
            }
        )
        frames = []
        for _ in range(10):
            try:
                frame = await ws.comm.receive_json_from(timeout=3)
            except Exception:
                break
            frames.append(frame)
            if frame.get("type") in ("mount", "error", "navigate"):
                break
    assert not any(f.get("type") == "mount" for f in frames), frames
    assert any(f.get("type") in ("error", "navigate") for f in frames), frames
    assert "Traceback" not in caplog.text
    assert "get_app_list" not in caplog.text


def test_a_failed_route_lookup_is_cached(monkeypatch):
    from djust.admin_ext import views as admin_views

    calls = []
    real = admin_views._registry_id_from_route

    def counting(view):
        calls.append(view)
        return real(view)

    monkeypatch.setattr(admin_views, "_registry_id_from_route", counting)
    view = _view_at(admin_views.AdminIndexView, "/no-such-page/")
    for _ in range(3):
        assert view._admin_site is None
    assert len(calls) == 1
