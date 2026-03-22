#!/usr/bin/env python3
"""
Playwright test for dj-loading HTML attribute functionality.

Tests:
1. LoadingManager available
2. dj-loading attributes present in DOM
3. Button disabled during loading (dj-loading.disable)
"""

import asyncio
import sys
from playwright.async_api import async_playwright


async def test_loading_attribute():
    """Test dj-loading attributes with automated browser testing"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Collect console logs
        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        print("📄 Loading dj-loading attribute test page: http://localhost:8002/tests/loading/")
        await page.goto("http://localhost:8002/tests/loading/")

        # Wait for LiveView to mount
        print("⏳ Waiting for LiveView to mount...")
        await asyncio.sleep(2)

        # Wait for client-side automated tests to complete
        # Client tests: 2s initial delay + 0.5s test3 start + 0.1s check + 1.2s slow op = ~3.8s
        print("⏳ Waiting for automated tests to run...")
        await asyncio.sleep(5)

        try:
            # Read client test results from JS array (VDOM patches wipe DOM elements,
            # but the clientTestResults array persists in the JS runtime)
            js_results = await page.evaluate("""
                () => {
                    if (typeof clientTestResults === 'undefined') return null;
                    return clientTestResults.map(t => ({
                        name: t.name,
                        passed: t.passed,
                        expected: t.expected,
                        actual: t.actual
                    }));
                }
            """)

            if js_results is None:
                print("❌ ERROR: clientTestResults array not found in page JS context")
                # Fall back to direct checks
                js_results = []

            client_test_count = len(js_results)
            client_passed = sum(1 for t in js_results if t["passed"])

            # Print relevant console logs
            print("\n🔍 Console Logs:")
            loading_logs = [
                log
                for log in console_logs
                if "[Test]" in log or "[Loading]" in log or "LoadingManager" in log
            ]
            for log in loading_logs[-30:]:
                print(f"  {log}")

            # Direct check: LoadingManager available
            print("\n🔍 LoadingManager Check:")
            has_loading_manager = await page.evaluate(
                "() => typeof globalLoadingManager !== 'undefined'"
            )
            print(f"  globalLoadingManager available: {has_loading_manager}")

            # Direct check: dj-loading attributes in DOM
            loading_elements = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll(
                        '[dj-loading\\\\.disable], [dj-loading\\\\.class], '
                        + '[dj-loading\\\\.show], [dj-loading\\\\.hide]'
                    );
                    return els.length;
                }
            """)
            print(f"  Elements with dj-loading attributes: {loading_elements}")

            # Manual test: Click button and check disabled state
            print("\n🔍 Manual Button Disable Test:")
            disable_button = await page.query_selector("button[dj-loading\\.disable]")
            if disable_button:
                is_disabled_before = await disable_button.is_disabled()
                print(f"  Button disabled before click: {is_disabled_before}")

                print("  Clicking button with dj-loading.disable...")
                await disable_button.click()

                await asyncio.sleep(0.1)
                is_disabled_during = await disable_button.is_disabled()
                print(f"  Button disabled during operation: {is_disabled_during}")

                await asyncio.sleep(1.2)
                is_disabled_after = await disable_button.is_disabled()
                print(f"  Button disabled after operation: {is_disabled_after}")

                manual_test_passed = is_disabled_during and not is_disabled_after
                print(f"  Manual disable test: {'✅ PASS' if manual_test_passed else '❌ FAIL'}")
            else:
                print("  ❌ ERROR: Could not find button with dj-loading.disable")
                manual_test_passed = False

            # Print client test results
            print("\n📊 Test Results:")
            print(f"  Client JS tests: {client_passed}/{client_test_count} passed")
            for t in js_results:
                icon = "✅" if t["passed"] else "❌"
                print(f"    {icon} {t['name']}: {t['actual']}")
            print(f"  Manual disable test: {'PASS' if manual_test_passed else 'FAIL'}")

            # Pass criteria: LoadingManager exists + attributes present + manual disable works
            # Client JS tests are a bonus (they test the same things but from inside the page)
            all_pass = has_loading_manager and loading_elements > 0 and manual_test_passed
            if all_pass:
                print("\n✅ PASS: All core checks passed")
                await browser.close()
                return 0
            else:
                reasons = []
                if not has_loading_manager:
                    reasons.append("LoadingManager not available")
                if loading_elements == 0:
                    reasons.append("No dj-loading attributes in DOM")
                if not manual_test_passed:
                    reasons.append("Button disable test failed")
                print(f"\n❌ FAIL: {', '.join(reasons)}")
                await browser.close()
                return 1

        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            import traceback

            traceback.print_exc()
            print("\n🔍 Full console output (last 50 lines):")
            for log in console_logs[-50:]:
                print(f"  {log}")

            try:
                html = await page.content()
                print("\n🔍 Page HTML (first 5000 chars):")
                print(html[:5000])
            except Exception:
                print("Could not get page HTML")

            await browser.close()
            return 1


if __name__ == "__main__":
    exit_code = asyncio.run(test_loading_attribute())
    sys.exit(exit_code)
