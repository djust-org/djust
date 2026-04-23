#!/usr/bin/env python3
"""Validate test-count phrases in CHANGELOG.md ``[Unreleased]`` against reality.

Scans the ``[Unreleased]`` section of ``CHANGELOG.md`` and, for each phrase
like ``N JSDOM cases``, ``N regression tests``, ``N test cases``,
``N unit tests``, or ``N parameterized cases`` that also names a concrete
test file path (``tests/js/<name>.test.js`` or
``python/djust/tests/<name>.py`` or ``tests/unit/<name>.py`` etc.), counts
the actual tests in that file and compares.

Exits 0 on match or when there's nothing to check.
Exits 1 with a diff report on mismatch.

Usage::

    python scripts/check-changelog-test-counts.py [path/to/CHANGELOG.md]

Closes #908.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# Phrases we recognize as claims of a test count.
# Note: "new" / "additional" signal a DELTA, not a total — we skip those
# because counting "added in this release" requires git-blame-level analysis.
# The validator only checks phrases that assert a current-total count of
# tests in a named file.
COUNT_PHRASE_RE = re.compile(
    r"(?<!\w)(?P<count>\d+)\s+"
    r"(?P<kind>JSDOM\s+cases|JSDOM\s+regression\s+cases|"
    r"regression\s+tests|regression\s+cases|"
    r"test\s+cases|unit\s+tests|parameterized\s+cases)",
    re.IGNORECASE,
)
# Words that, if they appear immediately before a matched count phrase, mark
# the phrase as a delta ("2 new cases", "3 additional tests") rather than a
# total. We skip these because deltas can't be checked by simple file scan.
DELTA_PREFIX_RE = re.compile(
    r"\b(?:new|additional|more|extra|added)\s+$",
    re.IGNORECASE,
)

# File-path references inside CHANGELOG prose (we allow single backticks).
PY_TEST_PATH_RE = re.compile(
    r"`((?:python/djust|tests|python)/[^\s`]*?tests?[^\s`]*?\.py)`"
)
JS_TEST_PATH_RE = re.compile(r"`(tests/js/[^\s`]+?\.test\.js)`")

# Count test functions inside Python files. Matches top-level and class-level.
PY_TEST_FN_RE = re.compile(r"^[ \t]*def\s+test_\w+\s*\(", re.MULTILINE)
# Python pytest.mark.parametrize adds a case per tuple; count via rough sum
# of ``parametrize(...)`` argument list lengths is out of scope here — a
# ``test_*`` function is counted as ONE test, which mirrors ``pytest --collect-only -q``
# at the nodeid level per-function (not per-param). If a CHANGELOG entry says
# "parameterized cases" we still just count functions, which is the common
# manual-count convention used in djust CHANGELOG entries today.

# Count JS test/it() calls. Matches ``it(``, ``test(``, ``it.only(``, etc.
# Deliberately loose — close enough for CHANGELOG sanity.
JS_TEST_FN_RE = re.compile(
    r"(?:^|[\s;{])(?:it|test)(?:\.only|\.skip)?\s*\(",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Mismatch:
    file: str
    claimed: int
    actual: int
    phrase: str
    source_line: int

    def format(self) -> str:
        delta = self.actual - self.claimed
        sign = "+" if delta > 0 else ""
        return (
            f"  {self.file}: CHANGELOG says {self.claimed} ({self.phrase!r}) "
            f"but file has {self.actual} ({sign}{delta}) "
            f"[CHANGELOG.md line {self.source_line}]"
        )


def extract_unreleased(changelog: str) -> tuple[str, int]:
    """Return the ``[Unreleased]`` section body and its start-line (1-based)."""
    lines = changelog.splitlines()
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s*\[Unreleased\]", line, re.IGNORECASE):
            start_idx = i + 1
            break
    if start_idx is None:
        return "", 0
    end_idx = len(lines)
    for j in range(start_idx, len(lines)):
        # next version heading => end of unreleased
        if re.match(r"^##\s+\[[^\]]+\]", lines[j]):
            end_idx = j
            break
    section_lines = lines[start_idx:end_idx]
    return "\n".join(section_lines), start_idx + 1  # +1 => 1-based


def count_tests_in_file(path: Path) -> int | None:
    """Return the number of test cases/functions in *path*, or None if unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if path.suffix == ".py":
        return len(PY_TEST_FN_RE.findall(text))
    if path.name.endswith(".test.js") or path.suffix == ".js":
        return len(JS_TEST_FN_RE.findall(text))
    return None


