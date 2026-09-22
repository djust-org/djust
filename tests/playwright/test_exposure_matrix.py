#!/usr/bin/env python3
"""ADR-038 E5: explicit exposure in a real browser, over WebSocket and SSE.

Drives ``/demos/exposure/`` (explicit policy) and its legacy twin
``/demos/exposure-legacy/`` on the demo server through mount, events,
background work, a failing handler under DEBUG, ``url_change``, a socket
reconnect, cross-worker restore (a second server process on the same database)
and live back-navigation. After each flow it asserts at every destination the
browser and the server expose:

- the page HTML;
- every frame the client received (WebSocket frames, or SSE events captured
  by an init script that listens alongside djust's own listeners);
- the stored Django session rows, decoded;
- the server log, when ``E5_SERVER_LOG`` points at it.

The explicit view must leak none of ``E5_UNDECLARED_SENTINEL`` (an ordinary
attribute), ``E5_PRIVATE_SENTINEL`` (an underscore attribute) and
``E5_ERROR_SENTINEL`` (a DEBUG handler failure). The legacy twin is the
harness control: under DEBUG its error frame carries ``E5_ERROR_SENTINEL``,
proving the harness sees what it looks for.

Standalone, like the other scripts here: exits 0 on success and non-zero with
the list of failures. Environment:

- ``E5_BASE`` (default ``http://localhost:8002``): the demo server.
- ``E5_SECOND`` (default ``http://localhost:8003``): a second process on the
  same database and SECRET_KEY, for cross-worker restore. Reported as not run
  when unreachable.
- ``E5_SERVER_LOG``: the demo server's log file, checked for sentinels.
- ``E5_DEMO_DIR`` (default ``examples/demo_project``): used to decode the
  session store through Django.
"""

import asyncio
import json
import os
import sys
import urllib.request

from playwright.async_api import async_playwright

BASE = os.environ.get("E5_BASE", "http://localhost:8002")
SECOND = os.environ.get("E5_SECOND", "http://localhost:8003")
SERVER_LOG = os.environ.get("E5_SERVER_LOG")
DEMO_DIR = os.environ.get("E5_DEMO_DIR", "examples/demo_project")

SENTINELS = ("E5_UNDECLARED_SENTINEL", "E5_PRIVATE_SENTINEL", "E5_ERROR_SENTINEL")

# Records every SSE event djust listens to, without replacing its listeners.
SSE_CAPTURE = """
(() => {
    window.__e5frames = [];
    const Original = window.EventSource;
    if (!Original) return;
    window.EventSource = function (url, options) {
        const source = new Original(url, options);
        const add = source.addEventListener.bind(source);
        source.addEventListener = function (type, listener, opts) {
            add(type, (event) => window.__e5frames.push(String(event.data)), opts);
            return add(type, listener, opts);
        };
        Object.defineProperty(source, 'onmessage', {
            set(fn) { add('message', (event) => { window.__e5frames.push(String(event.data)); fn(event); }); },
        });
        return source;
    };
    window.EventSource.prototype = Original.prototype;
})();
"""


def reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status < 500
    except Exception:
        return False


def session_dump(session_key):
    """This flow's stored session, decoded, as one JSON string (values included).

    Only the flow's own session: the legacy twin legitimately stores its public
    attributes, so reading every row would blame the explicit view for them.
    """
    sys.path.insert(0, os.path.abspath(DEMO_DIR))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    import django

    django.setup()
    from django.contrib.sessions.models import Session

    rows = Session.objects.filter(session_key=session_key)
    return json.dumps([row.get_decoded() for row in rows], default=str)


async def session_key_of(context):
    for cookie in await context.cookies():
        # Projects rename it (the demo uses djust_demo_sessionid).
        if cookie["name"].endswith("sessionid"):
            return cookie["value"]
    return None


# Mounts the view over a raw WebSocket to another worker. The browser sends
# the session cookie (cookies ignore the port), so this is a reconnect of the
# same session landing on a different process.
CROSS_WORKER_MOUNT = """
async ([wsUrl, view, path]) => new Promise((resolve) => {
    const socket = new WebSocket(wsUrl);
    const timer = setTimeout(() => { socket.close(); resolve({error: 'timeout'}); }, 8000);
    socket.onmessage = (event) => {
        const frame = JSON.parse(event.data);
        if (frame.type === 'connect') {
            socket.send(JSON.stringify({type: 'mount', view: view, params: {}, url: path}));
        } else if (frame.type === 'mount' || frame.type === 'error') {
            clearTimeout(timer); socket.close(); resolve(frame);
        }
    };
    socket.onerror = () => { clearTimeout(timer); resolve({error: 'socket error'}); };
})
"""


