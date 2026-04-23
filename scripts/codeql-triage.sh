#!/usr/bin/env bash
# codeql-triage.sh — dump open CodeQL alerts as a markdown triage table.
#
# Usage:
#   scripts/codeql-triage.sh                 # all open alerts
#   scripts/codeql-triage.sh py/empty-except # only alerts for that rule
#
# Requires: gh CLI, authenticated and scoped to the repo. Calls
# /repos/{owner}/{repo}/code-scanning/alerts?state=open&per_page=100 with
# --paginate and groups by rule.id. Sort within each group is file, line.
#
# Closes #916.

set -euo pipefail

RULE_FILTER="${1:-}"

# Resolve repo owner/name from gh so the script works in any clone.
repo_slug="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
today="$(date +%Y-%m-%d)"

# Fetch all open alerts, paginated. The GitHub REST API returns one JSON
# array per page; --paginate concatenates them into a single stream of
# arrays. Use jq --slurp to flatten into one array, then sort/group.
raw_json="$(gh api \
    "/repos/${repo_slug}/code-scanning/alerts?state=open&per_page=100" \
    --paginate)"

# Build markdown. All the grouping/sorting happens in jq so the shell stays
# small. If RULE_FILTER is set, prefilter before grouping.
echo "# CodeQL open alerts — ${today}"
echo ""
echo "Repo: \`${repo_slug}\` — state=open"
echo ""

if [[ -z "$raw_json" || "$raw_json" == "[]" ]]; then
    echo "_No open alerts._"
    exit 0
fi

# group_by needs the input pre-sorted on the group key; we slurp (absorb all
# pages into one array via --slurp + add), flatten, filter, then group.
echo "$raw_json" | jq -r --arg filter "$RULE_FILTER" '
    # Collapse paginated arrays into a single flat array.
    if type == "array" then . else [.] end
    | map(select(.state == "open"))
    | if ($filter != "") then map(select(.rule.id == $filter)) else . end
    | sort_by(.rule.id, .most_recent_instance.location.path, .most_recent_instance.location.start_line)
    | group_by(.rule.id)
    | map(
        "## \(.[0].rule.id) (\(length))\n\n" +
        "| # | file | line | severity | snippet |\n" +
        "|---|------|------|----------|---------|\n" +
        (
          map(
            "| \(.number)"
            + " | \(.most_recent_instance.location.path // "?")"
            + " | \(.most_recent_instance.location.start_line // "?")"
            + " | \(.rule.severity // .rule.security_severity_level // "?")"
            + " | \((.most_recent_instance.message.text // .rule.description // "") | gsub("\\|"; "\\|") | gsub("\n"; " ") | .[0:120])"
            + " |"
          ) | join("\n")
        )
    )
    | join("\n\n")
'
