#!/usr/bin/env python3
"""Real-browser check of the MapPicker hook and its vendored Leaflet (#2985, batch 5).

The component rendered a ``dj-hook`` that no shipped script answered, and its
docstring promised Leaflet without shipping it. Leaflet is now vendored
(ADR-040) and loaded on demand by ``map-picker.js``. This drives it, as a
reader would, against a real LiveView over a real WebSocket:

* Leaflet is loaded once per page, lazily, with Subresource Integrity, and a
  page without a map never fetches it; no request answers with an error status;
* a click or tap picks a location: the marker lands under the pointer (also in
  an RTL page) and the server receives validated numbers; the keyboard moves
  the marker (arrows, Shift, + / -), Enter chooses, Escape goes back, all
  announced; the marker stays on screen;
* the server stays in charge (a new position, an unrelated patch, a patch in
  the middle of a drag), resize follows the container, toggling the map off and
  on does not double-bind, no tile request is made for ``tile_url=""``;
* tiles unreachable or the network offline: the picker still works and says so;
* a tampered Leaflet (SRI mismatch) is refused and reported, a strict
  Content-Security-Policy with no ``unsafe-eval`` is not violated, hostile
  attribution and label stay text, an app's own hook keeps its place;
* the same under ``ManifestStaticFilesStorage``: hashed file names, the
  stylesheet's rewritten images and the explicit marker icons all load.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn (twice: DEBUG with the staticfiles handler, and DEBUG off with a
collected, hashed STATIC_ROOT) and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch5_map_picker_2985.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. ``SHOTS_DIR``
(optional) is where screenshots go. Exits 0 on success, non-zero with the
failures. Not part of the CI suite (see README.md).
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
    "cmpapp/__init__.py": "",
    "cmpapp/settings.py": """
        import os
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        HASHED = os.environ.get("HASHED") == "1"
        SECRET_KEY = "x"
        DEBUG = not HASHED
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
        STATIC_ROOT = BASE_DIR / "collected"
        if HASHED:
            STORAGES = {
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
                },
            }
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
            # /csp/ is served under a strict policy: scripts and <style> from the origin
            # only (no unsafe-eval; inline style ATTRIBUTES are allowed because the
            # component renders its height as one), images from the origin and data:.
            def __init__(self, get_response):
                self.get_response = get_response

            def __call__(self, request):
                response = self.get_response(request)
                if request.path.startswith("/csp/"):
                    response["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                        "style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; "
                        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'"
                    )
                return response
    """,
    "cmpapp/urls.py": """
        from django.conf import settings
        from django.urls import path, re_path
        from django.views.static import serve
        from . import views
        urlpatterns = [
            path("", views.Demo.as_view()),
            path("nomap/", views.NoMap.as_view()),
            path("osm/", views.Osm.as_view()),
            path("notiles/", views.NoTiles.as_view()),
            path("empty/", views.Empty.as_view()),
            path("rtl/", views.Rtl.as_view()),
            path("custom/", views.Custom.as_view()),
            path("csp/", views.Demo.as_view()),
            path("csp/nomap/", views.NoMap.as_view()),
            path("xss/", views.Xss.as_view()),
            path("tiles/<int:z>/<int:x>/<int:y>.png", views.tile),
            path("tilelog/", views.tilelog),
        ]
        if settings.HASHED:
            urlpatterns.append(re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}))
    """,
    "cmpapp/views.py": """
        import math
        import struct
        import zlib

        from django.http import HttpResponse, JsonResponse
        from djust import LiveView
        from djust.decorators import event_handler

        HOSTILE = "<img src=x onerror=window.__pwn=1><b>x</b>"
        TILES = []


        def _png(rgb=(222, 226, 214)):
            row = b"\\x00" + bytes(rgb) * 256
            edge = b"\\x00" + bytes((150, 150, 150)) * 256
            raw = edge + row * 254 + edge
            def chunk(kind, data):
                body = kind + data
                return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            return (b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


        PNG = _png()


        def tile(request, z, x, y):
            TILES.append((z, x, y))
            return HttpResponse(PNG, content_type="image/png")


        def tilelog(request):
            if request.GET.get("reset"):
                TILES.clear()
            return JsonResponse({"n": len(TILES)})


        def page(map_a, head="", html_attrs="", extra=""):
            return (
                "{% load live_tags djust_components %}<!DOCTYPE html><html" + html_attrs + "><head><title>m</title>"
                "{% djust_client_config %}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + head +
                '<script src="/static/djust_components/map-picker.js" defer></script></head><body>'
                '<div dj-root><h1>maps</h1>'
                '<p>a: <span id="picked-a">{{ picked_a }}</span> b: <span id="picked-b">{{ picked_b }}</span>'
                ' n: <span id="n">{{ n }}</span> rejected: <span id="rejected">{{ rejected }}</span>'
                ' events: <span id="events">{{ events }}</span></p>'
                '<button id="move" dj-click="move_server">server move</button>'
                '<button id="zoom-server" dj-click="zoom_server">server zoom</button>'
                '<button id="bump" dj-click="bump">bump</button>'
                '<button id="toggle" dj-click="toggle">toggle</button>'
                '{% if show %}<div id="wrap-a" style="width:500px">' + map_a + "</div>{% endif %}"
                + extra + "</div></body></html>"
            )


        TILED = (
            '{% map_picker lat=lat lng=lng zoom=zoom pick_event="set_location" height="360px" '
            'tile_url=tile_url attribution="(c) Test tiles" attribution_url="https://example.com/tiles" %}'
        )
        SECOND = (
            '<div id="wrap-b" style="width:300px">{% map_picker lat=5 lng=5 zoom=4 pick_event="set_other" '
            'height="200px" tile_url=tile_url attribution="(c) Test tiles" %}</div>'
        )


        class Base(LiveView):
            tile_url = "/tiles/{z}/{x}/{y}.png"

            def mount(self, request, **kwargs):
                self.lat = 51.5
                self.lng = -0.12
                self.zoom = 13
                self.n = 0
                self.picked_a = "none"
                self.picked_b = "none"
                self.rejected = 0
                self.events = 0
                self.show = True

            @event_handler()
            def set_location(self, lat=None, lng=None, **kwargs):
                # The documented validating handler: the browser is not trusted.
                self.events += 1
                try:
                    lat, lng = float(lat), float(lng)
                except (TypeError, ValueError):
                    self.rejected += 1
                    return
                if not (math.isfinite(lat) and math.isfinite(lng)):
                    self.rejected += 1
                    return
                if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                    self.rejected += 1
                    return
                self.lat, self.lng = lat, lng
                self.picked_a = repr(lat) + "," + repr(lng)

            @event_handler()
            def set_other(self, lat=None, lng=None, **kwargs):
                self.picked_b = str(lat) + "," + str(lng)

            @event_handler()
            def move_server(self, **kwargs):
                self.lat, self.lng = 35.0, 139.0
                self.picked_a = "server"

            @event_handler()
            def zoom_server(self, **kwargs):
                self.zoom = 6

            @event_handler()
            def bump(self, **kwargs):
                self.n += 1

            @event_handler()
            def toggle(self, **kwargs):
                self.show = not self.show


        class Demo(Base):
            template = page(TILED, extra=SECOND)

        class NoMap(Base):
            template = page("")

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.show = False

        class Osm(Base):
            template = page('{% map_picker lat=lat lng=lng pick_event="set_location" height="300px" %}')

        class NoTiles(Base):
            tile_url = "http://127.0.0.1:9/{z}/{x}/{y}.png"
            template = page(TILED)

        class Empty(Base):
            tile_url = ""
            template = page(TILED)

        class Rtl(Base):
            template = page(TILED, html_attrs=' dir="rtl"')

        class Custom(Base):
            # The app registered its own MapPicker hook.
            template = page(
                TILED,
                head="<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "MapPicker: {mounted() { window.__appHook = (window.__appHook || 0) + 1; }}};</script>",
            )

        class Xss(Base):
            template = page(
                '{% map_picker lat=lat lng=lng pick_event="set_location" tile_url=tile_url '
                'attribution=hostile attribution_url=hostile label=hostile %}'
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.hostile = HOSTILE
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
        from django.conf import settings
        from django.urls import path
        from djust.websocket import LiveViewConsumer

        http_app = django_app if settings.HASHED else ASGIStaticFilesHandler(django_app)
        application = ProtocolTypeRouter({
            "http": http_app,
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


def write_project(root: Path) -> None:
    for name, body in PROJECT.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))


def start_server(root: Path, port: int, hashed: bool = False) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(root), "HASHED": "1" if hashed else "0"}
    py = os.environ.get("DJUST_SERVER_PYTHON", sys.executable)
    if hashed:
        subprocess.run(
            [py, "-m", "django", "collectstatic", "--noinput", "--settings", "cmpapp.settings"],
            cwd=root,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    log = root / f"server-{port}.log"
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
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nomap/", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start:\n" + "\n".join(log.read_text().splitlines()[-15:]))


