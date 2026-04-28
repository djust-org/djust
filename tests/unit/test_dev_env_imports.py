"""Regression tests for dev-env critical imports (#1165 sub-item c).

PR #1149 added ``markdown`` and ``nh3`` to
``[project.optional-dependencies.dev]`` because
``djust.components.components.__init__`` eagerly imports
``Markdown`` (which needs both packages). Without those in dev,
pytest collection fails with an opaque ``ModuleNotFoundError``
during the very first ``import djust.components`` chain.

This test is a hard fail-fast guard: it runs at module-import time
in the test process, so a missing dep surfaces during collection
of THIS test (with a clear traceback) rather than in some random
unrelated test downstream.

Companion: ``scripts/check-dev-env-imports.py`` runs the same check
out-of-band (e.g. for pre-commit / Makefile hooks). The script is
the visible artifact; this test is the automated guard. They share
the same source of truth (:data:`CRITICAL_IMPORTS_HINTS`).
"""

from __future__ import annotations

import importlib

import pytest

# Paired list of (module_path, install hint). Mirrors
# ``scripts/check-dev-env-imports.py`` ``CRITICAL_IMPORTS``.
# When you add an eager-imported module to djust, add it here AND in
# the script.
CRITICAL_IMPORTS_HINTS = [
    (
        "djust.components.components",
        "Run 'uv sync' or 'pip install -e .[dev]' (#1149).",
    ),
    (
        "djust.components.components.markdown",
        "Install 'markdown' + 'nh3' (transitive deps of the Markdown component).",
    ),
]


@pytest.mark.parametrize(
    "module_path,hint",
    CRITICAL_IMPORTS_HINTS,
    ids=[m for m, _ in CRITICAL_IMPORTS_HINTS],
)
def test_critical_dev_env_imports(module_path: str, hint: str) -> None:
    """The dev-env must be able to import every entry in
    :data:`CRITICAL_IMPORTS_HINTS`. Note: we use a hard ``import``
    (and ``pytest.fail`` on ImportError) — NOT
    ``pytest.importorskip`` — because for a dev-env we want a hard
    fail, not a silent skip. A skip would let CI go green with a
    broken environment."""
    try:
        importlib.import_module(module_path)
    except ImportError as exc:
        pytest.fail(
            "dev-env critical import failed: %s\n"
            "  error: %s\n"
            "  hint:  %s" % (module_path, exc, hint)
        )
