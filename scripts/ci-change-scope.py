#!/usr/bin/env python3
"""Decide which Tests-workflow jobs a change set may skip (merge-queue + path scoping).

The Tests workflow (`.github/workflows/test.yml`) runs a `changes` job first, which calls
this script on `pull_request` and `merge_group` events and exposes two outputs:

* ``docs_only`` -- every changed file is documentation or a changelog fragment. Every
  expensive job is skipped and the ``test-summary`` gate reports success for it.
* ``rust_skip`` -- every changed file is documentation, or a Python test module under one
  of the three test roots. ``rust-tests`` (cargo test / clippy / fmt) is skipped. It is the
  only job a test-only change may skip: the Python matrix, JavaScript, benchmarks, the
  browser jobs and the rest still run.

THE RULE TABLE (the single source; docs/testing-strategy.md and the workflow comment quote
it, and tests/test_ci_change_scope.py pins all three together):

    DOCS      CHANGELOG.md, RETRO.md and every other root-level ``*.md``;
              everything under ``docs/``; everything under ``changelog.d/``.
    TEST_PY   ``*.py`` under ``tests/``, ``python/tests/`` or ``python/djust/tests/``.

    docs_only  = the change set is non-empty and every file is DOCS
    rust_skip  = the change set is non-empty and every file is DOCS or TEST_PY

Everything else -- workflow files, ``scripts/``, ``pyproject.toml``, ``Cargo.*``,
``crates/``, ``python/djust/`` source, ``.test_durations``, ``uv.lock``, a nested ``*.md``
such as ``python/djust/foo/README.md``, a fixture next to the tests -- is in neither class,
so ANY such file turns both outputs off and the full workflow runs.

FAIL-OPEN, ON PURPOSE: any doubt (an empty change set, a git error, an unexpected path,
no base/head sha) yields ``false``/``false``, i.e. the full run. A wrong skip is worse than
a slow run. The script never exits non-zero for a classification problem; if the job that
runs it fails outright, its dependants are skipped, which the ``test-summary`` gate reads as
a failure (it only accepts a skip that ``changes`` vouched for).

Why TEST_PY is ``.py`` only: ``python/tests/fixtures/*.json`` is ``include_str!``-ed by a
Rust test (``crates/djust_live/src/lib.rs``), so a test-tree file that is not a Python module
can reach the Rust build.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass

DOCS_DIRS = ("docs/", "changelog.d/")
TEST_ROOTS = ("tests/", "python/tests/", "python/djust/tests/")


def is_docs(path: str) -> bool:
    """Documentation or a changelog fragment (see the rule table)."""
    if path.startswith(DOCS_DIRS):
        return True
    return "/" not in path and path.endswith(".md")


def is_test_py(path: str) -> bool:
    """A Python module under one of the test roots (see the rule table)."""
    return path.endswith(".py") and path.startswith(TEST_ROOTS)


def _suspicious(path: str) -> bool:
    # Anything that is not a plain repo-relative path is never vouched for.
    return (
        not path
        or path.startswith("/")
        or any(part in ("", ".", "..") for part in path.split("/"))
        or "\\" in path
    )


@dataclass(frozen=True)
class Scope:
    docs_only: bool
    rust_skip: bool

    def lines(self) -> list[str]:
        return [
            f"docs_only={str(self.docs_only).lower()}",
            f"rust_skip={str(self.rust_skip).lower()}",
        ]


FULL_RUN = Scope(docs_only=False, rust_skip=False)


def classify(files: list[str]) -> Scope:
    """The whole decision. Pure, so the rule table is unit-testable."""
    files = [f for f in files if f != ""]
    if not files or any(_suspicious(f) for f in files):
        return FULL_RUN
    docs_only = all(is_docs(f) for f in files)
    rust_skip = all(is_docs(f) or is_test_py(f) for f in files)
    return Scope(docs_only=docs_only, rust_skip=rust_skip)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def changed_files(event: str, base: str, head: str) -> list[str]:
    """The files this event's change set touches.

    ``--no-renames`` so a rename lists BOTH the old and the new path: moving a source file
    into ``docs/`` must not read as a docs-only change.
    """
    if event == "pull_request":
        # The merge-base, so the comparison is what the PR changes and not everything
        # main gained since the branch point (the shape the docs shim already uses).
        base = _git("merge-base", base, head).strip()
    elif event != "merge_group":
        raise ValueError(f"unscoped event {event!r}")
    out = _git("diff", "--name-only", "--no-renames", "-z", base, head)
    return [f for f in out.split("\0") if f]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", required=True, help="pull_request or merge_group")
    parser.add_argument("--base", default="", help="base sha (PR base, or merge-group base)")
    parser.add_argument("--head", default="", help="head sha (PR head, or merge-group head)")
    args = parser.parse_args(argv)

    scope = FULL_RUN
    try:
        if args.base and args.head:
            files = changed_files(args.event, args.base, args.head)
            print(f"{len(files)} changed file(s):")
            for f in files:
                print(f"  {f}")
            scope = classify(files)
        else:
            print("::warning::no base/head sha -- running the full workflow")
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        print(f"::warning::could not list the changed files ({exc}) -- running the full workflow")
        scope = FULL_RUN

    for line in scope.lines():
        print(line)
    sink = os.environ.get("GITHUB_OUTPUT")
    if sink:
        with open(sink, "a", encoding="utf-8") as fh:
            fh.write("\n".join(scope.lines()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
