"""Text-colour contrast of the built-in presets stays fixed (#2885).

``test_theming_contrast_all_presets_2060.py`` already gates every pair and
fails on a stale exemption. This file adds the guarantees specific to the
#2885 remediation, which moved text TOKENS (the ``*_foreground`` labels,
``link`` and ``link_hover``) in lightness only, and left the rest to the owner:

1. No text pair that a small, same-side lightness move can fix is left failing
   or exempted. If a palette edit pushes a label below its minimum, the
   message names the command that proposes the fix.
2. The number of exempted text pairs is pinned EXACTLY: a fix must lower the
   pin in the same change (so slack cannot be re-spent on a regression), and
   an added exemption fails. The polarity flips and large moves were approved
   and applied (2026-10-06); what remains are the documented-hex tokens and the
   fill-painted ``accent_foreground``, which ``scripts/fix_theme_text_contrast.py
   --proposals --include-substantial`` lists and which wait for the owner.
3. ``link_hover`` is not in ``CONTRAST_PAIRS`` (that would change W001 for
   user presets) but it must not regress: it keeps the side of the resting link
   it started on, and a hover that is moved is moved together with its link.
4. The solver and the source rewriter do what they say, on a temporary copy.
"""

import importlib.util
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

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
#: remediation. EXACT: lower it when a pair is fixed.
PENDING_TEXT_PAIR_EXEMPTIONS = 23

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

    def test_exempted_text_pairs_are_pinned_exactly(self):
        exempt = [key for key in A11Y_EXEMPTIONS if key[2] in fix.LABEL_TOKENS]
        assert len(exempt) == PENDING_TEXT_PAIR_EXEMPTIONS, (
            f"{len(exempt)} exempted text pairs, pinned at {PENDING_TEXT_PAIR_EXEMPTIONS}: fix "
            f"the text colour instead of adding an exemption, and lower the pin when a pair is "
            f"fixed (#2885)"
        )

    def test_every_failing_text_pair_is_a_documented_pending_move(self):
        """A failing text pair must be a move the script proposes AND carry an
        exemption; a pair that fails silently cannot appear."""
        proposed = {(m.preset, m.mode, m.token) for m in fix.collect_moves()}
        for name, mode, fg, bg, minimum in _text_pair_rows():
            tokens = getattr(THEME_PRESETS[name], mode)
            ratio = _validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))
            if ratio < minimum:
                assert (name, mode, fg) in proposed
                assert (name, mode, fg, bg) in A11Y_EXEMPTIONS, (
                    f"{name}/{mode}: {fg} on {bg} = {ratio:.2f} < {minimum} with no exemption"
                )


class TestApprovedMovesAreApplied:
    """John approved the label/link polarity flips and large moves on 2026-10-06 (#2885);
    only the tokens he did not approve are still open."""

    HELD = {
        "documented hex",
        "paired with a documented-hex move",
        "painted as a fill (.status-badge-accent)",
    }

    def test_nothing_approved_is_left_unapplied(self):
        left = [m for m in fix.collect_moves(approve_substantial=True) if not m.hold]
        assert not left, [(m.preset, m.mode, m.token) for m in left]

    def test_only_the_documented_holds_remain(self):
        reasons = {m.hold for m in fix.collect_moves(approve_substantial=True)}
        assert reasons == self.HELD

    def test_the_brand_hex_tokens_and_fill_painted_accent_foreground_stay_held(self):
        held = {(m.preset, m.mode, m.token) for m in fix.collect_moves(approve_substantial=True)}
        for key in (
            ("monokai", "light", "link"),
            ("monokai", "dark", "link"),
            ("stripe", "light", "link"),
            ("stripe", "light", "muted_foreground"),
            ("github", "light", "muted_foreground"),
            ("monokai", "light", "link_hover"),
            ("dracula", "light", "accent_foreground"),
        ):
            assert key in held, key

    def test_a_flip_is_written_only_when_approved(self):
        before = fix.ColorScale(0, 0, 100)
        flip = fix.Move("x", "light", "primary_foreground", before, 10, True)
        assert not flip.is_nudge(fix.DEFAULT_MAX_DELTA)  # not a nudge, but not held either
        assert not flip.hold


