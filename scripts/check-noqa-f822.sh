#!/bin/bash
# Pre-push hook: flag `noqa: F822` annotations in __all__ patterns.
#
# Ruff silences py/undefined-export with `noqa: F822` but CodeQL flags it
# anyway. Centralizing the check here lets us catch new instances at push
# time and remediate via TYPE_CHECKING blocks (see PR #924) before CodeQL
# alerts pile up. Filed as Action Tracker #146 / GH #1061.
#
# Strategy:
#   1. Find every `noqa: F822` in tracked Python files.
#   2. For each, check if the file has been modified vs origin/main.
#   3. If yes, fail the push with a helpful pointer.
#
# Run manually:
#   bash scripts/check-noqa-f822.sh
#   bash scripts/check-noqa-f822.sh --all   # include unchanged files
set -euo pipefail

INCLUDE_ALL="${1:-}"
PATTERN="noqa: *F822"

if [ "$INCLUDE_ALL" = "--all" ]; then
    matches=$(git grep -nE "$PATTERN" -- 'python/**/*.py' 'tests/**/*.py' 2>/dev/null || true)
else
    # Limit to files changed in this push (vs origin/main).
    changed_files=$(git diff --name-only --diff-filter=ACMR origin/main..HEAD -- 'python/**/*.py' 'tests/**/*.py' 2>/dev/null || true)
    if [ -z "$changed_files" ]; then
        exit 0
    fi
    matches=$(echo "$changed_files" | xargs -I {} grep -HnE "$PATTERN" {} 2>/dev/null || true)
fi

if [ -z "$matches" ]; then
    exit 0
fi

echo "ERROR: \`# noqa: F822\` annotations in __all__ are deprecated."
echo
echo "$matches" | sed 's/^/  /'
echo
echo "Why: Ruff silences py/undefined-export with noqa: F822 but CodeQL"
echo "flags it as a security issue. Use TYPE_CHECKING-conditional imports"
echo "instead (see PR #924 for the canonical pattern):"
echo
echo "    from typing import TYPE_CHECKING"
echo "    if TYPE_CHECKING:"
echo "        from .submodule import SymbolName"
echo
echo "If the symbol is needed at runtime (not just for type hints), import"
echo "it eagerly at module top — don't \`noqa: F822\` it."
echo
echo "To override (truly unfixable case): run \`git push --no-verify\`"
echo "but file a tech-debt issue with the rationale."
exit 1
