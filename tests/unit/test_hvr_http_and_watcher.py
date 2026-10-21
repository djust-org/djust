"""Hot view replacement reaches a fresh page load, and the watcher sees every save.

Three gaps let a saved change to a view (its ``get_context_data``) and its
template go unserved until a restart:

1. The URLconf holds the callable ``as_view()`` returned at import, which
   closes over the class as it was then. ``importlib.reload`` made a new class
   for the open sockets, but every new HTTP request still built the old one.
2. An editor that saves atomically (writes a temp file, renames it over the
   original) produced events for the temp name only; the rename was ignored,
   so the real module was never reloaded.
3. Saves inside one debounce window were reported as the first path only.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import types

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import clear_url_caches, resolve

from djust.dev_server import DjustFileChangeHandler
from djust.hot_view_replacement import reload_module_if_liveview

VIEW_SOURCE = """
from djust import LiveView


class PageView(LiveView):
    template = "<div dj-root>{{ label }}</div>"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["label"] = "LABEL-%s"
        return context
"""

URLS_SOURCE = """
from django.contrib.auth.decorators import login_not_required
from django.urls import path

from {views} import PageView

urlpatterns = [
    path("plain/", PageView.as_view()),
    path("wrapped/", login_not_required(PageView.as_view())),
]
"""


@pytest.fixture
def project():
    tmpdir = tempfile.mkdtemp(prefix="hvr_http_")
    suffix = os.path.basename(tmpdir).replace("-", "_")
    views_name = "hvr_http_views_%s" % suffix
    urls_name = "hvr_http_urls_%s" % suffix
    views_path = os.path.join(tmpdir, views_name + ".py")
    with open(views_path, "w") as f:
        f.write(textwrap.dedent(VIEW_SOURCE % "v1"))
    with open(os.path.join(tmpdir, urls_name + ".py"), "w") as f:
        f.write(textwrap.dedent(URLS_SOURCE.format(views=views_name)))
    sys.path.insert(0, tmpdir)
    importlib.invalidate_caches()
    try:
        with override_settings(ROOT_URLCONF=urls_name):
            clear_url_caches()
            yield types.SimpleNamespace(views_name=views_name, views_path=views_path)
    finally:
        clear_url_caches()
        sys.path.remove(tmpdir)
        sys.modules.pop(views_name, None)
        sys.modules.pop(urls_name, None)
        shutil.rmtree(tmpdir, ignore_errors=True)


def _get(url):
    request = RequestFactory().get(url)
    request.user = AnonymousUser()
    request.session = SessionStore()
    response = resolve(url).func(request)
    return response.content.decode()


def _save(path, version):
    with open(path, "w") as f:
        f.write(textwrap.dedent(VIEW_SOURCE % version))
    # A reload in the same second must not reuse the cached bytecode.
    stamp = time.time() + 2
    os.utime(path, (stamp, stamp))


@pytest.mark.django_db
class TestFreshRequestAfterReload:
    @pytest.mark.parametrize("url", ["/plain/", "/wrapped/"])
    def test_new_request_runs_the_reloaded_class(self, project, url):
        assert "LABEL-v1" in _get(url)
        _save(project.views_path, "v2")
        assert reload_module_if_liveview(project.views_path) is not None
        body = _get(url)
        assert "LABEL-v2" in body, body

    def test_view_class_attribute_follows_the_reload(self, project):
        old_cls = resolve("/plain/").func.view_class
        _save(project.views_path, "v2")
        assert reload_module_if_liveview(project.views_path) is not None
        new_cls = sys.modules[project.views_name].PageView
        assert new_cls is not old_cls
        assert resolve("/plain/").func.view_class is new_cls

    def test_second_reload_still_followed(self, project):
        assert "LABEL-v1" in _get("/plain/")
        for version in ("v2", "v3"):
            _save(project.views_path, version)
            reload_module_if_liveview(project.views_path)
        assert "LABEL-v3" in _get("/plain/")


class _Event:
    def __init__(self, src, dest=None):
        self.src_path = src
        self.dest_path = dest
        self.is_directory = False


def _collecting_handler():
    seen = []
    done = threading.Event()

    def callback(path):
        seen.append(path)
        done.set()

    handler = DjustFileChangeHandler(on_change_callback=callback)
    handler.DEBOUNCE_SECONDS = 0.05
    return handler, seen, done


def _settle(handler):
    deadline = time.time() + 3
    while handler._debounce_thread and handler._debounce_thread.is_alive():
        if time.time() > deadline:
            raise AssertionError("debounce thread did not finish")
        time.sleep(0.01)


class TestWatcher:
    def test_atomic_save_reports_the_renamed_file(self):
        handler, seen, _ = _collecting_handler()
        handler.on_created(_Event("/app/runs/.!12345!views.py"))
        handler.on_modified(_Event("/app/runs/.!12345!views.py"))
        handler.on_moved(_Event("/app/runs/.!12345!views.py", "/app/runs/views.py"))
        _settle(handler)
        assert seen == ["/app/runs/views.py"]

    @pytest.mark.parametrize(
        "name", [".!12345!views.py", ".#views.py", ".views.py.swp", "views.py~", "4913"]
    )
    def test_editor_temp_files_are_ignored(self, name):
        handler, _, _ = _collecting_handler()
        assert handler.should_reload_for_path("/app/runs/" + name) is False

    def test_every_file_of_a_burst_is_reported(self):
        handler, seen, _ = _collecting_handler()
        handler.on_modified(_Event("/app/runs/views.py"))
        handler.on_modified(_Event("/app/runs/templates/runs/run.html"))
        handler.on_modified(_Event("/app/runs/views.py"))
        _settle(handler)
        assert seen == ["/app/runs/views.py", "/app/runs/templates/runs/run.html"]

    def test_python_files_of_a_burst_come_first(self):
        handler, seen, _ = _collecting_handler()
        handler.on_modified(_Event("/app/runs/templates/runs/run.html"))
        handler.on_modified(_Event("/app/runs/views.py"))
        _settle(handler)
        assert seen == ["/app/runs/views.py", "/app/runs/templates/runs/run.html"]


TEMPLATE_VIEW_SOURCE = """
from djust import LiveView


