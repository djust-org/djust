#!/usr/bin/env python3
"""
Browser-smoke canary (#1849, #1848) — guards the 1.0.7 runtime-break class.

Two real runtime/wiring breaks shipped in the 1.0.7 upgrade that the
8237-passing pytest suite + an HTTP-200 smoke both PASSED over (#1849); only
driving the live page in a browser caught them:

  1. A LiveView refused at WS mount (a stale ``dj-view`` ref rode a
     boundary-less ``startswith`` allowlist) — the page rendered server-side
     but never went live, so ``dj-click`` did nothing.
  2. An inline ``<script>`` placed INSIDE the dj-root registered a delegated
     click listener that the #1610 WS-mount morph re-created without executing,
     so the handler silently never registered (#1848) — no console error.

This guard drives ``/demos/browser-smoke/`` (BrowserSmokeView + its template,
the smallest faithful reproduction surface for both classes):

  A. MOUNT canary (class 1): click ``dj-click="bump"`` and assert the
     server-rendered count goes 0 -> 1. If mount is refused, the click is a
     no-op and the count stays 0. Also asserts NO console error and that the
     WebSocket actually connected.
  B. INLINE-SCRIPT canary (class 2): click the inline-script-wired tab button
     and assert ``.active`` moves from Tab 1 to Tab 2 + Panel 2 becomes
     visible. If the inline script inside the dj-root was not executed under
     the mount morph, the listener never registered and ``.active`` does not
     move.

Modeled on tests/playwright/test_nav_hooks.py / test_loading_attribute.py:
standalone script, playwright async API, exits 0 on success / non-zero with a
clear message on failure.

This canary is now a HARD merge gate (#1869 / Action Tracker #314): it runs in
its OWN blocking ``browser-smoke`` CI job (NOT ``continue-on-error``) and is
wired into the ``test-summary`` AND-condition, so a runtime break of either
class red-bars the PR. It was promoted per #1534 only after going green on the
runner in the non-blocking ``playwright-tests`` leg (the rest of that leg —
loading_attribute / cache_decorator / draft_mode / nav_hooks — stays
non-blocking because the FULL playwright suite can be flaky; only this stable
two-class canary gates). The #1848 inline-script branch was flipped from a
tolerated known-xfail to a HARD assertion once PR #1871 fixed #1848.
"""

import asyncio
import sys
from playwright.async_api import async_playwright

BASE = "http://localhost:8002"
PAGE = f"{BASE}/demos/browser-smoke/"


