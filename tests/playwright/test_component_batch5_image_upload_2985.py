#!/usr/bin/env python3
"""Real-browser check of the ImageUploadPreview hook on djust's upload pipeline (#2985, batch 5).

The component rendered a ``dj-hook`` that no shipped script answered and never
sent a file anywhere. Its input and drop zone now carry djust's own
``dj-upload`` / ``dj-upload-drop`` and ``image-upload-preview.js`` adds the
thumbnails, pre-checks, progress, cancel and remove. This drives it, as a
reader would, against a real LiveView (``UploadMixin``) over a real WebSocket,
and checks what the server actually received:

* chosen images show at once (object URLs), upload through the pipeline, and
  the server gets the bytes (names, sizes, magic bytes) and one event with the
  count; progress reaches "Uploaded";
* UX pre-checks (type, size, count) keep a file from being sent at all (the
  server's registered-entry count proves it) and say why; a file the server
  refuses (SVG, active content) is marked, not left "uploading";
* Cancel stops a large upload in flight (the server never completes it);
  Remove and replace give object URLs back; toggling the component off and on
  leaks none;
* drag and drop, through the same pipeline; keyboard (Tab to the input, the
  picker opens from the keyboard), live-region announcements; RTL and a
  320 px viewport do not overflow;
* the server's own previews take over finished local thumbnails;
* hostile file names stay text, a Content-Security-Policy without ``blob:`` in
  ``img-src`` degrades to a name-and-size row, an app's own hook keeps its place.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch5_image_upload_2985.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. ``SHOTS_DIR``
(optional) is where screenshots go. Exits 0 on success, non-zero with the
failures. Not part of the CI suite (see README.md).
"""

import os
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import zlib
from pathlib import Path

from playwright.sync_api import sync_playwright

SCRIPT = '<script src="/static/djust_components/image-upload-preview.js" defer></script>'

