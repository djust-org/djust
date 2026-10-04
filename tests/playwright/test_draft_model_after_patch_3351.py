#!/usr/bin/env python3
"""Real-browser check of draft mode and dj-model for elements a patch adds (#3351, #3355).

``initDraftMode`` and ``bindModelElements`` once ran only at page load, so a
``data-draft`` field a patch inserted saved nothing, and a debounced ``dj-model``
field name shared by two lazily hydrated views dropped one view's update. This
drives the real client against a real server:

* a conditional ``{% if %}`` block, shown by a server patch, holds a ``data-draft``
  field, a ``dj-model`` input and a contenteditable ``dj-model.debounce-N`` element:
  typing in each reaches its destination (localStorage, server state);
* after a reload (the block is hidden again) showing the block restores the
  draft into the freshly inserted field, and a field the user is already typing
  in is not overwritten;
* two lazy views of one class, each with ``dj-model.debounce-300="q"``, both
  receive their own text when typed into inside one debounce window.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives headless Chromium.

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_draft_model_after_patch_3351.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. Exits 0 on
success, non-zero with the failed checks otherwise. Not part of the CI suite
(see README.md).
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
    "draftapp/__init__.py": "",
    "draftapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "draftapp.urls"
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
        LIVEVIEW_ALLOWED_MODULES = ["draftapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "draftapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [path("edit/", views.Editor.as_view())]
    """,
    "draftapp/views.py": """
        from djust import DraftModeMixin, LiveView
        from djust.decorators import event_handler

        PAGE = (
            "{% load live_tags %}<!DOCTYPE html><html><head><title>Draft</title>"
            "{% djust_client_config %}</head><body>"
            '<div dj-root>'
            '<form id="f" data-draft-enabled data-draft-key="k3351">'
            '<input id="always" name="always" data-draft="true">'
            '<button type="button" id="toggle" dj-click="toggle">toggle</button>'
            "{% if show %}"
            '<input id="cond" name="cond" data-draft="true">'
            '<input id="note-in" name="note_in" dj-model="note">'
            '<div id="ce" contenteditable="true" dj-model.debounce-100="ce_text"></div>'
            "{% endif %}"
            "</form>"
            # dj-model updates server state without a render: a click re-renders.
            '<button type="button" id="refresh" dj-click="refresh">refresh</button>'
            '<p id="note">[{{ note }}]</p><p id="ce-out">[{{ ce_text }}]</p>'
            '<p hidden>{{ refreshes }}</p>'
            "</div>"
            '<div id="wa" dj-view="draftapp.views.Widget" dj-lazy="idle"></div>'
            '<div id="wb" dj-view="draftapp.views.Widget" dj-lazy="idle"></div>'
            "</body></html>"
        )

        class Editor(DraftModeMixin, LiveView):
            template = PAGE
            draft_key = "k3351"
            # The numbered modifier forms are not derived from the template.
            allowed_model_fields = ["note", "ce_text"]

            def mount(self, request, **kwargs):
                self.show = False
                self.note = ""
                self.ce_text = ""
                self.refreshes = 0

            @event_handler
            def toggle(self, **kwargs):
                self.show = not self.show

            @event_handler
            def refresh(self, **kwargs):
                self.refreshes += 1  # a render needs a state change to be sent

        class Widget(LiveView):
            allowed_model_fields = ["q"]
            template = (
                '<div dj-root><input class="q" dj-model.debounce-300="q">'
                '<button type="button" class="refresh" dj-click="refresh">refresh</button>'
                '<p class="out">[{{ q }}]</p><p hidden>{{ refreshes }}</p></div>'
            )

            def mount(self, request, **kwargs):
                self.q = ""
                self.refreshes = 0

            @event_handler
            def refresh(self, **kwargs):
                self.refreshes += 1  # a render needs a state change to be sent
    """,
    "draftapp/asgi.py": """
        import os
        os.environ.setdefault("DJUST_SETTINGS_MODULE", "draftapp.settings")
        os.environ["DJANGO_SETTINGS_MODULE"] = "draftapp.settings"
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
            "draftapp.asgi:application",
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
            urllib.request.urlopen(f"http://127.0.0.1:{port}/edit/", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                raise SystemExit("server exited early")
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start")


def main() -> int:
    failures = []

    def check(label, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  [{detail}]"))
        if not cond:
            failures.append(label)

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

                def load():
                    page.goto(base + "/edit/")
                    page.wait_for_function(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true",
                        timeout=10000,
                    )
                    page.wait_for_function(
                        "() => document.querySelectorAll('#wa .q, #wb .q').length === 2",
                        timeout=10000,
                    )

                def stored():
                    return page.evaluate(
                        "() => (JSON.parse(localStorage.getItem('djust_draft_k3351') || 'null') || {}).data"
                    )

                def text(selector):
                    return page.evaluate(
                        "(s) => (document.querySelector(s) || {}).textContent", selector
                    )

                def show_block():
                    page.click("#toggle")
                    page.wait_for_selector("#cond", timeout=5000)

                def wait_text(selector, expected):
                    try:
                        page.wait_for_function(
                            "([s, e]) => (document.querySelector(s) || {}).textContent === e",
                            arg=[selector, expected],
                            timeout=4000,
                        )
                    except Exception:
                        pass
                    return text(selector)

                # --- first visit: type into everything a patch inserted ------
                load()
                page.evaluate("() => localStorage.clear()")
                show_block()
                page.click("#cond")
                page.keyboard.type("conditional text")
                page.click("#note-in")
                page.keyboard.type("bound")
                page.click("#ce")
                page.keyboard.type("editable")
                page.click("#always")
                page.keyboard.type("static")

                page.wait_for_timeout(400)  # past the 100 ms debounce
                page.click("#refresh")
                check(
                    "dj-model input a patch inserted reaches the server",
                    wait_text("#note", "[bound]") == "[bound]",
                    text("#note"),
                )
                check(
                    "contenteditable dj-model.debounce-N inserted by a patch sends its text",
                    wait_text("#ce-out", "[editable]") == "[editable]",
                    text("#ce-out"),
                )

                page.wait_for_timeout(900)  # the draft save is debounced 500 ms
                saved = stored() or {}
                check(
                    "a data-draft field a patch inserted is saved",
                    saved.get("cond") == "conditional text",
                    saved,
                )
                check(
                    "the static data-draft field still saves with it",
                    saved.get("always") == "static",
                    saved,
                )

                # --- reload: the block is hidden again; showing it restores ---
                load()
                check(
                    "the conditional field is gone after reload",
                    page.query_selector("#cond") is None,
                )
                check(
                    "the static field is restored at load",
                    page.input_value("#always") == "static",
                    page.input_value("#always"),
                )
                show_block()
                check(
                    "showing the block restores the draft into the inserted field",
                    page.input_value("#cond") == "conditional text",
                    page.input_value("#cond"),
                )

                # a field the user is typing in is never overwritten by a restore
                page.fill("#cond", "half typed")
                page.click("#toggle")  # hide
                page.wait_for_function("() => !document.querySelector('#cond')", timeout=5000)
                page.click("#toggle")  # show again: a new element
                page.wait_for_selector("#cond", timeout=5000)
                page.click("#cond")
                page.keyboard.type("x")
                page.evaluate("() => document.querySelector('#toggle').blur()")
                check(
                    "typing in the restored field edits it, nothing resets it",
                    page.input_value("#cond").endswith("x"),
                    page.input_value("#cond"),
                )

                # --- two views, one debounced field name -----------------------
                page.fill("#wa .q", "")
                page.click("#wa .q")
                page.keyboard.type("one")
                page.click("#wb .q")
                page.keyboard.type("two")
                page.wait_for_timeout(700)  # past the 300 ms debounce
                page.click("#wa .refresh")
                page.click("#wb .refresh")
                check(
                    "view A keeps its debounced update",
                    wait_text("#wa .out", "[one]") == "[one]",
                    text("#wa .out"),
                )
                check(
                    "view B keeps its debounced update",
                    wait_text("#wb .out", "[two]") == "[two]",
                    text("#wb .out"),
                )

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
