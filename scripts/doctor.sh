#!/usr/bin/env bash
# doctor.sh — dev-environment self-diagnosis (issue #2061).
#
# WHY: three local-env incidents surfaced in one week while CI stayed green:
#   1. .venv/lib/python3.12/site-packages/djust.pth repointed at a DELETED
#      worktree by a subagent's `maturin develop` (import breakage + stale
#      .so left behind).
#   2. Embedded-PyO3 test binaries failing `init_fs_encoding` / a
#      `'/install'`-prefix bootstrap error.
#   3. pyo3-ffi failing to compile (SIGABRT in the `rustc -vV` build probe —
#      a homebrew rustc/LLVM version mismatch).
# Plus #1796: the native pre-push hook hardcoded `.venv/bin/python` and
# failed inside a `git worktree` (fixed via scripts/run-with-venv-python.sh;
# this doctor checks the fix is still wired in when run from a worktree).
#
# `make doctor` (or `bash scripts/doctor.sh` directly) runs EVERY check below
# regardless of earlier failures — no `set -e` — and prints one line per
# check:
#   [OK]/[WARN]/[FAIL] <name>: <finding>
# with a one-line remedy on WARN/FAIL. Exits non-zero iff any check FAILs
# (WARNs do not fail the exit code — a missing optional tool, e.g. no
# node_modules/ yet, is a WARN, never a crash or a hard failure).
#
# Test hooks (env-var overrides), used by
# python/djust/tests/test_make_doctor_2061.py for non-tautological coverage
# (#1468/#1200) — force a specific check's verdict without needing to
# actually break the environment:
#   DOCTOR_FAKE_STALE_SO=1   forces the "stale-extension" check to FAIL
#   DOCTOR_FAKE_PTH_BAD=1    forces the "djust.pth" check to FAIL
#
# Usage:
#   bash scripts/doctor.sh
#   make doctor
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

FAIL_COUNT=0
WARN_COUNT=0
OK_COUNT=0

ok() {
    OK_COUNT=$((OK_COUNT + 1))
    echo "[OK] $1: $2"
}

warn() {
    WARN_COUNT=$((WARN_COUNT + 1))
    echo "[WARN] $1: $2"
    if [ -n "${3:-}" ]; then
        echo "      remedy: $3"
    fi
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "[FAIL] $1: $2"
    if [ -n "${3:-}" ]; then
        echo "      remedy: $3"
    fi
}

# Best-effort `timeout` shim — macOS ships `timeout` on recent releases but
# not universally, and `gtimeout` (coreutils via brew) is the fallback. If
# neither is present, run without a timeout wrapper rather than crash.
_TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
    _TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
    _TIMEOUT_BIN="gtimeout"
fi

run_with_timeout() {
    local secs="$1"
    shift
    if [ -n "$_TIMEOUT_BIN" ]; then
        "$_TIMEOUT_BIN" "$secs" "$@"
    else
        "$@"
    fi
}

echo "djust doctor — dev-environment self-diagnosis (issue #2061)"
echo "repo root: $REPO_ROOT"
echo

# Resolve the project Python interpreter the same robust way the
# Makefile/pre-push hooks do: main-checkout .venv > local .venv > uv run >
# python3 on PATH (scripts/run-with-venv-python.sh, issue #1796).
VENV_PYTHON=""
if [ -f "$REPO_ROOT/scripts/run-with-venv-python.sh" ]; then
    VENV_PYTHON="$(bash "$REPO_ROOT/scripts/run-with-venv-python.sh" --print 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Check: venv — .venv/bin/python exists and `import djust` succeeds;
# djust.__file__ resolves INSIDE this checkout.
# ---------------------------------------------------------------------------
check_venv() {
    local name="venv"

    if [ -z "$VENV_PYTHON" ] || [ ! -x "$VENV_PYTHON" ]; then
        warn "$name" "no usable Python interpreter resolved (looked for .venv/bin/python, uv, python3 on PATH)" \
            "run 'make install' (or 'uv sync') to create .venv"
        return
    fi

    local import_out rc
    import_out="$("$VENV_PYTHON" -c 'import djust; print(djust.__file__)' 2>&1)"
    rc=$?
    if [ $rc -ne 0 ]; then
        fail "$name" "'import djust' failed via $VENV_PYTHON: $(echo "$import_out" | tail -1)" \
            "run 'uv run maturin develop' from the repo root"
        return
    fi

    local djust_pkg_dir expected_pkg_dir
    djust_pkg_dir="$(cd "$(dirname "$import_out")" 2>/dev/null && pwd -P || echo "")"
    expected_pkg_dir="$(cd "$REPO_ROOT/python/djust" 2>/dev/null && pwd -P || echo "")"

    if [ -n "$djust_pkg_dir" ] && [ "$djust_pkg_dir" = "$expected_pkg_dir" ]; then
        ok "$name" "$VENV_PYTHON -> import djust OK, resolves inside this checkout ($import_out)"
    else
        warn "$name" "'import djust' resolves OUTSIDE this checkout: $import_out (expected under $REPO_ROOT/python/djust — normal when running doctor from a worktree whose venv is shared with the main checkout)" \
            "run 'uv run maturin develop' from the repo root to repoint the editable install here"
    fi
}

