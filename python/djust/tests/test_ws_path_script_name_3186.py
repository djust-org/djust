"""#3186 — under ``FORCE_SCRIPT_NAME`` the page tells the client to open its
WebSocket at ``<prefix>/ws/live/``, not the host root.

Goes through the real GET path: Django's ``WSGIHandler`` runs the request
handler, which sets the script prefix from ``FORCE_SCRIPT_NAME`` /
``SCRIPT_NAME`` exactly as the WSGI/ASGI handlers do, and the LiveView renders
a template carrying ``{% djust_client_config %}`` with the Rust
``DjustTemplateBackend``. The client half (``connect()`` uses the emitted
path) is ``tests/js/ws_path_script_name_3186.test.js``.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import pytest
from django.core.handlers.wsgi import WSGIHandler
from django.test import RequestFactory, override_settings
from django.urls import clear_url_caches, path, set_script_prefix

from djust import LiveView
from djust.utils import clear_template_dirs_cache

_TEMPLATE = """{% load live_tags %}<!doctype html>
<html><head>{% djust_client_config %}</head>
<body><div dj-root><p>{{ greeting }}</p></div></body></html>
"""


class PrefixedView3186(LiveView):
    template_name = "ws3186/page.html"

    def mount(self, request, **kwargs):
        self.greeting = "hi"

    def get_context_data(self, **kwargs):
        return {"greeting": self.greeting}


urlpatterns = [path("page/", PrefixedView3186.as_view())]

_WS_META_RE = re.compile(r'<meta name="djust-ws-path" content="([^"]*)">')


@pytest.fixture
def page_template():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "ws3186").mkdir()
    (tmp / "ws3186" / "page.html").write_text(_TEMPLATE)
    templates = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp)],
            "APP_DIRS": True,
            "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
        }
    ]
    with override_settings(
        TEMPLATES=templates, ROOT_URLCONF="djust.tests.test_ws_path_script_name_3186"
    ):
        clear_template_dirs_cache()
        clear_url_caches()
        yield
    clear_template_dirs_cache()
    clear_url_caches()
    set_script_prefix("/")
    shutil.rmtree(tmp, ignore_errors=True)


def _ws_path(html: str) -> str:
    match = _WS_META_RE.search(html)
    assert match is not None, html
    return match.group(1)


def _wsgi_get(path_info: str, **environ_extra: str) -> "tuple[str, str]":
    """GET through Django's real ``WSGIHandler``.

    Not ``django.test.Client``: its ``ClientHandler`` never calls
    ``set_script_prefix``, so under the test client every page renders as if
    root-mounted and a prefix bug is invisible. ``WSGIHandler.__call__`` sets
    the prefix from ``FORCE_SCRIPT_NAME`` / ``SCRIPT_NAME`` as production does.
    """
    environ = RequestFactory().get(path_info, **environ_extra).environ
    status: "list[str]" = []

    def start_response(s: str, headers: object, exc_info: object = None) -> None:
        status.append(s)

    body = b"".join(WSGIHandler()(environ, start_response))
    return status[0], body.decode("utf-8")


@pytest.mark.django_db
def test_get_under_force_script_name_emits_prefixed_ws_path(page_template):
    with override_settings(FORCE_SCRIPT_NAME="/app"):
        status, html = _wsgi_get("/page/")
    assert status.startswith("200"), html
    assert _ws_path(html) == "/app/ws/live/"


@pytest.mark.django_db
def test_get_at_root_emits_default_ws_path(page_template):
    status, html = _wsgi_get("/page/")
    assert status.startswith("200"), html
    assert _ws_path(html) == "/ws/live/"


@pytest.mark.django_db
def test_get_under_script_name_emits_prefixed_ws_path(page_template):
    """A proxy that mounts the app at /app/ passes ``SCRIPT_NAME`` instead."""
    status, html = _wsgi_get("/page/", SCRIPT_NAME="/app")
    assert status.startswith("200"), html
    assert _ws_path(html) == "/app/ws/live/"
