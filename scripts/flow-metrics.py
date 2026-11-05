#!/usr/bin/env python3
"""Time per PR and per issue, against budgets, from GitHub (not from anyone's memory).

    python scripts/flow-metrics.py [--days 3] [--check] [--json]
    make flow-metrics DAYS=3

Why this exists: the same complaint (long runs, slow resolution, a hot laptop) came back three
times in one session, and a note in a doc did not stop it. A number does. It measures, from the
GitHub API, how long merged PRs were open, how many commits they took, and how long closed
issues were open, then compares each to a budget. ``--check`` exits 1 on a breach, so a retro
or a scheduled run can enforce it; it is deliberately NOT a merge gate (a metric on history
would block unrelated work).

Budgets (override with the env var in brackets) come from the 2026-11-04/05 baseline of 27
merged PRs and 42 closed issues: PR open->merged median 0.6h, p90 2.0h; issue open->closed
median 6.3h, p75 17.4h, p90 28.4h. Each is set tighter than the baseline p90/p75 on purpose.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys

REPO = os.environ.get("FLOW_REPO", "djust-org/djust")

#: name -> (default, env var, what it bounds)
BUDGETS = {
    "pr_lead_p90_hours": (3.0, "FLOW_PR_LEAD_P90_H", "PR open -> merged, 90th percentile"),
    "pr_commits_max": (8, "FLOW_PR_COMMITS_MAX", "commits in any one PR (review rounds)"),
    "issue_lead_p75_hours": (12.0, "FLOW_ISSUE_LEAD_P75_H", "issue open -> closed, 75th percentile"),
    "issue_lead_max_hours": (48.0, "FLOW_ISSUE_LEAD_MAX_H", "any one issue open -> closed"),
}


def budget(name: str, environ=None) -> float:
    default, var, _ = BUDGETS[name]
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(var, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _when(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def hours_between(start: str, end: str) -> float:
    return (_when(end) - _when(start)).total_seconds() / 3600


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile; ``p`` in 0..1; 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * p)))]


def summarize(prs: list[dict], issues: list[dict]) -> dict:
    """``prs``: dicts with created_at, merged_at, commits, number. ``issues``: created_at, closed_at, number."""
    pr_lead = [hours_between(p["created_at"], p["merged_at"]) for p in prs]
    issue_lead = [hours_between(i["created_at"], i["closed_at"]) for i in issues]
    commits = [p["commits"] for p in prs]
    return {
        "prs": len(prs),
        "pr_lead_median_hours": statistics.median(pr_lead) if pr_lead else 0.0,
        "pr_lead_p90_hours": percentile(pr_lead, 0.9),
        "pr_commits_max": max(commits, default=0),
        "pr_commits_worst": max(prs, key=lambda p: p["commits"])["number"] if prs else None,
        "issues": len(issues),
        "issue_lead_median_hours": statistics.median(issue_lead) if issue_lead else 0.0,
        "issue_lead_p75_hours": percentile(issue_lead, 0.75),
        "issue_lead_max_hours": max(issue_lead, default=0.0),
        "issue_slowest": max(issues, key=lambda i: hours_between(i["created_at"], i["closed_at"]))["number"]
        if issues
        else None,
    }


def breaches(summary: dict, environ=None) -> list[str]:
    """One line per budget the summary is over."""
    out = []
    for name, (_, _, what) in BUDGETS.items():
        limit, got = budget(name, environ), summary[name]
        if got > limit:
            out.append(f"{name}: {got:.1f} > {limit:g}  ({what})")
    return out


def _gh(path: str) -> list[dict]:
    out = subprocess.run(["gh", "api", "--paginate", path], capture_output=True, text=True, check=True)
    text = out.stdout.replace("][", ",")  # --paginate prints one array per page
    return json.loads(text) if text.strip() else []


def fetch(days: int) -> tuple[list[dict], list[dict]]:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    issues = [
        {"number": i["number"], "created_at": i["created_at"], "closed_at": i["closed_at"]}
        for i in _gh(f"repos/{REPO}/issues?state=closed&per_page=100&since={since}")
        if "pull_request" not in i and i.get("closed_at") and i["closed_at"] >= since
    ]
    prs = []
    for p in _gh(f"repos/{REPO}/pulls?state=closed&per_page=100&sort=updated&direction=desc"):
        if p.get("merged_at") and p["merged_at"] >= since:
            detail = json.loads(
                subprocess.run(["gh", "api", f"repos/{REPO}/pulls/{p['number']}"], capture_output=True, text=True, check=True).stdout
            )
            prs.append({"number": p["number"], "created_at": p["created_at"], "merged_at": p["merged_at"], "commits": detail["commits"]})
    return prs, issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--check", action="store_true", help="exit 1 when any budget is exceeded")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    prs, issues = fetch(args.days)
    summary = summarize(prs, issues)
    over = breaches(summary)
    if args.json:
        print(json.dumps({"summary": summary, "breaches": over}, indent=2))
    else:
        print(f"last {args.days} day(s): {summary['prs']} merged PRs, {summary['issues']} closed issues")
        print(f"  PR open->merged   median {summary['pr_lead_median_hours']:.1f}h  p90 {summary['pr_lead_p90_hours']:.1f}h  (budget p90 {budget('pr_lead_p90_hours'):g}h)")
        print(f"  PR commits        max {summary['pr_commits_max']}  worst #{summary['pr_commits_worst']}  (budget {budget('pr_commits_max'):g})")
        print(f"  issue open->close median {summary['issue_lead_median_hours']:.1f}h  p75 {summary['issue_lead_p75_hours']:.1f}h  max {summary['issue_lead_max_hours']:.1f}h  slowest #{summary['issue_slowest']}")
        print(f"  (budgets: issue p75 {budget('issue_lead_p75_hours'):g}h, issue max {budget('issue_lead_max_hours'):g}h)")
        for line in over:
            print(f"OVER BUDGET  {line}")
        if not over:
            print("within every budget")
    return 1 if (args.check and over) else 0


if __name__ == "__main__":
    sys.exit(main())
