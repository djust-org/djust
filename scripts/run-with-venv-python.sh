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
#
# WHY (#1810): #1796 fixed the *interpreter* resolution, but `maturin develop`
# installs `djust` editable via a plain `djust.pth` that appends the MAIN
# checkout's `python/` to `sys.path`. A `git push` from a linked worktree
# therefore runs the pre-push pytest suite against the MAIN tree's source, NOT
# the worktree's changes — so worktree pushes still needed `--no-verify`.
#
# `--worktree-pythonpath` mode emits the path that, when PREPENDED to
# `PYTHONPATH`, makes the CURRENT worktree's `python/` win over the `.pth`
# (PYTHONPATH is inserted before `.pth` processing). It also symlinks the
# matching compiled Rust `.so` from the editable-install target into the
# worktree's `python/djust/` so `import djust._rust` keeps working (the `.so`
# is gitignored and only exists in the checkout `maturin develop` built).
# Emits nothing (exit 0) when run from the editable-install target itself or
# outside a git tree — in those cases the `.pth` already points at the right
# source and no shadow is needed.
# CAVEAT: this shadows only Python source. Rust (`djust._rust`) changes still
# need `maturin develop` run against the worktree before they are gated.
set -euo pipefail

WORKTREE_PYTHONPATH=0
PRINT_ONLY=0
if [ "${1:-}" = "--worktree-pythonpath" ]; then
    WORKTREE_PYTHONPATH=1
    shift
elif [ "${1:-}" = "--print" ]; then
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

# --worktree-pythonpath: emit the path to PREPEND to PYTHONPATH so a worktree
# push tests the worktree's Python source (#1810). No-op (silent exit 0) when
# not in a linked worktree or when the worktree IS the editable-install target.
if [ "$WORKTREE_PYTHONPATH" -eq 1 ]; then
    # `--show-toplevel` is the CURRENT working tree root (worktree or main).
    worktree_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    # Nothing to do if we can't resolve a git tree, can't resolve the main
    # root, or this tree already IS the main checkout (its `.pth` points here).
    if [ -z "$worktree_root" ] || [ -z "$main_root" ] || \
       [ "$worktree_root" = "$main_root" ]; then
        exit 0
    fi
    worktree_python="$worktree_root/python"
    # No worktree `python/djust` package -> nothing to shadow.
    if [ ! -d "$worktree_python/djust" ]; then
        exit 0
    fi
    # Symlink the compiled Rust extension from the main checkout into the
    # worktree so `import djust._rust` resolves once Python source is shadowed.
    # The `.so` is gitignored (`*.so`), so the symlink never pollutes
    # `git status`. We can only do this if the venv interpreter is resolvable;
    # use its cache tag to pick the right `.so` filename. Failures here are
    # non-fatal — Python source shadowing still works, only Rust does not.
    venv_python_wt=""
    if [ -n "$main_root" ] && [ -x "$main_root/.venv/bin/python" ]; then
        venv_python_wt="$main_root/.venv/bin/python"
    elif [ -x ".venv/bin/python" ]; then
        venv_python_wt="$(cd "$(dirname .venv/bin/python)" && pwd)/python"
    fi
    if [ -n "$venv_python_wt" ]; then
        cache_tag="$("$venv_python_wt" -c 'import sys; print(sys.implementation.cache_tag)' 2>/dev/null || true)"
        if [ -n "$cache_tag" ]; then
            for so in "$main_root"/python/djust/_rust."$cache_tag"-*.so; do
                [ -e "$so" ] || continue
                target="$worktree_python/djust/$(basename "$so")"
                # Only (re)link if missing or pointing elsewhere.
                if [ ! -e "$target" ] || [ "$(readlink "$target" 2>/dev/null)" != "$so" ]; then
                    ln -sf "$so" "$target" 2>/dev/null || true
                fi
            done
        fi
    fi
    printf '%s\n' "$worktree_python"
    exit 0
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
