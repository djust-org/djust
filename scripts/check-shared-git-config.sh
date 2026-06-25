#!/usr/bin/env bash
# check-shared-git-config.sh — detect (and optionally repair) a corrupted
# `core.bare = true` in the SHARED `.git/config` (issue #1938).
#
# WHY: a linked `git worktree` shares ONE `.git/config` with the main checkout
# (the worktree's `.git` is a FILE pointing at `<main>/.git/worktrees/<name>`,
# and `core.*` is read from the shared `[core]` section). If anything flips
# `core.bare` to `true` in that shared config — a build/PyO3-repoint step that
# repoints the compiled extension via `git config core.bare true` (the #1804
# pattern), an IDE/GitKraken integration, or a stray manual command — then
# `git status` / `git push` break in BOTH the worktree AND the main checkout:
# every tracked file shows as deleted, because git now thinks the work tree
# has no working directory. Recovery is `git config core.bare false`.
#
# This script makes the #1804 "verify `git config core.bare` is false as a
# reflex" rule runnable. It reads `core.bare` from the SHARED config (resolved
# via `--git-common-dir`, so it works identically from any worktree or the main
# checkout) and:
#   - default:   prints status; exits 0 if bare is false/unset, 1 if true.
#   - --fix:     if bare is true, resets it to false (the documented recovery)
#                and exits 0; otherwise a no-op exit 0.
#
# It NEVER sets `core.bare true`. The only write it can make is the recovery
# `core.bare false`, gated behind --fix and only when a leak is detected.
#
# Usage:
#   bash scripts/check-shared-git-config.sh          # check (CI / reflex)
#   bash scripts/check-shared-git-config.sh --fix    # check + auto-recover
#
# Recommended worktree-subagent reflex (per CONTRIBUTING "Working in a
# `git worktree`"): run the check before AND after a `git push`, and `--fix`
# if it ever reports a leak.
set -euo pipefail

FIX=0
if [ "${1:-}" = "--fix" ]; then
    FIX=1
    shift
fi

# Resolve the SHARED git config. `--git-common-dir` points at the shared
# `.git` directory for both the main checkout and every linked worktree, so
# `<common-dir>/config` is the file the corruption lands in. `core.bare` is a
# `[core]`-section key, which git reads from this shared config (NOT from the
# per-worktree `config.worktree`), so this is the value `git status` actually
# uses.
common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [ -z "$common_dir" ]; then
    echo "check-shared-git-config: not inside a git repository — nothing to check." >&2
    exit 0
fi
shared_config="$common_dir/config"

# Read core.bare from the SHARED config file specifically (`--file`), so a
# per-worktree override can't mask the shared corruption.
bare="$(git config --file "$shared_config" --get core.bare 2>/dev/null || true)"

case "$bare" in
    true | yes | 1 | on)
        echo "LEAK DETECTED: core.bare = $bare in the shared config:" >&2
        echo "  $shared_config" >&2
        echo "  This corrupts git in BOTH the worktree and the main checkout" >&2
        echo "  (every tracked file shows as deleted). See issue #1938." >&2
        if [ "$FIX" -eq 1 ]; then
            git config --file "$shared_config" core.bare false
            echo "RECOVERED: reset core.bare -> false in the shared config." >&2
            exit 0
        fi
        echo "  Recover with: bash scripts/check-shared-git-config.sh --fix" >&2
        echo "           (or: git config core.bare false)" >&2
        exit 1
        ;;
    "" | false | no | 0 | off)
        echo "OK: core.bare is '${bare:-<unset>}' in the shared config ($shared_config)."
        exit 0
        ;;
    *)
        # Unexpected value — surface it but don't auto-mutate.
        echo "check-shared-git-config: unexpected core.bare value '$bare' in $shared_config" >&2
        exit 1
        ;;
esac