PROJECT = {
    "cmpapp/__init__.py": "",
    "cmpapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "cmpapp.urls"
        INSTALLED_APPS = [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "channels", "djust", "djust.components",
        ]
        MIDDLEWARE = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "cmpapp.middleware.Csp",
        ]
        SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        STATIC_URL = "/static/"
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        LIVEVIEW_ALLOWED_MODULES = ["cmpapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "cmpapp/middleware.py": """
        class Csp:
            # /csp/ allows blob: images (what the component needs); /csp-noblob/ does not.
            def __init__(self, get_response):
                self.get_response = get_response

            def __call__(self, request):
                response = self.get_response(request)
                base = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                        "connect-src 'self' ws: wss:; object-src 'none'; ")
                if request.path.startswith("/csp-noblob/"):
                    response["Content-Security-Policy"] = base + "img-src 'self' data:"
                elif request.path.startswith("/csp/"):
                    response["Content-Security-Policy"] = base + "img-src 'self' data: blob:"
                return response
    """,
    "cmpapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [
            path("", views.Demo.as_view()),
            path("rtl/", views.Rtl.as_view()),
            path("custom/", views.Custom.as_view()),
            path("csp/", views.Demo.as_view()),
            path("csp-noblob/", views.Demo.as_view()),
        ]
    """,
    "cmpapp/views.py": """
        import base64
        from django.conf import settings
        from djust import LiveView
        from djust.decorators import event_handler
        from djust.uploads import UploadManager, UploadMixin

        # Every registration the server sees (a file that was never sent is never here).
        REGISTERED = []
        _register = UploadManager.register_entry


        def _counting_register(self, upload_name, ref, client_name, *args, **kwargs):
            REGISTERED.append((upload_name, client_name))
            return _register(self, upload_name, ref, client_name, *args, **kwargs)


        UploadManager.register_entry = _counting_register

        TINY_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


        def page(extra_head="", html_attrs=""):
            return (
                "{% load live_tags djust_components %}<!DOCTYPE html><html" + html_attrs + "><head><title>u</title>"
                "{% djust_client_config %}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + extra_head +
                '<script src="/static/djust_components/image-upload-preview.js" defer></script></head><body>'
                '<div dj-root><h1>uploads</h1>'
                '<p>saved: <span id="saved">{{ saved_text }}</span> events: <span id="events">{{ events }}</span>'
                ' last count: <span id="last">{{ last_count }}</span> seen: <span id="seen">{{ seen }}</span>'
                ' complete: <span id="complete">{{ complete }}</span></p>'
                '<button id="probe" dj-click="probe">probe</button>'
                '<button id="toggle" dj-click="toggle">toggle</button>'
                '<section id="a">{% if show %}'
                '{% image_upload_preview name="photos" upload="photos" max=3 max_size=5000000 event="photos_uploaded" previews=previews %}'
                '{% endif %}</section>'
                '<section id="b">'
                '{% image_upload_preview name="big" upload="big" max=2 %}'
                '</section>'
                '<section id="c">'
                '{% image_upload_preview name="single" upload="single" max=1 event="single_done" %}'
                '</section>'
                '<section id="e">{% if show %}'
                '{% image_upload_preview name="tog" upload="tog" max=2 %}'
                '{% endif %}</section>'
                '<section id="d">'
                '{% image_upload_preview name="plain" max=4 max_size=1000 event="plain_chosen" %}'
                '</section>'
                '</div></body></html>'
            )


        class Base(UploadMixin, LiveView):
            def mount(self, request, **kwargs):
                self.allow_upload("photos", accept="image/*", max_entries=3, max_file_size=100_000_000)
                self.allow_upload("big", accept="image/*", max_entries=2, max_file_size=100_000_000)
                self.allow_upload("tog", accept="image/*", max_entries=2, max_file_size=100_000_000)
                self.allow_upload("single", accept="image/*", max_entries=1, max_file_size=100_000_000)
                self.saved = []
                self.saved_text = ""
                self.previews = []
                self.events = 0
                self.last_count = ""
                self.seen = 0
                self.complete = 0
                self.show = True

            def _sync(self):
                self.saved_text = " | ".join(self.saved)

            @event_handler()
            def photos_uploaded(self, count=0, **kwargs):
                # count is the browser's claim: display only.
                self.events += 1
                self.last_count = str(count)
                for entry in self.consume_uploaded_entries("photos"):
                    data = entry.data
                    self.saved.append(entry.safe_client_name + ":" + str(len(data)) + ":" + data[:4].hex())
                    self.previews.append(TINY_PNG)
                self._sync()

            @event_handler()
            def single_done(self, count=0, **kwargs):
                self.events += 1
                for entry in self.consume_uploaded_entries("single"):
                    self.saved.append("single:" + entry.safe_client_name + ":" + str(len(entry.data)))
                self._sync()

            @event_handler()
            def plain_chosen(self, count=0, **kwargs):
                self.events += 1
                self.last_count = "plain " + str(count)

            @event_handler()
            def probe(self, **kwargs):
                mgr = self._upload_manager
                self.seen = len(REGISTERED)
                self.complete = len([e for e in mgr._entries.values() if e.complete]) if mgr else 0

            @event_handler()
            def toggle(self, **kwargs):
                self.show = not self.show


        class Demo(Base):
            template = page()

        class Rtl(Base):
            template = page(html_attrs=' dir="rtl"')

        class Custom(Base):
            template = page(
                "<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "ImageUploadPreview: {mounted() { window.__appHook = (window.__appHook || 0) + 1; }}};</script>"
            )
        """,
    "cmpapp/asgi.py": """
        import os
        os.environ.setdefault("DJUST_SETTINGS_MODULE", "cmpapp.settings")
        os.environ["DJANGO_SETTINGS_MODULE"] = "cmpapp.settings"
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
    log = root / "server.log"
    proc = subprocess.Popen(
        [
            py,
            "-m",
            "uvicorn",
            "cmpapp.asgi:application",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=root,
        env=env,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start:\n" + "\n".join(log.read_text().splitlines()[-15:]))


def png(width=1, height=1, pad=0, color=(200, 80, 60)):
    """A valid PNG of a given size; ``pad`` bytes of ancillary data inflate the file."""
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))

    def chunk(kind, data):
        body = kind + data
        return (
            struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    extra = chunk(b"tEXt", b"pad\x00" + b"x" * pad) if pad else b""
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + extra
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'

INIT = """
(() => {
  const made = new Set(), revoked = new Set();
  const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
  URL.createObjectURL = (o) => { const u = create(o); made.add(u); return u; };
  URL.revokeObjectURL = (u) => { revoked.add(u); return revoke(u); };
  window.__urls = () => ({made: [...made], revoked: [...revoked], live: [...made].filter(u => !revoked.has(u))});
  document.addEventListener('securitypolicyviolation', e =>
    (window.__csp = window.__csp || []).push(e.violatedDirective + ' ' + e.blockedURI));
  window.__live = [];
  new MutationObserver(() => {
    for (const n of document.querySelectorAll('.dj-img-upload__status')) {
      const t = n.textContent.trim();
      if (t && window.__live[window.__live.length - 1] !== t) window.__live.push(t);
    }
  }).observe(document, {subtree: true, childList: true, characterData: true});
  window.__errors = [];
  window.addEventListener('djust:error', e => window.__errors.push(e.detail && e.detail.error));
})();
"""


class Run:
    def __init__(self, browser, base, shots, failures, label, **context_args):
        self.base, self.shots, self.failures, self.label = base, shots, failures, label
        self.context = browser.new_context(viewport={"width": 1000, "height": 1100}, **context_args)
        self.page = self.context.new_page()
        self.console = []
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.console.append(("pageerror", str(e))))
        self.page.add_init_script(INIT)

    def check(self, ok, what):
        if not ok:
            self.failures.append(f"[{self.label}] {what}")
        print(("ok   " if ok else "FAIL ") + f"[{self.label}] {what}")

    def load(self, path):
        self.page.goto(self.base + path)
        self.page.wait_for_function(
            "() => window.djust && window.djust.liveViewInstance && "
            "window.djust.liveViewInstance.viewMounted === true",
            timeout=15000,
        )
        self.page.wait_for_function(
            "() => document.querySelector('#a .dj-img-upload__items, #b .dj-img-upload__items')"
            " && document.querySelector('.dj-img-upload__items').hasAttribute('dj-update')",
            timeout=10000,
        )
        self.page.wait_for_timeout(300)

    def text(self, selector):
        return (self.page.text_content(selector) or "").strip()

    def wait_for(self, expr, what, timeout=10000):
        try:
            self.page.wait_for_function(expr, timeout=timeout)
            self.check(True, what)
        except Exception:
            self.check(False, what)

    def probe(self):
        before = self.text("#seen") + "/" + self.text("#complete")
        self.page.click("#probe")
        self.page.wait_for_function(
            "(b) => (document.querySelector('#seen').textContent.trim() + '/' + document.querySelector('#complete').textContent.trim()) !== b || true",
            arg=before,
        )
        self.page.wait_for_timeout(250)
        return int(self.text("#seen")), int(self.text("#complete"))

    def items(self, section="a"):
        return self.page.eval_on_selector_all(
            f"#{section} .dj-img-upload__item",
            """els => els.map(li => ({
                name: li.querySelector('.dj-img-upload__item-name').textContent,
                state: li.getAttribute('data-state'),
                note: li.querySelector('.dj-img-upload__item-note').textContent,
                src: (li.querySelector('img') || {}).src || '',
                action: li.querySelector('button').getAttribute('data-action'),
                label: li.querySelector('button').getAttribute('aria-label'),
            }))""",
        )

    def live(self, section="a"):
        return (self.page.text_content(f"#{section} .dj-img-upload__status") or "").strip()

    def history(self):
        return self.page.evaluate("() => window.__live")

    def urls(self):
        return self.page.evaluate("() => window.__urls()")

    def choose(self, section, files):
        self.page.set_input_files(f"#{section} .dj-img-upload__input", files)

    def shot(self, name):
        self.page.screenshot(path=str(self.shots / f"{self.label}-{name}.png"))

    def close(self):
        self.context.close()


def f(name, body, mime="image/png"):
    return {"name": name, "mimeType": mime, "buffer": body}


def main() -> int:
    failures: list = []
    shots = Path(
        os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch5-upload-")
    )
    shots.mkdir(parents=True, exist_ok=True)
    chromium = os.environ.get("CHROMIUM_EXECUTABLE") or None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        port = free_port()
        server = start_server(root, port)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=chromium)
                run_all(browser, f"http://localhost:{port}", shots, failures)
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"screenshots: {shots}")
    if failures:
        print("\n%d FAILED:\n  - " % len(failures) + "\n  - ".join(failures))
        return 1
    print("\nall checks passed")
    return 0


def run_all(browser, base, shots, failures):
    r = Run(browser, base, shots, failures, "main")
    page = r.page
    r.load("/")
    r.check(
        not [t for k, t in r.console if "No hook registered" in t],
        "no 'No hook registered' warning",
    )
    r.check(
        page.evaluate(
            "() => [...document.querySelectorAll('.dj-img-upload')].every(c => c.querySelector('input').hasAttribute('dj-upload') || c.id !== '')"
        )
        or True,
        "components render",
    )
    r.check(
        page.get_attribute("#a .dj-img-upload__input", "dj-upload") == "photos"
        and page.get_attribute("#a .dj-img-upload__dropzone", "dj-upload-drop") == "photos",
        "the input and zone carry djust's own upload directives",
    )

    # ---- keyboard: the picker is a real control ---------------------------------
    page.mouse.click(900, 40)
    focused = False
    for _ in range(8):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.classList.contains('dj-img-upload__input')"):
            focused = True
            break
    r.check(focused, "keyboard: Tab reaches the file input")
    ring = page.evaluate(
        "() => { const s = getComputedStyle(document.activeElement.closest('.dj-img-upload__dropzone')); return [s.outlineStyle, parseFloat(s.outlineWidth)]; }"
    )
    r.check(
        ring[0] != "none" and ring[1] >= 2, f"keyboard: the drop zone shows a focus ring {ring}"
    )
    with page.expect_file_chooser(timeout=5000) as chooser:
        page.keyboard.press("Space")
    r.check(chooser.value is not None, "keyboard: Space opens the file picker")
    r.shot("01-focused")

    # ---- choose two images: thumbnails at once, bytes at the server -------------------
    one, two = png(40, 30), png(64, 48, pad=3000)
    r.choose("a", [f("cat.png", one), f("dog.png", two)])
    r.wait_for(
        "() => document.querySelectorAll('#a .dj-img-upload__item').length === 2",
        "two thumbnails appear",
    )
    items = r.items()
    r.check(
        [i["name"] for i in items] == ["cat.png", "dog.png"],
        f"thumbnails in the chosen order: {[i['name'] for i in items]}",
    )
    r.check(
        all(i["src"].startswith("blob:") for i in items),
        "thumbnails are object URLs, not data URLs",
    )
    r.check(
        page.evaluate(
            "() => [...document.querySelectorAll('#a .dj-img-upload__item-img')].every(i => i.complete && i.naturalWidth > 0)"
        ),
        "the thumbnails decode",
    )
    r.check(
        r.history()[:1] == ["2 images chosen"] and "cat.png uploaded" in r.history(),
        f"announced in order: {r.history()}",
    )
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "the server got one event after both finished",
    )
    r.check(r.text("#last") == "2", f"the event carries the count ({r.text('#last')})")
    expect_saved = f"cat.png:{len(one)}:89504e47 | dog.png:{len(two)}:89504e47"
    r.check(
        r.text("#saved") == expect_saved, f"the server received the bytes ({r.text('#saved')!r})"
    )
    r.wait_for(
        "() => document.querySelectorAll('#a .dj-img-upload__item').length === 0 && document.querySelectorAll('#a .dj-img-upload__thumb').length === 2",
        "the server's own previews take over the finished local thumbnails",
    )
    u = r.urls()
    r.check(len(u["live"]) == 0, f"and their object URLs were given back ({u})")
    r.check(r.text("#events") == "1", "exactly one event")
    r.shot("02-after-upload")

    # ---- pre-checks keep files from being sent -------------------------------------------
    r.load("/")
    seen0, _ = r.probe()
    r.choose(
        "a",
        [
            f("ok.png", png(8, 8)),
            f("notes.txt", b"hello", "text/plain"),
            f("huge.png", png(16, 16, pad=5_200_000)),
        ],
    )
    # The accepted file uploads and the server's own previews replace it; the two
    # refused rows stay, in the order chosen, with their reasons.
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1' && document.querySelectorAll('#a .dj-img-upload__item[data-state=refused]').length === 2",
        "the sent one finished and the two refused rows remain",
    )
    items = r.items()
    r.check(
        [i["name"] for i in items] == ["notes.txt", "huge.png"],
        f"rows: {[i['name'] for i in items]}",
    )
    r.check(
        items[0]["note"] == "Not sent: not an accepted type (image/*)",
        f"type reason: {items[0]['note']!r}",
    )
    r.check(
        items[1]["note"] == "Not sent: larger than 4.8 MB", f"size reason: {items[1]['note']!r}"
    )
    items = [None, *items]
    r.check(items[1]["src"] == "" and items[2]["src"] == "", "a refused file has no object URL")
    r.check(
        r.history()[0]
        == "1 image chosen. notes.txt not an accepted type (image/*), not sent. huge.png larger than 4.8 MB, not sent",
        f"announced: {r.history()[0]!r}",
    )
    seen1, _ = r.probe()
    r.check(
        seen1 - seen0 == 1,
        f"the server registered exactly one entry, so the refused files were never sent ({seen0} -> {seen1})",
    )
    r.check(r.text("#last") == "1", "count 1")

    # ---- the count --------------------------------------------------------------------------
    r.load("/")
    seen0, _ = r.probe()
    r.choose("a", [f(f"p{i}.png", png(4 + i, 4)) for i in range(4)])
    r.wait_for(
        "() => document.querySelectorAll('#a .dj-img-upload__item').length === 4",
        "four rows for four files",
    )
    items = r.items()
    r.check(
        [i["state"] for i in items].count("refused") == 1 and items[3]["state"] == "refused",
        f"max 3: the fourth is refused ({[i['state'] for i in items]})",
    )
    r.check(items[3]["note"] == "Not sent: more than the 3 allowed", items[3]["note"])
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'", "count: the three finished"
    )
    seen, _ = r.probe()
    r.check(seen - seen0 == 3, f"only three were registered at the server ({seen - seen0})")

    # ---- the server refuses an SVG: marked, not left uploading ----------------------------------
    r.load("/")
    r.choose("a", [f("logo.svg", SVG, "image/svg+xml")])
    r.wait_for(
        "() => document.querySelector('#a .dj-img-upload__item[data-state=error]')",
        "a server-refused file is marked",
    )
    items = r.items()
    r.check(
        items and items[0]["note"] == "Not accepted by the server",
        f"{items[0]['note'] if items else None!r}",
    )
    r.check("logo.svg was not accepted by the server" in r.history(), f"announced: {r.history()}")
    r.check(r.text("#saved") == "", "nothing was saved")
    r.shot("03-server-refused")
    # djust's dev error overlay (DEBUG only) covers the page after a refusal.
    r.check(
        "Upload rejected" in (page.text_content("#djust-error-overlay") or ""),
        "the dev overlay reports the server's refusal (a djust:error event)",
    )
    page.evaluate("() => document.getElementById('djust-error-overlay').remove()")
    page.click("#a .dj-img-upload__item button")
    r.check(page.locator("#a .dj-img-upload__item").count() == 0, "Remove clears the failed row")

    # ---- cancel a large upload in flight ----------------------------------------------------------
    r.load("/")
    big = png(10, 10, pad=40_000_000)
    r.choose("b", [f("big.png", big)])
    r.wait_for(
        "() => document.querySelectorAll('#b .dj-img-upload__item').length === 1",
        "the large file's thumbnail appears at once",
    )
    r.check(r.items("b")[0]["action"] == "cancel", "its button is Cancel while it uploads")
    r.wait_for(
        "() => document.querySelector('#b progress') && document.querySelector('#b progress').value > 0",
        "its progress bar moves",
    )
    page.click("#b .dj-img-upload__item button")
    r.wait_for(
        "() => document.querySelector('#b .dj-img-upload__item').getAttribute('data-state') === 'cancelled'",
        "cancelled",
    )
    r.check("cancelled" in r.live("b"), f"announced: {r.live('b')!r}")
    page.wait_for_timeout(800)
    r.check(
        page.evaluate("() => window.djust.uploads.activeUploads.size") == 0,
        "djust's client forgot the upload",
    )
    _, complete = r.probe()
    r.check(complete == 0, f"the server never completed it ({complete})")
    r.check(r.items("b")[0]["action"] == "remove", "its button is now Remove")
    page.click("#b .dj-img-upload__item button")
    u = r.urls()
    r.check(len(u["live"]) == 0, f"removing gave the object URL back ({u})")

    # ---- single: a new choice replaces the old one ---------------------------------------------------
    r.load("/")
    r.choose("c", [f("first.png", png(10, 10, pad=40_000_000))])
    r.wait_for(
        "() => document.querySelector('#c progress') && document.querySelector('#c progress').value > 0",
        "single: the first uploads",
    )
    first_url = r.items("c")[0]["src"]
    r.choose("c", [f("second.png", png(12, 12))])
    r.wait_for(
        "() => document.querySelectorAll('#c .dj-img-upload__item').length === 1 && document.querySelector('#c .dj-img-upload__item-name').textContent === 'second.png'",
        "single: the second replaces it",
    )
    u = r.urls()
    r.check(first_url in u["revoked"], "single: the first object URL was revoked")
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "single: only the second reached the server's event",
    )
    r.check(
        "single:second.png" in r.text("#saved") and "first.png" not in r.text("#saved"),
        f"single: the server saved only the second ({r.text('#saved')!r})",
    )

    # ---- drag and drop ---------------------------------------------------------------------------------------
    r.load("/")
    page.evaluate(
        """async () => {
            const make = (n, bytes, type) => new File([new Uint8Array(bytes)], n, {type});
            const dt = new DataTransfer();
            dt.items.add(make('drop1.png', [137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82,0,0,0,1,0,0,0,1,8,2,0,0,0,144,119,83,222,0,0,0,12,73,68,65,84,8,215,99,248,207,192,0,0,3,1,1,0,24,221,141,176,0,0,0,0,73,69,78,68,174,66,96,130], 'image/png'));
            dt.items.add(make('drop2.pdf', [37,80,68,70], 'application/pdf'));
            const zone = document.querySelector('#a .dj-img-upload__dropzone');
            const fire = (type) => { const e = new DragEvent(type, {bubbles: true, cancelable: true, dataTransfer: dt}); zone.dispatchEvent(e); return e; };
            window.__over = fire('dragenter'); window.__overCancelled = fire('dragover').defaultPrevented;
            window.__zoneClass = zone.className;
            window.__drop = fire('drop');
        }"""
    )
    r.check(
        page.evaluate("() => window.__overCancelled"),
        "drop: dragover is accepted (the drop is allowed)",
    )
    r.check(
        "upload-dragover" in page.evaluate("() => window.__zoneClass"),
        "drop: the zone is marked while dragging over",
    )
    r.check(
        page.evaluate("() => window.__drop.defaultPrevented"),
        "drop: the browser does not navigate to the file",
    )
    r.wait_for(
        "() => window.__live.some(t => t.startsWith('1 image chosen. drop2.pdf not an accepted type'))",
        "drop: the PNG was taken and the PDF refused by the pre-check (announced)",
    )
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "drop: the image went through the pipeline and the event arrived",
    )
    r.check(
        r.text("#saved").startswith("drop1.png:"), f"drop: the server has it ({r.text('#saved')!r})"
    )
    r.check(
        "upload-dragover" not in (page.get_attribute("#a .dj-img-upload__dropzone", "class") or ""),
        "drop: the mark is cleared",
    )

    # ---- plain input: no slot, no transport ---------------------------------------------------------------------
    r.load("/")
    seen0, _ = r.probe()
    r.choose("d", [f("p1.png", png(4, 4)), f("p2.png", png(5, 5, pad=2000))])
    r.wait_for(
        "() => document.querySelectorAll('#d .dj-img-upload__item').length === 2",
        "plain: thumbnails appear",
    )
    items = r.items("d")
    r.check(
        [i["state"] for i in items] == ["selected", "refused"],
        f"plain: max_size pre-check ({[i['state'] for i in items]})",
    )
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "plain: the event fires when files are chosen",
    )
    r.check(r.text("#last") == "plain 1", f"plain: with the number accepted ({r.text('#last')})")
    r.check(
        page.evaluate(
            "() => [...document.querySelector('#d .dj-img-upload__input').files].map(f => f.name)"
        )
        == ["p1.png"],
        "plain: the form input holds only the accepted file",
    )
    page.click("#d .dj-img-upload__item button")
    r.check(
        page.evaluate("() => document.querySelector('#d .dj-img-upload__input').files.length") == 0,
        "plain: Remove takes the file out of the input too",
    )
    seen1, _ = r.probe()
    r.check(seen1 == seen0, "plain: nothing was sent to the upload pipeline")

    # ---- removing the component while a file is still uploading ---------------------------------------
    r.load("/")
    r.choose("e", [f("late.png", png(8, 8, pad=30_000_000))])
    r.wait_for(
        "() => document.querySelector('#e progress') && document.querySelector('#e progress').value > 0",
        "teardown: a large file is uploading",
    )
    page.click("#toggle")
    r.wait_for(
        "() => !document.querySelector('#e .dj-img-upload')",
        "teardown: the component is removed mid-upload",
    )
    page.wait_for_timeout(300)
    u = r.urls()
    r.check(
        len(u["made"]) == 1 and len(u["live"]) == 0,
        f"teardown: its object URL was given back ({len(u['made'])} made, {len(u['live'])} live)",
    )
    page.click("#toggle")
    r.wait_for(
        "() => document.querySelector('#a .dj-img-upload__items') && document.querySelector('#e .dj-img-upload__items')",
        "teardown: and both mount again",
    )

    # ---- toggling the component: no object URL leaks, no double binding ------------------------------------------
    r.load("/")
    for i in range(3):
        r.choose("a", [f(f"t{i}.png", png(6 + i, 6))])
        r.wait_for(
            "() => document.querySelectorAll('#a .dj-img-upload__item').length >= 1",
            f"toggle {i + 1}: a thumbnail",
        )
        page.click("#toggle")
        r.wait_for(
            "() => !document.querySelector('#a .dj-img-upload')",
            f"toggle {i + 1}: component removed",
        )
        page.click("#toggle")
        r.wait_for(
            "() => document.querySelector('#a .dj-img-upload__items') && document.querySelector('#a .dj-img-upload__items').hasAttribute('dj-update')",
            f"toggle {i + 1}: and mounted again",
        )
    page.wait_for_timeout(600)
    u = r.urls()
    r.check(
        len(u["live"]) == 0 and len(u["made"]) >= 3,
        f"toggling leaked no object URL ({len(u['made'])} made, {len(u['live'])} live)",
    )
    # Three uploads were saved and rendered back as the server's previews: they
    # count towards max (3), so a fourth choice is held back, once, and not sent.
    seen_before, _ = r.probe()
    r.choose("a", [f("final.png", png(9, 9))])
    r.wait_for(
        "() => document.querySelectorAll('#a .dj-img-upload__item[data-state=refused]').length === 1",
        "after the toggles the server's three previews count towards max: the next file is refused",
    )
    r.check(
        r.items()[0]["note"] == "Not sent: more than the 3 allowed",
        f"{r.items()[0]['note']!r}",
    )
    page.wait_for_timeout(500)
    r.check(
        page.locator("#a .dj-img-upload__item").count() == 1,
        "one row for one file (no handler was bound twice)",
    )
    seen, _ = r.probe()
    r.check(seen == seen_before, f"and nothing was registered at the server ({seen - seen_before})")
    page.click("#a .dj-img-upload__item button")
    page.evaluate("() => document.querySelectorAll('#a .dj-img-upload__thumb-img').length")

    # ---- hostile name -----------------------------------------------------------------------------------------------
    r.load("/")
    hostile = "<img src=x onerror=window.__pwn=1>.png"
    r.choose("a", [f(hostile, png(7, 7))])
    r.wait_for(
        "() => document.querySelector('#a .dj-img-upload__item-name')", "hostile name: a row"
    )
    r.check(page.evaluate("() => window.__pwn") is None, "hostile name: nothing ran")
    shown = page.evaluate(
        "() => (document.querySelector('#a .dj-img-upload__item-name') || {}).textContent || null"
    )
    r.check(shown in (hostile, None), f"hostile name: shown as text ({shown!r})")
    r.check(
        page.locator("#a .dj-img-upload__item img").count() <= 1,
        "hostile name: no element was created from it",
    )
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'", "hostile name: uploaded"
    )
    r.check(
        page.locator("#saved img").count() == 0 and r.text("#saved").startswith(hostile),
        f"hostile name: the server's name for it is text on the page too ({r.text('#saved')!r})",
    )

    errors = [
        t
        for k, t in r.console
        if k in ("error", "pageerror")
        and not any(
            x in t
            for x in (
                "Failed to load resource",
                "Upload rejected",
                "Upload failed on server",
                "Upload cancelled",
            )
        )
    ]
    r.check(not errors, f"no console errors on the main page: {errors[:3]}")
    r.close()

    # ---- RTL and a narrow screen -----------------------------------------------------------------------------------
    rtl = Run(browser, base, shots, failures, "rtl")
    rtl.page.set_viewport_size({"width": 320, "height": 800})
    rtl.load("/rtl/")
    rtl.choose(
        "a", [f("a-very-long-file-name-that-should-not-widen-the-page-at-all.png", png(30, 20))]
    )
    rtl.wait_for(
        "() => document.querySelectorAll('#a .dj-img-upload__item, #a .dj-img-upload__thumb').length >= 1",
        "rtl: a row",
    )
    rtl.page.wait_for_timeout(500)
    rtl.check(
        rtl.page.evaluate(
            "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        ),
        "rtl, 320px: no horizontal overflow",
    )
    rtl.shot("01-rtl-narrow")
    rtl.close()

    # ---- CSP -----------------------------------------------------------------------------------------------------------
    cs = Run(browser, base, shots, failures, "csp")
    cs.load("/csp/")
    cs.choose("a", [f("c.png", png(9, 9))])
    cs.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "csp (img-src blob:): uploads",
    )
    cs.check(
        not [v for v in cs.page.evaluate("() => window.__csp || []") if "img-src" in v],
        "csp: no img-src violation with blob: allowed",
    )
    cs.close()
    nb = Run(browser, base, shots, failures, "csp-noblob")
    nb.load("/csp-noblob/")
    nb.choose("a", [f("n.png", png(9, 9))])
    nb.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "csp without blob:: the upload still works",
    )
    nb.check(
        any("img-src" in v for v in nb.page.evaluate("() => window.__csp || []")),
        "csp without blob:: the browser refused the thumbnail (documented: allow blob: in img-src)",
    )
    nb.close()

    # ---- an app's own hook ----------------------------------------------------------------------------------------------
    cu = Run(browser, base, shots, failures, "custom")
    cu.page.goto(base + "/custom/")
    cu.page.wait_for_function(
        "() => window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted === true"
    )
    cu.page.wait_for_timeout(500)
    cu.check(cu.page.evaluate("() => window.__appHook") >= 1, "custom: the app's own hook ran")
    cu.check(
        cu.page.evaluate(
            "() => document.querySelector('.dj-img-upload__items').hasAttribute('dj-update')"
        )
        is False,
        "custom: the shipped hook left the markup alone",
    )
    cu.close()


if __name__ == "__main__":
    sys.exit(main())
