#!/usr/bin/env python3
"""ADR-037 row 20: events from an embedded child reach the child view.

Drives ``/demos/embedded-directives/`` over WebSocket, SSE and HTTP-only
transports. The parent and the ``{% live_render %}`` child declare the same
handler names; ``dj-shortcut``, ``dj-click-away``, ``dj-paste`` and
``dj-click`` inside the child must be received by the child and never by the
parent. ``{% live_render %}`` stamps ``data-djust-embedded`` on its wrapper
and on event-bearing elements; this checks the directives the stamp list
left out.

At ``f3768bbc9`` + row 20 this fails, for reasons the stamp list does not
decide (the child's wrapper is stamped, and the client looks up ancestors):

- ``dj-paste`` attaches no owner context, so over every transport it reaches
  the parent;
- over HTTP-only every event from the child reaches the parent, including a
  stamped ``dj-click``.

Standalone, like the other scripts here: exits 0 on success and non-zero
with the list of failures. ``EMBEDDED_BASE`` (default
``http://localhost:18438``) names the demo server.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

from _transports import INIT, TransportWatch

BASE = os.environ.get("EMBEDDED_BASE", "http://localhost:18438")
PATH = "/demos/embedded-directives/"
PASTE = """
() => {
    const box = document.getElementById('paste-box');
    box.focus();
    const data = new DataTransfer();
    data.setData('text/plain', 'pasted');
    box.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
}
"""


async def received(page, which):
    return (await page.text_content("#%s-received" % which) or "").split()


async def landed(page, token):
    """Which view first shows ``token``: "child", "parent" or "nowhere".

    Read right after each action: a parent render re-mounts the child, so the
    child's earlier record does not survive a misrouted event.
    """
    for _ in range(50):
        for which in ("child", "parent"):
            if token in await received(page, which):
                return which
        await page.wait_for_timeout(100)
    return "nowhere"


async def run(browser, transport, failures):
    context = await browser.new_context()
    await context.add_init_script(INIT[transport])
    page = await context.new_page()
    watch = TransportWatch(page)
    await page.goto(BASE + PATH)
    await page.wait_for_timeout(1500)

    actions = [
        ("away", lambda: page.click("#outside")),
        ("shortcut", lambda: page.keyboard.press("Control+k")),
        ("paste", lambda: page.evaluate(PASTE)),
        ("click", lambda: page.click("#child-click")),
    ]
    for token, act in actions:
        await page.click("h1")  # Nothing focused, and a click-away already sent.
        await page.wait_for_timeout(300)
        await act()
        where = await landed(page, token)
        if where != "child":
            failures.append("%s: %s reached %s, not the child" % (transport, token, where))
    watch.check(transport, transport, failures)
    await context.close()


async def main():
    failures = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for transport in ("websocket", "sse", "http"):
            await run(browser, transport, failures)
        await browser.close()
    if failures:
        print("FAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("OK: every directive inside the embedded child reached the child on all transports")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
