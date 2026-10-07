#!/usr/bin/env python3
"""Every dialog djust ships still closes on Escape, with the server told.

The built-in keyboard handling presses Escape in a ``role="dialog"`` (or
``.dj-modal``) through the dialog's explicit close control: ``.dj-modal__close``
or a ``dj-click`` control marked ``data-dj-close``. This opens each shipped
dialog in a real LiveView over WebSocket WITHOUT any of the components' own hook
scripts on the page, presses Escape, and asserts the server received the
dialog's close event (and that nothing else was sent, and that the console did
not say Escape was ignored). A new dialog-like component that forgets its marker
fails here.

Dialogs: ``Modal`` (class), ``ImageLightbox`` (class and the ``{% lightbox %}``
tag, which is the Rust handler inside a LiveView), ``Tour`` (class and
``{% tour %}``), and the theming ``{% theme_modal %}``. ``{% modal %}``
renders ``modal-overlay`` without a dialog role and has no Escape handling.

Self-contained: builds a one-page project in a temp directory, serves it with
uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_shipped_dialogs_escape.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. Exits 0 on
success, non-zero with the failures. Not part of the CI suite (see README.md).
"""

import json
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
    "dlgapp/__init__.py": "",
    "dlgapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "dlgapp.urls"
        INSTALLED_APPS = [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "channels", "djust", "djust.components", "djust.theming",
        ]
        MIDDLEWARE = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ]
        SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        STATIC_URL = "/static/"
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        LIVEVIEW_ALLOWED_MODULES = ["dlgapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "dlgapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [path("", views.Page.as_view())]
    """,
    "dlgapp/views.py": """
        from djust import LiveView
        from djust.decorators import event_handler
        from djust.components.components.image_lightbox import ImageLightbox
        from djust.components.components.modal import Modal
        from djust.components.components.tour import Tour

        PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
        IMAGES = [{"src": PIXEL, "alt": "one"}, {"src": PIXEL, "alt": "two"}]
        STEPS = [
            {"target": "#nowhere", "title": "One", "content": "First."},
            {"target": "#nowhere", "title": "Two", "content": "Second."},
        ]
        VARIANTS = ["modal_class", "lightbox_class", "lightbox_tag", "tour_class", "tour_tag", "theme_modal"]

        TEMPLATE = (
            "{% load live_tags djust_components theme_components %}<!DOCTYPE html><html><head><title>t</title>"
            "{% djust_client_config %}</head><body><div dj-root>"
            '<p>which: <span id="which">{{ which }}</span> log: <span id="log">{{ log }}</span></p>'
            + "".join(
                f'<button id="open-{v}" dj-click="show" data-value="{v}">{v}</button>' for v in VARIANTS
            )
            + '{% if which == "modal_class" %}{{ modal_class|safe }}{% endif %}'
            + '{% if which == "lightbox_class" %}{{ lightbox_class|safe }}{% endif %}'
            + '{% if which == "lightbox_tag" %}{% lightbox images=images active=0 open=True close_event="close_lightbox" %}{% endif %}'
            + '{% if which == "tour_class" %}{{ tour_class|safe }}{% endif %}'
            + '{% if which == "tour_tag" %}{% tour steps=steps active=0 event="tour" %}{% endif %}'
            + '{% if which == "theme_modal" %}{% theme_modal id="tm" title="Themed" is_open=True %}{% endif %}'
            + "</div></body></html>"
        )

        class Page(LiveView):
            template = TEMPLATE

            def mount(self, request, **kwargs):
                self.which = ""
                self.log = ""
                self.images = IMAGES
                self.steps = STEPS
                self.modal_class = Modal(title="Stock", content="x", is_open=True, close_event="close_modal").render()
                self.lightbox_class = ImageLightbox(images=IMAGES, open=True, close_event="close_lightbox").render()
                self.tour_class = Tour(steps=STEPS, active=0, event="tour").render()

            def _note(self, text):
                self.log = (self.log + " " + text).strip()

            @event_handler()
            def show(self, value="", **kwargs):
                self.which = str(value)
                self.log = ""

            @event_handler()
            # No **kwargs on purpose: a click on the (marked) close control must
            # not send an argument the handler does not declare.
            def close_modal(self):
                self._note("close_modal")
                self.which = ""

            @event_handler()
            def close_lightbox(self):
                self._note("close_lightbox")
                self.which = ""

            @event_handler()
            def tour(self, value="", **kwargs):
                self._note("tour:" + repr(value))
                if value in ("skip", "finish"):
                    self.which = ""

            @event_handler()
            def toggle_modal(self, value="", **kwargs):
                self._note("toggle_modal:" + repr(value))
                self.which = ""
    """,
    "dlgapp/asgi.py": """
        import os
        os.environ.setdefault("DJUST_SETTINGS_MODULE", "dlgapp.settings")
        os.environ["DJANGO_SETTINGS_MODULE"] = "dlgapp.settings"
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

# variant -> (selector that proves it is on screen, the one event Escape must send)
EXPECT = {
    "modal_class": (".dj-modal", {"event": "close_modal"}),
    "lightbox_class": (".dj-lightbox", {"event": "close_lightbox"}),
    "lightbox_tag": (".dj-lightbox", {"event": "close_lightbox"}),
    "tour_class": (".dj-tour__popover", {"event": "tour", "value": "skip"}),
    "tour_tag": (".dj-tour__popover", {"event": "tour", "value": "skip"}),
    "theme_modal": ("[data-theme-modal] [role=dialog]", {"event": "toggle_modal", "value": "tm"}),
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
            "dlgapp.asgi:application",
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
                console = []
                page.on("console", lambda m: console.append((m.type, m.text)))
                page.on(
                    "websocket",
                    lambda ws: ws.on(
                        "framesent",
                        lambda f: (
                            sent.append(json.loads(str(f))) if '"type":"event"' in str(f) else None
                        ),
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

                for variant, (selector, want) in EXPECT.items():
                    page.evaluate(f"() => document.getElementById('open-{variant}').click()")
                    page.wait_for_selector(selector, timeout=6000, state="attached")
                    page.wait_for_timeout(500)
                    # focus something inside the dialog (keyboard-nav moves focus in on open)
                    inside = page.evaluate(
                        f"() => !!document.activeElement.closest({json.dumps(selector)}.split(' ')[0]) "
                        "|| !!document.activeElement.closest('[role=dialog], .dj-modal')"
                    )
                    if not inside:
                        page.evaluate(
                            "() => { const d = document.querySelector('[role=dialog], .dj-modal'); "
                            "const f = d && d.querySelector('button, [tabindex]'); if (f) f.focus(); else if (d) { d.setAttribute('tabindex','-1'); d.focus(); } }"
                        )
                    mark = len(sent)
                    console.clear()
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(700)
                    got = sent[mark:]
                    names = [(g["event"], (g.get("params") or {}).get("value")) for g in got]
                    expect = (want["event"], want.get("value"))
                    check(
                        names == [expect],
                        f"{variant}: Escape sends exactly {expect} to the server (got {names})",
                    )
                    check(
                        page.text_content("#which").strip() == "",
                        f"{variant}: the server closed it (which = {page.text_content('#which').strip()!r})",
                    )
                    check(
                        not [t for k, t in console if "no longer clicks" in t],
                        f"{variant}: no 'Escape ignored' console warning for djust's own markup",
                    )
                # A real click on each marked close control still works (the marker is
                # not sent as an argument), including for handlers with no **kwargs.
                for variant, (selector, want) in EXPECT.items():
                    if variant.startswith("tour"):
                        continue  # the tour's close control (Skip) is covered by Escape above
                    page.evaluate(f"() => document.getElementById('open-{variant}').click()")
                    page.wait_for_selector(selector, timeout=6000, state="attached")
                    page.wait_for_timeout(300)
                    mark = len(sent)
                    page.evaluate(
                        "() => document.querySelector('[data-dj-close], .dj-modal__close').click()"
                    )
                    page.wait_for_timeout(600)
                    check(
                        [g["event"] for g in sent[mark:]] == [want["event"]]
                        and page.text_content("#which").strip() == "",
                        f"{variant}: a click on its close control sends {want['event']} and closes it",
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
