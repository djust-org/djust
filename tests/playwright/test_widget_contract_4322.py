#!/usr/bin/env python3
"""Real-browser proof of the JavaScript widget contract (#4322, after #3656).

Drives a real LiveView in headless Chromium with two established libraries:
the shipped Chart.js adapter (``DJUST_CONFIG["extensions"] = ["chart"]`` with
Chart.js 4 from ``node_modules``) and an in-repo contenteditable editor, a
test-only fixture hook (``RichText`` below, no third-party editor library) that
is never part of djust. Each check prints ``ok`` / ``FAIL``; see "The widget contract" in
docs/website/guides/hooks.md for the rules.

WebSocket run (``/w/``):

* several instances of one widget (two charts, two editors) stay independent;
* a server patch updates a chart in place (same ``Chart`` object) and leaves the
  other chart and an editor's unsent text alone;
* ``pushEvent(..., {outcome: true})``: ok (after the reply is applied), error,
  rejected (strict parameters), rate_limited, disconnected and cancelled;
* IME composition and the caret inside the editor survive a patch to the
  widget's own element;
* a keyed item moved to another parent is destroyed and mounted once each;
* a hook removed while its async ``mounted()`` is pending gets ``destroyed()``
  and its late continuation sees it;
* full recovery after a failed patch (``html_recovery``) keeps every widget
  instance and unsent text, restores server-owned attributes and keeps the
  ones listed in ``dj-ignore-attrs``;
* a dropped socket reconnects with server state restored (``persist="server"``)
  and the widgets' local state kept;
* ``dj-navigate`` and ``live_redirect`` away destroy every widget once (no
  leaked Chart.js instance); Back mounts them again, with the editor's unsent
  draft restored from the widget's own storage.

SSE run and HTTP-only run (transport pinned, as tests/playwright/_transports.py
does): widgets mount, patches keep editor text, charts update, pushEvent
outcomes, and over SSE a stream reconnect with server state and ``dj-navigate``
+ Back.

Strict CSP run (``/csp/``, ``script-src 'nonce-…'`` only): the Chart.js adapter,
a ``{% colocated_hook %}`` and the editor all work with no CSP violation, and
an inline script without the nonce is blocked.

Self-contained: builds the project in a temp dir and serves it with uvicorn::

    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_widget_contract_4322.py

Needs ``npm ci`` (chart.js is a devDependency). ``CHROMIUM_EXECUTABLE``
optionally names the browser. Exits 0 on success, non-zero with the failures.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _transports import INIT, TransportWatch  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
VENDOR = {
    "chart.umd.js": REPO / "node_modules/chart.js/dist/chart.umd.js",
}

PROJECT = {
    "wapp/__init__.py": "",
    "wapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "wapp.urls"
        INSTALLED_APPS = [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "channels", "djust",
        ]
        MIDDLEWARE = [
            "wapp.csp.NonceCSP",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ]
        SESSION_ENGINE = "django.contrib.sessions.backends.cache"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        STATIC_URL = "/static/"
        STATICFILES_DIRS = [BASE_DIR / "assets"]
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        LIVEVIEW_ALLOWED_MODULES = ["wapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        DJUST_CONFIG = {"extensions": ["chart"]}
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "wapp/csp.py": """
        import secrets

        class NonceCSP:
            \"\"\"request.csp_nonce on every request; a nonce-only policy on /csp/.\"\"\"

            def __init__(self, get_response):
                self.get_response = get_response

            def __call__(self, request):
                request.csp_nonce = secrets.token_urlsafe(16)
                response = self.get_response(request)
                if request.path.startswith("/csp/"):
                    response["Content-Security-Policy"] = (
                        "script-src 'nonce-%s'; object-src 'none'; base-uri 'none'"
                        % request.csp_nonce
                    )
                return response
    """,
    "wapp/urls.py": """
        from django.urls import include, path
        from djust.sse import sse_urlpatterns
        from . import views
        urlpatterns = [
            path("w/", views.WidgetPage.as_view()),
            path("other/", views.OtherPage.as_view()),
            path("csp/", views.CspPage.as_view()),
            path("djust/", include(sse_urlpatterns)),
        ]
    """,
    "wapp/views.py": r'''
        import json
        import time

        from djust import LiveView, state
        from djust.decorators import event_handler, rate_limit

        HEAD = (
            '{% load live_tags %}<!DOCTYPE html><html><head><title>w</title>'
            '{% djust_client_config %}'
            '<script nonce="{{ nonce }}" src="/static/vendor/chart.umd.js"></script>'
            '<script nonce="{{ nonce }}" src="/static/hooks.js"></script>'
            '</head><body>'
        )

        BODY = """
        <div dj-root>
          <span id="count">{{ n }}</span>
          <button id="bump" dj-click="bump">bump</button>
          <button id="move" dj-click="move">move</button>
          <button id="toggle-slow" dj-click="toggle_slow">slow</button>
          <a id="go-other" href="/other/" dj-navigate="/other/">other</a>
          <button id="redirect" dj-click="go_other">redirect</button>
          <canvas id="c1" dj-hook="Chart" dj-ignore-attrs="style"
                  dj-hook-value-type='"bar"' dj-hook-value-data='{{ c1 }}'></canvas>
          <canvas id="c2" dj-hook="Chart" dj-ignore-attrs="style"
                  dj-hook-value-type='"line"' dj-hook-value-data='{{ c2 }}'></canvas>
          <div id="ed-a" dj-hook="RichText" dj-hook-value-doc='{{ doc_a_json }}' data-mark="{{ n }}">
            <div id="ed-a-host" dj-update="ignore"></div>
            <p class="saved">{{ doc_a }}</p>
          </div>
          <div id="ed-b" dj-hook="RichText" dj-hook-value-doc='{{ doc_b_json }}' data-mark="{{ n }}">
            <div id="ed-b-host" dj-update="ignore"></div>
            <p class="saved">{{ doc_b }}</p>
          </div>
          <ul id="left">{% for i in left %}<li id="k-{{ i }}" dj-key="{{ i }}" dj-hook="Rec" data-label="k-{{ i }}">{{ i }}</li>{% endfor %}</ul>
          <ul id="right">{% for i in right %}<li id="k-{{ i }}" dj-key="{{ i }}" dj-hook="Rec" data-label="k-{{ i }}">{{ i }}</li>{% endfor %}</ul>
          {% if slow %}<div id="slow" dj-hook="Slow"></div>{% endif %}
          <div id="writer" dj-hook="Writer" dj-ignore-attrs="data-kept" title="t{{ n }}"></div>
        </div>
        """

        def chart(values):
            return json.dumps({"labels": ["a", "b", "c"], "datasets": [{"label": "s", "data": values}]})

        def nonce_of(view):
            return getattr(getattr(view, "request", None), "csp_nonce", "") or ""

        class WidgetPage(LiveView):
            template = HEAD + BODY + "</body></html>"
            n = state(0, persist="server")
            doc_a = state("alpha", persist="server")
            doc_b = state("beta", persist="server")
            left = state(default_factory=lambda: [1, 2, 3])
            right = state(default_factory=lambda: [4])
            slow = state(False)

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["c1"] = chart([self.n, 2, 3])
                ctx["c2"] = chart([5, 5, 5])
                ctx["doc_a_json"] = json.dumps(self.doc_a)
                ctx["doc_b_json"] = json.dumps(self.doc_b)
                ctx["nonce"] = nonce_of(self)
                return ctx

            @event_handler()
            def bump(self):
                self.n += 1

            @event_handler()
            def save_doc(self, key: str, text: str):
                if key == "ed-a":
                    self.doc_a = text
                else:
                    self.doc_b = text

            @event_handler()
            def boom(self):
                raise ValueError("boom")

            @event_handler()
            def typed(self, n: int):
                self.n = n

            @event_handler()
            @rate_limit(rate=0.01, burst=1, on_exceed="drop")
            def limited(self):
                pass

            @event_handler()
            def slow_ack(self):
                time.sleep(1.5)

            @event_handler()
            def move(self):
                self.left = [1, 3]
                self.right = [4, 2]

            @event_handler()
            def toggle_slow(self):
                self.slow = not self.slow

            @event_handler()
            def go_other(self):
                self.live_redirect("/other/")

        class OtherPage(LiveView):
            template = (
                HEAD + '<div dj-root><p id="other-marker">other</p>'
                '<a id="go-w" href="/w/" dj-navigate="/w/">w</a></div></body></html>'
            )

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["nonce"] = nonce_of(self)
                return ctx

        class CspPage(LiveView):
            template = (
                HEAD
                + '<script>window.__unsafeInline = true;</script>'
                + """
                <div dj-root>
                  {% colocated_hook "Colo" %}
                    hook.mounted = function() { window.__log.push('colo.mounted'); };
                    hook.updated = function() { window.__log.push('colo.updated'); };
                  {% endcolocated_hook %}
                  <span id="count">{{ n }}</span>
                  <button id="bump" dj-click="bump">bump</button>
                  <div id="colo" dj-hook="Colo" data-n="{{ n }}"></div>
                  <canvas id="c1" width="300" height="150" dj-hook="Chart"
                          dj-hook-value-type='"bar"' dj-hook-value-data='{{ c1 }}'></canvas>
                  <div id="ed-a" dj-hook="RichText" dj-hook-value-doc='"csp"' data-mark="{{ n }}">
                    <div id="ed-a-host" dj-update="ignore"></div>
                  </div>
                </div></body></html>"""
            )
            n = state(0)

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["c1"] = chart([self.n, 1, 1])
                ctx["nonce"] = nonce_of(self)
                return ctx

            @event_handler()
            def bump(self):
                self.n += 1
    ''',
    # The test's own hooks, loaded as a nonce'd file so the strict-CSP page uses
    # them too. RichText is the editor widget: its own contenteditable subtree
    # lives in the dj-update=ignore host, props arrive as dj-hook-value-doc, edits leave by pushEvent, and the
    # unsent text is the widget's own draft (localStorage), kept across patches,
    # reconnects and navigation.
    "assets/hooks.js": r"""
        window.__log = [];
        window.__comp = [];
        const log = (x) => window.__log.push(x);
        window.DjustHooks = Object.assign(window.DjustHooks || {}, {
          Rec: {
            mounted() { this.el.__inst = Math.random(); log(this.el.dataset.label + '.mounted'); },
            updated() { log(this.el.dataset.label + '.updated'); },
            destroyed() { log(this.el.dataset.label + '.destroyed'); },
          },
          Slow: {
            async mounted() {
              log('slow.mounted.start');
              await window.__slowGate;
              if (this.gone) { log('slow.mounted.abandoned'); return; }
              this.el.textContent = 'ready';
              log('slow.mounted.done');
            },
            destroyed() { this.gone = true; log('slow.destroyed'); },
          },
          Writer: {
            mounted() { this.el.setAttribute('data-kept', 'w'); this.el.setAttribute('data-lost', 'w'); },
          },
          RichText: {
            mounted() {
              const id = this.el.id;
              this.el.__inst = Math.random();
              this.draftKey = 'draft:' + location.pathname + id;
              const host = this.el.querySelector('[dj-update="ignore"]');
              // The widget owns a contenteditable inside the dj-update=ignore host:
              // a patch never reaches it, so the caret, focus and an IME
              // composition in it survive.
              const ed = document.createElement('div');
              ed.className = 'ed-editor';
              ed.contentEditable = 'true';
              ed.style.whiteSpace = 'pre-wrap';
              ed.style.minHeight = '1.5em';
              ed.style.border = '1px solid #888';
              this.editor = ed;
              let draft = null;
              try { draft = localStorage.getItem(this.draftKey); } catch (e) { draft = null; }
              this.dirty = draft !== null;
              ed.textContent = draft !== null ? draft : this.values.doc;
              host.appendChild(ed);
              // 'input' fires for the user's typing and composition only, never
              // for the programmatic textContent writes of mounted()/updated().
              ed.addEventListener('input', () => {
                this.dirty = true;
                try { localStorage.setItem(this.draftKey, this.text()); } catch (e) { /* no storage */ }
              });
              ['compositionstart', 'compositionend'].forEach((t) =>
                ed.addEventListener(t, () => window.__comp.push(id + ':' + t)));
              log(id + '.mounted');
            },
            text() { return this.editor.textContent; },
            // The caret as a character offset into the text, or null when the
            // selection is not inside the editor.
            caret() {
              const sel = getSelection();
              if (!sel.rangeCount || !this.editor.contains(sel.anchorNode)) return null;
              const r = document.createRange();
              r.selectNodeContents(this.editor);
              r.setEnd(sel.anchorNode, sel.anchorOffset);
              return r.toString().length;
            },
            updated() {
              log(this.el.id + '.updated');
              // Server props only replace content the user has not edited.
              if (!this.dirty && this.values.doc !== this.text()) this.editor.textContent = this.values.doc;
            },
            async save() {
              const outcome = await this.pushEvent('save_doc', { key: this.el.id, text: this.text() }, { outcome: true });
              if (outcome.status === 'ok') {
                this.dirty = false;
                try { localStorage.removeItem(this.draftKey); } catch (e) { /* no storage */ }
              }
              return outcome;
            },
            disconnected() { log(this.el.id + '.disconnected'); },
            reconnected() { log(this.el.id + '.reconnected'); },
            destroyed() { log(this.el.id + '.destroyed'); this.editor = null; },
          },
        });
    """,
    "wapp/asgi.py": """
        import os
        os.environ["DJANGO_SETTINGS_MODULE"] = "wapp.settings"
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
    vendor = root / "assets" / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    for name, src in VENDOR.items():
        if not src.exists():
            raise SystemExit(
                "%s is missing: run npm ci (chart.js is a devDependency)" % src
            )
        shutil.copy(src, vendor / name)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")])),
    }
    py = os.environ.get("DJUST_SERVER_PYTHON", sys.executable)
    proc = subprocess.Popen(
        [
            py,
            "-m",
            "uvicorn",
            "wapp.asgi:application",
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
            urllib.request.urlopen(f"http://127.0.0.1:{port}/w/", timeout=1)
            return proc
        except Exception:  # noqa: BLE001 -- the server is still starting; retried below
            if proc.poll() is not None:
                raise SystemExit("server exited early")
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start")


class Run:
    """One browser page and its check helpers."""

    def __init__(self, browser, base, label, init=""):
        self.label = label
        self.base = base
        self.context = browser.new_context()
        if init:
            self.context.add_init_script(init)
        # The DEBUG error overlay (the deliberate handler errors) must not
        # cover the page the checks click on.
        self.context.add_init_script(
            "document.addEventListener('DOMContentLoaded', () => { const s = document.createElement('style');"
            " s.textContent = '#djust-error-overlay{display:none!important}'; document.head.appendChild(s); });"
        )
        self.context.add_init_script(
            "window.__csp = []; document.addEventListener('securitypolicyviolation',"
            " (e) => window.__csp.push([e.violatedDirective, e.blockedURI, e.sourceFile, e.lineNumber, e.sample].join(' ')));"
        )
        self.page = self.context.new_page()
        self.watch = TransportWatch(self.page)
        self.errors = []
        self.frames = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append("pageerror: %s" % e))
        self.page.on(
            "websocket", lambda ws: ws.on("framereceived", lambda f: self.frames.append(f))
        )
        self.failures = []

    def check(self, ok, what):
        what = "%s: %s" % (self.label, what)
        if not ok:
            self.failures.append(what)
        print(("ok   " if ok else "FAIL ") + what)

    def js(self, expr, arg=None):
        return self.page.evaluate(expr, arg)

    def wait(self, expr, timeout=8000):
        try:
            self.page.wait_for_function(expr, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 -- reported by the caller's check
            return False

    def goto(self, path, live=True):
        self.page.goto(self.base + path)
        if live:
            self.wait(
                "() => window.djust && window.djust.liveViewInstance && "
                "window.djust.liveViewInstance.viewMounted === true",
                15000,
            )
        self.wait("() => window.__log && window.__log.includes('ed-a.mounted')", 15000)
        self.page.wait_for_timeout(400)

    def log(self):
        return self.js("() => window.__log.slice()")

    def clear(self):
        self.js("() => { window.__log.length = 0; window.__comp.length = 0; }")

    def count(self):
        return self.js(
            "() => document.getElementById('count') && document.getElementById('count').textContent"
        )

    def bump_by_click(self):
        before = int(self.count())
        self.page.click("#bump")
        ok = self.wait(f"() => document.getElementById('count').textContent === '{before + 1}'")
        self.page.wait_for_timeout(250)
        return ok

    def editor_text(self, id_):
        return self.js(f"() => window.djust.getHook('#{id_}').text()")

    def type_into(self, id_, text):
        self.page.click(f"#{id_}-host .ed-editor")
        self.page.keyboard.press("End")
        self.page.keyboard.press("Control+End")
        self.page.keyboard.type(text)

    def outcome(self, id_, event, payload):
        return self.js(
            "([id, ev, p]) => window.djust.getHook('#' + id).pushEvent(ev, p, {outcome: true})"
            ".then(o => ({status: o.status, code: o.error && o.error.code,"
            " details: !!(o.error && o.error.validation_details), reply: !!o.reply}))",
            [id_, event, payload],
        )


def chart_state(run):
    return run.js(
        """() => ({
            count: Object.keys(Chart.instances).length,
            c1data: (Chart.getChart(document.getElementById('c1')) || {data: {datasets: [{data: []}]}}).data.datasets[0].data.slice(),
            hasC1: !!Chart.getChart(document.getElementById('c1')),
            hasC2: !!Chart.getChart(document.getElementById('c2')),
            distinct: Chart.getChart(document.getElementById('c1')) !== Chart.getChart(document.getElementById('c2')),
        })"""
    )


def common_widget_checks(run):
    """Several instances, a patch, unsent edits and the basic outcomes; every transport."""
    log = run.log()
    run.check(
        all(log.count(f"{x}.mounted") == 1 for x in ("ed-a", "ed-b", "k-1", "k-4")),
        f"page load: every widget instance mounted once ({log})",
    )
    cs = chart_state(run)
    run.check(
        cs["hasC1"] and cs["hasC2"] and cs["distinct"] and cs["count"] == 2,
        f"several instances: two Chart.js charts, one per canvas ({cs})",
    )
    run.check(
        run.editor_text("ed-a") == "alpha"
        and run.editor_text("ed-b") == "beta"
        and run.js(
            "() => window.djust.getHook('#ed-a').editor !== window.djust.getHook('#ed-b').editor"
        ),
        "several instances: two editors, each with its own server props",
    )

    # A patch: the chart whose data changed updates IN PLACE; the other is not
    # redrawn; an editor's unsent text stays.
    run.js(
        """() => {
            window.__c1 = Chart.getChart(document.getElementById('c1'));
            // The adapter's updated() assigns a new data object; Chart.js's own
            // resize redraws do not.
            window.__c2data = Chart.getChart(document.getElementById('c2')).data;
            window.__edB = document.getElementById('ed-b').__inst;
        }"""
    )
    run.type_into("ed-b", " unsent")
    n0 = int(run.count())
    run.check(run.bump_by_click(), "patch: the server render arrived")
    cs = chart_state(run)
    run.check(
        run.js("() => Chart.getChart(document.getElementById('c1')) === window.__c1")
        and cs["c1data"][0] == n0 + 1,
        f"Chart.js adapter: the chart whose data changed updated in place ({cs['c1data']})",
    )
    run.check(
        run.js("() => Chart.getChart(document.getElementById('c2')).data === window.__c2data"),
        "Chart.js adapter: the other chart instance was not redrawn",
    )
    run.check(
        run.editor_text("ed-b") == "beta unsent"
        and run.js("() => document.getElementById('ed-b').__inst === window.__edB"),
        f"rich text: unsent text and the instance survive the patch ({run.editor_text('ed-b')!r})",
    )

    # Outcomes.
    run.type_into("ed-a", "!")
    saved = run.js("() => window.djust.getHook('#ed-a').save().then(o => o.status)")
    run.page.wait_for_timeout(200)
    run.check(
        saved == "ok"
        and run.js("() => document.querySelector('#ed-a .saved').textContent") == "alpha!",
        f"pushEvent outcome ok: the save was applied when it resolved ({saved})",
    )
    rejected = run.outcome("ed-a", "typed", {"n": "x"})
    run.check(
        rejected["status"] == "rejected" and rejected["details"],
        f"pushEvent outcome rejected: strict parameter refusal ({rejected})",
    )
    first = run.outcome("ed-a", "limited", {})
    second = run.outcome("ed-a", "limited", {})
    run.check(
        first["status"] == "ok" and second["status"] == "rate_limited",
        f"pushEvent outcome rate_limited: @rate_limit dropped the second ({first}, {second})",
    )


def websocket_run(browser, base, failures):
    run = Run(browser, base, "websocket")
    run.goto("/w/")
    common_widget_checks(run)

    err = run.outcome("ed-a", "boom", {})
    run.check(err["status"] == "error", f"pushEvent outcome error: the handler raised ({err})")
    # A raising handler's error (DEBUG detail included) names its ref: an event
    # still pending behind it is not settled by it.
    both = run.js(
        "() => { const h = window.djust.getHook('#ed-a');"
        " const a = h.pushEvent('boom', {}, {outcome: true});"
        " const b = h.pushEvent('slow_ack', {}, {outcome: true});"
        " return Promise.all([a, b]).then(os => os.map(o => o.status)); }"
    )
    run.check(both == ["error", "ok"], f"an error outcome settles only its own event ({both})")

    # IME composition and caret across a patch to the widget's own element.
    run.page.click("#ed-a-host .ed-editor")
    run.page.keyboard.press("Control+End")
    run.page.keyboard.type(" ab")
    cdp = run.context.new_cdp_session(run.page)
    cdp.send("Input.imeSetComposition", {"text": "にほ", "selectionStart": 2, "selectionEnd": 2})
    run.page.wait_for_timeout(100)
    before = run.js(
        "() => ({sel: window.djust.getHook('#ed-a').caret(),"
        " range: (() => { const r = getSelection().getRangeAt(0); return [r.startContainer.textContent, r.startOffset]; })()})"
    )
    run.clear()
    n = int(run.count())
    run.js("() => window.djust.handleEvent('bump', {})")
    run.wait(f"() => document.getElementById('count').textContent === '{n + 1}'")
    run.page.wait_for_timeout(300)
    after = run.js(
        "() => ({sel: window.djust.getHook('#ed-a').caret(),"
        " active: document.activeElement && document.activeElement.closest('#ed-a-host') !== null,"
        " range: (() => { const r = getSelection().getRangeAt(0); return [r.startContainer.textContent, r.startOffset]; })(),"
        " comp: window.__comp.slice(), mark: document.getElementById('ed-a').dataset.mark})"
    )
    run.check(
        "ed-a.updated" in run.log() and after["mark"] == str(n + 1),
        "IME: the patch changed the widget's own element (updated() ran)",
    )
    run.check(
        after["active"] and after["comp"] == [] and after["range"] == before["range"],
        f"IME: focus, the composition and the caret survived the patch ({before} -> {after})",
    )
    # The same across a full morph: a failed patch recovered by html_recovery.
    run.frames.clear()
    run.js("() => document.getElementById('count').remove()")
    run.js("() => window.djust.handleEvent('bump', {})")
    run.wait(
        f"() => document.getElementById('count') && document.getElementById('count').textContent === '{n + 2}'"
    )
    run.page.wait_for_timeout(300)
    morphed = run.js(
        "() => ({active: document.activeElement && document.activeElement.closest('#ed-a-host') !== null,"
        " range: (() => { const r = getSelection().getRangeAt(0); return [r.startContainer.textContent, r.startOffset]; })(),"
        " comp: window.__comp.slice()})"
    )
    run.check(
        any("html_recovery" in f for f in run.frames)
        and morphed["active"]
        and morphed["comp"] == []
        and morphed["range"] == before["range"],
        f"IME: focus, the composition and the caret survived a full morph ({morphed})",
    )
    cdp.send("Input.insertText", {"text": "にほん"})
    run.page.wait_for_timeout(200)
    text = run.editor_text("ed-a")
    run.check(
        text.endswith(" abにほん") and "ed-a:compositionend" in run.js("() => window.__comp"),
        f"IME: the composition committed where it started ({text!r})",
    )

    # Keyed movement across parents.
    marks = run.js("() => ['k-1','k-3','k-4'].map(id => document.getElementById(id).__inst)")
    run.clear()
    run.page.click("#move")
    run.wait("() => document.querySelector('#right #k-2') !== null")
    run.page.wait_for_timeout(300)
    log = [x for x in run.log() if x.startswith("k-")]
    run.check(
        log.count("k-2.destroyed") == 1
        and log.count("k-2.mounted") == 1
        and log.index("k-2.destroyed") < log.index("k-2.mounted"),
        f"keyed move across parents: destroyed() then mounted() once each ({log})",
    )
    run.check(
        marks == run.js("() => ['k-1','k-3','k-4'].map(id => document.getElementById(id).__inst)")
        and not [
            x
            for x in log
            if x != "k-2.destroyed" and x != "k-2.mounted" and not x.endswith(".updated")
        ],
        "keyed move across parents: the other items keep their instances",
    )

    # Removal while an async mounted() is pending.
    run.js("() => { window.__slowGate = new Promise(r => { window.__releaseSlow = r; }); }")
    run.clear()
    run.page.click("#toggle-slow")
    run.wait("() => window.__log.includes('slow.mounted.start')")
    run.page.click("#toggle-slow")
    run.wait("() => window.__log.includes('slow.destroyed')")
    run.js("() => window.__releaseSlow()")
    run.page.wait_for_timeout(200)
    log = [x for x in run.log() if x.startswith("slow.")]
    run.check(
        log == ["slow.mounted.start", "slow.destroyed", "slow.mounted.abandoned"],
        f"late async mounted(): destroyed() first, the continuation saw it ({log})",
    )

    # Full recovery after a failed patch.
    run.js(
        "() => { window.__a = document.getElementById('ed-a').__inst; window.__b = document.getElementById('ed-b').__inst;"
        " window.__c1 = Chart.getChart(document.getElementById('c1')); }"
    )
    ed_b_before = run.editor_text("ed-b")
    run.frames.clear()
    run.clear()
    n = int(run.count())
    run.js("() => document.getElementById('count').remove()")  # the next patch cannot apply
    run.js("() => window.djust.handleEvent('bump', {})")
    recovered = run.wait(
        f"() => document.getElementById('count') && document.getElementById('count').textContent === '{n + 1}'"
    )
    run.page.wait_for_timeout(400)
    run.check(
        recovered and any("html_recovery" in f for f in run.frames),
        "full recovery: the failed patch was recovered by an html_recovery morph",
    )
    log = run.log()
    run.check(
        not [x for x in log if x.endswith((".destroyed", ".mounted"))]
        and run.js(
            "() => document.getElementById('ed-a').__inst === window.__a && document.getElementById('ed-b').__inst === window.__b"
        )
        and run.js("() => Chart.getChart(document.getElementById('c1')) === window.__c1"),
        f"full recovery: every widget keeps its instance ({log})",
    )
    run.check(
        all(f"{x}.updated" in log for x in ("ed-a", "ed-b", "k-1", "k-4")),
        "full recovery: updated() for every hook (a morph has no change record)",
    )
    run.check(run.editor_text("ed-b") == ed_b_before, "full recovery: unsent editor text kept")
    attrs = run.js(
        "() => { const w = document.getElementById('writer'); return [w.getAttribute('data-kept'), w.getAttribute('data-lost'), w.title]; }"
    )
    run.check(
        attrs == ["w", None, f"t{n + 1}"],
        f"full recovery: a widget-written attribute is reset unless in dj-ignore-attrs ({attrs})",
    )

    # Reconnect with server state restored.
    run.type_into("ed-b", " more")
    ed_b_before = run.editor_text("ed-b")
    n = run.count()
    saved_a = run.js("() => document.querySelector('#ed-a .saved').textContent")
    run.js(
        "() => { window.__a = document.getElementById('ed-a').__inst; window.__c1 = Chart.getChart(document.getElementById('c1')); }"
    )
    run.clear()
    run.js("() => window.djust.liveViewInstance.ws.close()")
    run.wait("() => ['ed-a','ed-b'].every(id => window.__log.includes(id + '.reconnected'))", 15000)
    run.wait("() => window.djust.liveViewInstance.viewMounted === true", 15000)
    run.page.wait_for_timeout(1000)
    run.check(
        run.count() == n
        and run.js("() => document.querySelector('#ed-a .saved').textContent") == saved_a,
        f"reconnect: server state restored (count {run.count()} == {n})",
    )
    log = run.log()
    run.check(
        not [x for x in log if x.endswith((".destroyed", ".mounted"))]
        and run.js("() => document.getElementById('ed-a').__inst === window.__a")
        and run.js("() => Chart.getChart(document.getElementById('c1')) === window.__c1")
        and run.editor_text("ed-b") == ed_b_before,
        f"reconnect: widget instances and unsent text kept ({log})",
    )
    saved = run.js("() => window.djust.getHook('#ed-b').save().then(o => o.status)")
    run.check(saved == "ok", "reconnect: the editor pushes its unsent text afterwards (ok)")

    # Disconnected: the socket drops while the event is pending.
    status = run.js(
        "() => { const p = window.djust.getHook('#ed-a').pushEvent('slow_ack', {}, {outcome: true});"
        " setTimeout(() => window.djust.liveViewInstance.ws.close(), 100); return p.then(o => o.status); }"
    )
    run.check(status == "disconnected", f"pushEvent outcome disconnected ({status})")
    run.wait("() => window.djust.liveViewInstance.viewMounted === true", 15000)
    run.page.wait_for_timeout(800)

    # dj-navigate away and Back.
    run.type_into("ed-b", " draft")
    draft = run.editor_text("ed-b")
    for how in ("dj-navigate", "live_redirect"):
        run.clear()
        run.page.click("#go-other" if how == "dj-navigate" else "#redirect")
        run.wait("() => document.getElementById('other-marker') !== null", 10000)
        run.page.wait_for_timeout(400)
        log = run.log()
        run.check(
            log.count("ed-a.destroyed") == 1
            and log.count("ed-b.destroyed") == 1
            and log.count("k-1.destroyed") == 1
            and run.js("() => Object.keys(Chart.instances).length") == 0,
            f"{how}: every widget destroyed once, no Chart.js instance left ({log})",
        )
        run.clear()
        run.page.go_back()
        run.wait(
            "() => window.__log.includes('ed-b.mounted') && window.__log.includes('ed-a.mounted')",
            15000,
        )
        run.wait("() => window.djust.liveViewInstance.viewMounted === true", 15000)
        run.page.wait_for_timeout(600)
        log = run.log()
        run.check(
            all(log.count(f"{x}.mounted") == 1 for x in ("ed-a", "ed-b", "k-1"))
            and run.js("() => Object.keys(Chart.instances).length") == 2,
            f"{how} + Back: every widget mounted once again, both charts drawn ({log})",
        )
        run.check(
            run.editor_text("ed-b") == draft,
            f"{how} + Back: the unsent draft restored by the widget ({run.editor_text('ed-b')!r})",
        )

    # Cancelled: the client closes the connection (as a page swap does).
    status = run.js(
        "() => { const p = window.djust.getHook('#ed-a').pushEvent('slow_ack', {}, {outcome: true});"
        " setTimeout(() => window.djust.liveViewInstance.disconnect(), 100); return p.then(o => o.status); }"
    )
    run.check(status == "cancelled", f"pushEvent outcome cancelled ({status})")

    bad = [
        e
        for e in run.errors
        if "Canvas is already in use" in e or "[dj-hook]" in e or "pageerror" in e
    ]
    run.check(not bad, f"no hook, Chart.js or page errors ({bad})")
    run.watch.check("websocket", "websocket", run.failures)
    failures.extend(run.failures)
    run.context.close()


def sse_run(browser, base, failures):
    run = Run(browser, base, "sse", INIT["sse"])
    run.goto("/w/")
    common_widget_checks(run)

    # The stream is cut and reconnects: server state restored, widgets kept.
    run.js("() => { window.__a = document.getElementById('ed-a').__inst; }")
    n = run.count()
    text_b = run.editor_text("ed-b")
    run.clear()
    run.js("() => window.djust.liveViewInstance.eventSource.close()")
    run.js(
        "() => { const i = window.djust.liveViewInstance; i.enabled = true; i.disconnect();"
        " i.connect(i.primaryViewPath, {}); }"
    )
    run.wait("() => window.djust.liveViewInstance.viewMounted === true", 15000)
    run.page.wait_for_timeout(800)
    got = [
        run.count(),
        run.js("() => document.getElementById('ed-a').__inst === window.__a"),
        run.editor_text("ed-b"),
    ]
    run.check(
        got == [n, True, text_b],
        f"stream reconnect: server state restored, widget instances and unsent text kept ({got} vs {n}, {text_b!r}, {run.log()})",
    )
    run.check(run.bump_by_click(), "stream reconnect: the view answers again")

    run.clear()
    run.page.click("#go-other")
    run.wait("() => document.getElementById('other-marker') !== null", 10000)
    run.page.wait_for_timeout(400)
    log = run.log()
    run.check(
        log.count("ed-a.destroyed") == 1
        and run.js("() => Object.keys(Chart.instances).length") == 0,
        f"dj-navigate over SSE: widgets destroyed once ({log})",
    )
    run.clear()
    run.page.go_back()
    run.wait("() => window.__log.includes('ed-a.mounted')", 15000)
    run.page.wait_for_timeout(600)
    run.check(
        run.log().count("ed-a.mounted") == 1
        and run.js("() => Object.keys(Chart.instances).length") == 2,
        "dj-navigate over SSE + Back: widgets mounted once again",
    )
    run.watch.check("sse", "sse", run.failures)
    failures.extend(run.failures)
    run.context.close()


def http_run(browser, base, failures):
    run = Run(browser, base, "http-only", INIT["http"])
    run.goto("/w/", live=False)
    run.check(
        run.js(
            "() => window.djust.liveViewInstance && window.djust.liveViewInstance.httpOnly === true"
        ),
        "the page chose the HTTP transport",
    )
    common_widget_checks(run)
    run.watch.check("http", "http-only", run.failures)
    failures.extend(run.failures)
    run.context.close()


def csp_run(browser, base, failures):
    run = Run(browser, base, "strict-csp")
    run.goto("/csp/")
    run.check(
        run.js("() => window.__unsafeInline") is None,
        "the nonce-only policy is in force (an inline script without the nonce was blocked)",
    )
    run.check(
        "colo.mounted" in run.log()
        and run.js(
            "() => document.querySelector('script[type=\"djust/hook\"]').dataset.djustHookRegistered"
        )
        != "error",
        "colocated hook: registered and mounted under the nonce-only policy",
    )
    run.check(
        run.js("() => !!Chart.getChart(document.getElementById('c1'))"),
        "Chart.js adapter: mounted under the nonce-only policy",
    )
    run.type_into("ed-a", "!")
    run.check(
        run.editor_text("ed-a") == "csp!", "rich text editor: works under the nonce-only policy"
    )
    run.clear()
    run.check(run.bump_by_click(), "a patch applies under the nonce-only policy")
    run.check(
        "colo.updated" in run.log()
        and run.js("() => Chart.getChart(document.getElementById('c1')).data.datasets[0].data[0]")
        == 1,
        "colocated hook and adapter updated by the patch",
    )
    violations = run.js("() => window.__csp.slice()")
    elem = [v for v in violations if v.startswith("script-src-elem")]
    # The DEBUG-only debug panel binds inline handlers (script-src-attr); the
    # panel is a development tool, not part of the page's client.
    other = [
        v for v in violations if not v.startswith("script-src-elem") and "debug-panel.js" not in v
    ]
    run.check(
        len(elem) == 1 and "/csp/" in elem[0] and not other,
        f"the only script blocked is the deliberate inline one ({violations})",
    )
    failures.extend(run.failures)
    run.context.close()


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        server = start_server(Path(tmp), port)
        base = f"http://localhost:{port}"
        try:
            with sync_playwright() as p:
                executable = os.environ.get("CHROMIUM_EXECUTABLE")
                browser = p.chromium.launch(
                    **({"executable_path": executable} if executable else {})
                )
                for scenario in (websocket_run, sse_run, http_run, csp_run):
                    try:
                        scenario(browser, base, failures)
                    except Exception as exc:  # noqa: BLE001 -- reported as a failure
                        failures.append("%s crashed: %r" % (scenario.__name__, exc))
                        print("FAIL %s crashed: %r" % (scenario.__name__, exc))
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
