#!/usr/bin/env python3
"""Audit recent merged PRs for missing pipeline-run Stage 14 retros.

Filed as #1212 (v0.9.5 retro Action Tracker #193). The pipeline-run skill
has a "MANDATORY retro-artifact gate" that should fire before
``completed_at`` is set on the state file. But two PRs in v0.9.5 (#1203,
#1204) merged WITHOUT retro comments — the gate didn't fire, likely
because pipeline-run wasn't used for those merges at all.

This script catches that failure mode after-the-fact: scan recent merged
PRs, find any that lack retro markers in their comments, and report.
The complementary "before-the-fact" CI check (a scheduled GitHub Action
that flags merged PRs without retros within 24 hours of merge) is
deferred to a follow-up; this script is the one-time audit + reusable
diagnostic.

Usage:

    python scripts/audit-pipeline-bypass.py
    python scripts/audit-pipeline-bypass.py --since 1175
    python scripts/audit-pipeline-bypass.py --limit 100

Flags PRs as a "potential bypass" if their comments contain none of the
retro markers (``Retrospective``, ``Quality:``, ``Lessons learned``,
``RETRO_COMPLETE``, ``what went well``).

Prints a summary table; does NOT auto-close, auto-comment, or otherwise
mutate state. Manual triage is the right shape for retro backfill.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

RETRO_MARKERS = re.compile(
    r"retrospective|quality:\s*\d|lessons\s+learned|retro_complete|what\s+went\s+well",
    re.IGNORECASE,
)


def gh(args: list[str]) -> str:
    """Run gh, return stdout (empty on failure)."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def merged_prs(since: int | None, limit: int) -> list[dict]:
    """Return merged PRs (most recent first), optionally filtered by since."""
    raw = gh(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,mergedAt,author",
        ]
    )
    if not raw:
        return []
    prs = json.loads(raw)
    if since is not None:
        prs = [p for p in prs if p["number"] >= since]
    return prs


def has_retro(pr_number: int) -> tuple[bool, int]:
    """Return ``(has_retro, comment_count)`` for a PR.

    A retro is detected when at least one user comment matches the
    RETRO_MARKERS regex. Bot comments (github-actions, dependabot) are
    excluded from the marker scan; their comment count is included in
    the total for context.
    """
    raw = gh(["pr", "view", str(pr_number), "--json", "comments"])
    if not raw:
        return (False, 0)
    data = json.loads(raw)
    comments = data.get("comments", []) or []
    user_comments = [
        c
        for c in comments
        if c.get("author", {}).get("login", "") not in {"github-actions"}
        and not c.get("author", {}).get("login", "").startswith("dependabot")
    ]
    combined = "\n".join(c.get("body", "") for c in user_comments)
    return (bool(RETRO_MARKERS.search(combined)), len(comments))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="Only audit PRs with number >= this value.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max number of merged PRs to scan (default: 50, recent first).",
    )
    parser.add_argument(
        "--dependabot",
        action="store_true",
        help=(
            "Include dependabot-authored PRs (default: excluded; they're "
            "automated bumps that don't need retros)."
        ),
    )
    args = parser.parse_args()

    prs = merged_prs(args.since, args.limit)
    if not prs:
        print("No merged PRs found (gh authentication issue or empty repo).")
        return 0

    if not args.dependabot:
        prs = [p for p in prs if not p.get("author", {}).get("login", "").startswith("dependabot")]

    bypassed: list[tuple[int, str, str, int]] = []
    for pr in prs:
        n = pr["number"]
        retro, comment_count = has_retro(n)
        if not retro:
            bypassed.append((n, pr["mergedAt"][:10], pr["title"], comment_count))

    print(f"Audited {len(prs)} merged PRs (excluding dependabot).")
    print(f"Found {len(bypassed)} PRs without retro markers in comments.\n")

    if not bypassed:
        print("All audited PRs have retros.")
        return 0

    print("PRs without retro markers (oldest first):")
    print(f"  {'PR':<6} {'merged':<12} {'comments':<9}  title")
    print(f"  {'-' * 6} {'-' * 12} {'-' * 9}  {'-' * 60}")
    for n, merged, title, count in sorted(bypassed):
        title_short = title[:60]
        print(f"  #{n:<5} {merged:<12} {count:<9}  {title_short}")

    print()
    print("Next step: post a backfill retro comment on each PR via")
    print('  gh pr comment <N> --body "$(cat <retro-file>)"')
    print()
    print("If the PR's pipeline-state file exists with completed_at=null,")
    print("the gate could have caught this. If no state file exists, the")
    print("PR was merged outside pipeline-run entirely — file as a")
    print("'pipeline-bypass merge' for milestone-retro tracking.")
    print()
    return 1 if bypassed else 0


if __name__ == "__main__":
    sys.exit(main())
