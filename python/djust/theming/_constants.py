"""
Dependency-free shared style instances used across theme files.

Lives alongside ``_types.py`` as the second half of the cyclic-import fix:
instances previously defined in ``theme_packs.py`` and imported via
``themes/_base.py`` are now defined here and imported by both
``theme_packs.py`` (for back-compat re-export) and ``themes/_base.py``
(directly).

IMPORTANT: this module may only import from ``_types`` and stdlib. Keep
it dependency-free so ``themes/*`` can import without walking back
through ``theme_packs.py`` / ``presets.py``.
"""

from ._types import (
    AnimationStyle,
    IconStyle,
    IllustrationStyle,
    InteractionStyle,
    PatternStyle,
)

# =============================================================================
# Icon Style Presets (design-system-level, from theme_packs.py L730-L836)
# =============================================================================

ICON_MATERIAL = IconStyle(
    name="material",
    style="filled",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ICON_IOS = IconStyle(
    name="ios",
    style="outlined",
    weight="thin",
    size_scale=1.0,
    stroke_width="1.5",
    corner_rounding="4px",
)

ICON_FLUENT = IconStyle(
    name="fluent",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ICON_PLAYFUL = IconStyle(
    name="playful",
    style="rounded",
    weight="regular",
    size_scale=1.1,
    stroke_width="2",
    corner_rounding="8px",
)

ICON_CORPORATE = IconStyle(
    name="corporate",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)

ICON_DENSE = IconStyle(
    name="dense",
    style="outlined",
    weight="thin",
    size_scale=0.85,
    stroke_width="1.5",
    corner_rounding="0px",
)

ICON_MINIMAL = IconStyle(
    name="minimal",
    style="outlined",
    weight="thin",
    size_scale=0.9,  # Smaller icons
    stroke_width="1.5",
    corner_rounding="2px",
)

ICON_BRUTALIST = IconStyle(
    name="brutalist",
    style="filled",
    weight="bold",
    size_scale=1.2,  # Larger, bold icons
    stroke_width="3",
    corner_rounding="0px",
)

ICON_ELEGANT = IconStyle(
    name="elegant",
    style="outlined",
    weight="thin",
    size_scale=1.0,
    stroke_width="1",  # Very thin strokes
    corner_rounding="4px",
)

ICON_RETRO = IconStyle(
    name="retro",
    style="filled",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",  # Sharp pixels
)

ICON_ORGANIC = IconStyle(
    name="organic",
    style="rounded",
    weight="regular",
    size_scale=1.1,
    stroke_width="2",
    corner_rounding="8px",  # Very rounded
)

ICON_DJUST = IconStyle(
    name="djust",
    style="outlined",
    weight="regular",
    size_scale=1.0,
    stroke_width="2",
    corner_rounding="0px",
)


# =============================================================================
# Animation Style Presets (design-system-level, from theme_packs.py L843-L1033)
# =============================================================================

ANIM_MATERIAL = AnimationStyle(
    name="material",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

ANIM_IOS = AnimationStyle(
    name="ios",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="scale",
    hover_scale=1.05,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="snappy",
    duration_fast="0.15s",
    duration_normal="0.25s",
    duration_slow="0.35s",
    easing="cubic-bezier(0.42, 0, 0.58, 1)",
)

ANIM_FLUENT = AnimationStyle(
    name="fluent",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="progress",
    transition_style="smooth",
    duration_fast="0.167s",
    duration_normal="0.25s",
    duration_slow="0.367s",
    easing="cubic-bezier(0.1, 0.9, 0.2, 1)",
)

ANIM_PLAYFUL = AnimationStyle(
    name="playful",
    entrance_effect="bounce",
    exit_effect="scale",
    hover_effect="scale",
    hover_scale=1.05,
    hover_translate_y="0px",
    click_effect="bounce",
    loading_style="pulse",
    transition_style="bouncy",
    duration_fast="0.2s",
    duration_normal="0.3s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
)

ANIM_CORPORATE = AnimationStyle(
    name="corporate",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.01,
    hover_translate_y="-1px",
    click_effect="none",
    loading_style="progress",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

ANIM_DENSE = AnimationStyle(
    name="dense",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="progress",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.1s",
    duration_slow="0.15s",
    easing="linear",
)

ANIM_MINIMAL = AnimationStyle(
    name="minimal",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="none",  # No hover effects
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="progress",
    transition_style="smooth",
    duration_fast="0.2s",
    duration_normal="0.3s",
    duration_slow="0.4s",
    easing="ease-out",
)

ANIM_BRUTALIST = AnimationStyle(
    name="brutalist",
    entrance_effect="none",  # Instant appearance
    exit_effect="none",
    hover_effect="scale",
    hover_scale=1.05,  # Bold scale
    hover_translate_y="0px",
    click_effect="pulse",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.05s",  # Very fast
    duration_normal="0.1s",
    duration_slow="0.15s",
    easing="linear",  # No easing curves
)

ANIM_ELEGANT = AnimationStyle(
    name="elegant",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,  # Subtle
    hover_translate_y="-4px",  # Gentle lift
    click_effect="none",  # No aggressive feedback
    loading_style="skeleton",
    transition_style="smooth",
    duration_fast="0.4s",  # Slower, more graceful
    duration_normal="0.6s",
    duration_slow="0.8s",
    easing="cubic-bezier(0.25, 0.46, 0.45, 0.94)",  # Elegant curve
)

ANIM_RETRO = AnimationStyle(
    name="retro",
    entrance_effect="slide",  # Old-school slide
    exit_effect="slide",
    hover_effect="glow",
    hover_scale=1.0,  # No scaling
    hover_translate_y="0px",
    click_effect="bounce",  # Arcade-style
    loading_style="progress",
    transition_style="snappy",
    duration_fast="0.1s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.68, -0.55, 0.265, 1.55)",  # Bouncy
)

ANIM_ORGANIC = AnimationStyle(
    name="organic",
    entrance_effect="scale",  # Organic growth
    exit_effect="scale",
    hover_effect="glow",
    hover_scale=1.03,
    hover_translate_y="-2px",
    click_effect="ripple",  # Natural ripple
    loading_style="pulse",
    transition_style="bouncy",
    duration_fast="0.3s",
    duration_normal="0.5s",
    duration_slow="0.8s",
    easing="cubic-bezier(0.34, 1.56, 0.64, 1)",  # Organic bounce
)

ANIM_DJUST = AnimationStyle(
    name="djust",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.2s",
    duration_slow="0.3s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)


# =============================================================================
# Interaction Style Presets (design-system-level, from theme_packs.py L1040-L1146)
# =============================================================================

INTERACT_MATERIAL = InteractionStyle(
    name="material",
    button_hover="lift",
    link_hover="underline",
    card_hover="lift",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_IOS = InteractionStyle(
    name="ios",
    button_hover="scale",
    link_hover="color",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_FLUENT = InteractionStyle(
    name="fluent",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_CORPORATE = InteractionStyle(
    name="corporate",
    button_hover="darken",
    link_hover="underline",
    card_hover="border",
    focus_style="ring",
    focus_ring_width="2px",
)

INTERACT_DENSE = InteractionStyle(
    name="dense",
    button_hover="darken",
    link_hover="underline",
    card_hover="none",
    focus_style="outline",
    focus_ring_width="1px",
)

INTERACT_BRUTALIST = InteractionStyle(
    name="brutalist",
    button_hover="glow",  # Bold glow effect
    link_hover="background",
    card_hover="shadow",  # Hard shadow change
    focus_style="outline",
    focus_ring_width="4px",  # Thick focus ring
)

INTERACT_ELEGANT = InteractionStyle(
    name="elegant",
    button_hover="lift",
    link_hover="color",  # Subtle color shift
    card_hover="shadow",  # Soft shadow lift
    focus_style="glow",
    focus_ring_width="2px",
)

INTERACT_RETRO = InteractionStyle(
    name="retro",
    button_hover="scale",  # Arcade-style scale
    link_hover="background",
    card_hover="border",  # Pixel border change
    focus_style="outline",
    focus_ring_width="2px",
)

INTERACT_ORGANIC = InteractionStyle(
    name="organic",
    button_hover="glow",  # Soft organic glow
    link_hover="color",
    card_hover="lift",  # Natural lift
    focus_style="glow",
    focus_ring_width="3px",
)

INTERACT_DJUST = InteractionStyle(
    name="djust",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    focus_style="ring",
    focus_ring_width="2px",
)


# Design-system-level MINIMAL/PLAYFUL — separate names so the pack-level
# INTERACT_MINIMAL / INTERACT_PLAYFUL below can keep those canonical names
# without losing the (different) design-system bindings. In the original
# theme_packs.py these were both named INTERACT_MINIMAL / INTERACT_PLAYFUL
# and the DESIGN_* construction captured whichever value existed at the
# point of reference (i.e. the narrower design-system versions).
_INTERACT_MINIMAL_DS = InteractionStyle(
    name="minimal",
    button_hover="darken",  # Subtle color change
    link_hover="underline",
    card_hover="none",  # No card hover
    focus_style="underline",
    focus_ring_width="1px",
)

_INTERACT_PLAYFUL_DS = InteractionStyle(
    name="playful",
    button_hover="glow",
    link_hover="background",
    card_hover="lift",
    focus_style="glow",
    focus_ring_width="3px",
)


# =============================================================================
# Pack-level Icon Style Presets (from theme_packs.py L1451-L1489)
# =============================================================================

ICON_OUTLINED = IconStyle(
    name="outlined",
    style="outlined",
    weight="regular",
    stroke_width="2",
    corner_rounding="0",
)

ICON_FILLED = IconStyle(
    name="filled",
    style="filled",
    weight="regular",
    stroke_width="0",
    corner_rounding="0",
)

ICON_ROUNDED = IconStyle(
    name="rounded",
    style="rounded",
    weight="regular",
    stroke_width="2",
    corner_rounding="4px",
)

ICON_SHARP = IconStyle(
    name="sharp",
    style="sharp",
    weight="bold",
    stroke_width="2.5",
    corner_rounding="0",
)

ICON_THIN = IconStyle(
    name="thin",
    style="outlined",
    weight="thin",
    stroke_width="1",
    corner_rounding="0",
)


# =============================================================================
# Pack-level Animation Style Presets (from theme_packs.py L1496-L1574)
# =============================================================================

ANIM_SMOOTH = AnimationStyle(
    name="smooth",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="lift",
    hover_scale=1.02,
    hover_translate_y="-2px",
    click_effect="ripple",
    loading_style="spinner",
    transition_style="smooth",
    duration_fast="0.15s",
    duration_normal="0.3s",
    duration_slow="0.5s",
    easing="cubic-bezier(0.4, 0, 0.2, 1)",
)

ANIM_SNAPPY = AnimationStyle(
    name="snappy",
    entrance_effect="scale",
    exit_effect="scale",
    hover_effect="scale",
    hover_scale=1.05,
    hover_translate_y="0px",
    click_effect="pulse",
    loading_style="progress",
    transition_style="snappy",
    duration_fast="0.08s",
    duration_normal="0.12s",
    duration_slow="0.2s",
    easing="cubic-bezier(0.68, -0.55, 0.265, 1.55)",
)

ANIM_BOUNCY = AnimationStyle(
    name="bouncy",
    entrance_effect="bounce",
    exit_effect="scale",
    hover_effect="scale",
    hover_scale=1.1,
    hover_translate_y="0px",
    click_effect="bounce",
    loading_style="pulse",
    transition_style="bouncy",
    duration_fast="0.2s",
    duration_normal="0.4s",
    duration_slow="0.6s",
    easing="cubic-bezier(0.34, 1.56, 0.64, 1)",
)

ANIM_INSTANT = AnimationStyle(
    name="instant",
    entrance_effect="none",
    exit_effect="none",
    hover_effect="none",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="spinner",
    transition_style="instant",
    duration_fast="0.05s",
    duration_normal="0.1s",
    duration_slow="0.15s",
    easing="linear",
)

ANIM_GENTLE = AnimationStyle(
    name="gentle",
    entrance_effect="fade",
    exit_effect="fade",
    hover_effect="glow",
    hover_scale=1.0,
    hover_translate_y="0px",
    click_effect="none",
    loading_style="skeleton",
    transition_style="smooth",
    duration_fast="0.3s",
    duration_normal="0.5s",
    duration_slow="0.8s",
    easing="cubic-bezier(0.25, 0.46, 0.45, 0.94)",
)


# =============================================================================
# Pattern Style Presets (from theme_packs.py L1581-L1639)
# =============================================================================

PATTERN_MINIMAL = PatternStyle(
    name="minimal",
    background_pattern="none",
    pattern_opacity=0.0,
    pattern_scale="1rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)

PATTERN_DOTS = PatternStyle(
    name="dots",
    background_pattern="dots",
    pattern_opacity=0.05,
    pattern_scale="1.5rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)

PATTERN_GRID = PatternStyle(
    name="grid",
    background_pattern="grid",
    pattern_opacity=0.03,
    pattern_scale="2rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)

PATTERN_NOISE = PatternStyle(
    name="noise",
    background_pattern="noise",
    pattern_opacity=0.02,
    pattern_scale="1rem",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.15,
)

PATTERN_GLASS = PatternStyle(
    name="glass",
    background_pattern="none",
    pattern_opacity=0.0,
    pattern_scale="1rem",
    surface_style="glass",
    backdrop_blur="12px",
    noise_intensity=0.0,
)

PATTERN_GRADIENT = PatternStyle(
    name="gradient",
    background_pattern="gradient",
    pattern_opacity=0.1,
    pattern_scale="100%",
    surface_style="flat",
    backdrop_blur="0px",
    noise_intensity=0.0,
)


# =============================================================================
# Pack-level Interaction Style Presets (from theme_packs.py L1646-L1692)
#
# INTERACT_PLAYFUL and INTERACT_MINIMAL are defined in BOTH the design-system
# block (above) and the pack block (here). Python's last-definition-wins
# semantics meant the pack versions shadowed the design-system ones at runtime
# even though the design-system lookups referenced the earlier bindings by
# value. We preserve that runtime behaviour here by keeping both names.
# =============================================================================

INTERACT_SUBTLE = InteractionStyle(
    name="subtle",
    button_hover="lift",
    link_hover="underline",
    card_hover="shadow",
    button_click="scale",
    focus_style="ring",
    focus_ring_width="2px",
    focus_ring_offset="2px",
    cursor_style="pointer",
)

INTERACT_BOLD = InteractionStyle(
    name="bold",
    button_hover="scale",
    link_hover="background",
    card_hover="lift",
    button_click="pulse",
    focus_style="outline",
    focus_ring_width="3px",
    focus_ring_offset="0px",
    cursor_style="pointer",
)

INTERACT_MINIMAL = InteractionStyle(
    name="minimal",
    button_hover="darken",
    link_hover="color",
    card_hover="border",
    button_click="none",
    focus_style="underline",
    focus_ring_width="1px",
    focus_ring_offset="0px",
    cursor_style="default",
)

INTERACT_PLAYFUL = InteractionStyle(
    name="playful",
    button_hover="glow",
    link_hover="background",
    card_hover="lift",
    button_click="ripple",
    focus_style="glow",
    focus_ring_width="3px",
    focus_ring_offset="3px",
    cursor_style="pointer",
)


# =============================================================================
# Illustration Style Presets (from theme_packs.py L1699-L1737)
# =============================================================================

ILLUST_FLAT = IllustrationStyle(
    name="flat",
    illustration_type="flat",
    image_border_radius="0.5rem",
    image_filter="none",
    preferred_aspect="16:9",
)

ILLUST_3D = IllustrationStyle(
    name="3d",
    illustration_type="3d",
    image_border_radius="1rem",
    image_filter="vibrant",
    preferred_aspect="1:1",
)

ILLUST_LINE = IllustrationStyle(
    name="line-art",
    illustration_type="line-art",
    image_border_radius="0.25rem",
    image_filter="none",
    preferred_aspect="4:3",
)

ILLUST_HAND_DRAWN = IllustrationStyle(
    name="hand-drawn",
    illustration_type="hand-drawn",
    image_border_radius="1.5rem",
    image_filter="none",
    preferred_aspect="16:9",
)

ILLUST_RETRO = IllustrationStyle(
    name="retro",
    illustration_type="flat",
    image_border_radius="0px",
    image_filter="none",
    preferred_aspect="4:3",
)


__all__ = [
    # Design-system-level icon presets
    "ICON_MATERIAL",
    "ICON_IOS",
    "ICON_FLUENT",
    "ICON_PLAYFUL",
    "ICON_CORPORATE",
    "ICON_DENSE",
    "ICON_MINIMAL",
    "ICON_BRUTALIST",
    "ICON_ELEGANT",
    "ICON_RETRO",
    "ICON_ORGANIC",
    "ICON_DJUST",
    # Design-system-level animation presets
    "ANIM_MATERIAL",
    "ANIM_IOS",
    "ANIM_FLUENT",
    "ANIM_PLAYFUL",
    "ANIM_CORPORATE",
    "ANIM_DENSE",
    "ANIM_MINIMAL",
    "ANIM_BRUTALIST",
    "ANIM_ELEGANT",
    "ANIM_RETRO",
    "ANIM_ORGANIC",
    "ANIM_DJUST",
    # Design-system-level interaction presets
    "INTERACT_MATERIAL",
    "INTERACT_IOS",
    "INTERACT_FLUENT",
    "INTERACT_CORPORATE",
    "INTERACT_DENSE",
    "INTERACT_BRUTALIST",
    "INTERACT_ELEGANT",
    "INTERACT_RETRO",
    "INTERACT_ORGANIC",
    "INTERACT_DJUST",
    # Design-system-level minimal/playful (internal — pack names shadow these)
    "_INTERACT_MINIMAL_DS",
    "_INTERACT_PLAYFUL_DS",
    # Pack-level icon presets
    "ICON_OUTLINED",
    "ICON_FILLED",
    "ICON_ROUNDED",
    "ICON_SHARP",
    "ICON_THIN",
    # Pack-level animation presets
    "ANIM_SMOOTH",
    "ANIM_SNAPPY",
    "ANIM_BOUNCY",
    "ANIM_INSTANT",
    "ANIM_GENTLE",
    # Pattern presets
    "PATTERN_MINIMAL",
    "PATTERN_DOTS",
    "PATTERN_GRID",
    "PATTERN_NOISE",
    "PATTERN_GLASS",
    "PATTERN_GRADIENT",
    # Pack-level interaction presets (INTERACT_MINIMAL/PLAYFUL shadow DS ones)
    "INTERACT_SUBTLE",
    "INTERACT_BOLD",
    "INTERACT_MINIMAL",
    "INTERACT_PLAYFUL",
    # Illustration presets
    "ILLUST_FLAT",
    "ILLUST_3D",
    "ILLUST_LINE",
    "ILLUST_HAND_DRAWN",
    "ILLUST_RETRO",
]
