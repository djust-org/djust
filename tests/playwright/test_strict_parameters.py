#!/usr/bin/env python3
"""ADR-036 P2 (d): strict event parameters in a real browser.

Drives ``/demos/strict-parameters/`` on the demo server over WebSocket, SSE
and HTTP-only transports, and checks both what the browser sends and what
the view received:

- a strict ``dj-click`` sends only its ``dj-value-*`` argument, typed, and
  never the element's ``data-*`` attributes;
- a malformed typed literal is rejected in the browser: no request, no
  ``dj-disable-with`` effect, one value-free ``djust:error``;
- ``dj-input`` and ``dj-submit`` send only the generated values the strict
  handler declares (ADR-036 Q1), and never ``_target`` (Q2);
- a legacy handler on the same page still receives ``data-*`` and
  ``dj-value-*`` values;
- a hand-crafted message with a forged ``component`` key never reaches the
  handler.

Standalone, like the other scripts here: exits 0 on success and non-zero
with the list of failures. ``STRICT_BASE`` (default
``http://localhost:18436``) names the demo server.
"""

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("STRICT_BASE", "http://localhost:18436")
PATH = "/demos/strict-parameters/"
ERRORS = """
(() => {
    window.__strictErrors = [];
    window.addEventListener('djust:error', e => window.__strictErrors.push(e.detail));
})();
"""


def event_params(raw):
    """(event, params) from an outbound WS frame, SSE message or HTTP body."""
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
    await context.add_init_script(INIT[transport] + ERRORS)
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
        if header:  # HTTP fallback: flat params, event in a header
            sent.append((header, json.loads(request.post_data)))
        else:  # SSE message endpoint: a wire-shape event frame
            sent.append(event_params(request.post_data))

    page.on("request", on_request)

    async def text(selector):
        return (await page.text_content(selector)).strip()

    async def expect(selector, value, timeout=5000):
        try:
            await page.wait_for_function(
                "([s, v]) => document.querySelector(s)?.textContent.trim() === v",
                arg=[selector, value],
                timeout=timeout,
            )
        except Exception:
            failures.append(f"{label}: {selector} is {await text(selector)!r}, expected {value!r}")

    async def click(selector):
        # A server rejection opens the DEBUG overlay over the page; a failed
        # expectation is already recorded, so keep driving the remaining flows.
        await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")
        await page.click(selector, timeout=5000)

    def last(event):
        found = [item for item in sent if item and item[0] == event]
        return found[-1][1] if found else None

    await page.goto(BASE + PATH, timeout=60000)
    if transport == "http":
        await page.wait_for_function("() => !!(window.djust && window.djust.handleEvent)")
    else:
        await page.wait_for_function(
            "() => !!(window.djust && window.djust.liveViewInstance"
            " && window.djust.liveViewInstance.viewMounted)",
            timeout=10000,
        )

    # 1. Strict click: the typed dj-value-* argument only.
    await click("#strict-pick")
    await expect("#strict-result", "pick:7:int")
    if last("pick") != {"item_id": 7}:
        failures.append(f"{label}: strict click sent {last('pick')!r}")

    # 2. A malformed literal never leaves the browser and applies no effect.
    before = len(sent)
    await click("#strict-bad")
    await page.wait_for_timeout(500)
    if [item for item in sent[before:] if item]:
        failures.append(f"{label}: rejected click still sent {sent[before:]!r}")
    if await text("#strict-bad") != "Bad literal":
        failures.append(f"{label}: dj-disable-with ran on a rejected event")
    errors = await page.evaluate("() => window.__strictErrors")
    if errors != [
        {
            "error": "Invalid event arguments for this handler.",
            "traceback": None,
            "event": "pick",
            "validation_details": None,
        }
    ]:
        failures.append(f"{label}: rejection report was {errors!r}")
    await expect("#strict-calls", "1")
    # Visible: under DEBUG the dev overlay shows it. Dismiss it like a developer.
    overlay = await page.evaluate(
        "() => document.getElementById('djust-error-overlay')?.textContent || null"
    )
    if overlay is not None and "7x" in overlay:
        failures.append(f"{label}: the error overlay echoes the rejected literal")
    await page.evaluate("() => document.getElementById('djust-error-overlay')?.remove()")

    # 3. dj-input: only the declared `value`.
    await page.fill("#strict-search", "abc")
    await expect("#strict-result", "search:abc")
    if last("search") != {"value": "abc"}:
        failures.append(f"{label}: strict dj-input sent {last('search')!r}")

    # 4. dj-submit: only the declared field, no _target.
    await click("#strict-save")
    await expect("#strict-result", "save:Title")
    if last("save") != {"title": "Title"}:
        failures.append(f"{label}: strict dj-submit sent {last('save')!r}")

    # 5. The legacy control still receives dj-value-* and data-* values.
    await click("#strict-legacy")
    await expect("#strict-result", "legacy:mode,row")

    # 6. A hand-crafted forged message never reaches the handler.
    if transport == "http":
        status = await page.evaluate(
            """async () => (await fetch(location.href, {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': window.djust.csrfToken(),
                          'X-Djust-Event': 'pick'},
                body: JSON.stringify({item_id: '8', component: 'forged'})})).status"""
        )
        if status != 400:
            failures.append(f"{label}: forged HTTP event answered {status}")
    else:
        await page.evaluate(
            """() => window.djust.liveViewInstance.sendMessage({type: 'event', event: 'pick',
                params: {item_id: '8', component: 'forged'}, ref: 9999})"""
        )
        await page.wait_for_timeout(800)
    await expect("#strict-result", "legacy:mode,row")
    await expect("#strict-calls", "4")

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
                failures.append(f"{transport}: flow aborted: {type(exc).__name__}")
        await browser.close()
    if failures:
        print("❌ ADR-036 strict parameter browser matrix failed:")
        for failure in failures:
            print("  -", failure)
        sys.exit(1)
    print("✅ ADR-036 strict parameter browser matrix passed (websocket, sse, http)")


if __name__ == "__main__":
    asyncio.run(main())
