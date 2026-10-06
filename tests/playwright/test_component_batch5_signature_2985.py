#!/usr/bin/env python3
"""Real-browser check of the SignaturePad hook (#2985, batch 5).

The component rendered a ``dj-hook`` that no shipped script answered. This drives
``signature-pad.js`` as a reader would, against a real LiveView over a real
WebSocket, and checks what the server actually received and validated with
``djust.components.signature.decode_signature_data_url``:

* real mouse, touch (CDP touch events: the page does not scroll while drawing)
  and pen (pressure) input draws on the canvas; the ink is where the pointer
  was, at device pixel ratios 1, 2 and 3, on a narrowed container and in RTL;
* Clear, Undo and the empty state; Save sends a PNG the server's helper accepts,
  at the pad's own size (not the screen's); the hidden form field carries it;
* the 200 KB cap: a very detailed signature is re-rendered smaller until it fits
  or refused; at the default 64 KiB WebSocket frame limit the server answers
  "Message too large" and the hook sends it again, smaller, and it arrives;
* the keyboard alternative: Tab, Enter, type a name, Enter: a signature with
  ink arrives without a pointer;
* forged values sent straight to the event are refused by the documented
  handler (not PNG, bomb header, bad CRC, oversized...);
* a patch in the middle of a stroke, the server disabling the pad, toggling the
  component five times (one handler, no leak), a resize that keeps the drawing;
* hostile label and typed name stay text; an app's own hook keeps its place.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch5_signature_2985.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. ``SHOTS_DIR``
(optional) is where screenshots go. Exits 0 on success, non-zero with the
failures. Not part of the CI suite (see README.md).
"""

import base64
import os
import random
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
    "cmpapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [
            path("", views.Demo.as_view()),
            path("rtl/", views.Rtl.as_view()),
            path("custom/", views.Custom.as_view()),
            path("xss/", views.Xss.as_view()),
        ]
    """,
    "cmpapp/views.py": """
        from djust import LiveView
        from djust.decorators import event_handler
        from djust.components.signature import SignatureError, decode_signature_data_url


        def page(body, head="", html_attrs=""):
            return (
                "{% load live_tags djust_components %}<!DOCTYPE html><html" + html_attrs + "><head><title>s</title>"
                "{% djust_client_config %}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + head +
                '<script src="/static/djust_components/signature-pad.js" defer></script></head><body>'
                '<div dj-root><h1>signatures</h1>'
                '<p>saved: <span id="saved">{{ saved_text }}</span> n: <span id="n">{{ saved }}</span>'
                ' rejected: <span id="rejected">{{ rejected }}</span> why: <span id="why">{{ why }}</span>'
                ' bump: <span id="bump-n">{{ bumps }}</span> mode: <span id="state">{{ state }}</span></p>'
                '<button id="bump" dj-click="bump">bump</button>'
                '<button id="toggle" dj-click="toggle">toggle</button>'
                '<button id="lock" dj-click="lock">lock</button>'
                + body +
                '</div></body></html>'
            )


        MAIN = (
            '<section id="a">{% if show %}{% signature_pad name="sig" save_event="save_signature" width=400 height=200 pen_color="#102030" pen_width=3 disabled=locked %}{% endif %}</section>'
            '<section id="b">{% signature_pad name="small" save_event="save_small" width=400 height=200 max_bytes=12000 %}</section>'
            '<section id="c">{% signature_pad name="tiny" save_event="save_tiny" width=400 height=200 max_bytes=2048 %}</section>'
            '<section id="d">{% signature_pad name="drawonly" save_event="save_signature" typed=False %}</section>'
        )


        class Base(LiveView):
            def mount(self, request, **kwargs):
                self.saved = 0
                self.rejected = 0
                self.why = ""
                self.saved_text = ""
                self.bumps = 0
                self.show = True
                self.locked = False
                self.state = ""
                self.log = []

            def _record(self, signature, label):
                try:
                    image = decode_signature_data_url(signature)
                except SignatureError as exc:
                    self.rejected += 1
                    self.why = str(exc)
                    return
                self.saved += 1
                self.log.append(label + ":" + str(image.width) + "x" + str(image.height) + ":" + str(len(image.data)) + ":" + image.data[:4].hex() + ":" + str(len(signature)))
                self.saved_text = " | ".join(self.log)

            @event_handler()
            def save_signature(self, signature="", **kwargs):
                # The documented handler: the value is the browser's claim.
                self._record(signature, "sig")

            @event_handler()
            def save_small(self, signature="", **kwargs):
                self._record(signature, "small")

            @event_handler()
            def save_tiny(self, signature="", **kwargs):
                self._record(signature, "tiny")

            @event_handler()
            def bump(self, **kwargs):
                self.bumps += 1

            @event_handler()
            def toggle(self, **kwargs):
                self.show = not self.show

            @event_handler()
            def lock(self, **kwargs):
                self.locked = not self.locked
                self.state = "locked" if self.locked else "open"


        class Demo(Base):
            template = page(MAIN)

        class Rtl(Base):
            template = page(MAIN, html_attrs=' dir="rtl"')

        class Custom(Base):
            template = page(
                MAIN,
                head="<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "SignaturePad: {mounted() { window.__appHook = (window.__appHook || 0) + 1; }}};</script>",
            )

        class Xss(Base):
            template = page(
                '{% signature_pad name="s" save_event="save_signature" label=hostile %}'
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.hostile = "<img src=x onerror=window.__pwn=1><b>x</b>"
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


# ---- hostile values ----------------------------------------------------------------------


def chunk(kind, body):
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def ihdr(w, h):
    return chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))


