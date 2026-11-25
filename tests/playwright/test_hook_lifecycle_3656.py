#!/usr/bin/env python3
"""Real-browser check of the dj-hook lifecycle contract (#3656).

Drives a real LiveView over WebSocket in headless Chromium and records every
hook callback in ``window.__log``:

* a keyed reorder keeps each hook instance and its ``this.el`` (no
  destroyed/mounted, one updated each);
* an element the server re-renders with another tag gets destroyed() on the old
  instance before mounted() on the new one;
* a hook nested in an inserted hook element gets mounted() only, parent first;
* a hook inside a ``dj-update="ignore"`` region keeps its instance and node;
* a hook whose beforeUpdate()/updated() throw does not stop the patch or the
  other hooks;
* a dropped socket gives every hook one disconnected(), and the reconnect one
  reconnected().

Self-contained: builds a one-page LiveView project in a temp directory, serves
it with uvicorn and drives it with headless Chromium::

    pip install playwright && playwright install chromium
    DJUST_SERVER_PYTHON=.venv/bin/python python tests/playwright/test_hook_lifecycle_3656.py

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

PROJECT = {
    "hookapp/__init__.py": "",
    "hookapp/settings.py": """
        from pathlib import Path
        BASE_DIR = Path(__file__).resolve().parent.parent
        SECRET_KEY = "x"
        DEBUG = True
        ALLOWED_HOSTS = ["*"]
        ROOT_URLCONF = "hookapp.urls"
        INSTALLED_APPS = [
            "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles",
            "channels", "djust",
        ]
        MIDDLEWARE = [
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
        ]
        SESSION_ENGINE = "django.contrib.sessions.backends.cache"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        STATIC_URL = "/static/"
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        LIVEVIEW_ALLOWED_MODULES = ["hookapp"]
        DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
        TEMPLATES = [{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []},
        }]
    """,
    "hookapp/urls.py": """
        from django.urls import path
        from . import views
        urlpatterns = [path("", views.Page.as_view())]
    """,
    "hookapp/views.py": '''
        from djust import LiveView, state
        from djust.decorators import event_handler

        BODY = """
        <div dj-root>
          <span id="count">{{ n }}</span>
          <button id="bump" dj-click="bump">bump</button>
          <button id="reverse" dj-click="reverse">reverse</button>
          <button id="swap" dj-click="swap">swap</button>
          <button id="nest" dj-click="nest">nest</button>
          <ul id="list">
            {% for i in items %}<li id="it-{{ i }}" dj-key="{{ i }}" dj-hook="Rec" data-label="it-{{ i }}">{{ i }}</li>{% endfor %}
          </ul>
          {% if section %}<section id="box" dj-hook="Rec" data-label="box-section"></section>
          {% else %}<div id="box" dj-hook="Rec" data-label="box-div"></div>{% endif %}
          {% if nested %}<div id="outer" dj-hook="Rec" data-label="outer"><span id="inner" dj-hook="Rec" data-label="inner"></span></div>{% endif %}
          <div id="z" dj-update="ignore"><canvas id="cv" dj-hook="Rec" data-label="cv"></canvas></div>
          <div id="boom" dj-hook="Boom" data-label="boom" title="{{ n }}"></div>
        </div>
        """

        HOOKS = """<script>
          window.__log = [];
          window.DjustHooks = Object.assign(window.DjustHooks || {}, {
            Rec: {
              mounted() { this.el.__inst = Math.random(); window.__log.push(this.el.dataset.label + '.mounted'); },
              beforeUpdate() { window.__log.push(this.el.dataset.label + '.beforeUpdate'); },
              updated() { window.__log.push(this.el.dataset.label + '.updated'); },
              destroyed() { window.__log.push(this.el.dataset.label + '.destroyed'); },
              disconnected() { window.__log.push(this.el.dataset.label + '.disconnected'); },
              reconnected() { window.__log.push(this.el.dataset.label + '.reconnected'); },
            },
            Boom: {
              beforeUpdate() { throw new Error('boom-before'); },
              updated() { throw new Error('boom-updated'); },
            },
          });
        </script>"""

        TEMPLATE = (
            "{% load live_tags %}<!DOCTYPE html><html><head><title>t</title>{% djust_client_config %}"
            + HOOKS + "</head><body>" + BODY + "</body></html>"
        )

        class Page(LiveView):
            template = TEMPLATE
            n = state(0)
            items = state(default_factory=lambda: [1, 2, 3])
            section = state(False)
            nested = state(False)

            @event_handler()
            def bump(self, **kwargs):
                self.n += 1

            @event_handler()
            def reverse(self, **kwargs):
                self.items = list(reversed(self.items))

            @event_handler()
            def swap(self, **kwargs):
                self.section = not self.section

            @event_handler()
            def nest(self, **kwargs):
                self.nested = True
    ''',
    "hookapp/asgi.py": """
        import os
        os.environ["DJANGO_SETTINGS_MODULE"] = "hookapp.settings"
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
            "hookapp.asgi:application",
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
        except Exception:  # noqa: BLE001 -- the server is still starting; retried below
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
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                page = browser.new_page()
                errors = []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))

                def check(ok, what):
                    if not ok:
                        failures.append(what)
                    print(("ok   " if ok else "FAIL ") + what)

                def mounted():
                    return page.evaluate(
                        "() => window.djust && window.djust.liveViewInstance && "
                        "window.djust.liveViewInstance.viewMounted === true"
                    )

                page.goto(f"http://localhost:{port}/")
                page.wait_for_function(
                    "() => window.djust && window.djust.liveViewInstance && "
                    "window.djust.liveViewInstance.viewMounted === true",
                    timeout=15000,
                )
                page.wait_for_timeout(400)

                def act(button, expect_js):
                    page.evaluate("() => { window.__log.length = 0; }")
                    page.click(f"#{button}")
                    page.wait_for_function(expect_js, timeout=6000)
                    page.wait_for_timeout(300)
                    return page.evaluate("() => window.__log.slice()")

                def marks(selector):
                    return page.evaluate(
                        f"() => Array.from(document.querySelectorAll('{selector}')).map(e => e.__inst || null)"
                    )

                initial = page.evaluate("() => window.__log.slice()")
                # The socket's mount render that follows is an update: the hooks
                # get beforeUpdate()/updated(), and are not mounted again.
                check(
                    sorted(x for x in initial if x.endswith((".mounted", ".destroyed")))
                    == sorted(
                        [
                            "it-1.mounted",
                            "it-2.mounted",
                            "it-3.mounted",
                            "box-div.mounted",
                            "cv.mounted",
                        ]
                    ),
                    f"page load: each hook mounted once, none destroyed ({initial})",
                )

                # keyed reorder
                before = dict(zip(["1", "2", "3"], marks("#list li")))
                log = act("reverse", "() => document.querySelector('#list li').id === 'it-3'")
                after = dict(zip(["3", "2", "1"], marks("#list li")))
                check(before == after, "keyed reorder: every item keeps its hook instance and node")
                check(
                    not [
                        x
                        for x in log
                        if x.startswith("it-")
                        and (x.endswith("destroyed") or x.endswith("mounted"))
                    ],
                    f"keyed reorder: no destroyed()/mounted() ({log})",
                )
                check(
                    all(log.count(f"it-{i}.updated") == 1 for i in (1, 2, 3)),
                    "keyed reorder: one updated() per item",
                )

                # replacement by another tag
                log = act("swap", "() => document.getElementById('box').tagName === 'SECTION'")
                check(
                    "box-div.destroyed" in log
                    and "box-section.mounted" in log
                    and log.index("box-div.destroyed") < log.index("box-section.mounted"),
                    f"replacement: destroyed() on the old node before mounted() on the new ({log})",
                )

                # nested hooks inserted together
                log = act("nest", "() => !!document.getElementById('inner')")
                nested = [x for x in log if x.startswith(("outer.", "inner."))]
                check(
                    nested == ["outer.mounted", "inner.mounted"],
                    f"nested insert: mounted() parent first, and no updated() in the same update ({nested})",
                )

                # ignored region + throwing hook
                cv_before = marks("#cv")
                errors.clear()
                log = act("bump", "() => document.getElementById('count').textContent === '1'")
                check(
                    marks("#cv") == cv_before,
                    "dj-update=ignore: the hook keeps its instance and node",
                )
                check(
                    page.evaluate("() => document.getElementById('boom').title") == "1",
                    "throwing hook: the patch still landed on its own element",
                )
                check(
                    "cv.updated" in log and "it-1.updated" in log,
                    f"throwing hook: the other hooks still got updated() ({log})",
                )
                check(
                    any("Boom.beforeUpdate()" in e for e in errors)
                    and any("Boom.updated()" in e for e in errors),
                    "throwing hook: both errors were logged",
                )

                # drop the socket; the client reconnects by itself
                page.evaluate(
                    "() => { window.__log.length = 0; window.djust.liveViewInstance.ws.close(); }"
                )
                page.wait_for_function(
                    "() => window.__log.some(x => x.endsWith('.reconnected'))",
                    timeout=15000,
                )
                page.wait_for_timeout(1200)
                check(mounted(), "reconnect: the view is mounted again")
                log = page.evaluate("() => window.__log.slice()")
                labels = ["it-1", "it-2", "it-3", "box-section", "outer", "inner", "cv"]
                check(
                    all(
                        log.count(f"{x}.disconnected") == 1 and log.count(f"{x}.reconnected") == 1
                        for x in labels
                    ),
                    f"reconnect: one disconnected() and one reconnected() per hook ({log})",
                )
                check(
                    all(
                        log.index(f"{x}.disconnected") < log.index(f"{x}.reconnected")
                        for x in labels
                    ),
                    "reconnect: disconnected() before reconnected()",
                )
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