class Run:
    """One browser session against one server: checks, console and network logs."""

    def __init__(self, browser, base, shots, failures, label, **context_args):
        self.base = base
        self.shots = shots
        self.failures = failures
        self.label = label
        self.context = browser.new_context(viewport={"width": 1000, "height": 900}, **context_args)
        self.page = self.context.new_page()
        self.console = []
        self.responses = []
        self.requests = []
        self.violations = []
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.console.append(("pageerror", str(e))))
        self.page.on("response", lambda r: self.responses.append((r.status, r.url)))
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.add_init_script(
            "document.addEventListener('securitypolicyviolation', e => "
            "(window.__csp = window.__csp || []).push(e.violatedDirective + ' ' + e.blockedURI + ' ' + (e.sample || '').slice(0, 60)));"
        )

    def check(self, ok, what):
        if not ok:
            self.failures.append(f"[{self.label}] {what}")
        print(("ok   " if ok else "FAIL ") + f"[{self.label}] {what}")

    def load(self, path, wait_map=True):
        self.page.goto(self.base + path)
        self.page.wait_for_function(
            "() => window.djust && window.djust.liveViewInstance && "
            "window.djust.liveViewInstance.viewMounted === true",
            timeout=15000,
        )
        if wait_map:
            self.page.wait_for_function(
                "() => document.querySelector('#wrap-a .leaflet-map-pane')", timeout=15000
            )
        self.page.wait_for_timeout(300)

    def text(self, selector):
        return (self.page.text_content(selector) or "").strip()

    def wait_text(self, selector, want, what):
        try:
            self.page.wait_for_function(
                "([s, w]) => document.querySelector(s).textContent.trim() === w",
                arg=[selector, want],
                timeout=6000,
            )
            self.check(True, what)
        except Exception:
            self.check(False, f"{what}: got {self.text(selector)!r}, want {want!r}")

    def wait_for(self, expr, what, timeout=6000):
        try:
            self.page.wait_for_function(expr, timeout=timeout)
            self.check(True, what)
        except Exception:
            self.check(False, what)

    def live(self, wrap="#wrap-a"):
        return self.page.evaluate(
            "(w) => (document.querySelector(w + ' .dj-map-picker__sr[role=status]') || {}).textContent || ''",
            wrap,
        ).strip()

    def box(self, selector):
        return self.page.eval_on_selector(
            selector,
            "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; }",
        )

    def marker(self, wrap="#wrap-a"):
        """Where the marker's anchor is, in viewport pixels."""
        return self.page.evaluate(
            """(w) => {
                const m = document.querySelector(w + ' .leaflet-marker-icon').getBoundingClientRect();
                const img = m.width > 20 && m.height > 30;
                return {x: m.left + (img ? 12 : m.width / 2), y: img ? m.bottom : m.top + m.height / 2};
            }""",
            wrap,
        )

    def hook_eval(self, expr, wrap="#wrap-a"):
        return self.page.evaluate(
            "(a) => { const el = document.querySelector(a[0] + ' [dj-hook=\"MapPicker\"]');"
            " const hook = window.djust.getHook(el); return (" + expr + "); }",
            [wrap],
        )

    def page_point(self, lat, lng, wrap="#wrap-a"):
        """Where a coordinate is on the page, in viewport pixels."""
        return self.page.evaluate(
            """([w, lat, lng]) => {
                const m = window.djust.getHook(document.querySelector(w + ' [dj-hook]'))._map;
                const b = m.getContainer().getBoundingClientRect();
                const p = m.latLngToContainerPoint([lat, lng]);
                return {x: b.left + p.x, y: b.top + p.y};
            }""",
            [wrap, lat, lng],
        )

    def map_center_click(self, wrap="#wrap-a", dx=0, dy=0):
        b = self.box(f"{wrap} .dj-map-picker__map")
        x, y = b["x"] + b["w"] / 2 + dx, b["y"] + b["h"] / 2 + dy
        self.page.mouse.click(x, y)
        return x, y

    def shot(self, name):
        self.page.screenshot(path=str(self.shots / f"{self.label}-{name}.png"))

    def close(self):
        self.context.close()


