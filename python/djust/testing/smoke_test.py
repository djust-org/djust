"""
Reusable Playwright smoke test for any djust app.

Verifies that a running djust app has:
- Page loading (200 response, non-empty body)
- No JS console errors
- Static files serving (client.js, debug-panel.js/css)
- WebSocket connectivity (/ws/live/)
- No resource 404s
- data-djust-root element in DOM
- Event handler basics (dj-click doesn't error)

Usage as CLI:
    python -m djust.testing.smoke_test http://localhost:8000
    python -m djust.testing.smoke_test --headless http://localhost:8089

Usage in code:
    from djust.testing.smoke_test import DjustSmokeTest
    smoke = DjustSmokeTest('http://localhost:8000')
    results = await smoke.run_all()
"""

import asyncio
import sys
import time

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ANSI colors
class _C:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


class SmokeTestResult:
    """Result of a single smoke test."""

    __slots__ = ('name', 'passed', 'message', 'duration', 'skipped')

    def __init__(self, name, passed, message='', duration=0.0, skipped=False):
        self.name = name
        self.passed = passed
        self.message = message
        self.duration = duration
        self.skipped = skipped

    def __repr__(self):
        status = 'PASS' if self.passed else ('SKIP' if self.skipped else 'FAIL')
        return f'<SmokeTestResult {self.name}: {status} ({self.duration:.0f}ms)>'


class DjustSmokeTest:
    """Reusable Playwright smoke test for any djust app."""

    STATIC_FILES = [
        '/static/djust/js/client.js',
        '/static/djust/js/debug-panel.js',
        '/static/djust/css/debug-panel.css',
    ]

    def __init__(self, base_url='http://localhost:8000', headless=True, timeout_ms=10000):
        self.base_url = base_url.rstrip('/')
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._console_errors = []
        self._failed_resources = []

    async def run_all(self):
        """Run all smoke tests, return list of SmokeTestResult."""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright is required for smoke tests.\n"
                "Install it with:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            )

        results = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                # Collect console errors and failed resources
                self._console_errors = []
                self._failed_resources = []

                page.on('console', lambda msg: (
                    self._console_errors.append(msg.text)
                    if msg.type == 'error' else None
                ))
                page.on('response', lambda resp: (
                    self._failed_resources.append(f'{resp.status} {resp.url}')
                    if resp.status >= 400 else None
                ))

                # Run tests in order (page load first, others depend on it)
                results.append(await self._run_test(self.test_page_loads, page))

                # Only continue if page loaded
                if results[0].passed:
                    tests = [
                        self.test_no_console_errors,
                        self.test_static_files,
                        self.test_websocket_connects,
                        self.test_no_resource_404s,
                        self.test_djust_root_exists,
                        self.test_event_handler_works,
                    ]
                    for test in tests:
                        results.append(await self._run_test(test, page))
                else:
                    # Skip remaining tests
                    for name in [
                        'test_no_console_errors', 'test_static_files',
                        'test_websocket_connects', 'test_no_resource_404s',
                        'test_djust_root_exists', 'test_event_handler_works',
                    ]:
                        results.append(SmokeTestResult(
                            name, passed=False,
                            message='Skipped (page failed to load)',
                            skipped=True,
                        ))
            finally:
                await browser.close()

        return results

    async def _run_test(self, test_fn, page):
        """Run a single test method, catch exceptions, measure time."""
        name = test_fn.__name__
        t0 = time.monotonic()
        try:
            await test_fn(page)
            duration = (time.monotonic() - t0) * 1000
            return SmokeTestResult(name, passed=True, duration=duration)
        except AssertionError as e:
            duration = (time.monotonic() - t0) * 1000
            return SmokeTestResult(name, passed=False, message=str(e), duration=duration)
        except Exception as e:
            duration = (time.monotonic() - t0) * 1000
            return SmokeTestResult(name, passed=False, message=f'{type(e).__name__}: {e}', duration=duration)

    async def test_page_loads(self, page):
        """Page returns 200 and has content."""
        response = await page.goto(self.base_url, timeout=self.timeout_ms)
        assert response is not None, "No response received"
        assert response.status == 200, f"Expected 200, got {response.status}"
        content = await page.content()
        assert len(content) > 100, f"Page content too short ({len(content)} chars)"

    async def test_no_console_errors(self, page):
        """No JS errors in console."""
        # Give a moment for any deferred errors
        await page.wait_for_timeout(500)
        if self._console_errors:
            errors = '; '.join(self._console_errors[:5])
            suffix = f' (+{len(self._console_errors) - 5} more)' if len(self._console_errors) > 5 else ''
            raise AssertionError(f"Console errors: {errors}{suffix}")

    async def test_static_files(self, page):
        """djust static files (client.js, debug-panel.js/css) return 200."""
        failures = []
        for path in self.STATIC_FILES:
            url = f'{self.base_url}{path}'
            resp = await page.request.get(url)
            if resp.status != 200:
                failures.append(f'{path} → {resp.status}')
        if failures:
            raise AssertionError(f"Static file failures: {', '.join(failures)}")

    async def test_websocket_connects(self, page):
        """WebSocket connects to /ws/live/."""
        ws_url = self.base_url.replace('http://', 'ws://').replace('https://', 'wss://')
        ws_url += '/ws/live/'

        connected = await page.evaluate(f"""
            () => new Promise((resolve) => {{
                const ws = new WebSocket('{ws_url}');
                const timer = setTimeout(() => {{ ws.close(); resolve(false); }}, 5000);
                ws.onopen = () => {{ clearTimeout(timer); ws.close(); resolve(true); }};
                ws.onerror = () => {{ clearTimeout(timer); resolve(false); }};
            }})
        """)
        assert connected, f"WebSocket failed to connect to {ws_url}"

    async def test_no_resource_404s(self, page):
        """No loaded resources return 4xx/5xx."""
        if self._failed_resources:
            items = '; '.join(self._failed_resources[:5])
            suffix = f' (+{len(self._failed_resources) - 5} more)' if len(self._failed_resources) > 5 else ''
            raise AssertionError(f"Failed resources: {items}{suffix}")

    async def test_djust_root_exists(self, page):
        """data-djust-root element exists in DOM."""
        el = await page.query_selector('[data-djust-root]')
        assert el is not None, "No element with data-djust-root found"

    async def test_event_handler_works(self, page):
        """If there's a dj-click element, clicking it doesn't error."""
        el = await page.query_selector('[dj-click]')
        if el is None:
            return  # No dj-click elements, test passes vacuously

        # Clear console errors, click, check for new errors
        errors_before = len(self._console_errors)
        await el.click()
        await page.wait_for_timeout(500)
        new_errors = self._console_errors[errors_before:]
        if new_errors:
            raise AssertionError(f"Errors after dj-click: {'; '.join(new_errors[:3])}")