def log_tail(offset):
    if not SERVER_LOG or not os.path.exists(SERVER_LOG):
        return ""
    with open(SERVER_LOG, encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        return handle.read()


def log_offset():
    if not SERVER_LOG or not os.path.exists(SERVER_LOG):
        return 0
    return os.path.getsize(SERVER_LOG)


async def text(page, selector):
    return await page.evaluate(
        f"() => (document.querySelector({json.dumps(selector)}) || {{}}).textContent"
    )


async def wait_text(page, selector, expected, timeout=8000):
    await page.wait_for_function(
        """([selector, expected]) => {
            const el = document.querySelector(selector);
            return el && el.textContent.trim() === expected;
        }""",
        arg=[selector, expected],
        timeout=timeout,
    )


async def wait_mounted(page):
    await page.wait_for_function(
        "() => !!(window.djust && (window.djust.liveViewInstance || window.djust._mountReady))",
        timeout=10000,
    )


async def run_flow(browser, transport, policy, failures, notes):
    label = f"{policy}/{transport}"
    path = "/demos/exposure/" if policy == "explicit" else "/demos/exposure-legacy/"
    context = await browser.new_context()
    frames = []
    if transport == "sse":
        await context.add_init_script("window.DJUST_USE_WEBSOCKET = false;")
        await context.add_init_script(SSE_CAPTURE)
    page = await context.new_page()
    page.on(
        "websocket",
        lambda ws: ws.on(
            "framereceived",
            lambda payload: frames.append(payload if isinstance(payload, str) else str(payload)),
        ),
    )
    start = log_offset()

    async def received():
        if transport == "sse":
            return frames + (await page.evaluate("() => window.__e5frames || []"))
        return frames

    await page.goto(BASE + path, timeout=60000)
    await wait_mounted(page)
    await wait_text(page, "#matrix-count", "0")

    await page.click("#matrix-increment")
    await wait_text(page, "#matrix-count", "1")
    await page.click("#matrix-increment")
    await wait_text(page, "#matrix-count", "2")
    await page.click("#matrix-spawn")
    await wait_text(page, "#matrix-count", "12", timeout=10000)
    await page.click("#matrix-page2")
    await wait_text(page, "#matrix-page", "2")
    await page.click("#matrix-boom")
    await page.wait_for_timeout(800)
    # The DEBUG dev overlay opens on the error; dismiss it like a developer.
    await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")

    all_frames = "\n".join(await received())
    html = await page.content()
    if policy == "legacy":
        # Harness control: legacy DEBUG error detail reaches the client.
        if "E5_ERROR_SENTINEL" not in all_frames:
            failures.append(f"{label}: control failed, legacy DEBUG error frame lacks the sentinel")
    else:
        for sentinel in SENTINELS:
            if sentinel in html:
                failures.append(f"{label}: {sentinel} in page HTML")
            if sentinel in all_frames:
                failures.append(f"{label}: {sentinel} in received frames")

        if transport == "websocket":
            # Reconnect: declared server state survives, nothing else does.
            await page.evaluate("() => window.djust.liveViewInstance.ws.close()")
            await page.wait_for_timeout(1500)
            await wait_mounted(page)
            try:
                await wait_text(page, "#matrix-count", "12", timeout=10000)
            except Exception:
                failures.append(
                    f"{label}: reconnect did not restore count=12 "
                    f"(got {await text(page, '#matrix-count')!r})"
                )

            # Cross-worker: a WebSocket mount of the same session on a second
            # process (same database) restores declared state. A full page
            # load mounts fresh by design, so this exercises the reconnect path.
            if reachable(SECOND + path):
                ws_url = SECOND.replace("http", "ws", 1) + "/ws/live/"
                view = "djust_demos.views.exposure_demo.ExposureMatrixView"
                frame = await page.evaluate(CROSS_WORKER_MOUNT, [ws_url, view, path])
                if frame.get("type") == "mount" and ">12<" in (frame.get("html") or ""):
                    notes.append(f"{label}: cross-worker WebSocket restore on {SECOND} verified")
                else:
                    failures.append(
                        f"{label}: cross-worker restore failed: {json.dumps(frame)[:300]}"
                    )
                for sentinel in SENTINELS:
                    if sentinel in json.dumps(frame):
                        failures.append(f"{label}: {sentinel} in the cross-worker mount frame")
            else:
                notes.append(f"{label}: cross-worker NOT RUN ({SECOND} unreachable)")

            # Live back-navigation restores the client-persisted page field.
            await page.click("#matrix-away")
            await page.wait_for_timeout(1500)
            await page.go_back()
            await page.wait_for_timeout(1500)
            try:
                await wait_text(page, "#matrix-page", "2", timeout=8000)
                notes.append(f"{label}: back-navigation restored page=2")
            except Exception:
                failures.append(
                    f"{label}: back-navigation did not restore page=2 "
                    f"(got {await text(page, '#matrix-page')!r})"
                )

            all_frames = "\n".join(await received())
            for sentinel in SENTINELS:
                if sentinel in all_frames:
                    failures.append(f"{label}: {sentinel} in frames after reconnect/navigation")

        stored = await asyncio.to_thread(session_dump, await session_key_of(context))
        if stored == "[]":
            failures.append(f"{label}: no stored session found for this flow")
        for sentinel in SENTINELS:
            if sentinel in stored:
                failures.append(f"{label}: {sentinel} in the stored session")
        logged = log_tail(start)
        if SERVER_LOG:
            for sentinel in SENTINELS:
                if sentinel in logged:
                    failures.append(f"{label}: {sentinel} in the server log")
        else:
            notes.append(f"{label}: server log NOT CHECKED (E5_SERVER_LOG unset)")

    await context.close()


async def main():
    failures, notes = [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        for transport in ("websocket", "sse"):
            for policy in ("explicit", "legacy"):
                try:
                    await run_flow(browser, transport, policy, failures, notes)
                except Exception as exc:  # a stuck step is a failure, reported by name
                    failures.append(f"{policy}/{transport}: flow did not complete: {exc!r}")
        await browser.close()
    for note in notes:
        print("  -", note)
    if failures:
        print("❌ ADR-038 E5 matrix failures:")
        for failure in failures:
            print("  *", failure)
        return 1
    print("✅ ADR-038 E5 matrix passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