def near(a, b, tol):
    return abs(a - b) <= tol


def main() -> int:
    failures: list = []
    shots = Path(os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch5-map-"))
    shots.mkdir(parents=True, exist_ok=True)
    chromium = os.environ.get("CHROMIUM_EXECUTABLE") or None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root)
        port, hashed_port = free_port(), free_port()
        server = start_server(root, port)
        hashed_server = start_server(root, hashed_port, hashed=True)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=chromium)
                run_main(browser, f"http://localhost:{port}", shots, failures)
                run_hashed(browser, f"http://localhost:{hashed_port}", shots, failures)
                browser.close()
        finally:
            for proc in (server, hashed_server):
                proc.terminate()
                proc.wait(timeout=10)
    print(f"screenshots: {shots}")
    if failures:
        print("\n%d FAILED:\n  - " % len(failures) + "\n  - ".join(failures))
        return 1
    print("\nall checks passed")
    return 0


def run_main(browser, base, shots, failures):
    r = Run(browser, base, shots, failures, "main")
    page = r.page

    # ---- a page without a map never fetches Leaflet --------------------------
    r.load("/nomap/", wait_map=False)
    r.check(not [u for u in r.requests if "leaflet" in u], "no-map page: Leaflet is not requested")
    r.check(
        page.evaluate("() => typeof window.L") == "undefined",
        "no-map page: window.L is not defined",
    )

    # ---- two maps on one page ------------------------------------------------
    r.requests.clear()
    r.responses.clear()
    r.load("/")
    r.check(
        not [t for k, t in r.console if "No hook registered" in t],
        "no 'No hook registered' warning",
    )
    leaflet_js = [u for u in r.requests if u.endswith("/leaflet.js")]
    leaflet_css = [u for u in r.requests if u.endswith("/leaflet.css")]
    r.check(
        len(leaflet_js) == 1 and len(leaflet_css) == 1,
        f"two maps: one Leaflet script and one stylesheet ({len(leaflet_js)}, {len(leaflet_css)})",
    )
    r.check(
        page.evaluate("() => window.L && window.L.version") == "1.9.4",
        "window.L is the vendored 1.9.4",
    )
    r.check(
        page.evaluate(
            '() => [...document.head.querySelectorAll(\'script[src*="leaflet"], link[href*="leaflet"]\')]'
            ".every(n => /^sha384-/.test(n.getAttribute('integrity') || ''))"
        ),
        "the injected tags carry Subresource Integrity",
    )
    r.check(page.locator(".leaflet-map-pane").count() == 2, "both maps are built")
    page.wait_for_timeout(700)
    gens = page.evaluate(
        "() => [...document.querySelectorAll('[dj-hook=MapPicker]')].map(e => window.djust.getHook(e)._gen)"
    )
    r.check(
        gens == [1, 1],
        f"the WebSocket mount's morph does not wipe the maps: each was built once ({gens})",
    )
    tile_requests = [u for u in r.requests if "/tiles/" in u]
    r.check(
        tile_requests and len(tile_requests) == len(set(tile_requests)),
        f"each tile was requested once ({len(tile_requests)} requests)",
    )
    r.check(
        not [x for x in r.responses if x[0] >= 400],
        f"no request answered with an error status: {[x for x in r.responses if x[0] >= 400][:3]}",
    )
    r.check(
        page.evaluate(
            "() => { const i = document.querySelector('#wrap-a img.leaflet-marker-icon'); "
            "return !!i && i.complete && i.naturalWidth === 25 && i.naturalHeight === 41; }"
        ),
        "marker: the explicit icon URL loaded (25x41)",
    )
    r.check(
        page.evaluate(
            "() => { const i = document.querySelector('#wrap-a img.leaflet-marker-shadow'); "
            "return !!i && i.complete && i.naturalWidth === 41; }"
        ),
        "marker: the shadow loaded",
    )
    r.check(
        page.evaluate(
            "() => { const t = [...document.querySelectorAll('#wrap-a img.leaflet-tile')]; "
            "return t.length > 0 && t.every(i => i.complete && i.naturalWidth === 256); }"
        ),
        "tiles loaded from the template",
    )
    attribution = r.text("#wrap-a .dj-map-picker__attribution")
    r.check(attribution == "Leaflet | (c) Test tiles", f"attribution text: {attribution!r}")
    r.check(
        page.eval_on_selector_all(
            "#wrap-a .dj-map-picker__attribution a",
            "els => els.map(e => e.getAttribute('href') + '|' + e.rel)",
        )
        == [
            "https://leafletjs.com|noopener noreferrer",
            "https://example.com/tiles|noopener noreferrer",
        ],
        "attribution links open safely",
    )
    r.check(
        r.text("#wrap-a .dj-map-picker__coords") == "51.50000\u00b0 N, 0.12000\u00b0 W",
        "readout shows the initial position",
    )
    r.shot("01-two-maps")

    # ---- the pointer ---------------------------------------------------------
    cx, cy = r.map_center_click(dx=60, dy=-40)
    r.wait_for(
        "() => document.querySelector('#picked-a').textContent !== 'none'",
        "click: the server received a location",
    )
    got = r.text("#picked-a")
    lat, lng = (float(v) for v in got.split(","))
    px = r.page_point(lat, lng)
    r.check(
        near(px["x"], cx, 1.5) and near(px["y"], cy, 1.5),
        f"click: the server's coordinates are the clicked pixel ({got} -> {px} vs {cx},{cy})",
    )
    r.check(
        len(got.split(",")[0].split(".")[1]) <= 6 and len(got.split(",")[1].split(".")[1]) <= 6,
        "click: at most six decimals",
    )
    m = r.marker()
    r.check(
        near(m["x"], cx, 2) and near(m["y"], cy, 2),
        f"click: the marker is under the pointer ({m} vs {cx},{cy})",
    )
    r.check(r.live().startswith("Location chosen: "), f"click: announced ({r.live()!r})")
    r.check(r.text("#events") == "1", "click: exactly one event reached the server")
    r.wait_for(
        "() => document.querySelector('#wrap-a [dj-hook]').getAttribute('data-lat') === '"
        + str(lat)
        + "'",
        "click: the echo re-render carries the picked latitude",
    )
    m2 = r.marker()
    r.check(
        near(m2["x"], m["x"], 1) and near(m2["y"], m["y"], 1),
        "click: the server's echo does not move the marker",
    )

    # the second map is independent
    b = r.box("#wrap-b .dj-map-picker__map")
    page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
    r.wait_for(
        "() => document.querySelector('#picked-b').textContent !== 'none'",
        "second map: its own event",
    )
    r.check(
        r.text("#events") == "1" and r.text("#picked-a") == got,
        "second map: did not touch the first",
    )

    # ---- the keyboard --------------------------------------------------------
    r.load("/")
    start = r.marker()
    page.focus("#wrap-a .dj-map-picker__map")
    r.check(
        page.evaluate("() => document.activeElement.classList.contains('dj-map-picker__map')"),
        "keyboard: the map takes focus",
    )
    r.check(
        page.evaluate(
            "() => { const e = document.activeElement; const d = document.getElementById(e.getAttribute('aria-describedby'));"
            " return e.getAttribute('aria-label') === 'Map picker' && !!d && /Enter/.test(d.textContent); }"
        ),
        "keyboard: named and described",
    )
    page.mouse.click(900, 40)  # empty space: sequential focus starts from the top of the page
    for _ in range(12):
        page.keyboard.press("Tab")
        if page.evaluate("() => document.activeElement.classList.contains('dj-map-picker__map')"):
            break
    on_map = page.evaluate("() => document.activeElement.classList.contains('dj-map-picker__map')")
    ring = page.evaluate(
        "() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; }"
    )
    r.check(
        on_map and ring[0] != "none" and ring[1] >= 2,
        f"keyboard: Tab reaches the map and it shows a focus ring (on the map: {on_map}, ring {ring})",
    )
    r.shot("02-focused")
    for _ in range(3):
        page.keyboard.press("ArrowRight")
    m = r.marker()
    r.check(
        near(m["x"] - start["x"], 24, 1) and near(m["y"], start["y"], 1),
        f"ArrowRight x3 moves 24px east ({m['x'] - start['x']:.1f}, {m['y'] - start['y']:.1f})",
    )
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Shift+ArrowDown")
    m = r.marker()
    r.check(
        near(m["y"] - start["y"], 56, 1),
        f"ArrowUp then Shift+ArrowDown nets 56px south ({m['y'] - start['y']:.1f})",
    )
    r.check(r.text("#events") == "0", "keyboard: moving sends nothing")
    r.check("Press Enter to choose" in r.live(), f"keyboard: a move is announced ({r.live()!r})")
    r.check(
        "dj-map-picker__coords--pending"
        in (page.get_attribute("#wrap-a .dj-map-picker__coords", "class") or ""),
        "keyboard: the readout shows the move as pending",
    )
    pending = r.marker()
    page.keyboard.press("Enter")
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "Enter: one event reaches the server",
    )
    chosen = r.text("#picked-a")
    lat, lng = (float(v) for v in chosen.split(","))
    px = r.page_point(lat, lng)
    r.check(
        near(px["x"], pending["x"], 1.5) and near(px["y"], pending["y"], 1.5),
        f"Enter: the marker's position was sent ({chosen} -> {px} vs {pending})",
    )
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Escape")
    r.check(
        r.marker()["x"] == pending["x"] or near(r.marker()["x"], pending["x"], 1),
        "Escape: the marker goes back to the chosen location",
    )
    r.check("Back at the chosen location" in r.live(), f"Escape: announced ({r.live()!r})")
    r.check(r.text("#events") == "1", "Escape: sends nothing")
    zoom0 = r.hook_eval("hook._map.getZoom()")
    page.keyboard.press("+")
    r.check(r.hook_eval("hook._map.getZoom()") == zoom0 + 1, "+ zooms in")
    page.keyboard.press("-")
    page.keyboard.press("-")
    r.check(r.hook_eval("hook._map.getZoom()") == zoom0 - 1, "- zooms out")
    # a held key does not flood
    page.evaluate(
        "() => { const m = document.querySelector('#wrap-a .dj-map-picker__map');"
        " for (let i = 0; i < 20; i++) m.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', repeat: i > 0, bubbles: true, cancelable: true})); }"
    )
    r.wait_for(
        "() => document.querySelector('#events').textContent === '2'",
        "a held Enter chooses once, not once per repeat",
    )
    r.check(r.text("#events") == "2", "held Enter: exactly two events in total")

    # the marker stays on screen
    r.load("/")
    page.focus("#wrap-a .dj-map-picker__map")
    for _ in range(40):
        page.keyboard.press("Shift+ArrowRight")
    for _ in range(20):
        page.keyboard.press("Shift+ArrowDown")
    mb, cb = r.marker(), r.box("#wrap-a .dj-map-picker__map")
    r.check(
        cb["x"] <= mb["x"] <= cb["x"] + cb["w"] and cb["y"] <= mb["y"] <= cb["y"] + cb["h"],
        f"the marker stays inside the map after long keyboard moves ({mb} in {cb})",
    )

    # ---- the server stays in charge -----------------------------------------
    r.load("/")
    page.focus("#wrap-a .dj-map-picker__map")
    page.keyboard.press("Shift+ArrowRight")
    pending = r.marker()
    created = r.hook_eval("hook._map._leaflet_id")
    page.evaluate("() => window.djust.handleEvent('bump', {})")
    r.wait_text("#n", "1", "an unrelated server patch happened")
    r.check(
        near(r.marker()["x"], pending["x"], 1), "an unrelated patch leaves a keyboard move alone"
    )
    r.check(
        r.hook_eval("hook._map._leaflet_id") == created,
        "an unrelated patch does not rebuild the map",
    )
    page.keyboard.press("Enter")
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "the move is still chosen after the patch",
    )
    page.keyboard.press("ArrowRight")
    page.click("#move")
    r.wait_text("#picked-a", "server", "server move: handler ran")
    r.wait_for(
        "() => { const m = window.djust.getHook(document.querySelector('#wrap-a [dj-hook]'))._marker.getLatLng();"
        " return Math.abs(m.lat - 35) < 1e-6 && Math.abs(m.lng - 139) < 1e-6; }",
        "server move: the marker follows the server's coordinates",
    )
    page.click("#zoom-server")
    r.wait_for(
        "() => window.djust.getHook(document.querySelector('#wrap-a [dj-hook]'))._map.getZoom() === 6",
        "server zoom: the map zooms",
    )

    # a patch in the middle of a drag
    r.load("/")
    b = r.box("#wrap-a .dj-map-picker__map")
    before = r.hook_eval("[hook._map.getCenter().lat, hook._map.getCenter().lng]")
    map_id = r.hook_eval("hook._map._leaflet_id")
    cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 40, cy + 10, steps=4)
    page.evaluate("() => window.djust.handleEvent('bump', {})")
    r.wait_text("#n", "1", "a patch landed in the middle of a drag")
    page.mouse.move(cx + 120, cy + 30, steps=4)
    page.mouse.up()
    after = r.hook_eval("[hook._map.getCenter().lat, hook._map.getCenter().lng]")
    r.check(
        after[1] < before[1],
        f"drag: the map kept panning through the patch ({before[1]:.4f} -> {after[1]:.4f})",
    )
    r.check(r.hook_eval("hook._map._leaflet_id") == map_id, "drag: and was not rebuilt")
    r.check(r.text("#events") == "0", "drag: a drag is not a pick")

    # ---- resize ---------------------------------------------------------------
    r.load("/")
    size = (
        "() => { const m = window.djust.getHook(document.querySelector('#wrap-a [dj-hook]'))._map;"
        " const e = m.getContainer(); const s = m.getSize(); return [s.x === e.clientWidth, s.y === e.clientHeight, s.x, s.y]; }"
    )
    r.check(
        page.evaluate(size)[:2] == [True, True], "resize: the map starts at its container's size"
    )
    page.evaluate(
        "() => { document.querySelector('#wrap-a .dj-map-picker').style.height = '240px'; }"
    )
    r.wait_for(
        "() => { const r = (" + size + ")(); return r[0] && r[1] && r[3] === 240; }",
        "resize: the map follows a new container height (240)",
    )
    page.evaluate("() => { document.querySelector('#wrap-a').style.width = '300px'; }")
    r.wait_for(
        "() => { const r = (" + size + ")(); return r[0] && r[1] && r[2] === 298; }",
        "resize: the map follows a new container width (298)",
    )
    cx, cy = r.map_center_click()
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "resize: a click after resizing still picks",
    )
    m = r.marker()
    r.check(
        near(m["x"], cx, 2) and near(m["y"], cy, 2), "resize: and the marker is under the pointer"
    )
    page.evaluate("() => { document.querySelector('#wrap-a').style.display = 'none'; }")
    page.wait_for_timeout(100)
    page.evaluate("() => { document.querySelector('#wrap-a').style.display = ''; }")
    r.wait_for(
        "() => { const r = (" + size + ")(); return r[0] && r[1] && r[2] === 298; }",
        "resize: hidden then shown again, the map has its size back",
    )

    # ---- toggling the map off and on -----------------------------------------
    r.load("/")
    for i in range(5):
        page.click("#toggle")
        r.wait_for(
            "() => !document.querySelector('#wrap-a')", f"toggle {i + 1}: the map is removed"
        )
        page.click("#toggle")
        r.wait_for(
            "() => document.querySelector('#wrap-a .leaflet-map-pane')",
            f"toggle {i + 1}: and built again",
        )
    r.check(
        page.locator("#wrap-a .leaflet-map-pane").count() == 1,
        "toggle: one map pane after five cycles",
    )
    r.check(
        page.locator("#wrap-a .leaflet-control-zoom").count() == 1,
        "toggle: one zoom control after five cycles",
    )
    r.check(
        page.locator(".leaflet-container").count() == 2,
        "toggle: two Leaflet containers on the page (not more)",
    )
    cx, cy = r.map_center_click()
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "toggle: a click sends exactly one event",
    )
    page.wait_for_timeout(200)
    r.check(r.text("#events") == "1", "toggle: and still exactly one a moment later")
    page.focus("#wrap-a .dj-map-picker__map")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    r.wait_for(
        "() => document.querySelector('#events').textContent === '2'",
        "toggle: Enter sends exactly one more",
    )
    page.wait_for_timeout(200)
    r.check(r.text("#events") == "2", "toggle: no double-binding of the keyboard")

    # ---- wheel zoom needs focus ----------------------------------------------
    r.load("/")
    z = r.hook_eval("hook._map.getZoom()")
    b = r.box("#wrap-a .dj-map-picker__map")
    page.mouse.move(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
    page.mouse.wheel(0, -300)
    page.wait_for_timeout(300)
    r.check(
        r.hook_eval("hook._map.getZoom()") == z,
        "wheel: does not zoom a map that has no focus (the page can scroll)",
    )
    page.focus("#wrap-a .dj-map-picker__map")
    page.mouse.wheel(0, -400)
    r.wait_for(
        "() => window.djust.getHook(document.querySelector('#wrap-a [dj-hook]'))._map.getZoom() > "
        + str(z),
        "wheel: zooms while the map has focus",
    )

    # ---- touch -----------------------------------------------------------------
    r.close()
    touch = Run(browser, base, shots, failures, "touch", has_touch=True)
    touch.load("/")
    b = touch.box("#wrap-a .dj-map-picker__map")
    touch.page.touchscreen.tap(b["x"] + b["w"] / 2 + 30, b["y"] + b["h"] / 2 + 20)
    touch.wait_for(
        "() => document.querySelector('#events').textContent === '1'", "touch: a tap picks"
    )
    m = touch.marker()
    touch.check(
        near(m["x"], b["x"] + b["w"] / 2 + 30, 2) and near(m["y"], b["y"] + b["h"] / 2 + 20, 2),
        "touch: the marker is under the finger",
    )
    touch.close()

    # ---- the default tiles ------------------------------------------------------
    osm = Run(browser, base, shots, failures, "osm")
    osm_requests = []

    def osm_route(route):
        osm_requests.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="image/png",
            body=urllib.request.urlopen(base + "/tiles/1/1/1.png").read(),
        )

    osm.context.route("https://tile.openstreetmap.org/**", osm_route)
    osm.load("/osm/")
    osm.wait_for(
        "() => document.querySelector('#wrap-a img.leaflet-tile') || document.querySelector('.leaflet-tile')",
        "default tiles: tiles are requested",
    )
    osm.page.wait_for_timeout(500)
    osm.check(
        osm_requests
        and all(
            u.startswith("https://tile.openstreetmap.org/") and u.endswith(".png")
            for u in osm_requests
        ),
        f"default tiles: OpenStreetMap's standard tile server ({osm_requests[:1]})",
    )
    text = osm.text(".dj-map-picker__attribution")
    osm.check(
        text == "Leaflet | \u00a9 OpenStreetMap contributors",
        f"default tiles: the required attribution ({text!r})",
    )
    osm.check(
        osm.page.eval_on_selector_all(
            ".dj-map-picker__attribution a", "els => els.map(e => e.getAttribute('href'))"
        )
        == ["https://leafletjs.com", "https://www.openstreetmap.org/copyright"],
        "default tiles: attribution links to OpenStreetMap's copyright page",
    )
    osm.shot("01-default-tiles")
    osm.close()

    # ---- tiles unreachable ------------------------------------------------------
    nt = Run(browser, base, shots, failures, "notiles")
    nt.load("/notiles/")
    nt.wait_for(
        "() => { const n = document.querySelector('.dj-map-picker__notice'); return n && !n.hidden && /tiles could not be loaded/.test(n.textContent); }",
        "tiles unreachable: a notice says so",
    )
    nt.check(
        "Map tiles could not be loaded" in nt.live(),
        f"tiles unreachable: announced ({nt.live()!r})",
    )
    nt.shot("01-tiles-failed")
    cx, cy = nt.map_center_click(dx=20, dy=20)
    nt.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "tiles unreachable: a click still picks",
    )
    m = nt.marker()
    nt.check(
        near(m["x"], cx, 2) and near(m["y"], cy, 2),
        "tiles unreachable: the marker is under the pointer",
    )
    nt.check(
        nt.page.locator(".dj-map-picker__attribution").count() == 1,
        "tiles unreachable: attribution still shown",
    )
    nt.page.focus(".dj-map-picker__map")
    nt.page.keyboard.press("ArrowDown")
    nt.page.keyboard.press("Enter")
    nt.wait_for(
        "() => document.querySelector('#events').textContent === '2'",
        "tiles unreachable: the keyboard picks",
    )
    nt.close()

    # ---- offline after load -----------------------------------------------------
    off = Run(browser, base, shots, failures, "offline")
    off.load("/")
    off.context.set_offline(True)
    off.page.wait_for_timeout(200)
    cx, cy = off.map_center_click(dx=-30)
    off.check(near(off.marker()["x"], cx, 2), "offline: the marker follows a click")
    off.page.focus("#wrap-a .dj-map-picker__map")
    off.page.keyboard.press("ArrowRight")
    off.check(
        off.page.evaluate(
            "() => document.querySelector('#wrap-a .dj-map-picker__coords').textContent.length > 0"
        ),
        "offline: keyboard moves still update the readout",
    )
    off.context.set_offline(False)
    off.close()

    # ---- no tiles configured ----------------------------------------------------
    em = Run(browser, base, shots, failures, "empty")
    urllib.request.urlopen(base + "/tilelog/?reset=1").read()
    em.load("/empty/")
    em.page.wait_for_timeout(400)
    served = json.loads(urllib.request.urlopen(base + "/tilelog/").read())["n"]
    em.check(
        not [u for u in em.requests if "/tiles/" in u] and served == 0,
        f"tile_url='': no tile request ({[u for u in em.requests if '/tiles/' in u][:2]}, server saw {served})",
    )
    em.check(em.page.locator("img.leaflet-tile").count() == 0, "tile_url='': no tile elements")
    em.check(
        em.page.locator(".dj-map-picker__notice").count() == 0
        or em.page.evaluate("() => document.querySelector('.dj-map-picker__notice').hidden"),
        "tile_url='': no 'tiles failed' notice",
    )
    em.map_center_click()
    em.wait_for(
        "() => document.querySelector('#events').textContent === '1'",
        "tile_url='': the picker works",
    )
    em.shot("01-no-tiles")
    em.close()

    # ---- RTL ----------------------------------------------------------------------
    rtl = Run(browser, base, shots, failures, "rtl")
    rtl.load("/rtl/")
    rtl.shot("01-rtl")
    cx, cy = rtl.map_center_click(dx=70, dy=-30)
    rtl.wait_for(
        "() => document.querySelector('#events').textContent === '1'", "rtl: a click picks"
    )
    m = rtl.marker()
    rtl.check(
        near(m["x"], cx, 2) and near(m["y"], cy, 2),
        f"rtl: the marker is under the pointer ({m} vs {cx},{cy})",
    )
    got = rtl.text("#picked-a")
    lat, lng = (float(v) for v in got.split(","))
    px = rtl.page_point(lat, lng)
    rtl.check(
        near(px["x"], cx, 1.5) and near(px["y"], cy, 1.5),
        f"rtl: the coordinates are the clicked pixel ({got} -> {px})",
    )
    right_of = rtl.page.evaluate(
        "() => { const c = document.querySelector('#wrap-a .dj-map-picker__map').getBoundingClientRect();"
        " const z = document.querySelector('#wrap-a .leaflet-control-zoom').getBoundingClientRect();"
        " const a = document.querySelector('#wrap-a .dj-map-picker__attribution').getBoundingClientRect();"
        " return [z.left >= c.left - 1 && z.right <= c.right + 1, a.left >= c.left - 1 && a.right <= c.right + 1]; }"
    )
    rtl.check(
        right_of == [True, True], f"rtl: controls and attribution stay inside the map {right_of}"
    )
    rtl.check(
        rtl.page.evaluate(
            "() => getComputedStyle(document.querySelector('#wrap-a .dj-map-picker__coords')).direction"
        )
        == "ltr",
        "rtl: the readout is laid out left-to-right",
    )
    rtl.page.focus("#wrap-a .dj-map-picker__map")
    before = rtl.marker()
    rtl.page.keyboard.press("ArrowRight")
    after = rtl.marker()
    rtl.check(
        after["x"] > before["x"],
        "rtl: ArrowRight moves the marker east (right), geography not text direction",
    )
    rtl.close()

    # ---- an app's own hook ---------------------------------------------------------
    cu = Run(browser, base, shots, failures, "custom")
    cu.load("/custom/", wait_map=False)
    cu.page.wait_for_timeout(500)
    cu.check(cu.page.evaluate("() => window.__appHook") == 1, "custom: the app's own hook ran once")
    cu.check(
        cu.page.evaluate("() => typeof window.L") == "undefined",
        "custom: the shipped hook did not load Leaflet",
    )
    cu.check(not [u for u in cu.requests if "leaflet" in u], "custom: no Leaflet request")
    cu.check(
        cu.page.evaluate(
            "() => document.querySelector('.dj-map-picker__map').hasAttribute('tabindex')"
        )
        is False,
        "custom: the shipped hook left the markup alone",
    )
    cu.close()

    # ---- hostile attribution and label -----------------------------------------------
    xs = Run(browser, base, shots, failures, "xss")
    xs.load("/xss/")
    xs.check(xs.page.evaluate("() => window.__pwn") is None, "xss: nothing ran")
    xs.check(
        xs.page.locator(".dj-map-picker img[onerror], .dj-map-picker b").count() == 0,
        "xss: no element was created from attribution or label",
    )
    attr = xs.text(".dj-map-picker__attribution")
    xs.check(
        attr == "Leaflet | <img src=x onerror=window.__pwn=1><b>x</b>",
        f"xss: attribution is text ({attr!r})",
    )
    xs.check(
        xs.page.eval_on_selector_all(
            ".dj-map-picker__attribution a", "els => els.map(e => e.getAttribute('href'))"
        )
        == ["https://leafletjs.com"],
        "xss: a non-http attribution URL is not a link",
    )
    xs.check(
        xs.page.get_attribute(".dj-map-picker__map", "aria-label")
        == "<img src=x onerror=window.__pwn=1><b>x</b>",
        "xss: the label is the accessible name, as text",
    )
    xs.close()

    # ---- a strict Content-Security-Policy ------------------------------------------------
    cs = Run(browser, base, shots, failures, "csp")
    # What djust's own client reports on this policy with no map on the page
    # (its injected <style> elements) is the baseline; the map must add nothing.
    cs.load("/csp/nomap/", wait_map=False)
    cs.page.wait_for_timeout(300)
    baseline = cs.page.evaluate("() => window.__csp || []")
    cs.load("/csp/")
    cs.page.wait_for_timeout(300)
    violations = [v for v in cs.page.evaluate("() => window.__csp || []") if v not in baseline]
    cs.check(
        not violations,
        f"csp: the maps add no violation (no eval, no <style>, no inline script): {violations[:3]} (baseline {baseline[:1]})",
    )
    cs.check(cs.page.locator(".leaflet-map-pane").count() == 2, "csp: maps are built")
    cs.map_center_click()
    cs.wait_for("() => document.querySelector('#events').textContent === '1'", "csp: picking works")
    cs.close()

    # ---- a tampered Leaflet ----------------------------------------------------------------
    tp = Run(browser, base, shots, failures, "sri")

    def tamper(route):
        response = route.fetch()
        route.fulfill(response=response, body=response.body() + b"\n;window.__tampered=1;")

    tp.context.route("**/leaflet.js", tamper)
    tp.load("/", wait_map=False)
    tp.page.wait_for_timeout(800)
    tp.check(
        tp.page.evaluate("() => window.__tampered") is None, "sri: the tampered script did not run"
    )
    tp.check(
        tp.page.evaluate("() => typeof window.L") == "undefined", "sri: Leaflet is not defined"
    )
    tp.wait_for(
        "() => { const n = document.querySelector('.dj-map-picker__notice'); return n && n.textContent === 'The map could not be loaded.'; }",
        "sri: the map says it could not be loaded",
    )
    tp.check(
        len([t for k, t in tp.console if "Leaflet did not load" in t]) == 1,
        "sri: one console warning, not one per map",
    )
    tp.check(not [t for k, t in tp.console if k == "pageerror"], "sri: no page error")
    tp.shot("01-load-failed")
    tp.close()

    # ---- reduced motion --------------------------------------------------------------------
    rm = Run(browser, base, shots, failures, "motion", reduced_motion="reduce")
    rm.load("/")
    rm.check(
        rm.hook_eval(
            "hook._map.options.zoomAnimation === false && hook._map.options.fadeAnimation === false"
        ),
        "reduced motion: no zoom or fade animation",
    )
    rm.close()

    # ---- validation on the server (the documented handler) ----------------------------------
    sv = Run(browser, base, shots, failures, "server")
    sv.load("/")
    for payload in (
        '{lat: "abc", lng: 1}',
        "{lat: 91, lng: 0}",
        "{lat: 0, lng: 181}",
        "{lat: null, lng: null}",
        "{lat: 1e999, lng: 1}",
    ):
        sv.page.evaluate(f"() => window.djust.handleEvent('set_location', {payload})")
    sv.wait_for(
        "() => document.querySelector('#rejected').textContent === '5'",
        "server: the documented handler rejects forged numbers",
    )
    sv.check(sv.text("#picked-a") == "none", "server: and keeps its state")
    sv.close()

    # ---- console cleanliness over the main run -----------------------------------------------
    r2 = Run(browser, base, shots, failures, "final")
    r2.load("/")
    r2.map_center_click()
    r2.page.focus("#wrap-a .dj-map-picker__map")
    r2.page.keyboard.press("ArrowRight")
    errors = [t for k, t in r2.console if k in ("error", "pageerror", "warning")]
    r2.check(
        not errors, f"no console errors, warnings or page errors on the main page: {errors[:3]}"
    )
    r2.close()


