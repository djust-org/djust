#!/usr/bin/env python3
"""Real-browser check that Escape inside a dialog only uses an explicit close control.

The built-in keyboard handling used to press Escape in any ``role="dialog"`` by
dispatching the first ``dj-click`` control it found (a Delete button, a Next
button that needs its value) and acted again on keys a component's own handler
had already taken. This drives dialogs against a real LiveView over WebSocket
and reads back, from the server, exactly which events arrived:

* a dialog whose first control is Delete and which has ``data-dj-close``
  Cancel: Escape sends only the close event;
* a dialog with no close control: Escape sends nothing;
* the stock ``.dj-modal__close`` is still used, and Tab still wraps;
* a dialog whose own handler takes Escape (preventDefault, then presses its
  own button): exactly one event reaches the server, and its value arrives.

Self-contained: builds a one-page LiveView project in a temp directory, serves
it with uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_keyboard_nav_escape.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. Exits 0 on
success, non-zero with the failures. Not part of the CI suite (see README.md).
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
    "navapp/__init__.py": "",
    "navapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "navapp.urls"
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
        LIVEVIEW_ALLOWED_MODULES = ["navapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "navapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [path("", views.Page.as_view())]
    """,
    "navapp/views.py": '''
        from djust import LiveView
        from djust.decorators import event_handler

        BODY = """
        <div dj-root>
          <p>events: <span id="log">{{ log }}</span></p>
          <button id="open-a" dj-click="show" data-value="a">a</button>
          <button id="open-b" dj-click="show" data-value="b">b</button>
          <button id="open-c" dj-click="show" data-value="c">c</button>
          <button id="open-d" dj-click="show" data-value="d">d</button>
          {% if which == "a" %}
          <div role="dialog" aria-modal="true" id="dlg-a">
            <button id="a-del" dj-click="delete_everything">Delete</button>
            <button id="a-cancel" data-dj-close dj-click="close_a">Cancel</button>
          </div>
          {% endif %}
          {% if which == "b" %}
          <div role="dialog" aria-modal="true" id="dlg-b">
            <button id="b-del" dj-click="delete_everything">Delete</button>
            <button id="b-next" dj-click="step" data-value="next">Next</button>
          </div>
          {% endif %}
          {% if which == "c" %}
          <div class="dj-modal" role="dialog" aria-modal="true" id="dlg-c">
            <button id="c-del" dj-click="delete_everything">Delete</button>
            <button class="dj-modal__close" id="c-close" dj-click="close_c">x</button>
            <button id="c-last">last</button>
          </div>
          {% endif %}
          {% if which == "d" %}
          <div role="dialog" aria-modal="true" id="dlg-d">
            <button id="d-first" dj-click="step" data-value="skip">Skip</button>
            <button id="d-last" dj-click="step" data-value="next">Next</button>
          </div>
          {% endif %}
        </div>
        """

        TEMPLATE = (
            "{% load live_tags %}<!DOCTYPE html><html><head><title>t</title>{% djust_client_config %}</head><body>"
            + BODY
            + """<script>
              // A component's own key handling: takes Escape and Tab, then presses its own button.
              document.addEventListener('keydown', (e) => {
                const d = document.getElementById('dlg-d');
                if (!d || !d.contains(e.target)) return;
                if (e.key === 'Escape') { e.preventDefault(); document.getElementById('d-first').click(); }
                if (e.key === 'Tab') { e.preventDefault(); document.getElementById('d-last').focus(); }
              }, false);
            </script></body></html>"""
        )

        class Page(LiveView):
            template = TEMPLATE

            def mount(self, request, **kwargs):
                self.which = ""
                self.log = ""

            def _note(self, text):
                self.log = (self.log + " " + text).strip()

            @event_handler()
            def show(self, value="", **kwargs):
                self.which = str(value)
                self.log = ""

            @event_handler()
            def delete_everything(self, **kwargs):
                self._note("DELETE")

            @event_handler()
            def close_a(self, **kwargs):
                self._note("close_a")
                self.which = ""

            @event_handler()
            def close_c(self, **kwargs):
                self._note("close_c")
                self.which = ""

            @event_handler()
            def step(self, value="", **kwargs):
                self._note("step:" + repr(value))
    ''',
    "navapp/asgi.py": """
        import os
        os.environ.setdefault("DJUST_SETTINGS_MODULE", "navapp.settings")
        os.environ["DJANGO_SETTINGS_MODULE"] = "navapp.settings"
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
            "navapp.asgi:application",
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
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
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
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                page = browser.new_page()
                sent = []
                page.on(
                    "websocket",
                    lambda ws: ws.on(
                        "framesent",
                        lambda f: sent.append(str(f)) if '"type":"event"' in str(f) else None,
                    ),
                )

                def check(ok, what):
                    if not ok:
                        failures.append(what)
                    print(("ok   " if ok else "FAIL ") + what)

                page.goto(f"http://localhost:{port}/")
                page.wait_for_function(
                    "() => window.djust && window.djust.liveViewInstance && "
                    "window.djust.liveViewInstance.viewMounted === true",
                    timeout=15000,
                )
                page.wait_for_timeout(300)

                def events_since(mark):
                    return [s for s in sent[mark:] if '"event":"show"' not in s]

                def log():
                    return (page.text_content("#log") or "").strip()

                def open_dialog(which, focus_id):
                    page.evaluate(f"() => document.getElementById('open-{which}').click()")
                    page.wait_for_selector(f"#dlg-{which}", timeout=6000)
                    page.wait_for_timeout(300)
                    page.focus(f"#{focus_id}")

                # a: first control is Delete, with a marked Cancel
                open_dialog("a", "a-del")
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                got = [s for s in events_since(mark)]
                check(
                    len(got) == 1 and '"event":"close_a"' in got[0],
                    f"marked dialog: Escape sends only the close event ({[g[:60] for g in got]})",
                )
                check("DELETE" not in log(), "marked dialog: the Delete control was not pressed")

                # b: no close control at all
                open_dialog("b", "b-del")
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                check(not events_since(mark), "no close control: Escape sends nothing")
                check(
                    page.evaluate("() => !!document.getElementById('dlg-b')")
                    and "DELETE" not in log(),
                    "no close control: the dialog stays and Delete was not pressed",
                )

                # c: stock close button, Tab still wraps
                open_dialog("c", "c-last")
                page.keyboard.press("Tab")
                check(
                    page.evaluate("() => document.activeElement.id") == "c-del",
                    "stock modal: Tab on the last control wraps to the first",
                )
                page.focus("#c-last")
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                got = events_since(mark)
                check(
                    len(got) == 1 and '"event":"close_c"' in got[0],
                    "stock modal: Escape still sends the close event, once",
                )

                # d: a component that handles Escape and Tab itself
                open_dialog("d", "d-first")
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                got = events_since(mark)
                check(
                    len(got) == 1 and '"event":"step"' in got[0] and '"value":"skip"' in got[0],
                    f"own handler: Escape reaches the server once, with its value ({[g[:80] for g in got]})",
                )
                page.focus("#d-first")
                page.keyboard.press("Tab")
                check(
                    page.evaluate("() => document.activeElement.id") == "d-last",
                    "own handler: its Tab handling is not undone by the dialog trap",
                )
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    if failures:
        print("\n%d FAILED:\n  - " % len(failures) + "\n  - ".join(failures))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