def find_mismatches(
    unreleased_body: str,
    body_start_line: int,
    repo_root: Path,
) -> list[Mismatch]:
    """Walk the [Unreleased] body and return any (claim vs. reality) mismatches."""
    mismatches: list[Mismatch] = []

    # Slice the body into bullet "chunks" so a phrase and its file reference
    # stay connected even across hard-wrapped lines. A chunk starts at a line
    # that begins with "- " (possibly with leading whitespace) and runs to the
    # next such line.
    lines = unreleased_body.splitlines()
    chunks: list[tuple[int, list[str]]] = []  # (line_offset_1based, lines)
    cur: list[str] = []
    cur_start = 0
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s+", line):
            if cur:
                chunks.append((cur_start, cur))
            cur = [line]
            cur_start = i
        else:
            if cur:
                cur.append(line)
    if cur:
        chunks.append((cur_start, cur))

    for offset, chunk_lines in chunks:
        chunk = "\n".join(chunk_lines)
        counts = list(COUNT_PHRASE_RE.finditer(chunk))
        if not counts:
            continue
        py_paths = PY_TEST_PATH_RE.findall(chunk)
        js_paths = JS_TEST_PATH_RE.findall(chunk)

        # Heuristic: if the chunk mentions JSDOM, prefer js paths; otherwise
        # prefer py paths. If both exist, check whichever matches the phrase's
        # "kind" keyword.
        for m in counts:
            # Skip deltas: "2 new cases" cannot be validated without git
            # history. Only check phrases that assert a file total.
            prefix = chunk[max(0, m.start() - 30) : m.start()]
            if DELTA_PREFIX_RE.search(prefix):
                continue
            claimed = int(m.group("count"))
            kind = m.group("kind").lower()
            phrase = m.group(0)
            if "jsdom" in kind and js_paths:
                candidates = js_paths
            elif "unit" in kind and py_paths:
                candidates = py_paths
            elif py_paths:
                candidates = py_paths
            elif js_paths:
                candidates = js_paths
            else:
                continue
            # Sum counts across all candidate files in the chunk. If the
            # CHANGELOG lists multiple files, we assume the stated count
            # covers all of them — this matches the existing convention
            # ("12 regression tests across foo.py, bar.py, baz.py").
            total = 0
            unresolved = False
            for rel in candidates:
                actual = count_tests_in_file(repo_root / rel)
                if actual is None:
                    unresolved = True
                    break
                total += actual
            if unresolved:
                continue
            if total != claimed:
                src_line = body_start_line + offset
                mismatches.append(
                    Mismatch(
                        file=", ".join(candidates),
                        claimed=claimed,
                        actual=total,
                        phrase=phrase,
                        source_line=src_line,
                    )
                )
    return mismatches


def main(argv: list[str]) -> int:
    changelog_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_CHANGELOG
    if not changelog_path.exists():
        print(f"CHANGELOG not found at {changelog_path}", file=sys.stderr)
        return 1
    repo_root = changelog_path.resolve().parent
    text = changelog_path.read_text(encoding="utf-8")
    body, body_start_line = extract_unreleased(text)
    if not body.strip():
        # No Unreleased section or it's empty — nothing to validate.
        return 0
    mismatches = find_mismatches(body, body_start_line, repo_root)
    if not mismatches:
        return 0
    print(
        "CHANGELOG test-count drift — [Unreleased] claims do not match test files:\n",
        file=sys.stderr,
    )
    for mm in mismatches:
        print(mm.format(), file=sys.stderr)
    print(
        "\nFix: update CHANGELOG.md to match the actual test count, "
        "or add the missing tests.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
