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

MERGE QUEUE: for ``merge_group`` the change set is the UNION of ``base_sha..head_sha`` and
``merge-base(head_sha, origin/<base_ref>)..head_sha``, and both lists are logged. The payload's
``base_sha`` is not verified to mean "the target branch" for a grouped queue, so the result must
not depend on it (a [code PR, docs PR] group could otherwise read as docs-only). A union can only
add files, so it can only make a run bigger.

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


def _diff(base: str, head: str) -> list[str]:
    out = _git("diff", "--name-only", "--no-renames", "-z", base, head)
    return [f for f in out.split("\0") if f]


def branch_name(base_ref: str) -> str:
    """``refs/heads/main`` -> ``main`` (the merge_group payload carries the full ref)."""
    name = base_ref.strip()
    for prefix in ("refs/heads/", "refs/remotes/origin/", "origin/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
    return name


def merge_group_diffs(base: str, head: str, base_ref: str) -> tuple[list[str], list[str]]:
    """The two views of a merge-queue entry's change set, both against ``head``.

    The meaning of ``merge_group.base_sha`` is not verified: for a stacked or grouped queue it
    could be the group's own parent, so ``base_sha..head_sha`` would cover only the last PR
    (a [code PR, docs PR] group would read as docs-only). So the result never depends on it:

    * ``by_base_sha``: ``base_sha..head_sha``, as the payload states it;
    * ``by_merge_base``: ``merge-base(head_sha, origin/<base_ref>)..head_sha``, everything the
      entry adds to the target branch, whatever ``base_sha`` means.

    The caller classifies their UNION. A missing ``base_ref`` or an unfetched branch raises, and
    ``main`` turns that into a full run.
    """
    name = branch_name(base_ref)
    if not name:
        raise ValueError("merge_group has no base_ref")
    merge_base = _git("merge-base", head, f"origin/{name}").strip()
    return _diff(base, head), _diff(merge_base, head)


def pull_request_files(base: str, head: str) -> list[str]:
    """What the PR changes: the diff from the merge-base, not from the moved-on base tip.

    ``--no-renames`` (in ``_diff``) so a rename lists BOTH the old and the new path: moving a
    source file into ``docs/`` must not read as a docs-only change.
    """
    return _diff(_git("merge-base", base, head).strip(), head)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", required=True, help="pull_request or merge_group")
    parser.add_argument("--base", default="", help="base sha (PR base, or merge-group base)")
    parser.add_argument("--head", default="", help="head sha (PR head, or merge-group head)")
    parser.add_argument(
        "--base-ref", default="", help="merge_group only: the target branch (payload base_ref)"
    )
    args = parser.parse_args(argv)

    scope = FULL_RUN
    try:
        if args.base and args.head:
            if args.event == "merge_group":
                # Log BOTH views so the first real queue run shows what base_sha meant.
                by_base_sha, by_merge_base = merge_group_diffs(args.base, args.head, args.base_ref)
                for label, listed in (
                    (f"base_sha {args.base[:12]}..head_sha {args.head[:12]}", by_base_sha),
                    (
                        f"merge-base with origin/{branch_name(args.base_ref)}..head_sha",
                        by_merge_base,
                    ),
                ):
                    print(f"{len(listed)} file(s) by {label}:")
                    for f in listed:
                        print(f"  {f}")
                files = sorted(set(by_base_sha) | set(by_merge_base))
                if set(by_base_sha) != set(by_merge_base):
                    print("::notice::the two merge_group diffs differ -- classifying their union")
            elif args.event == "pull_request":
                files = pull_request_files(args.base, args.head)
            else:
                raise ValueError(f"unscoped event {args.event!r}")
            print(f"{len(files)} changed file(s) classified:")
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
