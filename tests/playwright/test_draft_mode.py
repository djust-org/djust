#!/usr/bin/env python3
"""
Playwright test for DraftModeMixin functionality.

Tests:
1. localStorage API available
2. Draft key configuration
3. Auto-save after typing (500ms debounce)
4. Auto-restore on page reload
5. Draft clearing on successful save
"""

import asyncio
import sys
from playwright.async_api import async_playwright


async def test_draft_mode():
    """Test DraftModeMixin with automated browser testing"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Collect console logs
        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        print("📄 Loading draft mode test page: http://localhost:8002/tests/draft-mode/")
        await page.goto("http://localhost:8002/tests/draft-mode/")

        # Wait for LiveView to mount and tests to start
        print("⏳ Waiting for automated tests to complete...")
        await asyncio.sleep(4)  # 2s for mount + 2s for tests

        # Get test results from the page
        try:
            # Count client-side test results
            client_tests = await page.query_selector_all("#client-test-results .test-result-item")
            passed_tests = await page.query_selector_all("#client-test-results .test-result-passed")
            failed_tests = await page.query_selector_all("#client-test-results .test-result-failed")

            client_test_count = len(client_tests)
            client_passed = len(passed_tests)
            client_failed = len(failed_tests)

            # Get draft save count
            draft_save_el = await page.query_selector("#draft-save-count")
            draft_saves = await draft_save_el.inner_text() if draft_save_el else "0"

            # Get draft status
            draft_status_el = await page.query_selector("#draft-status")
            draft_status = await draft_status_el.inner_text() if draft_status_el else "Unknown"

            print("\n📊 Test Results:")
            print(f"  Client tests run: {client_test_count}")
            print(f"  Client tests passed: {client_passed}")
            print(f"  Client tests failed: {client_failed}")
            print(f"  Draft saves: {draft_saves}")
            print(f"  Draft status: {draft_status.strip()}")

            # Print relevant console logs
            print("\n🔍 Console Logs:")
            draft_logs = [log for log in console_logs if '[Test]' in log or '[Draft]' in log]
            for log in draft_logs[-20:]:  # Last 20 relevant logs
                print(f"  {log}")

            # Additional test: Check localStorage directly
            print("\n🔍 localStorage Check:")
            draft_key = await page.evaluate("() => '{{ draft_key }}'")
            has_draft = await page.evaluate("""
                () => localStorage.getItem('djust_draft_test_article_editor') !== null
            """)
            print(f"  Draft key: test_article_editor")
            print(f"  Draft in localStorage: {has_draft}")

            if has_draft:
                draft_content = await page.evaluate("""
                    () => localStorage.getItem('djust_draft_test_article_editor')
                """)
                print(f"  Draft content: {draft_content}")

            # Determine if tests passed
            expected_tests = 4  # localStorage, draft key, persistence, auto-save
            if client_test_count >= expected_tests and client_failed == 0:
                print(f"\n✅ PASS: All {client_passed} tests passed")
                await browser.close()
                return 0
            else:
                print(f"\n❌ FAIL: Expected all tests to pass")
                print(f"           Got {client_passed} passed, {client_failed} failed")

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

                await browser.close()
                return 1

        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            print("\n🔍 Full console output (last 50 lines):")
            for log in console_logs[-50:]:
                print(f"  {log}")
            await browser.close()
            return 1


if __name__ == "__main__":
    exit_code = asyncio.run(test_draft_mode())
    sys.exit(exit_code)
