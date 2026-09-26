#!/usr/bin/env python3
"""ADR-034 C2: interactive dropdowns in a real browser.

Drives ``/demos/interactive-dropdown/`` on the demo server over WebSocket,
SSE and HTTP-only transports, and checks what the browser sent, what the
server did and what the page shows:

- Two server-owned menus of the same type open, close and select
  independently. Each selection runs only its own callback, once, and the
  callback's change renders.
- A disabled item cannot be chosen in the page, and hand-crafted events with
  a disabled value, an unknown value, a stale component id, or a direct call
  to the output callback change nothing.
- Client-owned (native popover) menus:
  - A toggle reports the actual visibility after a click, Escape and an
    outside click.
  - The quiet observer changes nothing, so its reports change nothing in the
    page.
  - The live observer's change renders.
  - The menu without an observer sends nothing.
  - Choosing an item closes the popover at once.
  - Keyboard opening reports, and a server patch leaves an open popover open.
  - Over WebSocket, toggles while disconnected stay local, and the current
    value is reported once after the reconnect.

Standalone, like the other scripts here: exits 0 on success and non-zero
with the list of failures. ``DROPDOWN_BASE`` (default
``http://localhost:18438``) names the demo server.
"""

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("DROPDOWN_BASE", "http://localhost:18438")
PATH = "/demos/interactive-dropdown/"
MUTATIONS = """
(() => {
    window.__ddMutations = 0;
    window.__ddErrors = [];
    window.addEventListener('djust:error', e => window.__ddErrors.push(e.detail));
    const start = () => {
        const root = document.querySelector('[dj-root]');
        if (!root) return setTimeout(start, 20);
        new MutationObserver(records => { window.__ddMutations += records.length; })
            .observe(root, {subtree: true, childList: true, attributes: true, characterData: true});
    };
    document.addEventListener('DOMContentLoaded', start);
})();
"""
STAGE: dict = {}


