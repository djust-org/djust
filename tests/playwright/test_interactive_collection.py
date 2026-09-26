#!/usr/bin/env python3
"""ADR-034 C3: a keyed collection of interactive dropdowns in a real browser.

Drives ``/demos/interactive-collection/`` over WebSocket, SSE and HTTP-only
transports. The transport each run really used is checked. Rows are keyed
"a", "b", "c" (Alpha, Beta, Gamma). Row "c" is client-owned and observed.

- Rows open independently. A selection runs the collection's callback once,
  with that row's key.
- Reordering keeps each row's state with its row, not its position.
- A row's "Remove" (the collection's own callback syncs it away) removes
  only that row. A hand-crafted event for the removed row's old identity is
  refused and changes nothing.
- "Restore" re-adds the row with a new identity; the old one stays refused.
- The client row's toggle is reported with its key.

Standalone, like the other scripts here: exits 0 on success and non-zero with
the list of failures. ``COLLECTION_BASE`` (default ``http://localhost:18438``)
names the demo server.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("COLLECTION_BASE", "http://localhost:18438")
PATH = "/demos/interactive-collection/"
STAGE: dict = {}


async def run(browser, transport, failures):
    label = transport
    context = await browser.new_context()
    await context.add_init_script(INIT[transport])
    page = await context.new_page()
    watch = TransportWatch(page)

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

    async def rows():
        """[(label, component id, open)] in page order."""
        return await page.evaluate(
            """() => [...document.querySelectorAll('#ic-rows .dj-dropdown-menu')].map(m => [
                m.querySelector('.dj-dropdown-menu__trigger').textContent.trim(),
                m.getAttribute('data-component-id'),
                m.classList.contains('dj-dropdown-menu--open')])"""
        )

    def row(name):
        return f"#ic-rows .dj-dropdown-menu:has(> .dj-dropdown-menu__trigger:text-is('{name}'))"

    async def forge(component_id, value, ref):
        await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")
        if transport == "http":
            return await page.evaluate(
                """async ([id, value]) => (await fetch(location.href, {method: 'POST',
                    headers: {'Content-Type': 'application/json',
                              'X-CSRFToken': window.djust.csrfToken(),
                              'X-Djust-Event': 'select'},
                    body: JSON.stringify({component_id: id, value})})).status""",
                [component_id, value],
            )
        await page.evaluate(
            """([id, value, ref]) => window.djust.liveViewInstance.sendMessage({type: 'event',
                event: 'select', ref, params: {component_id: id, value}})""",
            [component_id, value, ref],
        )
        await page.wait_for_timeout(600)
        # A refused event opens the DEBUG error overlay over the page.
        await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")
        return None

    await page.goto(BASE + PATH, timeout=60000)
    if transport == "http":
        await page.wait_for_function("() => !!(window.djust && window.djust.handleEvent)")
    else:
        await page.wait_for_function(
            "() => !!(window.djust && window.djust.liveViewInstance"
            " && window.djust.liveViewInstance.viewMounted)",
            timeout=10000,
        )

    # 1. Rows are independent; a selection reaches the callback with its key.
    STAGE[transport] = 1
    await page.click(f"{row('Alpha')} .dj-dropdown-menu__trigger")
    await page.wait_for_selector(f"{row('Alpha')}.dj-dropdown-menu--open", timeout=6000)
    await page.click(f"{row('Beta')} .dj-dropdown-menu__trigger")
    await page.wait_for_selector(f"{row('Beta')}.dj-dropdown-menu--open", timeout=6000)
    await page.click(f"{row('Alpha')} .dj-dropdown-menu__item[dj-value-value='details']")
    await expect("#ic-result", "a:details")
    await expect("#ic-calls", "1")
    state = {name: opened for name, _id, opened in await rows()}
    if state.get("Alpha") or not state.get("Beta"):
        failures.append(f"{label}: after selecting in Alpha, rows are {state!r}")

    # 2. Reordering keeps state with its row.
    STAGE[transport] = 2
    before = {name: cid for name, cid, _ in await rows()}
    await page.click("#ic-reverse")
    await page.wait_for_function(
        "() => document.querySelector('#ic-rows .dj-dropdown-menu__trigger').textContent.trim() === 'Gamma'",
        timeout=6000,
    )
    after = await rows()
    if [name for name, *_ in after] != ["Gamma", "Beta", "Alpha"]:
        failures.append(f"{label}: reversed order is {after!r}")
    if {name: cid for name, cid, _ in after} != before:
        failures.append(f"{label}: reordering changed identities {after!r}")
    if {name: opened for name, _c, opened in after} != {
        "Gamma": False,
        "Beta": True,
        "Alpha": False,
    }:
        failures.append(f"{label}: reordering moved state {after!r}")

    # 3. Remove Beta from its own callback; its old identity is refused.
    STAGE[transport] = 3
    beta_id = before["Beta"]
    await page.click(f"{row('Beta')} .dj-dropdown-menu__item[dj-value-value='remove']")
    await expect("#ic-result", "b:remove")
    await expect("#ic-calls", "2")
    names = [name for name, *_ in await rows()]
    if names != ["Gamma", "Alpha"]:
        failures.append(f"{label}: after removing Beta, rows are {names!r}")
    status = await forge(beta_id, "details", 9001)
    if transport == "http" and (status is None or status < 400):
        failures.append(f"{label}: a removed row's forged event answered {status}")
    await expect("#ic-result", "b:remove")
    await expect("#ic-calls", "2")

    # 4. Restore re-adds Beta with a new identity; the old one stays refused.
    STAGE[transport] = 4
    await page.click("#ic-restore")
    await page.wait_for_selector(row("Beta"), timeout=6000)
    restored = {name: cid for name, cid, _ in await rows()}
    if restored.get("Beta") in (None, beta_id):
        failures.append(f"{label}: restored Beta identity {restored.get('Beta')!r}")
    if restored.get("Alpha") != before["Alpha"]:
        failures.append(f"{label}: restoring changed Alpha's identity")
    await forge(beta_id, "details", 9002)
    await page.click(f"{row('Beta')} .dj-dropdown-menu__trigger")
    await page.click(f"{row('Beta')} .dj-dropdown-menu__item[dj-value-value='details']")
    await expect("#ic-result", "b:details")
    await expect("#ic-calls", "3")

    # 5. The client row reports its toggle with its key.
    STAGE[transport] = 5
    await page.click(f"{row('Gamma')} .dj-dropdown-menu__trigger")
    await expect("#ic-observed", "c:open")
    await page.keyboard.press("Escape")
    await expect("#ic-observed", "c:closed")

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
        print("❌ ADR-034 interactive collection browser matrix failed:")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("✅ ADR-034 interactive collection browser matrix passed (websocket, sse, http)")


if __name__ == "__main__":
    asyncio.run(main())
