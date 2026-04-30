#!/usr/bin/env python3
"""Check for newly-introduced unused private methods in python/djust/.

Run as a pre-push hook. Scans the current branch's diff (vs ``origin/main``)
for newly-added ``def _name(self, ...):`` private method definitions on
classes; for each, greps the entire ``python/djust/`` and ``tests/`` trees
for callers. Fails if any newly-added private method has zero callers
anywhere — that's the failure mode that produced the dead-code
``_lazy_serialize_context`` cited in PR #1206 / #1205 (closed by PR #1216
v0.9.5 retro Action Tracker #197 / GitHub #1209).

Pre-existing dead methods are intentionally NOT flagged — only methods
added (or modified) in the current branch. This avoids forcing a 50-method
cleanup PR before any other PR can ship.

Skips:

- Dunder methods (``__init__``, ``__call__``, etc.) — implicit framework
  contracts; never directly called by name.
- Methods inside ``tests/`` and ``__pycache__/`` directories — test fixtures
  and bytecode.
- Methods whose ``def`` line carries ``# noqa: dead-method-allowed`` —
  explicit escape hatch for reflection-called methods.

Exit codes:

- ``0``: no newly-added dead private methods (pass).
- ``1``: at least one newly-added private method has no callers (fail with
  a report listing offenders).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
PRIVATE_METHOD_DEF_RE = re.compile(
    r"^\s*def\s+(_[a-z][a-z0-9_]*)\s*\(\s*self",
    re.MULTILINE,
)
DUNDER_RE = re.compile(r"^__\w+__$")
NOQA_RE = re.compile(r"#\s*noqa:\s*dead-method-allowed", re.IGNORECASE)


def _git_output(args: list[str]) -> str:
    """Run a git command from PROJECT_ROOT, return stdout (empty on failure)."""
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def changed_python_files() -> list[Path]:
    """Files in ``python/djust/`` modified in the current branch vs origin/main."""
    output = _git_output(["diff", "--name-only", "origin/main...HEAD", "--", "python/djust/"])
    files: list[Path] = []
    for line in output.strip().splitlines():
        if not line.endswith(".py"):
            continue
        if "tests/" in line or "__pycache__" in line:
            continue
        path = PROJECT_ROOT / line
        if path.exists():
            files.append(path)
    return files


def newly_added_methods(file_path: Path) -> list[tuple[str, str]]:
    """Return ``[(method_name, def_line_with_noqa_check)]`` for methods
    appearing in the branch's added/modified diff for the file.

    The diff is filtered to ``+``-prefixed lines (added/modified), so renames
    and pure deletions don't trip the check.
    """
    rel = file_path.relative_to(PROJECT_ROOT)
    output = _git_output(["diff", "origin/main...HEAD", "--", str(rel)])
    added = "\n".join(
        line[1:]
        for line in output.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )

    methods: list[tuple[str, str]] = []
    for match in PRIVATE_METHOD_DEF_RE.finditer(added):
        name = match.group(1)
        if DUNDER_RE.match(name):
            continue
        # Find the full def-line in the original added text to check noqa.
        line_start = added.rfind("\n", 0, match.start()) + 1
        line_end = added.find("\n", match.end())
        if line_end == -1:
            line_end = len(added)
        def_line = added[line_start:line_end]
        if NOQA_RE.search(def_line):
            continue
        methods.append((name, def_line.strip()))
    return methods


def has_callers(method_name: str, def_file: Path) -> bool:
    """Return True if any non-definition reference to ``method_name`` exists
    in ``python/djust/`` or ``tests/``.

    Looks for: ``.METHOD(``, ``"METHOD"``, ``'METHOD'``. The string-literal
    forms catch ``getattr(self, '_method')`` reflection.
    """
    patterns = [
        rf"\.{re.escape(method_name)}\(",
        rf"['\"]({re.escape(method_name)})['\"]",
    ]
    for pattern in patterns:
        result = subprocess.run(
            [
                "grep",
                "-rE",
                "--include=*.py",
                "--exclude-dir=__pycache__",
                pattern,
                "python/djust/",
                "tests/",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            file_part = line.split(":", 1)[0]
            line_text = line.split(":", 2)[-1]
            same_file = (PROJECT_ROOT / file_part).resolve() == def_file.resolve()
            if same_file and re.search(rf"def\s+{re.escape(method_name)}\s*\(", line_text):
                continue
            return True
    return False


def main() -> int:
    # Best-effort: if origin/main isn't fetched, skip the check rather than
    # block. Pre-push hook runs on local-state-divergence; missing origin
    # ref is operator-environment problem, not a PR-quality problem.
    if not _git_output(["rev-parse", "--verify", "origin/main"]).strip():
        return 0

    files = changed_python_files()
    if not files:
        return 0

    dead: list[tuple[Path, str, str]] = []
    for file_path in files:
        for method, def_line in newly_added_methods(file_path):
            if not has_callers(method, file_path):
                dead.append((file_path, method, def_line))

    if not dead:
        return 0

    print("FAIL: newly-added private method(s) have no callers anywhere:")
    print()
    for file_path, method, def_line in dead:
        rel = file_path.relative_to(PROJECT_ROOT)
        print(f"  {rel}:")
        print(f"      {def_line}")
    print()
    print("This is the failure mode that produced the dead-code")
    print("`_lazy_serialize_context` in PR #1206 / #1205. A private method")
    print("with zero call sites is dead code that misleads future")
    print("investigators when its body resembles a reported symptom.")
    print()
    print("Choose one:")
    print("  (a) wire the method into a real call site (framework hot path)")
    print("  (b) inline the logic at the single use site")
    print("  (c) delete the method")
    print("  (d) annotate the def line with `# noqa: dead-method-allowed`")
    print("      if it's reflection-called (e.g., dispatcher lookup).")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
