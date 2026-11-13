#!/usr/bin/env python3
"""VoiceInput browser contract over a real LiveView/WebSocket.

SpeechRecognition is simulated; this never opens a microphone or contacts a
speech service. Chromium drives user clicks, final/interim callbacks, permission
errors, focus changes, teardown, unsupported browsers and custom hooks. This
verifies UI and transport behavior, not recognition accuracy or OS permissions.
Run with DJUST_SERVER_PYTHON set to an absolute worktree interpreter path.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request

from playwright.sync_api import sync_playwright

PROJECT = {
    "voiceapp/__init__.py": "",
    "voiceapp/settings.py": """
        SECRET_KEY = "voice-test-only"
        DEBUG = True
        ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
        ROOT_URLCONF = "voiceapp.urls"
        INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.staticfiles", "channels",
            "djust", "djust.components"]
        MIDDLEWARE = ["django.contrib.sessions.middleware.SessionMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware"]
        SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
        DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
        CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        STATIC_URL = "/static/"
        LIVEVIEW_ALLOWED_MODULES = ["voiceapp"]
        TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True, "OPTIONS": {"context_processors": []}}]
    """,
    "voiceapp/urls.py": """
        from django.urls import path
        from .views import Demo, Custom
        urlpatterns = [path("", Demo.as_view()), path("custom/", Custom.as_view())]
    """,
    "voiceapp/views.py": '''
        from djust import LiveView
        from djust.decorators import event_handler
        from djust.components.components.voice_input import clean_transcript

        PAGE = """{% load live_tags djust_components %}<!DOCTYPE html><html lang="en-US">
        <head><title>VoiceInput browser test</title>{% djust_client_config %}
        <link rel="stylesheet" href="/static/djust_components/components.css">
        <script src="/static/djust_components/voice-input.js" defer></script></head><body>
        <div dj-root><h1>Voice input</h1><span id="count">{{ count }}</span>
        <span id="text">{{ text }}</span>
        {% if show %}{% voice_input event="transcribe" continuous=True max_seconds=60 %}{% endif %}
        <input id="elsewhere" aria-label="Other field"><button id="toggle" dj-click="toggle">Toggle</button>
        </div></body></html>"""

        class Demo(LiveView):
            template = PAGE

            def mount(self, request, **kwargs):
                self.count = 0
                self.text = ""
                self.show = True

            @event_handler()
            def transcribe(self, text: str = "", **kwargs):
                self.text = clean_transcript(text, max_length=500)
                self.count += 1

            @event_handler()
            def toggle(self, **kwargs):
                self.show = not self.show

        class Custom(Demo):
            template = PAGE.replace("<head>", "<head><script>window.DjustHooks = {VoiceInput: "
                "{mounted: function() {window.__customVoice = true;}}};</script>")
    ''',
    "voiceapp/asgi.py": """
        import os
        os.environ["DJANGO_SETTINGS_MODULE"] = "voiceapp.settings"
        from django.core.asgi import get_asgi_application
        django_app = get_asgi_application()
        from channels.auth import AuthMiddlewareStack
        from channels.routing import ProtocolTypeRouter, URLRouter
        from channels.security.websocket import AllowedHostsOriginValidator
        from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
        from django.urls import path
        from djust.websocket import LiveViewConsumer
        application = ProtocolTypeRouter({
            "http": ASGIStaticFilesHandler(django_app),
            "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter([
                path("ws/live/", LiveViewConsumer.as_asgi())
            ]))),
        })
    """,
}

FAKE_RECOGNITION = """
window.__recognizers = [];
class BrowserSpeechProbe {
    constructor() { this.calls = []; window.__recognizers.push(this); }
    start() { this.calls.push('start'); }
    stop() { this.calls.push('stop'); if (this.onend) this.onend(); }
    abort() { this.calls.push('abort'); if (this.onerror) this.onerror({error:'aborted'}); if (this.onend) this.onend(); }
    result(text, final) {
        const result = [{transcript:text}]; result.isFinal = final;
        this.onresult({resultIndex:0, results:[result]});
    }
    error(code) { this.onerror({error:code}); if (this.onend) this.onend(); }
}
window.SpeechRecognition = BrowserSpeechProbe;
window.webkitSpeechRecognition = BrowserSpeechProbe;
"""


def start_server(root: Path, port: int) -> subprocess.Popen:
    for name, body in PROJECT.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
    source = str(Path(__file__).resolve().parents[2] / "python")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), source])}
    py = os.environ.get("DJUST_SERVER_PYTHON", sys.executable)
    with (root / "server.log").open("w") as log:
        proc = subprocess.Popen(
            [
                py,
                "-m",
                "uvicorn",
                "voiceapp.asgi:application",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return proc
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=10)
    raise RuntimeError((root / "server.log").read_text())


def load(page, url):
    page.goto(url)
    page.wait_for_function("() => window.djust?.liveViewInstance?.viewMounted === true")
    page.wait_for_function(
        "() => !!window.djust.getHook(document.querySelector('[dj-hook=VoiceInput]'))"
    )


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print("ok  " + description, flush=True)


def run(browser, base):
    context = browser.new_context()
    context.add_init_script(FAKE_RECOGNITION)
    page = context.new_page()
    load(page, base)
    mic = page.locator('[dj-hook="VoiceInput"]')
    check(page.evaluate("() => window.__recognizers.length") == 0, "mount never starts recognition")
    check(
        "Google or Microsoft" in page.locator(".dj-voice-input__disclosure").inner_text(),
        "vendor disclosure visible",
    )
    mic.click()
    check(mic.get_attribute("aria-pressed") == "true", "a user click starts listening")
    page.emulate_media(reduced_motion="reduce")
    check(
        page.locator(".dj-voice-input__pulse").evaluate("el => getComputedStyle(el).animationName")
        == "none",
        "reduced motion stops the pulse animation",
    )
    page.evaluate("() => window.__recognizers.at(-1).result('hel', false)")
    check(
        page.locator(".dj-voice-input__interim").inner_text() == "hel", "interim feedback visible"
    )
    check(page.locator("#count").inner_text() == "0", "interim transcript never sent")
    page.evaluate("() => window.__recognizers.at(-1).result('hello world', true)")
    page.wait_for_function("() => document.querySelector('#count').textContent === '1'")
    check(
        page.locator("#text").inner_text() == "hello world", "final transcript reaches real server"
    )
    check(mic.get_attribute("aria-pressed") == "true", "a server patch preserves listening state")
    shots = Path(os.environ.get("SHOTS_DIR") or tempfile.gettempdir())
    shots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(shots / "voice-input.png"), full_page=True)

    page.locator("#elsewhere").focus()
    page.wait_for_function(
        "() => document.querySelector('[dj-hook=VoiceInput]').getAttribute('aria-pressed') === 'false'"
    )
    check(
        page.evaluate("() => window.__recognizers.at(-1).calls.at(-1)") == "abort",
        "focus away aborts listening",
    )
    mic.click()
    page.evaluate("() => window.__recognizers.at(-1).error('not-allowed')")
    check(
        "denied" in page.locator(".dj-voice-input__status").inner_text(),
        "permission-denied feedback",
    )
    check(mic.get_attribute("aria-pressed") == "false", "permission error resets pressed state")
    mic.focus()
    page.keyboard.press("Space")
    check(mic.get_attribute("aria-pressed") == "true", "keyboard activation starts listening")
    page.evaluate(
        "() => window.__recognizers.at(-1).result('<img src=x onerror=window.__pwn=1>', true)"
    )
    page.wait_for_function("() => document.querySelector('#count').textContent === '2'")
    check(
        page.locator("#text img").count() == 0 and not page.evaluate("() => window.__pwn"),
        "transcript markup stays escaped text",
    )
    # Dispatch without moving focus, so only teardown can abort recognition.
    page.evaluate("() => document.querySelector('#toggle').click()")
    page.wait_for_function("() => !document.querySelector('[dj-hook=VoiceInput]')")
    check(
        page.evaluate("() => window.__recognizers.at(-1).calls.at(-1)") == "abort",
        "teardown aborts listening",
    )
    context.close()

    context = browser.new_context()
    context.add_init_script(
        "window.SpeechRecognition = undefined; window.webkitSpeechRecognition = undefined;"
    )
    page = context.new_page()
    load(page, base)
    check(
        page.locator('[dj-hook="VoiceInput"]').is_disabled(), "unsupported browser disables button"
    )
    check(
        "not supported" in page.locator(".dj-voice-input__status").inner_text(),
        "unsupported-browser feedback",
    )
    context.close()

    context = browser.new_context()
    context.add_init_script(FAKE_RECOGNITION)
    page = context.new_page()
    load(page, base + "custom/")
    check(page.evaluate("() => window.__customVoice === true"), "custom hook retained")
    page.locator('[dj-hook="VoiceInput"]').click()
    check(
        page.evaluate("() => window.__recognizers.length") == 0,
        "shipped hook does not run over custom hook",
    )
    context.close()


def main():
    with tempfile.TemporaryDirectory(prefix="djust-voice-browser-") as tmp:
        root = Path(tmp)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = start_server(root, port)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, executable_path=os.environ.get("CHROMIUM_EXECUTABLE") or None
                )
                try:
                    run(browser, f"http://127.0.0.1:{port}/")
                finally:
                    browser.close()
        finally:
            server.terminate()
            server.wait(timeout=10)
    print("all voice browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
