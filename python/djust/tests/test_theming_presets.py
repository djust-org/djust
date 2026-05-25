"""
Tests for theme presets.
"""

import pytest
from djust.theming.presets import THEME_PRESETS, ThemePreset

pytestmark = pytest.mark.theming


def test_all_presets_exist():
    """Test that all expected presets exist."""
    expected_presets = ["default", "shadcn", "blue", "green", "purple", "orange", "rose"]
    for preset_name in expected_presets:
        assert preset_name in THEME_PRESETS, f"Preset '{preset_name}' not found"


def test_preset_structure():
    """Test that each preset has the correct structure."""
    for name, preset in THEME_PRESETS.items():
        assert isinstance(preset, ThemePreset), f"Preset '{name}' is not a ThemePreset"
        assert preset.name == name, f"Preset name mismatch: {preset.name} != {name}"
        assert preset.display_name, f"Preset '{name}' has no display_name"
        assert preset.description, f"Preset '{name}' has no description"
        assert preset.light, f"Preset '{name}' has no light theme"
        assert preset.dark, f"Preset '{name}' has no dark theme"


def test_theme_tokens():
    """Test that theme tokens have valid values."""
    preset = THEME_PRESETS["default"]

    # Test light mode tokens
    assert preset.light.background is not None
    assert preset.light.foreground is not None
    assert preset.light.primary is not None
    assert preset.light.primary_foreground is not None

    # Test dark mode tokens
    assert preset.dark.background is not None
    assert preset.dark.foreground is not None
    assert preset.dark.primary is not None
    assert preset.dark.primary_foreground is not None


def test_preset_get():
    """Test getting a preset by name."""
    preset = THEME_PRESETS.get("blue")
    assert preset is not None
    assert preset.name == "blue"
    assert preset.display_name == "Blue"


def test_invalid_preset():
    """Test that getting an invalid preset returns None."""
    preset = THEME_PRESETS.get("nonexistent")
    assert preset is None


def test_get_preset_consults_runtime_registry_first_1595():
    """Regression for #1595 — `presets.get_preset()` must consult the runtime
    registry first so user-registered presets actually reach the CSS generator.

    Before the fix: `get_preset()` read from the static `THEME_PRESETS` module
    dict only, so a preset added via `register_preset()` was visible to the
    manager / theme switcher / introspection (which consult the registry) but
    INVISIBLE to the renderer (which goes through this function). The user's
    `--primary` was silently replaced with the default slate-black palette.

    Mirrors the registry-first dispatch in `theme_packs.get_theme_pack()`
    (`python/djust/theming/theme_packs.py:1216-1222`).
    """
    from djust.theming import presets
    from djust.theming.registry import get_registry, register_preset

    sentinel_name = "_regression_1595_runtime_only"
    sentinel = ThemePreset(
        name=sentinel_name,
        display_name="1595 sentinel",
        description="present in runtime registry, NOT in static THEME_PRESETS",
        light=THEME_PRESETS["default"].light,
        dark=THEME_PRESETS["default"].dark,
    )

    # Pre-condition: not present in the static dict.
    assert sentinel_name not in THEME_PRESETS, (
        "test setup invariant violated — sentinel name must not exist statically"
    )

    register_preset(sentinel_name, sentinel)
    try:
        # The registry sees it.
        assert get_registry().get_preset(sentinel_name) is sentinel

        # The bug-of-record: presets.get_preset() must ALSO see it.
        # Pre-fix this returns DEFAULT_THEME instead of `sentinel`.
        result = presets.get_preset(sentinel_name)
        assert result is sentinel, (
            f"#1595: presets.get_preset({sentinel_name!r}) returned "
            f"{result.name!r} instead of the runtime-registered preset. "
            "Registry/static-dict divergence — the CSS generator would render "
            "the wrong palette."
        )
    finally:
        # Clean up so subsequent tests don't see the sentinel.
        get_registry()._presets.pop(sentinel_name, None)


def test_get_preset_static_dict_fallback_when_registry_lacks_name():
    """Companion to #1595: static dict fallback must still work for built-in
    presets that the runtime registry doesn't know about.

    Locks in the second half of the registry-first-OR-static-fallback contract.
    """
    from djust.theming import presets

    # "blue" is in the static dict; no test setup registers it at runtime.
    result = presets.get_preset("blue")
    assert result.name == "blue", f"static-dict fallback broke: got {result.name!r} for 'blue'"


def test_get_preset_unknown_name_returns_default():
    """Companion to #1595: when neither the registry nor the static dict has
    the name, `get_preset()` falls back to DEFAULT_THEME (existing behavior).
    """
    from djust.theming import presets

    result = presets.get_preset("definitely_does_not_exist_anywhere_1595")
    assert result is presets.DEFAULT_THEME, (
        f"unknown-name fallback broke: got {result.name!r} instead of DEFAULT_THEME"
    )


if __name__ == "__main__":
    pytest.main([__file__])
