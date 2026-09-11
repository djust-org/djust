#!/usr/bin/env python3
"""
Playwright regression guard for TableComponent multi-select state (#2781).

Drives /demos/table-select/ (a selectable three-row ``TableComponent``) in a
real browser and, after every click, asserts BOTH what the user sees (the
``checked`` PROPERTY of every checkbox in the live DOM) AND what the server
holds (``selected_rows``, rendered into ``#selected-csv``). The four features
the issue enumerates, in the order it enumerates them:

  1. the header checkbox checks / unchecks every row checkbox
  2. checking MANY rows is allowed (row 1 then row 2 -> both stay checked)
  3. unchecking any row unchecks the header checkbox
  4. checking every row checks the header checkbox

Why a browser (#1849): the #2780 WebSocket tests asserted ``selected_rows``
and were green, because they built the event from the rendered control under
the ``dj-click`` model (every ``data-*`` becomes a param). The REAL client's
form-event path (``buildFormEventParams``, 09-event-binding.js) sends only
``value`` / ``field`` / ``component_id`` / ``dj-value-*`` — so the rendered
``data-row-id`` never left the browser and ``toggle_row`` ran as
``row_id=""``. Only the real client builds the real frame; this test asserts
the DOM ``checked`` PROPERTY and the server readout together so a drift on
either side of the wire goes red.

Modeled on tests/playwright/test_browser_smoke.py: standalone script,
playwright async API, exits 0 on success and non-zero with a clear message.
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("DJUST_PLAYWRIGHT_BASE", "http://localhost:8002")
PAGE = f"{BASE}/demos/table-select/"

ROW = 'input[dj-change="toggle_row"][dj-value-row-id="{rid}"]'
HEADER = 'input[dj-change="toggle_all"]'


async def _wait_csv(page, expected, failures, step):
    """Wait until the server's ``selected_rows`` readout equals ``expected``."""
    try:
        await page.wait_for_function(
            "(exp) => (document.querySelector('#selected-csv')||{}).textContent.trim() === exp",
            arg=expected,
            timeout=8000,
        )
        return True
    except Exception:
        got = await page.evaluate("() => (document.querySelector('#selected-csv')||{}).textContent")
        failures.append(f"{step}: server selected_rows is {got!r}, expected {expected!r}")
        return False


async def _dom_state(page):
    """The live ``checked`` PROPERTY of the header + every row checkbox."""
    return await page.evaluate(
        """
        () => ({
            header: document.querySelector('input[dj-change="toggle_all"]').checked,
            rows: Object.fromEntries(
                Array.from(document.querySelectorAll('input[dj-change="toggle_row"]'))
                    .map(el => [el.getAttribute('dj-value-row-id'), el.checked])
            ),
        })
        """
    )


async def _expect(page, failures, step, header, rows):
    """Assert the DOM ``checked`` properties match ``header`` + ``rows``."""
    # Give the patch frame a tick to land after the server readout changed:
    # the csv span and the checkboxes are patched in the same frame, but the
    # assertion is on properties so poll briefly instead of asserting once.
    want = {"header": header, "rows": rows}
    try:
        await page.wait_for_function(
            """
            (want) => {
                const h = document.querySelector('input[dj-change="toggle_all"]');
                if (!h || h.checked !== want.header) return false;
                for (const [rid, on] of Object.entries(want.rows)) {
                    const el = document.querySelector(
                        'input[dj-change="toggle_row"][dj-value-row-id="' + rid + '"]');
                    if (!el || el.checked !== on) return false;
                }
                return true;
            }
            """,
            arg=want,
            timeout=4000,
        )
    except Exception:
        got = await _dom_state(page)
        failures.append(f"{step}: DOM checked state is {got}, expected {want}")


async def test_table_select():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        console_errors = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        failures = []

        print(f"📄 Loading table-select canary: {PAGE}")
        await page.goto(PAGE, wait_until="networkidle", timeout=30000)

        print("⏳ Waiting for LiveView WS mount...")
        try:
            await page.wait_for_function(
                "() => { const dj = window.djust; return !!(dj && (dj.liveViewInstance || dj._mountReady)); }",
                timeout=8000,
            )
        except Exception:
            failures.append("mount: LiveView never connected over the WebSocket")

        rows = await page.evaluate(
            "() => document.querySelectorAll('input[dj-change=\"toggle_row\"]').length"
        )
        if rows != 3:
            failures.append(f"setup: expected 3 row checkboxes, found {rows}")

        # --- Feature 2: checking MANY rows is allowed ---
        print("➡️ Feature 2: check row 1, then row 2")
        await page.click(ROW.format(rid="1"))
        await _wait_csv(page, "1", failures, "feature 2 (row 1)")
        await _expect(
            page, failures, "feature 2 (row 1)", False, {"1": True, "2": False, "3": False}
        )

        await page.click(ROW.format(rid="2"))
        await _wait_csv(page, "1,2", failures, "feature 2 (row 2)")
        await _expect(
            page, failures, "feature 2 (row 2)", False, {"1": True, "2": True, "3": False}
        )

        # --- Feature 4: checking every row checks the header ---
        print("➡️ Feature 4: check row 3 -> header must become checked")
        await page.click(ROW.format(rid="3"))
        await _wait_csv(page, "1,2,3", failures, "feature 4")
        await _expect(page, failures, "feature 4", True, {"1": True, "2": True, "3": True})

        # --- Feature 3: unchecking any row unchecks the header ---
        print("➡️ Feature 3: uncheck row 2 -> header must become unchecked")
        await page.click(ROW.format(rid="2"))
        await _wait_csv(page, "1,3", failures, "feature 3")
        await _expect(page, failures, "feature 3", False, {"1": True, "2": False, "3": True})

        # --- Feature 1: the header checks / unchecks every row ---
        print("➡️ Feature 1: header click selects all; second click clears all")
        await page.click(HEADER)
        await _wait_csv(page, "1,2,3", failures, "feature 1 (select all)")
        await _expect(
            page, failures, "feature 1 (select all)", True, {"1": True, "2": True, "3": True}
        )

        await page.click(HEADER)
        await _wait_csv(page, "", failures, "feature 1 (clear all)")
        await _expect(
            page, failures, "feature 1 (clear all)", False, {"1": False, "2": False, "3": False}
        )

        # A second pass of feature 2 from the cleared state: the header's
        # select-all/clear-all must not have left stale client state behind.
        print("➡️ Feature 2 again after clear-all: rows 3 then 1")
        await page.click(ROW.format(rid="3"))
        await _wait_csv(page, "3", failures, "feature 2 again (row 3)")
        await page.click(ROW.format(rid="1"))
        await _wait_csv(page, "3,1", failures, "feature 2 again (row 1)")
        await _expect(page, failures, "feature 2 again", False, {"1": True, "2": False, "3": True})

        await browser.close()

        if console_errors:
            print("⚠️ console errors:")
            for line in console_errors:
                print("   ", line)

        if failures:
            print("\n❌ FAILURES:")
            for f in failures:
                print("  -", f)
            return False
        print("\n✅ TableComponent multi-select: all four features hold in the browser")
        return True


if __name__ == "__main__":
    ok = asyncio.run(test_table_select())
    sys.exit(0 if ok else 1)
