#!/usr/bin/env python3
"""Real-browser check of the combobox's Escape and Tab inside dialogs (N-4f).

jsdom models neither a native <dialog>'s own Escape (cancel -> close) nor the
browser's focus order on Tab, so these are driven in headless Chromium against
a real LiveView over WebSocket:

* ``{% modal native=True %}`` holding an app-level ``dj-window-keydown.escape``
  binding: with the combobox list open, Escape closes the list and puts the
  chosen label back; the dialog stays open, no event reaches the server. A
  second Escape (list closed) closes the dialog and reaches the binding.
* a sheet with an app-level ``dj-window-keydown.escape`` close binding: the
  first Escape (list open) does not reach the binding; the second does.
* typing an exact label and pressing Tab once commits the option (the server
  is told), moves focus to the next control, and clears the list's filter.

Self-contained: builds a one-page LiveView project in a temp directory, serves
it with uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_combobox_escape_blur.py

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
    "navapp/views.py": """
        from djust import LiveView
        from djust.decorators import event_handler
        from djust.components.templatetags.djust_components import combobox, render_modal

        OPTIONS = [
            {"value": "a", "label": "Alpha"},
            {"value": "b", "label": "Beta"},
            {"value": "c", "label": "Gamma"},
        ]

        def box(value):
            return str(combobox(name="city", label="City", value=value, options=OPTIONS,
                                event="pick_city", id="city"))

        TEMPLATE = (
            "{% load live_tags %}<!DOCTYPE html><html><head><title>t</title>{% djust_client_config %}"
            "<style>.combobox-dropdown{display:none}"
            ".combobox-input:focus~.combobox-dropdown{display:block}"
            ".combobox--closed .combobox-dropdown{display:none!important}"
            ".sheet{display:none}.sheet[data-open]{display:block}</style>"
            "</head><body>"
            "<div dj-root>"
            "<p>events: <span id='log'>{{ log }}</span></p>"
            "<button id='open-modal' dj-click='show' data-value='modal'>modal</button>"
            "<button id='open-sheet' dj-click='show' data-value='sheet'>sheet</button>"
            "{% if which == 'modal' %}{{ modal_html|safe }}{% endif %}"
            "{% if which == 'sheet' %}"
            "<div class='sheet sheet-right' id='sheet' data-open='true' "
            "dj-window-keydown.escape='close_sheet'>"
            "<div class='sheet-body'>{{ box_html|safe }}<input id='after'></div></div>"
            "{% endif %}"
            "</div></body></html>"
        )

        class Page(LiveView):
            template = TEMPLATE

            def mount(self, request, **kwargs):
                self.which = ""
                self.log = ""
                self.city = "a"
                self._render_boxes()

            def _render_boxes(self):
                self.box_html = box(self.city)
                self.modal_html = str(render_modal(
                    {"title": "Pick", "native": True, "id": "dlg"},
                    # The app's own Escape-to-close binding, as the Point Guard
                    # port's dialog form has.
                    "<div dj-window-keydown.escape='app_escape'>"
                    + self.box_html + "<input id='after'></div>",
                ))

            def _note(self, text):
                self.log = (self.log + " " + text).strip()

            @event_handler()
            def show(self, value="", **kwargs):
                self.which = str(value)
                self.log = ""

            @event_handler()
            def pick_city(self, value="", **kwargs):
                self.city = str(value)
                self._render_boxes()
                self._note("pick:" + self.city)

            @event_handler()
            def close_modal(self, **kwargs):
                self._note("close_modal")
                self.which = ""

            @event_handler()
            def app_escape(self, **kwargs):
                self._note("app_escape")
                self.which = ""

            @event_handler()
            def close_sheet(self, **kwargs):
                self._note("close_sheet")
                self.which = ""
    """,
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
    # Keep a caller's PYTHONPATH (a worktree's python/ dir) after the project.
    pythonpath = os.pathsep.join(p for p in (str(root), os.environ.get("PYTHONPATH", "")) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath}
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

                def events_since(mark):
                    return [s for s in sent[mark:] if '"event":"show"' not in s]

                def names(frames):
                    out = []
                    for f in frames:
                        for name in ("pick_city", "close_modal", "close_sheet", "app_escape"):
                            if '"event":"%s"' % name in f:
                                out.append(name)
                    return out

                def load():
                    page.goto(f"http://localhost:{port}/")
                    page.wait_for_function(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true",
                        timeout=15000,
                    )
                    page.wait_for_timeout(300)

                def open_(which, selector):
                    page.evaluate(f"() => document.getElementById('open-{which}').click()")
                    page.wait_for_selector(selector, timeout=6000)
                    page.wait_for_timeout(400)
                    page.focus("#city")
                    page.wait_for_timeout(100)

                def list_open():
                    return page.evaluate(
                        "() => { const l = document.getElementById('city-listbox'); "
                        "return !!l && getComputedStyle(l).display !== 'none'; }"
                    )

                def city_text():
                    return page.evaluate(
                        "() => { const i = document.getElementById('city'); return i ? i.value : null; }"
                    )

                def visible_options():
                    return page.evaluate(
                        "() => Array.from(document.querySelectorAll('#city-listbox .combobox-option'))"
                        ".filter(o => o.style.display !== 'none').length"
                    )

                def dialog_open():
                    return page.evaluate(
                        "() => { const d = document.querySelector('dialog.dj-modal'); "
                        "return !!d && d.open; }"
                    )

                load()

                # 1. native <dialog> modal: Escape with the list open
                open_("modal", "dialog.dj-modal[open]")
                check(dialog_open() and list_open(), "modal: the dialog and the list are open")
                page.keyboard.type("Be")
                page.wait_for_timeout(200)
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                check(dialog_open(), "modal: Escape with the list open leaves the dialog open")
                check(not list_open(), "modal: Escape closes the list")
                check(
                    city_text() == "Alpha",
                    f"modal: Escape restores the chosen label ({city_text()!r})",
                )
                check(
                    names(events_since(mark)) == [],
                    f"modal: no event reaches the server ({names(events_since(mark))})",
                )
                check(
                    page.evaluate("() => document.activeElement.id") == "city",
                    "modal: focus stays on the input",
                )
                mark = len(sent)
                page.focus("#city") if dialog_open() else None
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                check(not dialog_open(), "modal: a second Escape (list closed) closes the dialog")
                check(
                    "app_escape" in names(events_since(mark)),
                    f"modal: the second Escape reaches the app's binding ({names(events_since(mark))})",
                )

                # 2. sheet with an app-level window Escape binding
                open_("sheet", "#sheet")
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                check(
                    names(events_since(mark)) == [] and page.query_selector("#sheet") is not None,
                    f"sheet: Escape with the list open does not reach the window binding "
                    f"({names(events_since(mark))})",
                )
                mark = len(sent)
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                check(
                    names(events_since(mark)) == ["close_sheet"],
                    f"sheet: the second Escape reaches the window binding ({names(events_since(mark))})",
                )

                # 3. type an exact label, press Tab once
                load()
                open_("modal", "dialog.dj-modal[open]")
                page.fill("#city", "")
                page.keyboard.type("Beta")
                page.wait_for_timeout(200)
                check(visible_options() == 1, "tab: typing filters the list")
                mark = len(sent)
                page.keyboard.press("Tab")
                page.wait_for_timeout(700)
                check(
                    page.evaluate("() => document.activeElement.id") == "after",
                    "tab: one Tab moves focus to the next control "
                    f"(focus on {page.evaluate('() => document.activeElement.id')!r})",
                )
                check(
                    names(events_since(mark)) == ["pick_city"]
                    and '"value":"b"' in "".join(events_since(mark)),
                    f"tab: the typed label is committed to the server ({names(events_since(mark))})",
                )
                check(visible_options() == 3, "tab: the list's filter is reset")
                check(
                    page.evaluate("() => document.getElementById('city-value').value") == "b",
                    "tab: the hidden value is the typed option's",
                )
                check(dialog_open(), "tab: the dialog stays open")
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
