#!/usr/bin/env python3
"""Real-browser check that `<body>` is a root (#3302).

A page whose `<body>` carries `dj-view` / `dj-root` has the page's top-level
siblings (header, main, footer, ...) as the view's content. Before #3302 the
WebSocket mount replaced the whole body with the first element; now the body is
the root and the nodes the server did not render (djust's injected scripts, the
debug panel, browser-extension nodes) are left alone.

Self-contained: builds a LiveView project in a temp directory, serves it with
uvicorn and drives it with headless Chromium over WebSocket, SSE and HTTP-only.

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_body_root_3302.py

``CHROMIUM_EXECUTABLE`` optionally names a Chromium binary. ``BODY_ROOT_ONLY``
(comma separated section names) runs a subset. Exits 0 on success, non-zero
with the list of failures. Not part of the CI suite (see README.md).
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

sys.path.insert(0, str(Path(__file__).parent))
from _transports import INIT, TransportWatch  # noqa: E402

SIBLINGS = (
    '<header id="hd">H</header>'
    '{% if n|divisibleby:2 %}<aside id="as">even</aside>{% endif %}'
    '<main id="mn"><span id="cnt">{{ n }}</span> <input id="inp" type="text"> '
    '<button id="btn" dj-click="inc">+</button>'
    '<div id="tall" style="height:3000px">tall</div></main>'
    '<footer id="ft">F{{ n }}</footer>'
)


def document(body_attrs, inner, head=""):
    return (
        "{% load live_tags %}<!DOCTYPE html><html><head><title>t</title>"
        "{% djust_client_config %}"
        + head
        + "</head><body"
        + body_attrs
        + ">"
        + inner
        + "</body></html>"
    )


PROJECT = {
    "bodyapp/__init__.py": "",
    "bodyapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "bodyapp.urls"
        INSTALLED_APPS = [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "channels", "djust", "bodyapp",
        ]
        MIDDLEWARE = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ]
        SESSION_ENGINE = "django.contrib.sessions.backends.cache"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        STATIC_URL = "/static/"
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        LIVEVIEW_ALLOWED_MODULES = ["bodyapp"]
        DJUST_LIVE_RENDER_ALLOWED_MODULES = ["bodyapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "bodyapp/asgi.py": """
        import os
        os.environ["DJANGO_SETTINGS_MODULE"] = "bodyapp.settings"
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
    # `{% extends %}`: the body root lives in the BASE template.
    "bodyapp/templates/bodyapp/base.html": (
        "{% load live_tags %}<!DOCTYPE html><html><head><title>t</title>"
        "{% djust_client_config %}</head><body dj-root>{% block content %}{% endblock %}</body></html>"
    ),
    "bodyapp/templates/bodyapp/ext.html": (
        '{% extends "bodyapp/base.html" %}{% block content %}' + SIBLINGS + "{% endblock %}"
    ),
}

PAGES = {
    # name -> (body attributes, inner markup, extra class attributes)
    "body": ' dj-view="bodyapp.views.Body"',
    "bodyroot": " dj-root",
}


