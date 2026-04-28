#!/usr/bin/env python3
"""Verify the dev-env can import critical eager-import modules.

Several djust modules eagerly import optional-extra dependencies at
package init time. The most prominent: ``djust.components.components``
imports :class:`djust.components.components.markdown.Markdown`, which
in turn requires both ``markdown`` and ``nh3`` at import time. If a
dev-env is missing either, ``pytest`` collection fails with an opaque
``ImportError`` long before the user-facing tests run.

#1149 added ``markdown`` and ``nh3`` to the
``[project.optional-dependencies.dev]`` block in ``pyproject.toml`` for
this exact reason. This script is a regression guard: run it after a
fresh ``uv sync`` (or ``pip install -e .[dev]``) and it asserts every
module in :data:`CRITICAL_IMPORTS` actually imports cleanly.

Exit codes:
    0 — all imports succeeded.
    1 — at least one import failed; the missing-package hint is printed.

Usage::

    python scripts/check-dev-env-imports.py

Could be wired into ``Makefile`` or ``.pre-commit-config.yaml`` later;
this script ships standalone first so a follow-up PR can pick up
automation without coupling.

#1165 (sub-item c).
"""

from __future__ import annotations

import importlib
import sys
from typing import List, Tuple

# Each entry: (module_path, hint_for_missing_dep).
#
# When adding a new entry, prefer (a) modules that eagerly import
# optional-extra packages at top level, or (b) modules whose silent
# absence has caused test-collection failures in the past. The hint is
# printed verbatim when the import fails, so name the install-extra
# (e.g. ``pip install -e '.[dev]'`` or ``pip install markdown nh3``).
CRITICAL_IMPORTS: List[Tuple[str, str]] = [
    (
        "djust.components.components",
        "djust.components.components eagerly imports the Markdown component "
        "(see #1149). Install dev deps: 'uv sync' or 'pip install -e .[dev]' "
        "(pulls in markdown + nh3).",
    ),
    (
        "djust.components.components.markdown",
        "Markdown component requires 'markdown' and 'nh3' at import time. "
        "Install: 'pip install markdown nh3' or 'pip install -e .[dev]'.",
    ),
]


def main() -> int:
    failures: List[Tuple[str, str, BaseException]] = []
    for module_path, hint in CRITICAL_IMPORTS:
        try:
            importlib.import_module(module_path)
        except BaseException as exc:  # noqa: BLE001 — diagnostic script
            failures.append((module_path, hint, exc))

    if not failures:
        print("OK: all %d critical dev-env imports succeeded." % len(CRITICAL_IMPORTS))
        return 0

    print(
        "FAIL: %d/%d critical dev-env imports failed."
        % (len(failures), len(CRITICAL_IMPORTS)),
        file=sys.stderr,
    )
    for module_path, hint, exc in failures:
        print("", file=sys.stderr)
        print("  module: %s" % module_path, file=sys.stderr)
        print("  error:  %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        print("  hint:   %s" % hint, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
