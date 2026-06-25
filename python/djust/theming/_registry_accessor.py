"""Theme registry singleton + accessor — leaf module (CodeQL py/cyclic-import #1662).

Holds the ``ThemeRegistry`` class (registration + lookup + thread-safe
singleton + ``discover()``) and the ``get_registry()`` accessor, extracted from
``registry.py``. This module imports ONLY stdlib — never ``.theme_packs`` /
``.manifest`` / ``.presets`` / ``.registry`` — so that ``theme_packs`` and
``manifest`` (which only need the singleton) can reach the registry WITHOUT
importing ``registry`` (the back-edge that closed the
``registry ↔ theme_packs`` and ``registry ↔ manifest`` import cycles).

Discovery (the only edges from the registry *toward* ``theme_packs`` /
``manifest``) is wired in ``registry.py`` and installed here via
``set_discovery_hook`` — keeping it out of this leaf so the leaf has zero
theming out-edges. The dependency direction is now one-directional:

    registry ──> theme_packs / manifest / _builtin_presets / _config
    theme_packs / manifest / presets / ... ──> _registry_accessor  (leaf)

``registry`` re-exports ``ThemeRegistry`` / ``get_registry`` for back-compat, so
``from djust.theming.registry import get_registry`` keeps working — no behavior
change, no public-API change.
"""

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Discovery hook, installed by ``registry.py`` at import time. Kept as a
# module-level callable so this leaf never imports ``theme_packs`` / ``manifest``
# (which would re-create the #1662 SCC). Signature: ``hook(registry) -> None``.
_discovery_hook: Optional[Callable[["ThemeRegistry"], None]] = None


def set_discovery_hook(hook: Callable[["ThemeRegistry"], None]) -> None:
    """Install the discovery callback (called once from ``registry.py``).

    The hook populates a registry from the built-in dicts, the ``DJUST_THEMES``
    setting, and the convention-based ``themes_dir``. It lives in ``registry.py``
    (which may import ``theme_packs`` / ``manifest``) so this leaf stays free of
    theming out-edges.
    """
    global _discovery_hook
    _discovery_hook = hook


class ThemeRegistry:
    """Singleton registry for theme presets and design systems.

    Thread-safe. Populated during AppConfig.ready() via discover().
    Third-party apps call register_preset()/register_theme() in their own ready().
    """

    _instance: Optional["ThemeRegistry"] = None
    _lock = threading.Lock()

    # Instance state initialized in __new__ (via the singleton-construction
    # local ``inst``); declared at class level so the types are visible to
    # static analysis (the attrs are populated once per process).
    _presets: dict[str, Any]
    _themes: dict[str, Any]  # design systems
    _packs: dict[str, Any]  # theme packs
    _manifests: dict[str, Any]  # ThemeManifest objects
    _discovered: bool

    def __new__(cls) -> "ThemeRegistry":
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

    def register_preset(self, name: str, preset: Any) -> None:
        """Register a color preset. Overwrites if name exists."""
        with self._lock:
            self._presets[name] = preset

    def register_theme(self, name: str, theme: Any) -> None:
        """Register a design system. Overwrites if name exists."""
        with self._lock:
            self._themes[name] = theme

    def register_pack(self, name: str, pack: Any) -> None:
        """Register a theme pack. Overwrites if name exists."""
        with self._lock:
            self._packs[name] = pack

    def register_manifest(self, name: str, manifest: Any) -> None:
        """Register a parsed ThemeManifest."""
        with self._lock:
            self._manifests[name] = manifest

    # ------------------------------------------------------------------
    # Lookup API
    # ------------------------------------------------------------------

    def get_preset(self, name: str, default: Any = None) -> Any:
        """Get a preset by name, or *default* if not found."""
        return self._presets.get(name, default)

    def get_theme(self, name: str, default: Any = None) -> Any:
        """Get a design system by name, or *default* if not found."""
        return self._themes.get(name, default)

    def get_pack(self, name: str, default: Any = None) -> Any:
        """Get a theme pack by name, or *default* if not found."""
        return self._packs.get(name, default)

    def get_manifest(self, name: str) -> Any:
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
        """Populate registry from all sources. Called from apps.py ready().

        Delegates to the discovery hook installed by ``registry.py`` via
        ``set_discovery_hook`` (so this leaf never imports ``theme_packs`` /
        ``manifest``). Importing ``djust.theming.registry`` anywhere installs the
        hook; ``apps.ready()`` imports it before calling ``discover()``.
        """
        if self._discovered:
            return
        with self._lock:
            if self._discovered:
                return
            # The hook is installed by ``registry.py`` at import time, and
            # importing the ``djust.theming`` package imports ``.registry``
            # (see __init__.py) — so any caller that reached ``get_registry``
            # has already installed the hook. We intentionally do NOT import
            # ``registry`` here: that would give this leaf a back-edge into the
            # SCC (#1662). If the hook is somehow absent, discovery is a no-op
            # (registrations still work) rather than reopening the cycle.
            if _discovery_hook is not None:
                _discovery_hook(self)
            self._discovered = True

    # ------------------------------------------------------------------
    # Reset (for testing)
    # ------------------------------------------------------------------

    @classmethod
    def _reset(cls) -> None:
        """Reset singleton. For tests only."""
        with cls._lock:
            cls._instance = None


# Module-level convenience accessor
def get_registry() -> ThemeRegistry:
    """Get the global ThemeRegistry singleton."""
    return ThemeRegistry()