def views_source():
    out = [
        "from djust import LiveView",
        "from djust.decorators import event_handler",
        "",
        "class Counter(LiveView):",
        "    def mount(self, request, **kw):",
        "        self.n = 0",
        "    @event_handler()",
        "    def inc(self, **kw):",
        "        self.n += 1",
        "",
    ]

    def cls(name, template=None, template_name=None, extra=""):
        out.append(f"class {name}(Counter):")
        if template is not None:
            out.append(f"    template = {template!r}")
        if template_name:
            out.append(f"    template_name = {template_name!r}")
        out.append(extra or "    pass")
        out.append("")

    cls("Body", document(' dj-view="bodyapp.views.Body"', SIBLINGS))
    cls("BodyRoot", document(" dj-root", SIBLINGS))
    cls("Ext", template_name="bodyapp/ext.html")
    cls("Stream", document(" dj-root", SIBLINGS), extra="    streaming_render = True")
    cls("Wrapped", document("", "<div dj-root>" + SIBLINGS + "</div>"))
    # An app script toggles a body class; the server never sets one.
    cls(
        "Theme",
        document(
            ' dj-root class="light"',
            SIBLINGS
            + '<button id="theme" onclick="document.body.classList.toggle(\'dark\')">t</button>',
        ),
    )
    cls("BodyClick", document(' dj-root dj-click="inc"', SIBLINGS.replace(' dj-click="inc"', "")))
    # Embedded children inside a body root: an ordinary one and a sticky one.
    child = (
        '<div dj-root><span id="ccnt">{{ n }}</span>'
        '<button id="cbtn" dj-click="inc">c+</button></div>'
    )
    cls("Child", child)
    cls("StickyChild", child, extra='    sticky = True\n    sticky_id = "sc"')
    embed = (
        SIBLINGS
        + '<section id="child">{% live_render "bodyapp.views.Child" %}</section>'
        + '<section id="stk">{% live_render "bodyapp.views.StickyChild" sticky=True %}</section>'
    )
    cls("Embed", document(" dj-root", embed))
    cls("EmbedWrap", document("", "<div dj-root>" + embed + "</div>"))
    # Navigation between two body-root pages.
    for name, other in (("NavA", "nava"), ("NavB", "navb")):
        target = "navb" if name == "NavA" else "nava"
        inner = (
            f'<nav><a id="go" dj-navigate="/{target}/">go</a></nav><h1 id="which">{name}</h1>'
            + SIBLINGS
        )
        cls(name, document(" dj-root", inner))
    # Lazy views inside a body root (one mount_batch).
    cls(
        "Widget",
        '<div dj-root><span class="wn">{{ n }}</span><button class="wb" dj-click="inc">w</button></div>',
    )
    lazy = (
        SIBLINGS
        + '<div id="w1" dj-view="bodyapp.views.Widget" dj-lazy="idle"></div>'
        + '<div id="w2" dj-view="bodyapp.views.Widget" dj-lazy="idle"></div>'
    )
    cls("Lazy", document(" dj-root", lazy))
    cls("LazyWrap", document("", "<div dj-root>" + lazy + "</div>"))
    # The shape T005 warns about: dj-view on <body>, dj-root on an inner element.
    cls(
        "Split",
        document(' dj-view="bodyapp.views.Split"', "<div dj-root>" + SIBLINGS + "</div>"),
    )
    cls("Twin", document(" dj-root", "<div dj-root>" + SIBLINGS + "</div>"))
    return "\n".join(out)


