#!/usr/bin/env python3
"""
Playwright regression guard for the v1.0.2 navigation arc (#1742).

Dogfoods three paths end-to-end against the running demo server so a future
regression red-bars CI internally instead of surfacing downstream:

  #1733 — zero-wiring route map SPA nav. Page A -> Page B via dj-navigate
          must NOT do a full page reload: a window sentinel set on first
          load survives, and window.location.pathname changes to B.

  #1737 — SSR->hydration flash parity. On initial load of Page A, a
          MutationObserver on the [dj-view] root must record NO remove+re-add
          of its direct children during the first ~1s (the #1724/#1737
          wholesale-replacement symptom).

  #1738 — DjustHooks/dj-hook. The DemoWidget hook's mounted() marker is set
          after initial load AND a fresh mounted() fires on the destination
          page after the SPA dj-navigate (proving hooks fire on patch-insert).

Modeled on tests/playwright/test_loading_attribute.py / test_cache_decorator.py:
standalone script, playwright async API, exits 0 on success and non-zero with
a clear message on failure.
"""

import asyncio
import sys
from playwright.async_api import async_playwright

BASE = "http://localhost:8002"
PAGE_A = f"{BASE}/demos/nav-a/"


async def test_nav_hooks():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        failures = []

        print(f"📄 Loading Nav Demo Page A: {PAGE_A}")

        # Install the observer before navigation so it can catch early hydration churn.
        await page.add_init_script(
            """
            (() => {
                window.__nav_sentinel = Date.now();
                window.__hydration_child_churn = 0;
                window.__nav_observed_root = false;

                const observeRoot = () => {
                    const root = document.querySelector('[dj-view]');
                    if (!root || window.__nav_obs) {
                        return;
                    }

                    window.__nav_observed_root = true;
                    const obs = new MutationObserver((records) => {
                        for (const r of records) {
                            if (r.target === root &&
                                (r.removedNodes.length > 0 || r.addedNodes.length > 0)) {
                                window.__hydration_child_churn += 1;
                            }
                        }
                    });
                    obs.observe(root, { childList: true });
                    window.__nav_obs = obs;
                };

                observeRoot();

                const installDocumentObserver = () => {
                    if (!document.documentElement || window.__nav_doc_obs) {
                        return;
                    }

                    const docObs = new MutationObserver(() => observeRoot());
                    docObs.observe(document.documentElement, {
                        childList: true,
                        subtree: true,
                    });
                    window.__nav_doc_obs = docObs;
                };

                installDocumentObserver();

                document.addEventListener("DOMContentLoaded", () => {
                    observeRoot();
                    installDocumentObserver();
                });
            })();
            """
        )

        await page.goto(PAGE_A)

        # Wait for the WebSocket connect + hooks mount + hydration observer.
        print("⏳ Waiting for hydration + hook mount...")
        try:
            await page.wait_for_function(
                """
                () => window.__nav_observed_root === true &&
                    window.__demoWidgetMounted === true &&
                    (window.__demoWidgetMountCount || 0) >= 1 &&
                    !!document.querySelector('#demo-widget-a')?.dataset.djustHookMounted
                """,
                timeout=8000,
            )
        except Exception:
            failures.append(
                "#1738/#1737: timed out waiting for Page A hydration observer and hook mount"
            )

        # --- #1738: DemoWidget hook mounted on Page A ---
        mounted_a = await page.evaluate(
            "() => ({ flag: !!window.__demoWidgetMounted, "
            "count: window.__demoWidgetMountCount || 0, "
            "elMarked: !!document.querySelector('#demo-widget-a')?.dataset.djustHookMounted })"
        )
        print(f"🔍 #1738 hook on A: {mounted_a}")
        if not (mounted_a["flag"] and mounted_a["count"] >= 1 and mounted_a["elMarked"]):
            failures.append(
                "#1738: DemoWidget hook did not mount on Page A "
                f"(window.__demoWidgetMounted / mount count / el marker): {mounted_a}"
            )

        # --- #1737: no wholesale [dj-view] child replacement on first load ---
        observed = await page.evaluate("() => window.__nav_observed_root")
        churn = await page.evaluate("() => window.__hydration_child_churn")
        print(f"🔍 #1737 observed [dj-view] root: {observed}, child churn: {churn}")
        if not observed:
            failures.append("#1737: could not find a [dj-view] root to observe")
        elif churn > 0:
            failures.append(
                f"#1737: [dj-view] direct children were removed/re-added {churn} "
                "time(s) during first-load hydration (flash regression)"
            )

        # --- #1733: SPA nav A -> B preserves the window sentinel ---
        sentinel_before = await page.evaluate("() => window.__nav_sentinel")
        mount_count_before = await page.evaluate("() => window.__demoWidgetMountCount || 0")
        print(
            f"🔍 sentinel before nav: {sentinel_before}, hook mounts so far: {mount_count_before}"
        )

        print("➡️  Clicking dj-navigate link to Page B...")
        await page.click("#go-to-b")

        # Wait for the SPA mount frame to arrive + new view to render.
        try:
            await page.wait_for_function(
                """
                (mountCountBefore) => window.location.pathname === '/demos/nav-b/' &&
                                    (window.__demoWidgetMountCount || 0) > mountCountBefore &&
                                    !!document.querySelector('#demo-widget-b')?.dataset.djustHookMounted
                """,
                arg=mount_count_before,
                timeout=8000,
            )
        except Exception:
            failures.append("#1733/#1738: timed out waiting for Page B SPA nav and hook mount")

        path_after = await page.evaluate("() => window.location.pathname")
        sentinel_after = await page.evaluate("() => window.__nav_sentinel")
        print(f"🔍 pathname after nav: {path_after}, sentinel after: {sentinel_after}")

        # Full page reload would wipe window.__nav_sentinel -> undefined/None.
        if sentinel_after is None or sentinel_after != sentinel_before:
            failures.append(
                "#1733: window sentinel did not survive navigation — this was a "
                f"full page reload, not SPA nav (before={sentinel_before}, after={sentinel_after})"
            )
        if path_after != "/demos/nav-b/":
            failures.append(f"#1733: pathname is {path_after}, expected /demos/nav-b/")

        # --- #1738: hook fired again on the patch-inserted widget on Page B ---
        mounted_b = await page.evaluate(
            "() => ({ count: window.__demoWidgetMountCount || 0, "
            "elMarked: !!document.querySelector('#demo-widget-b')?.dataset.djustHookMounted })"
        )
        print(f"🔍 #1738 hook on B after SPA nav: {mounted_b}")
        if mounted_b["count"] <= mount_count_before:
            failures.append(
                "#1738: DemoWidget mounted() did not fire on the SPA patch-inserted "
                f"Page B widget (mount count stayed {mounted_b['count']}, was {mount_count_before})"
            )
        if not mounted_b["elMarked"]:
            failures.append(
                "#1738: Page B widget element has no djustHookMounted marker after SPA nav"
            )

        # ---- Report ----
        print("\n📊 Nav/Hooks regression results:")
        if not failures:
            print("✅ PASS: SPA sentinel survived, no hydration flash, hook survived nav")
            await browser.close()
            return 0

        print("❌ FAIL:")
        for f in failures:
            print(f"  - {f}")
        print("\n🔍 Console output (last 40 lines):")
        for log in console_logs[-40:]:
            print(f"  {log}")
        await browser.close()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(test_nav_hooks())
    sys.exit(exit_code)
