"""
Theme preset definitions using HSL color tokens.

Based on shadcn/ui theming system with CSS custom properties.
Each preset is defined in its own file under themes/.

Note: the dataclass types (``ColorScale``, ``ThemeTokens``, ``SurfaceTreatment``,
``ThemePreset``) live in ``_types.py`` so that ``themes/_base.py`` can
import them without going back through this module — avoiding the cyclic
import that CodeQL's ``py/unsafe-cyclic-import`` flagged across ~55 theme
files. They are re-exported from this module for backward compatibility so
existing consumers like ``from djust.theming.presets import ColorScale``
keep working unchanged.

The built-in ``*_THEME`` PRESET imports and the ``THEME_PRESETS`` dict
live in ``_builtin_presets.py``. That extraction (PR #TBD, alerts
#2352/#2351/#1900/#1883) breaks the ``presets ↔ registry`` cyclic
import — ``registry.py`` now imports ``THEME_PRESETS`` from
``_builtin_presets`` directly instead of through this module. We
re-export ``THEME_PRESETS`` from here so existing user code (and the
~10 internal consumers listed in ``__all__``) keeps working.
"""

from ._builtin_presets import *  # noqa: F401, F403 — back-compat re-export of *_THEME names
from ._builtin_presets import DEFAULT_THEME, THEME_PRESETS
from ._types import ColorScale, SurfaceTreatment, ThemePreset, ThemeTokens


def get_preset(name: str) -> ThemePreset:
    """Get a theme preset by name.

    Resolution order, matching ``theme_packs.get_theme_pack()``:

    1. Runtime registry (presets added via ``register_preset()``).
    2. Built-in static ``THEME_PRESETS`` dict.
    3. ``DEFAULT_THEME`` fallback.

    Before #1595 only step 2 + 3 ran, so user-registered presets were visible
    to the manager/registry/introspection but invisible to the CSS generator
    that ultimately renders ``--primary`` etc. into ``:root``.
    """
    from ._registry_accessor import get_registry

    return get_registry().get_preset(name) or THEME_PRESETS.get(name, DEFAULT_THEME)


def list_presets() -> list[dict]:
    """Return list of available presets with metadata."""
    return [
        {
            "name": preset.name,
            "display_name": preset.display_name,
            "description": preset.description,
        }
        for preset in THEME_PRESETS.values()
    ]


__all__ = [
    # Re-exported types (back-compat)
    "ColorScale",
    "ThemeTokens",
    "SurfaceTreatment",
    "ThemePreset",
    # Registry / helpers
    "THEME_PRESETS",
    "get_preset",
    "list_presets",
]