URLS = {
    "body": "Body",
    "bodyroot": "BodyRoot",
    "ext": "Ext",
    "stream": "Stream",
    "wrapped": "Wrapped",
    "theme": "Theme",
    "bodyclick": "BodyClick",
    "embed": "Embed",
    "embedwrap": "EmbedWrap",
    "lazywrap": "LazyWrap",
    "nava": "NavA",
    "navb": "NavB",
    "lazy": "Lazy",
    "split": "Split",
    "twin": "Twin",
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(root: Path, port: int) -> subprocess.Popen:
    files = dict(PROJECT)
    files["bodyapp/views.py"] = views_source()
    files["bodyapp/urls.py"] = (
        "from django.urls import include, path\nfrom djust.sse import sse_urlpatterns\nfrom . import views\n"
        "urlpatterns = [\n    path('djust/', include(sse_urlpatterns)),\n"
        + "".join(f"    path('{u}/', views.{c}.as_view()),\n" for u, c in URLS.items())
        + "]\n"
    )
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
    # BODY_ROOT_EXTRA_PYTHONPATH puts another djust build first (to compare with
    # the version before #3302).
    extra = os.environ.get("BODY_ROOT_EXTRA_PYTHONPATH")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(root), extra]))}
    py = os.environ.get("DJUST_SERVER_PYTHON", sys.executable)
    proc = subprocess.Popen(
        [
            py,
            "-m",
            "uvicorn",
            "bodyapp.asgi:application",
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
            urllib.request.urlopen(f"http://127.0.0.1:{port}/wrapped/", timeout=1)
            return proc
        except Exception:
            if proc.poll() is not None:
                raise SystemExit("server exited early")
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start")


# Simulates what a browser extension or dev toolbar does to <body>: a node first,
# a node last, and an inline script that must run exactly once.
EXTENSION = """
document.addEventListener('DOMContentLoaded', () => {
  if (!window.__noLead) {
    const lead = document.createElement('div'); lead.id = 'ext-lead';
    document.body.insertBefore(lead, document.body.firstChild);
  }
  const trail = document.createElement('div'); trail.id = 'ext-trail';
  document.body.appendChild(trail);
  const s = document.createElement('script');
  s.id = 'ext-script'; s.nonce = 'abc';
  s.textContent = 'window.__extRuns = (window.__extRuns || 0) + 1;';
  document.body.appendChild(s);
});
"""

SNAP = """() => ({
  ids: ['hd','mn','ft','cnt','btn'].filter(i => document.getElementById(i)),
  cnt: (document.getElementById('cnt') || {}).textContent || null,
  ft: (document.getElementById('ft') || {}).textContent || null,
  aside: !!document.getElementById('as'),
  mounted: !!(window.djust && window.djust.liveViewInstance && window.djust.liveViewInstance.viewMounted),
  lead: document.body.firstElementChild && document.body.firstElementChild.id,
  trail: !!document.getElementById('ext-trail'),
  extRuns: window.__extRuns || 0,
  clientScripts: document.querySelectorAll('script[src*="client"]').length,
  debug: !!document.getElementById('djust-debug-button'),
  bodyClass: document.body.className,
})"""


class Run:
    def __init__(self, browser, base, failures):
        self.browser, self.base, self.failures = browser, base, failures

    def check(self, label, cond, detail=""):
        print(("ok   " if cond else "FAIL ") + label + ("" if cond else "  " + str(detail)))
        if not cond:
            self.failures.append(label + (" " + str(detail) if detail else ""))

    def open(self, transport, path, extension=True):
        ctx = self.browser.new_context()
        ctx.add_init_script(INIT[transport])
        if transport == "http":
            # An HTTP-only page is never stamped with dj-id, so a LEADING foreign
            # node cannot be told apart from the page's own: a documented limit.
            ctx.add_init_script("window.__noLead = true;")
        if extension:
            ctx.add_init_script(EXTENSION)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)[:200]))
        page.on(
            "console",
            lambda m: errors.append("console: " + m.text[:200]) if m.type == "error" else None,
        )
        watch = TransportWatch(page)
        page.goto(self.base + path)
        return ctx, page, errors, watch

    def wait_live(self, page, transport):
        if transport == "http":
            page.wait_for_timeout(1500)
            return
        try:
            page.wait_for_function(
                "() => window.djust && window.djust.liveViewInstance && "
                "window.djust.liveViewInstance.viewMounted === true",
                timeout=8000,
            )
        except Exception:
            pass
        page.wait_for_timeout(500)

    def settle_text(self, page, selector, expected, timeout=6000):
        try:
            page.wait_for_function(
                "([s, e]) => { const el = document.querySelector(s); return !!el && el.textContent === e; }",
                arg=[selector, expected],
                timeout=timeout,
            )
            return True
        except Exception:
            return False

    def settle_cnt(self, page, expected, timeout=6000):
        try:
            page.wait_for_function(
                "(e) => (document.getElementById('cnt') || {}).textContent === e",
                arg=expected,
                timeout=timeout,
            )
            return True
        except Exception:
            return False


def section_basic(run, transport, page_name):
    label = f"{page_name}/{transport}"
    ctx, page, errors, watch = run.open(transport, f"/{page_name}/")
    http = page.evaluate(SNAP)
    run.check(
        f"{label}: HTTP render has every sibling",
        http["ids"] == ["hd", "mn", "ft", "cnt", "btn"],
        http,
    )
    run.wait_live(page, transport)
    scripts_before = http["clientScripts"]
    mounted = page.evaluate(SNAP)
    run.check(
        f"{label}: siblings survive the mount",
        mounted["ids"] == ["hd", "mn", "ft", "cnt", "btn"],
        mounted,
    )
    if transport != "http":
        run.check(f"{label}: view mounted", mounted["mounted"], mounted)
    # The first element of <body>: the extension's node (not added on an HTTP-only
    # page), else the page's own first element.
    if transport != "http":
        lead_ok = "ext-lead"
    else:
        lead_ok = "" if page_name == "wrapped" else "hd"
    run.check(
        f"{label}: extension nodes survive the mount",
        mounted["lead"] == lead_ok and mounted["trail"],
        mounted,
    )
    run.check(f"{label}: extension script ran once", mounted["extRuns"] == 1, mounted["extRuns"])
    run.check(
        f"{label}: djust client scripts kept", mounted["clientScripts"] == scripts_before, mounted
    )
    run.check(f"{label}: debug panel kept", mounted["debug"] == http["debug"], mounted)
    # Type, keep focus, scroll, then update twice (a conditional sibling toggles).
    page.fill("#inp", "hello")
    page.focus("#inp")
    page.evaluate("() => window.scrollTo(0, 600)")
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: first update lands", run.settle_cnt(page, "1"))
    s1 = page.evaluate(SNAP)
    run.check(f"{label}: conditional sibling removed", not s1["aside"], s1)
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: second update lands", run.settle_cnt(page, "2"))
    s2 = page.evaluate(SNAP)
    run.check(f"{label}: conditional sibling inserted", s2["aside"] and s2["ft"] == "F2", s2)
    state = page.evaluate(
        "() => ({v: document.getElementById('inp').value, f: document.activeElement && document.activeElement.id,"
        " y: Math.round(window.scrollY)})"
    )
    run.check(f"{label}: input value kept", state["v"] == "hello", state)
    run.check(f"{label}: focus kept", state["f"] == "inp", state)
    run.check(f"{label}: scroll kept", abs(state["y"] - 600) < 5, state)
    run.check(
        f"{label}: extension nodes still there after updates",
        s2["lead"] == lead_ok and s2["trail"] and s2["extRuns"] == 1,
        s2,
    )
    run.check(f"{label}: no client errors", not errors, errors[:3])
    watch.check(transport, label, run.failures)
    ctx.close()


