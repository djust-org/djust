#!/usr/bin/env python3
"""Refuse to tag a release on a commit its release line's branch does not contain (#3149).

``make release`` tags ``HEAD``. The tag must stay reachable from the branch
the release ships from, because ``scripts/check-changelog-tagged-sections.py``
enumerates release tags with ``git tag --merged HEAD``: a tag the branch
cannot reach is invisible to it, and on ``main`` two tests in
``tests/test_changelog_tagged_sections.py`` go red and the pre-push hook
refuses every push from a branch cut from ``main``.

The way it happened, twice (v1.3.0rc1, v1.3.0rc3): the release was tagged on
``release/X`` and the release PR (#3131) was then **squash**-merged. A squash
commit has a different hash from the tagged commit, so the tag never became an
ancestor of ``main``; #3135 had to merge the tagged commit back in.

The rule this script enforces: tag *after* the release PR has landed, on the
branch it landed on. Concretely:

1. the current branch is ``main`` or an ``X.Y`` maintenance branch — not
   ``release/*``, whose commits a squash merge discards; and
2. ``HEAD`` is an ancestor of that branch on the remote, i.e. what is being
   tagged has already been pushed to (merged into) the release line.

Publishing is unaffected: ``.github/workflows/release.yml`` runs on the tag
push and builds the tagged commit, whose tree is the release PR's tree.

Usage:
    python3 scripts/check-release-tag-target.py [--remote origin]

Exit code:
    0 -- HEAD is a valid tag target
    1 -- it is not (the message names the fix)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

#: ``main`` or an ``X.Y`` maintenance branch (``1.2``, ``10.11``).
_RELEASE_LINE_RE = re.compile(r"^(main|\d+\.\d+)$")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def check(remote: str = "origin") -> tuple[bool, str]:
    """``(ok, message)`` for tagging the current ``HEAD``."""
    branch = _git("branch", "--show-current").stdout.strip()
    if not _RELEASE_LINE_RE.match(branch):
        return False, (
            "Tag releases on main or an X.Y maintenance branch, after the release PR "
            "has merged (got %r). A tag on release/* is lost when the PR is "
            "squash-merged: the tag never becomes reachable from main (#3149, "
            "v1.3.0rc3 / #3131). Merge the release PR, then: git switch main && "
            "git pull && make release VERSION=..." % (branch or "a detached HEAD")
        )

    fetched = _git("fetch", "-q", remote, "refs/heads/%s" % branch)
    if fetched.returncode != 0:
        return False, "Could not fetch %s/%s: %s" % (remote, branch, fetched.stderr.strip())

    if _git("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD").returncode != 0:
        return False, (
            "HEAD is not on %s/%s. Tag the commit the release PR landed as: push or "
            "merge it first, then git pull and re-run (#3149)." % (remote, branch)
        )
    return True, "HEAD is on %s/%s; a tag here stays reachable from %s." % (remote, branch, branch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)
    ok, message = check(args.remote)
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
