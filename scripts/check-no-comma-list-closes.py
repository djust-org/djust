#!/usr/bin/env python3
"""Check for bare comma-list ``Closes #X, #Y`` in branch commit messages.

GitHub's auto-close-keyword parser only matches a closing keyword
(``Closes`` / ``Fixes`` / ``Resolves`` etc.) when it precedes EACH issue
ref. Comma-list shapes like ``Closes #1195, #1196, #1197.`` parse as
ONLY closing #1195 — the first one. The remaining refs stay open and
require manual closure.

This bit twice in 24 hours during the v0.9.1-6 → v0.9.1-7 drain
(PR #1225 closed only #1195 of 6; PR #1226 closed only #1037 of 15;
both required manual stragger closure). Filed as #1227, fixed here.

The check runs as a pre-push hook. Scans every commit in the current
branch (``origin/main..HEAD``) for the failure pattern. Fails if any
commit message has a bare comma-list close-keyword line. Pre-existing
commits on origin/main are not scanned — only branch-local commits the
operator is about to push.

Whitelisted shapes (one ``Closes`` per ref) are accepted:

    Closes #1195.
    Closes #1196.
    ...

OR multiple keywords on one line, also accepted:

    Closes #1195, closes #1196.

The failure pattern is the **bare** comma-list: a single closing keyword
followed by ``#N, #M`` (no second keyword between the refs).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# Closing keywords GitHub recognizes. See:
# https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue
KEYWORDS = ("close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved")
KEYWORD_GROUP = "(?:" + "|".join(KEYWORDS) + ")"

# The failure pattern: a line where:
#   1. A closing keyword precedes the FIRST issue ref (#N).
#   2. After the first ref, comma + optional whitespace + #M (NOT preceded
#      by another closing keyword).
#
# Captures the bad line so we can report it.
BAD_PATTERN = re.compile(
    rf"\b{KEYWORD_GROUP}\b\s+#\d+\s*,\s*#\d+",
    re.IGNORECASE,
)

# Allowlist: a comma-list where EACH ref has its own preceding keyword.
# Example: ``Closes #1, closes #2, closes #3.``
# We don't strictly need to detect this — BAD_PATTERN won't match it
# because the regex requires a comma + #M with no keyword between.


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def branch_commit_messages() -> list[tuple[str, str]]:
    """Return ``[(short_sha, full_message)]`` for each commit in the
    current branch (vs ``origin/main``).
    """
    # If origin/main isn't fetched, exit cleanly (best-effort).
    if not _git(["rev-parse", "--verify", "origin/main"]).strip():
        return []

    log = _git(["log", "origin/main..HEAD", "--format=%h%x00%B%x1e", "--no-merges"])
    commits: list[tuple[str, str]] = []
    # %x1e = record separator
    for record in log.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x00", 1)
        if len(parts) != 2:
            continue
        sha, body = parts[0], parts[1]
        commits.append((sha.strip(), body.strip()))
    return commits


def find_bad_lines(message: str) -> list[str]:
    """Return list of offending lines in a commit message body.

    Skips lines that are inside backtick-quoted code fences (``` ... ```)
    or indented as Markdown code blocks (4+ leading spaces / a tab) — these
    are documentation showing the bad pattern, not actual close-keyword
    invocations. GitHub's auto-close parser ignores them too.

    Also skips lines where the closing keyword appears INSIDE a backtick-
    quoted inline span (e.g. ``the bare ``Closes #X, #Y`` form...``) —
    inline-quoted prose explaining the pattern shouldn't trip the lint.
    """
    bad: list[str] = []
    in_fence = False
    for line in message.splitlines():
        # Toggle code-fence state on lines starting with ``` (allowing
        # optional leading whitespace).
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Skip Markdown indented-code-block lines (4+ leading spaces / tab)
        # but only if they actually look like code (not just deep prose
        # indentation). Heuristic: 4+ spaces at start AND no preceding
        # bullet/number marker on this line.
        if line.startswith("    ") or line.startswith("\t"):
            continue
        # Strip backtick-inline-quoted spans before scanning. ``foo`` and
        # ``Closes #X, #Y`` quoted in prose shouldn't trigger.
        scan_line = re.sub(r"``[^`]*``|`[^`]*`", "", line)
        if BAD_PATTERN.search(scan_line):
            bad.append(line.strip())
    return bad


def main() -> int:
    commits = branch_commit_messages()
    if not commits:
        return 0

    failures: list[tuple[str, list[str]]] = []
    for sha, message in commits:
        bad = find_bad_lines(message)
        if bad:
            failures.append((sha, bad))

    if not failures:
        return 0

    print("FAIL: bare comma-list close-keyword detected in branch commit message(s) (#1227):")
    print()
    for sha, bad_lines in failures:
        print(f"  commit {sha}:")
        for line in bad_lines:
            print(f"    {line!r}")
    print()
    print("GitHub's auto-close parser matches a closing keyword (Closes /")
    print("Fixes / Resolves / etc.) only when it precedes EACH issue ref.")
    print("The comma-list shape `Closes #X, #Y, #Z` closes ONLY #X — the")
    print("rest stay open and require manual closure (this bit PR #1225 +")
    print("PR #1226 in 24 hours; v0.9.1-6 retro tracker #1227).")
    print()
    print("Fix: put each closing keyword on its own line:")
    print()
    print("    Closes #1195.")
    print("    Closes #1196.")
    print("    Closes #1197.")
    print()
    print("Then `git commit --amend` (or interactive rebase if multiple")
    print("commits affected) and push again. See")
    print("docs/PULL_REQUEST_CHECKLIST.md §'Linked Issues' for the canon.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
