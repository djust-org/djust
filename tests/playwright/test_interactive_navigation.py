#!/usr/bin/env python3
"""ADR-034 C4: a fixed interactive dropdown survives Back navigation.

Drives ``/demos/interactive-nav/`` over WebSocket and SSE (signed navigation
snapshots need a live transport; HTTP-only pages reload). The transport each
run really used is checked.

Each transport runs two paths:

- **Session path.** Select "edit", open the menu again, leave with
  ``dj-navigate`` and press Back. The server's saved session state wins: the
  same identity, still open, the selection kept, and the view state back.
- **Signed path.** The same flow, but the page's server-saved state is dropped
  before Back, as when it is gone (another worker, expiry), so the restore must
  use the server-signed snapshot.
  - A legacy view's signed snapshot is the one issued with its mount frame;
    later events do not refresh it (only explicit-policy views get refreshed
    tokens). So the restore brings back the mount-time state: closed and
    "none".
  - It keeps the menu's identity. A fresh mount would issue a new one, so the
    same identity is what proves the signed binding restore ran.

In both paths no output fires during the restore, and the next selection
still reaches the view's callback. The Service Worker's snapshot cache is
replaced by an in-page store with the same bridge methods
(``captureState``/``lookupState``/``forgetState``); only that storage is
simulated.

Standalone: exits 0 on success. ``NAVIGATION_BASE`` (default
``http://localhost:18438``) names the demo server.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("NAVIGATION_BASE", "http://localhost:18438")
PATH = "/demos/interactive-nav/"
STAGE: dict = {}
SW_BRIDGE = """() => {
    const store = {};
    window.__snapshotCaptures = 0;
    window.djust._sw = Object.assign({}, window.djust._sw || {}, {
        captureState(url, slug, json) {
            window.__snapshotCaptures += 1;
            store[url] = {view_slug: slug, state_json: json, ts: Date.now()};
        },
        forgetState(url) { delete store[url]; },
        lookupState(url) {
            const hit = store[url];
            return Promise.resolve(hit ? Object.assign({hit: true}, hit) : {hit: false});
        },
    });
}"""


async def run(browser, transport, path, failures):
    label = f"{transport}/{path}"
    context = await browser.new_context()
    await context.add_init_script(INIT[transport])
    page = await context.new_page()
    watch = TransportWatch(page)
    sent = []
    page.on(
        "websocket",
        lambda ws: ws.on("framesent", lambda p: sent.append(p) if isinstance(p, str) else None),
    )
    page.on("request", lambda r: sent.append(r.post_data or "") if r.method == "POST" else None)

    async def text(selector):
        return ((await page.text_content(selector)) or "").strip()

    async def expect(selector, value, timeout=8000):
        try:
            await page.wait_for_function(
                "([s, v]) => document.querySelector(s)?.textContent.trim() === v",
                arg=[selector, value],
                timeout=timeout,
            )
        except Exception:
            failures.append(f"{label}: {selector} is {await text(selector)!r}, expected {value!r}")

    async def mounted():
        await page.wait_for_function(
            "() => !!(window.djust && window.djust.liveViewInstance"
            " && window.djust.liveViewInstance.viewMounted)",
            timeout=10000,
        )

    async def menu_state():
        return await page.evaluate(
            """() => { const m = document.querySelector('#in-menu .dj-dropdown-menu');
                return m && [m.getAttribute('data-component-id'),
                             m.classList.contains('dj-dropdown-menu--open')]; }"""
        )

    await page.goto(BASE + PATH, timeout=60000)
    await mounted()
    await page.evaluate(SW_BRIDGE)

    STAGE[transport] = 1
    await page.click("#in-menu .dj-dropdown-menu__trigger")
    await page.click("#in-menu .dj-dropdown-menu__item[dj-value-value='edit']")
    await expect("#in-result", "edit")
    await page.click("#in-menu .dj-dropdown-menu__trigger")
    await page.wait_for_selector("#in-menu .dj-dropdown-menu--open", timeout=6000)
    identity, opened = await menu_state()
    selects_before = sum('"select"' in item for item in sent)

    STAGE[transport] = 2
    await page.click("#in-away")
    await page.wait_for_function("() => location.pathname === '/demos/nav-a/'", timeout=10000)
    await mounted()
    captures = await page.evaluate("() => window.__snapshotCaptures")
    if not captures:
        failures.append(f"{label}: leaving the page captured no signed snapshot")
    if path == "signed":
        # Drop the server-saved state so the restore must use the signed snapshot.
        removed = await page.evaluate(
            "async () => (await (await fetch('/demos/interactive-nav/forget/')).json()).removed"
        )
        if not removed:
            failures.append(f"{label}: no server-saved state to drop")
    await page.go_back()
    await page.wait_for_function(f"() => location.pathname === '{PATH}'", timeout=10000)
    await page.wait_for_selector("#in-menu .dj-dropdown-menu", timeout=10000)
    await mounted()
    restore_sent = [
        item for item in sent if "live_redirect_mount" in item and "state_snapshot" in item
    ]
    if not restore_sent:
        failures.append(f"{label}: Back sent no live_redirect_mount with a state_snapshot")
    expected_result, expected_open = ("edit", True) if path == "session" else ("none", False)
    await expect("#in-result", expected_result)
    state = await menu_state()
    if state != [identity, expected_open]:
        failures.append(
            f"{label}: after Back the menu is {state!r}, expected {[identity, expected_open]!r}"
        )
    if sum('"select"' in item for item in sent) != selects_before:
        failures.append(f"{label}: the restore sent a selection")

    STAGE[transport] = 3
    if not expected_open:
        await page.click("#in-menu .dj-dropdown-menu__trigger")
    await page.click("#in-menu .dj-dropdown-menu__item[dj-value-value='archive']")
    await expect("#in-result", "archive")

    watch.check(transport, label, failures)
    await context.close()


async def main():
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for transport in ("websocket", "sse"):
            for path in ("session", "signed"):
                try:
                    await run(browser, transport, path, failures)
                except Exception as exc:  # noqa: BLE001 - report every transport
                    failures.append(
                        f"{transport}/{path}: flow aborted in step {STAGE.get(transport)}: "
                        f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                    )
        await browser.close()
    if failures:
        print("❌ ADR-034 interactive navigation browser check failed:")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print(
        "✅ ADR-034 interactive navigation browser check passed (websocket, sse; session and signed)"
    )


if __name__ == "__main__":
    asyncio.run(main())
