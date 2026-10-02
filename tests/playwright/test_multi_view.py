#!/usr/bin/env python3
"""#3252: several LiveViews on one WebSocket stay independently live.

Drives ``/demos/multi-view/`` over WebSocket. The page holds its own eager view,
two ``dj-lazy="idle"`` views of one class that hydrate together (one
``mount_batch``) and a ``dj-lazy="click"`` view of a second class that hydrates
on its first click (one ``mount``). Before #3252 the socket held one view: the
first hydration replaced the page view ("Event rejected" on its next click), a
push aimed at one view ran on another, and only the last view of a batch was
live.

The script checks, in a real browser against the real server, that after every
view has hydrated

* a click in each view runs on that view and changes only its counters;
* a push sent by a handler of the page view reaches the views of the class it
  was sent to, and no other view (the page's own counter included);
* a file chosen in a lazy view's input uploads to that view (its ``upload_register``
  is addressed to it, and the entries it saves are its own), and a ``dj-hook`` inside
  a lazy view pushes its event to that view, not to the page view, which has a
  handler of the same name;
* the page view still answers;
* after the socket drops and reconnects, every view mounts again and answers.

Standalone, like the other scripts here: exits 0 on success and non-zero with
the list of failures. ``MULTI_VIEW_BASE`` (default ``http://localhost:18439``)
names the demo server.
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


async def counts(page):
    """Every view's counters, read from the page."""
    return {
        "page": (await text(page, "#page-clicks"), await text(page, "#page-pushes")),
        "a": (
            await text(page, "#widget-a [data-role=clicks]"),
            await text(page, "#widget-a [data-role=pushes]"),
        ),
        "b": (
            await text(page, "#widget-b [data-role=clicks]"),
            await text(page, "#widget-b [data-role=pushes]"),
        ),
    }


async def settled(page, expected, what, failures):
    """Wait for the counters to equal ``expected``; record a failure otherwise.

    ``expected`` names the views to compare ("page", "a", "b", "late").
    """
    last = None
    for _ in range(60):
        last = await counts(page)
        if "late" in expected:
            last["late"] = (
                await text(page, "#late [data-role=clicks]"),
                await text(page, "#late [data-role=pushes]"),
            )
        if last == expected:
            return True
        await page.wait_for_timeout(100)
    failures.append("%s: expected %r, saw %r" % (what, expected, last))
    return False


