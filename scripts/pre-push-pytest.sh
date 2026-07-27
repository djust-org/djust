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
# merge-base and report which of them were already broken. When the check
# cannot run, say so plainly rather than guessing — a wrong attribution is
# worse than none, and this script has produced one twice (see the parsing and
# reporting notes below). Both times it was confident.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

PATHS=(tests/ python/tests/ python/djust/tests/)
WT="$(bash scripts/run-with-venv-python.sh --worktree-pythonpath 2>/dev/null || true)"
export PYTHONPATH="${WT:+$WT:}."

# Checking more than this many ids serially costs more than it is worth: each
# pytest start is ~1.5s here, and `git push` is blocked with no output while it
# runs. A systemic break (one bad import -> hundreds of failures) is exactly
# when the cap matters, and it is also the case where the first N are entirely
# representative.
MAX_ATTRIBUTED=40

REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT

bash scripts/run-with-venv-python.sh -m pytest "${PATHS[@]}" -q 2>&1 | tee "$REPORT"
STATUS=${PIPESTATUS[0]}
[ "$STATUS" -eq 0 ] && exit 0

# Split `FAILED <nodeid> - <message>` at the first " - " OUTSIDE brackets.
#
# Both simpler forms are wrong, and each shipped once. `sed 's/ - .*//'`
# truncates any id whose PARAMETER contains " - " (`test_x[a - b]`).
# `sed 's/ - [^-]*$//'` — the fix for that — only substitutes when the message
# has no hyphen at all, which is false for most real pytest messages
# (`AssertionError: assert 1 == -1`, `KeyError: 'main-health'`). The id then
# keeps the message glued on, is unresolvable at the merge-base, and gets
# reported as NEW: the same confidently-wrong answer, reached from the other
# side. It also made the verdict depend on terminal width, since pytest
# truncates that line to $COLUMNS.
#
# A node id is `path::name` or `path::name[params]`, so " - " can only occur
# inside the brackets. Tracking bracket depth is therefore exact, not a
# heuristic.
FAILED_IDS=()
while IFS= read -r _line; do
    [ -n "$_line" ] && FAILED_IDS+=("$_line")
done < <(grep -E '^FAILED ' "$REPORT" | sed 's/^FAILED //' | awk '{
    depth = 0
    n = length($0)
    for (i = 1; i <= n; i++) {
        c = substr($0, i, 1)
        if (c == "[") depth++
        else if (c == "]") { if (depth > 0) depth-- }
        else if (depth == 0 && substr($0, i, 3) == " - ") { print substr($0, 1, i - 1); next }
    }
    print $0
}' | sort -u)

if [ "${#FAILED_IDS[@]}" -eq 0 ]; then
    echo
    echo "pytest failed but reported no FAILED lines — see the output above."
    exit "$STATUS"
fi
COUNT=${#FAILED_IDS[@]}

echo
echo "──────────────────────────────────────────────────────────────────────"
echo "  $COUNT failing test(s):"
printf '%s\n' "${FAILED_IDS[@]}" | sed 's/^/    /'
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
# Resolve the interpreter the SAME way the main run does, via
# run-with-venv-python.sh. Hardcoding "$REPO_ROOT/.venv/bin/python" is wrong in
# a linked worktree — which is where most agent work in this repo happens —
# because a worktree has no .venv of its own. Every id then failed to execute,
# landed in UNRESOLVED, and (before the reporting fix below) was announced as
# "new on this branch". That was parallel-path drift between the two pytest
# invocations of a single file.
PYBIN="$(bash scripts/run-with-venv-python.sh --print 2>/dev/null || true)"
[ -z "$PYBIN" ] && PYBIN="$REPO_ROOT/.venv/bin/python"

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
# one.
PRE_IDS=()
NEW_IDS=()
UNRESOLVED=0
CHECKED=0
SKIPPED_FOR_CAP=0
for _id in "${FAILED_IDS[@]}"; do
    if [ "$CHECKED" -ge "$MAX_ATTRIBUTED" ]; then
        SKIPPED_FOR_CAP=$((SKIPPED_FOR_CAP + 1))
        continue
    fi
    CHECKED=$((CHECKED + 1))
    _out=$(
        cd "$SCRATCH" || exit 1
        PYTHONPATH="$SCRATCH/python:$SCRATCH" "$PYBIN" -m pytest "$_id" -q 2>&1
    )
    _rc=$?
    # Classify on the EXIT CODE, not on grepping the output text.
    #
    # Grepping was wrong in a way no additional string would have fixed. It
    # matched `no tests ran|not found` to mean "absent at the base" — but
    # pytest emits "fixture 'x' not found", and any `pytest.fail("… not
    # found")` in a test's own message matches too, so a test that was BROKEN
    # at the merge-base was announced as new on this branch. It also had no
    # arm at all for a test that ERRORED rather than failed.
    #
    #   0  passed at the base            -> new on this branch
    #   1  failed OR errored at the base -> pre-existing
    #   4  id/file did not resolve       -> absent (or unimportable, below)
    #   5  nothing collected             -> absent
    #   2/3 interrupted / internal       -> cannot tell
    #
    # rc 4 covers two different things, so it is split on pytest's OWN
    # message: `ERROR: not found:` / `ERROR: file or directory not found:` is
    # arg resolution failing to find the id or its file,
    # while `found no collectors` is a module that would not import. A test's
    # assertion text cannot forge either, because a failing test exits 1.
    case "$_rc" in
        0) NEW_IDS+=("$_id") ;;
        1) PRE_IDS+=("$_id") ;;
        4 | 5)
            if [ "$_rc" -eq 4 ] && ! printf '%s' "$_out" | grep -qE '^ERROR: (file or directory )?not found:'; then
                # Unimportable at the base, not absent. Not comparable.
                UNRESOLVED=$((UNRESOLVED + 1))
            elif "$PYBIN" -m pytest --collect-only "$_id" -q >/dev/null 2>&1; then
                # Absent at the merge-base usually means the branch added it.
                # But it is also what a MIS-PARSED id looks like, and the two
                # are indistinguishable here. It just failed in THIS tree, so
                # if it cannot be collected here the parse is what is broken —
                # and calling it new would blame the pusher for this script's
                # own defect, which both earlier parsing bugs did.
                NEW_IDS+=("$_id")
            else
                UNRESOLVED=$((UNRESOLVED + 1))
            fi
            ;;
        *) UNRESOLVED=$((UNRESOLVED + 1)) ;;
    esac
