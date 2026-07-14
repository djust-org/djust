#!/usr/bin/env python
"""Report WCAG AA contrast failures across ALL registered theme presets.

Runs the same 6-pair x 2-mode matrix that
``python/djust/tests/test_theming_new_themes_v11.py::TestContrast`` gates
for the 5 newest themes, but over every preset in
``djust.theming.presets.THEME_PRESETS``. Prints a per-theme summary of
failures with computed ratios, sorted worst-first, plus an overall count.

Used at Stage 4 of #2060 to seed the initial ``A11Y_EXEMPTIONS`` entries in
``python/djust/theming/a11y_exemptions.py`` — every currently-failing
(theme, mode, pair) becomes a documented, reason-carrying exemption rather
than a silent gap. Kept in the repo as a standing report tool: re-run it
whenever a new preset is added or a legacy palette is revisited.

Usage:
    PYTHONPATH="$(pwd)/python" python scripts/report_theme_contrast.py
    PYTHONPATH="$(pwd)/python" python scripts/report_theme_contrast.py --python-dict
"""

from __future__ import annotations

import argparse
import os
import sys

import django
from django.conf import settings

if not settings.configured:
    settings.configure(DEFAULT_AUTO_FIELD="django.db.models.BigAutoField")
django.setup()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from djust.theming.accessibility import AccessibilityValidator  # noqa: E402
from djust.theming.presets import THEME_PRESETS  # noqa: E402

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


def collect_failures() -> list[tuple[str, str, str, str, float, float]]:
    """Return (theme, mode, fg_token, bg_token, ratio, minimum) for every
    pair that fails its WCAG AA minimum."""
    validator = AccessibilityValidator()
    failures = []
    for theme_name in sorted(THEME_PRESETS):
        preset = THEME_PRESETS[theme_name]
        for mode in MODES:
            tokens = getattr(preset, mode)
            for fg_name, bg_name, minimum in PAIRS:
                fg = getattr(tokens, fg_name)
                bg = getattr(tokens, bg_name)
                ratio = validator.calculate_contrast_ratio(fg, bg)
                if ratio < minimum:
                    failures.append((theme_name, mode, fg_name, bg_name, ratio, minimum))
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python-dict",
        action="store_true",
        help="Emit the failures as A11Y_EXEMPTIONS-shaped Python dict entries instead of a report.",
    )
    args = parser.parse_args()

    failures = collect_failures()
    total_checks = len(THEME_PRESETS) * len(MODES) * len(PAIRS)

    if args.python_dict:
        for theme_name, mode, fg_name, bg_name, ratio, _minimum in failures:
            print(
                f'    ("{theme_name}", "{mode}", "{fg_name}", "{bg_name}"): '
                f'"grandfathered at gate introduction (2026-07, #2060); ratio {ratio:.2f}",'
            )
        return

    print(f"Presets checked: {len(THEME_PRESETS)}")
    print(f"Total (theme, mode, pair) checks: {total_checks}")
    print(f"Failures: {len(failures)}")
    print()

    if not failures:
        print("No failures. Every registered preset meets AA on all 6 pairs in both modes.")
        return

    themes_failing = sorted({f[0] for f in failures})
    print(f"Themes with >=1 failure: {len(themes_failing)} / {len(THEME_PRESETS)}")
    print(", ".join(themes_failing))
    print()

    print("Worst offenders (lowest ratio first):")
    for theme_name, mode, fg_name, bg_name, ratio, minimum in sorted(failures, key=lambda f: f[4])[
        :15
    ]:
        print(
            f"  {theme_name}/{mode}: {fg_name} on {bg_name} = {ratio:.2f} "
            f"(need {minimum}, short by {minimum - ratio:.2f})"
        )
    print()

    catastrophic = [f for f in failures if f[4] < 3.0]
    if catastrophic:
        print(
            f"Catastrophic (<3.0) — {len(catastrophic)} pairs, candidates for a palette-fix follow-up:"
        )
        for theme_name, mode, fg_name, bg_name, ratio, minimum in sorted(
            catastrophic, key=lambda f: f[4]
        ):
            print(f"  {theme_name}/{mode}: {fg_name} on {bg_name} = {ratio:.2f}")
        print()

    print("Per-theme detail:")
    by_theme: dict[str, list[tuple]] = {}
    for f in failures:
        by_theme.setdefault(f[0], []).append(f)
    for theme_name in themes_failing:
        print(f"  {theme_name}:")
        for _theme_name, mode, fg_name, bg_name, ratio, minimum in sorted(
            by_theme[theme_name], key=lambda f: (f[1], f[4])
        ):
            print(f"    {mode}: {fg_name} on {bg_name} = {ratio:.2f} < {minimum}")


if __name__ == "__main__":
    main()
