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

The bar is mechanical, because the thing that failed was a human claim of
completeness (#1859: a pin nobody can drift past beats a promise). A file may
teach `@client_state` all it likes — it must just also say that it is inert,
somewhere a reader of THAT mention will see.

"Somewhere a reader will see" was originally the whole FILE, and #2692 showed
that is too loose: deleting only the `@client_state` caveat in
`docs/BEST_PRACTICES_AI.md` left the checker green, because `@debounce`'s
marker 28 lines earlier — inside the same code fence — satisfied the file.
That is exactly the #2680 shape the checker exists to catch: siblings labelled
inert, this one not, and the contrast actively inviting the inference that
this one works. So the marker is now scoped to the mention:

  * a **header banner** — a marker in the file's opening construct (the
    markdown preamble before the first `##` or fence, a module docstring, a
    leading template/HTML/JS/YAML comment) discharges the whole file, because
    a reader meets it before any example; or
  * a **block-local** marker — one inside the mention's own block, i.e. the
    maximal run of consecutive non-blank lines containing it.

A marker that is neither discharges nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories worth scanning: what a human or an AI agent reads as instruction.
# `tests` and `scripts` are in the list because a test or a helper script is
# read as a worked example just as readily as a doc is (#2692).
SCAN_DIRS = ["docs", "python/djust", "python/tests", ".semgrep", "examples", "scripts", "tests"]
SCAN_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".html", ".txt", ".js"}

# Every repo-root `.md` is scanned too — the original list named only
# `README.md`/`llms.txt`, so `QUICKSTART.md` could have reintroduced a
# `djustSecurity.` call with nothing to catch it (#2692).
ROOT_EXTRA_FILES = ("llms.txt",)

# This file explains the rules, and the changelog / CONTRIBUTING / retro
# records the deletions in prose. Their mentions are the audit trail, not
# prescriptions.
EXEMPT = {
    "scripts/check-inert-api-claims.py",
    "python/djust/tests/test_inert_api_claims.py",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "RETRO.md",
    "ROADMAP.md",
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


def _files(root: Path = ROOT) -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in SCAN_SUFFIXES and "__pycache__" not in p.parts:
                out.append(p)
    for p in root.glob("*.md"):
        if p.is_file():
            out.append(p)
    for name in ROOT_EXTRA_FILES:
        p = root / name
        if p.is_file():
            out.append(p)
    return sorted(set(out))


# A markdown heading of depth >= 2, i.e. the end of the document preamble.
_MD_SUBHEADING = re.compile(r"^\s{0,3}#{2,6}\s")
# A leading `"""`/`'''` module docstring opener, prefixes and all.
_PY_DOCSTRING_OPEN = re.compile(r"""^\s*(?:[rubRUBfF]{0,2})("{3}|'{3})""")
# The opening delimiter of a leading comment, per family.
_COMMENT_OPEN = {
    ".html": ("{#", "<!--"),
    ".js": ("/*", "//"),
    ".yaml": ("#",),
    ".yml": ("#",),
}
_COMMENT_CLOSE = {"{#": "#}", "<!--": "-->", "/*": "*/"}


def _header_zone(text: str, suffix: str) -> int:
    """How many leading lines count as the file's header banner.

    A marker there discharges the whole file: it is the title block, the module
    docstring, or the leading comment — a reader meets it before any example.
    Anything below it only speaks for its own block (#2692).
    """
    lines = text.splitlines()
    if suffix == ".py":
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            m = _PY_DOCSTRING_OPEN.match(ln)
            if not m:
                return 0  # no module docstring — no banner zone
            quote = m.group(1)
            if quote in ln[m.end() :]:
                return i + 1
            for j in range(i + 1, len(lines)):
                if quote in lines[j]:
                    return j + 1
            return 0
        return 0
    if suffix in {".md", ".txt"}:
        for i, ln in enumerate(lines):
            if _MD_SUBHEADING.match(ln) or ln.lstrip().startswith("```"):
                return i
        return len(lines)
    openers = _COMMENT_OPEN.get(suffix)
    if not openers:
        return 0
    zone = 0
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        opener = next((o for o in openers if stripped.startswith(o)), None)
        if opener is None:
            break
        closer = _COMMENT_CLOSE.get(opener)
        if closer is None:  # a `//` or `#` line comment
            zone = i + 1
            i += 1
            continue
        if closer in stripped[len(opener) :]:
            zone = i + 1
            i += 1
            continue
        for j in range(i + 1, len(lines)):
            if closer in lines[j]:
                zone = j + 1
                i = j + 1
                break
        else:
            return zone
    return zone


def _block_bounds(lines: list[str], lineno: int) -> tuple[int, int]:
    """The maximal run of consecutive non-blank lines containing `lineno` (1-based)."""
    start = end = lineno - 1
    while start > 0 and lines[start - 1].strip():
        start -= 1
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    return start + 1, end + 1


def check(root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
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

        if not INERT_DECORATOR_USE.search(text):
            continue
        lines = text.splitlines()
        banner = _header_zone(text, path.suffix)
        if any(INERT_MARKER.search(ln) for ln in lines[:banner]):
            continue  # the file opens by saying it is inert — every mention is covered
        for lineno, line in enumerate(lines, 1):
            if not INERT_DECORATOR_USE.search(line):
                continue
            start, end = _block_bounds(lines, lineno)
            if any(INERT_MARKER.search(lines[k - 1]) for k in range(start, end + 1)):
                continue
            failures.append(
                f"{rel}:{lineno}: teaches `@client_state` with no marker on this line or "
                f"anywhere in its block (lines {start}-{end}), and no header banner. It "
                f"stamps metadata nothing in the shipped client reads (#2656) — a handler "
                f"decorated with it behaves exactly like an undecorated one. A marker "
                f"elsewhere in the file does NOT cover this mention (#2692): add one "
                f"mentioning #2656 (or the word INERT) here.\n    {line.strip()}"
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
