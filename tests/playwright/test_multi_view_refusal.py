#!/usr/bin/env python3
"""#3252: a refused lazy view fails alone, over WebSocket and over SSE.

Drives ``/demos/multi-view/`` as an anonymous visitor. The page holds a lazy
view that needs a login (``#guarded``) beside the page view and the other lazy
views. Before, its refusal was a page-level ``navigate``: the browser left for
the login page. Now

* the page stays where it is (the URL does not change, nothing navigates);
* the guarded container shows its own refusal (``role="alert"``, the sign-in
  link), and the secret it guards is not on the page;
* the page view and the other lazy views keep answering their own clicks;
* the refused view is not mounted again by a reconnect and never answers.

Standalone, like the other scripts here: exits 0 on success and non-zero with
the list of failures. ``MULTI_VIEW_BASE`` (default ``http://localhost:18439``)
names the demo server; ``CHROMIUM_EXECUTABLE`` optionally names the browser.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("MULTI_VIEW_BASE", "http://localhost:18439")
PATH = "/demos/multi-view/"


async def text(page, selector):
    return (await page.text_content(selector) or "").strip()


async def run(browser, transport, failures):
    label = transport
    context = await browser.new_context()
    await context.add_init_script(INIT[transport])
    page = await context.new_page()
    await page.route(
        "**/*",
        lambda route: route.continue_() if route.request.url.startswith(BASE) else route.abort(),
    )
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    navigations = []
    page.on(
        "framenavigated",
        lambda frame: navigations.append(frame.url) if frame == page.main_frame else None,
    )
    watch = TransportWatch(page)
    await page.goto(BASE + PATH)

    try:
        await page.wait_for_selector("#guarded .dj-view-refused", timeout=15000)
        await page.wait_for_selector("#widget-a [data-role=click]", timeout=15000)
        await page.wait_for_selector("#widget-b [data-role=click]", timeout=15000)
    except Exception:  # noqa: BLE001 - reported below
        failures.append("%s: the refusal or the sibling views never appeared" % label)
        await context.close()
        return

    refused = page.locator("#guarded .dj-view-refused")
    if await refused.get_attribute("role") != "alert":
        failures.append("%s: the refusal is not an alert" % label)
    if await page.get_attribute("#guarded", "data-djust-refused") != "login_required":
        failures.append("%s: the container does not name the reason" % label)
    link = page.locator("#guarded .dj-view-refused a")
    if await link.count() != 1 or "login" not in (await link.get_attribute("href") or ""):
        failures.append("%s: the sign-in link is missing" % label)
    if "members only" in await page.content():
        failures.append("%s: the guarded view's content reached an anonymous visitor" % label)

    # Nothing navigated: the browser is where it was, and it never reloaded.
    if page.url != BASE + PATH or len(navigations) != 1:
        failures.append("%s: the page navigated: %r (now %s)" % (label, navigations, page.url))

    # The page view and the sibling views answer their own clicks.
    await page.click("#page-click")
    await page.click("#widget-a [data-role=click]")
    await page.click("#widget-b [data-role=click]")
    await page.click("#widget-b [data-role=click]")
    for _ in range(60):
        seen = (
            await text(page, "#page-clicks"),
            await text(page, "#widget-a [data-role=clicks]"),
            await text(page, "#widget-b [data-role=clicks]"),
        )
        if seen == ("1", "1", "2"):
            break
        await page.wait_for_timeout(100)
    else:
        failures.append(
            "%s: clicks after the refusal were %r, expected ('1', '1', '2')" % (label, seen)
        )

    # The refused view is gone from the client's slots, so nothing remounts it.
    mounted = await page.evaluate("window.djust.viewSlots.mounted()")
    if "guarded" in mounted:
        failures.append("%s: the refused view is still registered: %r" % (label, mounted))

    if errors:
        failures.append("%s: page errors: %r" % (label, errors))
    watch.check(transport, label, failures)
    await context.close()


async def main():
    failures = []
    async with async_playwright() as p:
        executable = os.environ.get("CHROMIUM_EXECUTABLE")
        browser = await p.chromium.launch(**({"executable_path": executable} if executable else {}))
        for transport in ("websocket", "sse"):
            try:
                await run(browser, transport, failures)
            except Exception as exc:  # noqa: BLE001 - a run that breaks is a failure
                failures.append("%s: the run raised: %s" % (transport, str(exc).splitlines()[0]))
        await browser.close()
    if failures:
        print("FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print(
        "OK: a login-required lazy view was refused in its own container over WebSocket and "
        "SSE; the page did not navigate and the other views kept answering"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
