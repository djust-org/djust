#!/usr/bin/env bash
# Pre-push pytest wrapper that says WHOSE failures these are (#2139).
#
# The pre-push hook runs the suite (scoped to the pushed range since #2526,
# see below). When `main` is red — as it was for
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

# Scope the run to what the pushed range can affect (#2526). The full suite
# is 20,000 tests in ~8.5 minutes and a PR pushes three or four times; CI is
# the authoritative full run. scripts/select-tests.py picks the test files
# from the diff (changed tests; tests named after / importing a changed
# module; tests whose text mentions a changed file's basename — the source-pin
# tests that read renderer.rs etc.) and answers FULL for anything with
# whole-suite blast radius (conftest/pyproject/hook changes, djust_core,
# the package roots, a routing/flip/convergence branch, an empty selection).
# When the selector is absent or cannot run, the full suite runs: a narrower
# run that was never chosen is worse than a slow one. DJUST_PREPUSH_FULL=1
# forces the full suite by hand.
if [ -f scripts/select-tests.py ] && [ "${DJUST_PREPUSH_FULL:-}" != "1" ]; then
    SEL_OUT="$(mktemp)"
    if bash scripts/run-with-venv-python.sh scripts/select-tests.py >"$SEL_OUT"; then
        if [ "$(head -n 1 "$SEL_OUT")" != "FULL" ]; then
            SELECTED=()
            while IFS= read -r _sel; do
                [ -n "$_sel" ] && SELECTED+=("$_sel")
            done < "$SEL_OUT"
            if [ "${#SELECTED[@]}" -gt 0 ]; then
                PATHS=("${SELECTED[@]}")
            fi
        fi
    else
        echo "select-tests.py could not run — running the full suite."
    fi
    rm -f "$SEL_OUT"
fi

# Checking more than this many ids serially costs more than it is worth: each
# pytest start is ~1.5s here, and `git push` is blocked with no output while it
# runs. A systemic break (one bad import -> hundreds of failures) is exactly
# when the cap matters, and it is also the case where the first N are entirely
# representative.
MAX_ATTRIBUTED=40

REPORT="$(mktemp)"
trap 'rm -f "$REPORT"' EXIT

# --benchmark-disable: run every benchmark BODY (so correctness regressions in
# them still block a push) but do not enforce their latency thresholds here.
#
# Those thresholds are already skipped under `-n auto`, which is how CI and
# `make test` run — so the serial pre-push was the ONLY place enforcing them,
# and it is the worst possible place: the machine has just executed 10,000
# tests in this process, so a warm, fragmented heap makes the median
# systematically slower. `test_vdom_diff_list_reorder` passes standalone three
# times in a row and fails here at 7.57ms against a 5ms target, which measures
# the environment rather than the code — and blocked EVERY push on main, which
# is the exact scenario this script exists to make legible.
#
# tests/benchmarks/conftest.py's own docstring already names the intended
# enforcement point: "the benchmark-gated CI job (--benchmark-only serial)".
# This makes that true instead of aspirational. See #2156.
#
# -n auto: 330s -> 84s, measured on this suite (10,260 tests). The gap is
# bigger than the core count explains, because ~120s of the serial 330s is
# spent BLOCKED rather than computing — `user`+`sys` total only ~210s. Most of
# that wait is the eleven tests in python/tests/test_deploy_cli.py, each of
# which stands up a real loopback HTTPServer for the OAuth callback flow.
#
# This run was serial only by inheritance. The reason it needed to be —
# enforcing benchmark thresholds — was removed directly above, and nothing
# replaced it: no test declares `xdist_group` or a serial marker, the FAILED-id
# parsing below is unaffected by sharding, and .github/workflows/test.yml
# already runs `-n auto` over these same paths, so parallel is the configuration
# CI has been proving all along.
#
# What serial DID still provide, undocumented: pytest-randomly is not installed,
# so a serial run executes in deterministic definition order — a genuinely
# different ordering from xdist's sharding, and order-dependent bugs hide from
# one ordering while surfacing under the other (#2187 is an open instance).
# That coverage is NOT lost here: .github/workflows/main-health.yml runs this
# same suite serially every day at 07:00 UTC. Definition order therefore still
# runs daily, just not on the push path — which is the right place for it,
# since an ordering flake is a property of `main` rather than of the branch
# being pushed, and blocking a push on one tells the pusher nothing actionable.
#
# So: if main-health ever gains `-n auto`, definition order stops being
# exercised anywhere and this trade silently stops holding.
#
# Probed rather than assumed. xdist is a dev dependency, so it is present after
# `uv sync --extra dev` — but "should be installed" is not "is installed", and
# passing `-n auto` to a pytest without it does not degrade, it ABORTS:
#
#   __main__.py: error: unrecognized arguments: -n
#
# The suite never runs, and the pusher gets an argparse usage dump instead of
# test results — from a hook whose entire purpose is making a blocked push
# legible. A partial install is exactly the situation where you least want the
# error to be about the harness. Falling back to serial is slower and correct;
# aborting is neither.
#
# This is not hypothetical: tests/test_red_main_attribution_behaviour_2139.py
# drives this script against a synthetic repo under a minimal interpreter, and
# all 22 of its cases failed this way when the flag was added unconditionally.
PARALLEL=()
if bash scripts/run-with-venv-python.sh -c 'import xdist' >/dev/null 2>&1; then
    PARALLEL=(-n auto)
fi
bash scripts/run-with-venv-python.sh -m pytest "${PATHS[@]}" -q "${PARALLEL[@]+"${PARALLEL[@]}"}" --benchmark-disable 2>&1 | tee "$REPORT"
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
            else
                # Absent at the merge-base usually means the branch added it.
                # But it is also what a MIS-PARSED id looks like, and the two
                # are indistinguishable here. It just failed in THIS tree, so
                # if it cannot be collected here the parse is what is broken —
                # and calling it new would blame the pusher for this script's
                # own defect, which both earlier parsing bugs did.
                "$PYBIN" -m pytest --collect-only "$_id" -q >/dev/null 2>&1
                _crc=$?
                # Only pytest's OWN "cannot resolve this id" codes demote to
                # unresolved. Any OTHER non-zero means the backstop itself
                # could not run — a plugin, a rootdir, an environment we do not
                # control — and a check that cannot execute must not make the
                # answer worse than it was without it. CI hit exactly this:
                # a genuinely-new test came back unattributed because the
                # second pytest failed for reasons unrelated to the id.
                case "$_crc" in
                    4 | 5) UNRESOLVED=$((UNRESOLVED + 1)) ;;
                    *) NEW_IDS+=("$_id") ;;
                esac
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