def section_wrapped(run, transport):
    """The existing wrapper contract is unchanged."""
    label = f"wrapped/{transport}"
    ctx, page, errors, watch = run.open(transport, "/wrapped/")
    run.wait_live(page, transport)
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: update lands", run.settle_cnt(page, "1"))
    s = page.evaluate(SNAP)
    lead_ok = "ext-lead" if transport != "http" else ""
    run.check(
        f"{label}: extension nodes kept",
        s["lead"] == lead_ok and s["trail"] and s["extRuns"] == 1,
        s,
    )
    run.check(f"{label}: no client errors", not errors, errors[:3])
    ctx.close()


def section_navigation(run, transport):
    label = f"navigate/{transport}"
    ctx, page, errors, _watch = run.open(transport, "/nava/")
    run.wait_live(page, transport)
    page.evaluate("() => { window.__marker = 'alive'; }")
    page.click("#go")
    try:
        page.wait_for_function(
            "() => document.getElementById('which') && document.getElementById('which').textContent === 'NavB'",
            timeout=8000,
        )
    except Exception:
        pass
    page.wait_for_timeout(800)
    s = page.evaluate(SNAP)
    which = page.evaluate("() => (document.getElementById('which') || {}).textContent")
    run.check(f"{label}: moved to the second page", which == "NavB", which)
    run.check(
        f"{label}: no reload (marker alive)", page.evaluate("() => window.__marker") == "alive"
    )
    run.check(
        f"{label}: extension nodes kept across navigation",
        s["lead"] == "ext-lead" and s["trail"] and s["extRuns"] == 1,
        s,
    )
    run.check(
        f"{label}: djust scripts kept across navigation",
        s["clientScripts"] >= 1 and s["debug"],
        s,
    )  # noqa: E712
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: update after navigation", run.settle_cnt(page, "1"))
    page.go_back()
    try:
        page.wait_for_function(
            "() => document.getElementById('which') && document.getElementById('which').textContent === 'NavA'",
            timeout=8000,
        )
    except Exception:
        pass
    page.wait_for_timeout(800)
    back = page.evaluate("() => (document.getElementById('which') || {}).textContent")
    run.check(f"{label}: Back returns to the first page", back == "NavA", back)
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: update after Back", run.settle_cnt(page, "1"))
    s = page.evaluate(SNAP)
    run.check(
        f"{label}: extension nodes kept after Back",
        s["lead"] == "ext-lead" and s["trail"] and s["extRuns"] == 1,
        s,
    )
    run.check(f"{label}: no client errors", not errors, errors[:3])
    ctx.close()


def section_theme(run):
    label = "theme/websocket"
    ctx, page, errors, _watch = run.open("websocket", "/theme/")
    run.wait_live(page, "websocket")
    page.click("#theme")
    for n in ("1", "2", "3"):
        page.evaluate("() => document.getElementById('btn').click()")
        run.settle_cnt(page, n)
    s = page.evaluate(SNAP)
    run.check(
        f"{label}: app-toggled body class survives server updates",
        "dark" in s["bodyClass"] and "light" in s["bodyClass"],
        s["bodyClass"],
    )
    run.check(f"{label}: no client errors", not errors, errors[:3])
    ctx.close()


def section_body_click(run):
    label = "bodyclick/websocket"
    ctx, page, errors, _watch = run.open("websocket", "/bodyclick/")
    run.wait_live(page, "websocket")
    page.click("#hd")
    run.check(f"{label}: dj-click on <body> fires", run.settle_cnt(page, "1"))
    ctx.close()