def run_hashed(browser, base, shots, failures):
    r = Run(browser, base, shots, failures, "hashed")
    r.load("/")
    cfg = json.loads(r.page.get_attribute("#wrap-a [dj-hook]", "data-leaflet"))
    r.check(
        all(
            "." in cfg[k].rsplit("/", 1)[1].rsplit(".", 1)[0]
            for k in ("js", "css", "icon", "icon2x", "shadow")
        ),
        f"hashed: the hook is given hashed names ({cfg['js']}, {cfg['icon']})",
    )
    r.check(
        not [x for x in r.responses if x[0] >= 400 and "/tiles/" not in x[1]],
        f"hashed: no asset answered with an error status: {[x for x in r.responses if x[0] >= 400][:3]}",
    )
    r.check(
        r.page.evaluate(
            "() => { const i = document.querySelector('#wrap-a img.leaflet-marker-icon'); "
            "return !!i && i.complete && i.naturalWidth === 25; }"
        ),
        "hashed: the marker icon (explicit URL) loads from its hashed name",
    )
    r.check(
        r.page.evaluate("() => window.L && window.L.Icon.Default.imagePath") in (None, ""),
        "hashed: Leaflet's own icon-path detection never ran",
    )
    # The stylesheet's own image reference, rewritten by the storage, resolves.
    ok = r.page.evaluate(
        """async () => {
            const link = [...document.querySelectorAll('link[rel=stylesheet]')].find(l => /leaflet/.test(l.href));
            const css = await (await fetch(link.href)).text();
            const urls = [...css.matchAll(/url\\("(images\\/[^"]+)"\\)/g)].map(m => new URL(m[1], link.href).href);
            const out = [];
            for (const u of urls) out.push((await fetch(u)).status);
            return {n: urls.length, statuses: out};
        }"""
    )
    r.check(
        ok["n"] == 3 and all(s == 200 for s in ok["statuses"]),
        f"hashed: the stylesheet's rewritten images all resolve {ok}",
    )
    r.map_center_click()
    r.wait_for(
        "() => document.querySelector('#events').textContent === '1'", "hashed: picking works"
    )
    r.shot("01-hashed")
    r.close()


if __name__ == "__main__":
    sys.exit(main())
