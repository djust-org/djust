#!/usr/bin/env python3
"""
Test @cache decorator functionality using Playwright
"""
import asyncio
import sys
from playwright.async_api import async_playwright


async def test_cache():
    """Test the @cache decorator with automated browser testing"""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Track console logs
        console_logs = []
        page.on("console", lambda msg: console_logs.append(msg.text))

        # Navigate to cache test page
        print("📄 Loading cache test page: http://localhost:8002/tests/cache/")
        await page.goto("http://localhost:8002/tests/cache/", wait_until="domcontentloaded")

        # Enable debug mode
        await page.evaluate("window.djustDebug = true")

        # Wait for automated tests to complete (they take ~7 seconds)
        print("⏳ Waiting for automated cache tests to complete...")
        await asyncio.sleep(8)

        # Extract results from console logs
        cache_hits = len([log for log in console_logs if "Cache hit" in log])
        cache_misses = len([log for log in console_logs if "Cache miss" in log or "calling server" in log])

        print(f"\n📊 Cache Test Results:")
        print(f"  Cache hits:   {cache_hits}")
        print(f"  Cache misses: {cache_misses}")

        # Print relevant cache logs
        print(f"\n🔍 Cache Debug Logs:")
        cache_logs = [log for log in console_logs if "cache" in log.lower() and ("hit" in log.lower() or "miss" in log.lower() or "cached" in log.lower())]
        for log in cache_logs:
            print(f"  {log}")

        # Expected: 3 misses (laptop, empty, mouse) and 2 hits (laptop repeat, mouse repeat)
        expected_hits = 2
        expected_misses = 3

        success = cache_hits >= expected_hits and cache_misses >= expected_misses

        print(f"\n{'✅ PASS' if success else '❌ FAIL'}: Expected {expected_hits} cache hits and {expected_misses} misses")
        print(f"           Got {cache_hits} cache hits and {cache_misses} misses")

        if not success:
            print("\n🔍 Full console output (last 50 lines):")
            for log in console_logs[-50:]:
                print(f"  {log}")

        await browser.close()
        return success


if __name__ == "__main__":
    try:
        success = asyncio.run(test_cache())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
