"""Text-colour contrast of the built-in presets stays fixed (#2885).

``test_theming_contrast_all_presets_2060.py`` already gates every pair and
fails on a stale exemption. This file adds the guarantees specific to the
#2885 remediation, which moved text colours (the ``*_foreground`` labels and
``link``) in lightness only, and left the rest to the owner:

1. No text pair that a small, same-side lightness move can fix is left failing
   or exempted. If a palette edit pushes a label below its minimum, the
   message names the command that proposes the fix.
2. The number of exempted text pairs may only go down. What remains are the
   polarity flips and large moves that visibly restyle a button or badge, which
   ``scripts/fix_theme_text_contrast.py --proposals`` lists and which wait for
   the owner's review.
3. The solver and the source rewriter agree with the measured contrast.
"""

import importlib.util
import os
import sys

import pytest

from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS, CONTRAST_PAIRS
from djust.theming.accessibility import AccessibilityValidator
from djust.theming.presets import THEME_PRESETS

_SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "scripts", "fix_theme_text_contrast.py"
)
_spec = importlib.util.spec_from_file_location("fix_theme_text_contrast_2885", _SCRIPT)
assert _spec is not None and _spec.loader is not None
fix = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fix  # @dataclass resolves its module through sys.modules
_spec.loader.exec_module(fix)

#: Exempted text pairs (``*_foreground`` labels and ``link``) at the #2885
#: remediation. Lower it when a pair is fixed; raising it hides a regression.
PENDING_TEXT_PAIR_EXEMPTIONS = 401

_validator = AccessibilityValidator()


def _text_pair_rows():
    return [
        (name, mode, fg, bg, minimum)
        for name in sorted(THEME_PRESETS)
        for mode in fix.MODES
        for fg, bg, minimum, _label in CONTRAST_PAIRS
        if fg in fix.LABEL_TOKENS
    ]


class TestTextContrastStaysFixed:
    def test_no_nudgeable_text_pair_is_left_failing(self):
        pending = [m for m in fix.collect_moves() if m.is_nudge(fix.DEFAULT_MAX_DELTA)]
        assert not pending, (
            "text pairs a small same-side lightness move fixes are failing; run "
            "`PYTHONPATH=python python scripts/fix_theme_text_contrast.py --apply`: "
            + ", ".join(
                f"{m.preset}/{m.mode} {m.token} L{m.before.lightness}->{m.after_lightness}"
                for m in pending
            )
        )

    def test_exempted_text_pairs_only_go_down(self):
        exempt = [key for key in A11Y_EXEMPTIONS if key[2] in fix.LABEL_TOKENS]
        assert len(exempt) <= PENDING_TEXT_PAIR_EXEMPTIONS, (
            f"{len(exempt)} exempted text pairs, up from {PENDING_TEXT_PAIR_EXEMPTIONS}: fix the "
            f"text colour instead of adding an exemption (#2885)"
        )

    def test_every_failing_text_pair_is_a_documented_pending_move(self):
        """A failing text pair must be a flip or large move the script proposes AND
        carry an exemption; a pair that fails silently cannot appear."""
        proposed = {(m.preset, m.mode, m.token) for m in fix.collect_moves()}
        for name, mode, fg, bg, minimum in _text_pair_rows():
            tokens = getattr(THEME_PRESETS[name], mode)
            ratio = _validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))
            if ratio < minimum:
                assert (name, mode, fg) in proposed
                assert (name, mode, fg, bg) in A11Y_EXEMPTIONS, (
                    f"{name}/{mode}: {fg} on {bg} = {ratio:.2f} < {minimum} with no exemption"
                )


class TestSolver:
    def test_solved_lightness_passes_every_surface(self):
        for move in fix.collect_moves():
            tokens = getattr(THEME_PRESETS[move.preset], move.mode)
            for bg, minimum in fix.pairs_for(move.token):
                ratio = _validator.calculate_contrast_ratio(move.after, getattr(tokens, bg))
                assert ratio >= minimum, (
                    f"{move.preset}/{move.mode} {move.token} on {bg}: {ratio:.2f}"
                )

    def test_a_move_keeps_hue_and_saturation(self):
        for move in fix.collect_moves():
            assert (move.after.h, move.after.s) == (move.before.h, move.before.s)

    def test_a_passing_colour_is_never_moved(self):
        tokens = THEME_PRESETS["default"].light
        surfaces = [(tokens.background, 4.5)]
        assert fix.solve_lightness(tokens.foreground, surfaces) is None

    def test_solves_to_the_nearest_lightness(self):
        tokens = THEME_PRESETS["default"].light
        grey = fix.ColorScale(0, 0, 60)
        light = fix.solve_lightness(grey, [(tokens.background, 4.5)])
        assert light is not None and light < 60
        assert fix.margin(fix.ColorScale(0, 0, light), [(tokens.background, 4.5)]) >= 0
        assert fix.margin(fix.ColorScale(0, 0, light + 1), [(tokens.background, 4.5)]) < 0

    def test_polarity_flip_is_not_a_nudge(self):
        before = fix.ColorScale(0, 0, 100)
        flip = fix.Move("x", "light", "primary_foreground", before, 10, True)
        near = fix.Move("x", "light", "primary_foreground", before, 95, False)
        far = fix.Move("x", "light", "primary_foreground", before, 70, False)
        assert not flip.is_nudge(15)
        assert near.is_nudge(15)
        assert not far.is_nudge(15)


class TestCheckModeIsWired:
    @pytest.mark.parametrize("flag", ["--check"])
    def test_check_exits_zero_when_nothing_is_pending(self, flag, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fix_theme_text_contrast.py", flag])
        assert fix.main() == 0
