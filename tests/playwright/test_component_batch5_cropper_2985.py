#!/usr/bin/env python3
"""Real-browser check of the ImageCropper hook (#2985, batch 5).

The component rendered a ``dj-hook`` that no shipped script answered. This
drives ``image-cropper.js`` as a reader would against a real LiveView over a real
WebSocket, and checks what the server actually received and cropped with
``djust.components.cropping.crop_image`` (Pillow; the server's interpreter needs
it):

* the box is in the image's NATURAL pixels (a 6000 px image shown 600 px wide,
  a 1600 px one shown smaller, a 150 px one shown larger than itself): dragging
  a handle by N screen pixels changes the box by N / scale natural pixels, and
  Crop sends only ``{x, y, width, height}`` as whole numbers;
* the server crops the right pixels (average colour of the cropped region of a
  four-quadrant image), including for a JPEG whose EXIF orientation makes the
  browser show it rotated: the helper applies the orientation first, a naive
  crop of the raw pixels gets another region;
* mouse, touch (CDP; the page does not scroll) and keyboard (arrows, Shift, Alt,
  Enter, Escape, announcements) move, resize and draw boxes; a locked 16:9 stays
  16:9; a minimum is respected; an image smaller than it is selected whole;
* resizing the window keeps the box over the same pixels; the server showing
  another image resets it; a patch in the middle of a drag does not interrupt
  it; five toggles leave one handler;
* forged boxes (NaN, strings, bools, negative sizes, absurd magnitudes, outside
  the image) and an image that claims to be gigapixel are refused by the
  documented handler; a broken image disables Crop; RTL, hostile alt text and
  an app's own hook.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives it with headless Chromium::

    pip install playwright pillow && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch5_cropper_2985.py

``CHROMIUM_EXECUTABLE`` optionally points at a Chromium binary. ``SHOTS_DIR``
(optional) is where screenshots go. Exits 0 on success, non-zero with the
failures. Not part of the CI suite (see README.md).
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
            path("hostile/", views.Hostile.as_view()),
            path("img/<str:name>", views.image),
        ]
    """,
    "cmpapp/images.py": '''
        import struct
        import zlib
        from io import BytesIO
        from pathlib import Path

        from PIL import Image

        RED, GREEN, BLUE, YELLOW = (255, 0, 0), (0, 200, 0), (0, 0, 255), (255, 255, 0)


        def quadrants(width, height):
            """Top left red, top right green, bottom left blue, bottom right yellow."""
            image = Image.new("RGB", (width, height))
            half_w, half_h = width // 2, height // 2
            for box, colour in (((0, 0, half_w, half_h), RED), ((half_w, 0, width, half_h), GREEN),
                                ((0, half_h, half_w, height), BLUE), ((half_w, half_h, width, height), YELLOW)):
                image.paste(colour, box)
            return image


        def chunk(kind, body):
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


        def build(directory: Path):
            directory.mkdir(parents=True, exist_ok=True)
            quadrants(800, 600).save(directory / "photo.png")
            quadrants(1600, 900).save(directory / "wide.png")
            quadrants(6000, 4000).save(directory / "big.png")
            quadrants(150, 100).save(directory / "tiny.png")
            quadrants(900, 600).save(directory / "other.png")
            # Raw 400x300: left half red, right half blue, tagged "rotate 90 clockwise":
            # shown upright (300x400) the red half is the TOP, the blue half the BOTTOM.
            raw = Image.new("RGB", (400, 300), BLUE)
            raw.paste(RED, (0, 0, 200, 300))
            exif = Image.Exif()
            exif[0x0112] = 6
            raw.save(directory / "rotated.jpg", exif=exif, quality=95, subsampling=0)
            # A few hundred bytes that claim to be 30000 x 30000 pixels.
            header = b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0))
            (directory / "bomb.png").write_bytes(header + chunk(b"IDAT", zlib.compress(b"\\x00" * 64)) + chunk(b"IEND", b""))
            (directory / "broken.png").write_bytes(b"not an image")
    ''',
    "cmpapp/views.py": """
        import tempfile
        from pathlib import Path

        from django.http import FileResponse, Http404
        from PIL import Image, ImageOps, ImageStat

        from djust import LiveView
        from djust.decorators import event_handler
        from djust.components.cropping import CropError, crop_image

        from . import images

        IMG_DIR = Path(tempfile.mkdtemp(prefix="cropper-images-"))
        images.build(IMG_DIR)


        def image(request, name):
            path = IMG_DIR / name
            if not path.is_file() or path.parent != IMG_DIR:
                raise Http404
            return FileResponse(open(path, "rb"), content_type="image/jpeg" if name.endswith(".jpg") else "image/png")


        def page(body, head="", html_attrs=""):
            return (
                "{% load live_tags djust_components %}<!DOCTYPE html><html" + html_attrs + "><head><title>c</title>"
                "{% djust_client_config %}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + head +
                '<script src="/static/djust_components/image-cropper.js" defer></script></head><body style="margin:0;padding:8px">'
                '<div dj-root><h1>crops</h1>'
                '<p>last: <span id="last">{{ last }}</span> n: <span id="n">{{ crops }}</span>'
                ' rejected: <span id="rejected">{{ rejected }}</span> why: <span id="why">{{ why }}</span>'
                ' bump: <span id="bump-n">{{ bumps }}</span> src: <span id="src">{{ src_a }}</span> naive: <span id="naive">{{ naive }}</span></p>'
                '<button id="bump" dj-click="bump">bump</button>'
                '<button id="toggle" dj-click="toggle">toggle</button>'
                '<button id="swap" dj-click="swap">swap</button>'
                '<button id="lock" dj-click="lock">lock</button>'
                '<button id="bomb" dj-click="use_bomb">bomb</button>'
                + body +
                '</div></body></html>'
            )


        MAIN = (
            '<section id="a" style="width:600px">{% if show %}{% image_cropper src=src_a crop_event="crop_a" disabled=locked %}{% endif %}</section>'
            '<section id="b" style="width:600px">{% image_cropper src="/img/wide.png" crop_event="crop_b" aspect_ratio="16/9" %}</section>'
            '<section id="c" style="width:600px">{% image_cropper src="/img/tiny.png" crop_event="crop_c" min_width=200 min_height=200 %}</section>'
            '<section id="d" style="width:300px">{% image_cropper src="/img/rotated.jpg" crop_event="crop_d" %}</section>'
            '<section id="e" style="width:600px">{% image_cropper src="/img/big.png" crop_event="crop_e" aspect_ratio="1:1" min_width=100 min_height=100 %}</section>'
            '<section id="f" style="width:600px">{% image_cropper src="/img/missing.png" crop_event="crop_f" %}</section>'
        )

        FILES = {"a": "photo.png", "b": "wide.png", "c": "tiny.png", "d": "rotated.jpg", "e": "big.png"}
        RATIOS = {"b": "16/9", "e": "1:1"}


        class Base(LiveView):
            def mount(self, request, **kwargs):
                self.crops = 0
                self.rejected = 0
                self.why = ""
                self.last = ""
                self.bumps = 0
                self.show = True
                self.locked = False
                self.src_a = "/img/photo.png"
                self.naive = ""
                self.files = dict(FILES)

            def _crop(self, key, x, y, width, height):
                try:
                    cropped = crop_image(IMG_DIR / self.files[key], x, y, width, height, aspect_ratio=RATIOS.get(key))
                except CropError as exc:
                    self.rejected += 1
                    self.why = str(exc)
                    return
                with Image.open(__import__("io").BytesIO(cropped.data)) as result:
                    mean = [round(v) for v in ImageStat.Stat(result.convert("RGB")).mean]
                self.crops += 1
                self.last = key + ":" + str(cropped.width) + "x" + str(cropped.height) + ":" + ",".join(str(v) for v in mean)
                if key == "d":
                    # What a crop of the raw pixels (no EXIF orientation) would have given.
                    with Image.open(IMG_DIR / "rotated.jpg") as raw:
                        naive = raw.crop((int(x), int(y), int(x) + int(width), int(y) + int(height)))
                        self.naive = ",".join(str(round(v)) for v in ImageStat.Stat(naive.convert("RGB")).mean)

            @event_handler()
            def crop_a(self, x=None, y=None, width=None, height=None, **kwargs):
                self._crop("a", x, y, width, height)

            @event_handler()
            def crop_b(self, x=None, y=None, width=None, height=None, **kwargs):
                self._crop("b", x, y, width, height)

            @event_handler()
            def crop_c(self, x=None, y=None, width=None, height=None, **kwargs):
                self._crop("c", x, y, width, height)

            @event_handler()
            def crop_d(self, x=None, y=None, width=None, height=None, **kwargs):
                self._crop("d", x, y, width, height)

            @event_handler()
            def crop_e(self, x=None, y=None, width=None, height=None, **kwargs):
                self._crop("e", x, y, width, height)

            @event_handler()
            def crop_f(self, x=None, y=None, width=None, height=None, **kwargs):
                self._crop("a", x, y, width, height)

            @event_handler()
            def bump(self, **kwargs):
                self.bumps += 1

            @event_handler()
            def toggle(self, **kwargs):
                self.show = not self.show

            @event_handler()
            def swap(self, **kwargs):
                self.src_a = "/img/other.png"
                self.files["a"] = "other.png"

            @event_handler()
            def lock(self, **kwargs):
                self.locked = not self.locked

            @event_handler()
            def use_bomb(self, **kwargs):
                self.files["a"] = "bomb.png"


        class Demo(Base):
            template = page(MAIN)

        class Rtl(Base):
            template = page(MAIN, html_attrs=' dir="rtl"')

        class Custom(Base):
            template = page(
                MAIN,
                head="<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "ImageCropper: {mounted() { window.__appHook = (window.__appHook || 0) + 1; }}};</script>",
            )

        class Hostile(Base):
            template = page(
                '<section id="a">{% image_cropper src=hostile alt=hostile crop_event="crop_a" %}</section>'
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.hostile = "/img/photo.png?x=<img src=x onerror=window.__pwn=1>"
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
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/img/photo.png", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start:\n" + "\n".join(log.read_text().splitlines()[-15:]))


INIT = """
(() => {
  window.__live = [];
  new MutationObserver(() => {
    for (const n of document.querySelectorAll('.dj-image-cropper__status')) {
      const t = n.textContent.trim();
      if (t && window.__live[window.__live.length - 1] !== t) window.__live.push(t);
    }
  }).observe(document, {subtree: true, childList: true, characterData: true});
})();
"""


class Run:
    def __init__(self, browser, base, shots, failures, label, **context_args):
        self.base, self.shots, self.failures, self.label = base, shots, failures, label
        args = {"viewport": {"width": 1000, "height": 1400}, **context_args}
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
            "() => { const s = document.querySelector('#a .dj-image-cropper__selection'); return s && s.hasAttribute('dj-update') && !s.hidden; }",
            timeout=15000,
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

    def hook(self, section="a"):
        return f"window.djust.getHook(document.querySelector('#{section} .dj-image-cropper'))"

    def box(self, section="a"):
        return self.page.evaluate(
            f"() => {{ const b = {self.hook(section)}._box; return b ? [b.x, b.y, b.w, b.h] : null; }}"
        )

    def geometry(self, section="a"):
        """(natural w, natural h, displayed w, displayed h, image left, image top) in CSS px."""
        return self.page.evaluate(
            """(sel) => { const i = document.querySelector(sel); const r = i.getBoundingClientRect();
                return [i.naturalWidth, i.naturalHeight, r.width, r.height, r.left, r.top]; }""",
            f"#{section} .dj-image-cropper__image",
        )

    def show(self, section):
        """Scroll the section into the middle of the viewport (page coordinates follow)."""
        self.page.evaluate(
            "(s) => document.querySelector('#' + s).scrollIntoView({block: 'center'})", section
        )
        self.page.wait_for_timeout(120)

    def to_page(self, nx, ny, section="a"):
        self.show(section)
        nw, _nh, dw, _dh, left, top = self.geometry(section)
        k = dw / nw
        return left + nx * k, top + ny * k

    def selection(self, section="a"):
        b = self.page.eval_on_selector(
            f"#{section} .dj-image-cropper__selection",
            "e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }",
        )
        return b

    def handle(self, name, section="a"):
        self.show(section)
        b = self.page.eval_on_selector(
            f"#{section} [data-handle='{name}']",
            "e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }",
        )
        return b

    def drag(self, start, end, steps=8):
        self.page.mouse.move(*start)
        self.page.mouse.down()
        self.page.mouse.move(*end, steps=steps)
        self.page.mouse.up()

    def crop(self, section="a"):
        self.page.click(f"#{section} .dj-image-cropper__crop-btn")

    def shot(self, name):
        self.page.screenshot(path=str(self.shots / f"{self.label}-{name}.png"))

    def close(self):
        self.context.close()


def near(a, b, tol):
    return abs(a - b) <= tol


def main() -> int:
    failures: list = []
    shots = Path(
        os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch5-cropper-")
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
    r = Run(browser, base, shots, failures, "main", device_scale_factor=2)
    page = r.page
    r.load()
    r.check(
        not [t for k, t in r.console if "No hook registered" in t],
        "no 'No hook registered' warning",
    )
    nw, nh, dw, dh, left, top = r.geometry()
    r.check(
        [nw, nh] == [800, 600] and near(dw, 600, 3),
        f"A: a 800x600 image shown {dw:.0f} px wide ({nw}x{nh})",
    )
    box = r.box()
    r.check(
        box == [80.0, 60.0, 640.0, 480.0],
        f"A: the box starts at 80% of the image, centred, in natural pixels {box}",
    )
    sel = r.selection()
    r.check(
        near(sel[0], left + 80 * dw / nw, 1.5) and near(sel[2], 640 * dw / nw, 1.5),
        f"A: and is drawn over it at the displayed scale ({sel[0]:.1f}, {sel[2]:.1f})",
    )
    r.check(
        "Crop area 80, 60, 640 by 480 pixels of 800 by 600" in r.history(),
        f"announced: {r.history()[:2]}",
    )
    r.shot("01-initial")

    # ---- move with the mouse, crop, the server crops the right pixels --------------------------------------------------
    k = dw / nw
    cx, cy = r.to_page(400, 300)
    r.drag((cx, cy), (cx - 80 * k, cy - 60 * k))  # to the top left: the box becomes x 0, y 0
    box = r.box()
    r.check(
        near(box[0], 0, 4) and near(box[1], 0, 4) and box[2] == 640,
        f"A: dragging the box moves it by the pointer's distance in natural pixels {box}",
    )
    # Shrink it into the red quadrant with the south-east handle, then crop
    sx, sy = r.handle("se")
    r.drag((sx, sy), (sx - 400 * k, sy - 280 * k))
    box = r.box()
    r.check(
        near(box[2], 240, 8) and near(box[3], 200, 8),
        f"A: the south-east handle resizes from the north-west corner {box}",
    )
    r.crop()
    r.wait_for(
        "() => document.querySelector('#n').textContent === '1'", "A: Crop reached the server"
    )
    last = r.text("#last")
    parts = last.split(":")
    w, h = (int(v) for v in parts[1].split("x"))
    r.check(
        parts[0] == "a" and near(w, box[2], 2) and near(h, box[3], 2),
        f"A: it cropped the size that was drawn ({last})",
    )
    r.check(
        parts[2] == "255,0,0",
        f"A: and the right pixels: a box inside the red quadrant is red ({parts[2]})",
    )
    r.check("Crop sent:" in r.history()[-1], f"announced: {r.history()[-1]!r}")

    # ---- draw a new box in the yellow quadrant -----------------------------------------------------------------------------
    r.drag(r.to_page(450, 350), r.to_page(700, 550))
    box = r.box()
    r.check(
        near(box[0], 450, 4) and near(box[1], 350, 4) and near(box[2], 250, 5),
        f"A: dragging on the image draws a new box {box}",
    )
    r.crop()
    r.wait_for("() => document.querySelector('#n').textContent === '2'", "A: second crop")
    r.check(
        r.text("#last").endswith(":255,255,0"), f"A: yellow quadrant -> yellow ({r.text('#last')})"
    )
    # a click without a drag changes nothing
    before = r.box()
    r.page.mouse.click(*r.to_page(100, 100))
    r.check(r.box() == before, "A: a click on the image does not move the box")

    # ---- keyboard ------------------------------------------------------------------------------------------------------------------
    r.page.click("#a .dj-image-cropper__reset-btn")
    r.check(r.box() == [80.0, 60.0, 640.0, 480.0], "A: Reset restores the starting box")
    r.page.focus("#a .dj-image-cropper__selection")
    step = max(1, round(800 / dw))
    r.page.keyboard.press("ArrowRight")
    r.check(
        r.box()[0] == 80 + step, f"keyboard: ArrowRight moves one screen pixel ({step} natural)"
    )
    r.page.keyboard.press("Shift+ArrowLeft")
    r.page.keyboard.press("Shift+ArrowUp")
    r.check(
        near(r.box()[0], 80 + step - 10 * step, 0.01) and near(r.box()[1], 60 - 10 * step, 0.01),
        f"keyboard: Shift moves ten steps {r.box()}",
    )
    r.page.keyboard.press("Alt+ArrowLeft")
    r.page.keyboard.press("Alt+ArrowUp")
    r.check(
        near(r.box()[2], 640 - step, 0.01) and near(r.box()[3], 480 - step, 0.01),
        f"keyboard: Alt with the arrows resizes {r.box()}",
    )
    r.check(
        r.history()[-1].startswith("Crop area "),
        f"keyboard: every change is announced ({r.history()[-1]!r})",
    )
    r.page.keyboard.press("Escape")
    r.check(r.box() == [80.0, 60.0, 640.0, 480.0], "keyboard: Escape resets")
    r.page.keyboard.press("Shift+ArrowLeft")
    for _ in range(40):
        r.page.keyboard.press("Shift+ArrowLeft")
    r.check(r.box()[0] == 0, "keyboard: the box stops at the image edge")
    before = int(r.text("#n"))
    r.page.keyboard.press("Enter")
    r.wait_for(
        f"() => document.querySelector('#n').textContent === '{before + 1}'",
        "keyboard: Enter crops",
    )
    r.check(r.text("#last").startswith("a:640x480:"), r.text("#last"))
    r.shot("02-keyboard")

    # ---- locked 16:9 (a 1600x900 image) -------------------------------------------------------------------------------------------------
    nwb, nhb, dwb, dhb, lb, tb = r.geometry("b")
    r.check([nwb, nhb] == [1600, 900], "B: image 1600x900")
    bb = r.box("b")
    r.check(near(bb[2] / bb[3], 16 / 9, 1e-6), f"B: the starting box is 16:9 {bb}")
    for handle, delta in (("se", (-120, -30)), ("nw", (40, 90)), ("e", (-60, 0)), ("s", (0, 50))):
        hx, hy = r.handle(handle, "b")
        r.drag((hx, hy), (hx + delta[0], hy + delta[1]))
        bx = r.box("b")
        r.check(
            near(bx[2] / bx[3], 16 / 9, 1e-6),
            f"B: still 16:9 after the {handle} handle ({bx[2]:.1f} x {bx[3]:.1f})",
        )
    r.crop("b")
    r.wait_for(
        "() => document.querySelector('#n').textContent === '4'",
        "B: the crop reached the server and passed its 16:9 check",
    )
    wb, hb = (int(v) for v in r.text("#last").split(":")[1].split("x"))
    r.check(
        abs(wb / hb - 16 / 9) / (16 / 9) < 0.02,
        f"B: whole pixels, still 16:9 within rounding ({wb}x{hb})",
    )
    r.check(r.text("#rejected") == "0", "B: the server's ratio check accepted the rounded box")

    # ---- a small image shown larger; the minimum --------------------------------------------------------------------------------------------
    nwc, nhc, dwc, dhc, lc, tc = r.geometry("c")
    bc = r.box("c")
    r.check(
        [nwc, nhc] == [150, 100] and bc == [0.0, 0.0, 150.0, 100.0],
        f"C: a 150x100 image smaller than min 200 is selected whole {bc} (shown {dwc:.0f} px wide)",
    )
    r.crop("c")
    r.wait_for("() => document.querySelector('#n').textContent === '5'", "C: crop")
    r.check(r.text("#last").startswith("c:150x100:"), r.text("#last"))

    # ---- a large image: natural pixels, not screen pixels -------------------------------------------------------------------------------------
    nwe, nhe, dwe, dhe, le, te = r.geometry("e")
    ke = dwe / nwe
    r.check(
        [nwe, nhe] == [6000, 4000] and near(ke, 0.1, 0.001),
        f"E: a 6000 px image shown at scale {ke:.3f}",
    )
    be = r.box("e")
    r.check(near(be[2] / be[3], 1.0, 1e-9), f"E: 1:1 box {be}")
    hx, hy = r.handle("se", "e")
    r.drag((hx, hy), (hx - 30, hy - 30))  # 30 screen px = 300 natural px
    be2 = r.box("e")
    r.check(
        near(be[2] - be2[2], 300, 12),
        f"E: 30 screen px are 300 natural px ({be[2]:.0f} -> {be2[2]:.0f})",
    )
    r.crop("e")
    r.wait_for("() => document.querySelector('#n').textContent === '6'", "E: crop")
    r.check(
        int(r.text("#last").split(":")[1].split("x")[0]) == round(be2[2]),
        f"E: sent {r.text('#last')}",
    )

    # ---- the EXIF-rotated JPEG ---------------------------------------------------------------------------------------------------------------------
    nwd, nhd, dwd, dhd, ld, td = r.geometry("d")
    r.check(
        [nwd, nhd] == [300, 400],
        f"D: the browser shows the rotated JPEG upright: natural {nwd}x{nhd} (the file is 400x300)",
    )
    r.page.focus("#d .dj-image-cropper__selection")
    r.drag(r.to_page(150, 200, "d"), r.to_page(150, 200, "d"), steps=1)  # focus only
    # A box over the lower part of the displayed image (the blue half: the file's right half),
    # which in raw-pixel terms lies inside the left (red) half and inside the raw image.
    r.drag(r.to_page(10, 210, "d"), r.to_page(190, 290, "d"))
    bd = r.box("d")
    r.check(
        near(bd[0], 10, 4) and near(bd[1], 210, 4) and near(bd[2], 180, 6),
        f"D: a box over the lower part of the upright image {bd}",
    )
    r.crop("d")
    r.wait_for("() => document.querySelector('#n').textContent === '7'", "D: crop")
    mean = [int(v) for v in r.text("#last").split(":")[2].split(",")]
    r.check(
        mean[2] > 200 and mean[0] < 60,
        f"D: the server cropped the BLUE bottom (EXIF applied first): {mean}",
    )
    naive = [int(v) for v in r.text("#naive").split(",")]
    r.check(
        naive[0] > 200 and naive[2] < 60,
        f"D: a crop of the raw pixels with the same box would have given RED: {naive}",
    )

    # ---- a broken image ----------------------------------------------------------------------------------------------------------------------------------
    r.wait_for(
        "() => document.querySelector('#f .dj-image-cropper__crop-btn').disabled",
        "F: a missing image disables Crop",
    )
    r.check("The image could not be loaded" in r.history(), "F: and says so")

    # ---- forged boxes ---------------------------------------------------------------------------------------------------------------------------------------
    r.load()
    forged = {
        "strings": {"x": "10", "y": "10", "width": "50", "height": "50"},
        "bools": {"x": True, "y": False, "width": True, "height": True},
        "null size": {"x": 1, "y": 1, "width": None, "height": 5},
        "negative size": {"x": 10, "y": 10, "width": -50, "height": 50},
        "zero size": {"x": 10, "y": 10, "width": 0, "height": 50},
        "absurd": {"x": 10**15, "y": 0, "width": 10**15, "height": 5},
        "outside": {"x": 5000, "y": 5000, "width": 100, "height": 100},
        "missing": {"x": 1},
    }
    for name, payload in forged.items():
        before = int(r.text("#rejected"))
        page.evaluate("(p) => window.djust.handleEvent('crop_a', p)", payload)
        try:
            page.wait_for_function(
                "(b) => parseInt(document.querySelector('#rejected').textContent) > b",
                arg=before,
                timeout=6000,
            )
            r.check(True, f"forged: {name} is refused ({r.text('#why')!r})")
        except Exception:
            r.check(False, f"forged: {name} was not refused")
    r.check(r.text("#n") == "0", "forged: nothing was cropped")
    page.evaluate(
        "() => window.djust.handleEvent('crop_a', {x: -50, y: -50, width: 100, height: 100})"
    )
    r.wait_for(
        "() => document.querySelector('#n').textContent === '1'",
        "a box that sticks out is clamped, not refused",
    )
    r.check(r.text("#last") == "a:50x50:255,0,0", r.text("#last"))
    page.evaluate(
        "() => window.djust.handleEvent('crop_a', {x: 0.4, y: 0.6, width: 10.5, height: 20.5})"
    )
    r.wait_for("() => document.querySelector('#n').textContent === '2'", "fractions are rounded")
    page.click("#bomb")
    r.wait_for("() => true", "the server now crops an image that claims 30000x30000 pixels")
    page.evaluate("() => window.djust.handleEvent('crop_a', {x: 0, y: 0, width: 10, height: 10})")
    r.wait_for(
        "() => document.querySelector('#why').textContent.includes('too large')",
        "a gigapixel image is refused before it is decoded",
    )

    # ---- the server swaps the image; a patch mid-drag; toggles ------------------------------------------------------------------------------------------------
    r.load()
    r.page.focus("#a .dj-image-cropper__selection")
    r.page.keyboard.press("Shift+ArrowRight")
    moved = r.box()
    page.click("#swap")
    r.wait_for(
        "() => document.querySelector('#src').textContent === '/img/other.png'",
        "the server swapped the image",
    )
    page.wait_for_function(
        "() => { const i = document.querySelector('#a .dj-image-cropper__image'); return i.complete && i.naturalWidth === 900; }",
        timeout=10000,
    )
    page.wait_for_timeout(300)
    r.check(
        r.box() == [90.0, 60.0, 720.0, 480.0],
        f"a new image resets the box to its own 80% ({r.box()}, was {moved})",
    )
    cx, cy = r.to_page(450, 300)
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + 20, cy, steps=3)
    page.evaluate("() => window.djust.handleEvent('bump', {})")
    r.wait_for(
        "() => document.querySelector('#bump-n').textContent === '1'",
        "a patch landed in the middle of a drag",
    )
    page.mouse.move(cx + 40, cy, steps=3)
    page.mouse.up()
    nw2, _nh2, dw2, _dh2, _l, _t = r.geometry()
    r.check(
        near(r.box()[0], 90 + 40 * nw2 / dw2, 6),
        f"the drag continued through the patch {r.box()[0]:.1f}",
    )
    for i in range(5):
        page.click("#toggle")
        r.wait_for(
            "() => !document.querySelector('#a .dj-image-cropper')", f"toggle {i + 1}: removed"
        )
        page.click("#toggle")
        r.wait_for(
            "() => { const s = document.querySelector('#a .dj-image-cropper__selection'); return s && s.hasAttribute('dj-update') && !s.hidden; }",
            f"toggle {i + 1}: mounted again",
        )
    before = int(r.text("#n"))
    r.crop()
    r.wait_for(
        f"() => document.querySelector('#n').textContent === '{before + 1}'",
        "after five toggles one click is one crop",
    )
    page.wait_for_timeout(500)
    r.check(int(r.text("#n")) == before + 1, "and still one a moment later")
    page.click("#lock")
    r.wait_for(
        "() => document.querySelector('#a .dj-image-cropper__crop-btn').disabled",
        "the server disabled the cropper: Crop is disabled",
    )
    b0 = r.box()
    cx, cy = r.to_page(450, 300)
    r.drag((cx, cy), (cx + 60, cy))
    r.check(r.box() == b0, "and dragging does nothing")
    page.click("#lock")
    r.close()

    # ---- resizing the window keeps the box over the same pixels ------------------------------------------------------------------------------------------------------
    w = Run(
        browser,
        base,
        shots,
        failures,
        "resize",
        device_scale_factor=1,
        viewport={"width": 1000, "height": 1400},
    )
    w.load()
    w.page.add_style_tag(content="#a { width: 100% !important; }")
    w.page.wait_for_timeout(300)
    before_box = w.box()
    w.page.set_viewport_size({"width": 420, "height": 1400})
    w.page.wait_for_function(
        "() => document.querySelector('#a .dj-image-cropper__image').getBoundingClientRect().width < 450",
        timeout=10000,
    )
    w.page.wait_for_timeout(300)
    after_geo = w.geometry()
    sel = w.selection()
    k2 = after_geo[2] / after_geo[0]
    w.check(
        w.box() == before_box, f"window narrowed: the box is the same in natural pixels ({w.box()})"
    )
    w.check(
        near(sel[0], after_geo[4] + before_box[0] * k2, 2) and near(sel[2], before_box[2] * k2, 2),
        f"and is drawn over the same pixels at the new scale ({sel[2]:.0f} px wide for scale {k2:.3f})",
    )
    w.close()

    # ---- touch ------------------------------------------------------------------------------------------------------------------------------------------------------------------
    t = Run(
        browser,
        base,
        shots,
        failures,
        "touch",
        device_scale_factor=2,
        has_touch=True,
        viewport={"width": 700, "height": 700},
    )
    t.load()
    t.page.evaluate(
        "() => document.querySelector('#a .dj-image-cropper__selection').scrollIntoView({block: 'center'})"
    )
    t.page.wait_for_timeout(300)
    y0 = t.page.evaluate("() => window.scrollY")
    cdp = t.context.new_cdp_session(t.page)
    sel = t.selection()
    start = (sel[0] + sel[2] / 2, sel[1] + sel[3] / 2)
    box0 = t.box()
    cdp.send(
        "Input.dispatchTouchEvent",
        {"type": "touchStart", "touchPoints": [{"x": start[0], "y": start[1], "id": 1}]},
    )
    for i in range(1, 9):
        cdp.send(
            "Input.dispatchTouchEvent",
            {
                "type": "touchMove",
                "touchPoints": [{"x": start[0] + 6 * i, "y": start[1] + 3 * i, "id": 1}],
            },
        )
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    t.page.wait_for_timeout(300)
    box1 = t.box()
    nwt, _a, dwt, _b, _c, _d = t.geometry()
    kt = nwt / dwt
    t.check(
        near(box1[0] - box0[0], 48 * kt, 6) and near(box1[1] - box0[1], 24 * kt, 6),
        f"touch: a finger moved the box by its distance ({box1[0] - box0[0]:.0f}, {box1[1] - box0[1]:.0f}) natural",
    )
    t.check(
        t.page.evaluate("() => window.scrollY") == y0,
        "touch: the page did not scroll during the drag",
    )
    t.close()

    # ---- RTL ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
    rt = Run(browser, base, shots, failures, "rtl", device_scale_factor=1)
    rt.load("/rtl/")
    cx, cy = rt.to_page(400, 300)
    nwr, _x, dwr, _y, _z, _w = rt.geometry()
    rt.drag((cx, cy), (cx + 60, cy + 30))
    rb = rt.box()
    rt.check(
        near(rb[0], 80 + 60 * nwr / dwr, 8) and near(rb[1], 60 + 30 * nwr / dwr, 8),
        f"rtl: dragging right moves the box right (east) {rb}",
    )
    sel = rt.selection()
    geo = rt.geometry()
    rt.check(
        near(sel[0], geo[4] + rb[0] * geo[2] / geo[0], 2), "rtl: and the box is drawn where it says"
    )
    rt.shot("01-rtl")
    rt.close()

    # ---- hostile src/alt, an app's own hook -------------------------------------------------------------------------------------------------------------------------------------------
    h = Run(browser, base, shots, failures, "hostile")
    h.page.goto(base + "/hostile/")
    h.page.wait_for_function(
        "() => window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted === true"
    )
    h.page.wait_for_timeout(500)
    h.check(
        h.page.evaluate("() => window.__pwn") is None
        and h.page.locator(".dj-image-cropper img").count() == 1,
        "hostile src and alt: nothing ran, no extra element",
    )
    h.check(
        h.page.get_attribute(".dj-image-cropper__image", "alt").startswith("/img/photo.png?x=<img"),
        "the alt text is text",
    )
    h.close()
    c = Run(browser, base, shots, failures, "custom")
    c.page.goto(base + "/custom/")
    c.page.wait_for_function(
        "() => window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted === true"
    )
    c.page.wait_for_timeout(500)
    c.check(c.page.evaluate("() => window.__appHook") >= 1, "custom: the app's own hook ran")
    c.check(
        c.page.evaluate(
            "() => document.querySelector('.dj-image-cropper__selection').hasAttribute('dj-update')"
        )
        is False,
        "custom: the shipped hook left the markup alone",
    )
    c.close()

    errors = [
        t
        for k, t in r.console
        if k in ("error", "pageerror") and not any(s in t for s in ("Failed to load resource",))
    ]
    r.check(not errors, f"no console errors on the main page: {errors[:3]}")


if __name__ == "__main__":
    sys.exit(main())
