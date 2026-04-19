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


if __name__ == "__main__":
    pytest.main([__file__])
