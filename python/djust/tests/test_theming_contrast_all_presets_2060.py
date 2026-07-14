"""WCAG AA contrast gate over ALL registered built-in theme presets (#2060).

``test_theming_new_themes_v11.py::TestContrast`` gates the 6-pair x 2-mode
matrix strictly for the 5 newest themes only (sakura, obsidian, dune,
mission_control, art_nouveau — no exemptions there, by design; that file
stays untouched). This module generalizes the same matrix to every preset
registered in ``djust.theming.presets.THEME_PRESETS`` (68 presets as of
2026-07), so any future preset is gated by construction the moment it's
registered.

Legacy palettes that already failed AA when this gate was introduced are
NOT silently exempted from coverage — they are asserted-failing-with-
tolerance against their documented entry in
``djust.theming.a11y_exemptions.A11Y_EXEMPTIONS`` (scope discipline, #1079:
this PR does not redesign shipped palettes). Two failure modes are both
covered:

1. A non-exempted pair fails AA -> the gate fails (this is the new
   protection #2060 adds).
2. An exemption entry's pair now PASSES AA (someone fixed the palette but
   forgot to remove the now-stale exemption) -> the gate fails with
   "stale exemption" (#1859: an anti-drift pin must be load-bearing, not
   decorative — this keeps ``A11Y_EXEMPTIONS`` from silently rotting into
   a list of exemptions nobody needs anymore).

Gate-off self-test (#1468): manually removing any one exemption entry for
a still-failing pair from ``A11Y_EXEMPTIONS`` and re-running
``TestAllPresetsContrast::test_non_exempt_pairs_meet_wcag_aa`` turns that
parametrized case red (see PR body for the manual verification transcript).
"""

import pytest

from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS
from djust.theming.accessibility import AccessibilityValidator
from djust.theming.presets import THEME_PRESETS

# Mirrors TestContrast.PAIRS in test_theming_new_themes_v11.py exactly.
PAIRS = [
    ("foreground", "background", 4.5),
    ("card_foreground", "card", 4.5),
    ("primary_foreground", "primary", 4.5),
    ("destructive_foreground", "destructive", 4.5),
    ("accent_foreground", "accent", 4.5),
    ("muted_foreground", "background", 4.5),
]

MODES = ["light", "dark"]

_validator = AccessibilityValidator()

ALL_PRESET_NAMES = sorted(THEME_PRESETS)

# Every (preset, mode) combination x every pair, as a flat parametrize list
# so a single failing case is individually addressable in CI output.
_ALL_CASES = [
    (preset_name, mode, fg_name, bg_name, minimum)
    for preset_name in ALL_PRESET_NAMES
    for mode in MODES
    for fg_name, bg_name, minimum in PAIRS
]


def _case_id(case):
    preset_name, mode, fg_name, bg_name, _minimum = case
    return f"{preset_name}-{mode}-{fg_name}_on_{bg_name}"


def _ratio_for(preset_name: str, mode: str, fg_name: str, bg_name: str) -> float:
    tokens = getattr(THEME_PRESETS[preset_name], mode)
    fg = getattr(tokens, fg_name)
    bg = getattr(tokens, bg_name)
    return _validator.calculate_contrast_ratio(fg, bg)


class TestAllPresetsRegistered:
    def test_report_script_and_test_cover_the_same_preset_set(self):
        """Sanity check that this file's preset set matches THEME_PRESETS
        directly (not a hardcoded snapshot that could silently drift when a
        new preset is registered)."""
        assert ALL_PRESET_NAMES == sorted(THEME_PRESETS.keys())
        assert len(ALL_PRESET_NAMES) >= 60  # guards against an import-time regression


class TestAllPresetsContrast:
    """WCAG AA on the load-bearing text pairs, every preset, both modes."""

    @pytest.mark.parametrize("case", _ALL_CASES, ids=_case_id)
    def test_non_exempt_pairs_meet_wcag_aa(self, case):
        preset_name, mode, fg_name, bg_name, minimum = case
        key = (preset_name, mode, fg_name, bg_name)
        ratio = _ratio_for(preset_name, mode, fg_name, bg_name)

        if key in A11Y_EXEMPTIONS:
            pytest.skip(f"exempted: {A11Y_EXEMPTIONS[key]}")

        assert ratio >= minimum, (
            f"{preset_name}/{mode}: {fg_name} on {bg_name} = {ratio:.2f} < {minimum} "
            f"and no entry in A11Y_EXEMPTIONS — either fix the palette or add a "
            f"documented exemption in python/djust/theming/a11y_exemptions.py"
        )


class TestExemptionsStillNeeded:
    """Anti-rot gate (#1859): every A11Y_EXEMPTIONS entry must still be
    NEEDED. A pair that now passes AA is a stale exemption — decorative,
    not documentation — and must be removed."""

    @pytest.mark.parametrize(
        "key",
        sorted(A11Y_EXEMPTIONS.keys()),
        ids=lambda k: f"{k[0]}-{k[1]}-{k[2]}_on_{k[3]}",
    )
    def test_exemption_is_still_failing(self, key):
        preset_name, mode, fg_name, bg_name = key

        assert preset_name in THEME_PRESETS, (
            f"A11Y_EXEMPTIONS has a stale entry for unregistered preset {preset_name!r} "
            f"— remove it from python/djust/theming/a11y_exemptions.py"
        )

        # The pair itself must exist in PAIRS — an exemption for a pair the
        # gate no longer checks is meaningless.
        minimum = next(
            (m for fg, bg, m in PAIRS if fg == fg_name and bg == bg_name),
            None,
        )
        assert minimum is not None, (
            f"A11Y_EXEMPTIONS entry {key} references a pair not in PAIRS "
            f"— remove it or update PAIRS to match"
        )

        ratio = _ratio_for(preset_name, mode, fg_name, bg_name)
        assert ratio < minimum, (
            f"stale exemption — remove it: {preset_name}/{mode} {fg_name} on {bg_name} "
            f"now passes WCAG AA (ratio {ratio:.2f} >= {minimum}). The entry in "
            f"A11Y_EXEMPTIONS is no longer needed; delete it from "
            f"python/djust/theming/a11y_exemptions.py."
        )

    def test_exemption_count_is_reasonable(self):
        """Loose upper bound so a bulk future addition to A11Y_EXEMPTIONS
        (rather than a real palette fix) doesn't slip through unnoticed."""
        assert 0 < len(A11Y_EXEMPTIONS) <= 250