async def test_browser_smoke():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Capture console errors (the #1848 break is silent — no console error —
        # but a mount refusal or other regression often logs one).
        console_errors = []
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        failures = []

        print(f"📄 Loading browser-smoke canary: {PAGE}")
        # The CI harness (.github/actions/djust-playwright-server) now polls
        # /demos/browser-smoke/ until it answers 200 BEFORE this test runs
        # (#1943), so the route is already warm here. The explicit 60s timeout
        # (up from the 30s default) is belt-and-suspenders for a cold-cache
        # first paint: it removes the fixed-30s `page.goto` deadline that
        # intermittently red-barred the BLOCKING browser-smoke gate (#1869) on
        # cold Rust-compile runs, while still failing on a truly-down server.
        await page.goto(PAGE, timeout=60000)

        # --- A. MOUNT canary (#1849 class 1) ---
        # Wait for the LiveView to connect over the WebSocket. djust exposes the
        # live instance on window.djust; a refused mount never sets it up.
        print("⏳ Waiting for LiveView WS mount...")
        try:
            # window.djust.liveViewInstance is the live WS connection object
            # (set in 01-dom-helpers-turbo.js once the consumer connects); a
            # refused mount never gets here. _mountReady flips true after the
            # first mount response is applied. Accept either signal.
            await page.wait_for_function(
                """
                () => {
                    const dj = window.djust;
                    return !!(dj && (dj.liveViewInstance || dj._mountReady));
                }
                """,
                timeout=8000,
            )
        except Exception:
            failures.append(
                "#1849-mount: timed out waiting for window.djust.liveViewInstance "
                "(LiveView never connected over the WebSocket — mount refused?)"
            )

        print('➡️ Clicking dj-click="bump" (mount round-trip)...')
        await page.click("#smoke-bump")

        # The count is server-rendered into #smoke-count; a successful mount +
        # event round-trip patches it 0 -> 1. A refused mount leaves it at 0.
        try:
            await page.wait_for_function(
                """
                () => {
                    const el = document.querySelector('#smoke-count');
                    return el && el.textContent.trim() === '1';
                }
                """,
                timeout=8000,
            )
        except Exception:
            count = await page.evaluate(
                "() => (document.querySelector('#smoke-count')||{}).textContent"
            )
            failures.append(
                "#1849-mount: dj-click did not round-trip — #smoke-count is "
                f"{count!r}, expected '1' (LiveView did not mount / WS event lost)"
            )

        # --- B. INLINE-SCRIPT canary (#1848 class 2) ---
        # The tab toggle is wired by an inline <script> INSIDE the dj-root. If
        # the mount morph re-created that <script> without executing it, the
        # delegated listener never registered and .active will not move.
        #
        # HARD ASSERTION (was a tolerated known-xfail until #1848 landed):
        # #1848 was FIXED by PR #1871 — the #1610 WS-mount morph now
        # re-executes classic inline <script> via
        # window.djust._runInsertedScripts (re-create each <script> via
        # document.createElement + replaceWith, the only DOM op that makes the
        # browser run an already-in-tree inert script). So the inline <script>
        # inside the dj-root runs on mount, its delegated listener registers,
        # and this toggle works. We now assert the EXPECTED-correct behavior as
        # a HARD regression guard: ANY failure (the inline script silently not
        # running again — the exact #1848 regression — OR the toggle otherwise
        # broken) red-bars this now-gating canary. The former xfail tolerance
        # (warn-not-fail when window.__smokeTabsWired was undefined) was
        # removed when #1848 was fixed (Action Tracker #314 / #1869).
        print("➡️ Clicking inline-script-wired Tab 2 (#1848 regression guard)...")
        active_before = await page.evaluate(
            "() => document.querySelector('.smoke-tab-button.active')?.id"
        )
        await page.click("#smoke-tab-2")
        try:
            await page.wait_for_function(
                """
                () => {
                    const t2 = document.querySelector('#smoke-tab-2');
                    const p2 = document.querySelector('#smoke-panel-2');
                    return t2 && t2.classList.contains('active') &&
                           p2 && p2.classList.contains('active');
                }
                """,
                timeout=4000,
            )
            print("✅ #1848 inline-script toggle worked (regression guard green).")
        except Exception:
            active_after = await page.evaluate(
                "() => document.querySelector('.smoke-tab-button.active')?.id"
            )
            handled = await page.evaluate("() => window.__smokeTabsHandled || 0")
            inline_ran = await page.evaluate("() => !!window.__smokeTabsWired")
            failures.append(
                "#1848-inline-script: tab toggle did not fire — active tab "
                f"stayed {active_after!r} (before={active_before!r}), "
                f"handler invocations={handled}, inline-script-ran={inline_ran}. "
                "#1848 was fixed by PR #1871 (re-execute classic <script> on "
                "the #1610 mount morph via window.djust._runInsertedScripts); "
                "this is a REGRESSION of that fix — the inline <script> inside "
                "the dj-root was not executed under the mount morph, so its "
                "delegated listener never registered."
            )

        # --- No console errors (the mount canary should be clean) ---
        if console_errors:
            failures.append("console/page errors during smoke: " + "; ".join(console_errors[:5]))

        await browser.close()

        if failures:
            print("\n❌ Browser smoke canary FAILED:")
            for f in failures:
                print(f"   - {f}")
            return 1

        print("\n✅ Browser smoke canary passed (mount + inline-script control).")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(test_browser_smoke())
    sys.exit(exit_code)