class TemplatePageView(LiveView):
    template_name = "hvr_tpl_page.html"
"""


@pytest.fixture
def template_project(settings):
    tmpdir = tempfile.mkdtemp(prefix="hvr_tpl_")
    suffix = os.path.basename(tmpdir).replace("-", "_")
    views_name = "hvr_tpl_views_%s" % suffix
    urls_name = "hvr_tpl_urls_%s" % suffix
    with open(os.path.join(tmpdir, views_name + ".py"), "w") as f:
        f.write(textwrap.dedent(TEMPLATE_VIEW_SOURCE))
    with open(os.path.join(tmpdir, urls_name + ".py"), "w") as f:
        f.write(
            "from django.urls import path\n"
            "from %s import TemplatePageView\n"
            "urlpatterns = [path('tpl/', TemplatePageView.as_view())]\n" % views_name
        )
    template_path = os.path.join(tmpdir, "hvr_tpl_page.html")
    with open(template_path, "w") as f:
        f.write("<div dj-root>BODY-1</div>")
    # The backend the djust scaffold configures; it reads templates from disk.
    templates = [
        {"BACKEND": "djust.template.backend.DjustTemplateBackend", "DIRS": [tmpdir]},
        *settings.TEMPLATES,
    ]
    sys.path.insert(0, tmpdir)
    importlib.invalidate_caches()
    try:
        settings.TEMPLATES = templates
        settings.ROOT_URLCONF = urls_name
        clear_url_caches()
        yield types.SimpleNamespace(template_path=template_path)
    finally:
        clear_url_caches()
        sys.path.remove(tmpdir)
        sys.modules.pop(views_name, None)
        sys.modules.pop(urls_name, None)
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.mark.django_db
class TestTemplateOnlyChange:
    def _get(self, session):
        request = RequestFactory().get("/tpl/")
        request.user = AnonymousUser()
        request.session = session
        return resolve("/tpl/").func(request).content.decode()

    def test_same_session_sees_the_edited_template(self, template_project):
        from djust.hot_view_replacement import note_template_change

        session = SessionStore()
        assert "BODY-1" in self._get(session)
        with open(template_project.template_path, "w") as f:
            f.write("<div dj-root>BODY-2</div>")
        note_template_change(template_project.template_path)
        body = self._get(session)
        assert "BODY-2" in body, body
