#!/usr/bin/env python3
"""Real WS/SSE recovery after a 250ms save exceeds its 150ms storage deadline.

Standalone browser guard. Starts an isolated demo process with a test-only
save delay, then checks the error event, nonblocking status, catch-up render
and a normal subsequent click. No forced clicks, dismissal or wider deadline.
Requires the demo database migrations and Playwright Chromium. Artifacts go to
TRANSIENT_SAVE_ARTIFACTS (default scratch/transient-save-recovery).
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[2]


def serve(port):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    from demo_project.asgi import application
    from django.conf import settings
    from djust.runtime import ViewRuntime
    import uvicorn

    settings.DJUST_EXPLICIT_STATE_SAVE_TIMEOUT = 0.150
    original = ViewRuntime._save_explicit_root

    def delayed(runtime, view, request):
        if (
            type(view).__name__ == "ExposureMatrixView"
            and view.page == 2
            and not getattr(runtime, "_test_save_delayed", False)
        ):
            runtime._test_save_delayed = True
            time.sleep(0.250)
        return original(runtime, view, request)

    ViewRuntime._save_explicit_root = delayed
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="info")


async def check_browser(base, artifacts):
    from playwright.async_api import async_playwright
    from _transports import INIT, TransportWatch
    from test_exposure_matrix import wait_mounted, wait_text

    results = []
    failures = []
    async with async_playwright() as playwright:
        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        browser = await playwright.chromium.launch(**options)
        try:
            for transport in ("websocket", "sse"):
                context = await browser.new_context()
                await context.add_init_script(
                    INIT[transport]
                    + """
                    window.saveErrors = [];
                    window.addEventListener('djust:error', e => {
                        const record = {detail: e.detail};
                        window.saveErrors.push(record);
                        queueMicrotask(() => {
                            record.statusShown = !!document.querySelector('#djust-save-status');
                            record.overlayShown = !!document.querySelector('#djust-error-overlay');
                        });
                    });
                """
                )
                page = await context.new_page()
                watch = TransportWatch(page)
                try:
                    await page.goto(base + "/demos/exposure/")
                    await wait_mounted(page)
                    await page.click("#matrix-increment")
                    await wait_text(page, "#matrix-count", "1")
                    await page.click("#matrix-increment")
                    await wait_text(page, "#matrix-count", "2")
                    await page.click("#matrix-spawn")
                    await wait_text(page, "#matrix-count", "12")
                    await page.click("#matrix-page2")
                    await wait_text(page, "#matrix-page", "2")
                    errors = await page.evaluate("window.saveErrors")
                    # The exact click which a stale modal intercepted on main.
                    await page.click("#matrix-boom", timeout=4000)
                    await page.wait_for_selector("#djust-error-overlay")
                    assert (
                        "E5_ERROR_SENTINEL"
                        in await page.locator("#djust-error-overlay").inner_text()
                    )
                    assert len(errors) == 1, errors
                    assert errors[0]["detail"]["code"] == "state_error", errors
                    assert errors[0]["detail"]["transient"] is True, errors
                    assert errors[0]["statusShown"] is True, errors
                    assert errors[0]["overlayShown"] is False, errors
                    assert await page.locator("#djust-save-status").count() == 0
                    assert len(await page.evaluate("window.saveErrors")) == 2
                    watch.check(transport, transport, failures)
                    print(
                        f"PASS {transport}: transient observed, status recovered, normal click and exception overlay"
                    )
                except Exception as exc:
                    failures.append(f"{transport}: {exc}")
                    await page.screenshot(path=str(artifacts / f"{transport}-failure.png"))
                finally:
                    results.append(
                        {"transport": transport, "errors": await page.evaluate("window.saveErrors")}
                    )
                    await context.close()
        finally:
            await browser.close()
    (artifacts / "browser-errors.json").write_text(json.dumps(results, indent=2))
    if failures:
        raise AssertionError("\n".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.server:
        serve(args.port)
        return
    artifacts = Path(
        os.environ.get("TRANSIENT_SAVE_ARTIFACTS", ROOT / "scratch/transient-save-recovery")
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        (str(ROOT / "python"), str(ROOT / "examples/demo_project"), str(ROOT))
    )
    env["DJUST_DEMO_LOG_CONSOLE"] = "1"
    env["DJUST_DEMO_DEBUG"] = "1"
    with (artifacts / "server.log").open("w") as log:
        server = subprocess.Popen(
            [sys.executable, __file__, "--server", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"Server exited; see {artifacts / 'server.log'}")
                try:
                    with urllib.request.urlopen(base + "/demos/exposure/", timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Demo server startup")
                    time.sleep(0.1)
            asyncio.run(check_browser(base, artifacts))
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