class TestDeliberateExemptions:
    """Two groups are exempt by the owner's decision (2026-10-06), not as debt: they carry
    their own reason, and everything else in the exemption list is the undecided rest."""

    def test_every_input_border_row_says_it_is_a_non_text_component(self):
        rows = {k: v for k, v in A11Y_EXEMPTIONS.items() if (k[2], k[3]) == ("input", "background")}
        assert len(rows) == 131
        for key, reason in rows.items():
            assert "non-text UI components" in reason and "revisit on request" in reason, key
            assert "grandfathered" not in reason, key

    def test_the_brand_hex_rows_name_the_hex_their_source_documents(self):
        from djust.theming.a11y_exemptions import BRAND_HEX_EXEMPTIONS

        assert len(BRAND_HEX_EXEMPTIONS) == 6
        for (preset, _mode, _fg, _bg), hex_ in BRAND_HEX_EXEMPTIONS.items():
            source = (Path(fix.THEMES_DIR) / f"{preset}.py").read_text().lower()
            assert hex_.lower() in source, f"{preset} no longer documents {hex_}"
            reason = A11Y_EXEMPTIONS[(preset, _mode, _fg, _bg)]
            assert hex_ in reason and "not debt" in reason

    def test_the_label_and_link_rows_left_are_exactly_the_brand_hex_and_accent_ones(self):
        from djust.theming.a11y_exemptions import BRAND_HEX_EXEMPTIONS

        rest = {k for k in A11Y_EXEMPTIONS if k[2] in fix.LABEL_TOKENS} - set(BRAND_HEX_EXEMPTIONS)
        assert rest and all(k[2] == "accent_foreground" for k in rest)
        assert len(rest) + len(BRAND_HEX_EXEMPTIONS) == PENDING_TEXT_PAIR_EXEMPTIONS


class TestLinkHover:
    def test_no_nudgeable_hover_is_left_failing(self):
        pending = [
            m
            for m in fix.collect_moves()
            if m.token == fix.HOVER_TOKEN and m.is_nudge(fix.DEFAULT_MAX_DELTA)
        ]
        assert not pending

    def test_a_link_that_moves_takes_its_hover_along_on_the_same_side(self):
        # amber light: link L45 fails, hover L35 started darker than the link.
        tokens = THEME_PRESETS["amber"].light
        tokens = replace(
            tokens,
            link=fix.ColorScale(tokens.link.h, tokens.link.s, 45),
            link_hover=fix.ColorScale(tokens.link_hover.h, tokens.link_hover.s, 35),
        )
        assert tokens.link_hover.lightness < tokens.link.lightness
        moves = {m.token: m for m in fix.moves_for_mode("amber", "light", tokens, lambda _t: "")}
        assert "link" in moves and "link_hover" in moves
        link_after, hover_after = moves["link"].after, moves["link_hover"].after
        assert hover_after.lightness < link_after.lightness  # still darkens the link
        for surface in (tokens.background, tokens.card):
            assert _validator.calculate_contrast_ratio(link_after, surface) >= 4.5
            assert _validator.calculate_contrast_ratio(hover_after, surface) >= 4.5

    def test_a_pair_is_held_together_when_the_hover_is_not_a_nudge(self):
        # sunrise light as it was before #2885: link L52 fails, its hover L48 needs 18 points.
        tokens = THEME_PRESETS["sunrise"].light
        tokens = replace(
            tokens,
            link=fix.ColorScale(tokens.link.h, tokens.link.s, 52),
            link_hover=fix.ColorScale(tokens.link_hover.h, tokens.link_hover.s, 48),
        )
        moves = {m.token: m for m in fix.moves_for_mode("sunrise", "light", tokens, lambda _t: "")}
        assert not moves["link_hover"].is_nudge(fix.DEFAULT_MAX_DELTA)
        assert moves["link"].delta <= fix.DEFAULT_MAX_DELTA and not moves["link"].flip
        assert moves["link"].hold  # a small link move, held because its hover is not a nudge
        assert not moves["link"].is_nudge(fix.DEFAULT_MAX_DELTA)

    def test_a_documented_hex_is_never_a_nudge(self):
        tokens = THEME_PRESETS["monokai"].light
        moves = {
            m.token: m
            for m in fix.moves_for_mode("monokai", "light", tokens, lambda _t: "  # Purple #ae81ff")
        }
        assert moves["link"].hold == "documented hex"
        assert not moves["link"].is_nudge(fix.DEFAULT_MAX_DELTA)


