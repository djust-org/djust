#!/usr/bin/env python3
"""ADR-035 F2: the managed edit object in a real browser.

Drives ``/demos/model-form/<pk>/`` on the demo server over WebSocket, SSE
and HTTP-only transports. ``/demos/model-form/`` resets three products first.

- The edit page renders the object and its form without a custom ``mount()``.
- An invalid price shows its field error; nothing is saved.
- A valid submit shows the save message, and a fresh page load shows the
  saved values: exactly what was submitted, saved once.
- Leaving the page and coming back with the browser's Back button leaves a
  working form: the next submit is saved too.
- A filtered-out (inactive) product and a forbidden (restricted) one answer
  the same 403 body, and neither page renders a form.
- A hand-crafted event cannot retarget the edit: ``id``/``pk`` in the payload
  do not change which product is saved.

Standalone, like the other scripts here: exits 0 on success and non-zero
with the list of failures. ``MODEL_FORM_BASE`` (default
``http://localhost:18437``) names the demo server.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

STAGE: dict = {}
BASE = os.environ.get("MODEL_FORM_BASE", "http://localhost:18437")


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

    async def ready():
        if transport == "http":
            await page.wait_for_function("() => !!(window.djust && window.djust.handleEvent)")
        else:
            await page.wait_for_function(
                "() => !!(window.djust && window.djust.liveViewInstance"
                " && window.djust.liveViewInstance.viewMounted)",
                timeout=10000,
            )

    async def fill(selector, value):
        await page.fill(selector, value)
        await page.dispatch_event(selector, "change")

    await page.goto(BASE + "/demos/model-form/", timeout=60000)
    ids = {
        key: (await page.get_attribute(f"#mf-{key}", "href")).rstrip("/").rsplit("/", 1)[-1]
        for key in ("editable", "inactive", "restricted")
    }
    edit_url = f"{BASE}/demos/model-form/{ids['editable']}/"

    STAGE[transport] = 1
    # 1. The page renders the object and its form.
    await page.goto(edit_url)
    await ready()
    await expect("#mf-title", "ADR-035 editable")
    if await page.input_value("#mf-price") != "10.00":
        failures.append(f"{label}: initial price {await page.input_value('#mf-price')!r}")

    STAGE[transport] = 2
    # 2. Invalid input: a field error, nothing saved.
    await fill("#mf-price", "abc")
    await page.click("#mf-save")
    await expect("#mf-error-price", "Enter a number.")
    await expect("#mf-saved", "")

    STAGE[transport] = 3
    # 3. Valid input: saved once, visible after a fresh load.
    name = f"ADR-035 editable {transport}"
    await fill("#mf-name", name)
    await fill("#mf-price", "12.50")
    await page.click("#mf-save")
    await expect("#mf-saved", f"Saved {name}")
    await expect("#mf-error-price", "")
    await page.goto(edit_url)
    await ready()
    await expect("#mf-title", name)
    if await page.input_value("#mf-price") != "12.50":
        failures.append(f"{label}: saved price {await page.input_value('#mf-price')!r}")

    STAGE[transport] = 4
    # 4. Back navigation leaves a working form. (The index resets the demo
    # data, so leave for another page.)
    await page.goto(BASE + "/demos/counter/")
    await page.go_back()
    await ready()
    back_name = f"{name} back"
    await fill("#mf-name", back_name)
    await page.click("#mf-save")
    await expect("#mf-saved", f"Saved {back_name}")

    STAGE[transport] = 5
    # 5. A forged identity in the payload does not retarget the edit.
    forged = f"{name} forged"
    if transport == "http":
        await page.evaluate(
            """async ([n, target]) => (await fetch(location.href, {method: 'POST',
                headers: {'Content-Type': 'application/json',
                          'X-CSRFToken': window.djust.csrfToken(),
                          'X-Djust-Event': 'submit_form'},
                body: JSON.stringify({name: n, price: '12.50', stock: '1',
                                      id: target, pk: target})})).status""",
            [forged, ids["restricted"]],
        )
    else:
        await page.evaluate(
            """([n, target]) => window.djust.liveViewInstance.sendMessage({type: 'event',
                event: 'submit_form', ref: 9999,
                params: {name: n, price: '12.50', stock: '1', id: target, pk: target}})""",
            [forged, ids["restricted"]],
        )
        await page.wait_for_timeout(800)
    await page.goto(edit_url)
    await ready()
    await expect("#mf-title", forged)

    STAGE[transport] = 6
    # 6. Filtered-out and forbidden answer the same denial, with no form.
    bodies = []
    for key in ("inactive", "restricted"):
        response = await page.goto(f"{BASE}/demos/model-form/{ids[key]}/")
        bodies.append((response.status, (await page.content()).count("mf-form")))
        if "ADR-035 restricted" in await page.content():
            failures.append(f"{label}: the denied {key} page rendered the product")
    if bodies != [(403, 0), (403, 0)]:
        failures.append(f"{label}: denial pages {bodies!r}")

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
        print("❌ ADR-035 model form browser matrix failed:")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("✅ ADR-035 model form browser matrix passed (websocket, sse, http)")


if __name__ == "__main__":
    asyncio.run(main())
