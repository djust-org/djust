#!/usr/bin/env bash
# Pre-push pytest wrapper that says WHOSE failures these are (#2139).
#
# The pre-push hook runs the full suite. When `main` is red — as it was for
# three doc-snippet tests until #2134 — every branch's push is rejected for a
# reason that has nothing to do with the branch. The failing test names are
# buried under ~40 passing hook lines, and nothing distinguishes "your change
# broke this" from "this was already broken on the merge-base".
#
# That distinction is the only thing a contributor needs at that moment, and
# deriving it by hand costs a full suite run against a scratch checkout. It
# cost three failed pushes to diagnose the last time.
#
# So: run the suite; if it fails, re-run ONLY the failing tests against the
# merge-base and report which of them were already broken. When the
# merge-base check cannot run, say so plainly rather than guessing — a wrong
# attribution is worse than none.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

PATHS=(tests/ python/tests/ python/djust/tests/)
WT="$(bash scripts/run-with-venv-python.sh --worktree-pythonpath 2>/dev/null || true)"
export PYTHONPATH="${WT:+$WT:}."

REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT

bash scripts/run-with-venv-python.sh -m pytest "${PATHS[@]}" -q 2>&1 | tee "$REPORT"
STATUS=${PIPESTATUS[0]}
[ "$STATUS" -eq 0 ] && exit 0

# Collect the failing node ids into an ARRAY, not a whitespace-split string.
# pytest does not sanitise ids: `@pytest.mark.parametrize("v", ["a b"])` yields
# `test_x[a b]`, and word-splitting that into two unresolvable args poisons the
# whole merge-base run (see below). Also strip only the LAST ` - ` so an id
# containing that sequence survives.
FAILED_IDS=()
while IFS= read -r _line; do
    [ -n "$_line" ] && FAILED_IDS+=("$_line")
done < <(grep -E '^FAILED ' "$REPORT" | sed 's/^FAILED //' | sed 's/ - [^-]*$//' | sort -u)
FAILED=$(printf '%s\n' "${FAILED_IDS[@]+"${FAILED_IDS[@]}"}")
if [ "${#FAILED_IDS[@]}" -eq 0 ]; then
    echo
    echo "pytest failed but reported no FAILED lines — see the output above."
    exit "$STATUS"
