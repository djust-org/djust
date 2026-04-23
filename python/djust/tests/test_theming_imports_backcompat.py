"""Regression test for backward-compat imports after the _types/_constants refactor.

All the types and instances that used to live in presets.py / theme_packs.py
must still be importable from those locations for user code that imports
them directly.
"""


def test_presets_exports_types():
    from djust.theming.presets import ColorScale, SurfaceTreatment, ThemePreset, ThemeTokens

    assert ColorScale.__module__  # just verify they're real classes
    assert ThemeTokens.__module__
    assert ThemePreset.__module__
    assert SurfaceTreatment.__module__


def test_theme_packs_exports_types():
    from djust.theming.theme_packs import (
        AnimationStyle,
        DesignSystem,
        IconStyle,
        IllustrationStyle,
        InteractionStyle,
        LayoutStyle,
        PatternStyle,
        SurfaceStyle,
        ThemePack,
        TypographyStyle,
    )

    for cls in (
        TypographyStyle,
        LayoutStyle,
        SurfaceStyle,
        IconStyle,
        AnimationStyle,
        InteractionStyle,
        DesignSystem,
        PatternStyle,
        IllustrationStyle,
        ThemePack,
    ):
        assert cls.__module__  # still importable


def test_theme_packs_exports_instances():
    from djust.theming.theme_packs import (
        ANIM_SMOOTH,
        ICON_OUTLINED,
        ILLUST_FLAT,
        ILLUST_LINE,
        INTERACT_SUBTLE,
        PATTERN_GRID,
        PATTERN_MINIMAL,
    )

    # Each is a dataclass instance with a `name` attr
    for inst in (
        PATTERN_MINIMAL,
        PATTERN_GRID,
        ILLUST_LINE,
        ILLUST_FLAT,
        ICON_OUTLINED,
        ANIM_SMOOTH,
        INTERACT_SUBTLE,
    ):
        assert hasattr(inst, "name")


def test_base_exports_identical_objects():
    """Types + instances re-exported via _base must be the SAME objects."""
    from djust.theming.presets import ColorScale as CS_presets
    from djust.theming.themes._base import ColorScale as CS_base

    assert CS_base is CS_presets

    from djust.theming.theme_packs import PATTERN_MINIMAL as PM_packs
    from djust.theming.themes._base import PATTERN_MINIMAL as PM_base

    assert PM_base is PM_packs

    from djust.theming.theme_packs import ThemePack as TP_packs
    from djust.theming.themes._base import ThemePack as TP_base

    assert TP_base is TP_packs


def test_vercel_theme_still_loads():
    from djust.theming.themes.vercel import DESIGN_SYSTEM, PACK, PRESET

    assert PRESET is not None
    assert PACK is not None
    assert DESIGN_SYSTEM is not None


def test_registries_fully_populated():
    """Lazy imports still fill DESIGN_SYSTEMS and THEME_PACKS after refactor."""
    from djust.theming.theme_packs import get_all_design_systems, get_all_theme_packs

    design_systems = get_all_design_systems()
    theme_packs = get_all_theme_packs()

    assert len(design_systems) > 50
    assert len(theme_packs) > 50
    assert "vercel" in design_systems
    assert "vercel" in theme_packs


def test_interaction_style_ds_vs_pack_distinction_preserved():
    """
    The original theme_packs.py had TWO InteractionStyle classes and
    overlapping INTERACT_MINIMAL / INTERACT_PLAYFUL names. DESIGN_MINIMAL
    and DESIGN_PLAYFUL captured the narrower (design-system) values,
    while PACK_* captured the wider (pack) values. The refactor preserves
    that via `_INTERACT_MINIMAL_DS` / `_INTERACT_PLAYFUL_DS`.
    """
    from djust.theming.theme_packs import get_all_design_systems, get_all_theme_packs

    design_systems = get_all_design_systems()
    theme_packs = get_all_theme_packs()

    # DS minimal: link_hover="underline", card_hover="none"
    ds_minimal = design_systems["minimalist"]
    assert ds_minimal.interaction.link_hover == "underline"
    assert ds_minimal.interaction.card_hover == "none"

    # Pack playful: button_click="ripple", focus_ring_offset="3px"
    pack_playful = theme_packs["playful"]
    assert pack_playful.interaction_style.button_click == "ripple"
    assert pack_playful.interaction_style.focus_ring_offset == "3px"
