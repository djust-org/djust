#!/usr/bin/env python3
"""#3252: several LiveViews over SSE and with no live transport.

Drives ``/demos/multi-view/`` (the page the WebSocket script uses) with the
WebSocket pinned off.

* **SSE** (``EventSource`` available). The page view and the lazy views each
  answer their own clicks, a ``dj-hook`` inside a lazy view pushes its event to
  that view and not to the page view (which has a handler of the same name), the
  click-hydrated view mounts beside the rest, and after the stream is cut and
  reconnects every view mounts again and answers. The views hydrate with a
  ``mount`` frame each (there is no ``mount_batch`` over SSE), and every frame
  of a view is addressed to its container.
* **HTTP-only** (no ``EventSource`` either). A lazy view cannot go live: its
  hydration reports ``djust:error`` with ``code: "view_unavailable"`` instead of
  leaving a container that looks interactive, nothing is posted for it, and the
  page view keeps answering through the HTTP fallback.

Standalone, like the other scripts here: exits 0 on success and non-zero with
the list of failures. ``MULTI_VIEW_BASE`` (default ``http://localhost:18439``)
names the demo server; ``CHROMIUM_EXECUTABLE`` optionally names the browser.
"""

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("MULTI_VIEW_BASE", "http://localhost:18439")
PATH = "/demos/multi-view/"

#: Records every ``djust:error`` the page raises, for the HTTP-only run.
RECORD_ERRORS = """
window.__djustErrors = [];
window.addEventListener('djust:error', (e) => window.__djustErrors.push(e.detail));
"""


async def text(page, selector):
    return (await page.text_content(selector) or "").strip()


async def counts(page):
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


async def dom_click(page, selector):
    """Click through the DOM: the dev error overlay (shown for the ``djust:error``
    the run provokes on purpose) would intercept a pointer click."""
    await page.eval_on_selector(selector, "(el) => el.click()")


async def new_page(browser, transport):
    context = await browser.new_context()
    await context.add_init_script(INIT[transport])
    await context.add_init_script(RECORD_ERRORS)
    page = await context.new_page()
    # The demo layout loads fonts and CSS from CDNs; nothing here needs them.
    await page.route(
        "**/*",
        lambda route: route.continue_() if route.request.url.startswith(BASE) else route.abort(),
    )
    return context, page


async def run_sse(browser, failures):
    context, page = await new_page(browser, "sse")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    posts = []

    def on_request(request):
        if request.method == "POST" and "/djust/sse/" in request.url:
            try:
                posts.append(json.loads(request.post_data or "{}"))
            except ValueError:
                pass

    page.on("request", on_request)
    watch = TransportWatch(page)
    await page.goto(BASE + PATH)
    try:
        await page.wait_for_selector("#widget-a [data-role=click]", timeout=15000)
        await page.wait_for_selector("#widget-b [data-role=click]", timeout=15000)
    except Exception:  # noqa: BLE001 - reported below
        failures.append("sse: the lazy views never hydrated")
        await context.close()
        return

    zero = {"page": ("0", "0"), "a": ("0", "0"), "b": ("0", "0")}
    await settled(page, zero, "sse: after hydration", failures)

    mounts = [p for p in posts if p.get("type") == "mount" and p.get("target_id")]
    if sorted(m["target_id"] for m in mounts) != ["uploader", "widget-a", "widget-b"]:
        failures.append("sse: lazy mounts were %r" % ([m.get("target_id") for m in mounts],))
    if any(p.get("type") == "mount_batch" for p in posts):
        failures.append("sse: a mount_batch was sent over SSE")

    await page.click("#page-click")
    await settled(page, {**zero, "page": ("1", "0")}, "sse: page click", failures)
    await page.click("#widget-a [data-role=click]")
    await settled(
        page, {"page": ("1", "0"), "a": ("1", "0"), "b": ("0", "0")}, "sse: click in a", failures
    )
    await page.click("#widget-b [data-role=click]")
    await page.click("#widget-b [data-role=click]")
    await settled(
        page, {"page": ("1", "0"), "a": ("1", "0"), "b": ("2", "0")}, "sse: clicks in b", failures
    )

    # The click-hydrated view mounts beside all of them.
    await page.click("#late")
    await page.wait_for_selector("#late [data-role=click]", timeout=15000)
    await page.click("#late [data-role=click]")
    await settled(
        page,
        {"page": ("1", "0"), "a": ("1", "0"), "b": ("2", "0"), "late": ("1", "0")},
        "sse: click in the late view",
        failures,
    )

    # A dj-hook in a lazy view pushes its event to that view; the page view has
    # a handler of the same name and must not run it.
    await page.click("#widget-a [data-role=hook-click]")
    await settled(
        page,
        {"page": ("1", "0"), "a": ("2", "0"), "b": ("2", "0"), "late": ("1", "0")},
        "sse: hook in a",
        failures,
    )

    # The events of a lazy view are addressed to it; the page view's are not.
    events = [p for p in posts if p.get("type") == "event"]
    by_target = {}
    for event in events:
        by_target.setdefault(event.get("target_id"), 0)
        by_target[event.get("target_id")] += 1
    expected = {None: 1, "widget-a": 2, "widget-b": 2, "late": 1}
    if by_target != expected:
        failures.append("sse: events by address %r, expected %r" % (by_target, expected))

    # The stream drops and EventSource reconnects: the page view mounts again
    # for the new session, and every view hydrated beside it with it.
    await page.evaluate("window.djust.liveViewInstance.eventSource.close()")
    await page.evaluate(
        "() => { const i = window.djust.liveViewInstance; i.enabled = true; i.disconnect();"
        " i.connect(i.primaryViewPath, Object.fromEntries(new URLSearchParams(location.search))); }"
    )
    try:
        await page.wait_for_function(
            """() => {
                const i = window.djust.liveViewInstance, s = window.djust.viewSlots;
                return i.viewMounted &&
                    ['widget-a', 'widget-b', 'late'].every(id => s.version(id) !== null);
            }""",
            timeout=20000,
        )
    except Exception:  # noqa: BLE001 - reported below
        failures.append("sse: the views did not mount again after the reconnect")
    else:
        await page.click("#page-click")
        await page.click("#widget-a [data-role=click]")
        await page.click("#widget-b [data-role=click]")
        await page.click("#late [data-role=click]")
        await settled(
            page,
            {"page": ("1", "0"), "a": ("1", "0"), "b": ("1", "0"), "late": ("1", "0")},
            "sse: after the reconnect",
            failures,
        )

    if errors:
        failures.append("sse: page errors: %r" % (errors,))
    watch.check("sse", "sse", failures)
    await context.close()


