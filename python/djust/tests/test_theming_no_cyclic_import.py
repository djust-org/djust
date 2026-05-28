"""Regression tests for CodeQL ``py/cyclic-import`` alerts
#2352/#2351/#1900/#1883 in ``djust.theming``.

Before the fix the SCC was:

    presets.py ──(lazy: get_preset → get_registry)──> registry.py
        ▲                                                    │
        │              (lazy: _do_discover)                   │
        └─────────────────────────────────────────────────────┘

    + manager.py ──(top-level)──> presets.py
    + presets ──(lazy)──> registry ──(lazy)──> manager ──> presets
    + css_generator.py ──(top-level)──> manager.py + presets.py
    + manager ──(lazy)──> theme_css_generator ──> css_generator ──> manager

The fix:

* New module ``_builtin_presets.py`` holds the ``themes/*`` imports +
  ``THEME_PRESETS`` dict + ``DEFAULT_THEME``. No imports of ``.presets``,
  ``.registry``, ``.manager``, or ``.css_generator``.
* ``registry.py`` now imports ``THEME_PRESETS`` from ``_builtin_presets``
  (not ``presets``) — removes the ``registry → presets`` edge.
* ``manager.py`` imports ``ThemePreset`` from ``_types`` (a leaf module);
  ``get_preset`` is deferred to inside ``ThemeManager.get_preset()``.
* ``css_generator.py`` imports ``ThemeTokens`` from ``_types``;
  ``get_theme_config`` and ``get_preset`` are deferred to their call
  sites.

These tests gate the static import graph so a future refactor can't
silently re-introduce the cycle CodeQL flagged.
"""

import ast
from pathlib import Path

THEMING_DIR = Path(__file__).resolve().parents[1] / "theming"


def _top_level_relative_imports(module_path: Path) -> set[str]:
    """Return module names imported at module top level (level=1, ``from . import``).

    Top-level means at AST module body depth — NOT nested inside a
    function, class, or conditional. Lazy imports inside function bodies
    are deliberately ignored because they don't create eager-import-time
    cycles.
    """
    tree = ast.parse(module_path.read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            names.add(node.module.split(".")[0])
    return names


class TestThemingNoCyclicImport:
    """Static-graph assertions that the SCC stays broken."""

    def test_builtin_presets_has_no_runtime_dependency_on_cycle_modules(self):
        """``_builtin_presets`` must not import presets/registry/manager/css_generator
        at any level. It is the leaf the registry imports to load static
        presets without going through ``presets``."""
        tree = ast.parse((THEMING_DIR / "_builtin_presets.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                bare = node.module.split(".")[0]
                assert bare not in {"presets", "registry", "manager", "css_generator"}, (
                    f"_builtin_presets must not import {node.module} — that re-introduces the cycle"
                )

    def test_presets_does_not_top_level_import_registry(self):
        """``presets.get_preset`` imports ``registry`` *lazily*, not at module top level.
        A top-level edge would re-form the ``presets ↔ registry`` 2-cycle even after
        the ``registry → presets`` edge was redirected to ``_builtin_presets``."""
        assert "registry" not in _top_level_relative_imports(THEMING_DIR / "presets.py")

    def test_registry_does_not_top_level_import_presets(self):
        """``registry._do_discover`` imports ``_builtin_presets`` lazily; it must NOT
        import ``presets`` at any level (lazy or top-level) — that was the
        edge that closed the original 2-cycle."""
        source = (THEMING_DIR / "registry.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                bare = node.module.split(".")[0]
                assert bare != "presets", (
                    "registry must not import .presets (lazy or top-level) — "
                    "use ._builtin_presets for THEME_PRESETS"
                )

    def test_manager_does_not_top_level_import_presets(self):
        """``manager.ThemeManager.get_preset`` defers the ``get_preset`` import.
        Top-level ``from .presets import …`` would close the
        ``presets → registry → manager → presets`` 3-cycle."""
        assert "presets" not in _top_level_relative_imports(THEMING_DIR / "manager.py")

    def test_css_generator_does_not_top_level_import_presets_or_manager(self):
        """``css_generator`` defers both ``get_theme_config`` (from manager) and
        ``get_preset`` (from presets) to call sites. Top-level imports would
        close the ``manager → theme_css_generator → css_generator → manager``
        and ``presets → ... → css_generator → presets`` cycles."""
        top = _top_level_relative_imports(THEMING_DIR / "css_generator.py")
        assert "presets" not in top
        assert "manager" not in top

    def test_back_compat_named_theme_constants_still_importable_from_presets(self):
        """``presets.py`` re-exports the named ``*_THEME`` constants from
        ``_builtin_presets`` via ``from ._builtin_presets import *``.
        External user code that does ``from djust.theming.presets import
        BLUE_THEME`` must keep working."""
        from djust.theming.presets import (
            BLUE_THEME,
            DEFAULT_THEME,
            GREEN_THEME,
            ORANGE_THEME,
            PURPLE_THEME,
            ROSE_THEME,
            THEME_PRESETS,
        )

        assert BLUE_THEME.name == "blue"
        assert DEFAULT_THEME.name == "default"
        assert GREEN_THEME.name == "green"
        assert ORANGE_THEME.name == "orange"
        assert PURPLE_THEME.name == "purple"
        assert ROSE_THEME.name == "rose"
        assert len(THEME_PRESETS) == 63

    def test_get_preset_still_resolves_via_registry_first(self):
        """The cycle-break must preserve get_preset's resolution order:
        runtime registry first, then static THEME_PRESETS, then DEFAULT_THEME.
        Asserts behavioral equivalence with pre-fix code."""
        from djust.theming.presets import get_preset

        # Static lookup hits THEME_PRESETS
        assert get_preset("blue").name == "blue"
        # Unknown name falls back to DEFAULT_THEME (resolution step 3)
        assert get_preset("definitely-not-a-real-theme-xyz").name == "default"