async def run(browser, failures):
    context = await browser.new_context()
    await context.add_init_script(INIT["websocket"])
    page = await context.new_page()
    # The demo layout loads fonts and CSS from CDNs; nothing here needs them.
    await page.route(
        "**/*",
        lambda route: route.continue_() if route.request.url.startswith(BASE) else route.abort(),
    )
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    watch = TransportWatch(page)
    await page.goto(BASE + PATH)
    # Both idle views hydrate (together, as one mount_batch) and show their buttons.
    try:
        await page.wait_for_selector("#widget-b [data-role=click]", timeout=15000)
        await page.wait_for_selector("#widget-a [data-role=click]", timeout=15000)
    except Exception:  # noqa: BLE001 - reported below
        failures.append("the lazy views never hydrated")
        await context.close()
        return

    zero = {"page": ("0", "0"), "a": ("0", "0"), "b": ("0", "0")}
    await settled(page, zero, "after hydration", failures)

    # The page view still answers after the siblings hydrated (it used to be
    # replaced by them: "Event rejected").
    await page.click("#page-click")
    await settled(page, {**zero, "page": ("1", "0")}, "page click", failures)

    # Each lazy view answers, and only it changes.
    await page.click("#widget-a [data-role=click]")
    await settled(
        page, {"page": ("1", "0"), "a": ("1", "0"), "b": ("0", "0")}, "click in widget a", failures
    )
    await page.click("#widget-b [data-role=click]")
    await page.click("#widget-b [data-role=click]")
    await settled(
        page, {"page": ("1", "0"), "a": ("1", "0"), "b": ("2", "0")}, "clicks in widget b", failures
    )

    # A handler of the page view pushes to the widget class: both views of it
    # take the push; the page view does not.
    await page.click("#push-widgets")
    await settled(
        page,
        {"page": ("1", "0"), "a": ("1", "1"), "b": ("2", "1")},
        "push to the widgets",
        failures,
    )

    # The click-hydrated view mounts beside all of them, as a single mount.
    await page.click("#late")
    await page.wait_for_selector("#late [data-role=click]", timeout=15000)
    await page.click("#late [data-role=click]")
    await settled(
        page,
        {"page": ("1", "0"), "a": ("1", "1"), "b": ("2", "1"), "late": ("1", "0")},
        "click in the late view",
        failures,
    )
    # A push to its class reaches it alone.
    await page.click("#push-late")
    await settled(
        page,
        {"page": ("1", "0"), "a": ("1", "1"), "b": ("2", "1"), "late": ("1", "1")},
        "push to the late view",
        failures,
    )

    # Everything is still live: the page view, each widget, in any order.
    await page.click("#page-click")
    await page.click("#widget-a [data-role=click]")
    await settled(
        page,
        {"page": ("2", "0"), "a": ("2", "1"), "b": ("2", "1"), "late": ("1", "1")},
        "all views still live",
        failures,
    )

    # A dj-hook inside a lazy view pushes its event to that view. The page view
    # has a handler of the same name: it must not run there.
    await page.click("#widget-a [data-role=hook-click]")
    await settled(
        page,
        {"page": ("2", "0"), "a": ("3", "1"), "b": ("2", "1"), "late": ("1", "1")},
        "hook in widget a",
        failures,
    )

    # A file chosen in the lazy uploader's input uploads to that view. An
    # unaddressed register would reach the page view, which has no upload slot
    # ("No uploads configured for this view").
    await page.wait_for_selector("#uploader [data-role=file]", timeout=15000)
    await page.set_input_files(
        "#uploader [data-role=file]",
        files=[{"name": "note.txt", "mimeType": "text/plain", "buffer": b"hello"}],
    )
    saved = False
    for _ in range(40):
        await page.click("#uploader [data-role=save]")
        if "note.txt (5 bytes)" in await text(page, "#uploader [data-role=saved]"):
            saved = True
            break
        await page.wait_for_timeout(250)
    if not saved:
        failures.append(
            "the upload did not reach the lazy view: saved=%r"
            % (await text(page, "#uploader [data-role=saved]"),)
        )

    # The socket drops and the client reconnects: the page view and every view
    # hydrated beside it mount again on the new socket, and each still answers.
    await page.evaluate("window.djust.liveViewInstance.ws.close()")
    # Wait for the new socket and for every slot's mount reply (a click while the
    # socket is down is the HTTP fallback's, not this test's subject).
    try:
        await page.wait_for_function(
            """() => {
                const ws = window.djust.liveViewInstance;
                const slots = window.djust.viewSlots;
                return ws.ws && ws.ws.readyState === 1 && ws.viewMounted &&
                    ['widget-a', 'widget-b', 'late'].every(id => slots.version(id) !== null);
            }""",
            timeout=20000,
        )
    except Exception:  # noqa: BLE001 - reported below
        failures.append("the views did not mount again after the reconnect")
    else:
        await page.click("#page-click")
        await page.click("#widget-a [data-role=click]")
        await page.click("#widget-b [data-role=click]")
        await page.click("#late [data-role=click]")
        await settled(
            page,
            {"page": ("1", "0"), "a": ("1", "0"), "b": ("1", "0"), "late": ("1", "0")},
            "after the reconnect",
            failures,
        )

    if errors:
        failures.append("page errors: %r" % (errors,))
    watch.check("websocket", "websocket", failures)
    await context.close()


async def main():
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await run(browser, failures)
        await browser.close()
    if failures:
        print("FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print(
        "OK: the page view, two batch-hydrated views and a click-hydrated view each answered "
        "their own clicks and pushes over one WebSocket"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
