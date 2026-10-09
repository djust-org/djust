#!/usr/bin/env python3
"""Self-contained HTTP-only browser regression for attribute-form lazy views.

Run with .venv/bin/python; optionally set CHROMIUM_EXECUTABLE. Creates a
scratch Django project and terminates its server in finally. Both WebSocket
and EventSource are disabled by _transports.INIT and their absence verified.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright
from _transports import INIT, TransportWatch

ROOT = Path(__file__).resolve().parents[2]
PROJECT = {
    "lazyfixture/__init__.py": "",
    "lazyfixture/settings.py": """
SECRET_KEY = "local-browser-test-3252"
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
ROOT_URLCONF = "lazyfixture.urls"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.staticfiles", "djust", "djust.components", "lazyfixture"]
MIDDLEWARE = ["django.contrib.sessions.middleware.SessionMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.middleware.csrf.CsrfViewMiddleware"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
STATIC_URL = "/static/"
LIVEVIEW_CONFIG = {"hot_reload_auto_enable": False}
DJUST_LIVE_RENDER_ALLOWED_MODULES = ["lazyfixture.views"]
TEMPLATES = [{"BACKEND": "djust.template_backend.DjustTemplateBackend", "APP_DIRS": True}]
""",
    "lazyfixture/urls.py": """
from django.urls import path
from .views import Page
urlpatterns = [path("", Page.as_view())]
""",
    "lazyfixture/views.py": """
from djust import LiveView
from djust.decorators import event_handler
class Child(LiveView):
    exposure_policy = "legacy"
    template = '<div><span class="count">{{ count }}</span><button dj-click="inc">Increment</button></div>'
    def mount(self, request, **kwargs):
        self.count = 0
    @event_handler
    def inc(self, **kwargs):
        self.count += 1
class Page(LiveView):
    exposure_policy = "legacy"
    template = '<!DOCTYPE html><html><head></head><body><main dj-root><div id="one" dj-view="lazyfixture.views.Child" dj-lazy="click">Load first</div><div id="two" class="{{ extra }}" dj-view="lazyfixture.views.Child" dj-lazy="click">Load second</div></main></body></html>'
    def mount(self, request, **kwargs):
        self.extra = "unrelated-dynamic-class"
""",
    "lazyfixture/asgi.py": """
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lazyfixture.settings")
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
application = ASGIStaticFilesHandler(get_asgi_application())
""",
}


def main():
    with tempfile.TemporaryDirectory(prefix="djust-lazy-3252-") as temp:
        root = Path(temp)
        for name, content in PROJECT.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "python") + os.pathsep + str(root))
        log_path = ROOT / "context/terminal/browser-server-3252.log"
        with log_path.open("w") as log:
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "lazyfixture.asgi:application",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                url = "http://127.0.0.1:%d/" % port
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise AssertionError("Server exited; inspect " + str(log_path))
                    try:
                        urllib.request.urlopen(url, timeout=1).close()
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("Server startup timed out")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE")
                    )
                    try:
                        context = browser.new_context()
                        context.add_init_script(INIT["http"])
                        page = context.new_page()
                        watch = TransportWatch(page)
                        errors = []
                        page.on("pageerror", lambda exc: errors.append(str(exc)))
                        page.goto(url)
                        page.wait_for_function("() => !!window.djust?.handleEvent")
                        ids = page.locator("[data-djust-lazy-id]").evaluate_all(
                            "els => els.map(el => el.getAttribute('data-djust-lazy-id'))"
                        )
                        assert len(ids) == 2 and len(set(ids)) == 2, ids
                        assert page.locator(".count").count() == 0
                        for target in ("one", "two"):
                            page.click("#" + target)
                            page.locator("#" + target + " .count").wait_for()
                        for target, expected in (("one", 1), ("two", 1), ("one", 2), ("two", 2)):
                            page.click("#" + target + " button")
                            page.wait_for_function(
                                "([id,n]) => document.querySelector('#'+id+' .count')?.textContent === String(n)",
                                arg=[target, expected],
                            )
                        assert page.locator("#one .count").inner_text() == "2"
                        assert page.locator("#two .count").inner_text() == "2"
                        # An independent browser session gets different addresses
                        # and counters; it cannot hydrate the first session's id.
                        other = browser.new_context()
                        other.add_init_script(INIT["http"])
                        second = other.new_page()
                        second.goto(url)
                        second.wait_for_function("() => !!window.djust?.handleEvent")
                        other_ids = second.locator("[data-djust-lazy-id]").evaluate_all(
                            "els => els.map(el => el.getAttribute('data-djust-lazy-id'))"
                        )
                        assert not set(ids) & set(other_ids)
                        response = other.request.post(
                            url,
                            data={"event": "djust_lazy_mount", "params": {"view_id": ids[0]}},
                            headers={
                                "X-CSRFToken": next(
                                    c["value"] for c in other.cookies() if c["name"] == "csrftoken"
                                )
                            },
                        )
                        assert response.status == 400
                        failures = []
                        watch.check("http", "attribute lazy", failures)
                        assert not failures, failures
                        assert not errors, errors
                        print(
                            "PASS: HTTP-only, two lazy mounts, four isolated events (2/2), cross-session ID refused; no WebSocket/SSE"
                        )
                    finally:
                        browser.close()
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