class TestSolver:
    def test_every_proposed_move_passes_its_surfaces(self):
        for move in fix.collect_moves():
            tokens = getattr(THEME_PRESETS[move.preset], move.mode)
            for bg, minimum in fix.pairs_for(move.token):
                ratio = _validator.calculate_contrast_ratio(move.after, getattr(tokens, bg))
                assert ratio >= minimum, f"{move.preset}/{move.mode} {move.token} on {bg}"

    def test_a_move_keeps_hue_and_saturation(self):
        for move in fix.collect_moves():
            assert (move.after.h, move.after.s) == (move.before.h, move.before.s)

    def test_a_passing_colour_is_never_moved(self):
        tokens = THEME_PRESETS["default"].light
        assert fix.solve_lightness(tokens.foreground, [(tokens.background, 4.5)]) is None

    def test_solves_to_the_nearest_lightness_with_margin(self):
        tokens = THEME_PRESETS["default"].light
        grey = fix.ColorScale(0, 0, 60)
        surfaces = [(tokens.background, 4.5)]
        light = fix.solve_lightness(grey, surfaces)
        assert light is not None and light < 60
        assert fix.margin(fix.ColorScale(0, 0, light), surfaces) >= fix.SOLVE_MARGIN
        assert fix.margin(fix.ColorScale(0, 0, light + 1), surfaces) < fix.SOLVE_MARGIN

    def test_polarity_flip_is_not_a_nudge(self):
        before = fix.ColorScale(0, 0, 100)
        flip = fix.Move("x", "light", "primary_foreground", before, 10, True)
        near = fix.Move("x", "light", "primary_foreground", before, 95, False)
        far = fix.Move("x", "light", "primary_foreground", before, 70, False)
        assert not flip.is_nudge(15)
        assert near.is_nudge(15)
        assert not far.is_nudge(15)


class TestApplyAndPruneOnACopy:
    @pytest.fixture
    def sandbox(self, monkeypatch, tmp_path):
        themes = tmp_path / "themes"
        shutil.copytree(fix.THEMES_DIR, themes, ignore=shutil.ignore_patterns("__pycache__"))
        exemptions = tmp_path / "a11y_exemptions.py"
        shutil.copy(fix.EXEMPTIONS_PATH, exemptions)
        monkeypatch.setattr(fix, "THEMES_DIR", str(themes))
        monkeypatch.setattr(fix, "EXEMPTIONS_PATH", str(exemptions))
        return themes, exemptions

    def test_apply_rewrites_the_literal_line_and_leaves_no_temp_file(self, sandbox):
        themes, _ = sandbox
        dracula = themes / "dracula.py"
        before = dracula.read_text()
        tokens = THEME_PRESETS["dracula"].light
        move = fix.Move("dracula", "light", "muted_foreground", tokens.muted_foreground, 30, False)
        assert fix.apply_moves([move]) == 1
        after = dracula.read_text()
        assert "muted_foreground=ColorScale(0, 0, 40)," in before
        assert "muted_foreground=ColorScale(0, 0, 30)," in after
        assert before.count("\n") == after.count("\n")
        assert not [p for p in themes.iterdir() if ".tmp" in p.name]

    def test_apply_refreshes_a_hex_quoted_in_the_comment(self, sandbox):
        themes, _ = sandbox
        stripe = themes / "stripe.py"
        tokens = THEME_PRESETS["stripe"].light
        move = fix.Move("stripe", "light", "link", tokens.link, 67, False, "documented hex")
        fix.apply_moves([move])
        line = next(ln for ln in stripe.read_text().splitlines() if "Blurple links" in ln)
        assert "ColorScale(243, 100, 67)" in line
        assert "#5F57FF" in line and "#635BFF" not in line

    def test_apply_refuses_a_source_that_disagrees_and_edits_nothing(self, sandbox):
        themes, _ = sandbox
        dracula = themes / "dracula.py"
        before = dracula.read_text()
        move = fix.Move("dracula", "light", "muted_foreground", fix.ColorScale(1, 2, 3), 30, False)
        with pytest.raises(SystemExit):
            fix.apply_moves([move])
        assert dracula.read_text() == before

    def test_prune_drops_exactly_the_stale_rows(self, sandbox):
        _, exemptions = sandbox
        text = exemptions.read_text()
        marker = "A11Y_EXEMPTIONS: dict[tuple[str, str, str, str], str] = {\n"
        stale_row = (
            '    (\n        "default",\n        "light",\n        "foreground",\n'
            '        "background",\n    ): "stale test row",\n'
        )
        head, tail = text.split(marker, 1)
        exemptions.write_text(head + marker + stale_row + tail)
        assert fix.prune_exemptions() == 1
        assert exemptions.read_text() == text
        assert fix.prune_exemptions() == 0


class TestCheckModeIsWired:
    def test_check_exits_zero_when_nothing_is_pending(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["fix_theme_text_contrast.py", "--check"])
        assert fix.main() == 0