done

PRE_COUNT=${#PRE_IDS[@]}
YOURS=${#NEW_IDS[@]}
RESOLVED=$((PRE_COUNT + YOURS))

echo
# Every arm is gated on what was actually RESOLVED. An earlier version gated
# the "all new" arm on PRE_COUNT alone, so a run where nothing could be checked
# printed "None of these fail at the merge-base — all N are new on this branch"
# and then contradicted itself two lines later with "NOT attributed". The
# headline is the part people read.
if [ "$RESOLVED" -eq 0 ]; then
    echo "  None of these could be checked at the merge-base, so they are NOT"
    echo "  attributed. Treat them as unknown rather than as yours."
elif [ "$PRE_COUNT" -gt 0 ] && [ "$YOURS" -eq 0 ]; then
    # "Your branch did not cause them" is a statement about ALL the failures,
    # so it may only be made when all of them were actually checked. With
    # anything unresolved or skipped for the cap, the unexamined ones could be
    # the pusher's own regression — and telling them to go wait for someone
    # else to fix main is then the worst available advice.
    printf '%s\n' "${PRE_IDS[@]}" | sed 's/^/    /'
    if [ "$UNRESOLVED" -eq 0 ] && [ "$SKIPPED_FOR_CAP" -eq 0 ]; then
        echo "  ALL $PRE_COUNT failure(s) ALSO fail at the merge-base."
        echo "  Your branch did not cause them — main is red."
        echo
        echo "  This push is still blocked, which is deliberate. Fix main (or wait"
        echo "  for the fix to land) rather than reaching for --no-verify."
    else
        echo "  The $PRE_COUNT failure(s) above ALSO fail at the merge-base, so"
        echo "  your branch did not cause THOSE. The rest were not checked, so"
        echo "  do not read this as main being the only problem."
    fi
elif [ "$PRE_COUNT" -gt 0 ]; then
    echo "  $PRE_COUNT of $RESOLVED checked failure(s) are PRE-EXISTING at the"
    echo "  merge-base; $YOURS are new on this branch:"
    printf '%s\n' "${NEW_IDS[@]}" | sed 's/^/    /'
else
    echo "  None of the $RESOLVED checked failure(s) fail at the merge-base —"
    echo "  all $YOURS are new on this branch:"
    printf '%s\n' "${NEW_IDS[@]}" | sed 's/^/    /'
fi
if [ "$UNRESOLVED" -gt 0 ]; then
    echo
    echo "  ($UNRESOLVED could not be checked at the merge-base and are NOT"
    echo "  attributed — treat them as unknown, not as yours.)"
fi
if [ "$SKIPPED_FOR_CAP" -gt 0 ]; then
    echo
    echo "  ($SKIPPED_FOR_CAP more were NOT checked: the cap is"
    echo "  $MAX_ATTRIBUTED and the ids are sorted, so these are the"
    echo "  alphabetically-first $MAX_ATTRIBUTED, not the most relevant ones.)"
fi
echo "──────────────────────────────────────────────────────────────────────"

exit "$STATUS"
