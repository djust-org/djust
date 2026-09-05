#!/usr/bin/env python3
"""Refuse docs and code that prescribe an API which does not exist (#2679, #2680).

Two failure modes, both found by the #2690 review after a first pass claimed —
by hand-counting — to have fixed "four places":

1. **A deleted global still prescribed as the remedy.** `security.js` was never
   loaded by anything, so `window.djustSecurity` was `undefined` in every
   browser; it was deleted in #2679. But `docs/SECURITY_GUIDELINES.md`'s
   Banned-Patterns table and — worse — two LIVE `.semgrep` rules still told a
   developer hitting a real XSS / prototype-pollution finding to call
   `djustSecurity.safeSetInnerHTML()`.

2. **An inert decorator taught as a working feature.** `@client_state` stamps
   metadata that nothing in the shipped client reads (#2656, same class as
   `@debounce` / `@throttle` / `@optimistic`), and the `StateBus` that was to
   consume it was deleted in #2680. Roughly a hundred doc lines still routed
   users to it — decision guides, cheat sheets, an MCP tool description, and a
   `window.StateBus.subscribe(...)` snippet against a class that no longer
   exists.

The bar is deliberately FILE-level and mechanical, because the thing that
failed was a human claim of completeness (#1859: a pin nobody can drift past
beats a promise). A file may teach `@client_state` all it likes — it must just
also say, somewhere, that it is inert.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories worth scanning: what a human or an AI agent reads as instruction.
SCAN_DIRS = ["docs", "python/djust", ".semgrep", "examples"]
SCAN_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".html", ".txt", ".js"}

# This file explains the rules, and the changelog/CONTRIBUTING record the
# deletions in prose. Their mentions are the audit trail, not prescriptions.
EXEMPT = {
    "scripts/check-inert-api-claims.py",
    "python/djust/tests/test_inert_api_claims.py",
    "CONTRIBUTING.md",
}

# A CALL on the deleted global. `djustSecurity` bare is fine — that is how the
# corrections say "there is no djustSecurity global".
DELETED_GLOBAL_CALL = re.compile(r"\bdjustSecurity\s*\.")

# The deleted class, reached as a live object.
DELETED_STATEBUS_USE = re.compile(
    r"\b(?:window\s*\.\s*StateBus|StateBus\s*\.\s*(?:subscribe|set|get|notify))"
)

# Teaching the inert decorator: a decoration or an import of it.
INERT_DECORATOR_USE = re.compile(
    r"@client_state\s*\(|from\s+djust[\w.]*\s+import\s+[^\n]*\bclient_state\b"
)

# Any of these anywhere in the file discharges the requirement.
INERT_MARKER = re.compile(r"#2656|\bINERT\b|\binert\b")


def _files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in SCAN_SUFFIXES and "__pycache__" not in p.parts:
                out.append(p)
    for name in ("llms.txt", "README.md"):
        p = ROOT / name
        if p.is_file():
            out.append(p)
    return out


def check() -> list[str]:
    failures: list[str] = []
    for path in sorted(_files()):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            if DELETED_GLOBAL_CALL.search(line):
                failures.append(
                    f"{rel}:{lineno}: calls `djustSecurity.` — that global does not exist "
                    f"(security.js was never loaded and was deleted in #2679). "
                    f"Prescribe the real remedy instead (textContent; UNSAFE_KEYS / Map / "
                    f"Object.create(null)).\n    {line.strip()}"
                )
            if DELETED_STATEBUS_USE.search(line):
                failures.append(
                    f"{rel}:{lineno}: uses `StateBus` as a live object — that class was "
                    f"deleted in #2680 (it had zero consumers).\n    {line.strip()}"
                )

        if INERT_DECORATOR_USE.search(text) and not INERT_MARKER.search(text):
            first = next(
                (i for i, ln in enumerate(text.splitlines(), 1) if INERT_DECORATOR_USE.search(ln)),
                1,
            )
            failures.append(
                f"{rel}:{first}: teaches `@client_state` without saying anywhere in the file "
                f"that it is inert. It stamps metadata nothing in the shipped client reads "
                f"(#2656) — a handler decorated with it behaves exactly like an undecorated "
                f"one. Add a marker mentioning #2656 (or the word INERT)."
            )
    return failures


def main() -> int:
    failures = check()
    if failures:
        print(f"{len(failures)} inert/deleted-API claim(s) found:\n", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nEach of these tells a reader (or an AI agent) to use something that "
            "does not do what it says. See #2679 / #2680 / #2656.",
            file=sys.stderr,
        )
        return 1
    print("OK — no docs or code prescribe a deleted or inert API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