# ---------------------------------------------------------------------------
# Check: djust.pth — the site-packages djust.pth / __editable__*djust*.pth
# entry points into THIS checkout's python/ dir, not a (possibly deleted)
# worktrees/ path.
# ---------------------------------------------------------------------------
check_pth() {
    local name="djust.pth"

    if [ -n "${DOCTOR_FAKE_PTH_BAD:-}" ]; then
        fail "$name" "[test-hook DOCTOR_FAKE_PTH_BAD=1] simulated djust.pth pointing at a deleted worktrees/ path" \
            "run 'uv run maturin develop' from the repo root"
        return
    fi

    if [ -z "$VENV_PYTHON" ] || [ ! -x "$VENV_PYTHON" ]; then
        warn "$name" "no venv python resolved — cannot locate site-packages" \
            "run 'make install' (or 'uv sync') to create .venv"
        return
    fi

    local site_packages
    site_packages="$("$VENV_PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' 2>/dev/null || true)"
    if [ -z "$site_packages" ] || [ ! -d "$site_packages" ]; then
        warn "$name" "could not resolve a site-packages directory for $VENV_PYTHON" \
            "run 'make install' (or 'uv sync')"
        return
    fi

    local pth_file
    pth_file="$(ls "$site_packages"/djust.pth 2>/dev/null | head -1)"
    if [ -z "$pth_file" ]; then
        pth_file="$(ls "$site_packages"/__editable__*djust*.pth 2>/dev/null | head -1)"
    fi
    if [ -z "$pth_file" ] || [ ! -f "$pth_file" ]; then
        warn "$name" "no djust.pth / __editable__*djust*.pth found under $site_packages" \
            "run 'uv run maturin develop' from the repo root"
        return
    fi

    local pth_target expected
    pth_target="$(head -1 "$pth_file")"
    expected="$REPO_ROOT/python"

    if [ "$pth_target" = "$expected" ]; then
        ok "$name" "$pth_file -> $pth_target (this checkout)"
        return
    fi

    case "$pth_target" in
        */worktrees/*)
            if [ -d "$pth_target" ]; then
                warn "$name" "$pth_file points into a worktrees/ path (fragile — worktrees get deleted): $pth_target" \
                    "run 'uv run maturin develop' from the repo root to repoint at a durable checkout"
            else
                fail "$name" "$pth_file points into a DELETED worktrees/ path: $pth_target (the #2061 incident)" \
                    "run 'uv run maturin develop' from the repo root"
            fi
            ;;
        *)
            warn "$name" "$pth_file points outside this checkout: $pth_target (expected $expected — normal when this checkout is a worktree sharing the main checkout's venv)" \
                "run 'uv run maturin develop' from the repo root"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Check: stale extension — mtime of python/djust/_rust.cpython-*.so vs the
# newest crates/**/*.rs file.
# ---------------------------------------------------------------------------
check_stale_so() {
    local name="stale-extension"

    if [ -n "${DOCTOR_FAKE_STALE_SO:-}" ]; then
        fail "$name" "[test-hook DOCTOR_FAKE_STALE_SO=1] simulated python/djust/_rust*.so older than crates/**/*.rs" \
            "run 'uv run maturin develop'"
        return
    fi

    local so_file
    so_file="$(ls "$REPO_ROOT"/python/djust/_rust.cpython-*.so 2>/dev/null | head -1)"
    if [ -z "$so_file" ]; then
        warn "$name" "no compiled python/djust/_rust.cpython-*.so found" \
            "run 'uv run maturin develop' (or 'make build') to compile the extension"
        return
    fi

    if [ ! -d "$REPO_ROOT/crates" ]; then
        warn "$name" "crates/ directory not found — cannot compare mtimes" \
            "verify you are at the repo root"
        return
    fi

    local newer_count newer_example
    newer_count="$(find "$REPO_ROOT/crates" -name '*.rs' -newer "$so_file" 2>/dev/null | wc -l | tr -d ' ')"
    if [ "${newer_count:-0}" -gt 0 ]; then
        newer_example="$(find "$REPO_ROOT/crates" -name '*.rs' -newer "$so_file" 2>/dev/null | head -1)"
        warn "$name" "$(basename "$so_file") is older than $newer_count crates/**/*.rs file(s), e.g. $newer_example" \
            "run 'uv run maturin develop' to rebuild the extension"
    else
        ok "$name" "$(basename "$so_file") is up to date vs crates/**/*.rs"
    fi
}

# ---------------------------------------------------------------------------
# Check: embedded-pyo3 — run ONE small existing PyO3-embedding test under
# crates/djust_templates/tests/ to smoke-test the embedded CPython bootstrap.
# ---------------------------------------------------------------------------
check_embedded_pyo3() {
    local name="embedded-pyo3"

    if ! command -v cargo >/dev/null 2>&1; then
        warn "$name" "cargo not found on PATH" \
            "install Rust via https://rustup.rs (or 'brew install rust')"
        return
    fi

    if [ ! -d "$REPO_ROOT/crates/djust_templates/tests" ]; then
        warn "$name" "crates/djust_templates/tests/ not found" \
            "verify you are at the repo root"
        return
    fi

    # Pick ONE small PyO3-embedding test file — the smallest (by line count)
    # test under crates/djust_templates/tests/ that actually embeds CPython
    # (Python::initialize / pyo3::prelude), not just links pyo3 types.
    local candidates test_file test_name
    candidates="$(grep -l 'Python::initialize\|pyo3::prelude' "$REPO_ROOT"/crates/djust_templates/tests/*.rs 2>/dev/null)"
    if [ -z "$candidates" ]; then
        warn "$name" "no embedded-PyO3 test file found under crates/djust_templates/tests/" \
            "nothing to smoke-test — see issue #2061"
        return
    fi
    test_file="$(echo "$candidates" | xargs wc -l 2>/dev/null | grep -v ' total$' | sort -n | head -1 | awk '{print $2}')"
    if [ -z "$test_file" ]; then
        test_file="$(echo "$candidates" | head -1)"
    fi
    test_name="$(basename "$test_file" .rs)"

    local output rc
    output="$(cd "$REPO_ROOT" && run_with_timeout 30 cargo test -p djust_templates --test "$test_name" 2>&1)"
    rc=$?

    if [ $rc -eq 0 ]; then
        ok "$name" "cargo test -p djust_templates --test $test_name passed"
    elif [ $rc -eq 124 ]; then
        warn "$name" "cargo test -p djust_templates --test $test_name timed out after 30s (cold build cache?)" \
            "run 'cargo build -p djust_templates --tests' once to warm the cache, then re-run doctor"
    elif echo "$output" | grep -qi "init_fs_encoding\|'/install'"; then
        fail "$name" "cargo test -p djust_templates --test $test_name: embedded CPython bootstrap failed (init_fs_encoding class): $(echo "$output" | grep -i "init_fs_encoding\|/install" | head -1 | tr -s ' ')" \
            "Python env vars/homebrew python drift — try a clean shell; see issue #2061"
    elif echo "$output" | grep -qi "SIGABRT\|signal: 6"; then
        fail "$name" "cargo test -p djust_templates --test $test_name: rustc build probe crashed (SIGABRT in the rustc -vV probe): $(echo "$output" | grep -i "SIGABRT\|signal: 6" | head -1 | tr -s ' ')" \
            "rustc/LLVM toolchain drift — try a clean shell (unset stray DYLD_*/LLVM env vars) or reinstall the rust toolchain; see issue #2061"
    else
        fail "$name" "cargo test -p djust_templates --test $test_name failed: $(echo "$output" | tail -3 | tr '\n' ' ')" \
            "see issue #2061 and the captured output above"
    fi
}

# ---------------------------------------------------------------------------
# Check: node — node_modules/ present; npx vitest --version works.
# ---------------------------------------------------------------------------
check_node() {
    local name="node"

    if [ ! -d "$REPO_ROOT/node_modules" ]; then
        warn "$name" "node_modules/ not found" "run 'npm install'"
        return
    fi

    if ! command -v npx >/dev/null 2>&1; then
        warn "$name" "node_modules/ present but 'npx' not found on PATH" \
            "install Node.js (see https://nodejs.org)"
        return
    fi

    local output rc
    output="$(cd "$REPO_ROOT" && run_with_timeout 20 npx vitest --version 2>&1)"
    rc=$?
    if [ $rc -eq 0 ]; then
        ok "$name" "node_modules/ present; npx vitest --version -> $(echo "$output" | tail -1)"
    else
        fail "$name" "'npx vitest --version' failed: $(echo "$output" | tail -1)" \
            "run 'npm install'"
    fi
}

# ---------------------------------------------------------------------------
# Check: hooks — .git/hooks/pre-commit and pre-push exist + executable; note
# the #1796 caveat (pre-push used to hardcode .venv/bin/python, breaking in
# a worktree) when running from a worktree.
# ---------------------------------------------------------------------------
check_hooks() {
    local name="hooks"

    local common_dir git_dir
    common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -z "$common_dir" ]; then
        warn "$name" "not inside a git repository" ""
        return
    fi
    git_dir="$(git rev-parse --path-format=absolute --git-dir 2>/dev/null || true)"

    local pre_commit="$common_dir/hooks/pre-commit"
    local pre_push="$common_dir/hooks/pre-push"
    local problems=""
    if [ ! -x "$pre_commit" ]; then
        problems="${problems}pre-commit missing/not executable ($pre_commit); "
    fi
    if [ ! -x "$pre_push" ]; then
        problems="${problems}pre-push missing/not executable ($pre_push); "
    fi

    if [ -n "$problems" ]; then
        fail "$name" "$problems" \
            "run 'make pre-commit-install' from the main checkout"
        return
    fi

    if [ -n "$git_dir" ] && [ "$git_dir" != "$common_dir" ]; then
        # Running from a linked worktree (#1796): a worktree has no local
        # .venv, so verify the run-with-venv-python.sh fix is still wired
        # into the pytest/cargo pre-push hooks.
        if grep -q "run-with-venv-python.sh" "$REPO_ROOT/.pre-commit-config.yaml" 2>/dev/null; then
            ok "$name" "pre-commit/pre-push present + executable; running from a worktree — #1796 fix (scripts/run-with-venv-python.sh) is wired into .pre-commit-config.yaml"
        else
            warn "$name" ".pre-commit-config.yaml no longer references scripts/run-with-venv-python.sh while running from a worktree — pre-push may hardcode .venv/bin/python again (#1796 regression)" \
                "push with --no-verify and run gates manually against the main checkout's .venv, or restore the run-with-venv-python.sh wiring in .pre-commit-config.yaml"
        fi
    else
        ok "$name" "pre-commit/pre-push present + executable"
    fi
}