fi
COUNT=${#FAILED_IDS[@]}

echo
echo "──────────────────────────────────────────────────────────────────────"
echo "  $COUNT failing test(s):"
printf '%s\n' "$FAILED" | sed 's/^/    /'
echo "──────────────────────────────────────────────────────────────────────"

# Which of these were already failing on the merge-base?
BASE=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
[ -z "$BASE" ] && BASE=main
MERGE_BASE=$(git merge-base HEAD "origin/$BASE" 2>/dev/null || true)

if [ -z "$MERGE_BASE" ]; then
    echo "  Could not resolve a merge-base against origin/$BASE, so these"
    echo "  failures are NOT attributed. Run 'git fetch origin' and retry to"
    echo "  find out whether they are yours."
    exit "$STATUS"
fi
if [ "$(git rev-parse HEAD)" = "$MERGE_BASE" ]; then
    echo "  HEAD is the merge-base, so every failure above is pre-existing."
    exit "$STATUS"
fi

# ALL of them, not `head -1`: the tree carries one .so per Python ABI
# (_rust.cpython-311/312/314-darwin.so), and copying an arbitrary one lands a
# mismatched ABI, so every import fails at the merge-base and every failure
# looks pre-existing. That is the wrong answer delivered confidently.
SO_COUNT=$(find python/djust -maxdepth 1 -name "_rust*.so" 2>/dev/null | wc -l | tr -d ' ')
if [ "$SO_COUNT" -eq 0 ]; then
    echo "  No built _rust extension found, so the merge-base check would not"
    echo "  be comparable. Failures NOT attributed."
    exit "$STATUS"
fi

SCRATCH=$(mktemp -d)
cleanup() { git worktree remove --force "$SCRATCH" >/dev/null 2>&1 || true; rm -rf "$SCRATCH"; rm -f "$REPORT"; }
trap cleanup EXIT

echo "  Checking which of these already fail at the merge-base"
echo "  ($(git rev-parse --short "$MERGE_BASE"))…"
if ! git worktree add --detach "$SCRATCH" "$MERGE_BASE" >/dev/null 2>&1; then
    echo "  Could not create a scratch worktree. Failures NOT attributed."
    exit "$STATUS"
fi
# The compiled extension is gitignored, so the scratch tree needs it copied in
# or every test errors on import and everything looks "pre-existing".
cp python/djust/_rust*.so "$SCRATCH/python/djust/" 2>/dev/null || true

REPO_ROOT=$(pwd)
# ONE INVOCATION PER ID, deliberately. pytest resolves every argument before
# collecting, so a SINGLE id that does not exist at the merge-base — a test the
# branch adds, renames, or re-parametrizes — aborts the entire session with
# "no tests ran" and zero results. Passing the whole set therefore reports
# every genuinely pre-existing failure as "new on this branch": the confidently
# wrong answer this script's header calls worse than none, delivered in exactly
# the mixed case the script exists for.
#
# An earlier version passed them all at once and then WHITELISTED "no tests
# ran" as usable signal, which turned a safe non-answer into an unsafe wrong
# one. N is the number of failures, so per-id is cheap.
PRE_IDS=()
UNRESOLVED=0
for _id in "${FAILED_IDS[@]}"; do
    _out=$(
        cd "$SCRATCH" || exit 1
        PYTHONPATH="$SCRATCH/python:$SCRATCH" \
            "$REPO_ROOT/.venv/bin/python" -m pytest "$_id" -q 2>&1
    )
    if printf '%s' "$_out" | grep -qE '^FAILED '; then
        PRE_IDS+=("$_id")
    elif printf '%s' "$_out" | grep -qE '[0-9]+ passed'; then
        : # passed at the base -> new on this branch
    elif printf '%s' "$_out" | grep -qE 'no tests ran|not found'; then
        : # absent at the base -> new by definition
    else
        # Could not tell for this id (import error, venv mismatch). Count it
        # so the report can say so rather than silently calling it new.
        UNRESOLVED=$((UNRESOLVED + 1))
    fi
done

PRE=$(printf '%s\n' "${PRE_IDS[@]+"${PRE_IDS[@]}"}")
PRE_COUNT=${#PRE_IDS[@]}
YOURS=$((COUNT - PRE_COUNT - UNRESOLVED))

echo
if [ "$PRE_COUNT" -gt 0 ] && [ "$YOURS" -eq 0 ]; then
    echo "  ALL $PRE_COUNT failure(s) ALSO fail at the merge-base."
    echo "  Your branch did not cause them — main is red."
    echo
    echo "  This push is still blocked, which is deliberate. Fix main (or wait"
    echo "  for the fix to land) rather than reaching for --no-verify."
elif [ "$PRE_COUNT" -gt 0 ]; then
    echo "  $PRE_COUNT of $COUNT failure(s) are PRE-EXISTING at the merge-base;"
    echo "  $YOURS are new on this branch:"
    for _id in "${FAILED_IDS[@]}"; do
        _is_pre=0
        for _p in "${PRE_IDS[@]+"${PRE_IDS[@]}"}"; do
            [ "$_id" = "$_p" ] && _is_pre=1 && break
        done
        [ "$_is_pre" -eq 0 ] && echo "    $_id"
    done
else
    echo "  None of these fail at the merge-base — all $COUNT are new on this"
    echo "  branch."
fi
if [ "$UNRESOLVED" -gt 0 ]; then
    echo
    echo "  ($UNRESOLVED could not be checked at the merge-base and are NOT"
    echo "  attributed — treat them as unknown, not as yours.)"
fi
echo "──────────────────────────────────────────────────────────────────────"

exit "$STATUS"
