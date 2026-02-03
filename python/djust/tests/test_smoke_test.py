"""
Tests for the djust smoke test framework itself.

These test the framework's logic without requiring a running server
or Playwright — we mock the browser interactions.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from djust.testing.smoke_test import (
    DjustSmokeTest,
    SmokeTestResult,
    print_results,
    _C,
)


class TestSmokeTestResult(unittest.TestCase):
    """Test the SmokeTestResult dataclass."""

    def test_pass_result(self):
        r = SmokeTestResult('test_foo', passed=True, duration=42.5)
        self.assertTrue(r.passed)
        self.assertFalse(r.skipped)
        self.assertIn('PASS', repr(r))

    def test_fail_result(self):
        r = SmokeTestResult('test_foo', passed=False, message='broke', duration=10.0)
        self.assertFalse(r.passed)
        self.assertIn('FAIL', repr(r))

    def test_skip_result(self):
        r = SmokeTestResult('test_foo', passed=False, skipped=True, message='skipped')
        self.assertFalse(r.passed)
        self.assertTrue(r.skipped)
        self.assertIn('SKIP', repr(r))


class TestDjustSmokeTestInit(unittest.TestCase):
    """Test initialization."""

    def test_default_url(self):
        s = DjustSmokeTest()
        self.assertEqual(s.base_url, 'http://localhost:8000')

    def test_custom_url_strips_trailing_slash(self):
        s = DjustSmokeTest('http://example.com:9000/')
        self.assertEqual(s.base_url, 'http://example.com:9000')

    def test_headless_default(self):
        s = DjustSmokeTest()
        self.assertTrue(s.headless)


class TestPrintResults(unittest.TestCase):
    """Test the print_results output function."""

    def test_all_pass_returns_true(self):
        results = [
            SmokeTestResult('t1', True, duration=10),
            SmokeTestResult('t2', True, duration=20),
        ]
        self.assertTrue(print_results(results))

    def test_any_fail_returns_false(self):
        results = [
            SmokeTestResult('t1', True, duration=10),
            SmokeTestResult('t2', False, message='bad', duration=20),
        ]
        self.assertFalse(print_results(results))

    def test_skips_only_returns_true(self):
        results = [
            SmokeTestResult('t1', True, duration=10),
            SmokeTestResult('t2', False, skipped=True, message='skip', duration=0),
        ]
        # Skips don't count as failures — only actual failures do
        self.assertTrue(print_results(results))


class TestIndividualTests(unittest.TestCase):
    """Test individual smoke test methods with mocked page."""

    def setUp(self):
        self.smoke = DjustSmokeTest('http://localhost:8000')

    def test_test_page_loads_success(self):
        page = AsyncMock()
        response = AsyncMock()
        response.status = 200
        page.goto.return_value = response
        page.content.return_value = '<html>' + 'x' * 200 + '</html>'

        asyncio.run(self.smoke.test_page_loads(page))  # Should not raise

    def test_test_page_loads_404(self):
        page = AsyncMock()
        response = AsyncMock()
        response.status = 404
        page.goto.return_value = response

        with self.assertRaises(AssertionError):
            asyncio.run(self.smoke.test_page_loads(page))

    def test_test_no_console_errors_clean(self):
        page = AsyncMock()
        self.smoke._console_errors = []
        asyncio.run(self.smoke.test_no_console_errors(page))  # Should not raise

    def test_test_no_console_errors_with_errors(self):
        page = AsyncMock()
        self.smoke._console_errors = ['Uncaught TypeError: x is not a function']
        with self.assertRaises(AssertionError):
            asyncio.run(self.smoke.test_no_console_errors(page))

    def test_test_djust_root_exists_found(self):
        page = AsyncMock()
        page.query_selector.return_value = MagicMock()  # Non-None
        asyncio.run(self.smoke.test_djust_root_exists(page))

    def test_test_djust_root_exists_missing(self):
        page = AsyncMock()
        page.query_selector.return_value = None
        with self.assertRaises(AssertionError):
            asyncio.run(self.smoke.test_djust_root_exists(page))

    def test_test_no_resource_404s_clean(self):
        page = AsyncMock()
        self.smoke._failed_resources = []
        asyncio.run(self.smoke.test_no_resource_404s(page))

    def test_test_no_resource_404s_with_failures(self):
        page = AsyncMock()
        self.smoke._failed_resources = ['404 /static/missing.js']
        with self.assertRaises(AssertionError):
            asyncio.run(self.smoke.test_no_resource_404s(page))

    def test_test_event_handler_no_elements(self):
        """No dj-click elements = vacuous pass."""
        page = AsyncMock()
        page.query_selector.return_value = None
        asyncio.run(self.smoke.test_event_handler_works(page))  # Should not raise

    def test_test_static_files_success(self):
        page = AsyncMock()
        resp = AsyncMock()
        resp.status = 200
        page.request.get.return_value = resp
        asyncio.run(self.smoke.test_static_files(page))

    def test_test_static_files_failure(self):
        page = AsyncMock()
        resp = AsyncMock()
        resp.status = 404
        page.request.get.return_value = resp
        with self.assertRaises(AssertionError):
            asyncio.run(self.smoke.test_static_files(page))


class TestRunTest(unittest.TestCase):
    """Test the _run_test wrapper."""

    def test_catches_assertion_error(self):
        smoke = DjustSmokeTest()

        async def failing_test(page):
            raise AssertionError("expected failure")

        page = AsyncMock()
        result = asyncio.run(smoke._run_test(failing_test, page))
        self.assertFalse(result.passed)
        self.assertIn('expected failure', result.message)

    def test_catches_generic_exception(self):
        smoke = DjustSmokeTest()

        async def exploding_test(page):
            raise ValueError("boom")

        page = AsyncMock()
        result = asyncio.run(smoke._run_test(exploding_test, page))
        self.assertFalse(result.passed)
        self.assertIn('ValueError', result.message)

    def test_measures_duration(self):
        smoke = DjustSmokeTest()

        async def slow_test(page):
            import asyncio as aio
            await aio.sleep(0.05)

        page = AsyncMock()
        result = asyncio.run(smoke._run_test(slow_test, page))
        self.assertTrue(result.passed)
        self.assertGreater(result.duration, 40)  # at least ~50ms


class TestPlaywrightMissing(unittest.TestCase):
    """Test behavior when playwright is not installed."""

    def test_run_all_raises_without_playwright(self):
        smoke = DjustSmokeTest()
        with patch('djust.testing.smoke_test.HAS_PLAYWRIGHT', False):
            with self.assertRaises(RuntimeError) as ctx:
                asyncio.run(smoke.run_all())
            self.assertIn('Playwright is required', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
