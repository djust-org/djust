#!/usr/bin/env bash
# Pre-push `cargo test`, scoped to the crates the pushed range touches (#2526)
# and run in DEBUG (#2654 — see the invocation at the bottom for the numbers).
#
# scripts/select-tests.py --cargo prints `-p <crate>` for every crate whose
# files changed plus every workspace crate that depends on it, or
# `--workspace` when Cargo.toml / Cargo.lock / djust_core changed. djust_live
# is excluded either way, as the pre-#2526 hook excluded it. When the selector
# is absent or cannot run, the whole workspace runs.
#
# PYO3_PYTHON must be an EMBEDDABLE interpreter (#2072): see
# scripts/embeddable-python.sh.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 1

ARGS=(--workspace --exclude djust_live)
if [ -f scripts/select-tests.py ] && [ "${DJUST_PREPUSH_FULL:-}" != "1" ]; then
    if SEL="$(bash scripts/run-with-venv-python.sh scripts/select-tests.py --cargo)"; then
        if [ -z "$SEL" ]; then
            echo "pre-push cargo: selector returned nothing; running the workspace" >&2
        fi
        if [ -n "$SEL" ] && [ "$SEL" != "--workspace" ]; then
            ARGS=()
            for _tok in $SEL; do
                [ "$_tok" = "-p" ] && continue
                [ "$_tok" = "djust_live" ] && continue
                ARGS+=(-p "$_tok")
            done
            if [ "${#ARGS[@]}" -eq 0 ]; then
                echo "cargo test: only djust_live changed, which the pre-push run excludes — nothing to run."
                exit 0
            fi
        fi
    else
        echo "select-tests.py could not run — testing the whole workspace."
    fi
fi

# DEBUG, not --release (#2654).
#
# The `--release` this replaces predates the #2526 scoping work — it was in the
# original inline hook entry and carried no rationale in the script, the config
# or the commit that moved it here. Measured on a cold worktree, the exact
# invocation this hook makes:
#
#     cargo test --workspace --exclude djust_live            54.7s
#     cargo test --workspace --exclude djust_live --release  169.0s
#
# 3.1x on the workspace (6.6x on a single crate), for identical results: the
# whole suite passes in debug, 0 failures. That is ~2 minutes back on every
# push that touches Cargo.toml / Cargo.lock / djust_core, which is what the
# selector escalates to `--workspace` for.
#
# Checked before flipping: the only wall-clock in any Rust test is the 10s
# HANG watchdog in `crates/djust_templates/tests/free_threaded_safety.rs`,
# which completes in 0.02s in debug — a 500x margin, and a deadline rather than
# a performance assertion. No test asserts on elapsed time.
#
# Benchmarks are unaffected: they are a separate `cargo bench` / pytest-benchmark
# path and still build optimised. Set DJUST_PREPUSH_RELEASE=1 to restore the
# optimised run locally.
if [ "${DJUST_PREPUSH_RELEASE:-}" = "1" ]; then
    echo "cargo test ${ARGS[*]} --release -q"
    PYO3_PYTHON="$(bash scripts/embeddable-python.sh)" cargo test "${ARGS[@]}" --release -q
else
    echo "cargo test ${ARGS[*]} -q"
    PYO3_PYTHON="$(bash scripts/embeddable-python.sh)" cargo test "${ARGS[@]}" -q
fi
