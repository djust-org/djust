"""Hot view replacement follows an edit to a class defined in another module.

A view's behaviour often lives in a mixin or base class in a module of its
own. Saving that module used to change nothing: it defines no LiveView, so it
was not reloaded, and even when it was, the views in other modules kept
inheriting from the class object they were built with. Now the module is
reloaded and so is every module defining a LiveView whose MRO contains one of
its classes (subclasses of those views in further modules too, bases before
subclasses). A fresh page load, a new socket mount and a mounted view all run
the new code.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import textwrap
import time
import types

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import clear_url_caches, resolve

from djust.hot_view_replacement import (
    apply_class_swap,
    reload_module_if_liveview,
    resolve_reload_pairs,
)

MIXIN_SOURCE = """
class LabelMixin:
    def label(self):
        return "LABEL-%s"
"""

VIEW_SOURCE = """
from djust import LiveView

from {mixins} import LabelMixin


class PageView(LabelMixin, LiveView):
    template = "<div dj-root>{{{{ label }}}}</div>"

    def mount(self, request, **kwargs):
        self.count = 7

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["label"] = self.label()
        return context
"""

SUB_SOURCE = """
from {views} import PageView


class SubPageView(PageView):
    template = "<div dj-root>sub {{{{ label }}}}</div>"
"""

URLS_SOURCE = """
from django.urls import path

from {views} import PageView
from {sub} import SubPageView

urlpatterns = [
    path("page/", PageView.as_view()),
    path("sub/", SubPageView.as_view()),
]
"""


def _write(path, source):
    with open(path, "w") as f:
        f.write(textwrap.dedent(source))
    # A rewrite within the same second must not reuse the cached bytecode.
    stamp = time.time() + 2
    os.utime(path, (stamp, stamp))


@pytest.fixture
def project():
    tmpdir = tempfile.mkdtemp(prefix="hvr_xmod_")
    tag = os.path.basename(tmpdir).replace("-", "_")
    names = types.SimpleNamespace(
        mixins="hvr_xmod_mixins_%s" % tag,
        views="hvr_xmod_views_%s" % tag,
        sub="hvr_xmod_sub_%s" % tag,
        urls="hvr_xmod_urls_%s" % tag,
    )
    mixin_path = os.path.join(tmpdir, names.mixins + ".py")
    _write(mixin_path, MIXIN_SOURCE % "v1")
    _write(os.path.join(tmpdir, names.views + ".py"), VIEW_SOURCE.format(mixins=names.mixins))
    _write(os.path.join(tmpdir, names.sub + ".py"), SUB_SOURCE.format(views=names.views))
    _write(
        os.path.join(tmpdir, names.urls + ".py"),
        URLS_SOURCE.format(views=names.views, sub=names.sub),
    )
    sys.path.insert(0, tmpdir)
    importlib.invalidate_caches()
    try:
        with override_settings(ROOT_URLCONF=names.urls):
            clear_url_caches()
            resolve("/page/")  # import the URLconf, and through it every module
            yield types.SimpleNamespace(names=names, mixin_path=mixin_path)
    finally:
        clear_url_caches()
        sys.path.remove(tmpdir)
        for name in vars(names).values():
            sys.modules.pop(name, None)
        shutil.rmtree(tmpdir, ignore_errors=True)


def _get(url):
    request = RequestFactory().get(url)
    request.user = AnonymousUser()
    request.session = SessionStore()
    return resolve(url).func(request).content.decode()


def _edit_mixin(project, version="v2"):
    _write(project.mixin_path, MIXIN_SOURCE % version)
    return reload_module_if_liveview(project.mixin_path)


@pytest.mark.django_db
class TestFreshPageLoad:
    def test_view_using_the_mixin_serves_the_edit(self, project):
        assert "LABEL-v1" in _get("/page/")
        assert _edit_mixin(project) is not None
        body = _get("/page/")
        assert "LABEL-v2" in body, body

    def test_subclass_in_a_third_module_serves_the_edit(self, project):
        assert "sub LABEL-v1" in _get("/sub/")
        _edit_mixin(project)
        body = _get("/sub/")
        assert "sub LABEL-v2" in body, body

    def test_subclass_inherits_the_reloaded_view(self, project):
        _edit_mixin(project)
        views = sys.modules[project.names.views]
        sub = sys.modules[project.names.sub]
        assert issubclass(sub.SubPageView, views.PageView)
        assert sys.modules[project.names.mixins].LabelMixin in views.PageView.__mro__

    def test_two_edits_in_a_row(self, project):
        _edit_mixin(project, "v2")
        _edit_mixin(project, "v3")
        assert "LABEL-v3" in _get("/page/")
        assert "sub LABEL-v3" in _get("/sub/")

    def test_result_names_every_rebuilt_class(self, project):
        result = _edit_mixin(project)
        rebuilt = {(new.__module__, new.__name__) for _, new in result.class_pairs}
        assert (project.names.views, "PageView") in rebuilt
        assert (project.names.sub, "SubPageView") in rebuilt

    def test_unrelated_module_is_not_reloaded(self, project):
        before = sys.modules["djust.live_view"]
        _edit_mixin(project)
        assert sys.modules["djust.live_view"] is before


@pytest.mark.django_db
class TestMountedView:
    def test_mounted_instance_swaps_to_the_rebuilt_class(self, project):
        views = sys.modules[project.names.views]
        view = views.PageView()
        view.count = 7
        result = _edit_mixin(project)
        meta = {
            "module": result.module_name,
            "class_names": [new.__name__ for _, new in result.class_pairs],
            "modules": result.modules,
        }
        pairs = resolve_reload_pairs(meta)
        ok, reason = apply_class_swap(view, pairs)
        assert ok, reason
        assert view.label() == "LABEL-v2"
        assert view.count == 7


def _session():
    session = SessionStore()
    session.save()
    return session


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_new_socket_mount_serves_the_edit(project):
    from djust.websocket import LiveViewConsumer

    await sync_to_async(_edit_mixin)(project)
    with override_settings(
        DEBUG=False,
        DJUST_TENANTS=None,
        DJUST_CONFIG={},
        LIVEVIEW_ALLOWED_MODULES=[project.names.views],
    ):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(
            session=await sync_to_async(_session)(), user=AnonymousUser(), tenant=None
        )
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        await socket.send_json_to(
            {"type": "mount", "view": project.names.views + ".PageView", "url": "/page/"}
        )
        frame = await socket.receive_json_from(timeout=5)
        await socket.disconnect()
    assert frame["type"] == "mount", frame
    assert "LABEL-v2" in frame["html"], frame["html"]
