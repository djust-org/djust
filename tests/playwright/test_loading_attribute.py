#!/usr/bin/env python3
"""
Playwright test for @loading HTML attribute functionality.

Tests:
1. LoadingManager available
2. @loading attributes present in DOM
3. Button disabled during loading (@loading.disable)
4. Button opacity changes during loading (@loading.class)
5. Spinner shows during loading (@loading.show)
6. Content hides during loading (@loading.hide)
7. Server-side handler invocation
"""

import asyncio
import sys
from playwright.async_api import async_playwright


async def test_loading_attribute():
    """Test @loading attributes with automated browser testing"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Collect console logs
        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        print("📄 Loading @loading attribute test page: http://localhost:8002/tests/loading/")
        await page.goto("http://localhost:8002/tests/loading/")

        # Wait for LiveView to mount
        print("⏳ Waiting for LiveView to mount...")
        await asyncio.sleep(1)

        # Wait for automated tests to start
        print("⏳ Waiting for automated tests to run...")
        await asyncio.sleep(3)  # 2s delay + 1s for test execution

        # Get test results from the page
        try:
            # Count client-side test results
            client_tests = await page.query_selector_all("#client-test-results .test-result-item")
            passed_tests = await page.query_selector_all("#client-test-results .test-result-passed")
            failed_tests = await page.query_selector_all("#client-test-results .test-result-failed")

            client_test_count = len(client_tests)
            client_passed = len(passed_tests)
            client_failed = len(failed_tests)

            # Get operation counts
            slow_count_el = await page.query_selector(".stat-card:has-text('Slow Operations') .stat-value")
            slow_count = await slow_count_el.inner_text() if slow_count_el else "0"

            fast_count_el = await page.query_selector(".stat-card:has-text('Fast Operations') .stat-value")
            fast_count = await fast_count_el.inner_text() if fast_count_el else "0"

            # Get tests passed count
            tests_passed_el = await page.query_selector("#tests-passed-count")
            tests_passed = await tests_passed_el.inner_text() if tests_passed_el else "0/0"

            print("\n📊 Test Results:")
            print(f"  Client tests run: {client_test_count}")
            print(f"  Client tests passed: {client_passed}")
            print(f"  Client tests failed: {client_failed}")
            print(f"  Tests passed stat: {tests_passed}")
            print(f"  Slow operations: {slow_count}")
            print(f"  Fast operations: {fast_count}")

            # Print relevant console logs
            print("\n🔍 Console Logs:")
            loading_logs = [log for log in console_logs if '[Test]' in log or '[Loading]' in log or 'LoadingManager' in log]
            for log in loading_logs[-30:]:  # Last 30 relevant logs
                print(f"  {log}")

            # Additional test: Check LoadingManager directly
            print("\n🔍 LoadingManager Check:")
            has_loading_manager = await page.evaluate("""
                () => typeof globalLoadingManager !== 'undefined'
            """)
            print(f"  globalLoadingManager available: {has_loading_manager}")

            # Check for @loading attributes in DOM
            loading_elements = await page.evaluate("""
                () => {
                    const elements = document.querySelectorAll('[\\\\@loading\\\\.disable], [\\\\@loading\\\\.class], [\\\\@loading\\\\.show], [\\\\@loading\\\\.hide]');
                    return elements.length;
                }
            """)
            print(f"  Elements with @loading attributes: {loading_elements}")

            # Manual test: Click button and check disabled state
            print("\n🔍 Manual Button Disable Test:")
            disable_button = await page.query_selector('button[\\@loading\\.disable]')
            if disable_button:
                # Check initial state
                is_disabled_before = await disable_button.is_disabled()
                print(f"  Button disabled before click: {is_disabled_before}")

                # Click button
                print("  Clicking button with @loading.disable...")
                await disable_button.click()

                # Check disabled state immediately (should be disabled during 1s operation)
                await asyncio.sleep(0.1)
                is_disabled_during = await disable_button.is_disabled()
                print(f"  Button disabled during operation: {is_disabled_during}")

                # Wait for operation to complete
                await asyncio.sleep(1.2)
                is_disabled_after = await disable_button.is_disabled()
                print(f"  Button disabled after operation: {is_disabled_after}")

                # Manual test passed if button was disabled during but not after
                manual_test_passed = is_disabled_during and not is_disabled_after
                print(f"  Manual disable test: {'✅ PASS' if manual_test_passed else '❌ FAIL'}")
            else:
                print("  ❌ ERROR: Could not find button with @loading.disable")
                manual_test_passed = False

            # Determine if tests passed
            expected_tests = 3  # LoadingManager available, attributes present, button disabled
            if client_test_count >= expected_tests and client_failed == 0 and manual_test_passed:
                print(f"\n✅ PASS: All {client_passed} automated tests + manual test passed")
                await browser.close()
                return 0
            else:
                print(f"\n❌ FAIL: Expected all tests to pass")
                print(f"           Got {client_passed} passed, {client_failed} failed")
                print(f"           Manual test: {'PASS' if manual_test_passed else 'FAIL'}")

                # Print failed test details
                if client_failed > 0:
                    print("\n🔍 Failed Test Details:")
                    failed_items = await page.query_selector_all("#client-test-results .test-result-failed")
                    for item in failed_items:
                        name = await item.query_selector("h6")
                        expected = await item.query_selector("p:has-text('Expected:')")
                        actual = await item.query_selector("p:has-text('Actual:')")

                        if name:
                            print(f"  ❌ {await name.inner_text()}")
                        if expected:
                            print(f"     {await expected.inner_text()}")
                        if actual:
                            print(f"     {await actual.inner_text()}")

                # Print full page HTML for debugging
                print("\n🔍 Page HTML (first 5000 chars):")
                html = await page.content()
                print(html[:5000])

                await browser.close()
                return 1

        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            print("\n🔍 Full console output (last 50 lines):")
            for log in console_logs[-50:]:
                print(f"  {log}")

            # Print page HTML for debugging
            try:
                html = await page.content()
                print("\n🔍 Page HTML (first 5000 chars):")
                print(html[:5000])
            except:
                print("Could not get page HTML")

            await browser.close()
            return 1


if __name__ == "__main__":
    exit_code = asyncio.run(test_loading_attribute())
    sys.exit(exit_code)
