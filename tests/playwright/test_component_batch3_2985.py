#!/usr/bin/env python3
"""Real-browser check of the ActivityFeed, Terminal and Tour hooks (#2985, batch 3).

Those three components rendered a ``dj-hook`` that no shipped script answered.
Each now has a script under ``djust_components/``. This drives them, as a
reader would, against a real LiveView over a real WebSocket:

* ActivityFeed: events pushed by the server appear at the top, the feed keeps
  its length, the new activity is announced, Page Down / Page Up move between
  articles, a 200-event burst stays cheap;
* Terminal: streamed lines with ANSI colour look like the lines the server
  rendered (a computed-style comparison over a corpus), a stream follows the
  newest line until the reader scrolls up (also with max_lines trimming old
  rows, over several bursts), 2,000 pushed lines keep following, hostile text
  and escape sequences stay text, oversized lines and long parameter lists are
  cheap;
* LogViewer: the same trimmed-burst following (the shared follow logic);
* Tour: spotlight and ring line up with the target (also inside a transformed
  ancestor), the popover stays in the viewport, the target is scrolled into
  view, a step whose target is missing falls back to a centred popover, arrows
  and Escape drive the server through the component's own buttons, focus is
  trapped and returned;
* a page whose app registered its OWN hook of the same name keeps it.

Self-contained: builds a two-page LiveView project in a temp directory, serves
it with uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_component_batch3_2985.py

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
    for name in ("activity-feed", "terminal", "tour", "log-viewer")
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
            path("xform/", views.Xform.as_view()),
        ]
    """,
    "cmpapp/views.py": (
        '''
        from djust import LiveView
        from djust.decorators import event_handler

        SCRIPTS = '%s'

        ESC = "\\x1b"
        HOSTILE = "<img src=x onerror=window.__pwn=1>"

        CORPUS = [
            "plain " + ESC + "[32mgreen" + ESC + "[0m " + ESC + "[1mbold" + ESC + "[0m",
            ESC + "[1;31mboth" + ESC + "[0m end",
            ESC + "[1mb " + ESC + "[34mblue-bold " + ESC + "[92mbright",
            ESC + "[31mred " + ESC + "[mreset " + ESC + "[0m",
            ESC + "[7mrev" + ESC + "[38;5;12m 256 " + ESC + "[39mdef",
            ESC + "[2Jcleared" + ESC + "[ tail" + ESC,
            ESC + "[31" + " unterminated",
            ESC + "[31m" + HOSTILE + ESC + "[0m",
        ]

        BODY = """
        <div dj-root>
          <h1>components</h1>
          <button id="start-tour" dj-click="start_tour">start tour</button>
          <p>tour: <span id="tour-open">{{ tour_open }}</span> step <span id="tour-step">{{ tour_active }}</span>
             last: <span id="tour-last">{{ tour_last }}</span></p>
          <div id="xform" style="{{ xform_style }}">
            {%% if tour_open %%}{%% tour steps=steps active=tour_active event="tour" %%}{%% endif %%}
          </div>
          <nav id="t-sidebar" style="width:200px;padding:6px;border:1px solid #888">sidebar</nav>
          <button id="t-create" style="margin-left:300px">create</button>
          <button id="post" dj-click="post">post</button>
          <button id="burst" dj-click="burst">burst</button>
          <button id="burst-many" dj-click="burst_many">burst many</button>
          <section id="feed-box">
            {%% activity_feed events=events stream="activity_update" max=6 %%}
          </section>
          <section id="term-box">
            {%% terminal output=lines title="Build" stream_event="term_out" show_line_numbers=True max_lines=5000 %%}
          </section>
          <button id="term-one" dj-click="term_one">term one</button>
          <button id="term-many" dj-click="term_many">term many</button>
          <button id="term-corpus" dj-click="term_corpus">term corpus</button>
          <section id="server-term">
            {%% terminal output=corpus title="Server" %%}
          </section>
          <section id="stream-term">
            {%% terminal output=empty title="Stream" stream_event="corpus_out" %%}
          </section>
          <input id="t-input" aria-label="a field">
          <section id="trim-term">
            {%% terminal output=empty title="Trim" stream_event="trim_out" show_line_numbers=True wrap=True max_lines=50 %%}
          </section>
          <section id="trim-log">
            {%% log_viewer lines=empty stream_event="log_trim" max_lines=50 wrap=True %%}
          </section>
          <button id="trim-burst" dj-click="trim_burst">trim burst</button>
          <div style="height:2600px"></div>
          <button id="t-far">far away</button>
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
                self.events = [
                    {"user": "Alice Cooper", "action": "commented on", "target": "Issue #42", "time": "2m ago"},
                    {"user": "Bob", "action": "merged", "target": "PR #17", "time": "5m ago"},
                ]
                self.lines = ["$ make", ESC + "[32mok" + ESC + "[0m start"]
                self.corpus = list(CORPUS)
                self.empty = []
                self.xform_style = ""
                self.n = 0
                self.t = 0
                self.tour_open = False
                self.tour_active = 0
                self.tour_last = ""
                self.steps = [
                    {"target": "#t-sidebar", "title": "Navigation", "content": "Use the sidebar."},
                    {"target": "#t-create", "title": "Create " + HOSTILE, "content": "Click here " + HOSTILE},
                    {"target": "#t-missing", "title": "Gone", "content": "This target is not on the page."},
                    {"target": "#t-far", "title": "Far", "content": "Scrolled into view."},
                    {"target": "#t-input", "title": "Field", "content": "Type here."},
                ]

            @event_handler()
            def start_tour(self, **kwargs):
                self.tour_open = True
                self.tour_active = 0

            @event_handler()
            def tour(self, value="", **kwargs):
                self.tour_last = str(value)
                if value == "next":
                    self.tour_active = min(self.tour_active + 1, len(self.steps) - 1)
                elif value == "prev":
                    self.tour_active = max(self.tour_active - 1, 0)
                elif value in ("skip", "finish"):
                    self.tour_open = False

            @event_handler()
            def post(self, **kwargs):
                self.n += 1
                self.push_event("activity_update", {"events": [
                    {"user": "Poster " + str(self.n), "action": "posted", "target": "item " + str(self.n), "time": "now"}]})

            @event_handler()
            def burst(self, **kwargs):
                self.push_event("activity_update", {"events": [
                    {"user": "Burst " + str(i), "action": "did", "time": "now"} for i in range(10)]})

            @event_handler()
            def burst_many(self, **kwargs):
                for i in range(200):
                    self.push_event("activity_update", {"event": {"user": "Many " + str(i), "action": "did"}})

            @event_handler()
            def term_one(self, **kwargs):
                self.t += 1
                self.push_event("term_out", {"lines": [
                    ESC + "[1;33mwarn" + ESC + "[0m line " + str(self.t),
                    HOSTILE + ESC + "[31m" + HOSTILE + ESC + "[0m"]})

            @event_handler()
            def term_many(self, **kwargs):
                for i in range(2000):
                    self.push_event("term_out", {"line": "line " + str(i)})

            @event_handler()
            def trim_burst(self, **kwargs):
                # Lines of varying length (wrapped, so varying height) into two
                # views that keep only their last 50 lines.
                for i in range(1500):
                    text = "w" * (5 + (i * i * 31 + i * 7) %% 420) + " " + str(i)
                    self.push_event("trim_out", {"line": text})
                    self.push_event("log_trim", {"line": "INFO " + text})

            @event_handler()
            def term_corpus(self, **kwargs):
                self.push_event("corpus_out", {"lines": CORPUS})

        class Demo(Base):
            template = page(SCRIPTS)

        class Xform(Base):
            template = page(SCRIPTS)

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.xform_style = "transform: translate(40px, 30px); width: 600px"

        class Custom(Base):
            # The app registered its own hooks: two in window.djust.hooks, one in window.DjustHooks.
            template = page(
                "<script>window.djust = window.djust || {}; window.djust.hooks = {"
                "ActivityFeed: {mounted() { (window.__appHooks = window.__appHooks || []).push('feed'); }},"
                "Terminal: {mounted() { (window.__appHooks = window.__appHooks || []).push('terminal'); }}};"
                "window.DjustHooks = {"
                "Tour: {mounted() { (window.__appHooks = window.__appHooks || []).push('tour'); }}};"
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
    shots = Path(os.environ.get("SHOTS_DIR") or tempfile.mkdtemp(prefix="components-batch3-"))
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

                def active_class():
                    return page.evaluate(
                        "() => document.activeElement.className || document.activeElement.id"
                    )

                # ======================= ActivityFeed =======================
                load("/")
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "no 'No hook registered' warning with all three scripts",
                )
                feed = "#feed-box .dj-activity-feed"
                rows = f"{feed} .dj-activity-feed__item"
                users = (
                    "() => [...document.querySelectorAll('#feed-box .dj-activity-feed__user')]"
                    ".map(e => e.textContent)"
                )
                check(
                    page.eval_on_selector_all(rows, "els => els.map(e => e.tabIndex)") == [0, 0],
                    "feed: each article is focusable",
                )
                page.click("#post")
                page.wait_for_function(
                    "() => document.querySelector('#feed-box .dj-activity-feed__user').textContent === 'Poster 1'",
                    timeout=6000,
                )
                check(
                    page.evaluate(users)[:3] == ["Poster 1", "Alice Cooper", "Bob"],
                    "feed: a pushed event appears at the top",
                )
                page.wait_for_timeout(150)
                check(
                    "Poster 1 posted item 1" in live(),
                    f"feed: the activity is announced ({live()!r})",
                )
                page.click("#burst")
                page.wait_for_function(
                    "() => document.querySelector('#feed-box .dj-activity-feed__user').textContent === 'Burst 0'",
                    timeout=6000,
                )
                check(len(page.evaluate(users)) == 6, "feed: the feed keeps max_items rows (6)")
                check(
                    "10 new activities" in live(),
                    f"feed: a burst is announced as a count ({live()!r})",
                )
                check(
                    page.eval_on_selector_all(
                        rows, "els => els.map(e => e.getAttribute('aria-posinset'))"
                    )
                    == ["1", "2", "3", "4", "5", "6"],
                    "feed: articles are renumbered",
                )
                page.focus(f"{rows}:nth-child(1)")
                page.keyboard.press("PageDown")
                check(
                    page.evaluate(
                        "() => document.activeElement === document.querySelectorAll('#feed-box .dj-activity-feed__item')[1]"
                    ),
                    "feed: Page Down moves to the next article",
                )
                page.keyboard.press("PageUp")
                check(
                    page.evaluate(
                        "() => document.activeElement === document.querySelectorAll('#feed-box .dj-activity-feed__item')[0]"
                    ),
                    "feed: Page Up moves back",
                )
                t0 = time.time()
                page.click("#burst-many")
                page.wait_for_function(
                    "() => document.querySelector('#feed-box .dj-activity-feed__user').textContent === 'Many 199'",
                    timeout=20000,
                )
                took = time.time() - t0
                check(len(page.evaluate(users)) == 6, "feed: 200 single pushes still leave 6 rows")
                check(took < 8, f"feed: 200 pushed events took {took:.1f}s end to end (< 8)")
                page.screenshot(
                    path=str(shots / "01-feed.png"),
                    clip={"x": 0, "y": 0, "width": 1000, "height": 800},
                )

                # ======================= Terminal =======================
                body = "#term-box .dj-terminal__body"
                gap = "b => b.scrollHeight - b.clientHeight - b.scrollTop"
                root = "#term-box .dj-terminal"
                check(
                    page.get_attribute(root, "role") == "log"
                    and page.get_attribute(root, "aria-live") == "off"
                    and page.get_attribute(root, "aria-label") == "Build"
                    and page.get_attribute(body, "tabindex") == "0",
                    "terminal: named log region, quiet for screen readers, scrollable by keyboard",
                )
                page.click("#term-one")
                page.wait_for_function(
                    "s => document.querySelector(s).textContent.includes('warn line 1')",
                    arg=body,
                    timeout=6000,
                )
                check(
                    page.eval_on_selector(
                        body, "b => b.querySelectorAll('.dj-terminal__line-num').length"
                    )
                    == 4
                    and page.eval_on_selector(
                        body,
                        "b => b.lastElementChild.querySelector('.dj-terminal__line-num').textContent",
                    )
                    == "4",
                    "terminal: streamed lines continue the line numbers",
                )
                check(
                    page.evaluate(
                        "() => window.__pwn === undefined && !document.querySelector('#term-box img')"
                    ),
                    "terminal: hostile streamed text stays text",
                )
                colour = page.eval_on_selector(
                    body,
                    "b => getComputedStyle([...b.querySelectorAll('.dj-terminal__text span')].find(s => s.textContent === 'warn')).color",
                )
                weight = page.eval_on_selector(
                    body,
                    "b => getComputedStyle([...b.querySelectorAll('.dj-terminal__text span')].find(s => s.textContent === 'warn')).fontWeight",
                )
                check(
                    colour == "rgb(241, 196, 15)" and weight in ("700", "bold"),
                    f"terminal: bold yellow ANSI renders ({colour}, {weight})",
                )
                # a long stream keeps following
                t0 = time.time()
                page.evaluate("() => document.getElementById('term-many').click()")
                page.wait_for_function(
                    "s => document.querySelector(s).textContent.includes('line 1999')",
                    arg=body,
                    timeout=30000,
                )
                page.wait_for_timeout(400)
                took = time.time() - t0
                g = page.eval_on_selector(body, gap)
                check(
                    g <= 1,
                    f"terminal: 2,000 pushed lines keep following (gap {g:.0f}px, {took:.1f}s)",
                )
                page.evaluate("() => document.getElementById('term-many').click()")
                page.wait_for_timeout(2500)
                check(
                    page.eval_on_selector(body, gap) <= 1,
                    "terminal: and a second burst follows too",
                )
                # a reader scrolled up stays up
                page.eval_on_selector(body, "b => { b.scrollTop = 200; }")
                page.wait_for_timeout(200)
                page.evaluate("() => document.getElementById('term-many').click()")
                page.wait_for_timeout(3500)
                check(
                    page.eval_on_selector(body, "b => b.scrollTop") == 200,
                    "terminal: a reader who scrolled up stays up while 2,000 lines stream",
                )
                page.eval_on_selector(body, "b => { b.scrollTop = b.scrollHeight; }")
                page.wait_for_timeout(200)
                page.evaluate("() => document.getElementById('term-many').click()")
                page.wait_for_timeout(3500)
                check(
                    page.eval_on_selector(body, gap) <= 1,
                    "terminal: scrolling back to the bottom resumes following",
                )
                check(
                    page.eval_on_selector(
                        body, "b => b.querySelectorAll('.dj-terminal__line').length"
                    )
                    <= 5000,
                    "terminal: max_lines bounds the rows",
                )
                page.screenshot(
                    path=str(shots / "02-terminal.png"),
                    clip={"x": 0, "y": 0, "width": 1000, "height": 800},
                )

                # server-rendered lines and streamed lines look the same (computed style over a corpus)
                page.evaluate("() => document.getElementById('term-corpus').click()")
                page.wait_for_function(
                    "() => document.querySelectorAll('#stream-term .dj-terminal__line').length === 8",
                    timeout=6000,
                )
                runs = """
                    (sel) => [...document.querySelectorAll(sel + ' .dj-terminal__text')].map((line) => {
                        const out = [];
                        const walk = (n) => {
                            if (n.nodeType === 3) {
                                if (!n.textContent) return;
                                const cs = getComputedStyle(n.parentElement);
                                const w = parseInt(cs.fontWeight, 10) >= 600 ? 'b' : '';
                                const last = out[out.length - 1];
                                if (last && last[1] === cs.color && last[2] === w) last[0] += n.textContent;
                                else out.push([n.textContent, cs.color, w]);
                            } else n.childNodes.forEach(walk);
                        };
                        line.childNodes.forEach(walk);
                        return out;
                    })
                """
                server_runs = page.evaluate(runs, "#server-term")
                stream_runs = page.evaluate(runs, "#stream-term")
                check(
                    server_runs == stream_runs,
                    "terminal: streamed ANSI lines are styled exactly like server-rendered ones (8 lines, computed style)",
                )
                if server_runs != stream_runs:
                    for a, b in zip(server_runs, stream_runs):
                        if a != b:
                            print("   server:", a, "\n   stream:", b)
                # pathological input in the real page: cheap, flat, text only
                nested = page.evaluate(
                    """() => {
                        const hook = window.djust.getHook('#stream-term .dj-terminal');
                        const ESC = String.fromCharCode(27);
                        const t0 = performance.now();
                        window.djust.dispatchPushEventToHooks('corpus_out', {line: ESC + '[' + Array(100000).fill('1').join(';') + 'mtext'});
                        window.djust.dispatchPushEventToHooks('corpus_out', {line: 'y'.repeat(1000000)});
                        window.djust.dispatchPushEventToHooks('corpus_out', {line: ESC + '[31m' + 'z'.repeat(1000000)});
                        window.djust.dispatchPushEventToHooks('corpus_out', {line: ESC + '['.repeat(100000)});
                        window.djust.dispatchPushEventToHooks('corpus_out', {line: (ESC + '[1m').repeat(50000) + 'q'});
                        const took = performance.now() - t0;
                        const lines = [...document.querySelectorAll('#stream-term .dj-terminal__text')].slice(-5);
                        return {took, depth: Math.max(...lines.map((l) => { let d = 0; l.querySelectorAll('*').forEach((e) => { let n = 0, x = e; while (x && x !== l) { n++; x = x.parentElement; } d = Math.max(d, n); }); return d; })),
                                lens: lines.map((l) => l.textContent.length), ok: !!hook};
                    }"""
                )
                check(
                    nested["took"] < 2500,
                    f"terminal: oversized lines and long parameter lists took {nested['took']:.0f} ms (< 2500)",
                )
                check(
                    nested["depth"] <= 1,
                    f"terminal: styled runs stay flat (depth {nested['depth']})",
                )
                check(
                    nested["lens"] == [4, 1000000, 1000000, 100001, 1],
                    f"terminal: the text is all there ({nested['lens']})",
                )

                # ---- trimmed streams keep following across bursts (max_lines=50, wrapped, varying heights) ----
                for name, body_sel, tail in (
                    ("terminal", "#trim-term .dj-terminal__body", ".dj-terminal__text"),
                    ("log viewer", "#trim-log .dj-log-viewer__body", ".dj-log-viewer__text"),
                ):
                    gaps = []
                    for burst in range(1, 7):
                        page.evaluate("() => document.getElementById('trim-burst').click()")
                        page.wait_for_function(
                            "([s, t]) => { const r = [...document.querySelectorAll(s + ' ' + t)].pop(); "
                            "return r && r.textContent.endsWith(' 1499') && r.dataset.burst !== 'x'; }",
                            arg=[body_sel, tail],
                            timeout=30000,
                        )
                        page.wait_for_timeout(500)
                        gaps.append(page.eval_on_selector(body_sel, gap))
                        page.evaluate(
                            "([s, t]) => { [...document.querySelectorAll(s + ' ' + t)].pop().dataset.burst = 'x'; }",
                            [body_sel, tail],
                        )
                    check(
                        all(g <= 1 for g in gaps),
                        f"{name}: six trimmed bursts of 1,500 variable-height lines all end at the bottom (gaps {[round(g) for g in gaps]})",
                    )

                # ======================= Tour =======================
                page.evaluate("() => window.scrollTo(0, 0)")
                page.focus("#start-tour")
                page.click("#start-tour")
                page.wait_for_selector(".dj-tour__popover", timeout=6000)
                page.wait_for_timeout(500)
                check(
                    page.evaluate(
                        "() => document.activeElement.classList.contains('dj-tour__popover')"
                    ),
                    "tour: the popover takes focus",
                )
                check(
                    page.get_attribute(".dj-tour", "aria-labelledby")
                    == page.get_attribute(".dj-tour__title", "id"),
                    "tour: the dialog is named by the step title",
                )

                def geometry():
                    return page.evaluate(
                        """() => {
                            const r = (e) => { const b = e.getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; };
                            const tg = document.querySelector(document.querySelector('.dj-tour').dataset.target);
                            const ring = document.querySelector('.dj-tour__ring');
                            return {target: tg ? r(tg) : null, ring: ring && ring.style.display !== 'none' ? r(ring) : null,
                                    pop: r(document.querySelector('.dj-tour__popover')),
                                    clip: document.querySelector('.dj-tour__overlay').style.clipPath,
                                    vw: innerWidth, vh: innerHeight};
                        }"""
                    )

                g = geometry()
                tg, ring, pop = g["target"], g["ring"], g["pop"]
                check(
                    ring is not None
                    and abs(ring["l"] - (tg["l"] - 6)) <= 1.5
                    and abs(ring["t"] - (tg["t"] - 6)) <= 1.5
                    and abs(ring["w"] - (tg["w"] + 12)) <= 1.5
                    and abs(ring["h"] - (tg["h"] + 12)) <= 1.5,
                    f"tour: the ring lines up with the target ({ring} vs {tg})",
                )
                check("evenodd" in g["clip"], "tour: the overlay has a cut-out around the target")
                check(
                    pop["t"] >= tg["b"]
                    and pop["l"] >= 0
                    and pop["r"] <= g["vw"]
                    and pop["b"] <= g["vh"],
                    f"tour: the popover sits below the target, inside the viewport ({pop})",
                )
                check(
                    page.evaluate(
                        "() => { const t = document.querySelector('#t-sidebar').getBoundingClientRect(); const p = document.querySelector('.dj-tour__popover').getBoundingClientRect(); return p.top >= t.bottom; }"
                    ),
                    "tour: the popover does not cover the target",
                )
                page.screenshot(path=str(shots / "03-tour-step1.png"))
                # clicks reach the highlighted target through the cut-out, and nothing else
                check(
                    page.evaluate(
                        "() => document.elementFromPoint(100, document.querySelector('#t-sidebar').getBoundingClientRect().top + 8).id"
                    )
                    == "t-sidebar",
                    "tour: the highlighted target is clickable through the cut-out",
                )
                check(
                    page.evaluate("() => document.elementFromPoint(900, 700).className")
                    == "dj-tour__overlay",
                    "tour: the rest of the page is covered by the overlay",
                )
                page.keyboard.press("ArrowRight")
                wait_text("#tour-step", "1", "tour: ArrowRight goes to the next step on the server")
                wait_text(
                    "#tour-last",
                    "next",
                    "tour: through the component's own Next button (value 'next')",
                )
                page.wait_for_timeout(500)
                check(
                    live().startswith("Step 2 of 5"),
                    f"tour: the new step is announced ({live()!r})",
                )
                check(
                    page.evaluate(
                        "() => document.activeElement.classList.contains('dj-tour__popover')"
                    ),
                    "tour: focus is back in the popover after the server re-render",
                )
                check(
                    page.evaluate(
                        "() => window.__pwn === undefined && !document.querySelector('.dj-tour img')"
                    ),
                    "tour: hostile step text stays text",
                )
                g = geometry()
                check(
                    g["ring"] is not None and abs(g["ring"]["l"] - (g["target"]["l"] - 6)) <= 1.5,
                    "tour: the ring follows the target to the next step",
                )
                page.keyboard.press("ArrowRight")
                wait_text(
                    "#tour-step",
                    "2",
                    "tour: ArrowRight to the step whose target is not on the page",
                )
                page.wait_for_timeout(400)
                g = geometry()
                pop = g["pop"]
                check(
                    g["ring"] is None and g["clip"] == "",
                    "tour: a missing target highlights nothing",
                )
                check(
                    abs((pop["l"] + pop["r"]) / 2 - g["vw"] / 2) <= 2
                    and abs((pop["t"] + pop["b"]) / 2 - g["vh"] / 2) <= 2,
                    f"tour: and the popover is centred ({pop})",
                )
                page.keyboard.press("ArrowRight")
                wait_text("#tour-step", "3", "tour: ArrowRight to the far step")
                page.wait_for_timeout(1500)
                g = geometry()
                check(
                    g["target"] is not None
                    and 0 <= g["target"]["t"]
                    and g["target"]["b"] <= g["vh"],
                    f"tour: the far target was scrolled into view ({g['target']})",
                )
                check(
                    g["ring"] is not None
                    and abs(g["ring"]["t"] - (g["target"]["t"] - 6)) <= 1.5
                    and 0 <= g["pop"]["t"]
                    and g["pop"]["b"] <= g["vh"],
                    "tour: ring and popover are in place after the scroll",
                )
                before = geometry()["ring"]["t"]
                page.evaluate("() => window.scrollBy(0, -150)")
                page.wait_for_timeout(500)
                after = geometry()
                check(
                    after["ring"] is not None
                    and abs(after["ring"]["t"] - (after["target"]["t"] - 6)) <= 1.5
                    and abs(after["ring"]["t"] - before) > 20,
                    "tour: scrolling the page moves the ring with the target",
                )
                page.set_viewport_size({"width": 700, "height": 500})
                page.wait_for_timeout(700)
                g = geometry()
                check(
                    g["pop"]["r"] <= 700
                    and g["pop"]["b"] <= 500
                    and g["pop"]["l"] >= 0
                    and g["pop"]["t"] >= 0,
                    "tour: a resized window keeps the popover inside the viewport",
                )
                page.set_viewport_size({"width": 1000, "height": 800})
                page.wait_for_timeout(300)
                # focus is trapped
                inside = True
                for _ in range(8):
                    page.keyboard.press("Tab")
                    inside = inside and page.evaluate(
                        "() => !!document.activeElement.closest('.dj-tour')"
                    )
                check(inside, "tour: Tab stays inside the popover")
                page.keyboard.press("Shift+Tab")
                check(
                    page.evaluate("() => !!document.activeElement.closest('.dj-tour')"),
                    "tour: Shift+Tab too",
                )
                page.keyboard.press("ArrowLeft")
                wait_text("#tour-step", "2", "tour: ArrowLeft goes back")
                page.keyboard.press("Escape")
                wait_text("#tour-open", "False", "tour: Escape skips the tour on the server")
                wait_text(
                    "#tour-last",
                    "skip",
                    "tour: through the component's own Skip button (value 'skip'), and no stray empty event after it from djust's own modal Escape handling",
                )
                page.wait_for_timeout(300)
                check(
                    page.evaluate("() => document.activeElement.id") == "start-tour",
                    "tour: focus returns to the button that started it",
                )
                check(
                    page.evaluate("() => document.querySelector('.dj-tour__ring')") is None,
                    "tour: no ring is left behind",
                )
                # a second run: one key press moves exactly one step (no double binding)
                page.click("#start-tour")
                page.wait_for_selector(".dj-tour__popover", timeout=6000)
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(600)
                check(
                    text("#tour-step") == "1",
                    "tour: after restarting, one key press moves one step",
                )
                page.keyboard.press("Escape")
                wait_text("#tour-open", "False", "tour: closes again")

                # a field in the spotlight keeps focus and can be typed into
                page.click("#start-tour")
                page.wait_for_selector(".dj-tour__popover", timeout=6000)
                for want in ("1", "2", "3", "4"):
                    page.keyboard.press("ArrowRight")
                    wait_text("#tour-step", want, f"tour: ArrowRight to step {want}")
                    page.wait_for_timeout(300)
                page.wait_for_timeout(1500)
                box = page.locator("#t-input").bounding_box()
                page.mouse.click(box["x"] + 20, box["y"] + box["height"] / 2)
                page.keyboard.type("abc")
                check(
                    page.evaluate("() => document.activeElement.id") == "t-input"
                    and page.evaluate("() => document.getElementById('t-input').value") == "abc",
                    "tour: a field in the spotlight takes focus and can be typed into",
                )
                page.keyboard.press("ArrowLeft")
                page.wait_for_timeout(400)
                check(
                    text("#tour-step") == "4"
                    and page.evaluate("() => document.getElementById('t-input').selectionStart")
                    == 2,
                    "tour: and the arrow keys move its caret, not the tour",
                )
                page.keyboard.press("Escape")
                wait_text("#tour-open", "False", "tour: Escape in the field still skips the tour")

                # ======================= Tour inside a transformed ancestor =======================
                load("/xform/")
                page.focus("#start-tour")
                page.click("#start-tour")
                page.wait_for_selector(".dj-tour__popover", timeout=6000)
                page.wait_for_timeout(500)
                g = geometry()
                tg, ring = g["target"], g["ring"]
                check(
                    ring is not None
                    and abs(ring["l"] - (tg["l"] - 6)) <= 1.5
                    and abs(ring["t"] - (tg["t"] - 6)) <= 1.5
                    and abs(ring["w"] - (tg["w"] + 12)) <= 1.5
                    and abs(ring["h"] - (tg["h"] + 12)) <= 1.5,
                    f"tour: inside a transformed ancestor the ring still lines up with the target ({ring} vs {tg})",
                )
                check(
                    page.evaluate(
                        "() => { const o = document.querySelector('.dj-tour__overlay').getBoundingClientRect(); "
                        "return [Math.round(o.left), Math.round(o.top), Math.round(o.width), Math.round(o.height), innerWidth, innerHeight]; }"
                    )
                    == [0, 0, 1000, 800, 1000, 800],
                    "tour: the overlay is put back over the whole viewport (the ancestor's transform would have made it a 0-height box)",
                )
                check(
                    page.evaluate("() => document.elementFromPoint(900, 700).className")
                    == "dj-tour__overlay",
                    "tour: and it still covers the rest of the page",
                )
                g = geometry()
                check(
                    g["pop"]["t"] >= g["target"]["b"]
                    and 0 <= g["pop"]["l"]
                    and g["pop"]["r"] <= 1000
                    and g["pop"]["b"] <= 800,
                    f"tour: the popover is placed correctly inside the viewport ({g['pop']})",
                )
                page.screenshot(path=str(shots / "04-tour-transformed.png"))
                page.keyboard.press("Escape")
                wait_text("#tour-open", "False", "tour: closes")

                # ======================= app hooks win =======================
                console.clear()
                load("/custom/")
                ran = page.evaluate("() => window.__appHooks || []")
                check(
                    sorted(set(ran)) == ["feed", "terminal"],
                    f"custom: the app's own ActivityFeed and Terminal hooks (djust.hooks) ran ({ran})",
                )
                page.click("#start-tour")
                page.wait_for_selector(".dj-tour__popover", timeout=6000)
                page.wait_for_timeout(400)
                ran = page.evaluate("() => window.__appHooks || []")
                check("tour" in ran, "custom: the app's own Tour hook (window.DjustHooks) ran")
                check(
                    page.get_attribute(".dj-tour", "aria-labelledby") is None
                    and page.eval_on_selector(".dj-tour__overlay", "e => e.style.clipPath") == ""
                    and page.evaluate("() => !document.querySelector('.dj-tour__ring')")
                    and page.evaluate(
                        "() => !document.activeElement.classList.contains('dj-tour__popover')"
                    ),
                    "custom: the shipped Tour hook did not touch the dialog",
                )
                page.evaluate("() => document.getElementById('post').click()")
                page.wait_for_timeout(400)
                check(
                    page.eval_on_selector(
                        "#feed-box .dj-activity-feed__item", "e => e.hasAttribute('tabindex')"
                    )
                    is False
                    and page.eval_on_selector_all(
                        "#feed-box .dj-activity-feed__item", "els => els.length"
                    )
                    == 2,
                    "custom: the shipped feed hook left the app's feed alone (no tabindex, nothing streamed)",
                )
                check(
                    page.get_attribute("#term-box .dj-terminal", "role") is None
                    and page.eval_on_selector(
                        "#term-box .dj-terminal__body", "e => e.hasAttribute('tabindex')"
                    )
                    is False,
                    "custom: the shipped terminal hook left the app's terminal alone",
                )
                check(
                    not [t for k, t in console if "No hook registered" in t],
                    "custom: no 'No hook registered' warning",
                )
                page.screenshot(path=str(shots / "04-app-hooks-win.png"))

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
