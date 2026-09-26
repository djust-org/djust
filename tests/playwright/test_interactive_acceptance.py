#!/usr/bin/env python3
"""ADR-034 C4: interactive dropdown acceptance cases in a real browser.

Drives ``/demos/interactive-acceptance/`` over WebSocket, SSE and HTTP-only
transports (the transport each run really used is checked), and checks:

- **Async callback.** An async output callback is awaited before the render.
- **Failing callback.** A callback that raises reports a ``djust:error`` on every
  transport. On a socket, the component's own change (closed, selection
  applied) stays and the callback ran exactly once. Over HTTP, a failed turn
  saves nothing, so the next request sees the state from before it. Either
  way the menu keeps working.
- **The ``close`` action.** It closes an open menu and renders.
- **Observations.** A duplicate report (same lifetime and sequence) and a
  report from an old lifetime never reach the observer.
- **Keyboard.** A client-owned popover opens with Enter on its trigger, closes
  with Escape, and focus returns to the trigger.
- **Legacy.** A legacy plain ``DropdownMenu`` still works through its own view
  handlers.
- **Isolation (WebSocket run only).** A second browser context on the same page
  is not affected by the first one's menu or selection.

Standalone: exits 0 on success. ``ACCEPTANCE_BASE`` (default
``http://localhost:18438``) names the demo server.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("ACCEPTANCE_BASE", "http://localhost:18438")
PATH = "/demos/interactive-acceptance/"
STAGE: dict = {}
ERRORS = """
(() => {
    window.__iaErrors = [];
    window.addEventListener('djust:error', e => window.__iaErrors.push(e.detail));
})();
"""


async def open_page(browser, transport):
    context = await browser.new_context()
    await context.add_init_script(INIT[transport] + ERRORS)
    page = await context.new_page()
    watch = TransportWatch(page)
    await page.goto(BASE + PATH, timeout=60000)
    if transport == "http":
        await page.wait_for_function("() => !!(window.djust && window.djust.handleEvent)")
    else:
        await page.wait_for_function(
            "() => !!(window.djust && window.djust.liveViewInstance"
            " && window.djust.liveViewInstance.viewMounted)",
            timeout=10000,
        )
    return context, page, watch


async def run(browser, transport, failures):
    label = transport
    context, page, watch = await open_page(browser, transport)

    async def text(selector, target=None):
        return ((await (target or page).text_content(selector)) or "").strip()

    async def expect(selector, value, timeout=6000, target=None):
        target = target or page
        try:
            await target.wait_for_function(
                "([s, v]) => document.querySelector(s)?.textContent.trim() === v",
                arg=[selector, value],
                timeout=timeout,
            )
        except Exception:
            failures.append(
                f"{label}: {selector} is {await text(selector, target)!r}, expected {value!r}"
            )

    async def clear_overlay():
        await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")

    async def send(event, params, ref):
        await clear_overlay()
        if transport == "http":
            await page.evaluate(
                "([event, params]) => window.djust.handleEvent(event, params)", [event, params]
            )
        else:
            await page.evaluate(
                """([event, params, ref]) => window.djust.liveViewInstance.sendMessage(
                    {type: 'event', event, params, ref})""",
                [event, params, ref],
            )
        await page.wait_for_timeout(600)
        await clear_overlay()

    menu_id = await page.get_attribute("#ia-menu [data-component-id]", "data-component-id")
    quiet_id = await page.get_attribute("#ia-quiet [data-component-id]", "data-component-id")

    # 1. Async callback.
    STAGE[transport] = 1
    await page.click("#ia-menu .dj-dropdown-menu__trigger")
    await page.click("#ia-menu .dj-dropdown-menu__item[dj-value-value='save']")
    await expect("#ia-result", "saved")
    await expect("#ia-calls", "1")

    # 2. A failing callback: error reported, no rollback, one call.
    STAGE[transport] = 2
    errors_before = await page.evaluate("() => window.__iaErrors.length")
    await page.click("#ia-menu .dj-dropdown-menu__trigger")
    await page.click("#ia-menu .dj-dropdown-menu__item[dj-value-value='fail']")
    await page.wait_for_timeout(800)
    await clear_overlay()
    if await page.evaluate("() => window.__iaErrors.length") <= errors_before:
        failures.append(f"{label}: a failing callback reported no djust:error")
    await page.click("#ia-refresh")
    if transport == "http":
        # A failed HTTP turn answers 500 and saves nothing: the next request
        # restores the state from before it (the callback's increment too).
        await expect("#ia-calls", "1")
        await expect("#ia-selected", "save")
    else:
        # On a socket the component's own change stays, and the callback
        # ran once.
        await expect("#ia-calls", "2")
        await expect("#ia-selected", "fail")
        if await page.is_visible("#ia-menu .dj-dropdown-menu__content"):
            failures.append(f"{label}: the failed selection rolled the menu back open")
    # Either way the menu keeps working.
    if not await page.is_visible("#ia-menu .dj-dropdown-menu__content"):
        await page.click("#ia-menu .dj-dropdown-menu__trigger")
    await page.click("#ia-menu .dj-dropdown-menu__item[dj-value-value='save']")
    await expect("#ia-result", "saved")

    # 3. The close action.
    STAGE[transport] = 3
    await page.click("#ia-menu .dj-dropdown-menu__trigger")
    await page.wait_for_selector("#ia-menu .dj-dropdown-menu--open", timeout=6000)
    await send("close", {"component_id": menu_id}, 9001)
    await page.wait_for_selector("#ia-menu .dj-dropdown-menu--open", state="detached", timeout=6000)

    # 4. Duplicate and old-lifetime observations never reach the observer.
    STAGE[transport] = 4
    await page.click("#ia-refresh")
    await page.wait_for_timeout(500)
    before = int(await text("#ia-observed"))
    await page.click("#ia-quiet .dj-dropdown-menu__trigger")
    await page.wait_for_timeout(700)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(700)
    lifetime = await page.get_attribute("#ia-quiet [popover]", "data-dj-observe-lifetime")
    await send(
        "observe_toggle",
        {"component_id": quiet_id, "open": True, "sequence": 1, "lifetime": lifetime},
        9002,
    )
    await send(
        "observe_toggle",
        {"component_id": quiet_id, "open": True, "sequence": 9, "lifetime": "obs_" + "0" * 32},
        9003,
    )
    await page.click("#ia-refresh")
    await expect("#ia-observed", str(before + 2))

    # 5. Keyboard and focus on a client-owned popover.
    STAGE[transport] = 5
    await page.focus("#ia-quiet .dj-dropdown-menu__trigger")
    await page.keyboard.press("Enter")
    await page.wait_for_function(
        "() => document.querySelector('#ia-quiet [popover]').matches(':popover-open')",
        timeout=4000,
    )
    await page.keyboard.press("Escape")
    focus_back = await page.evaluate(
        """() => !document.querySelector('#ia-quiet [popover]').matches(':popover-open')
            && document.activeElement === document.querySelector('#ia-quiet .dj-dropdown-menu__trigger')"""
    )
    if not focus_back:
        failures.append(
            f"{label}: Escape did not close the popover and return focus to its trigger"
        )

    # 6. A legacy plain DropdownMenu with its own view handlers.
    STAGE[transport] = 6
    await page.click("#ia-legacy .dj-dropdown-menu__trigger")
    await page.wait_for_selector("#ia-legacy .dj-dropdown-menu--open", timeout=6000)
    await page.click("#ia-legacy [dj-click='legacy_edit']")
    await expect("#ia-result", "legacy:edit")

    # 7. Isolation between two browsers (one run is enough).
    if transport == "websocket":
        STAGE[transport] = 7
        await page.click("#ia-refresh")
        await page.wait_for_timeout(500)
        mine = (await text("#ia-result"), await text("#ia-calls"))
        other_context, other, _ = await open_page(browser, transport)
        await other.click("#ia-menu .dj-dropdown-menu__trigger")
        await other.wait_for_selector("#ia-menu .dj-dropdown-menu--open", timeout=6000)
        await other.click("#ia-menu .dj-dropdown-menu__item[dj-value-value='save']")
        await expect("#ia-result", "saved", target=other)
        await expect("#ia-calls", "1", target=other)
        await page.click("#ia-refresh")
        await expect("#ia-result", mine[0])
        await expect("#ia-calls", mine[1])
        if await page.is_visible("#ia-menu .dj-dropdown-menu__content"):
            failures.append(f"{label}: the other browser opened this browser's menu")
        await other_context.close()

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
        print("❌ ADR-034 interactive acceptance browser matrix failed:")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("✅ ADR-034 interactive acceptance browser matrix passed (websocket, sse, http)")


if __name__ == "__main__":
    asyncio.run(main())