def event_params(raw):
    """(event, params) from an outbound WS frame or SSE message."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("type") == "event":
        return data.get("event"), data.get("params") or {}
    return None


async def run(browser, transport, failures):
    label = transport
    context = await browser.new_context()
    await context.add_init_script(INIT[transport] + MUTATIONS)
    page = await context.new_page()
    watch = TransportWatch(page)
    sent = []

    page.on(
        "websocket",
        lambda ws: ws.on(
            "framesent",
            lambda payload: (
                sent.append(event_params(payload)) if isinstance(payload, str) else None
            ),
        ),
    )

    def on_request(request):
        if request.method != "POST" or not request.post_data:
            return
        header = request.headers.get("x-djust-event")
        if header:
            sent.append((header, json.loads(request.post_data)))
        else:
            sent.append(event_params(request.post_data))

    page.on("request", on_request)

    async def text(selector):
        return ((await page.text_content(selector)) or "").strip()

    async def expect(selector, value, timeout=6000):
        try:
            await page.wait_for_function(
                "([s, v]) => document.querySelector(s)?.textContent.trim() === v",
                arg=[selector, value],
                timeout=timeout,
            )
        except Exception:
            failures.append(f"{label}: {selector} is {await text(selector)!r}, expected {value!r}")

    async def is_open(menu):
        return await page.evaluate(
            "(s) => document.querySelector(s + ' .dj-dropdown-menu')"
            ".classList.contains('dj-dropdown-menu--open')",
            menu,
        )

    async def popover_open(menu):
        return await page.evaluate(
            "(s) => !!document.querySelector(s + ' [popover]')?.matches(':popover-open')", menu
        )

    def events(name):
        return [item for item in sent if item and item[0] == name]

    async def settle(ms=700):
        await page.wait_for_timeout(ms)

    await page.goto(BASE + PATH, timeout=60000)
    if transport == "http":
        await page.wait_for_function("() => !!(window.djust && window.djust.handleEvent)")
    else:
        await page.wait_for_function(
            "() => !!(window.djust && window.djust.liveViewInstance"
            " && window.djust.liveViewInstance.viewMounted)",
            timeout=10000,
        )
    ids = await page.evaluate(
        """() => Object.fromEntries(['project', 'account', 'quiet', 'live-menu', 'silent']
            .map(k => [k, document.querySelector('#dd-' + k + ' [data-component-id]')
                .getAttribute('data-component-id')]))"""
    )

    # 1. Two same-type server menus are independent.
    STAGE[transport] = 1
    await page.click("#dd-project .dj-dropdown-menu__trigger")
    await page.wait_for_selector("#dd-project .dj-dropdown-menu--open", timeout=6000)
    if await is_open("#dd-account"):
        failures.append(f"{label}: opening the project menu opened the account menu")
    await page.click("#dd-account .dj-dropdown-menu__trigger")
    await page.wait_for_selector("#dd-account .dj-dropdown-menu--open", timeout=6000)

    # 2. A selection runs only its own callback, once, and renders.
    STAGE[transport] = 2
    await page.click("#dd-project .dj-dropdown-menu__item[dj-value-value='archive']")
    await expect("#dd-result", "project:archive")
    await expect("#dd-calls", "1")
    if await is_open("#dd-project") or not await is_open("#dd-account"):
        failures.append(f"{label}: selection closed the wrong menu")
    await page.click("#dd-account .dj-dropdown-menu__item[dj-value-value='settings']")
    await expect("#dd-result", "account:settings")
    await expect("#dd-calls", "2")

    # 3. Disabled, forged, unknown and stale selections change nothing.
    STAGE[transport] = 3
    await page.click("#dd-project .dj-dropdown-menu__trigger")
    await page.wait_for_selector("#dd-project .dj-dropdown-menu--open", timeout=6000)
    if not await page.is_disabled("#dd-project .dj-dropdown-menu__item[dj-value-value='locked']"):
        failures.append(f"{label}: the disabled item is clickable")
    forged = [
        ("select", {"component_id": ids["project"], "value": "locked"}),
        ("select", {"component_id": ids["project"], "value": "missing"}),
        ("select", {"component_id": "cmp_" + "0" * 32, "value": "edit"}),
        ("on_project_menu_selected", {"value": "edit"}),
    ]
    for ref, (name, params) in enumerate(forged, 9000):
        await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")
        if transport == "http":
            status = await page.evaluate(
                """async ([name, params]) => (await fetch(location.href, {method: 'POST',
                    headers: {'Content-Type': 'application/json',
                              'X-CSRFToken': window.djust.csrfToken(),
                              'X-Djust-Event': name},
                    body: JSON.stringify(params)})).status""",
                [name, params],
            )
            if status < 400:
                failures.append(f"{label}: forged {name} {params!r} answered {status}")
        else:
            await page.evaluate(
                """([name, params, ref]) => window.djust.liveViewInstance.sendMessage(
                    {type: 'event', event: name, params, ref})""",
                [name, params, ref],
            )
        await settle(500)
    await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")
    await expect("#dd-result", "account:settings")
    await expect("#dd-calls", "2")

    # 4. The quiet observer reports actual visibility and renders nothing.
    STAGE[transport] = 4
    await page.click("#dd-show-observed")
    await settle()
    before = int(await text("#dd-observed"))
    await page.evaluate("() => { window.__ddMutations = 0; }")
    sent_before = len(events("observe_toggle"))
    await page.click("#dd-quiet .dj-dropdown-menu__trigger")
    await settle()
    if not await popover_open("#dd-quiet"):
        failures.append(f"{label}: the quiet popover did not open")
    await page.keyboard.press("Escape")
    await settle()
    await page.click("#dd-quiet .dj-dropdown-menu__trigger")
    await settle()
    await page.mouse.click(5, 5)  # outside the centred popover
    await settle()
    reports = [params.get("open") for _, params in events("observe_toggle")[sent_before:]]
    if reports != [True, False, True, False]:
        failures.append(f"{label}: quiet menu reported {reports!r}")
    mutations = await page.evaluate("() => window.__ddMutations")
    if mutations:
        failures.append(f"{label}: an unchanged observer caused {mutations} DOM mutation(s)")
    await page.click("#dd-show-observed")
    await expect("#dd-observed", str(before + 4))

    # 5. The live observer's change renders; selection dismisses at once.
    STAGE[transport] = 5
    await page.click("#dd-live-menu .dj-dropdown-menu__trigger")
    await expect("#dd-live", "open")
    await page.click("#dd-live-menu .dj-dropdown-menu__item")
    if await popover_open("#dd-live-menu"):
        failures.append(f"{label}: choosing a client-menu item left the popover open")
    await expect("#dd-live", "closed")

    # 6. A client menu without an observer sends nothing.
    STAGE[transport] = 6
    silent_before = len([item for item in sent if item])
    await page.click("#dd-silent .dj-dropdown-menu__trigger")
    await settle()
    await page.keyboard.press("Escape")
    await settle()
    extra = [item for item in sent if item][silent_before:]
    if extra:
        failures.append(f"{label}: the unobserved client menu sent {extra!r}")

    # 7. Keyboard opening reports too, and an unrelated server patch leaves
    # an open popover open.
    STAGE[transport] = 7
    sent_before = len(events("observe_toggle"))
    await page.focus("#dd-live-menu .dj-dropdown-menu__trigger")
    await page.keyboard.press("Enter")
    await expect("#dd-live", "open")
    await page.evaluate("() => window.djust.handleEvent('show_observed', {})")
    await settle()
    if not await popover_open("#dd-live-menu"):
        failures.append(f"{label}: a server patch closed the open popover")
    await page.keyboard.press("Escape")
    await expect("#dd-live", "closed")
    reports = [params.get("open") for _, params in events("observe_toggle")[sent_before:]]
    if reports != [True, False]:
        failures.append(f"{label}: keyboard open/close reported {reports!r}")

    # 8. WebSocket only: toggles while disconnected stay local; after the
    # reconnect the current value is reported once.
    if transport == "websocket":
        STAGE[transport] = 8
        await page.click("#dd-live-menu .dj-dropdown-menu__trigger")
        await expect("#dd-live", "open")
        sent_before = len(events("observe_toggle"))
        await page.evaluate("() => window.djust.liveViewInstance.ws.close()")
        await page.wait_for_function(
            "() => !window.djust.liveViewInstance.viewMounted", timeout=5000
        )
        await page.keyboard.press("Escape")
        await page.click("#dd-live-menu .dj-dropdown-menu__trigger")
        await page.keyboard.press("Escape")
        await page.wait_for_function(
            "() => window.djust.liveViewInstance.viewMounted", timeout=15000
        )
        await expect("#dd-live", "closed", timeout=10000)
        # The reconnect mounts fresh bindings (new identities); every observed
        # menu reports its current value once. Nothing was sent while offline.
        current = await page.evaluate(
            """() => Object.fromEntries(['quiet', 'live-menu'].map(k => [k,
                document.querySelector('#dd-' + k + ' [data-component-id]')
                    .getAttribute('data-component-id')]))"""
        )
        after = events("observe_toggle")[sent_before:]
        by_menu = {
            key: [params.get("open") for _, params in after if params.get("component_id") == cid]
            for key, cid in current.items()
        }
        if by_menu != {"quiet": [False], "live-menu": [False]} or len(after) != 2:
            failures.append(f"{label}: reconnect reported {after!r}")

    errors = await page.evaluate("() => window.__ddErrors.length")
    if transport != "http" and not errors:
        failures.append(f"{label}: forged events raised no djust:error")

    watch.check(transport, label, failures)
    await context.close()


async def main():
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        for transport in ("websocket", "sse", "http"):
            try:
                await run(browser, transport, failures)
            except Exception as exc:  # noqa: BLE001 - report every transport
                failures.append(
                    f"{transport}: flow aborted in step {STAGE.get(transport)}: "
                    f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
                )
        await browser.close()
    if failures:
        print("❌ ADR-034 interactive dropdown browser matrix failed:")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("✅ ADR-034 interactive dropdown browser matrix passed (websocket, sse, http)")


if __name__ == "__main__":
    asyncio.run(main())