async def run_http(browser, failures):
    context, page = await new_page(browser, "http")
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    watch = TransportWatch(page)
    await page.goto(BASE + PATH)
    await page.wait_for_function("() => !!(window.djust && window.djust.handleEvent)")
    # Every idle lazy view tries to hydrate and is reported, not left silent.
    try:
        await page.wait_for_function("() => window.__djustErrors.length >= 3", timeout=15000)
    except Exception:  # noqa: BLE001 - reported below
        failures.append("http: hydration of the lazy views reported nothing")
    reported = await page.evaluate("window.__djustErrors")
    if not reported or {e.get("code") for e in reported} != {"view_unavailable"}:
        failures.append("http: lazy hydration reported %r" % (reported,))
    # Nothing went live: the containers hold no view.
    for selector in ("#widget-a", "#widget-b", "#uploader"):
        if await page.query_selector(selector + " [data-role]"):
            failures.append("http: %s hydrated without a transport" % selector)

    # The page view answers through the HTTP fallback.
    await dom_click(page, "#page-click")
    await dom_click(page, "#page-click")
    try:
        await page.wait_for_function(
            "() => document.querySelector('#page-clicks').textContent.trim() === '2'",
            timeout=10000,
        )
    except Exception:  # noqa: BLE001 - reported below
        failures.append("http: the page view's clicks were not answered over HTTP")
    if watch.http_events < 2:
        failures.append("http: expected the page's events to be POSTed, saw %d" % watch.http_events)

    if errors:
        failures.append("http: page errors: %r" % (errors,))
    watch.check("http", "http", failures)
    await context.close()


async def main():
    failures = []
    async with async_playwright() as p:
        executable = os.environ.get("CHROMIUM_EXECUTABLE")
        browser = await p.chromium.launch(**({"executable_path": executable} if executable else {}))
        for name, run in (("sse", run_sse), ("http", run_http)):
            try:
                await run(browser, failures)
            except Exception as exc:  # noqa: BLE001 - a run that breaks is a failure
                failures.append("%s: the run raised: %s" % (name, str(exc).splitlines()[0]))
        await browser.close()
    if failures:
        print("FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print(
        "OK: over SSE the page view and the lazy views answered their own clicks and mounted "
        "again after a reconnect; with no transport the lazy views were reported unavailable "
        "and the page view kept answering over HTTP"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
