#!/usr/bin/env python3
"""Real-browser check of ResizablePanel ``resize_event`` and FileTree
``toggle_event`` (#2985).

Both are optional: with the kwarg the component also sends what the reader did
to the server, which can remember it and render it back; without it nothing is
sent. This drives them, as a reader would, against a real LiveView over a real
WebSocket:

* ResizablePanel: a drag sends ``{size}`` once, when it ends, and the server's
  clamped value is what the panel shows; arrow keys and Home/End send once per
  key; a panel without the kwarg sends nothing; a server re-render sends
  nothing; forged sizes are clamped or ignored by the documented handler;
* FileTree: expanding and collapsing a folder by click and by keyboard sends
  ``{path, expanded}`` once per change (a nested folder has its parents in the
  path), moving focus and selecting a file send no toggle, a server re-render
  with the stored state agrees with the page and sends nothing, a tree without
  the kwarg sends nothing, forged paths are ignored by the documented handler,
  a hostile folder name arrives as text.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_optional_events_2985.py

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

SCRIPTS = "".join(
    f'<script src="/static/djust_components/{name}.js" defer></script>'
    for name in ("resizable-panel", "file-tree")
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
        urlpatterns = [path("", views.Demo.as_view())]
    """,
    "cmpapp/views.py": (
        '''
        from djust import LiveView
        from djust.decorators import event_handler

        SCRIPTS = '%s'

        HOSTILE = "<img src=x onerror=window.__pwn=1>"

        TREE = [
            {"name": "src", "type": "folder", "children": [
                {"name": "utils", "type": "folder", "children": [{"name": "deep.py", "type": "file"}]},
                {"name": "main.py", "type": "file"},
            ]},
            {"name": "docs", "type": "folder", "children": [{"name": "guide.md", "type": "file"}]},
            {"name": HOSTILE, "type": "folder", "children": [{"name": "x.txt", "type": "file"}]},
            {"name": "README.md", "type": "file"},
        ]

        def folder_paths(nodes, prefix=()):
            out = []
            for n in nodes:
                if n.get("type") == "folder":
                    p = prefix + (n["name"],)
                    out.append(p)
                    out += folder_paths(n.get("children", []), p)
            return out

        def with_open(nodes, open_paths, prefix=()):
            out = []
            for n in nodes:
                n = dict(n)
                if n.get("type") == "folder":
                    p = prefix + (n["name"],)
                    n["expanded"] = p in open_paths
                    n["children"] = with_open(n.get("children", []), open_paths, p)
                out.append(n)
            return out

        BODY = """
        <div dj-root>
          <h1>optional events</h1>
          <p>size: <span id="rp-size">{{ size }}</span> events: <span id="rp-n">{{ rp_n }}</span>
             rejected: <span id="rp-rejected">{{ rp_rejected }}</span></p>
          <div id="rp-wrap" style="width:800px">
            <div id="rp-a">{%% resizable_panel direction="horizontal" min_size="120px" max_size="600px" initial_size=initial resize_event="panel_resized" %%}<p>with event</p>{%% endresizable_panel %%}</div>
            <div id="rp-b">{%% resizable_panel direction="horizontal" min_size="120px" max_size="600px" initial_size="300px" %%}<p>no event</p>{%% endresizable_panel %%}</div>
          </div>
          <p>open: <span id="ft-open">{{ open_text }}</span> events: <span id="ft-n">{{ ft_n }}</span>
             rejected: <span id="ft-rejected">{{ ft_rejected }}</span></p>
          <button id="redraw" dj-click="redraw">redraw</button>
          <div id="ft-a">{%% file_tree nodes=nodes selected="" event="select_file" toggle_event="folder_toggled" %%}</div>
          <div id="ft-b">{%% file_tree nodes=plain selected="" event="select_file" %%}</div>
        </div>
        """

        class Demo(LiveView):
            template = (
                "{%% load live_tags djust_components %%}<!DOCTYPE html><html><head><title>c</title>"
                "{%% djust_client_config %%}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + SCRIPTS + "</head><body>" + BODY + "</body></html>"
            )

            def mount(self, request, **kwargs):
                self.size = 300
                self.rp_n = 0
                self.rp_rejected = 0
                self.known = set(folder_paths(TREE))
                self.open_paths = {("src",)}
                self.ft_n = 0
                self.ft_rejected = 0
                self._derive()

            def _derive(self):
                self.initial = "%%dpx" %% self.size
                self.nodes = with_open(TREE, self.open_paths)
                self.plain = with_open(TREE, {("src",)})
                self.open_text = ";".join(sorted("/".join(p) for p in self.open_paths))

            @event_handler()
            def panel_resized(self, size=0, **kwargs):
                self.rp_n += 1
                try:
                    size = int(size)
                except (TypeError, ValueError):
                    self.rp_rejected += 1
                    return
                self.size = max(120, min(size, 600))
                self._derive()

            @event_handler()
            def folder_toggled(self, path=None, expanded=None, **kwargs):
                self.ft_n += 1
                if not isinstance(path, list) or tuple(path) not in self.known or not isinstance(expanded, bool):
                    self.ft_rejected += 1
                    return
                if expanded:
                    self.open_paths = self.open_paths | {tuple(path)}
                else:
                    self.open_paths = self.open_paths - {tuple(path)}
                self._derive()

            @event_handler()
            def redraw(self, **kwargs):
                self._derive()
                self.initial = "%%dpx" %% self.size

            @event_handler()
            def select_file(self, **kwargs):
                pass
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
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        server = start_server(Path(tmp), port)
        base = f"http://localhost:{port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                context = browser.new_context(viewport={"width": 1000, "height": 900})
                page = context.new_page()
                console = []
                page.on("console", lambda m: console.append((m.type, m.text)))
                page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

                def check(ok, what):
                    if not ok:
                        failures.append(what)
                    print(("ok   " if ok else "FAIL ") + what)

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

                def rect(selector):
                    return page.eval_on_selector(
                        selector,
                        "e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height}; }",
                    )

                page.goto(base + "/")
                page.wait_for_function(
                    "() => window.djust && window.djust.liveViewInstance && "
                    "window.djust.liveViewInstance.viewMounted === true",
                    timeout=15000,
                )
                page.wait_for_timeout(400)
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "no 'No hook registered' warning",
                )

                # ===================== ResizablePanel =====================
                panel_a = "#rp-a .dj-resizable-panel"
                handle_a = f"{panel_a} .dj-resizable-panel__handle"
                check(
                    page.get_attribute(panel_a, "data-resize-event") == "panel_resized"
                    and page.get_attribute("#rp-b .dj-resizable-panel", "data-resize-event")
                    is None,
                    "panel: the attribute is on the panel that asked for the event only",
                )
                r = rect(handle_a)
                page.mouse.move(r["l"] + r["w"] / 2, r["t"] + r["h"] / 2)
                page.mouse.down()
                page.mouse.move(r["l"] + 120, r["t"] + r["h"] / 2, steps=8)
                page.wait_for_timeout(300)
                check(text("#rp-n") == "0", "panel: nothing is sent while the drag is going")
                page.mouse.up()
                wait_text("#rp-n", "1", "panel: the end of a drag sends one event")
                size = round(rect(panel_a)["w"])
                check(
                    text("#rp-size") == str(size),
                    f"panel: the server has the size on screen ({text('#rp-size')} vs {size})",
                )
                check(
                    round(rect(panel_a)["w"]) == size,
                    "panel: and the server's re-render agrees with the page",
                )

                page.focus(handle_a)
                page.keyboard.press("ArrowRight")
                wait_text("#rp-n", "2", "panel: an arrow key sends one event")
                check(
                    text("#rp-size") == str(size + 10),
                    f"panel: with the size it made ({text('#rp-size')})",
                )
                page.keyboard.press("End")
                wait_text("#rp-size", "600", "panel: End sends the largest size")
                page.keyboard.press("Home")
                wait_text("#rp-size", "120", "panel: Home sends the smallest")
                n = text("#rp-n")
                page.keyboard.press("a")
                page.wait_for_timeout(300)
                check(text("#rp-n") == n, "panel: a key that is not a resize sends nothing")

                page.click("#redraw")
                page.wait_for_timeout(400)
                check(text("#rp-n") == n, "panel: a server re-render sends nothing")

                # a panel without the event sends nothing
                handle_b = "#rp-b .dj-resizable-panel .dj-resizable-panel__handle"
                r = rect(handle_b)
                page.mouse.move(r["l"] + r["w"] / 2, r["t"] + r["h"] / 2)
                page.mouse.down()
                page.mouse.move(r["l"] + 60, r["t"] + r["h"] / 2, steps=6)
                page.mouse.up()
                page.focus(handle_b)
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(400)
                check(
                    text("#rp-n") == n,
                    "panel: one without resize_event sends nothing (drag and keys)",
                )

                # forged sizes against the documented handler
                for forged in (99999, -5, "x", None):
                    page.evaluate(
                        "s => window.djust.handleEvent('panel_resized', {size: s})", forged
                    )
                page.wait_for_function(
                    "n => parseInt(document.querySelector('#rp-n').textContent) >= n + 4",
                    arg=int(n),
                    timeout=6000,
                )
                page.wait_for_timeout(300)
                check(
                    text("#rp-size") in ("120", "600") and int(text("#rp-rejected")) >= 2,
                    f"panel: forged sizes are clamped or rejected by the handler ({text('#rp-size')}, rejected {text('#rp-rejected')})",
                )

                # ===================== FileTree =====================
                tree = "#ft-a .dj-file-tree"
                row = lambda name: f'{tree} .dj-file-tree__node[data-name="{name}"]'  # noqa: E731
                check(
                    page.get_attribute(tree, "data-toggle-event") == "folder_toggled"
                    and page.get_attribute("#ft-b .dj-file-tree", "data-toggle-event") is None,
                    "tree: the attribute is on the tree that asked for the event only",
                )
                check(
                    text("#ft-open") == "src",
                    f"tree: the server starts with src open ({text('#ft-open')!r})",
                )
                page.click(row("docs"))
                wait_text(
                    "#ft-open",
                    "docs;src",
                    "tree: opening a folder sends {path, expanded: true} and the server remembers it",
                )
                check(text("#ft-n") == "1", "tree: one event for one click")
                page.click(row("src"))
                wait_text("#ft-open", "docs", "tree: closing sends expanded: false")
                page.click(row("src"))
                wait_text("#ft-open", "docs;src", "tree: and opening it again")
                page.click(row("utils"))
                wait_text(
                    "#ft-open",
                    "docs;src;src/utils",
                    "tree: a nested folder has its parents in the path",
                )
                check(
                    page.evaluate(
                        f"() => getComputedStyle(document.querySelector('{tree} .dj-file-tree__children .dj-file-tree__children')).display"
                    )
                    != "none",
                    "tree: the page and the server's re-render agree (the nested folder is open)",
                )
                n = text("#ft-n")
                page.click(row("main.py"))
                page.wait_for_timeout(300)
                check(text("#ft-n") == n, "tree: selecting a file sends no toggle")
                page.focus(row("docs"))
                page.keyboard.press("ArrowRight")  # already open: steps into it
                page.keyboard.press("ArrowDown")
                page.keyboard.press("ArrowUp")
                page.wait_for_timeout(300)
                check(text("#ft-n") == n, "tree: moving focus sends no toggle")
                page.focus(row("docs"))
                page.keyboard.press("ArrowLeft")
                wait_text("#ft-open", "src;src/utils", "tree: ArrowLeft closes and sends it")
                page.keyboard.press("ArrowRight")
                wait_text("#ft-open", "docs;src;src/utils", "tree: ArrowRight opens and sends it")
                page.keyboard.press("Enter")
                wait_text("#ft-open", "src;src/utils", "tree: Enter toggles and sends it")
                page.keyboard.press("Space")
                wait_text("#ft-open", "docs;src;src/utils", "tree: Space toggles and sends it")

                n = text("#ft-n")
                page.click("#redraw")
                page.wait_for_timeout(400)
                check(text("#ft-n") == n, "tree: a server re-render sends nothing")
                # the hostile folder name arrives as text
                page.click(row("<img src=x onerror=window.__pwn=1>"))
                wait_text(
                    "#ft-open",
                    "<img src=x onerror=window.__pwn=1>;docs;src;src/utils",
                    "tree: a hostile folder name is remembered as the text it is",
                )
                check(
                    page.evaluate(
                        "() => window.__pwn === undefined && document.querySelectorAll('#ft-open img').length === 0"
                    ),
                    "tree: and runs nothing",
                )
                # a tree without the event sends nothing
                n = text("#ft-n")
                page.click('#ft-b .dj-file-tree__node[data-name="docs"]')
                page.click('#ft-b .dj-file-tree__node[data-name="src"]')
                page.wait_for_timeout(400)
                check(text("#ft-n") == n, "tree: one without toggle_event sends nothing")
                # forged paths against the documented handler
                rejected = int(text("#ft-rejected"))
                for params in (
                    {"path": ["nope"], "expanded": True},
                    {"path": "src", "expanded": True},
                    {"path": ["src"], "expanded": "yes"},
                    {"path": ["src", "main.py"], "expanded": True},
                ):
                    page.evaluate("p => window.djust.handleEvent('folder_toggled', p)", params)
                page.wait_for_function(
                    "n => parseInt(document.querySelector('#ft-rejected').textContent) >= n + 4",
                    arg=rejected,
                    timeout=6000,
                )
                check(
                    "nope" not in text("#ft-open"),
                    f"tree: forged paths are rejected by the handler ({text('#ft-open')!r})",
                )
                check(
                    not [t for k, t in console if k == "pageerror"],
                    f"no page errors ({[t for k, t in console if k == 'pageerror']})",
                )
                browser.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()
    if failures:
        print(f"\n{len(failures)} FAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