# ---------------------------------------------------------------------------
# Check: git-config sanity — core.bare is false in the shared git config
# (the #1804 incident class: a build/PyO3-repoint step or IDE integration
# flips core.bare=true and breaks git in BOTH the worktree and the main
# checkout).
# ---------------------------------------------------------------------------
check_git_config() {
    local name="git-config"
    local checker="$REPO_ROOT/scripts/check-shared-git-config.sh"

    if [ -f "$checker" ]; then
        local output rc
        output="$(bash "$checker" 2>&1)"
        rc=$?
        if [ $rc -eq 0 ]; then
            ok "$name" "core.bare is false/unset in the shared git config"
        else
            fail "$name" "core.bare=true in the shared git config (the #1804 incident class): $(echo "$output" | tail -2 | tr '\n' ' ')" \
                "run 'bash scripts/check-shared-git-config.sh --fix' (or: git config core.bare false)"
        fi
        return
    fi

    # Fallback if the dedicated checker script is ever removed.
    local common_dir bare
    common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -z "$common_dir" ]; then
        warn "$name" "not inside a git repository" ""
        return
    fi
    bare="$(git config --file "$common_dir/config" --get core.bare 2>/dev/null || true)"
    case "$bare" in
        true | yes | 1 | on)
            fail "$name" "core.bare=$bare in the shared git config ($common_dir/config)" \
                "run 'git config core.bare false'"
            ;;
        *)
            ok "$name" "core.bare is '${bare:-<unset>}'"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Run every check regardless of earlier results, then summarize.
# ---------------------------------------------------------------------------
check_venv
check_pth
check_stale_so
check_embedded_pyo3
check_node
check_hooks
check_git_config

echo
echo "djust doctor summary: $OK_COUNT ok, $WARN_COUNT warn, $FAIL_COUNT fail"

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
exit 0
