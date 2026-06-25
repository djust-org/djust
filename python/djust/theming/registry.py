"""
Theme Registry — discovery wiring + public registration API.

The ``ThemeRegistry`` singleton + ``get_registry`` accessor live in the leaf
module ``_registry_accessor`` (which imports nothing from theming) so that
``theme_packs`` / ``manifest`` can reach the singleton without importing back
into ``registry`` — breaking the ``registry ↔ theme_packs`` and
``registry ↔ manifest`` import cycles (CodeQL py/cyclic-import #1662).

This module owns the *discovery* logic (the only edges from the registry toward
``theme_packs`` / ``manifest`` / ``_builtin_presets`` / ``_config``) and installs
it as the registry's discovery hook at import time. It re-exports
``ThemeRegistry`` / ``get_registry`` for back-compat, so existing imports such as
``from djust.theming.registry import get_registry`` keep working unchanged.
"""

import importlib
import logging
from pathlib import Path
from typing import Any

# Re-export the singleton + accessor from the leaf for back-compat. External
# callers and ``__init__`` import these from ``.registry``; identity is preserved.
from ._registry_accessor import (  # noqa: F401
    ThemeRegistry,
    get_registry,
    set_discovery_hook,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Discovery (installed as the registry's discovery hook). These functions hold
# the only registry → theme_packs / manifest edges; keeping them here (not in
# the leaf) keeps the dependency one-directional.
# ------------------------------------------------------------------


def _do_discover(registry: ThemeRegistry) -> None:
    """Internal: load from built-in dicts, DJUST_THEMES setting, themes_dir."""
    # 1. Built-in presets
    # Import from _builtin_presets, NOT .presets — the latter would
    # re-introduce the presets ↔ registry cycle (CodeQL alerts
    # #2352/#2351/#1900/#1883).
    from ._builtin_presets import THEME_PRESETS

    for name, preset in THEME_PRESETS.items():
        registry._presets[name] = preset

    # 2. Built-in design systems
    from .theme_packs import DESIGN_SYSTEMS

    for name, ds in DESIGN_SYSTEMS.items():
        registry._themes[name] = ds

    # 3. Built-in theme packs
    from .theme_packs import THEME_PACKS

    for name, pack in THEME_PACKS.items():
        registry._packs[name] = pack

    # 4. DJUST_THEMES setting (pip-installed theme packages)
    _discover_from_settings(registry)

    # 5. themes_dir (convention-based, from theme.toml files)
    _discover_from_themes_dir(registry)


def _discover_from_settings(registry: ThemeRegistry) -> None:
    """Load themes from DJUST_THEMES setting."""
    from django.conf import settings

    theme_packages = getattr(settings, "DJUST_THEMES", [])
    for package_name in theme_packages:
        try:
            _load_theme_package(registry, package_name)
        except Exception:
            logger.warning("Failed to load theme package '%s'", package_name)


def _load_theme_package(registry: ThemeRegistry, package_name: str) -> None:
    """Load a pip-installed theme package by importing its module."""
    mod = importlib.import_module(package_name)
    # Convention: package exposes get_theme_manifest() -> ThemeManifest
    if hasattr(mod, "get_theme_manifest"):
        manifest = mod.get_theme_manifest()
        # Detect templates directory in the package
        _detect_package_templates(mod, manifest)
        registry._manifests[manifest.name] = manifest
    # Convention: package exposes PRESETS dict
    if hasattr(mod, "PRESETS"):
        for name, preset in mod.PRESETS.items():
            registry._presets[name] = preset
    # Convention: package exposes DESIGN_SYSTEMS dict
    if hasattr(mod, "DESIGN_SYSTEMS"):
        for name, ds in mod.DESIGN_SYSTEMS.items():
            registry._themes[name] = ds
    # Convention: package exposes THEME_PACKS dict
    if hasattr(mod, "THEME_PACKS"):
        for name, pack in mod.THEME_PACKS.items():
            registry._packs[name] = pack


def _detect_package_templates(mod: Any, manifest: Any) -> None:
    """Detect templates/ dir in a package and set manifest.templates_dir."""
    if manifest.templates_dir is not None:
        return  # Already set by the package itself
    mod_file = getattr(mod, "__file__", None)
    if not mod_file:
        return
    pkg_dir = Path(mod_file).parent
    templates_dir = pkg_dir / "templates"
    if templates_dir.is_dir():
        manifest.templates_dir = templates_dir


def _discover_from_themes_dir(registry: ThemeRegistry) -> None:
    """Load theme.toml manifests from configured themes_dir."""
    from django.conf import settings

    from ._config import get_theme_config

    config = get_theme_config()
    themes_dir_rel = config.get("themes_dir", "themes/")
    base_dir = getattr(settings, "BASE_DIR", None)
    if not base_dir:
        return

    themes_dir = Path(base_dir) / themes_dir_rel
    if not themes_dir.is_dir():
        return

    from .manifest import load_theme_manifests

    for manifest in load_theme_manifests(themes_dir):
        registry._manifests[manifest.name] = manifest


# Install the discovery hook so ``ThemeRegistry.discover()`` (in the leaf) runs
# the wiring above. Importing this module is enough to wire discovery; apps.py
# imports it before calling ``get_registry().discover()``.
set_discovery_hook(_do_discover)


# ------------------------------------------------------------------
# Public registration API — use these in AppConfig.ready()
# ------------------------------------------------------------------


def register_preset(name: str, preset: Any) -> None:
    """Register a custom color preset.

    Call in your AppConfig.ready():

        from djust.theming.registry import register_preset
        from djust.theming.presets import ThemePreset, ThemeTokens

        register_preset("my_brand", ThemePreset(
            name="my_brand",
            display_name="My Brand",
            light=ThemeTokens(primary="220 90% 56%", ...),
            dark=ThemeTokens(primary="220 90% 70%", ...),
        ))
    """
    get_registry().register_preset(name, preset)


def register_design_system(name: str, design_system: Any) -> None:
    """Register a custom design system.

    Call in your AppConfig.ready():

        from djust.theming.registry import register_design_system
        from djust.theming.theme_packs import DesignSystem

        register_design_system("my_design", DesignSystem(...))
    """
    get_registry().register_theme(name, design_system)


def register_theme_pack(name: str, pack: Any) -> None:
    """Register a custom theme pack.

    Call in your AppConfig.ready():

        from djust.theming.registry import register_theme_pack
        from djust.theming.theme_packs import ThemePack

        register_theme_pack("my_pack", ThemePack(
            name="my_pack",
            display_name="My Theme",
            design_theme="material",
            color_preset="my_brand",
            ...
        ))
    """
    get_registry().register_pack(name, pack)
