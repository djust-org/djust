"""``sw_cacheable = True`` opts an explicit page into service-worker caching (#4176).

The transports are driven for real (``open_live_page``): the HTTP GET header
and the WebSocket and SSE mount frames all come from
``service_worker_cache_eligible``. The V037 check is run against fixture
views loaded from a temporary directory.
"""

from __future__ import annotations

import gc
import importlib.util
import sys
import textwrap
import types

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings
from django.urls import include, path

from djust import LiveView
from djust._exposure import service_worker_cache_eligible
from djust.checks.components import check_sw_cacheable_views
from djust.decorators import state
from djust.security.service_worker import SW_CACHE_HEADER
from djust.sse import sse_urlpatterns
from djust.testing import open_live_page

pytestmark = [pytest.mark.django_db(transaction=True)]


class PublicDocs(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ n }}</p></div>"
    sw_cacheable = True
    n = state(1)


class PrivatePage(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ n }}</p></div>"
    n = state(1)


urlpatterns = [
    path("sw/public/", PublicDocs.as_view()),
    path("sw/private/", PrivatePage.as_view()),
    path("djust/", include(sse_urlpatterns)),
]


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        LIVEVIEW_ALLOWED_MODULES=[__name__.rsplit(".", 1)[0]],
        ASGI_APPLICATION=None,
    ):
        yield


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def test_http_get_opted_in_page_is_cacheable_and_other_is_no_store():
    public = open_live_page("/sw/public/").response
    private = open_live_page("/sw/private/").response
    assert SW_CACHE_HEADER not in public
    assert private[SW_CACHE_HEADER] == "no-store"


async def test_websocket_mount_frame():
    page = await sync_to_async(open_live_page)("/sw/public/")
    async with await page.connect() as socket:
        assert socket.mount["type"] == "mount"
        assert "html" in socket.mount
        assert "sw_cache" not in socket.mount
        assert "sw_identity" in socket.mount
    page = await sync_to_async(open_live_page)("/sw/private/")
    async with await page.connect() as socket:
        assert socket.mount["sw_cache"] == "no-store"


async def test_sse_mount_frame():
    page = await sync_to_async(open_live_page)("/sw/public/")
    async with await page.connect_sse() as stream:
        assert stream.mount["type"] == "mount"
        assert "sw_cache" not in stream.mount
        assert "sw_identity" in stream.mount
    page = await sync_to_async(open_live_page)("/sw/private/")
    async with await page.connect_sse() as stream:
        assert stream.mount["sw_cache"] == "no-store"


def test_eligibility_rules(staged):
    opted = PublicDocs()
    assert service_worker_cache_eligible(opted) is True

    mixed = PublicDocs()
    mixed._get_all_child_views = lambda: {"c": PrivatePage()}
    assert service_worker_cache_eligible(mixed) is False

    all_in = PublicDocs()
    all_in._get_all_child_views = lambda: {"c": PublicDocs()}
    assert service_worker_cache_eligible(all_in) is True

    page = PrivatePage()
    page.sw_cacheable = True  # an instance assignment is inert
    assert service_worker_cache_eligible(page) is False

    class Yes(PrivatePage):
        sw_cacheable = "yes"  # type: ignore[assignment]

    assert service_worker_cache_eligible(Yes()) is False


# ---------------------------------------------------------------------------
# V037
# ---------------------------------------------------------------------------

MODULE = "sw_check_fixture_4176"

SOURCE = """
from djust import LiveView
from djust.decorators import state


class ReadsUser(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = True
    template = "<div dj-root></div>"

    def get_context_data(self, **kwargs):
        return {"who": self.request.user.get_username()}


class KeepsServerState(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = True
    template = "<div dj-root></div>"
    n = state(0, persist="server")


class TemplateReadsUser(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = True
    template = "<div dj-root>{% if user.is_authenticated %}hi{% endif %}</div>"


class Public(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = True
    template = "<div dj-root><p>{{ n }}</p></div>"
    n = state(1)


class NotOptedIn(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    def mount(self, request, **kwargs):
        self.who = request.user
        self.s = request.session


class ParentReadsUser(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = True
    template_name = "sw4176/page.html"


class NeedsLogin(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = True
    login_required = True
    template = "<div dj-root></div>"


class BadValue(LiveView):
    exposure_policy = "explicit"
    sw_cacheable = "yes"
    template = "<div dj-root></div>"
"""


def _load(tmp_path) -> types.ModuleType:
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


def _unload(module: types.ModuleType) -> None:
    sys.modules.pop(MODULE, None)
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == MODULE:
            value.abstract = True
    gc.collect()


@pytest.fixture
def fixture_module(tmp_path):
    templates = tmp_path / "templates"
    (templates / "sw4176").mkdir(parents=True)
    (templates / "sw4176" / "base.html").write_text(
        "<main dj-root>{{ user.username }}{% block content %}{% endblock %}</main>"
    )
    (templates / "sw4176" / "page.html").write_text(
        '{% extends "sw4176/base.html" %}{% block content %}hello{% endblock %}'
    )
    module = _load(tmp_path)
    engines = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(templates)],
            "APP_DIRS": True,
        }
    ]
    try:
        with override_settings(TEMPLATES=engines):
            yield module
    finally:
        _unload(module)


def _ours(messages):
    return {
        name: [m for m in messages if m.id == "djust.V037" and "%s.%s " % (MODULE, name) in m.msg]
        for name in (
            "ReadsUser",
            "KeepsServerState",
            "TemplateReadsUser",
            "ParentReadsUser",
            "NeedsLogin",
            "Public",
            "NotOptedIn",
            "BadValue",
        )
    }


def test_v037_reports_a_cacheable_view_that_is_per_visitor(fixture_module):
    found = _ours(check_sw_cacheable_views(None))
    assert len(found["ReadsUser"]) == 1 and "request.user" in found["ReadsUser"][0].msg
    assert len(found["KeepsServerState"]) == 1
    assert "'n'" in found["KeepsServerState"][0].msg
    assert "persist='server'" in found["KeepsServerState"][0].msg
    assert "user.is_authenticated" in found["TemplateReadsUser"][0].msg
    assert "'yes'" in found["BadValue"][0].msg
    # A read in a parent template ({% extends %}) counts too.
    assert "user.username" in found["ParentReadsUser"][0].msg
    assert "login_required" in found["NeedsLogin"][0].msg
    assert found["Public"] == []
    assert found["NotOptedIn"] == []
