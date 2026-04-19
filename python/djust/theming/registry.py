"""
Theme Registry — single source of truth for all registered presets,
design systems (themes), and theme manifests.

Thread-safe singleton, populated during AppConfig.ready() via discover().
Third-party apps register custom presets/themes in their own ready().
"""

import importlib
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ThemeRegistry:
    """Singleton registry for theme presets and design systems.

    Thread-safe. Populated during AppConfig.ready() via discover().
    Third-party apps call register_preset()/register_theme() in their own ready().
    """

    _instance: Optional["ThemeRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._presets = {}
                    inst._themes = {}  # design systems
                    inst._packs = {}  # theme packs
                    inst._manifests = {}  # ThemeManifest objects
                    inst._discovered = False
                    cls._instance = inst
        return cls._instance

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register_preset(self, name: str, preset) -> None:
        """Register a color preset. Overwrites if name exists."""
        with self._lock:
            self._presets[name] = preset

    def register_theme(self, name: str, theme) -> None:
        """Register a design system. Overwrites if name exists."""
        with self._lock:
            self._themes[name] = theme

    def register_pack(self, name: str, pack) -> None:
        """Register a theme pack. Overwrites if name exists."""
        with self._lock:
            self._packs[name] = pack

    def register_manifest(self, name: str, manifest) -> None:
        """Register a parsed ThemeManifest."""
        with self._lock:
            self._manifests[name] = manifest

    # ------------------------------------------------------------------
    # Lookup API
    # ------------------------------------------------------------------

    def get_preset(self, name: str, default=None):
        """Get a preset by name, or *default* if not found."""
        return self._presets.get(name, default)

    def get_theme(self, name: str, default=None):
        """Get a design system by name, or *default* if not found."""
        return self._themes.get(name, default)

    def get_pack(self, name: str, default=None):
        """Get a theme pack by name, or *default* if not found."""
        return self._packs.get(name, default)

    def get_manifest(self, name: str):
        """Get a ThemeManifest by name, or None if not found."""
        return self._manifests.get(name)

    def has_preset(self, name: str) -> bool:
        return name in self._presets

    def has_theme(self, name: str) -> bool:
        return name in self._themes

    def has_pack(self, name: str) -> bool:
        return name in self._packs

    def list_presets(self) -> dict:
        """Return a shallow copy of all registered presets."""
        return dict(self._presets)

    def list_themes(self) -> dict:
        """Return a shallow copy of all registered design systems."""
        return dict(self._themes)

    def list_packs(self) -> dict:
        """Return a shallow copy of all registered theme packs."""
        return dict(self._packs)

    def list_manifests(self) -> dict:
        """Return a shallow copy of all registered manifests."""
        return dict(self._manifests)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> None:
        """Populate registry from all sources. Called from apps.py ready()."""
        if self._discovered:
            return
        with self._lock:
            if self._discovered:
                return
            self._do_discover()
            self._discovered = True

    def _do_discover(self):
        """Internal: load from built-in dicts, DJUST_THEMES setting, themes_dir."""
        # 1. Built-in presets
        from .presets import THEME_PRESETS

        for name, preset in THEME_PRESETS.items():
            self._presets[name] = preset

        # 2. Built-in design systems
        from .theme_packs import DESIGN_SYSTEMS

        for name, ds in DESIGN_SYSTEMS.items():
            self._themes[name] = ds

        # 3. Built-in theme packs
        from .theme_packs import THEME_PACKS

        for name, pack in THEME_PACKS.items():
            self._packs[name] = pack

        # 4. DJUST_THEMES setting (pip-installed theme packages)
        self._discover_from_settings()

        # 4. themes_dir (convention-based, from theme.toml files)
        self._discover_from_themes_dir()

    def _discover_from_settings(self):
        """Load themes from DJUST_THEMES setting."""
        from django.conf import settings

        theme_packages = getattr(settings, "DJUST_THEMES", [])
        for package_name in theme_packages:
            try:
                self._load_theme_package(package_name)
            except Exception:
                logger.warning("Failed to load theme package '%s'", package_name)

    def _load_theme_package(self, package_name: str):
        """Load a pip-installed theme package by importing its module."""
        mod = importlib.import_module(package_name)
        # Convention: package exposes get_theme_manifest() -> ThemeManifest
        if hasattr(mod, "get_theme_manifest"):
            manifest = mod.get_theme_manifest()
            # Detect templates directory in the package
            self._detect_package_templates(mod, manifest)
            self._manifests[manifest.name] = manifest
        # Convention: package exposes PRESETS dict
        if hasattr(mod, "PRESETS"):
            for name, preset in mod.PRESETS.items():
                self._presets[name] = preset
        # Convention: package exposes DESIGN_SYSTEMS dict
        if hasattr(mod, "DESIGN_SYSTEMS"):
            for name, ds in mod.DESIGN_SYSTEMS.items():
                self._themes[name] = ds
        # Convention: package exposes THEME_PACKS dict
        if hasattr(mod, "THEME_PACKS"):
            for name, pack in mod.THEME_PACKS.items():
                self._packs[name] = pack

    @staticmethod
    def _detect_package_templates(mod, manifest):
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

    def _discover_from_themes_dir(self):
        """Load theme.toml manifests from configured themes_dir."""
        from django.conf import settings

        from .manager import get_theme_config

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
            self._manifests[manifest.name] = manifest

    # ------------------------------------------------------------------
    # Reset (for testing)
    # ------------------------------------------------------------------

    @classmethod
    def _reset(cls):
        """Reset singleton. For tests only."""
        with cls._lock:
            cls._instance = None


# Module-level convenience accessor
def get_registry() -> ThemeRegistry:
    """Get the global ThemeRegistry singleton."""
    return ThemeRegistry()


# ------------------------------------------------------------------
# Public registration API — use these in AppConfig.ready()
# ------------------------------------------------------------------


def register_preset(name: str, preset) -> None:
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


def register_design_system(name: str, design_system) -> None:
    """Register a custom design system.

    Call in your AppConfig.ready():

        from djust.theming.registry import register_design_system
        from djust.theming.theme_packs import DesignSystem

        register_design_system("my_design", DesignSystem(...))
    """
    get_registry().register_theme(name, design_system)


def register_theme_pack(name: str, pack) -> None:
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
