#!/usr/bin/env bash
# Resolve the project Python interpreter robustly and exec the given args.
#
# WHY: the native pre-push hook (and several `make` targets) used to hardcode
# `.venv/bin/python` relative to the current working directory. That works in
# the main checkout but FAILS inside a `git worktree` (e.g. the ones
# pipeline-drain subagents create under `.claude/worktrees/`), which has no
# `.venv` of its own. The only escape was `git push --no-verify`, which skips
# the real gates. See issue #1796.
#
# This resolver always finds the interpreter relative to the MAIN working tree
# root — `dirname` of the absolute common git dir — so it works identically
# from any linked worktree or from the main checkout. If no `.venv` is found
# there (CI, fresh clone, a contributor who uses a system venv) it falls back
# to `uv run python` and finally to `python3` on PATH.
#
# Usage:
#   bash scripts/run-with-venv-python.sh -m pytest tests/ -q
#   bash scripts/run-with-venv-python.sh scripts/check-handler-contracts.py
#
# Callers that need the interpreter PATH (not an exec) — e.g. `PYO3_PYTHON` for
# `cargo test` — can run it in `--print` mode:
#   PYO3_PYTHON="$(bash scripts/run-with-venv-python.sh --print)" cargo test ...
# `--print` emits a single absolute interpreter path when a `.venv` exists, or
# the absolute path of the resolved `uv`/`python3` interpreter otherwise.
set -euo pipefail

PRINT_ONLY=0
if [ "${1:-}" = "--print" ]; then
    PRINT_ONLY=1
    shift
fi

# Resolve the MAIN working tree root. `--git-common-dir` points at the shared
# `.git` directory: `<main-root>/.git` for both the main checkout and every
# linked worktree. Its parent is the main working tree root where `.venv` lives.
# `--path-format=absolute` guarantees an absolute path regardless of cwd.
main_root=""
if common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
    main_root="$(dirname "$common_dir")"
fi

venv_python=""
if [ -n "$main_root" ] && [ -x "$main_root/.venv/bin/python" ]; then
    venv_python="$main_root/.venv/bin/python"
elif [ -x ".venv/bin/python" ]; then
    # Fallback: a `.venv` in the current tree (non-git context, or a worktree
    # that happens to have its own venv).
    venv_python="$(cd "$(dirname .venv/bin/python)" && pwd)/python"
fi

if [ -n "$venv_python" ]; then
    if [ "$PRINT_ONLY" -eq 1 ]; then
        printf '%s\n' "$venv_python"
        exit 0
    fi
    exec "$venv_python" "$@"
fi

# No project `.venv` — fall back to `uv run python` (resolves/creates the
# environment from pyproject) and finally to `python3` on PATH.
if command -v uv >/dev/null 2>&1; then
    if [ "$PRINT_ONLY" -eq 1 ]; then
        # Emit the interpreter `uv run` would use (absolute path).
        uv run python -c 'import sys; print(sys.executable)'
        exit 0
    fi
    exec uv run python "$@"
elif command -v python3 >/dev/null 2>&1; then
    if [ "$PRINT_ONLY" -eq 1 ]; then
        command -v python3
        exit 0
    fi
    exec python3 "$@"
fi

echo "ERROR: no Python interpreter found." 1>&2
echo "  Looked for: \$MAIN_ROOT/.venv/bin/python ($main_root), ./.venv/bin/python," 1>&2
echo "  'uv' on PATH, then 'python3' on PATH — none were available." 1>&2
echo "  Run 'make install' in the main checkout to create the venv." 1>&2
exit 1
