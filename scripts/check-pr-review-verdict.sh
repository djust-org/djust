#!/usr/bin/env bash
# Pre-merge gate: does the PR's LAST review verdict say APPROVE, and was it
# given against the current head? (#2661)
#
# The previous gate grepped for the PRESENCE of review-shaped text, so a
# REQUEST_CHANGES review — or a self-authored one — satisfied it, and #2646
# merged against a review that said "do not merge". This script reads the
# verdict instead of the shape.
#
# A verdict is a line, in a PR review body or an issue comment, that starts
# (after markdown emphasis / list / quote / heading characters and an optional
# "Verdict:" label) with the token APPROVE or REQUEST_CHANGES. Reviews and
# comments are merged into one timeline and the newest verdict wins. Prose
# that mentions a token mid-sentence ("that APPROVE was wrong") is not a
# verdict. A formal GitHub review whose state is APPROVED / CHANGES_REQUESTED
# counts as that verdict even without a token in its body.
#
# Exit codes:
#   0  the last verdict is APPROVE and postdates the head commit (or is a
#      review submitted against the head sha)
#   1  the last verdict is REQUEST_CHANGES
#   2  no verdict at all
#   3  the last verdict is APPROVE but predates the head commit (stale — the
#      approved code is not the code that would merge)
#   4  usage / gh failure
#
# Usage: scripts/check-pr-review-verdict.sh <pr-number> [--repo owner/name]
set -euo pipefail

PR="${1:-}"
if [ -z "$PR" ]; then
    echo "usage: $0 <pr-number> [--repo owner/name]" >&2
    exit 4
fi
shift
REPO_ARGS=()
if [ "${1:-}" = "--repo" ] && [ -n "${2:-}" ]; then
    REPO_ARGS=(--repo "$2")
fi

PR_JSON=$(gh pr view "$PR" ${REPO_ARGS[@]+"${REPO_ARGS[@]}"} --json headRefOid,commits,reviews,comments,author) || {
    echo "check-pr-review-verdict: gh could not read PR #$PR" >&2
    exit 4
}
export PR_JSON PR

exec python3 - <<'PY'
import json
import os
import re
import sys

pr = os.environ["PR"]
data = json.loads(os.environ["PR_JSON"])
head = data["headRefOid"]
commits = data.get("commits") or []
head_at = ""
for c in commits:
    if c.get("oid") == head:
        head_at = c.get("committedDate") or ""
if not head_at and commits:
    head_at = commits[-1].get("committedDate") or ""

# ISO-8601 Zulu timestamps compare correctly as strings.
TOKEN = re.compile(r"^[\s*_`>#\-|]*(?:verdict\s*:\s*)?[\s*_`]*(APPROVE|REQUEST_CHANGES)\b", re.I)


def verdict_in(body: str) -> str | None:
    found = None
    for line in (body or "").splitlines():
        m = TOKEN.match(line)
        if m:
            found = m.group(1).upper()  # the LAST verdict line in one body wins
    return found


events = []  # (timestamp, verdict, source, commit_oid_or_empty, author)
for r in data.get("reviews") or []:
    v = verdict_in(r.get("body", ""))
    state = (r.get("state") or "").upper()
    if v is None and state == "APPROVED":
        v = "APPROVE"
    elif v is None and state == "CHANGES_REQUESTED":
        v = "REQUEST_CHANGES"
    if v:
        events.append(
            (
                r.get("submittedAt") or "",
                v,
                "review",
                (r.get("commit") or {}).get("oid") or "",
                (r.get("author") or {}).get("login") or "",
            )
        )
for c in data.get("comments") or []:
    v = verdict_in(c.get("body", ""))
    if v:
        events.append(
            (c.get("createdAt") or "", v, "comment", "", (c.get("author") or {}).get("login") or "")
        )

events.sort(key=lambda e: e[0])
print(f"PR #{pr}: head {head[:8]} committed {head_at or '?'}")
for ts, v, src, oid, who in events:
    against = f" against {oid[:8]}" if oid else ""
    print(f"  {ts}  {v:<15} ({src} by {who}{against})")

if not events:
    print("NO VERDICT: no APPROVE / REQUEST_CHANGES line in any review or comment")
    sys.exit(2)

ts, v, src, oid, who = events[-1]
if v != "APPROVE":
    print(f"BLOCKED: last verdict is {v} ({src} by {who} at {ts})")
    sys.exit(1)

if oid == head or (head_at and ts >= head_at):
    print(f"OK: last verdict is APPROVE ({src} by {who} at {ts}) and it covers head {head[:8]}")
    sys.exit(0)

print(
    f"STALE: last verdict is APPROVE at {ts}, but head {head[:8]} was committed "
    f"{head_at or 'later'} — the approved code is not what would merge"
)
sys.exit(3)
PY