MAGIC = b"\x89PNG\r\n\x1a\n"


def png(w=4, h=2, raw=None):
    raw = raw if raw is not None else (b"\x00" + b"\x00" * (w * 4)) * h
    return MAGIC + ihdr(w, h) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def url(data):
    return "data:image/png;base64," + base64.b64encode(data).decode()


FORGED = {
    "not a PNG": url(b"GIF89a" + b"\x00" * 30),
    "wrong type": "data:image/jpeg;base64,/9j/4AAQ",
    "javascript": "javascript:alert(1)",
    "bad base64": "data:image/png;base64,@@@@",
    "gigapixel header": url(
        MAGIC + ihdr(60000, 60000) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")
    ),
    "zlib bomb": url(
        MAGIC
        + ihdr(100, 100)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 20_000_000, 9))
        + chunk(b"IEND", b"")
    ),
    "bad CRC": url(png()[:-1] + b"\x00"),
    "trailing data": url(png() + b"<script>"),
    "empty": "",
}

INIT = """
(() => {
  window.__live = [];
  new MutationObserver(() => {
    for (const n of document.querySelectorAll('.dj-signature-pad__status')) {
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
        args = {"viewport": {"width": 1000, "height": 1300}, **context_args}
        self.context = browser.new_context(**args)
        self.page = self.context.new_page()
        self.console = []
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.console.append(("pageerror", str(e))))
        self.page.add_init_script(INIT)

    def check(self, ok, what):
        if not ok:
            self.failures.append(f"[{self.label}] {what}")
        print(("ok   " if ok else "FAIL ") + f"[{self.label}] {what}")

    def load(self, path="/"):
        self.page.goto(self.base + path)
        self.page.wait_for_function(
            "() => window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted === true",
            timeout=15000,
        )
        self.page.wait_for_function(
            "() => document.querySelector('.dj-signature-pad__canvas') && document.querySelector('.dj-signature-pad__canvas').hasAttribute('dj-update')",
            timeout=10000,
        )
        self.page.wait_for_timeout(500)

    def text(self, selector):
        return (self.page.text_content(selector) or "").strip()

    def wait_for(self, expr, what, timeout=10000):
        try:
            self.page.wait_for_function(expr, timeout=timeout)
            self.check(True, what)
        except Exception:
            self.check(False, what)

    def history(self):
        return self.page.evaluate("() => window.__live")

    def canvas(self, section="a"):
        return f"#{section} .dj-signature-pad__canvas"

    def box(self, section="a"):
        return self.page.eval_on_selector(
            self.canvas(section),
            "e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; }",
        )

    def ink(self, section="a"):
        """(count of non-transparent pixels, bounding box [x0, y0, x1, y1] in canvas pixels)."""
        return self.page.evaluate(
            """(sel) => {
                const c = document.querySelector(sel);
                const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
                let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
                for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) {
                    if (d[(y * c.width + x) * 4 + 3] > 20) { n++; x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
                }
                return [n, [x0, y0, x1, y1]];
            }""",
            self.canvas(section),
        )

    def region(self, fx, fy, section="a", radius=14):
        """Ink pixels within ``radius`` canvas pixels of a point given as fractions of the canvas."""
        return self.page.evaluate(
            """([sel, fx, fy, r]) => {
                const c = document.querySelector(sel);
                const cx = Math.round(fx * c.width), cy = Math.round(fy * c.height);
                const x0 = Math.max(0, cx - r), y0 = Math.max(0, cy - r);
                const d = c.getContext('2d').getImageData(x0, y0, Math.min(c.width, cx + r) - x0, Math.min(c.height, cy + r) - y0).data;
                let n = 0;
                for (let i = 3; i < d.length; i += 4) if (d[i] > 20) n++;
                return n;
            }""",
            [self.canvas(section), fx, fy, radius],
        )

    def hook(self, section="a"):
        return f"window.djust.getHook(document.querySelector('#{section} .dj-signature-pad'))"

    def stroke(self, points, section="a", steps=6):
        """A mouse stroke through page points given as fractions of the canvas box."""
        b = self.box(section)
        pts = [(b["x"] + fx * b["w"], b["y"] + fy * b["h"]) for fx, fy in points]
        self.page.mouse.move(*pts[0])
        self.page.mouse.down()
        for p in pts[1:]:
            self.page.mouse.move(*p, steps=steps)
        self.page.mouse.up()
        return pts

    def shot(self, name):
        self.page.screenshot(path=str(self.shots / f"{self.label}-{name}.png"))

    def close(self):
        self.context.close()


def scribble(seed=7, strokes=420):
    rnd = random.Random(seed)
    out = []
    for _ in range(strokes):
        x, y = rnd.random(), rnd.random()
        pts = [(x, y)]
        for _ in range(rnd.randint(2, 5)):
            x = min(1, max(0, x + rnd.uniform(-0.3, 0.3)))
            y = min(1, max(0, y + rnd.uniform(-0.5, 0.5)))
            pts.append((x, y))
        out.append(pts)
    return out


def main() -> int:
    failures: list = []
    shots = Path(
        os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch5-signature-")
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
    # ---- the main page, device pixel ratio 2 ------------------------------------------------
    r = Run(browser, base, shots, failures, "main", device_scale_factor=2)
    page = r.page
    r.load()
    r.check(
        not [t for k, t in r.console if "No hook registered" in t],
        "no 'No hook registered' warning",
    )
    r.check(
        page.evaluate(
            "() => { const c = document.querySelector('#a .dj-signature-pad__canvas'); return [c.width, c.height, c.clientWidth, c.clientHeight]; }"
        )
        == [800, 400, 400, 200],
        "dpr 2: the backing store is twice the displayed size (800x400 for 400x200) and survived the WebSocket mount's morph",
    )
    r.check(
        page.get_attribute("#a .dj-signature-pad__canvas", "aria-label") == "Signature drawing area"
        and page.get_attribute("#a .dj-signature-pad__canvas", "role") == "img",
        "the canvas is a labelled image",
    )
    r.check(
        all(page.is_disabled(f"#a .dj-signature-pad__{b}-btn") for b in ("save", "clear", "undo")),
        "empty: Save, Clear and Undo are disabled",
    )
    # ---- draw with the mouse -------------------------------------------------------------------------
    pts = r.stroke([(0.1, 0.7), (0.3, 0.2), (0.5, 0.8), (0.7, 0.2), (0.9, 0.6)])
    n, (x0, y0, x1, y1) = r.ink()
    b = r.box()
    exp = [(p[0] - b["x"]) * 2 for p in pts], [(p[1] - b["y"]) * 2 for p in pts]
    r.check(n > 500, f"the stroke left ink ({n} pixels)")
    # The stroke is smoothed through the midpoints of its points, so its ink stays a
    # little inside the polyline's corners, and the pen has a width.
    r.check(
        all(
            abs(a - b_) <= 16
            for a, b_ in zip([x0, y0, x1, y1], [min(exp[0]), min(exp[1]), max(exp[0]), max(exp[1])])
        ),
        f"the ink is where the pointer was: bbox {[x0, y0, x1, y1]} vs path {[round(min(exp[0])), round(min(exp[1])), round(max(exp[0])), round(max(exp[1]))]}",
    )
    r.check(
        not any(
            page.is_disabled(f"#a .dj-signature-pad__{b}-btn") for b in ("save", "clear", "undo")
        ),
        "after a stroke the buttons are enabled",
    )
    r.shot("01-drawn")
    # a tap leaves a dot; undo removes the last stroke only
    cb = r.box()
    page.mouse.click(cb["x"] + 0.5 * cb["w"], cb["y"] + 0.9 * cb["h"])
    r.check(
        page.evaluate(f"() => {r.hook()}._strokes.length") == 2,
        "a click is a dot (a second stroke)",
    )
    dot = r.region(0.5, 0.9)
    r.check(dot > 10, f"the dot has ink ({dot} pixels)")
    page.click("#a .dj-signature-pad__undo-btn")
    r.check(
        page.evaluate(f"() => {r.hook()}._strokes.length") == 1 and r.region(0.5, 0.9) == 0,
        "Undo removes the last stroke and its ink",
    )
    r.check(r.ink()[0] > n * 0.7, f"and the rest is redrawn ({r.ink()[0]} vs {n})")
    # a patch in the middle of a stroke
    before = r.ink()[0]
    page.mouse.move(cb["x"] + 20, cb["y"] + 20)
    page.mouse.down()
    page.mouse.move(cb["x"] + 80, cb["y"] + 60, steps=4)
    page.evaluate("() => window.djust.handleEvent('bump', {})")
    r.wait_for(
        "() => document.querySelector('#bump-n').textContent === '1'",
        "a server patch landed in the middle of a stroke",
    )
    page.mouse.move(cb["x"] + 150, cb["y"] + 30, steps=4)
    page.mouse.up()
    n3 = r.ink()[0]
    r.check(
        n3 > before + 100 and page.evaluate(f"() => {r.hook()}._strokes.length") == 2,
        f"the stroke continued through the patch and nothing was cleared ({before} -> {n3})",
    )
    r.check(
        page.evaluate("() => document.querySelector('#a .dj-signature-pad__canvas').width") == 800,
        "the patch did not reset the canvas size",
    )

    # ---- save: the server validates and decodes ------------------------------------------------------------
    page.click("#a .dj-signature-pad__save-btn")
    r.wait_for(
        "() => document.querySelector('#n').textContent === '1'",
        "Save: the server accepted the PNG (validated by the helper)",
    )
    saved = r.text("#saved")
    r.check(
        saved.startswith("sig:800x400:") and ":89504e47:" in saved,
        f"at the pad's own size x2 for this screen: {saved!r}",
    )
    r.check(int(saved.split(":")[-1]) <= 204800, "within the 200 KB cap")
    hidden = page.input_value("#a .dj-signature-pad__value")
    r.check(
        hidden.startswith("data:image/png;base64,") and len(hidden) == int(saved.split(":")[-1]),
        "the hidden form field carries the same value",
    )
    ink_back = page.evaluate(
        """async (v) => {
            const img = new Image(); img.src = v; await img.decode();
            const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
            const x = c.getContext('2d'); x.drawImage(img, 0, 0);
            const d = x.getImageData(0, 0, c.width, c.height).data; let n = 0;
            for (let i = 3; i < d.length; i += 4) if (d[i] > 20) n++;
            return [img.width, img.height, n];
        }""",
        hidden,
    )
    r.check(
        ink_back[:2] == [800, 400] and ink_back[2] > 500,
        f"the saved PNG decodes and has the ink ({ink_back})",
    )
    r.check("Signature saved" in r.history(), f"announced: {r.history()}")
    page.click("#a .dj-signature-pad__clear-btn")
    r.check(
        r.ink()[0] == 0 and page.input_value("#a .dj-signature-pad__value") == "",
        "Clear empties the canvas and the form field",
    )
    r.check(r.history()[-1] == "Signature cleared", f"announced: {r.history()[-1]!r}")

    # ---- the empty state --------------------------------------------------------------------------------------------
    before = int(r.text("#n"))
    page.evaluate(f"() => {r.hook()}._save()")
    page.wait_for_timeout(300)
    r.check(
        int(r.text("#n")) == before and r.history()[-1].startswith("Nothing to save"),
        f"empty: nothing is sent, and it says so ({r.history()[-1]!r})",
    )

    # ---- the cap -------------------------------------------------------------------------------------------------------
    for pts in scribble(strokes=420):
        r.stroke(pts, section="c", steps=1)
    r.check(
        page.evaluate(f"() => {r.hook('c')}._strokes.length") > 400,
        "a dense scribble on the 2 KB pad",
    )
    page.click("#c .dj-signature-pad__save-btn")
    page.wait_for_timeout(600)
    r.check(
        "too detailed" in r.history()[-1],
        f"2 KB cap: refused, with the reason ({r.history()[-1]!r})",
    )
    r.check(
        page.input_value("#c .dj-signature-pad__value") == "" and "tiny:" not in r.text("#saved"),
        "2 KB cap: nothing was sent or stored",
    )
    for pts in scribble(seed=11, strokes=6):
        r.stroke(pts, section="b", steps=1)
    page.click("#b .dj-signature-pad__save-btn")
    r.wait_for(
        "() => document.querySelector('#saved').textContent.includes('small:')",
        "12 KB cap: a smaller rendering was sent and accepted",
    )
    small = [x for x in r.text("#saved").split(" | ") if x.startswith("small:")][0]
    dims = small.split(":")[1]
    r.check(
        int(dims.split("x")[0]) < 800 and int(small.split(":")[-1]) <= 12000,
        f"12 KB cap: rendered below 800 px, data URL {small.split(':')[-1]} <= 12000 ({dims})",
    )
    r.shot("02-scribble")

    # ---- the default frame limit: "Message too large" ---------------------------------------------------------------------
    r.load()
    for pts in scribble(seed=3, strokes=60):
        r.stroke(pts, section="a", steps=1)
    first = page.evaluate(
        f"""() => {{ const h = {r.hook()}; const made = h._export(h._cap(), 0); return made ? made.url.length : 0; }}"""
    )
    r.check(
        65536 < first <= 204800,
        f"premise: the best rendering ({first} chars) is over the 64 KiB frame limit and under 200 KB",
    )
    page.click("#a .dj-signature-pad__save-btn")
    r.wait_for(
        "() => document.querySelector('#n').textContent === '1'",
        "the signature arrived (retried after the server's 'Message too large')",
        timeout=15000,
    )
    arrived = r.text("#saved").split(":")
    r.check(
        int(arrived[-1]) <= 48000 and arrived[1] != "800x400",
        f"it was sent again smaller: {arrived[1]} / {arrived[-1]} chars",
    )
    r.check("Signature sent at a smaller size" in r.history(), f"announced: {r.history()[-3:]}")
    r.check(
        any("Message too large" in (e or "") for e in page.evaluate("() => window.__errors")),
        "the server really did refuse the first frame",
    )

    # ---- forged values reach the documented handler ----------------------------------------------------------------------------
    r.load()
    for name, value in FORGED.items():
        before = int(r.text("#rejected"))
        page.evaluate("(v) => window.djust.handleEvent('save_signature', {signature: v})", value)
        try:
            page.wait_for_function(
                "(b) => parseInt(document.querySelector('#rejected').textContent) > b",
                arg=before,
                timeout=6000,
            )
            r.check(True, f"forged: {name} is refused ({r.text('#why')!r})")
        except Exception:
            r.check(False, f"forged: {name} was not refused")
    r.check(r.text("#n") == "0", "forged: nothing was accepted")
    # A value over the frame limit never reaches the handler: djust refuses the frame itself.
    errors_before = len(page.evaluate("() => window.__errors"))
    page.evaluate(
        "(v) => window.djust.handleEvent('save_signature', {signature: v})",
        "data:image/png;base64," + "A" * 300_000,
    )
    page.wait_for_function(
        "(n) => window.__errors.length > n && /Message too large/.test(window.__errors[window.__errors.length - 1] || '')",
        arg=errors_before,
        timeout=6000,
    )
    r.check(True, "forged: a 300 KB value is refused by the frame limit before any handler sees it")
    r.check(r.text("#n") == "0", "forged: still nothing accepted")
    page.evaluate(
        "(v) => window.djust.handleEvent('save_signature', {signature: v})", url(png(5, 3))
    )
    r.wait_for(
        "() => document.querySelector('#n').textContent === '1'",
        "a well-formed PNG sent the same way is accepted",
    )
    r.check(r.text("#saved").startswith("sig:5x3:"), r.text("#saved"))

    # ---- keyboard only ---------------------------------------------------------------------------------------------------------
    r.load()
    page.mouse.click(900, 40)
    seen = []
    for _ in range(8):
        page.keyboard.press("Tab")
        seen.append(page.evaluate("() => document.activeElement.className"))
        if "mode-btn" in seen[-1]:
            break
    r.check(
        "dj-signature-pad__mode-btn" in seen[-1], f"keyboard: Tab reaches 'Type instead' ({seen})"
    )
    page.keyboard.press("Enter")
    r.check(
        page.evaluate(
            "() => document.activeElement.classList.contains('dj-signature-pad__typed-input')"
        ),
        "keyboard: Enter on it moves focus into the name field",
    )
    r.check(page.is_visible("#a .dj-signature-pad__typed-input"), "the field is visible")
    page.keyboard.type("Ada Lovelace")
    ink = r.ink()
    r.check(ink[0] > 300, f"typing draws the name on the canvas ({ink[0]} pixels)")
    r.shot("03-typed")
    page.keyboard.press("Enter")
    r.wait_for(
        "() => document.querySelector('#n').textContent === '1'",
        "keyboard: Enter in the field saves, with no pointer used",
    )
    r.check(r.text("#saved").startswith("sig:800x400:"), r.text("#saved"))
    page.keyboard.press("Shift+Tab")
    r.check(
        page.evaluate("() => document.activeElement.tagName") in ("INPUT", "BUTTON"),
        "focus stays on real controls",
    )
    r.check(
        "Type your name; it becomes your signature" in r.history(), f"announced: {r.history()[:3]}"
    )

    # ---- the pad without the typed alternative -------------------------------------------------------------------------------------
    r.check(
        page.locator("#d .dj-signature-pad__typed, #d .dj-signature-pad__mode-btn").count() == 0,
        "typed=False: no field and no button",
    )

    # ---- the server disables the pad (a fresh page: this one is in typed mode) --------------------------------------------------------------------------------------------------
    r.load()
    page.click("#lock")
    r.wait_for(
        "() => document.querySelector('#state').textContent === 'locked'",
        "the server locked the pad",
    )
    r.check(
        page.is_disabled("#a .dj-signature-pad__save-btn")
        and page.is_disabled("#a .dj-signature-pad__mode-btn"),
        "locked: the buttons are disabled",
    )
    r.stroke([(0.2, 0.2), (0.6, 0.6)])
    r.check(
        r.ink()[0] == 0 or page.evaluate(f"() => {r.hook()}._strokes.length") == 0,
        "locked: drawing does nothing",
    )
    page.click("#lock")
    r.wait_for(
        "() => document.querySelector('#state').textContent === 'open'", "and unlocked again"
    )
    r.stroke([(0.2, 0.2), (0.6, 0.6)])
    r.check(page.evaluate(f"() => {r.hook()}._strokes.length") == 1, "unlocked: drawing works")
    r.close()

    # ---- toggling the component: one handler, no leak -------------------------------------------------------------------------------------
    t = Run(browser, base, shots, failures, "toggle", device_scale_factor=1)
    t.load()
    for i in range(5):
        t.page.click("#toggle")
        t.wait_for(
            "() => !document.querySelector('#a .dj-signature-pad')", f"toggle {i + 1}: removed"
        )
        t.page.click("#toggle")
        t.wait_for(
            "() => document.querySelector('#a .dj-signature-pad__canvas') && document.querySelector('#a .dj-signature-pad__canvas').hasAttribute('dj-update')",
            f"toggle {i + 1}: mounted again",
        )
    t.page.wait_for_timeout(400)
    t.stroke([(0.1, 0.1), (0.8, 0.8)])
    t.check(
        t.page.evaluate(f"() => {t.hook()}._strokes.length") == 1,
        "after five toggles one gesture is one stroke",
    )
    t.page.click("#a .dj-signature-pad__save-btn")
    t.wait_for(
        "() => document.querySelector('#n').textContent === '1'", "and one Save is one event"
    )
    t.page.wait_for_timeout(500)
    t.check(t.text("#n") == "1", "still one a moment later")
    t.check(
        t.text("#saved").startswith("sig:400x200:"),
        f"dpr 1: exported at the pad's size ({t.text('#saved')[:20]})",
    )
    t.close()

    # ---- device pixel ratio 3 and 1 -------------------------------------------------------------------------------------------------------------
    for dpr, expect in ((3, 1200), (1, 400)):
        d = Run(browser, base, shots, failures, f"dpr{dpr}", device_scale_factor=dpr)
        d.load()
        d.check(
            d.page.evaluate("() => document.querySelector('#a .dj-signature-pad__canvas').width")
            == expect,
            f"dpr {dpr}: backing store {expect}",
        )
        d.stroke([(0.1, 0.5), (0.9, 0.5)])
        n, (x0, y0, x1, y1) = d.ink()
        d.check(
            abs(x0 - 0.1 * expect) < 4 * dpr and abs(x1 - 0.9 * expect) < 4 * dpr,
            f"dpr {dpr}: ink spans the stroke {[x0, x1]}",
        )
        d.page.click("#a .dj-signature-pad__save-btn")
        d.wait_for("() => document.querySelector('#n').textContent === '1'", f"dpr {dpr}: saved")
        d.check(
            d.text("#saved").startswith(f"sig:{min(dpr, 2) * 400}x{min(dpr, 2) * 200}:"),
            f"dpr {dpr}: exported at up to 2x, never more: {d.text('#saved')[:24]}",
        )
        d.close()

    # ---- a narrow container, resize, RTL ---------------------------------------------------------------------------------------------------------------
    n = Run(
        browser,
        base,
        shots,
        failures,
        "narrow",
        device_scale_factor=2,
        viewport={"width": 320, "height": 1300},
    )
    n.load("/rtl/")
    css = n.page.evaluate(
        "() => { const c = document.querySelector('#a .dj-signature-pad__canvas'); return [c.clientWidth, c.clientHeight, c.width, c.height, document.documentElement.scrollWidth <= document.documentElement.clientWidth]; }"
    )
    n.check(
        css[0] < 400
        and css[0] >= 250
        and css[2] == css[0] * 2
        and abs(css[1] - css[0] / 2) <= 1
        and css[4],
        f"320 px, RTL: the canvas follows the container, keeps its aspect ratio, no page overflow {css}",
    )
    n.stroke([(0.1, 0.5), (0.9, 0.5)])
    n.check(
        abs(n.ink()[1][0] - 0.1 * css[2]) < 8 and abs(n.ink()[1][2] - 0.9 * css[2]) < 8,
        f"RTL, scaled: the ink is where the pointer was {n.ink()[1]}",
    )
    n.page.click("#a .dj-signature-pad__save-btn")
    n.wait_for("() => document.querySelector('#n').textContent === '1'", "scaled: saved")
    n.check(
        n.text("#saved").startswith("sig:800x400:"),
        f"scaled: still exported at the pad's own size ({n.text('#saved')[:20]})",
    )
    n.shot("01-rtl-narrow")
    ink_narrow = n.ink()[0]
    n.page.set_viewport_size({"width": 700, "height": 1300})
    n.wait_for(
        "() => document.querySelector('#a .dj-signature-pad__canvas').clientWidth === 400",
        "widening the window grows the canvas to its size",
    )
    n.check(
        n.page.evaluate(f"() => {n.hook()}._strokes.length") == 1 and n.ink()[0] > 0,
        "and the drawing was kept",
    )
    n.page.set_viewport_size({"width": 320, "height": 1300})
    n.wait_for(
        "() => document.querySelector('#a .dj-signature-pad__canvas').clientWidth < 400",
        "narrowing it again shrinks the canvas",
    )
    n.check(n.ink()[0] > ink_narrow * 0.5, "and the drawing is still there")
    n.close()

    # ---- touch: the page does not scroll while drawing -------------------------------------------------------------------------------------------------------
    tc = Run(
        browser,
        base,
        shots,
        failures,
        "touch",
        device_scale_factor=2,
        has_touch=True,
        viewport={"width": 500, "height": 500},
    )
    tc.load()
    tc.page.evaluate(
        "() => document.querySelector('#a .dj-signature-pad__canvas').scrollIntoView({block: 'center'})"
    )
    tc.page.wait_for_timeout(200)
    y0 = tc.page.evaluate("() => window.scrollY")
    cdp = tc.context.new_cdp_session(tc.page)
    b = tc.box()
    pts = [
        (b["x"] + 40, b["y"] + 150),
        (b["x"] + 120, b["y"] + 40),
        (b["x"] + 200, b["y"] + 150),
        (b["x"] + 280, b["y"] + 40),
    ]
    cdp.send(
        "Input.dispatchTouchEvent",
        {"type": "touchStart", "touchPoints": [{"x": pts[0][0], "y": pts[0][1], "id": 1}]},
    )
    for p in pts[1:]:
        for k in range(1, 6):
            q = [
                pts[pts.index(p) - 1][0] + (p[0] - pts[pts.index(p) - 1][0]) * k / 5,
                pts[pts.index(p) - 1][1] + (p[1] - pts[pts.index(p) - 1][1]) * k / 5,
            ]
            cdp.send(
                "Input.dispatchTouchEvent",
                {"type": "touchMove", "touchPoints": [{"x": q[0], "y": q[1], "id": 1}]},
            )
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    tc.page.wait_for_timeout(300)
    tc.check(tc.ink()[0] > 400, f"touch: a finger drew ({tc.ink()[0]} pixels)")
    tc.check(
        tc.page.evaluate("() => window.scrollY") == y0,
        "touch: the page did not scroll while drawing",
    )
    tc.check(
        tc.page.evaluate(f"() => {tc.hook()}._strokes[0].pen") is False,
        "touch: not treated as a pen",
    )
    tc.close()

    # ---- pen pressure ------------------------------------------------------------------------------------------------------------------------------------------------
    pn = Run(browser, base, shots, failures, "pen", device_scale_factor=1)
    pn.load()
    cdp = pn.context.new_cdp_session(pn.page)
    b = pn.box()

    def pen(kind, x, y, force):
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": x,
                "y": y,
                "button": "left" if kind != "mouseMoved" else "none",
                "buttons": 0 if kind == "mouseReleased" else 1,
                "pointerType": "pen",
                "force": force,
                "clickCount": 1,
            },
        )

    pen("mousePressed", b["x"] + 30, b["y"] + 60, 0.2)
    for k in range(1, 11):
        pen("mouseMoved", b["x"] + 30 + k * 12, b["y"] + 60 + k * 2, 0.2)
    for k in range(11, 21):
        pen("mouseMoved", b["x"] + 30 + k * 12, b["y"] + 60 + k * 2, 1.0)
    pen("mouseReleased", b["x"] + 270, b["y"] + 100, 1.0)
    pn.page.wait_for_timeout(200)
    info = pn.page.evaluate(
        f"() => {{ const s = {pn.hook()}._strokes[0]; return s ? [s.pen, Math.min(...s.pts.map(p => p[2])), Math.max(...s.pts.map(p => p[2]))] : null; }}"
    )
    pn.check(
        info is not None and info[0] is True and info[1] < 0.4 and info[2] > 0.8,
        f"pen: recognised, with pressure recorded {info}",
    )
    pn.close()

    # ---- hostile label, hostile typed name -------------------------------------------------------------------------------------------------------------------------------
    x = Run(browser, base, shots, failures, "xss")
    x.load("/xss/")
    x.check(
        x.page.evaluate("() => window.__pwn") is None
        and x.page.locator(".dj-signature-pad img, .dj-signature-pad b").count() == 0,
        "hostile label: nothing ran, no element was made",
    )
    x.check(
        x.page.get_attribute(".dj-signature-pad__canvas", "aria-label")
        == "<img src=x onerror=window.__pwn=1><b>x</b>",
        "the label is the accessible name, as text",
    )
    x.page.click(".dj-signature-pad__mode-btn")
    x.page.fill(".dj-signature-pad__typed-input", "<img src=x onerror=window.__pwn=1>")
    x.page.click(".dj-signature-pad__save-btn")
    x.wait_for(
        "() => document.querySelector('#n').textContent === '1'",
        "a hostile typed name is drawn and saved as a PNG",
    )
    x.check(
        x.page.evaluate("() => window.__pwn") is None
        and x.page.locator(".dj-signature-pad img").count() == 0,
        "and it stayed text",
    )
    x.close()

    # ---- an app's own hook ----------------------------------------------------------------------------------------------------------------------------------------------------------
    c = Run(browser, base, shots, failures, "custom")
    c.page.goto(base + "/custom/")
    c.page.wait_for_function(
        "() => window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted === true"
    )
    c.page.wait_for_timeout(500)
    c.check(c.page.evaluate("() => window.__appHook") >= 1, "custom: the app's own hook ran")
    c.check(
        c.page.evaluate(
            "() => document.querySelector('.dj-signature-pad__canvas').hasAttribute('dj-update')"
        )
        is False,
        "custom: the shipped hook left the markup alone",
    )
    c.close()

    errors = [
        t
        for k, t in r.console
        if k in ("error", "pageerror")
        and not any(s in t for s in ("Failed to load resource", "Message too large"))
    ]
    r.check(not errors, f"no console errors on the main page: {errors[:3]}")


if __name__ == "__main__":
    sys.exit(main())
