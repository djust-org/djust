#!/usr/bin/env python3
"""Resolve the checkable references in ``changelog.d/`` fragments against the tree.

Issue #2849: a fragment citing a test class name, file path, or count that
does not exist is exactly as checkable as a doc reference, and five shipped
PRs carried such claims with nothing catching them. This implements the
mechanical subset the issue bounds — for every pending fragment:

  1. A backtick-quoted file path (``foo/bar.py``, ``tests/js/x.test.js``,
     ``core-concepts/events.md`` …) must resolve in the tree. Bases, in
     order: the repo root, ``docs/`` and ``docs/website/`` (fragments cite
     doc pages in both root-relative and website-relative shorthand), and
     ``python/djust/`` (module-relative paths in prose).
  2. A backtick-quoted test class name (``TestFoo``) must exist as
     ``class TestFoo`` in the repo's Python test trees.

Out of scope, deliberately:

  * Counts (``N regression cases in `<file>` ``) — already checked by
    ``scripts/check-changelog-test-counts.py``, which scans fragments too;
    sharing its parser rather than adding a third one is the issue's own
    instruction, and the check already exists.
  * Arbitrary prose claims (categorical statements, PR bodies) — not
    mechanically checkable; #2849's rule for those is "open the file and
    cite the path; a claim that cannot be checked must not be written".
  * ``changelog.d/README.md`` — instructions, not a claim about the tree.

Exits 0 when every reference resolves; exits 1 naming the fragment, line,
and unresolved reference.

Run: python scripts/check-changelog-fragment-references.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIR = REPO_ROOT / "changelog.d"

# Only tokens that cannot be anything but a path are checked: a slash and a
# known file extension. Bare filenames (``manage.py``) and symbol references
# (``ViewRuntime.dispatch_mount``) are NOT paths and stay unchecked here —
# the latter would need the #2652-style class registry, which is per-doc and
# hand-maintained, not general.
_ALLOWED_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".md",
        ".ts",
        ".toml",
        ".rs",
        ".json",
        ".html",
        ".yml",
        ".yaml",
        ".cfg",
        ".txt",
        ".sh",
    }
)
_RESOLVE_BASES = ("", "docs", "docs/website", "python/djust")

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
# A test-class reference: TestFoo — Test followed by an uppercase letter and
# word characters, nothing else in the token (no slashes, parens, dots).
_TEST_CLASS_RE = re.compile(r"^Test[A-Z]\w*$")
# Where a ``class TestFoo`` definition may live.
_CLASS_DEF_DIRS = ("python", "tests")
_CLASS_DEF_RE = re.compile(r"^class\s+(Test\w+)\b", re.MULTILINE)


@dataclass(frozen=True)
class Finding:
    fragment: str
    line_no: int
    reference: str
    reason: str

    def __str__(self) -> str:
        return f"{self.fragment}:{self.line_no}: `{self.reference}` — {self.reason}"


def _path_resolves(repo_root: Path, token: str) -> bool:
    return any((repo_root / base / token).exists() for base in _RESOLVE_BASES)


def _defined_test_classes(repo_root: Path) -> "set[str]":
    names: set[str] = set()
    for base in _CLASS_DEF_DIRS:
        root = repo_root / base
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            names.update(_CLASS_DEF_RE.findall(text))
    return names


def check_fragments(repo_root: Path, fragments: "list[Path]") -> int:
    findings: list[Finding] = []
    class_index: "set[str] | None" = None  # built lazily, only if needed

    for fragment in fragments:
        text = fragment.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for token in _BACKTICK_RE.findall(line):
                token = token.strip()
                if "/" in token and token.endswith(tuple(_ALLOWED_SUFFIXES)):
                    if not _path_resolves(repo_root, token):
                        findings.append(
                            Finding(
                                fragment.name,
                                line_no,
                                token,
                                "path does not resolve in the tree "
                                f"(tried: {', '.join(b or '.' for b in _RESOLVE_BASES)})",
                            )
                        )
                elif _TEST_CLASS_RE.match(token):
                    if class_index is None:
                        class_index = _defined_test_classes(repo_root)
                    if token not in class_index:
                        findings.append(
                            Finding(
                                fragment.name,
                                line_no,
                                token,
                                "no `class <name>` definition in python/ or tests/",
                            )
                        )

    if findings:
        print("changelog.d fragment reference check FAILED:", file=sys.stderr)
        for f in findings:
            print(f, file=sys.stderr)
        print(
            f"\n{len(findings)} unresolved reference(s) — a fragment claim that "
            "names a path or test class must match the tree (#2849).",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    fragments = sorted(p for p in FRAGMENT_DIR.glob("*.md") if p.name != "README.md")
    if not fragments:
        return 0  # nothing to check
    return check_fragments(REPO_ROOT, fragments)


if __name__ == "__main__":
    raise SystemExit(main())
