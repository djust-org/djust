#!/usr/bin/env python3
"""Real-browser check of the page-shell fallback (#3036).

A live navigation to a page whose page shell differs must become a full page
load. The fragment case is the one a jsdom test cannot see: ``pushState`` has
already moved the address bar to ``/c/#sec`` when the server's mount reply
arrives, and ``location.replace('/c/#sec')`` from there is a same-document
FRAGMENT navigation, so nothing loads and the reader is stranded on the old
content under the new URL. The client must reload instead.

Self-contained: builds a three-page LiveView project in a temp directory,
serves it with uvicorn, and drives it with headless Chromium.

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_page_shell_fallback_3036.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary when Playwright's
own download is not the one installed. ``DJUST_SERVER_PYTHON`` is the interpreter that has djust, channels and uvicorn
installed (default: this interpreter). Exits 0 on success, non-zero with a
message on failure. Not part of the CI suite (see README.md).
"""

import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT = {
    "shellapp/__init__.py": "",
    "shellapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "shellapp.urls"
        INSTALLED_APPS = [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "channels", "djust",
        ]
        MIDDLEWARE = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ]
        SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        STATIC_URL = "/static/"
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        LIVEVIEW_ALLOWED_MODULES = ["shellapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "shellapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [
            path("a/", views.A.as_view()),
            path("b/", views.B.as_view()),
            path("c/", views.C.as_view()),
        ]
    """,
    "shellapp/views.py": """
        from djust import LiveView

        NAV = (
            '<nav><a id="to-a" dj-navigate="/a/">a</a><a id="to-b" dj-navigate="/b/">b</a>'
            '<a id="to-b-hash" dj-navigate="/b/#sec">b#sec</a>'
            '<a id="to-c" dj-navigate="/c/">c</a>'
            '<a id="to-c-hash" dj-navigate="/c/#sec">c#sec</a>'
            '<a id="to-c-query-hash" dj-navigate="/c/?q=1#sec">c?q#sec</a></nav>'
        )

        def page(name, head, tail=""):
            return (
                "{% load live_tags %}<!DOCTYPE html><html><head><title>" + name + "</title>"
                "{% djust_client_config %}" + head + "</head><body>"
                '<div dj-root><h1 id="which">' + name + "</h1>" + NAV + "<div id='sec'>sec</div></div>"
                + tail + "</body></html>"
            )

        SHELL_X = '<link rel="stylesheet" href="/static/x.css">'
        SHELL_Y = '<link rel="stylesheet" href="/static/y.css"><script src="/static/y.js"></script>'

        class A(LiveView):
            template = page("A", SHELL_X)

        class B(LiveView):
            template = page("B", SHELL_X)

        class C(LiveView):
            template = page("C", SHELL_Y)
    """,
    "shellapp/asgi.py": """
        import os
        os.environ.setdefault("DJUST_SETTINGS_MODULE", "shellapp.settings")
        os.environ["DJANGO_SETTINGS_MODULE"] = "shellapp.settings"
        from django.core.asgi import get_asgi_application
        django_app = get_asgi_application()
        from channels.routing import ProtocolTypeRouter, URLRouter
        from channels.security.websocket import AllowedHostsOriginValidator
        from channels.auth import AuthMiddlewareStack
        from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
        from django.urls import path
        from djust.websocket import LiveViewConsumer

        application = ProtocolTypeRouter({
            "http": ASGIStaticFilesHandler(django_app),
            "websocket": AllowedHostsOriginValidator(
                AuthMiddlewareStack(URLRouter([path("ws/live/", LiveViewConsumer.as_asgi())]))
            ),
        })
    """,
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(root: Path, port: int) -> subprocess.Popen:
    for name, body in PROJECT.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
    env = {**os.environ, "PYTHONPATH": str(root)}
    py = os.environ.get("DJUST_SERVER_PYTHON", sys.executable)
    proc = subprocess.Popen(
        [
            py,
            "-m",
            "uvicorn",
            "shellapp.asgi:application",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=root,
        env=env,
    )
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/a/", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                raise SystemExit("server exited early")
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start")


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        server = start_server(Path(tmp), port)
        base = f"http://localhost:{port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                page = browser.new_page()

                def load(path):
                    page.goto(base + path)
                    page.wait_for_function(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true",
                        timeout=10000,
                    )
                    page.evaluate("() => { window.__marker = 'alive'; }")

                def marker():
                    return page.evaluate("() => window.__marker || null")

                def which():
                    return page.evaluate("() => document.getElementById('which').textContent")

                def settle(expected_which):
                    page.wait_for_function(
                        "(w) => document.getElementById('which') && "
                        "document.getElementById('which').textContent === w",
                        arg=expected_which,
                        timeout=10000,
                    )
                    page.wait_for_function(
                        "() => window.djust.liveViewInstance.viewMounted === true", timeout=10000
                    )

                def check(label, cond):
                    print(("ok   " if cond else "FAIL ") + label)
                    if not cond:
                        failures.append(label)

                # Same shell: stays on the fast path (the document survives).
                load("/a/")
                page.click("#to-b")
                settle("B")
                check("same shell, no fragment: no reload", marker() == "alive")
                check("same shell: url moved", page.url.endswith("/b/"))

                load("/a/")
                page.click("#to-b-hash")
                settle("B")
                check("same shell with fragment: no reload", marker() == "alive")

                # Different shell: a real load, with and without a fragment.
                for link, url_tail in (
                    ("#to-c", "/c/"),
                    ("#to-c-hash", "/c/#sec"),
                    ("#to-c-query-hash", "/c/?q=1#sec"),
                ):
                    load("/a/")
                    page.click(link)
                    try:
                        page.wait_for_function(
                            "() => document.getElementById('which') && "
                            "document.getElementById('which').textContent === 'C' && "
                            "!window.__marker",
                            timeout=5000,
                        )
                    except Exception:
                        pass  # reported by the checks below
                    check(
                        f"different shell {url_tail}: the document was reloaded", marker() is None
                    )
                    check(f"different shell {url_tail}: url kept", page.url.endswith(url_tail))
                    check(
                        f"different shell {url_tail}: destination stylesheet present",
                        page.evaluate(
                            "() => !!document.querySelector('link[href=\"/static/y.css\"]')"
                        ),
                    )

                # Back/forward onto a hashed entry whose shell differs from the
                # document's (a deploy changed the page, or the entry predates a
                # shell change). Simulated by changing the document's own
                # fingerprint, because every entry in one document otherwise
                # shares its shell.
                load("/a/")
                page.click("#to-b-hash")
                settle("B")
                page.click("#to-a")  # a second entry on another path, so Back remounts
                settle("A")
                page.evaluate(
                    "() => document.querySelector('meta[name=djust-page-shell]')"
                    ".setAttribute('content', 'stale0stale0stale')"
                )
                page.go_back()
                try:
                    page.wait_for_function("() => !window.__marker", timeout=5000)
                except Exception:
                    pass  # reported by the checks below
                check("back onto a hashed entry with a different shell: reloaded", marker() is None)
                check("back onto a hashed entry: url kept", page.url.endswith("/b/#sec"))
                check("back onto a hashed entry: destination content", which() == "B")

                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
