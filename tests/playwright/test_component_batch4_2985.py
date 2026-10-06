#!/usr/bin/env python3
"""Real-browser check of the DashboardGrid, MentionsInput, CursorsOverlay and
CollabSelection hooks (#2985, batch 4).

Those four components rendered a ``dj-hook`` that no shipped script answered.
Each now has a script under ``djust_components/``. This drives them, as a
reader would, against a real LiveView over a real WebSocket:

* DashboardGrid: dragging a header moves a panel (the server receives
  ``{id, col, row}``, validates it and re-renders, and the panel lands in that
  cell), dragging the bottom handle resizes it (``{id, width, height}``), a
  dashed cell previews the drop, Escape cancels, the keyboard grabs, moves and
  resizes, a server patch that shifts the panels mid-drag is followed by id,
  an event the server rejects leaves the dashboard as it was, text in a panel
  can be selected, forged payloads are clamped or ignored by the example
  handler;
* MentionsInput: typing @ opens a filtered list (a real combobox: roles,
  expanded state, active descendant), the keyboard and the mouse choose, a
  chosen mention goes to the server as ``{text, mentions: [ids]}`` with the
  plain input event's fields kept, Enter that chooses does not also submit,
  edited-away mentions are dropped, hostile names stay text;
* CursorsOverlay: the arrows are decoration, joins and leaves are announced,
  labels stay inside the overlay and off each other (also after a resize),
  motion is eased and switched off under prefers-reduced-motion;
* CollabSelection: ranges of a target's text are highlighted with the CSS
  Custom Highlight API without touching the page's nodes, names sit above the
  start of each range and follow scrolling, server updates and edits of the
  text re-anchor, a hostile colour never reaches the style rule, teardown
  leaves nothing behind;
* a page whose app registered its OWN hooks of the same name keeps them.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch4_2985.py

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
    for name in ("dashboard-grid", "mentions-input", "cursors-overlay", "collab-selection")
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

        HOSTILE = "<img src=x onerror=window.__pwn=1>"
        DOC = "Hello brave new world. " * 60 + "The end."

        BODY = """
        <div dj-root>
          <h1>components</h1>

          <section id="dg-box">
            <p>log: <span id="dg-log">{{ dg_log }}</span> events: <span id="dg-n">{{ dg_n }}</span>
               rejected: <span id="dg-rejected">{{ dg_rejected }}</span></p>
            <button id="dg-drop-first" dj-click="dg_drop_first">drop first</button>
            <button id="dg-restore" dj-click="dg_restore">restore</button>
            <button id="dg-ignore" dj-click="dg_ignore">ignore events</button>
            {%% dashboard_grid panels=panels columns=4 row_height="120px" gap="10px" move_event="dashboard_move" resize_event="dashboard_resize" %%}{%% enddashboard_grid %%}
          </section>

          <section id="mn-box" style="margin-top:2rem">
            <p>sent: <span id="mn-n">{{ mn_n }}</span> text: <span id="mn-text">{{ mn_text }}</span>
               ids: <span id="mn-ids">{{ mn_ids }}</span> raw: <span id="mn-raw">{{ mn_raw }}</span>
               extra: <span id="mn-extra">{{ mn_extra }}</span></p>
            {%% mentions_input name="message" users=mention_users event="send_message" %%}
          </section>

          <section id="cu-box" style="margin-top:2rem">
            <button id="cu-move" dj-click="cu_move">move</button>
            <button id="cu-add" dj-click="cu_add">add</button>
            <button id="cu-remove" dj-click="cu_remove">remove</button>
            <button id="cu-pile" dj-click="cu_pile">pile</button>
            <div id="cu-stage" style="position:relative;width:420px;height:220px;border:1px solid #888">
              {%% cursors users=cursors %%}
            </div>
          </section>

          <section id="cs-box" style="margin-top:2rem">
            <button id="cs-move" dj-click="cs_move">move</button>
            <button id="cs-edit" dj-click="cs_edit">edit</button>
            <button id="cs-hostile" dj-click="cs_hostile">hostile colour</button>
            <button id="cs-hide" dj-click="cs_hide">hide</button>
            <div id="cs-scroll" style="height:90px;width:520px;overflow:auto;border:1px solid #888;padding:24px 8px 8px">
              <p id="doc" style="margin:0">{{ doc }}</p>
            </div>
            <div id="cs-xform" style="transform: translate(40px, 25px)">
              {%% if cs_show %%}{%% collab_selection users=selections target="#doc" %%}{%% endif %%}
            </div>
          </section>
          <div style="height:600px"></div>
        </div>
        """

        def page(head):
            return (
                "{%% load live_tags djust_components %%}<!DOCTYPE html><html><head><title>c</title>"
                "{%% djust_client_config %%}"
                '<link rel="stylesheet" href="/static/djust_components/components.css">'
                + head + "</head><body>" + BODY + "</body></html>"
            )

        PANELS = [
            {"id": "rev", "title": "Revenue", "col": 1, "row": 1, "width": 2, "height": 1,
             "content": "Quarterly revenue text that can be selected"},
            {"id": "usr", "title": "Users " + HOSTILE, "col": 3, "row": 1, "width": 1, "height": 1,
             "content": "1234"},
            {"id": "err", "title": "Errors", "col": 1, "row": 2, "width": 1, "height": 1, "content": "0"},
        ]

        class Base(LiveView):
            def mount(self, request, **kwargs):
                self.columns = 4
                self.panels = [dict(p) for p in PANELS]
                self.dg_log = ""
                self.dg_n = 0
                self.dg_rejected = 0
                self.dg_ignore_events = False
                self.mn_n = 0
                self.mn_text = ""
                self.mn_ids = ""
                self.mn_raw = ""
                self.mn_extra = ""
                self.mention_users = [
                    {"id": "1", "name": "Alice Cooper"},
                    {"id": "2", "name": "Bob"},
                    {"id": "3", "name": "Cora Lee"},
                    {"id": "4", "name": "Albert"},
                    {"id": "5", "name": HOSTILE},
                ]
                self.cursors = [
                    {"name": "Alice", "color": "#3b82f6", "x": 40, "y": 40},
                    {"name": "Bob", "color": "#ef4444", "x": 200, "y": 120},
                ]
                self.doc = DOC
                self.cs_show = True
                self.selections = [
                    {"name": "Alice", "color": "#3b82f6", "start": 0, "end": 5, "text": "Hello"},
                    {"name": "Bob", "color": "#ef4444", "start": 6, "end": 11, "text": "brave"},
                ]

            # ---- DashboardGrid: the handlers the component documents ----
            def _panel(self, pid):
                for p in self.panels:
                    if p["id"] == pid:
                        return p
                return None

            @event_handler()
            def dashboard_move(self, id="", col=1, row=1, **kwargs):
                self.dg_n += 1
                panel = self._panel(id)
                if panel is None or self.dg_ignore_events:
                    self.dg_rejected += 1
                    return
                try:
                    col, row = int(col), int(row)
                except (TypeError, ValueError):
                    self.dg_rejected += 1
                    return
                panel["col"] = max(1, min(col, self.columns - panel["width"] + 1))
                panel["row"] = max(1, min(row, 50))
                self.dg_log = "move %%s %%s,%%s" %% (id, panel["col"], panel["row"])

            @event_handler()
            def dashboard_resize(self, id="", width=1, height=1, **kwargs):
                self.dg_n += 1
                panel = self._panel(id)
                if panel is None or self.dg_ignore_events:
                    self.dg_rejected += 1
                    return
                try:
                    width, height = int(width), int(height)
                except (TypeError, ValueError):
                    self.dg_rejected += 1
                    return
                panel["width"] = max(1, min(width, self.columns - panel["col"] + 1))
                panel["height"] = max(1, min(height, 50))
                self.dg_log = "resize %%s %%sx%%s" %% (id, panel["width"], panel["height"])

            @event_handler()
            def dg_drop_first(self, **kwargs):
                self.panels = self.panels[1:]

            @event_handler()
            def dg_restore(self, **kwargs):
                self.panels = [dict(p) for p in PANELS]
                self.dg_ignore_events = False

            @event_handler()
            def dg_ignore(self, **kwargs):
                self.dg_ignore_events = True

            # ---- MentionsInput ----
            @event_handler()
            def send_message(self, text="", mentions=None, **kwargs):
                allowed = {u["id"] for u in self.mention_users}
                raw = mentions if isinstance(mentions, list) else []
                self.mn_n += 1
                self.mn_text = text
                self.mn_raw = ",".join(str(m) for m in raw)
                self.mn_ids = ",".join(m for m in raw if isinstance(m, str) and m in allowed)
                self.mn_extra = ",".join(sorted(k for k in kwargs if not k.startswith("_")))

            # ---- CursorsOverlay ----
            @event_handler()
            def cu_move(self, **kwargs):
                self.cursors = [dict(c, x=c["x"] + 30, y=c["y"] + 20) for c in self.cursors]

            @event_handler()
            def cu_add(self, **kwargs):
                self.cursors = self.cursors + [{"name": "Carol", "color": "#22c55e", "x": 400, "y": 205}]

            @event_handler()
            def cu_remove(self, **kwargs):
                self.cursors = [c for c in self.cursors if c["name"] != "Bob"]

            @event_handler()
            def cu_pile(self, **kwargs):
                self.cursors = self.cursors + [
                    {"name": "Dave", "color": "#f59e0b", "x": 40, "y": 40},
                    {"name": "Erin", "color": "#8b5cf6", "x": 40, "y": 40},
                ]

            # ---- CollabSelection ----
            @event_handler()
            def cs_move(self, **kwargs):
                self.selections = [
                    {"name": "Alice", "color": "#3b82f6", "start": 12, "end": 15, "text": "new"},
                    {"name": "Carol", "color": "#22c55e", "start": 99999, "end": 100000, "text": "end"},
                ]

            @event_handler()
            def cs_edit(self, **kwargs):
                self.doc = "Howdy" + DOC[5:]

            @event_handler()
            def cs_hostile(self, **kwargs):
                self.selections = [
                    {"name": "Mallory", "color": "red; } body { display:none } .x {", "start": 0, "end": 5, "text": "Hello"},
                    {"name": "Trudy", "color": "url(https://evil.example/x)", "start": 6, "end": 11, "text": "brave"},
                ]

            @event_handler()
            def cs_hide(self, **kwargs):
                self.cs_show = not self.cs_show

        class Demo(Base):
            template = page(SCRIPTS)

        class Custom(Base):
            # The app registered its own hooks: two in window.djust.hooks, two in window.DjustHooks.
            template = page(
                "<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "DashboardGrid: {mounted() { (window.__appHooks = window.__appHooks || []).push('grid'); }},"
                "MentionsInput: {mounted() { (window.__appHooks = window.__appHooks || []).push('mentions'); }}};"
                "window.DjustHooks = {"
                "CursorsOverlay: {mounted() { (window.__appHooks = window.__appHooks || []).push('cursors'); }},"
                "CollabSelection: {mounted() { (window.__appHooks = window.__appHooks || []).push('collab'); }}};"
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


GRID_GEOMETRY = """
() => {
    const g = document.querySelector('#dg-box .dj-dashboard-grid');
    const cs = getComputedStyle(g);
    const r = g.getBoundingClientRect();
    const sx = r.width / g.offsetWidth, sy = r.height / g.offsetHeight;
    const cols = cs.gridTemplateColumns.split(' ').map((v) => parseFloat(v) * sx);
    const rows = cs.gridTemplateRows.split(' ').map((v) => parseFloat(v) * sy);
    const gx = (parseFloat(cs.columnGap) || 0) * sx, gy = (parseFloat(cs.rowGap) || 0) * sy;
    const rtl = cs.direction === 'rtl';
    let x = rtl ? r.right : r.left, y = r.top;
    const xs = [], ys = [];
    cols.forEach((w) => { xs.push(rtl ? [x, x - w] : [x, x + w]); x += (rtl ? -1 : 1) * (w + gx); });
    rows.forEach((h) => { ys.push([y, y + h]); y += h + gy; });
    return {xs, ys, rtl};
}
"""


def main() -> int:
    failures = []
    shots = Path(os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch4-"))
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
                context = browser.new_context(viewport={"width": 1000, "height": 800})
                page = context.new_page()
                console = []
                page.on("console", lambda m: console.append((m.type, m.text)))
                page.on("pageerror", lambda e: console.append(("pageerror", str(e))))

                def check(ok, what):
                    if not ok:
                        failures.append(what)
                    print(("ok   " if ok else "FAIL ") + what)

                def load(path):
                    page.goto(base + path)
                    page.wait_for_function(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true",
                        timeout=15000,
                    )
                    page.wait_for_timeout(400)

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

                def live():
                    return page.evaluate(
                        "() => (document.getElementById('dj-component-live') || {}).textContent || ''"
                    )

                def rect(selector):
                    return page.eval_on_selector(
                        selector,
                        "e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height}; }",
                    )

                # ======================= DashboardGrid =======================
                load("/")
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "no 'No hook registered' warning with all four scripts",
                )
                grid = "#dg-box .dj-dashboard-grid"
                panel = lambda pid: f'{grid} [data-panel-id="{pid}"]'  # noqa: E731
                header = lambda pid: f"{panel(pid)} .dj-dashboard-grid__panel-header"  # noqa: E731
                handle = lambda pid: f"{panel(pid)} .dj-dashboard-grid__panel-resize"  # noqa: E731
                page.eval_on_selector(grid, "g => g.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(200)

                check(
                    page.eval_on_selector_all(
                        f"{grid} > .dj-dashboard-grid__panel",
                        "els => els.map(e => e.draggable)",
                    )
                    == [False, False, False],
                    "grid: native drag is off on every panel",
                )
                check(
                    page.eval_on_selector_all(
                        f"{grid} > .dj-dashboard-grid__panel", "els => els.map(e => e.tabIndex)"
                    )
                    == [0, -1, -1],
                    "grid: one tab stop",
                )

                def cell_center(col, row):
                    g = page.evaluate(GRID_GEOMETRY)
                    x = (g["xs"][col - 1][0] + g["xs"][col - 1][1]) / 2
                    y = (g["ys"][row - 1][0] + g["ys"][row - 1][1]) / 2
                    return x, y

                def panel_cell(pid):
                    """Which cell the panel's inline-start/top corner is in (from layout)."""
                    g = page.evaluate(GRID_GEOMETRY)
                    r = rect(panel(pid))
                    edge = r["r"] if g["rtl"] else r["l"]
                    col = min(range(len(g["xs"])), key=lambda i: abs(g["xs"][i][0] - edge)) + 1
                    row = min(range(len(g["ys"])), key=lambda i: abs(g["ys"][i][0] - r["t"])) + 1
                    return col, row

                def span_of(pid):
                    r = rect(panel(pid))
                    g = page.evaluate(GRID_GEOMETRY)
                    cw = abs(g["xs"][0][1] - g["xs"][0][0])
                    ch = g["ys"][0][1] - g["ys"][0][0]
                    gap = abs(g["xs"][1][0] - g["xs"][0][0]) - cw
                    w = round((r["w"] + gap) / (cw + gap))
                    h = round((r["h"] + gap) / (ch + gap))
                    return w, h

                def drag_header(pid, to_col, to_row, release=True):
                    r = rect(header(pid))
                    x0, y0 = r["l"] + 20, r["t"] + r["h"] / 2
                    cx, cy = cell_center(to_col, to_row)
                    start = panel_cell(pid)
                    gx, gy = cell_center(*start)
                    # the panel's top-left follows the pointer: move by the cell difference
                    page.mouse.move(x0, y0)
                    page.mouse.down()
                    page.mouse.move((x0 + cx - gx) / 1.0, (y0 + cy - gy) / 1.0, steps=10)
                    if release:
                        page.mouse.up()

                # a panel moves by dragging its header
                drag_header("usr", 2, 2)
                wait_text(
                    "#dg-log",
                    "move usr 2,2",
                    "grid: dragging a header sends {id, col, row} and the server moves the panel",
                )
                check(
                    panel_cell("usr") == (2, 2),
                    f"grid: the panel is in the cell the server put it in ({panel_cell('usr')})",
                )
                check(text("#dg-n") == "1", f"grid: exactly one move event ({text('#dg-n')})")

                # the preview while dragging
                r = rect(header("err"))
                page.mouse.move(r["l"] + 20, r["t"] + r["h"] / 2)
                page.mouse.down()
                cx, cy = cell_center(3, 1)
                gx, gy = cell_center(1, 2)
                page.mouse.move(r["l"] + 20 + cx - gx, r["t"] + r["h"] / 2 + cy - gy, steps=10)
                preview = page.evaluate(
                    f"""() => {{
                        const g = document.querySelector('{grid}');
                        const a = getComputedStyle(g, '::after');
                        return {{drop: g.getAttribute('data-dj-drop'), content: a.content,
                                 col: a.gridColumnStart, row: a.gridRowStart,
                                 dragging: document.querySelectorAll('.dj-dashboard-grid__panel--dragging').length,
                                 children: g.children.length}};
                    }}"""
                )
                check(
                    preview["drop"] == "move"
                    and preview["content"] not in ("none", "normal")
                    and preview["dragging"] == 1,
                    f"grid: a dashed cell previews the drop and the panel is marked ({preview})",
                )
                check(
                    preview["children"] == 3,
                    f"grid: nothing is added to the DOM while dragging ({preview['children']} children)",
                )
                page.screenshot(path=str(shots / "01-grid-preview.png"))
                page.keyboard.press("Escape")
                page.mouse.up()
                page.wait_for_timeout(300)
                check(text("#dg-n") == "1", "grid: Escape cancels the drag (no event)")
                check(
                    page.evaluate(
                        f"() => !document.querySelector('{grid}').hasAttribute('data-dj-drop')"
                    ),
                    "grid: and the preview is gone",
                )
                check(panel_cell("err") == (1, 2), "grid: the panel stayed put")

                # a press without movement and a drop where it started do nothing
                r = rect(header("err"))
                page.mouse.click(r["l"] + 20, r["t"] + r["h"] / 2)
                drag_header("err", 1, 2)
                page.wait_for_timeout(300)
                check(
                    text("#dg-n") == "1", "grid: a click, or a drop in the same cell, sends nothing"
                )

                # resize
                r = rect(handle("rev"))
                hx, hy = r["l"] + r["w"] - 6, r["t"] + r["h"] / 2
                page.mouse.move(hx, hy)
                page.mouse.down()
                cx, cy = cell_center(4, 1)
                g = page.evaluate(GRID_GEOMETRY)
                page.mouse.move(g["xs"][3][1] - 4, hy, steps=10)
                page.mouse.up()
                wait_text(
                    "#dg-log",
                    "resize rev 4x1",
                    "grid: dragging the bottom handle sends {id, width, height} and the server resizes",
                )
                check(span_of("rev") == (4, 1), f"grid: the panel is 4 wide now ({span_of('rev')})")
                # taller, down into a new row
                r = rect(handle("rev"))
                page.mouse.move(r["l"] + 40, r["t"] + r["h"] / 2)
                page.mouse.down()
                page.mouse.move(r["l"] + 40, r["t"] + 400, steps=10)
                page.mouse.up()
                page.wait_for_timeout(500)
                check(
                    text("#dg-log").startswith("resize rev 4x"),
                    f"grid: a resize down is requested too ({text('#dg-log')})",
                )

                # keyboard
                page.focus(panel("err"))
                page.keyboard.press("Space")
                check(
                    "Errors grabbed" in live(),
                    f"grid: Space grabs the panel and says so ({live()!r})",
                )
                page.keyboard.press("ArrowRight")
                page.keyboard.press("ArrowRight")
                page.keyboard.press("Enter")
                page.wait_for_function(
                    "() => document.querySelector('#dg-log').textContent.startsWith('move err')",
                    timeout=6000,
                )
                check(
                    text("#dg-log").startswith("move err 3,"),
                    f"grid: arrow keys move, Enter drops ({text('#dg-log')})",
                )
                check(
                    page.evaluate("() => document.activeElement.getAttribute('data-panel-id')")
                    == "err",
                    "grid: focus is on the panel after the drop",
                )
                page.keyboard.press("Space")
                page.keyboard.press("Shift+ArrowRight")
                page.keyboard.press("Shift+ArrowDown")
                page.keyboard.press("Space")
                page.wait_for_function(
                    "() => document.querySelector('#dg-log').textContent.startsWith('resize err')",
                    timeout=6000,
                )
                check(span_of("err") == (2, 2), f"grid: Shift+arrows resize ({span_of('err')})")
                page.keyboard.press("Space")
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Escape")
                n_before = text("#dg-n")
                page.wait_for_timeout(300)
                check(text("#dg-n") == n_before, "grid: Escape cancels a keyboard grab")

                # right-to-left: column 1 is on the right, a drag to the visual left goes to a higher column
                page.click("#dg-restore")
                page.wait_for_timeout(300)
                page.evaluate(f"() => {{ document.querySelector('{grid}').dir = 'rtl'; }}")
                page.wait_for_timeout(200)
                check(
                    panel_cell("usr") == (3, 1),
                    f"grid (rtl): column 3 is the third from the right ({panel_cell('usr')})",
                )
                drag_header("usr", 4, 1)
                wait_text(
                    "#dg-log",
                    "move usr 4,1",
                    "grid (rtl): dragging to the visual left sends the higher column",
                )
                check(
                    panel_cell("usr") == (4, 1),
                    f"grid (rtl): and the panel lands there ({panel_cell('usr')})",
                )
                page.focus(panel("usr"))
                page.keyboard.press("Enter")
                page.keyboard.press("ArrowRight")
                page.keyboard.press("Enter")
                wait_text(
                    "#dg-log",
                    "move usr 3,1",
                    "grid (rtl): ArrowRight moves visually right, to the lower column",
                )
                r = rect(handle("rev"))
                page.mouse.move(r["l"] + 6, r["t"] + r["h"] / 2)
                page.mouse.down()
                g = page.evaluate(GRID_GEOMETRY)
                page.mouse.move(g["xs"][3][1] + 4, r["t"] + r["h"] / 2, steps=10)
                page.mouse.up()
                wait_text(
                    "#dg-log",
                    "resize rev 4x1",
                    "grid (rtl): the handle widens the panel toward the left",
                )
                page.evaluate(f"() => {{ document.querySelector('{grid}').dir = ''; }}")
                page.click("#dg-restore")
                page.wait_for_timeout(300)

                # a grid drawn at 70%: measured as drawn
                page.evaluate(
                    f"() => {{ const g = document.querySelector('{grid}'); g.style.transform = 'scale(0.7)'; g.style.transformOrigin = 'top left'; }}"
                )
                page.wait_for_timeout(200)
                drag_header("err", 3, 2)
                wait_text(
                    "#dg-log", "move err 3,2", "grid (scaled): a drag is measured in drawn pixels"
                )
                page.evaluate(f"() => {{ document.querySelector('{grid}').style.transform = ''; }}")
                page.click("#dg-restore")
                page.wait_for_timeout(300)

                # touch: the same drag with a finger, on a touch-enabled page (pointer events from
                # real touches; touch-action keeps the page from scrolling instead)
                tctx = browser.new_context(has_touch=True, viewport={"width": 1000, "height": 800})
                tp = tctx.new_page()
                tp.goto(base + "/")
                tp.wait_for_function(
                    "() => window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted === true",
                    timeout=15000,
                )
                tp.wait_for_timeout(400)
                tp.eval_on_selector(grid, "g => g.scrollIntoView({block: 'start'})")
                tp.wait_for_timeout(200)
                scroll0 = tp.evaluate("() => window.scrollY")
                tr = tp.eval_on_selector(
                    f"{header('usr')}",
                    "e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }",
                )
                tg = tp.evaluate(GRID_GEOMETRY)
                x0, y0 = tr[0] + 20, tr[1] + tr[3] / 2
                dx = (tg["xs"][1][0] + tg["xs"][1][1]) / 2 - (tg["xs"][2][0] + tg["xs"][2][1]) / 2
                dy = (tg["ys"][1][0] + tg["ys"][1][1]) / 2 - (tg["ys"][0][0] + tg["ys"][0][1]) / 2
                tcdp = tctx.new_cdp_session(tp)
                tcdp.send(
                    "Input.dispatchTouchEvent",
                    {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]},
                )
                for i in range(1, 9):
                    tcdp.send(
                        "Input.dispatchTouchEvent",
                        {
                            "type": "touchMove",
                            "touchPoints": [{"x": x0 + dx * i / 8, "y": y0 + dy * i / 8}],
                        },
                    )
                    tp.wait_for_timeout(16)
                tcdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
                try:
                    tp.wait_for_function(
                        "() => document.querySelector('#dg-log').textContent.trim() === 'move usr 2,2'",
                        timeout=6000,
                    )
                    check(True, "grid (touch): dragging a header with a finger moves the panel")
                except Exception:
                    check(False, "grid (touch): dragging a header with a finger moves the panel")
                check(
                    abs(tp.evaluate("() => window.scrollY") - scroll0) < 2,
                    "grid (touch): and the page did not scroll instead",
                )
                tctx.close()

                # a server patch that shifts the panels while a drag is in progress
                page.click("#dg-restore")
                page.wait_for_timeout(400)
                r = rect(header("usr"))
                page.mouse.move(r["l"] + 20, r["t"] + r["h"] / 2)
                page.mouse.down()
                cx, cy = cell_center(2, 2)
                gx, gy = cell_center(*panel_cell("usr"))
                page.mouse.move(r["l"] + 20 + cx - gx, r["t"] + r["h"] / 2 + cy - gy, steps=8)
                page.evaluate("() => document.getElementById('dg-drop-first').click()")
                page.wait_for_timeout(500)
                check(
                    page.evaluate(
                        "() => [...document.querySelectorAll('.dj-dashboard-grid__panel--dragging')].map(e => e.getAttribute('data-panel-id'))"
                    )
                    == ["usr"],
                    "grid: after a patch shifted the panels, the dragged panel is still the one marked (by id)",
                )
                n_before = int(text("#dg-n"))
                page.mouse.move(
                    r["l"] + 20 + cx - gx + 2, r["t"] + r["h"] / 2 + cy - gy + 2, steps=2
                )
                page.mouse.up()
                page.wait_for_function(
                    "n => parseInt(document.querySelector('#dg-n').textContent) > n",
                    arg=n_before,
                    timeout=6000,
                )
                check(
                    text("#dg-log") == "move usr 2,2",
                    f"grid: the drop is for the panel that was dragged ({text('#dg-log')})",
                )
                page.click("#dg-restore")
                page.wait_for_timeout(400)

                # the server decides: an app that ignores the event leaves the dashboard as it was
                page.click("#dg-ignore")
                page.wait_for_timeout(300)
                before = panel_cell("usr")
                rejected = int(text("#dg-rejected"))
                drag_header("usr", 1, 2)
                page.wait_for_function(
                    "n => parseInt(document.querySelector('#dg-rejected').textContent) > n",
                    arg=rejected,
                    timeout=6000,
                )
                page.wait_for_timeout(300)
                check(
                    panel_cell("usr") == before,
                    "grid: an event the server ignores leaves the panel where it was",
                )
                check(
                    page.evaluate(
                        f"() => !document.querySelector('{grid}').hasAttribute('data-dj-drop')"
                    ),
                    "grid: and no preview is left behind",
                )
                page.click("#dg-restore")
                page.wait_for_timeout(400)

                # forged payloads against the documented handler
                forged = [
                    ("dashboard_move", {"id": "nope", "col": 1, "row": 1}),
                    ("dashboard_move", {"id": "usr", "col": 9999, "row": -5}),
                    ("dashboard_resize", {"id": "rev", "width": "x", "height": 1}),
                    ("dashboard_resize", {"id": "err", "width": 99, "height": 99}),
                ]
                n0 = int(text("#dg-n"))
                for name, params in forged:
                    page.evaluate("([n, p]) => window.djust.handleEvent(n, p)", [name, params])
                page.wait_for_function(
                    "n => parseInt(document.querySelector('#dg-n').textContent) >= n",
                    arg=n0 + 4,
                    timeout=6000,
                )
                page.wait_for_timeout(300)
                check(
                    panel_cell("usr")[0] == 4,
                    f"grid: a forged column is clamped to the grid ({panel_cell('usr')})",
                )
                check(
                    span_of("err")[0] == 4,
                    f"grid: a forged width is clamped to the columns ({span_of('err')})",
                )
                check(
                    int(text("#dg-rejected")) >= 2,
                    f"grid: an unknown id and a non-integer are rejected ({text('#dg-rejected')})",
                )
                page.click("#dg-restore")
                page.wait_for_timeout(400)

                # text in a panel can be selected, the title (hostile) is text
                body_sel = f"{panel('rev')} .dj-dashboard-grid__panel-body"
                r = rect(body_sel)
                page.mouse.move(r["l"] + 12, r["t"] + 14)
                page.mouse.down()
                page.mouse.move(r["l"] + 160, r["t"] + 14, steps=6)
                page.mouse.up()
                check(
                    len(page.evaluate("() => String(getSelection())")) > 3,
                    "grid: text in a panel can be selected (the panel is not natively draggable)",
                )
                check(
                    page.evaluate(
                        "() => window.__pwn === undefined && document.querySelectorAll('#dg-box img').length === 0"
                    ),
                    "grid: a hostile panel title stays text",
                )
                check(
                    text(f"{panel('usr')} .dj-dashboard-grid__panel-title").startswith(
                        "Users <img"
                    ),
                    "grid: and shows as typed",
                )
                page.screenshot(path=str(shots / "02-grid.png"))

                # ======================= MentionsInput =======================
                mn = "#mn-box .dj-mentions"
                inp = f"{mn} .dj-mentions__input"
                lst = f"{mn} .dj-mentions__dropdown"
                page.eval_on_selector(mn, "e => e.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(200)
                check(
                    page.get_attribute(inp, "role") == "combobox"
                    and page.get_attribute(inp, "aria-expanded") == "false"
                    and page.get_attribute(inp, "aria-controls") == page.get_attribute(lst, "id"),
                    "mentions: the input is a collapsed combobox over the list",
                )
                page.click(inp)
                page.keyboard.type("Hi @al")
                check(
                    page.evaluate(
                        f"() => getComputedStyle(document.querySelector('{lst}')).display"
                    )
                    == "block"
                    and page.get_attribute(inp, "aria-expanded") == "true",
                    "mentions: typing @ opens the list",
                )
                shown = page.evaluate(
                    f"() => [...document.querySelectorAll('{lst} .dj-mentions__item')].filter(e => getComputedStyle(e).display !== 'none').map(e => e.dataset.userName)"
                )
                check(
                    shown == ["Alice Cooper", "Albert"], f"mentions: the list is filtered ({shown})"
                )
                check(
                    page.evaluate(
                        f"() => document.querySelector('{inp}').getAttribute('aria-activedescendant') === document.querySelector('{lst} [aria-selected=true]').id"
                    ),
                    "mentions: the active suggestion is aria-activedescendant",
                )
                box_in, box_list = rect(inp), rect(lst)
                check(
                    abs(box_list["t"] - box_in["b"]) < 4 and box_list["h"] > 20,
                    f"mentions: the list sits under the input ({box_in['b']:.0f} vs {box_list['t']:.0f})",
                )
                page.screenshot(path=str(shots / "03-mentions.png"))
                page.keyboard.press("ArrowDown")
                page.keyboard.press("Enter")
                check(
                    page.input_value(inp) == "Hi @Albert ",
                    f"mentions: ArrowDown + Enter inserts the suggestion ({page.input_value(inp)!r})",
                )
                page.wait_for_timeout(300)
                check(text("#mn-n") == "0", "mentions: Enter that chooses does not also submit")
                check(
                    page.get_attribute(inp, "aria-expanded") == "false",
                    "mentions: choosing closes the list",
                )
                # a second mention with the mouse
                page.keyboard.type("and @co")
                row = f"{lst} .dj-mentions__item >> text=Cora Lee"
                page.click(row)
                check(
                    page.input_value(inp) == "Hi @Albert and @Cora Lee ",
                    f"mentions: clicking a suggestion inserts it ({page.input_value(inp)!r})",
                )
                check(
                    page.evaluate(
                        f"() => document.activeElement === document.querySelector('{inp}')"
                    ),
                    "mentions: focus stays in the input",
                )
                # an edited-away mention is dropped, a hand-typed one never counted
                page.keyboard.type("@Bob typed by hand")
                page.keyboard.press("Enter")
                page.wait_for_function(
                    "() => document.querySelector('#mn-n').textContent === '1'", timeout=6000
                )
                check(
                    text("#mn-text") == "Hi @Albert and @Cora Lee @Bob typed by hand",
                    f"mentions: the text is sent ({text('#mn-text')!r})",
                )
                check(
                    text("#mn-ids") == "4,3",
                    f"mentions: only chosen people are mentions, in order ({text('#mn-ids')!r})",
                )
                check(
                    "key" in text("#mn-extra")
                    and "value" in text("#mn-extra")
                    and "field" in text("#mn-extra"),
                    f"mentions: the plain input event's fields are kept ({text('#mn-extra')!r})",
                )
                # edit a chosen mention away
                page.fill(inp, "")
                page.click(inp)
                page.keyboard.type("@bo")
                page.keyboard.press("Enter")  # chooses Bob: "@Bob "
                page.evaluate(f"() => document.querySelector('{inp}').setSelectionRange(0, 0)")
                page.keyboard.press("Delete")  # the @ is edited away: "Bob "
                page.keyboard.press("End")
                page.keyboard.press("Enter")
                page.wait_for_function(
                    "() => document.querySelector('#mn-n').textContent === '2'", timeout=6000
                )
                check(
                    text("#mn-ids") == "",
                    f"mentions: a mention edited away is not sent ({text('#mn-ids')!r}, {text('#mn-text')!r})",
                )
                # Escape closes without submitting; the hostile name is text
                page.fill(inp, "")
                page.keyboard.type("@")
                page.keyboard.press("Escape")
                check(
                    page.get_attribute(inp, "aria-expanded") == "false",
                    "mentions: Escape closes the list",
                )
                page.wait_for_timeout(200)
                check(text("#mn-n") == "2", "mentions: and sends nothing")
                page.fill(inp, "")
                page.keyboard.type("@<img")
                page.keyboard.press("Enter")
                check(
                    page.input_value(inp).startswith("@<img src=x onerror=window.__pwn=1>"),
                    f"mentions: a hostile name is inserted as text ({page.input_value(inp)!r})",
                )
                check(
                    page.evaluate(
                        "() => window.__pwn === undefined && document.querySelectorAll('#mn-box img').length === 0"
                    ),
                    "mentions: and runs nothing",
                )
                page.keyboard.press("Enter")
                page.wait_for_function(
                    "() => document.querySelector('#mn-n').textContent === '3'", timeout=6000
                )
                check(
                    text("#mn-ids") == "5",
                    f"mentions: its id arrives as the string the server rendered ({text('#mn-ids')!r})",
                )

                # ======================= CursorsOverlay =======================
                cu = "#cu-box .dj-cursors"
                page.eval_on_selector("#cu-stage", "e => e.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(300)
                check(
                    page.eval_on_selector_all(
                        f"{cu} .dj-cursors__cursor",
                        "els => els.every(e => e.getAttribute('aria-hidden') === 'true')",
                    ),
                    "cursors: the arrows are decoration (aria-hidden)",
                )
                check(
                    page.get_attribute(cu, "aria-label") == "2 cursors",
                    "cursors: the group keeps its label",
                )
                page.click("#cu-add")
                page.wait_for_function(
                    "() => document.querySelectorAll('#cu-box .dj-cursors__cursor').length === 3",
                    timeout=6000,
                )
                page.wait_for_timeout(400)
                check(live() == "Carol joined", f"cursors: a join is announced ({live()!r})")

                def inside_and_apart():
                    stage = rect("#cu-stage")
                    rects = page.eval_on_selector_all(
                        f"{cu} .dj-cursors__label",
                        "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })",
                    )
                    inside = all(
                        lf >= stage["l"] - 1
                        and t >= stage["t"] - 1
                        and r <= stage["r"] + 1
                        and b <= stage["b"] + 1
                        for lf, t, r, b in rects
                    )
                    apart = all(
                        not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
                        for i, a in enumerate(rects)
                        for b in rects[i + 1 :]
                    )
                    return inside, apart, rects

                inside, apart, rects = inside_and_apart()
                check(
                    inside,
                    f"cursors: every label is inside the overlay, also the one at the corner ({[[round(v) for v in r] for r in rects]})",
                )
                check(apart, "cursors: and no label sits on another")
                page.screenshot(path=str(shots / "04-cursors.png"))
                page.click("#cu-pile")
                page.wait_for_function(
                    "() => document.querySelectorAll('#cu-box .dj-cursors__cursor').length === 5",
                    timeout=6000,
                )
                page.wait_for_timeout(500)
                inside, apart, rects = inside_and_apart()
                check(
                    apart and inside,
                    f"cursors: five cursors, three of them on one spot, still have separate labels ({[[round(v) for v in r] for r in rects]})",
                )
                page.click("#cu-remove")
                page.wait_for_function(
                    "() => document.querySelectorAll('#cu-box .dj-cursors__cursor').length === 4",
                    timeout=6000,
                )
                page.wait_for_timeout(400)
                check(live() == "Bob left", f"cursors: a leave is announced ({live()!r})")
                page.click("#cu-move")
                page.wait_for_timeout(600)
                check(live() == "Bob left", "cursors: a movement is not announced")
                check(
                    page.eval_on_selector(
                        f"{cu} .dj-cursors__cursor", "e => getComputedStyle(e).transitionDuration"
                    )
                    != "0s",
                    "cursors: arrows ease to a new position",
                )
                page.emulate_media(reduced_motion="reduce")
                check(
                    page.eval_on_selector(
                        f"{cu} .dj-cursors__cursor", "e => getComputedStyle(e).transitionDuration"
                    )
                    == "0s",
                    "cursors: not under prefers-reduced-motion",
                )
                page.emulate_media(reduced_motion="no-preference")
                # a narrower overlay: the label of a cursor that no longer has room on its right goes to its left
                page.wait_for_timeout(300)
                alice = page.eval_on_selector(
                    f"{cu} .dj-cursors__cursor[data-user=Alice]", "e => parseFloat(e.style.left)"
                )
                page.evaluate(
                    f"() => {{ document.getElementById('cu-stage').style.width = '{int(alice + 30)}px'; }}"
                )
                page.wait_for_timeout(500)
                flipped = page.eval_on_selector(
                    f"{cu} .dj-cursors__cursor[data-user=Alice] .dj-cursors__label",
                    "e => e.classList.contains('dj-cursors__label--left')",
                )
                lab = rect(f"{cu} .dj-cursors__cursor[data-user=Alice] .dj-cursors__label")
                stage = rect("#cu-stage")
                check(
                    flipped and lab["r"] <= stage["r"] + 1,
                    f"cursors: a resize of the overlay re-places the labels (flipped {flipped}, label right {lab['r']:.0f} vs {stage['r']:.0f})",
                )
                page.evaluate(
                    "() => { document.getElementById('cu-stage').style.width = '420px'; }"
                )
                page.wait_for_timeout(500)
                check(
                    not page.eval_on_selector(
                        f"{cu} .dj-cursors__cursor[data-user=Alice] .dj-cursors__label",
                        "e => e.classList.contains('dj-cursors__label--left')",
                    ),
                    "cursors: and back again when there is room",
                )

                # ======================= CollabSelection =======================
                cs = "#cs-box .dj-collab-sel"
                page.eval_on_selector("#cs-box", "e => e.scrollIntoView({block: 'start'})")
                page.wait_for_timeout(500)
                highlights = (
                    "() => { const out = {}; for (const [k, h] of CSS.highlights) "
                    "if (k.startsWith('dj-collab-')) out[k] = [...h].map(r => r.toString()).join('|'); return out; }"
                )
                got = page.evaluate(highlights)
                check(
                    sorted(got.values()) == ["Hello", "brave"],
                    f"collab: each selection is highlighted at its offsets in the target's text ({got})",
                )
                check(
                    page.evaluate("() => document.getElementById('doc').innerHTML")
                    == "Hello brave new world. " * 60 + "The end.",
                    "collab: the page's own text nodes are untouched (no marks, no wrappers)",
                )
                check(
                    page.eval_on_selector(
                        cs, "e => e.classList.contains('dj-collab-sel--anchored')"
                    ),
                    "collab: the component marks itself anchored",
                )
                names = page.eval_on_selector_all(
                    f"{cs} .dj-collab-sel__label",
                    "els => els.map(e => { const r = e.getBoundingClientRect(); return [e.textContent, getComputedStyle(e).position, r.left, r.top, r.bottom]; })",
                )
                doc_r = rect("#doc")
                check(
                    all(n[1] == "fixed" for n in names)
                    and abs(names[0][2] - doc_r["l"]) < 12
                    and names[0][4] <= doc_r["t"] + 24,
                    f"collab: each name sits above the start of its range ({names})",
                )
                range_rect = page.evaluate(
                    "() => { const k = Object.keys(Object.fromEntries(CSS.highlights)).filter(k => k.startsWith('dj-collab-'))[0]; "
                    "const r = [...CSS.highlights.get(k)][0].getBoundingClientRect(); return [r.left, r.top]; }"
                )
                check(
                    abs(names[0][2] - range_rect[0]) < 3 and abs(names[0][4] - range_rect[1]) < 8,
                    f"collab: the first name is placed from its range's own client rect ({names[0][2:]} vs {range_rect})",
                )
                page.screenshot(path=str(shots / "05-collab.png"))
                # the highlight really paints (the ::highlight rule applies a background)
                check(
                    page.evaluate("() => !!document.querySelector('style[data-dj-collab-sel]')"),
                    "collab: the highlight rules are in place",
                )
                # scrolling the target moves the names with the text; one scrolled out is hidden
                page.evaluate("() => { document.getElementById('cs-scroll').scrollTop = 40; }")
                page.wait_for_timeout(300)
                vis = page.eval_on_selector_all(
                    f"{cs} .dj-collab-sel__label",
                    "els => els.map(e => getComputedStyle(e).visibility)",
                )
                check(
                    vis == ["hidden", "hidden"],
                    f"collab: names whose text scrolled out of the target are hidden ({vis})",
                )
                page.evaluate("() => { document.getElementById('cs-scroll').scrollTop = 0; }")
                page.wait_for_timeout(300)
                vis = page.eval_on_selector_all(
                    f"{cs} .dj-collab-sel__label",
                    "els => els.map(e => getComputedStyle(e).visibility)",
                )
                check(
                    vis == ["visible", "visible"],
                    f"collab: and shown again when it scrolls back ({vis})",
                )
                # the server moves the selections (and one is out of range)
                page.click("#cs-move")
                page.wait_for_timeout(500)
                got = page.evaluate(highlights)
                check(
                    sorted(got.values()) == ["new"],
                    f"collab: a patch re-anchors; an out-of-range selection draws nothing ({got})",
                )
                # the server edits the text
                page.click("#cs-edit")
                page.wait_for_timeout(500)
                check(
                    page.evaluate(
                        "() => document.getElementById('doc').textContent.startsWith('Howdy')"
                    ),
                    "collab: the server changed the target's text",
                )
                got = page.evaluate(highlights)
                check(
                    sorted(got.values()) == ["new"],
                    f"collab: highlights follow the edited text ({got})",
                )
                # hostile colours never reach the style rule
                page.click("#cs-hostile")
                page.wait_for_timeout(500)
                css = page.evaluate(
                    "() => [...document.querySelectorAll('style[data-dj-collab-sel]')].map(s => s.textContent).join('\\n')"
                )
                check(
                    "evil.example" not in css and "display:none" not in css and "body" not in css,
                    f"collab: a hostile colour is replaced by the palette in the highlight rule ({css[:120]!r})",
                )
                check(
                    page.evaluate("() => getComputedStyle(document.body).display") != "none",
                    "collab: and the page is intact",
                )
                # the component going away takes its highlights with it
                page.click("#cs-hide")
                page.wait_for_timeout(500)
                check(
                    page.evaluate(highlights) == {}
                    and page.evaluate("() => !document.querySelector('style[data-dj-collab-sel]')"),
                    "collab: removing the component leaves no highlight and no rule behind",
                )

                # ======================= app hooks win =======================
                check(
                    not [t for k, t in console if k == "pageerror"],
                    f"no page errors ({[t for k, t in console if k == 'pageerror']})",
                )
                load("/custom/")
                page.wait_for_timeout(300)
                hooks = page.evaluate("() => window.__appHooks || []")
                check(
                    sorted(hooks) == ["collab", "cursors", "grid", "mentions"],
                    f"custom: the app's own hooks ran, one each ({hooks})",
                )
                check(
                    page.eval_on_selector("#dg-box .dj-dashboard-grid__panel", "e => e.draggable"),
                    "custom: the shipped grid hook left the app's grid alone",
                )
                check(
                    page.get_attribute("#mn-box .dj-mentions__input", "role") is None,
                    "custom: the shipped mentions hook left the app's input alone",
                )
                check(
                    page.eval_on_selector(
                        "#cu-box .dj-cursors__cursor", "e => e.getAttribute('aria-hidden')"
                    )
                    is None,
                    "custom: the shipped cursors hook left the app's overlay alone",
                )
                check(
                    page.evaluate("() => CSS.highlights.size") == 0,
                    "custom: the shipped collab hook drew nothing",
                )
                # without the shipped mentions hook the plain input event still goes
                page.click("#mn-box .dj-mentions__input")
                page.keyboard.type("plain text")
                page.keyboard.press("Enter")
                page.wait_for_function(
                    "() => document.querySelector('#mn-n').textContent === '1'", timeout=6000
                )
                check(
                    "value" in text("#mn-extra") and "key" in text("#mn-extra"),
                    f"custom: the plain Enter event (value, key, ...) still reaches the server ({text('#mn-extra')!r})",
                )
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "custom: no 'No hook registered' warning",
                )
                check(
                    not [t for k, t in console if k == "pageerror"],
                    f"no page errors on the custom page ({[t for k, t in console if k == 'pageerror']})",
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