def print_results(results):
    """Print colored results to stdout."""
    print()
    print(f"{_C.CYAN}{_C.BOLD}{'=' * 60}{_C.RESET}")
    print(f"{_C.CYAN}{_C.BOLD}  djust Smoke Test Results{_C.RESET}")
    print(f"{_C.CYAN}{_C.BOLD}{'=' * 60}{_C.RESET}")
    print()

    passed = failed = skipped = 0
    for r in results:
        ms = f"{_C.DIM}{r.duration:6.0f}ms{_C.RESET}"
        if r.skipped:
            skipped += 1
            print(f"  {_C.YELLOW}⏭  {r.name}{_C.RESET} {ms}")
            print(f"     {_C.DIM}{r.message}{_C.RESET}")
        elif r.passed:
            passed += 1
            print(f"  {_C.GREEN}✅ {r.name}{_C.RESET} {ms}")
        else:
            failed += 1
            print(f"  {_C.RED}❌ {r.name}{_C.RESET} {ms}")
            if r.message:
                print(f"     {_C.RED}{r.message}{_C.RESET}")

    print()
    print(f"{_C.CYAN}{'-' * 60}{_C.RESET}")
    total = passed + failed + skipped
    summary = f"  {total} tests: {_C.GREEN}✅ {passed} passed{_C.RESET}"
    if failed:
        summary += f", {_C.RED}❌ {failed} failed{_C.RESET}"
    if skipped:
        summary += f", {_C.YELLOW}⏭  {skipped} skipped{_C.RESET}"
    print(summary)
    print(f"{_C.CYAN}{'-' * 60}{_C.RESET}")
    print()

    if failed:
        print(f"  {_C.RED}Some smoke tests failed!{_C.RESET}")
    elif skipped:
        print(f"  {_C.YELLOW}Some tests were skipped.{_C.RESET}")
    else:
        print(f"  {_C.GREEN}{_C.BOLD}✅ All smoke tests passed!{_C.RESET}")
    print()

    return failed == 0


def main():
    """CLI entry point."""
    import argparse

    if not HAS_PLAYWRIGHT:
        print(
            f"\n{_C.RED}Error: Playwright is not installed.{_C.RESET}\n\n"
            f"Install it with:\n"
            f"  pip install playwright\n"
            f"  playwright install chromium\n"
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description='djust Smoke Test — verify a running djust app works correctly',
    )
    parser.add_argument('url', nargs='?', default='http://localhost:8000',
                        help='Base URL of the djust app (default: http://localhost:8000)')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='Run browser in headless mode (default)')
    parser.add_argument('--no-headless', action='store_true',
                        help='Run browser with visible UI')
    parser.add_argument('--timeout', type=int, default=10000,
                        help='Timeout in ms for page operations (default: 10000)')
    args = parser.parse_args()

    headless = not args.no_headless
    smoke = DjustSmokeTest(base_url=args.url, headless=headless, timeout_ms=args.timeout)
    results = asyncio.run(smoke.run_all())
    all_passed = print_results(results)
    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()
