#!/usr/bin/env bash
# codeql-alert-gate.sh — fail when a ref has open high/critical CodeQL alerts.
#
# Usage:
#   scripts/codeql-alert-gate.sh <ref> [owner/repo]
#   scripts/codeql-alert-gate.sh refs/tags/v1.2.0
#   scripts/codeql-alert-gate.sh refs/heads/main djust-org/djust
#
# Run by the "CodeQL alert gate" job of
# .github/workflows/pre-release-security-audit.yml after both CodeQL analyses
# have uploaded (the analyze action waits for processing), with the analysed
# ref — for a release that is the tag, refs/tags/vX.Y.Z. Publishing needs that
# job to pass.
#
# "High/critical" is the alert's security severity
# (rule.security_severity_level), which only security queries carry;
# quality-only findings (notes, warnings) never block. Dismissed and fixed
# alerts are not "open", so dismissing an alert in the Security tab with a
# reason is the reviewed allowlist for CodeQL, alongside the rule/path
# exclusions in .github/codeql/codeql-config.yml.
#
# Requires: gh CLI with security-events: read on the repo (GH_TOKEN in CI).

set -euo pipefail

ref="${1:?usage: $0 <ref> [owner/repo]}"
repo="${2:-${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}}"

# An alert query for a ref nothing was uploaded to returns an empty list, which
# would read as "clean". When CI says which analyses it just ran
# (CODEQL_EXPECT_CATEGORIES, comma-separated, for commit CODEQL_EXPECT_SHA),
# first prove each of them landed on this ref.
if [ -n "${CODEQL_EXPECT_CATEGORIES:-}" ]; then
    sha="${CODEQL_EXPECT_SHA:?CODEQL_EXPECT_SHA must be set with CODEQL_EXPECT_CATEGORIES}"
    analyses="$(gh api --paginate \
        "repos/${repo}/code-scanning/analyses?ref=${ref}&per_page=100" \
        | jq -s 'add // []')"
    IFS=',' read -r -a categories <<<"$CODEQL_EXPECT_CATEGORIES"
    for category in "${categories[@]}"; do
        found="$(jq --arg c "$category" --arg s "$sha" \
            '[.[] | select(.category == $c and .commit_sha == $s)] | length' <<<"$analyses")"
        if [ "$found" -eq 0 ]; then
            echo "::error::No CodeQL analysis '${category}' for ${sha} on ${ref}; refusing to read an empty alert list as clean."
            exit 1
        fi
        echo "Found CodeQL analysis '${category}' for ${sha:0:12} on ${ref}."
    done
fi

# --paginate emits one JSON array per page; jq -s flattens them. An API error
# (bad ref, missing permission) makes gh exit non-zero, and set -e fails the
# gate closed rather than reading "no alerts".
alerts="$(gh api --paginate \
    "repos/${repo}/code-scanning/alerts?state=open&ref=${ref}&per_page=100" \
    | jq -s 'add // []')"

total="$(jq 'length' <<<"$alerts")"
blocking="$(jq '[.[] | select(.rule.security_severity_level == "high" or .rule.security_severity_level == "critical")]' <<<"$alerts")"
count="$(jq 'length' <<<"$blocking")"

echo "Open CodeQL alerts on ${ref}: ${total} total, ${count} high/critical."
if [ "$count" -gt 0 ]; then
    jq -r '.[] | "  #\(.number) \(.rule.security_severity_level) \(.rule.id) \(.most_recent_instance.location.path):\(.most_recent_instance.location.start_line) [\(.most_recent_instance.category)] \(.html_url)"' <<<"$blocking"
    echo "::error::${count} open high/critical CodeQL alert(s) on ${ref}. Fix them, or dismiss each in the Security tab with a reviewed reason."
    exit 1
fi
echo "OK: no open high/critical CodeQL alerts on ${ref}."