def section_embed(run, transport, page_name="embed"):
    label = f"{page_name}/{transport}"
    ctx, page, errors, _watch = run.open(transport, f"/{page_name}/")
    run.wait_live(page, transport)
    page.evaluate("() => document.querySelector('#child #cbtn').click()")
    run.check(f"{label}: child updates itself", run.settle_text(page, "#child #ccnt", "1"))
    parent = page.evaluate("() => document.getElementById('cnt').textContent")
    run.check(f"{label}: child click did not touch the parent", parent == "0", parent)
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: parent updates", run.settle_cnt(page, "1"))
    if transport == "websocket":
        page.evaluate("() => document.querySelector('#stk #cbtn').click()")
        run.check(f"{label}: sticky child updates itself", run.settle_text(page, "#stk #ccnt", "1"))
    run.check(f"{label}: no client errors", not errors, errors[:3])
    ctx.close()


def section_lazy(run, page_name="lazy"):
    label = f"{page_name}/websocket"
    ctx, page, errors, _watch = run.open("websocket", f"/{page_name}/")
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#w1 .wb, #w2 .wb').length === 2", timeout=10000
        )
    except Exception:
        pass
    run.check(
        f"{label}: both lazy views hydrated",
        page.evaluate("() => document.querySelectorAll('#w1 .wb, #w2 .wb').length") == 2,
    )
    page.evaluate("() => document.querySelector('#w1 .wb').click()")
    run.settle_text(page, "#w1 .wn", "1")
    got = page.evaluate(
        "() => [document.querySelector('#w1 .wn').textContent, document.querySelector('#w2 .wn').textContent]"
    )
    run.check(f"{label}: each lazy view answers on its own", got == ["1", "0"], got)
    page.evaluate("() => document.getElementById('btn').click()")
    run.check(f"{label}: page view still updates", run.settle_cnt(page, "1"))
    s = page.evaluate(SNAP)
    run.check(
        f"{label}: extension nodes kept",
        s["lead"] == "ext-lead" and s["trail"] and s["extRuns"] == 1,
        s,
    )
    run.check(f"{label}: no client errors", not errors, errors[:3])
    ctx.close()


def section_split(run, page_name="split"):
    """A root declared INSIDE <body> wins over a root on <body> (the layout
    djust.T005 warns about): such a page keeps behaving exactly as it did
    before #3302, so these checks pass on the old build too."""
    label = f"{page_name}/websocket"
    ctx, page, errors, _watch = run.open("websocket", f"/{page_name}/")
    run.wait_live(page, "websocket")
    s = page.evaluate(SNAP)
    run.check(
        f"{label}: every sibling is on the page", s["ids"] == ["hd", "mn", "ft", "cnt", "btn"], s
    )
    page.evaluate("() => { const b = document.getElementById('btn'); if (b) b.click(); }")
    run.check(f"{label}: update lands", run.settle_cnt(page, "1"))
    run.check(f"{label}: no client errors", not errors, errors[:3])
    ctx.close()


def main() -> int:
    failures = []
    only = set(filter(None, os.environ.get("BODY_ROOT_ONLY", "").split(",")))

    def wanted(name):
        return not only or name in only

    def guarded(section, *args):
        try:
            section(*args)
        except Exception as exc:  # a crashed section is a failure, not the end of the run
            failures.append(f"{section.__name__}{args[1:]} crashed: {str(exc)[:120]}")
            print("FAIL " + failures[-1])

    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        server = start_server(Path(tmp), port)
        base = f"http://localhost:{port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                run = Run(browser, base, failures)
                if wanted("basic"):
                    for transport in ("websocket", "sse", "http"):
                        for page_name in ("body", "bodyroot", "ext", "wrapped"):
                            guarded(section_basic, run, transport, page_name)
                    guarded(section_basic, run, "websocket", "stream")
                if wanted("wrapped"):
                    for transport in ("websocket", "sse", "http"):
                        guarded(section_wrapped, run, transport)
                if wanted("navigate"):
                    for transport in ("websocket", "sse"):
                        guarded(section_navigation, run, transport)
                if wanted("theme"):
                    guarded(section_theme, run)
                if wanted("bodyclick"):
                    guarded(section_body_click, run)
                if wanted("embed"):
                    for transport in ("websocket", "sse", "http"):
                        guarded(section_embed, run, transport)
                        guarded(section_embed, run, transport, "embedwrap")
                if wanted("lazy"):
                    guarded(section_lazy, run)
                    guarded(section_lazy, run, "lazywrap")
                if wanted("split"):
                    guarded(section_split, run)
                    guarded(section_split, run, "twin")
                browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nOK: <body> works as a root on every transport checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
