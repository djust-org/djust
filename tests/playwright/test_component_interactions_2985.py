#!/usr/bin/env python3
"""Real-browser check of the SortableList, SortableGrid, JsonViewer and LogViewer hooks (#2985).

Those four components rendered a ``dj-hook`` that no shipped script answered.
Each now has a script under ``djust_components/``. This drives them, as a
reader would, against a real LiveView over a real WebSocket:

* SortableList / SortableGrid: a mouse drag and a keyboard grab-move-drop each
  send the new order to the server, the server's re-render agrees with what is on
  screen (the keyed diff moves the nodes the client already moved), a second
  reorder still works, and a server-initiated reorder lands correctly;
* JsonViewer: click and keyboard expand/collapse, and Copy puts the formatted
  JSON on the clipboard;
* LogViewer: a ``push_event`` appends coloured lines, the log follows the newest
  line until the reader scrolls up, and a server re-render is followed the same way;
* a page whose app registered its OWN hook of the same name keeps it: the app
  hook runs and the shipped one does not, while the shipped hooks of the other
  components on that page still work.

Self-contained: builds a two-page LiveView project in a temp directory, serves it
with uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_interactions_2985.py

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
    for name in ("sortable-list", "sortable-grid", "json-viewer", "log-viewer")
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
          <section id="list-box">
            {%% sortable_list items=items move_event="reorder_list" %%}
            <p>list: <span id="list-order">{{ list_csv }}</span></p>
          </section>
          <section id="grid-box">
            {%% sortable_grid items=cells columns=3 move_event="reorder_grid" %%}
            <p>grid: <span id="grid-order">{{ grid_csv }}</span></p>
          </section>
          <section id="json-box">{%% json_viewer data=doc collapsed_depth=1 %%}</section>
          <section id="log-box">
            {%% log_viewer lines=lines stream_event="new_logs" max_lines=40 %%}
          </section>
          <section id="log3-box">
            {%% log_viewer lines=lines2 stream_event="new_logs3" %%}
          </section>
          <section id="log2-box">
            {%% log_viewer lines=lines2 %%}
          </section>
          <button id="shuffle" dj-click="shuffle">shuffle</button>
          <button id="drop-one" dj-click="drop_one">drop one</button>
          <button id="stream" dj-click="stream">stream</button>
          <button id="grow" dj-click="grow">grow</button>
          <button id="burst" dj-click="stream_many">burst</button>
        </div>
        """

        def page(head):
            return (
                "{%% load live_tags djust_components %%}<!DOCTYPE html><html><head><title>c</title>"
                "{%% djust_client_config %%}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + head + "</head><body>" + BODY + "</body></html>"
            )

        class Base(LiveView):
            def mount(self, request, **kwargs):
                self.items = [{"id": k, "label": k.title()} for k in ("alpha", "beta", "gamma", "delta")]
                self.cells = [{"id": f"t{i}", "label": f"Tile {i}"} for i in range(1, 7)]
                self.doc = {"name": 'djust "viewer"', "tags": ["a", "b"], "nested": {"deep": {"k": 1}}}
                self.lines = [f"2026-10-04 INFO line {i}" for i in range(1, 40)]
                self.lines2 = [f"2026-10-04 DEBUG row {i}" for i in range(1, 40)]
                self.list_csv = ",".join(i["id"] for i in self.items)
                self.grid_csv = ",".join(c["id"] for c in self.cells)
                self.n = 0
                self.g = 0

            # `order` comes from the browser: accept it only as a permutation
            # of the ids rendered, ignore anything else.
            @event_handler()
            def reorder_list(self, order=None, **kwargs):
                by_id = {i["id"]: i for i in self.items}
                if not isinstance(order, list) or sorted(map(str, order)) != sorted(by_id):
                    return
                self.items = [by_id[str(k)] for k in order]
                self.list_csv = ",".join(i["id"] for i in self.items)

            @event_handler()
            def reorder_grid(self, order=None, **kwargs):
                by_id = {c["id"]: c for c in self.cells}
                if not isinstance(order, list) or sorted(map(str, order)) != sorted(by_id):
                    return
                self.cells = [by_id[str(k)] for k in order]
                self.grid_csv = ",".join(c["id"] for c in self.cells)

            @event_handler()
            def drop_one(self, **kwargs):
                self.items = [i for i in self.items if i["id"] != "beta"]
                self.list_csv = ",".join(i["id"] for i in self.items)

            @event_handler()
            def shuffle(self, **kwargs):
                self.items = list(reversed(self.items))
                self.list_csv = ",".join(i["id"] for i in self.items)

            @event_handler()
            def stream(self, **kwargs):
                self.n += 1
                self.push_event("new_logs", {"lines": [f"2026-10-04 ERROR streamed {self.n}"]})

            @event_handler()
            def stream_many(self, **kwargs):
                for i in range(2000):
                    self.push_event("new_logs3", {"line": f"2026-10-04 INFO burst {i}"})

            @event_handler()
            def grow(self, **kwargs):
                self.g += 1
                self.lines2 = self.lines2 + [f"2026-10-04 WARN grown {self.g}"]

        class Demo(Base):
            template = page(SCRIPTS)

        class Custom(Base):
            # The app registered its own SortableList and LogViewer hooks.
            template = page(
                "<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "SortableList: {mounted() { (window.__appHooks = window.__appHooks || []).push('list'); }},"
                "LogViewer: {mounted() { (window.__appHooks = window.__appHooks || []).push('log'); }}};"
                "window.DjustHooks = {SortableGrid: {mounted() { (window.__appHooks = window.__appHooks || []).push('grid'); }}};"
                "</script>" + SCRIPTS
            )
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
    shots = Path(os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-2985-"))
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
                context = browser.new_context(
                    permissions=["clipboard-read", "clipboard-write"],
                    viewport={"width": 900, "height": 1400},
                )
                page = context.new_page()
                console = []
                page.on("console", lambda m: console.append((m.type, m.text)))
                page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

                def check(ok, what):
                    if not ok:
                        failures.append(what)
                    print(("ok   " if ok else "FAIL ") + what)

                def drag(source, target, x, y):
                    """Press on `source`, move in steps, release over (`x`, `y`) of `target`.

                    Several intermediate moves: Chromium starts an HTML5 drag only
                    after the pointer travels with the button down, and one jump
                    sometimes starts nothing."""
                    sb = page.locator(source).bounding_box()
                    tb = page.locator(target).bounding_box()
                    sx, sy = sb["x"] + sb["width"] / 2, sb["y"] + sb["height"] / 2
                    # A click on empty page first: after the long keyboard section
                    # headless Chromium started no drag on the first press (any click
                    # elsewhere cleared it; fresh pages never showed it).
                    page.mouse.click(2, 2)
                    page.mouse.move(sx, sy)
                    page.mouse.down()
                    page.mouse.move(sx + 12, sy + 6, steps=4)
                    page.mouse.move(tb["x"] + x, tb["y"] + y, steps=12)
                    page.mouse.up()

                def load(path):
                    page.goto(base + path)
                    page.wait_for_function(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true",
                        timeout=10000,
                    )
                    page.wait_for_timeout(300)

                def ids(selector):
                    return page.eval_on_selector_all(
                        selector, "els => els.map(e => e.getAttribute('data-id'))"
                    )

                def csv(selector):
                    return page.text_content(selector).strip()

                def wait_text(selector, want, what):
                    try:
                        page.wait_for_function(
                            "([s, w]) => document.querySelector(s).textContent.trim() === w",
                            arg=[selector, want],
                            timeout=6000,
                        )
                        check(True, what)
                    except Exception:
                        check(False, f"{what}: got {csv(selector)!r}, want {want!r}")

                def live():
                    return page.evaluate(
                        "() => (document.getElementById('dj-component-live') || {}).textContent || ''"
                    )

                load("/")
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "no 'No hook registered' warning on the page with all four scripts",
                )

                # ---------------- SortableList ----------------
                li = "#list-box li.dj-sortable-list__item"
                check(
                    ids(li) == ["alpha", "beta", "gamma", "delta"], "list renders in server order"
                )
                check(
                    page.eval_on_selector_all(li, "els => els.map(e => e.tabIndex)")
                    == [0, -1, -1, -1],
                    "list has one tab stop (roving tabindex)",
                )
                box = page.locator(f"{li}[data-id=delta]").bounding_box()
                drag(
                    f"{li}[data-id=alpha]",
                    f"{li}[data-id=delta]",
                    box["width"] / 2,
                    box["height"] - 3,
                )
                wait_text(
                    "#list-order",
                    "beta,gamma,delta,alpha",
                    "mouse drag of alpha below delta reaches the server",
                )
                check(
                    ids(li) == ["beta", "gamma", "delta", "alpha"],
                    "list DOM agrees with the server after the keyed re-render",
                )
                page.locator("#list-box").screenshot(path=str(shots / "01-list-after-drag.png"))

                box = page.locator(f"{li}[data-id=beta]").bounding_box()
                drag(f"{li}[data-id=alpha]", f"{li}[data-id=beta]", box["width"] / 2, 3)
                wait_text(
                    "#list-order",
                    "alpha,beta,gamma,delta",
                    "a second drag (alpha above beta) still works",
                )
                check(
                    ids(li) == ["alpha", "beta", "gamma", "delta"],
                    "list DOM agrees after the second reorder",
                )

                page.locator(f"{li}[data-id=alpha]").focus()
                page.keyboard.press("Space")
                check("grabbed" in live(), "keyboard: Space grabs and announces")
                page.keyboard.press("ArrowDown")
                page.keyboard.press("ArrowDown")
                check(
                    ids(li) == ["beta", "gamma", "alpha", "delta"],
                    "keyboard: arrows move the grabbed item",
                )
                check(
                    csv("#list-order") == "alpha,beta,gamma,delta",
                    "keyboard: nothing sent before the drop",
                )
                page.keyboard.press("Enter")
                wait_text(
                    "#list-order",
                    "beta,gamma,alpha,delta",
                    "keyboard: Enter drops and the server gets the order",
                )
                check(
                    page.evaluate("() => document.activeElement.getAttribute('data-id')")
                    == "alpha",
                    "keyboard: focus stays on the moved item after the re-render",
                )
                page.keyboard.press("Space")
                page.keyboard.press("End")
                page.keyboard.press("Escape")
                check(
                    ids(li) == ["beta", "gamma", "alpha", "delta"],
                    "keyboard: Escape restores the order",
                )
                check(
                    csv("#list-order") == "beta,gamma,alpha,delta", "keyboard: Escape sends nothing"
                )

                page.click("#shuffle")
                wait_text("#list-order", "delta,alpha,gamma,beta", "server-initiated reorder")
                check(
                    ids(li) == ["delta", "alpha", "gamma", "beta"],
                    "server-initiated reorder lands on screen in order",
                )

                # a server patch lands while an item is grabbed
                page.locator(f"{li}[data-id=delta]").focus()
                page.keyboard.press("Space")
                page.keyboard.press("ArrowUp")
                # a programmatic click: a real one would move focus and end the grab
                page.evaluate("() => document.getElementById('drop-one').click()")
                wait_text(
                    "#list-order",
                    "delta,alpha,gamma",
                    "server removal during a grab reaches the page",
                )
                check(
                    "beta" not in ids(li) and ids(li) == ["delta", "alpha", "gamma"],
                    "patch during a grab: the removed item is gone from the page",
                )
                page.keyboard.press("Escape")
                check(
                    ids(li) == ["delta", "alpha", "gamma"]
                    and csv("#list-order") == "delta,alpha,gamma",
                    "patch during a grab: Escape does not bring the removed item back",
                )
                page.locator(f"{li}[data-id=delta]").focus()
                page.keyboard.press("Space")
                page.keyboard.press("ArrowDown")
                page.evaluate("() => document.getElementById('shuffle').click()")
                page.wait_for_function(
                    "() => document.querySelector('#list-order').textContent.trim() === 'gamma,alpha,delta'",
                    timeout=6000,
                )
                page.locator(f"{li}[data-id=gamma]").focus()
                page.keyboard.press("Escape")
                check(
                    ids(li) == ["gamma", "alpha", "delta"]
                    and csv("#list-order") == "gamma,alpha,delta",
                    "patch during a grab: Escape leaves the list as the server has it",
                )

                # ---------------- SortableGrid ----------------
                tile = "#grid-box .dj-sortable-grid__item"
                check(ids(tile) == [f"t{i}" for i in range(1, 7)], "grid renders in server order")
                box = page.locator(f"{tile}[data-id=t3]").bounding_box()
                drag(
                    f"{tile}[data-id=t1]",
                    f"{tile}[data-id=t3]",
                    box["width"] - 3,
                    box["height"] / 2,
                )
                wait_text(
                    "#grid-order",
                    "t2,t3,t1,t4,t5,t6",
                    "grid mouse drag (right half of t3) reaches the server",
                )
                check(
                    ids(tile) == ["t2", "t3", "t1", "t4", "t5", "t6"],
                    "grid DOM agrees with the server",
                )
                page.locator(f"{tile}[data-id=t2]").focus()
                page.keyboard.press("Enter")
                page.keyboard.press("ArrowDown")  # 3 columns: moves a row
                page.keyboard.press("Enter")
                wait_text(
                    "#grid-order",
                    "t3,t1,t4,t2,t5,t6",
                    "grid keyboard: ArrowDown moves a row of columns",
                )
                page.locator("#grid-box").screenshot(path=str(shots / "02-grid-after-keyboard.png"))

                # ---------------- JsonViewer ----------------
                page.evaluate(
                    """() => { const t = [...document.querySelectorAll('#json-box .dj-json__node')];
                               t.forEach((n, i) => n.id = i === 0 ? 'n-root' : 'n-' + i); }"""
                )
                collapsed = page.eval_on_selector_all(
                    "#json-box .dj-json__node--collapsed", "els => els.length"
                )
                check(collapsed >= 1, "json: deeper nodes start collapsed (collapsed_depth=1)")
                # a handle, not a locator: the locator would re-resolve to the next
                # collapsed node once this one is expanded
                toggle = page.locator(
                    "#json-box .dj-json__node--collapsed > .dj-json__toggle"
                ).first.element_handle()
                toggle.click()
                check(
                    toggle.get_attribute("aria-expanded") == "true",
                    "json: clicking a collapsed toggle expands it (aria-expanded)",
                )
                check(
                    page.eval_on_selector_all(
                        "#json-box .dj-json__node--collapsed", "els => els.length"
                    )
                    == collapsed - 1,
                    "json: only that node expanded",
                )
                toggle.focus()
                page.keyboard.press("Enter")
                check(
                    toggle.get_attribute("aria-expanded") == "false",
                    "json: Enter collapses it again",
                )
                page.keyboard.press("ArrowRight")
                check(toggle.get_attribute("aria-expanded") == "true", "json: ArrowRight expands")
                page.locator("#json-box .dj-json-viewer__copy").click()
                page.wait_for_timeout(300)
                clip = page.evaluate("() => navigator.clipboard.readText()")
                import json as _json

                try:
                    parsed = _json.loads(clip)
                except Exception:
                    parsed = None
                check(
                    parsed
                    == {"name": 'djust "viewer"', "tags": ["a", "b"], "nested": {"deep": {"k": 1}}},
                    "json: Copy puts the real (decoded) JSON on the clipboard",
                )
                check(
                    page.text_content("#json-box .dj-json-viewer__copy").strip() == "Copied",
                    "json: Copy button confirms",
                )
                page.locator("#json-box").screenshot(path=str(shots / "03-json-viewer.png"))

                # ---------------- LogViewer ----------------
                body = "#log-box .dj-log-viewer__body"
                body2 = "#log2-box .dj-log-viewer__body"
                at_bottom = "b => b.scrollTop + b.clientHeight >= b.scrollHeight - 2"
                check(
                    page.eval_on_selector(body, at_bottom), "log: opens scrolled to the newest line"
                )
                page.click("#stream")
                try:
                    page.wait_for_selector(f"{body} .dj-log-viewer__line--error", timeout=6000)
                    check(True, "log: a push_event appends an ERROR-coloured line")
                except Exception:
                    check(False, "log: a push_event appends an ERROR-coloured line")
                try:
                    page.wait_for_function(
                        "s => { const b = document.querySelector(s); return b.scrollTop + b.clientHeight >= b.scrollHeight - 2; }",
                        arg=body,
                        timeout=3000,
                    )
                except Exception:
                    pass
                check(
                    page.eval_on_selector(body, at_bottom),
                    "log: follows the streamed line (next frame)",
                )
                check(
                    page.eval_on_selector(
                        body,
                        "b => b.querySelector('.dj-log-viewer__line:last-child .dj-log-viewer__num').textContent",
                    )
                    == "40",
                    "log: streamed line numbering continues (40)",
                )
                page.focus(body)
                page.keyboard.press("Home")  # the reader: a key press, then the scroll event
                page.wait_for_timeout(
                    150
                )  # the scroll event: that is how the hook learns the reader moved
                page.click("#stream")
                page.wait_for_timeout(400)
                check(
                    page.eval_on_selector(body, "b => b.scrollTop") == 0,
                    "log: reader scrolled up -> not yanked to the bottom",
                )
                check(
                    page.eval_on_selector_all(f"{body} .dj-log-viewer__line", "els => els.length")
                    == 40,
                    "log: data-max-lines keeps the window at 40",
                )
                check(
                    page.eval_on_selector(body2, at_bottom),
                    "log2 (re-rendered): opens at the bottom",
                )
                page.click("#grow")
                page.wait_for_function(
                    "s => document.querySelector(s).textContent.includes('grown 1')",
                    arg=body2,
                    timeout=6000,
                )
                check(
                    page.eval_on_selector(body2, at_bottom), "log2: a server re-render is followed"
                )
                page.focus(body2)
                page.keyboard.press("Home")
                page.wait_for_timeout(150)  # the scroll event
                page.click("#grow")
                page.wait_for_function(
                    "s => document.querySelector(s).textContent.includes('grown 2')",
                    arg=body2,
                    timeout=6000,
                )
                check(
                    page.eval_on_selector(body2, "b => b.scrollTop") == 0,
                    "log2: reader scrolled up is left alone by a re-render",
                )
                page.locator("#log-box").screenshot(path=str(shots / "04-log-viewer-streamed.png"))
                page.locator("#log2-box").screenshot(
                    path=str(shots / "04b-log-viewer-rerendered.png")
                )

                # ---------------- LogViewer under a fast server stream ----------------
                load("/")
                body3 = "#log3-box .dj-log-viewer__body"
                gap = "b => b.scrollHeight - b.clientHeight - b.scrollTop"
                page.evaluate("() => document.getElementById('burst').click()")
                page.wait_for_function(
                    "s => document.querySelector(s).querySelectorAll('.dj-log-viewer__line').length >= 39 + 2000",
                    arg=body3,
                    timeout=20000,
                )
                page.wait_for_timeout(300)
                check(
                    page.eval_on_selector(body3, gap) <= 1,
                    f"log: 2,000 single events from the server keep following (gap {page.eval_on_selector(body3, gap):.0f}px)",
                )
                page.evaluate("() => document.getElementById('burst').click()")
                page.wait_for_function(
                    "s => document.querySelector(s).querySelectorAll('.dj-log-viewer__line').length >= 39 + 4000",
                    arg=body3,
                    timeout=20000,
                )
                page.wait_for_timeout(300)
                check(
                    page.eval_on_selector(body3, gap) <= 1,
                    "log: and a second burst follows too (pinning never lapsed)",
                )
                page.focus(body3)
                page.keyboard.press("Home")
                page.wait_for_timeout(200)
                page.evaluate("() => document.getElementById('burst').click()")
                page.wait_for_function(
                    "s => document.querySelector(s).querySelectorAll('.dj-log-viewer__line').length >= 39 + 6000",
                    arg=body3,
                    timeout=20000,
                )
                page.wait_for_timeout(300)
                check(
                    page.eval_on_selector(body3, "b => b.scrollTop") == 0,
                    "log: a reader who scrolled up stays up while 2,000 events stream",
                )
                page.eval_on_selector(body3, "b => { b.scrollTop = b.scrollHeight; }")
                page.wait_for_timeout(200)
                page.evaluate("() => document.getElementById('burst').click()")
                page.wait_for_function(
                    "s => document.querySelector(s).querySelectorAll('.dj-log-viewer__line').length >= 39 + 8000",
                    arg=body3,
                    timeout=20000,
                )
                page.wait_for_timeout(300)
                check(
                    page.eval_on_selector(body3, gap) <= 1,
                    "log: scrolling back to the bottom resumes following",
                )

                # ---------------- app hooks win ----------------
                console.clear()
                load("/custom/")
                ran = page.evaluate("() => window.__appHooks || []")
                check("list" in ran, "custom: the app's own SortableList hook ran (djust.hooks)")
                check("log" in ran, "custom: the app's own LogViewer hook ran (djust.hooks)")
                check(
                    "grid" in ran, "custom: the app's own SortableGrid hook ran (window.DjustHooks)"
                )
                check(
                    page.eval_on_selector("#list-box li", "e => e.hasAttribute('tabindex')")
                    is False
                    and page.eval_on_selector(
                        "#grid-box .dj-sortable-grid__item", "e => e.hasAttribute('tabindex')"
                    )
                    is False
                    and page.eval_on_selector(body, "b => b.hasAttribute('tabindex')") is False,
                    "custom: the shipped hooks did not touch those three elements",
                )
                page.locator("#json-box .dj-json__node--collapsed > .dj-json__toggle").first.click()
                check(
                    page.eval_on_selector_all(
                        "#json-box .dj-json__node--collapsed", "els => els.length"
                    )
                    < collapsed,
                    "custom: the shipped JsonViewer still works beside the app hooks",
                )
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "custom: no 'No hook registered' warning",
                )
                page.screenshot(path=str(shots / "05-app-hooks-win.png"))

                # ---------------- JsonViewer value colours (computed, in the browser) ----------------
                load("/")
                page.eval_on_selector(
                    "#json-box .dj-json__node--collapsed > .dj-json__toggle", "e => e.click()"
                )
                colours = page.evaluate(
                    """() => {
                        const c = (s) => getComputedStyle(document.querySelector(s)).color;
                        return {
                            string: c('#json-box .dj-json__value--string'),
                            viewer: getComputedStyle(document.querySelector('#json-box .dj-json-viewer')).backgroundColor,
                        };
                    }"""
                )

                def lum(rgb):
                    vals = [int(v) for v in rgb[rgb.index("(") + 1 : rgb.index(")")].split(",")[:3]]
                    ch = [
                        (v / 255) / 12.92
                        if v / 255 <= 0.03928
                        else (((v / 255) + 0.055) / 1.055) ** 2.4
                        for v in vals
                    ]
                    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

                ratio = (max(lum(colours["string"]), lum(colours["viewer"])) + 0.05) / (
                    min(lum(colours["string"]), lum(colours["viewer"])) + 0.05
                )
                check(
                    ratio >= 4.5,
                    f"json: a string value reads on the viewer ({colours['string']} on {colours['viewer']} = {ratio:.1f}:1)",
                )

                # ---------------- LogViewer: many single-line events stay cheap ----------------
                load("/")
                took = page.evaluate(
                    """() => {
                        const send = (p) => window.djust.dispatchPushEventToHooks('new_logs3', p);
                        const bulk = []; for (let i = 0; i < 10000; i++) bulk.push('INFO bulk ' + i);
                        send({lines: bulk});
                        const t0 = performance.now();
                        for (let i = 0; i < 10000; i++) send({line: 'INFO single ' + i});
                        return performance.now() - t0;
                    }"""
                )
                rows = page.eval_on_selector_all(
                    "#log3-box .dj-log-viewer__line", "els => els.length"
                )
                check(
                    rows == 39 + 20000, f"log: 20,000 streamed lines are all present ({rows} rows)"
                )
                check(
                    took < 8000,
                    f"log: 10,000 single-line events onto a 10,000-row viewer took {took:.0f} ms (< 8000)",
                )

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
