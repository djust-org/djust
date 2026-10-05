#!/usr/bin/env python3
"""Real-browser check of the ImageLightbox, FileTree, ResizablePanel and AnimatedNumber hooks (#2985, batch 2).

Those four components rendered a ``dj-hook`` that no shipped script answered.
Each now has a script under ``djust_components/``. This drives them, as a
reader would, against a real LiveView over a real WebSocket:

* ImageLightbox: opens with focus inside, arrows and Escape reach the server
  through the component's own controls, Tab stays inside, the new image is
  announced, focus returns to the opener, page scroll is locked while open;
* FileTree: click and keyboard expand/collapse, tree navigation, Enter on a
  file selects it on the server, the reader's open folders survive the
  server's re-render of the selection, and a 10,000-row tree stays responsive;
* ResizablePanel: mouse drag, arrow keys, clamping, double-click reset,
  disabled, horizontal and vertical;
* AnimatedNumber: counts, ends on the server's exact text, re-counts from the
  number on screen when the server changes it, reduced motion skips it;
* hostile text (names, alt, caption) stays text;
* a page whose app registered its OWN hook of the same name keeps it.

Self-contained: builds a three-page LiveView project in a temp directory, serves
it with uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch2_2985.py

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

SCRIPTS = "".join(
    f'<script src="/static/djust_components/{name}.js" defer></script>'
    for name in ("image-lightbox", "file-tree", "resizable-panel", "animated-number")
)

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
            path("custom/", views.Custom.as_view()),
            path("big/", views.Big.as_view()),
            path("rtl/", views.Rtl.as_view()),
            path("nocss/", views.NoCss.as_view()),
            path("dup/", views.Dup.as_view()),
        ]
    """,
    "cmpapp/views.py": (
        '''
        from djust import LiveView
        from djust.decorators import event_handler

        SCRIPTS = '%s'

        BODY = """
        <div dj-root>
          <h1>components</h1>
          <button id="open-lb" dj-click="open_lb">open lightbox</button>
          <p>active: <span id="lb-active">{{ active }}</span> open: <span id="lb-open">{{ lb_open }}</span></p>
          {%% lightbox images=images active=active open=lb_open %%}
          <section id="tree-box">
            {%% file_tree nodes=nodes selected=selected event="select_file" %%}
            <p>selected: <span id="sel">{{ selected }}</span></p>
          </section>
          <button id="move-sel" dj-click="move_selection">move selection</button>
          <button id="insert-folder" dj-click="insert_folder">insert folder</button>
          <button id="reverse-nodes" dj-click="reverse_nodes">reverse nodes</button>
          <button id="rename-src" dj-click="rename_src">rename src</button>
          <button id="swap-first" dj-click="swap_first">swap first two</button>
          <button id="remove-main" dj-click="remove_main">remove main.py</button>
          <section id="panels">
            <div id="p-h-box" style="width:800px">
              {%% resizable_panel direction="horizontal" min_size="100px" max_size="500px" initial_size="300px" %%}<p>horizontal</p>{%% endresizable_panel %%}
            </div>
            <div id="p-v-box" style="height:500px;width:300px">
              {%% resizable_panel direction="vertical" min_size="80px" max_size="400px" initial_size="150px" %%}<p>vertical</p>{%% endresizable_panel %%}
            </div>
            <div id="p-d-box" style="width:600px">
              {%% resizable_panel direction="horizontal" min_size="100px" initial_size="200px" disabled=True %%}<p>disabled</p>{%% endresizable_panel %%}
            </div>
          </section>
          <section id="num-box">
            <p>total: {%% animated_number value=total prefix="$" decimals=2 duration=700 %%}</p>
            <button id="bump" dj-click="bump">bump</button>
          </section>
        </div>
        """

        BIG_BODY = """
        <div dj-root>
          <h1>big tree</h1>
          {%% file_tree nodes=nodes selected=selected event="select_file" %%}
          <p>selected: <span id="sel">{{ selected }}</span></p>
        </div>
        """

        def page(head, body=BODY, css=True):
            return (
                "{%% load live_tags djust_components %%}<!DOCTYPE html><html><head><title>c</title>"
                "{%% djust_client_config %%}"
                + ('<link rel="stylesheet" href="/static/djust_components/components.css">' if css else "")
                + head + "</head><body>" + body + "</body></html>"
            )

        HOSTILE = "<img src=x onerror=window.__pwn=1>"

        class Base(LiveView):
            def mount(self, request, **kwargs):
                self.images = [
                    {"src": "data:image/gif;base64,R0lGODlhAQABAAAAACw=", "alt": "First", "caption": "The first"},
                    {"src": "data:image/gif;base64,R0lGODlhAQABAAAAACw=", "alt": "Second " + HOSTILE},
                    {"src": "data:image/gif;base64,R0lGODlhAQABAAAAACw=", "alt": "Third", "caption": "Last " + HOSTILE},
                ]
                self.active = 0
                self.lb_open = False
                self.nodes = [
                    {"name": "src", "type": "folder", "children": [
                        {"name": "main.py", "type": "file"},
                        {"name": "sub", "type": "folder", "expanded": False, "children": [
                            {"name": "deep.txt", "type": "file"}]},
                        {"name": "utils.py", "type": "file"},
                    ]},
                    {"name": "docs", "type": "folder", "children": [
                        {"name": HOSTILE, "type": "file"}, {"name": "guide.md", "type": "file"}]},
                    {"name": "README.md", "type": "file"},
                ]
                self.selected = "main.py"
                self.total = 1234.5

            @event_handler()
            def open_lb(self, **kwargs):
                self.lb_open = True

            @event_handler()
            def close_lightbox(self, **kwargs):
                self.lb_open = False

            @event_handler()
            def lightbox_navigate(self, value=0, **kwargs):
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    return
                self.active = max(0, min(value, len(self.images) - 1))

            @event_handler()
            def select_file(self, name="", **kwargs):
                self.selected = str(name)

            @event_handler()
            def move_selection(self, **kwargs):
                self.selected = "utils.py"

            @event_handler()
            def insert_folder(self, **kwargs):
                self.nodes = [
                    {"name": "aaa", "type": "folder", "children": [{"name": "a.py", "type": "file"}]}
                ] + self.nodes

            @event_handler()
            def rename_src(self, **kwargs):
                self.nodes = [dict(self.nodes[0], name="source")] + self.nodes[1:]

            @event_handler()
            def swap_first(self, **kwargs):
                self.nodes = [self.nodes[1], self.nodes[0]] + self.nodes[2:]

            @event_handler()
            def remove_main(self, **kwargs):
                src = self.nodes[0]
                self.nodes = [dict(src, children=[c for c in src["children"] if c["name"] != "main.py"])] + self.nodes[1:]

            @event_handler()
            def reverse_nodes(self, **kwargs):
                self.nodes = list(reversed(self.nodes))

            @event_handler()
            def bump(self, **kwargs):
                self.total = 98765.43

        class Demo(Base):
            template = page(SCRIPTS)

        class NoCss(Base):
            # No stylesheet at all: the hook has to do the collapsing itself.
            template = page(SCRIPTS, css=False)

        class Dup(Base):
            template = page(SCRIPTS)

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.nodes = [
                    {"name": "a", "type": "folder", "children": [
                        {"name": "lib", "type": "folder", "children": [{"name": "x.py", "type": "file"}]},
                        {"name": "lib", "type": "folder", "children": [{"name": "y.py", "type": "file"}]}]},
                    {"name": "b", "type": "folder", "children": [
                        {"name": "lib", "type": "folder", "children": [{"name": "z.py", "type": "file"}]}]},
                ]

        class Rtl(Base):
            template = page(SCRIPTS).replace("<div dj-root>", '<div dj-root dir="rtl">')

        class Custom(Base):
            # The app registered its own hooks: two in window.djust.hooks, two in window.DjustHooks.
            template = page(
                "<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "FileTree: {mounted() { (window.__appHooks = window.__appHooks || []).push('tree'); }},"
                "ResizablePanel: {mounted() { (window.__appHooks = window.__appHooks || []).push('panel'); }}};"
                "window.DjustHooks = {"
                "ImageLightbox: {mounted() { (window.__appHooks = window.__appHooks || []).push('lightbox'); }},"
                "AnimatedNumber: {mounted() { (window.__appHooks = window.__appHooks || []).push('number'); }}};"
                "</script>" + SCRIPTS
            )

        class Big(Base):
            template = page(SCRIPTS, BIG_BODY)

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.nodes = [
                    {"name": f"dir{d:03d}", "type": "folder", "children": [
                        {"name": f"file{d:03d}_{i:03d}.txt", "type": "file"} for i in range(100)]}
                    for d in range(100)
                ]
                self.selected = "file000_000.txt"
        '''
        % SCRIPTS
    ),
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
    shots = Path(os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch2-"))
    shots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        server = start_server(Path(tmp), port)
        base = f"http://localhost:{port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                context = browser.new_context(viewport={"width": 1000, "height": 1500})
                page = context.new_page()
                frames = []
                page.on(
                    "websocket",
                    lambda ws: ws.on(
                        "framesent",
                        lambda f: frames.append(str(f)) if '"type":"event"' in str(f) else None,
                    ),
                )
                console = []
                page.on("console", lambda m: console.append((m.type, m.text)))
                page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

                def check(ok, what):
                    if not ok:
                        failures.append(what)
                    print(("ok   " if ok else "FAIL ") + what)

                def load(path, ctx_page=None):
                    pg = ctx_page or page
                    pg.goto(base + path)
                    pg.wait_for_function(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true",
                        timeout=15000,
                    )
                    pg.wait_for_timeout(300)

                def text(selector):
                    return (page.text_content(selector) or "").strip()

                def wait_text(selector, want, what):
                    try:
                        page.wait_for_function(
                            "([s, w]) => document.querySelector(s).textContent.trim() === w",
                            arg=[selector, want],
                            timeout=6000,
                        )
                        check(True, what)
                    except Exception:
                        check(False, f"{what}: got {text(selector)!r}, want {want!r}")

                def active():
                    return page.evaluate(
                        "() => { const a = document.activeElement; "
                        "return a.tagName + '.' + (a.className || '') + '[' + (a.getAttribute('data-name') || '') + ']'; }"
                    )

                def live():
                    return page.evaluate(
                        "() => (document.getElementById('dj-component-live') || {}).textContent || ''"
                    )

                # ======================= ImageLightbox =======================
                load("/")
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "no 'No hook registered' warning with all four scripts",
                )
                page.focus("#open-lb")
                page.click("#open-lb")
                page.wait_for_selector(".dj-lightbox", timeout=6000)
                page.wait_for_timeout(200)
                check(
                    page.evaluate("() => document.activeElement.className") == "dj-lightbox__close",
                    "lightbox: focus moves into the dialog (the Close button)",
                )
                check(
                    page.evaluate("() => document.body.style.overflow") == "hidden",
                    "lightbox: page scroll is locked while open",
                )
                check(
                    page.get_attribute(".dj-lightbox", "aria-label") == "Image viewer",
                    "lightbox: the dialog has a name",
                )
                page.keyboard.press("ArrowRight")
                wait_text(
                    "#lb-active", "1", "lightbox: ArrowRight goes to the next image on the server"
                )
                page.wait_for_timeout(200)
                check(
                    "Second" in live() and "Image 2 of 3" in live(),
                    f"lightbox: the new image is announced ({live()!r})",
                )
                check(
                    page.evaluate("() => !!document.activeElement.closest('.dj-lightbox')"),
                    "lightbox: focus stays in the dialog after the server re-render",
                )
                page.keyboard.press("ArrowRight")
                wait_text("#lb-active", "2", "lightbox: ArrowRight again")
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(300)
                check(text("#lb-active") == "2", "lightbox: ArrowRight on the last image stays put")
                page.keyboard.press("ArrowLeft")
                wait_text("#lb-active", "1", "lightbox: ArrowLeft goes back")
                check(
                    page.evaluate(
                        "() => !document.querySelector('.dj-lightbox img').parentNode.querySelector('img[onerror]') && window.__pwn === undefined"
                    ),
                    "lightbox: hostile alt/caption text stays text",
                )
                # Tab stays inside: press Tab many times, focus must never leave the dialog
                inside = True
                for _ in range(8):
                    page.keyboard.press("Tab")
                    inside = inside and page.evaluate(
                        "() => !!document.activeElement.closest('.dj-lightbox')"
                    )
                check(inside, "lightbox: Tab cycles within the dialog")
                page.keyboard.press("Shift+Tab")
                check(
                    page.evaluate("() => !!document.activeElement.closest('.dj-lightbox')"),
                    "lightbox: Shift+Tab stays inside too",
                )
                # a swipe (synthetic touch events: the browser has no touch screen here)
                page.evaluate(
                    """() => {
                        const root = document.querySelector('.dj-lightbox');
                        const mk = (type, x, y) => {
                            const t = new Touch({identifier: 1, target: root, clientX: x, clientY: y});
                            root.dispatchEvent(new TouchEvent(type, {bubbles: true, cancelable: true,
                                touches: type === 'touchend' ? [] : [t], changedTouches: [t]}));
                        };
                        mk('touchstart', 400, 300); mk('touchend', 250, 305);
                    }"""
                )
                wait_text("#lb-active", "2", "lightbox: a swipe left goes to the next image")
                page.screenshot(path=str(shots / "01-lightbox.png"))
                mark = len(frames)
                page.keyboard.press("Escape")
                wait_text("#lb-open", "False", "lightbox: Escape closes it on the server")
                page.wait_for_timeout(300)
                closes = [f for f in frames[mark:] if '"event":"close_lightbox"' in f]
                check(
                    len(closes) == 1,
                    f"lightbox: Escape sends the close event exactly once ({len(closes)})",
                )
                page.wait_for_timeout(200)
                check(
                    page.evaluate("() => document.activeElement.id") == "open-lb",
                    "lightbox: focus returns to the button that opened it",
                )
                check(
                    page.evaluate("() => document.body.style.overflow") != "hidden",
                    "lightbox: page scroll is unlocked again",
                )
                # reopen: still one listener per key (no double navigation)
                page.click("#open-lb")
                page.wait_for_selector(".dj-lightbox", timeout=6000)
                page.keyboard.press("ArrowLeft")
                page.wait_for_timeout(500)
                check(
                    text("#lb-active") == "1",
                    "lightbox: after reopening, one key press moves one image",
                )
                page.keyboard.press("Escape")
                wait_text("#lb-open", "False", "lightbox: closes again")

                # ======================= FileTree =======================
                tree = ".dj-file-tree"

                def row(name):
                    return f'{tree} .dj-file-tree__node[data-name="{name}"]'

                def hidden_children(name):
                    return page.evaluate(
                        "s => getComputedStyle(document.querySelector(s).nextElementSibling).display === 'none'",
                        row(name),
                    )

                check(
                    page.evaluate(
                        "() => document.querySelectorAll('.dj-file-tree [tabindex=\"0\"]').length"
                    )
                    == 1,
                    "tree: exactly one tab stop",
                )
                check(
                    page.get_attribute(row("main.py"), "tabindex") == "0",
                    "tree: the tab stop is the selected row",
                )
                check(
                    hidden_children("sub"), "tree: a folder the server collapsed starts collapsed"
                )
                page.click(f"{row('sub')} .dj-file-tree__name")
                check(not hidden_children("sub"), "tree: clicking a folder row expands it")
                check(
                    page.get_attribute(row("sub"), "aria-expanded") == "true",
                    "tree: aria-expanded follows",
                )
                page.click(f"{row('sub')} .dj-file-tree__toggle")
                check(hidden_children("sub"), "tree: clicking the arrow collapses it")
                page.focus(row("main.py"))
                page.keyboard.press("ArrowDown")
                check(active().endswith("[sub]"), "tree: ArrowDown moves to the next visible row")
                page.keyboard.press("ArrowRight")
                check(not hidden_children("sub"), "tree: ArrowRight expands a collapsed folder")
                page.keyboard.press("ArrowRight")
                check(active().endswith("[deep.txt]"), "tree: ArrowRight again steps into it")
                page.keyboard.press("ArrowLeft")
                check(active().endswith("[sub]"), "tree: ArrowLeft steps out to the parent")
                page.keyboard.press("ArrowLeft")
                check(hidden_children("sub"), "tree: ArrowLeft collapses an open folder")
                page.keyboard.press("End")
                check(active().endswith("[README.md]"), "tree: End goes to the last visible row")
                page.keyboard.press("Home")
                check(active().endswith("[src]"), "tree: Home goes to the first row")
                page.focus(row("utils.py"))
                page.keyboard.press("Enter")
                wait_text(
                    "#sel",
                    "utils.py",
                    "tree: Enter on a file fires the row's own event (server selects it)",
                )
                # The reader opens a folder; a server-side selection change must not close it
                page.click(f"{row('sub')} .dj-file-tree__name")
                check(not hidden_children("sub"), "tree: the reader opened sub")
                page.evaluate("() => document.getElementById('move-sel').click()")
                page.wait_for_timeout(500)
                page.keyboard.press("Escape")
                check(
                    not hidden_children("sub"),
                    "tree: a server re-render of the selection leaves the reader's open folder open",
                )
                check(
                    page.get_attribute(row("utils.py"), "aria-selected") == "true"
                    and page.get_attribute(row("main.py"), "aria-selected") == "false",
                    "tree: aria-selected follows the server's selection",
                )
                page.focus(row("src"))
                page.keyboard.press("d")
                check(
                    active().endswith("[deep.txt]"),
                    f"tree: typing a letter jumps to the next row starting with it ({active()})",
                )
                page.wait_for_timeout(800)
                page.keyboard.press("d")
                check(
                    active().endswith("[docs]"),
                    f"tree: the same letter again moves on ({active()})",
                )
                check(
                    page.evaluate(
                        "() => window.__pwn === undefined && !document.querySelector('.dj-file-tree img')"
                    ),
                    "tree: a hostile file name stays text",
                )
                page.screenshot(
                    path=str(shots / "02-tree.png"),
                    clip={"x": 0, "y": 0, "width": 1000, "height": 700},
                )

                # ---- the server re-renders and rows shift (positional diff) ----
                load("/")
                page.click(f"{row('src')} .dj-file-tree__toggle")
                check(hidden_children("src"), "tree re-render: the reader collapses src")
                page.evaluate("() => document.getElementById('insert-folder').click()")
                page.wait_for_selector(row("aaa"), timeout=6000)
                page.wait_for_timeout(300)
                check(
                    not hidden_children("aaa")
                    and hidden_children("src")
                    and not hidden_children("docs"),
                    "tree re-render: a folder inserted above does not move the reader's collapse (aaa open, src still closed, docs open)",
                )
                check(
                    page.get_attribute(row("src"), "aria-expanded") == "false"
                    and page.get_attribute(row("docs"), "aria-expanded") == "true",
                    "tree re-render: aria-expanded follows the right folders",
                )
                page.focus(row("README.md"))
                page.evaluate("() => document.getElementById('reverse-nodes').click()")
                page.wait_for_timeout(600)
                check(
                    active().endswith("[README.md]"),
                    f"tree re-render: keyboard focus stays on README.md when the server reverses the rows ({active()})",
                )
                check(
                    page.evaluate(
                        "() => document.querySelectorAll('.dj-file-tree__node[tabindex=\"0\"]').length"
                    )
                    == 1,
                    "tree re-render: still exactly one tab stop",
                )
                check(
                    hidden_children("src"),
                    "tree re-render: and src is still collapsed after the reverse",
                )

                # ---- more server re-renders: rename, swap, remove the focused row / its parent ----
                load("/")
                page.click(f"{row('src')} .dj-file-tree__toggle")
                page.evaluate("() => document.getElementById('rename-src').click()")
                page.wait_for_selector(row("source"), timeout=6000)
                page.wait_for_timeout(300)
                check(
                    not hidden_children("source")
                    and page.get_attribute(row("source"), "aria-expanded") == "true",
                    "tree re-render: a folder renamed in place takes the server's state (open), not the old slot's collapse",
                )
                load("/")
                page.click(f"{row('src')} .dj-file-tree__toggle")
                page.evaluate("() => document.getElementById('swap-first').click()")
                page.wait_for_timeout(600)
                names = page.eval_on_selector_all(
                    ".dj-file-tree > .dj-file-tree__node--folder",
                    "els => els.map(e => e.getAttribute('data-name'))",
                )
                check(
                    names[:2] == ["docs", "src"]
                    and hidden_children("src")
                    and not hidden_children("docs"),
                    f"tree re-render: two folders swapped in place keep their own state ({names[:2]}, src closed, docs open)",
                )
                load("/")
                page.focus(row("main.py"))
                page.evaluate("() => document.getElementById('remove-main').click()")
                page.wait_for_timeout(600)
                check(
                    page.evaluate(
                        "() => !!document.activeElement.closest('.dj-file-tree') && document.activeElement.classList.contains('dj-file-tree__node')"
                    ),
                    f"tree re-render: removing the focused row leaves focus on a row of the tree ({active()})",
                )
                check(
                    page.evaluate(
                        "() => document.querySelectorAll('.dj-file-tree__node[tabindex=\"0\"]').length"
                    )
                    == 1,
                    "tree re-render: and one tab stop",
                )

                # ---- the same name under different parents and twice under one parent ----
                load("/dup/")
                libs = ".dj-file-tree__node[data-name=lib]"
                hidden_libs = (
                    "() => [...document.querySelectorAll('.dj-file-tree__node[data-name=lib]')]"
                    ".map(l => getComputedStyle(l.nextElementSibling).display === 'none')"
                )
                page.locator(libs).nth(1).locator(".dj-file-tree__toggle").click()
                check(
                    page.evaluate(hidden_libs) == [False, True, False],
                    "tree duplicates: collapsing the second lib under a leaves the first and b/lib open",
                )
                page.evaluate("() => document.getElementById('insert-folder').click()")
                page.wait_for_selector(row("aaa"), timeout=6000)
                page.wait_for_timeout(300)
                check(
                    page.evaluate(hidden_libs) == [False, True, False],
                    "tree duplicates: and each keeps its own state after a folder is inserted above",
                )

                # ---- no stylesheet at all: the hook collapses and expands by itself ----
                load("/nocss/")
                check(
                    page.evaluate(
                        "() => ![...document.querySelectorAll('link[rel=stylesheet]')].some((l) => l.href.includes('components.css'))"
                    ),
                    "no stylesheet: components.css is not loaded on this page",
                )
                page.click(f"{row('docs')} .dj-file-tree__toggle")
                check(
                    hidden_children("docs"),
                    "no stylesheet: collapsing an open folder hides its children",
                )
                page.click(f"{row('sub')} .dj-file-tree__name")
                check(
                    not hidden_children("sub"),
                    "no stylesheet: opening a server-collapsed folder shows its children",
                )
                check(
                    page.get_attribute(row("sub"), "aria-expanded") == "true"
                    and page.evaluate(
                        "s => document.querySelector(s).nextElementSibling.getBoundingClientRect().height > 0",
                        row("sub"),
                    ),
                    "no stylesheet: aria and layout agree",
                )
                page.evaluate("() => document.getElementById('insert-folder').click()")
                page.wait_for_selector(row("aaa"), timeout=6000)
                page.wait_for_timeout(300)
                check(
                    hidden_children("docs")
                    and not hidden_children("sub")
                    and not hidden_children("aaa"),
                    "no stylesheet: and a re-render that inserts a folder above leaves each state in place",
                )

                # ======================= ResizablePanel =======================
                load("/")

                def width(sel):
                    return page.evaluate(
                        "s => document.querySelector(s).getBoundingClientRect().width", sel
                    )

                def height(sel):
                    return page.evaluate(
                        "s => document.querySelector(s).getBoundingClientRect().height", sel
                    )

                ph = "#p-h-box .dj-resizable-panel"
                hh = f"{ph} .dj-resizable-panel__handle"
                check(
                    abs(width(ph) - 300) < 1, f"panel: starts at initial_size ({width(ph):.0f}px)"
                )
                check(
                    page.get_attribute(hh, "aria-valuenow") == "300"
                    and page.get_attribute(hh, "aria-valuemin") == "100"
                    and page.get_attribute(hh, "aria-valuemax") == "500"
                    and page.get_attribute(hh, "aria-orientation") == "vertical",
                    "panel: the handle is a splitter with its values",
                )
                box = page.locator(hh).bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx + 40, cy, steps=5)
                page.mouse.move(cx + 80, cy, steps=5)
                page.mouse.up()
                check(
                    abs(width(ph) - 380) < 2,
                    f"panel: dragging the handle right by 80px resizes it ({width(ph):.0f}px)",
                )
                box = page.locator(hh).bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx + 600, cy, steps=10)
                page.mouse.up()
                check(abs(width(ph) - 500) < 2, f"panel: clamped to max_size ({width(ph):.0f}px)")
                box = page.locator(hh).bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx - 900, cy, steps=10)
                page.mouse.up()
                check(abs(width(ph) - 100) < 2, f"panel: clamped to min_size ({width(ph):.0f}px)")
                page.focus(hh)
                page.keyboard.press("ArrowRight")
                page.keyboard.press("Shift+ArrowRight")
                check(
                    abs(width(ph) - 160) < 2,
                    f"panel: arrows resize by 10px, Shift by 50px ({width(ph):.0f}px)",
                )
                check(
                    page.get_attribute(hh, "aria-valuenow") == "160", "panel: aria-valuenow follows"
                )
                page.keyboard.press("End")
                check(abs(width(ph) - 500) < 2, "panel: End goes to the largest size")
                page.keyboard.press("Home")
                check(abs(width(ph) - 100) < 2, "panel: Home goes to the smallest size")
                page.dblclick(hh)
                check(
                    abs(width(ph) - 300) < 2,
                    f"panel: double-click resets to the initial size ({width(ph):.0f}px)",
                )
                pv = "#p-v-box .dj-resizable-panel"
                hv = f"{pv} .dj-resizable-panel__handle"
                check(
                    page.get_attribute(hv, "aria-orientation") == "horizontal",
                    "panel: a vertical panel's separator is horizontal",
                )
                page.focus(hv)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("ArrowRight")  # not this panel's axis
                check(
                    abs(height(pv) - 160) < 2,
                    f"panel: a vertical panel answers the vertical arrows ({height(pv):.0f}px)",
                )
                box = page.locator(hv).bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx, cy + 60, steps=6)
                page.mouse.up()
                check(
                    abs(height(pv) - 220) < 2,
                    f"panel: dragging a vertical panel resizes its height ({height(pv):.0f}px)",
                )
                pd = "#p-d-box .dj-resizable-panel"
                hd = f"{pd} .dj-resizable-panel__handle"
                check(
                    page.get_attribute(hd, "tabindex") is None
                    and page.get_attribute(hd, "aria-disabled") == "true",
                    "panel: a disabled panel's handle takes no focus",
                )
                box = page.locator(hd).bounding_box()
                page.mouse.move(box["x"] + 2, box["y"] + 10)
                page.mouse.down()
                page.mouse.move(box["x"] + 80, box["y"] + 10, steps=5)
                page.mouse.up()
                check(abs(width(pd) - 200) < 2, "panel: and cannot be dragged")
                page.evaluate(
                    "() => document.getElementById('bump').click()"
                )  # an unrelated server patch
                page.wait_for_timeout(500)
                check(
                    abs(width(ph) - 300) < 2 and abs(height(pv) - 220) < 2,
                    "panel: an unrelated patch leaves the reader's sizes",
                )
                page.screenshot(
                    path=str(shots / "03-panels.png"),
                    clip={"x": 0, "y": 600, "width": 1000, "height": 800},
                )

                # ---- right to left: the handle is on the panel's left edge ----
                load("/rtl/")
                prt = "#p-h-box .dj-resizable-panel"
                hrt = f"{prt} .dj-resizable-panel__handle"
                check(
                    page.evaluate(
                        "([p, h]) => document.querySelector(h).getBoundingClientRect().left < "
                        "document.querySelector(p).getBoundingClientRect().left + 20",
                        [prt, hrt],
                    ),
                    "rtl panel: the handle is on the panel's left edge",
                )
                w0 = width(prt)
                box = page.locator(hrt).bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx - 30, cy, steps=5)
                page.mouse.move(cx - 60, cy, steps=5)
                page.mouse.up()
                check(
                    abs(width(prt) - (w0 + 60)) < 2,
                    f"rtl panel: dragging the handle outward (left) grows it ({w0:.0f} -> {width(prt):.0f}px)",
                )
                box = page.locator(hrt).bounding_box()
                cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(cx, cy)
                page.mouse.down()
                page.mouse.move(cx + 50, cy, steps=6)
                page.mouse.up()
                check(
                    abs(width(prt) - (w0 + 10)) < 2,
                    f"rtl panel: dragging inward (right) shrinks it ({width(prt):.0f}px)",
                )
                page.focus(hrt)
                before = width(prt)
                page.keyboard.press("ArrowLeft")
                check(abs(width(prt) - (before + 10)) < 2, "rtl panel: ArrowLeft grows it")
                page.keyboard.press("ArrowRight")
                page.keyboard.press("ArrowRight")
                check(abs(width(prt) - (before - 10)) < 2, "rtl panel: ArrowRight shrinks it")

                # ======================= AnimatedNumber =======================
                load("/")
                value = "#num-box .dj-animated-number__value"
                samples = []
                for _ in range(12):
                    samples.append(text(value))
                    page.wait_for_timeout(70)
                check(
                    "1,234.50" == samples[-1],
                    f"number: ends on the server's exact text ({samples[-1]!r})",
                )
                check(
                    any(s not in ("1,234.50",) for s in samples[:6]),
                    f"number: counts through intermediate values ({samples[:6]})",
                )
                page.wait_for_timeout(300)
                page.evaluate("() => document.getElementById('bump').click()")
                mids = []
                for _ in range(14):
                    mids.append(text(value))
                    page.wait_for_timeout(60)
                check(
                    mids[-1] == "98,765.43",
                    f"number: a server change counts to the new exact text ({mids[-1]!r})",
                )
                moving = [m for m in mids if m not in ("1,234.50", "98,765.43")]
                check(
                    bool(moving)
                    and all(1234.5 <= float(m.replace(",", "")) <= 98765.43 for m in moving),
                    f"number: re-counts from the number on screen to the new one ({moving[:4]})",
                )
                check(
                    page.text_content("#num-box .dj-animated-number__prefix") == "$",
                    "number: the prefix is untouched",
                )
                reduced = browser.new_context(
                    reduced_motion="reduce", viewport={"width": 1000, "height": 1500}
                )
                rpage = reduced.new_page()
                load("/", rpage)
                rpage.wait_for_timeout(100)
                check(
                    (rpage.text_content(value) or "").strip() == "1,234.50",
                    "number: prefers-reduced-motion shows the final value at once",
                )
                reduced.close()
                page.screenshot(
                    path=str(shots / "04-number.png"),
                    clip={"x": 0, "y": 1300, "width": 1000, "height": 200},
                )

                # ======================= large tree =======================
                load("/big/")
                rows = page.evaluate(
                    "() => document.querySelectorAll('.dj-file-tree__node').length"
                )
                check(rows == 100 + 10000, f"big tree: {rows} rows rendered")
                stops = page.evaluate(
                    "() => document.querySelectorAll('.dj-file-tree__node[tabindex=\"0\"]').length"
                )
                check(stops == 1, f"big tree: exactly one tab stop across 10,100 rows ({stops})")
                timings = page.evaluate(
                    """() => {
                        const tree = document.querySelector('.dj-file-tree');
                        const hook = window.djust.getHook(tree);
                        const tIdle = performance.now();
                        hook._sync();
                        const idle = performance.now() - tIdle;
                        // worst case: a morph reset every row to the server's markup
                        tree.querySelectorAll('.dj-file-tree__node').forEach((n) => {
                            n.setAttribute('tabindex', '0');
                            n.removeAttribute('aria-level');
                            n.removeAttribute('aria-selected');
                        });
                        const t0 = performance.now();
                        hook._sync();
                        const sync = performance.now() - t0;
                        const first = tree.querySelector('.dj-file-tree__node');
                        first.focus();
                        const key = (k) => document.activeElement.dispatchEvent(
                            new KeyboardEvent('keydown', {key: k, bubbles: true, cancelable: true}));
                        const t1 = performance.now();
                        for (let i = 0; i < 20; i++) key('ArrowDown');
                        const nav = (performance.now() - t1) / 20;
                        const t2 = performance.now();
                        key('End');
                        const end = performance.now() - t2;
                        const folder = tree.querySelectorAll('.dj-file-tree__node--folder')[50];
                        const t3 = performance.now();
                        folder.click();
                        const expand = performance.now() - t3;
                        return {idle, sync, nav, end, expand, focused: document.activeElement.getAttribute('data-name')};
                    }"""
                )
                check(
                    timings["sync"] < 400,
                    f"big tree: re-applying every attribute on 10,100 rows takes {timings['sync']:.0f} ms (< 400)",
                )
                check(
                    timings["idle"] < 100,
                    f"big tree: a sync with nothing to change takes {timings['idle']:.0f} ms (< 100)",
                )
                check(
                    timings["nav"] < 40,
                    f"big tree: ArrowDown takes {timings['nav']:.1f} ms per press (< 40)",
                )
                check(timings["end"] < 120, f"big tree: End takes {timings['end']:.0f} ms (< 120)")
                check(
                    timings["expand"] < 120,
                    f"big tree: expanding a 100-row folder takes {timings['expand']:.0f} ms (< 120)",
                )
                page.locator(".dj-file-tree__node[data-name='file000_001.txt']").focus()
                page.keyboard.press("Enter")
                wait_text(
                    "#sel",
                    "file000_001.txt",
                    "big tree: Enter on a file in a 10,000-row tree selects it",
                )

                # ======================= app hooks win =======================
                console.clear()
                load("/custom/")
                ran = page.evaluate("() => window.__appHooks || []")
                check(
                    sorted(set(ran)) == ["number", "panel", "tree"],
                    f"custom: the app's own FileTree, ResizablePanel (djust.hooks) and AnimatedNumber (DjustHooks) hooks ran ({ran})",
                )
                page.click("#open-lb")
                page.wait_for_selector(".dj-lightbox", timeout=6000)
                page.wait_for_timeout(300)
                ran = page.evaluate("() => window.__appHooks || []")
                check(
                    "lightbox" in ran, "custom: the app's own ImageLightbox hook (DjustHooks) ran"
                )
                check(
                    page.evaluate("() => document.body.style.overflow") != "hidden"
                    and page.get_attribute(".dj-lightbox", "aria-label") is None,
                    "custom: the shipped lightbox hook did not touch the dialog",
                )
                check(
                    page.eval_on_selector(
                        ".dj-file-tree .dj-file-tree__node", "e => e.hasAttribute('aria-level')"
                    )
                    is False
                    and page.eval_on_selector(
                        ".dj-resizable-panel__handle", "e => e.hasAttribute('aria-valuenow')"
                    )
                    is False
                    and page.eval_on_selector(".dj-animated-number__value", "e => e.textContent")
                    == "1,234.50",
                    "custom: the shipped hooks left those elements alone (no aria, no counting)",
                )
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "custom: no 'No hook registered' warning",
                )
                page.screenshot(path=str(shots / "05-app-hooks-win.png"))

                errors = [t for k, t in console if k in ("error", "pageerror")]
                check(not errors, f"no console errors or page errors: {errors[:3]}")
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


if __name__ == "__main__":
    sys.exit(main())
