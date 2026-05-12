#!/usr/bin/env bash
# git-commit-with-precommit.sh — opt-in commit wrapper that auto-restages
# files modified by pre-commit hooks (ruff-format, ruff --fix, etc.) and
# then performs the commit.
#
# Motivates: closes #1464. Eliminates the ruff-bounce friction class where
# `git commit` triggers a pre-commit hook that reformats a staged file,
# leaves the reformat unstaged, and exits non-zero — forcing the caller to
# re-`git add` and re-`git commit`.
#
# Usage:
#   scripts/git-commit-with-precommit.sh -m "feat: something"
#   scripts/git-commit-with-precommit.sh -m "..." --signoff
#   make commit MSG="feat: something"
#
# All arguments after the script name are forwarded verbatim to `git commit`.
#
# Exit codes:
#   0  commit succeeded (with or without auto-restage)
#   1  no files staged before invocation
#   2  pre-commit failed for a non-modification reason (e.g. lint error)
#   3  `git commit` itself failed after pre-commit succeeded
#   4  post-commit verification did not find the new commit (Action #122)

set -euo pipefail

STAGED=$(git diff --cached --name-only --diff-filter=ACMR)
if [ -z "$STAGED" ]; then
    echo "git-commit-with-precommit: no files staged. Stage with 'git add' first." >&2
    exit 1
fi

PRE_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "")

# Capture content hashes of staged files BEFORE pre-commit so we can detect
# which files the hooks rewrote. Using `git hash-object` matches what `git
# add` will write to the index, so any post-hook diff is real. We use a
# temp file rather than an associative array because macOS ships bash 3.2
# which doesn't support `declare -A`.
PRE_HASH_FILE=$(mktemp -t djust-precommit-XXXXXX)
trap 'rm -f "$PRE_HASH_FILE"' EXIT
while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ -f "$f" ]; then
        printf '%s\t%s\n' "$(git hash-object "$f")" "$f" >>"$PRE_HASH_FILE"
    fi
done <<<"$STAGED"

# Run pre-commit against the staged files. If any hook rewrites a file,
# pre-commit exits non-zero — but only the rewrite case is recoverable.
# `--files <list>` matches the behavior of pre-commit's own commit hook.
set +e
# shellcheck disable=SC2086  # word-splitting STAGED is intentional
uvx pre-commit run --files $STAGED
PC_STATUS=$?
set -e

if [ "$PC_STATUS" -ne 0 ]; then
    REWROTE=0
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        [ -f "$f" ] || continue
        NEW_HASH=$(git hash-object "$f")
        # Look up pre-hash for this file (TAB-delimited "<hash>\t<path>").
        OLD_HASH=$(awk -F'\t' -v path="$f" '$2 == path { print $1; exit }' "$PRE_HASH_FILE")
        if [ "$OLD_HASH" != "$NEW_HASH" ]; then
            REWROTE=1
            break
        fi
    done <<<"$STAGED"

    if [ "$REWROTE" -eq 0 ]; then
        echo "git-commit-with-precommit: pre-commit failed and rewrote no files." >&2
        echo "Fix the reported lint/format errors and re-run." >&2
        exit 2
    fi

    echo "git-commit-with-precommit: pre-commit rewrote staged files; re-staging."
    # shellcheck disable=SC2086
    git add $STAGED
fi

# Forward all args to git commit. The pre-commit hook will run again from
# `git commit` itself; on this second pass the files are already
# auto-restaged so it should be a no-op.
if ! git commit "$@"; then
    echo "git-commit-with-precommit: git commit failed." >&2
    exit 3
fi

# Post-commit verification (Action #122). Confirm a new commit landed.
POST_HEAD=$(git rev-parse HEAD)
if [ "$POST_HEAD" = "$PRE_HEAD" ]; then
    echo "git-commit-with-precommit: HEAD did not advance after commit." >&2
    echo "Latest: $(git log -1 --format='%H %s')" >&2
    exit 4
fi

echo "git-commit-with-precommit: OK $(git log -1 --format='%h %s')"
