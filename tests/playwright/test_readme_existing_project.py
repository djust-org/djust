#!/usr/bin/env python3
"""
The README's "add djust to an existing project" steps, applied verbatim (#3995).

Those steps failed twice in two days: the hero view used an attribute that does
not exist (#3651), and then the documented ``asgi.py`` served no static files, so
the page loaded without the client and the counter never moved (#3653). The
second fault was a serving-path fault, which a test that only executes the
README's Python cannot see.

This script does what a reader does:

  1. creates a fresh Django project and app (``startproject`` / ``startapp``);
  2. copies each fenced block of that README section into the file the README
     names for it, unchanged;
  3. starts the server with the command the README gives (``uvicorn``);
  4. opens ``/counter/`` in a real browser, clicks the button, and requires the
     count to go from 0 to 1 with no console error and no failed request.

Standalone, like the other scripts in this directory: exits 0 on success and
non-zero with a message on failure. It needs no demo server; it starts its own.

    .venv/bin/python tests/playwright/test_readme_existing_project.py
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
SECTION_START = "To add djust to an existing project"
SECTION_END = "\n## "
_FENCE = re.compile(r"^```(\w+)\n(.*?)^```$", re.M | re.S)


def readme_blocks() -> dict[str, str]:
    """The five blocks of the section, keyed by the file each belongs in.

    A README that is restructured so the blocks can no longer be told apart
    fails here, by name, and not later with a confusing browser error.
    """
    text = README.read_text()
    start = text.index(SECTION_START)
    section = text[start : text.index(SECTION_END, start)]
    blocks = [(lang, body) for lang, body in _FENCE.findall(section)]
    found: dict[str, str] = {}
    for lang, body in blocks:
        if lang == "python" and "INSTALLED_APPS" in body:
            found["settings"] = body
        elif lang == "python" and "ProtocolTypeRouter" in body:
            found["asgi"] = body
        elif lang == "python" and body.startswith("# myapp/views.py"):
            found["views"] = body
        elif lang == "python" and body.startswith("# myproject/urls.py"):
            found["urls"] = body
        elif lang == "html" and "myapp/templates/counter.html" in body:
            found["template"] = body
    missing = {"settings", "asgi", "views", "urls", "template"} - set(found)
    if missing:
        raise SystemExit(
            "README section %r no longer has a block for: %s"
            % (SECTION_START, ", ".join(sorted(missing)))
        )
    return found


def build_project(root: Path, blocks: dict[str, str]) -> None:
    run = lambda *args: subprocess.run(  # noqa: E731
        [sys.executable, *args], cwd=root, check=True, capture_output=True
    )
    run("-m", "django", "startproject", "myproject", ".")
    run("manage.py", "startapp", "myapp")
    # An existing project has run its migrations (djust keeps view state in the
    # session, so the session table has to exist). Not a README step.
    run("manage.py", "migrate", "--noinput")
    settings = root / "myproject" / "settings.py"
    # The README's settings block lists "your existing apps" as a comment. Here the
    # existing apps are Django's defaults plus the app the view lives in; the
    # block itself is appended unchanged.
    settings.write_text(
        settings.read_text()
        + "\n_EXISTING_APPS = [*INSTALLED_APPS, 'myapp']\n\n"
        + blocks["settings"]
        + "\nINSTALLED_APPS = [*_EXISTING_APPS, *INSTALLED_APPS]\n"
    )
    (root / "myproject" / "asgi.py").write_text(blocks["asgi"])
    (root / "myapp" / "views.py").write_text(blocks["views"])
    (root / "myproject" / "urls.py").write_text(blocks["urls"])
    templates = root / "myapp" / "templates"
    templates.mkdir()
    (templates / "counter.html").write_text(blocks["template"])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_until_up(url: str, server: subprocess.Popen, log: Path, seconds: float = 40) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit("the server exited before answering:\n" + log.read_text()[-3000:])
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - localhost
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("the server did not answer %s:\n%s" % (url, log.read_text()[-3000:]))


async def drive(url: str) -> list[str]:
    from playwright.async_api import async_playwright

    problems: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.on(
            "console",
            lambda msg: (
                problems.append("console error: " + msg.text) if msg.type == "error" else None
            ),
        )
        page.on("pageerror", lambda exc: problems.append("page error: %s" % exc))
        page.on(
            "response",
            lambda r: (
                problems.append("HTTP %d for %s" % (r.status, r.url))
                if r.status >= 400 and not r.url.endswith("/favicon.ico")
                else None
            ),
        )
        await page.goto(url)
        heading = page.locator("h1")
        first = (await heading.inner_text()).strip()
        if first != "Count: 0":
            problems.append("the page opened showing %r, not 'Count: 0'" % first)
        await page.locator("button[dj-click='increment']").click()
        try:
            await page.wait_for_function(
                "document.querySelector('h1').innerText.trim() === 'Count: 1'", timeout=10_000
            )
        except Exception:  # noqa: BLE001 - reported with the page's own text below
            shown = (await heading.inner_text()).strip()
            problems.append("after one click the page shows %r, not 'Count: 1'" % shown)
        await browser.close()
    return problems


def main() -> int:
    blocks = readme_blocks()
    with tempfile.TemporaryDirectory(prefix="djust-readme-") as tmp:
        root = Path(tmp)
        build_project(root, blocks)
        port = free_port()
        log = root / "server.log"
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        env.pop("DJANGO_SETTINGS_MODULE", None)  # the README's asgi.py sets it
        with log.open("w") as out:
            # README step 4: `uvicorn myproject.asgi:application`
            server = subprocess.Popen(  # noqa: S603 - fixed argv, our own interpreter
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "myproject.asgi:application",
                    "--port",
                    str(port),
                ],
                cwd=root,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
            )
        url = "http://127.0.0.1:%d/counter/" % port
        try:
            wait_until_up(url, server, log)
            problems = asyncio.run(drive(url))
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if problems:
            print("FAIL: the README's existing-project steps do not give a working counter:")
            for problem in problems:
                print("  - " + problem)
            print("--- server log (tail) ---")
            print(log.read_text()[-3000:])
            return 1
    print("OK: README existing-project steps: /counter/ went from 'Count: 0' to 'Count: 1'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
