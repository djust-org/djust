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

In addition to merged PRs, the script also scans direct-to-main commits
since ``--lookback`` (default ``30 days ago``) and flags any that lack a
``Audit-bypass-reason: ...`` trailer in the commit body. PR-squash
commits (subject ends with ``(#NNN)``) are filtered out automatically.
This catches the bypass shape #1250 was filed for: ROADMAP-update
pushes that skip the PR/retro flow entirely.

Usage:

    python scripts/audit-pipeline-bypass.py
    python scripts/audit-pipeline-bypass.py --since 1175
    python scripts/audit-pipeline-bypass.py --limit 100
    python scripts/audit-pipeline-bypass.py --lookback "60 days ago"

Exit status is part of the interface:

    0  the audit completed and found nothing
    1  the audit completed and found something (``potential bypass`` rows)
    2  the audit could not complete — a required ``gh``/``git`` read failed,
       or an unexpected error. NOT a clean result.

The last stdout line is a machine-readable verdict,
``AUDIT_VERDICT: clean|findings|incomplete``, so a caller does not have to
infer the outcome from the exit code alone. A crash can no longer be
mistaken for findings (both used to exit 1) and a failed read can no longer
be mistaken for clean (it used to exit 0) — see #3129.

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
import traceback
from pathlib import Path

# Make ``scripts.lib.retro_markers`` importable both when this file is
# invoked as ``python scripts/audit-pipeline-bypass.py`` from the repo
# root (in which case ``scripts/`` is on sys.path) and when invoked in
# other ways. Adding the repo root keeps ``scripts.lib.retro_markers``
# resolvable via PEP 420 namespace packages.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.lib.retro_markers import RETRO_MARKER_REGEX  # noqa: E402

RETRO_MARKERS = re.compile(RETRO_MARKER_REGEX, re.IGNORECASE)

#: PR-squash commit subjects end with `` (#NNN)`` (the GitHub squash-merge
#: convention). Direct-to-main commits typically don't carry that suffix.
PR_SQUASH_SUFFIX = re.compile(r"\s\(#\d+\)\s*$")

#: Trailer line in a commit body declaring why a direct-to-main commit is
#: exempt from the retro audit (e.g., ``Audit-bypass-reason:
#: docs-only-roadmap-update``). One match anywhere in the commit body is
#: sufficient.
AUDIT_BYPASS_TRAILER = re.compile(r"^audit-bypass-reason:\s*\S+", re.IGNORECASE | re.MULTILINE)


class AuditIncomplete(RuntimeError):
    """A required data read failed, so the audit cannot reach a verdict.

    Raised instead of returning an empty, clean-looking result: an audit
    that could not read its inputs must never be reported as "no findings"
    (#3129).
    """


def gh(args: list[str]) -> str:
    """Run gh, returning stdout; raise AuditIncomplete if the read fails.

    A failed read is NOT an empty result. Returning "" here (the
    pre-#3129 behaviour) made an authentication failure indistinguishable
    from "no merged PRs" and every PR was then scored as missing a retro,
    while the same failure in the direct-commit scan produced a clean exit.
    """
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AuditIncomplete(
            f"`gh {' '.join(args)}` exited {result.returncode}: "
            f"{(result.stderr or '').strip()[:500]}"
        )
    if not result.stdout.strip():
        raise AuditIncomplete(f"`gh {' '.join(args)}` returned no output")
    return result.stdout


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


def direct_main_commits(lookback: str) -> list[tuple[str, str, str]]:
    """Return direct-to-main commits since ``lookback``, excluding PR squashes.

    Filed as #1250 (v0.9.2-2 retro Action Tracker #208) — the original
    audit only scanned merged PRs, missing the bypass shape where a
    commit is pushed directly to ``main`` without going through PR /
    retro. Examples include the milestone-open commit pattern that the
    pipeline-drain skill instructs.

    Returns a list of ``(sha, subject, body)`` tuples for each candidate
    direct commit. Subjects ending with `` (#NNN)`` are filtered out
    because they're squashed-PR commits whose retros are scanned by
    ``merged_prs`` instead.

    An empty list means the scan ran and matched nothing. A git failure
    raises ``AuditIncomplete`` — an unread scan is not an empty one.
    """
    try:
        # ``--first-parent main`` walks the main-branch line only;
        # ``--no-merges`` keeps merge commits out (the squash workflow
        # produces non-merge commits anyway, but defensive); the
        # ``%x1f`` field separator is unit-separator (0x1F) which is
        # safe inside commit subjects/bodies. ``%x1e`` is record-separator
        # (0x1E) for splitting commits.
        out = subprocess.check_output(
            [
                "git",
                "log",
                "--first-parent",
                "main",
                f"--since={lookback}",
                "--no-merges",
                "--format=%H%x1f%s%x1f%b%x1e",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # A git failure is not "no direct commits": reporting an unread scan
        # as empty is how a broken audit ends up looking clean (#3129).
        raise AuditIncomplete(f"`git log` failed: {exc}") from exc

    commits: list[tuple[str, str, str]] = []
    for record in out.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 2:
            continue
        sha = parts[0].strip()
        subject = parts[1] if len(parts) > 1 else ""
        body = parts[2] if len(parts) > 2 else ""
        # Filter out PR-squash commits: subject ends with " (#NNN)".
        if PR_SQUASH_SUFFIX.search(subject):
            continue
        commits.append((sha, subject, body))
    return commits


def commit_is_exempt(body: str) -> bool:
    """Return True if commit body declares an audit-bypass reason.

    The trailer ``Audit-bypass-reason: <reason>`` (case-insensitive) on
    its own line marks a commit as legitimately exempt — typical use
    case is ROADMAP-update commits the pipeline-drain skill instructs.
    Without an explicit reason, direct commits are flagged.
    """
    return bool(AUDIT_BYPASS_TRAILER.search(body or ""))


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
    parser.add_argument(
        "--lookback",
        type=str,
        default="30 days ago",
        help=(
            "Time window for direct-to-main commit scan (passed to "
            "git log --since; default: '30 days ago'). PR-squash commits "
            "are filtered out automatically."
        ),
    )
    args = parser.parse_args()

    prs = merged_prs(args.since, args.limit)
    if not prs:
        print("No merged PRs matched the scan window.")
        # Fall through to the direct-commit scan. A *failed* read raises
        # above; reaching here means the read succeeded and matched nothing.

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

    # Stage 2: scan direct-to-main commits since lookback (#1250).
    direct = direct_main_commits(args.lookback)
    flagged_direct: list[tuple[str, str]] = []
    for sha, subject, body in direct:
        if not commit_is_exempt(body):
            flagged_direct.append((sha, subject))

    print(
        f"Audited {len(direct)} direct-to-main commits since '{args.lookback}' "
        f"(excluding PR-squash commits)."
    )
    print(
        f"Found {len(flagged_direct)} direct commits without an `Audit-bypass-reason:` trailer.\n"
    )

    if not bypassed and not flagged_direct:
        print("All audited PRs and direct commits have retros / bypass reasons.")
        print("AUDIT_VERDICT: clean")
        return 0

    if bypassed:
        print("PRs without retro markers (oldest first):")
        print(f"  {'PR':<6} {'merged':<12} {'comments':<9}  title")
        print(f"  {'-' * 6} {'-' * 12} {'-' * 9}  {'-' * 60}")
        for n, merged, title, count in sorted(bypassed):
            title_short = title[:60]
            # Marker string ``potential bypass`` is what the GHA workflow
            # greps for to surface annotations; preserve it.
            print(f"  #{n:<5} {merged:<12} {count:<9}  {title_short}  (potential bypass)")
        print()

    if flagged_direct:
        print("Direct-to-main commits without `Audit-bypass-reason:` trailer:")
        print(f"  {'sha':<10}  subject")
        print(f"  {'-' * 10}  {'-' * 60}")
        for sha, subject in flagged_direct:
            sha_short = sha[:9]
            subject_short = subject[:60]
            print(f"  {sha_short:<10}  {subject_short}  (potential bypass)")
        print()
        print("To exempt a commit, add a trailer to its message, e.g.:")
        print("  Audit-bypass-reason: docs-only-roadmap-update")
        print()

    print("Next step: post a backfill retro comment on each PR via")
    print('  gh pr comment <N> --body "$(cat <retro-file>)"')
    print()
    print("If the PR's pipeline-state file exists with completed_at=null,")
    print("the gate could have caught this. If no state file exists, the")
    print("PR was merged outside pipeline-run entirely — file as a")
    print("'pipeline-bypass merge' for milestone-retro tracking.")
    print()
    print("AUDIT_VERDICT: findings")
    return 1


def run() -> int:
    """Entry point: a failure must never look like a verdict (#3129).

    Exit 1 means "the audit ran and found something". A crash also exits 1
    by Python's default, so a caller reading only the exit code cannot tell
    them apart — the same indistinguishability that let a broken audit
    report a clean summary. Everything that is not a completed audit
    therefore prints ``AUDIT_VERDICT: incomplete`` and exits 2.
    """
    try:
        return main()
    except AuditIncomplete as exc:
        print(f"audit incomplete: {exc}", file=sys.stderr)
        print("AUDIT_VERDICT: incomplete")
        return 2
    except Exception as exc:  # noqa: BLE001 — a crash must not read as findings
        traceback.print_exc()
        print(f"audit errored: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("AUDIT_VERDICT: incomplete")
        return 2


if __name__ == "__main__":
    sys.exit(run())
