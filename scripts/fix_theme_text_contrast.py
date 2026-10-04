#!/usr/bin/env python
"""Propose, apply and check text-colour fixes for the built-in theme presets (#2885).

``scripts/report_theme_contrast.py`` lists every (preset, mode, pair) of the
canonical matrix (``a11y_exemptions.CONTRAST_PAIRS``) that misses its WCAG
minimum. This script acts on the ones a TEXT-COLOUR move can fix, before any
surface (fill, background, border) is touched:

* ``label`` tokens: the ``*_foreground`` labels, plus ``link``. Each is solved
  ONLY in lightness (hue and saturation are kept), to the nearest integer
  lightness that clears its minimum on every surface it is checked against
  (``muted_foreground`` on both ``muted`` and ``background``; ``link`` on both
  ``background`` and ``card``). A pair that already passes is never moved.
* A solved move is a NUDGE when the label stays on the same side of its
  surface (no light-on-dark <-> dark-on-light polarity flip) and moves by at
  most ``--max-delta`` lightness points (default 15). Nudges are what
  ``--apply`` writes. Polarity flips and large moves visibly restyle a button
  or a badge, so they are listed as proposals (``--proposals``,
  ``--html``) and applied only with ``--include-substantial``.

The remaining failures are NOT text-colour fixes and are reported, never
applied: ``primary`` and the status colours read AS text on a page or a tint
(they are also fills, so fixing them needs a derived ``*_text`` token like
``destructive_text`` (#3320), or a new fill), and the ``input`` border is a
non-text UI edge.

Usage (from the repository root):
    PYTHONPATH=python python scripts/fix_theme_text_contrast.py            # summary
    PYTHONPATH=python python scripts/fix_theme_text_contrast.py --proposals
    PYTHONPATH=python python scripts/fix_theme_text_contrast.py --html OUT.html
    PYTHONPATH=python python scripts/fix_theme_text_contrast.py --apply
    PYTHONPATH=python python scripts/fix_theme_text_contrast.py --check
"""

from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from dataclasses import dataclass

import django
from django.conf import settings

if not settings.configured:
    settings.configure(DEFAULT_AUTO_FIELD="django.db.models.BigAutoField")
django.setup()

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "python"))

from djust.theming._types import ColorScale  # noqa: E402
from djust.theming.a11y_exemptions import CONTRAST_PAIRS  # noqa: E402
from djust.theming.accessibility import AccessibilityValidator  # noqa: E402
from djust.theming.presets import THEME_PRESETS  # noqa: E402

MODES = ("light", "dark")
THEMES_DIR = os.path.join(ROOT, "python", "djust", "theming", "themes")
EXEMPTIONS_PATH = os.path.join(ROOT, "python", "djust", "theming", "a11y_exemptions.py")

#: Default cap, in HSL lightness points, on a move ``--apply`` writes.
DEFAULT_MAX_DELTA = 15

_validator = AccessibilityValidator()

# Foreground tokens whose colour IS text, so a lightness move restyles text only.
LABEL_TOKENS = frozenset(
    {
        "foreground",
        "card_foreground",
        "popover_foreground",
        "code_foreground",
        "primary_foreground",
        "secondary_foreground",
        "muted_foreground",
        "accent_foreground",
        "destructive_foreground",
        "success_foreground",
        "warning_foreground",
        "info_foreground",
        "brand_foreground",
        "link",
    }
)


def _ratio(fg: ColorScale, bg: ColorScale) -> float:
    return _validator.calculate_contrast_ratio(fg, bg)


def pairs_for(token: str) -> list[tuple[str, float]]:
    """``[(surface token, minimum)]`` the matrix checks ``token`` against."""
    return [(bg, mn) for fg, bg, mn, _label in CONTRAST_PAIRS if fg == token]


def margin(colour: ColorScale, surfaces: list[tuple[ColorScale, float]]) -> float:
    """Worst ``ratio - minimum`` of ``colour`` over the surfaces (>= 0 passes)."""
    return min(_ratio(colour, surface) - mn for surface, mn in surfaces)


def solve_lightness(colour: ColorScale, surfaces: list[tuple[ColorScale, float]]) -> int | None:
    """Nearest integer lightness (hue and saturation kept) that passes every
    surface, ``None`` when ``colour`` already passes, ``-1`` when no lightness
    in 0..100 does. The tie between two equally near lightnesses goes to the
    one with more margin."""
    if margin(colour, surfaces) >= 0:
        return None
    for step in range(1, 101):
        found = []
        for sign in (1, -1):
            cand = colour.lightness + sign * step
            if 0 <= cand <= 100:
                m = margin(ColorScale(colour.h, colour.s, cand), surfaces)
                if m >= 0:
                    found.append((m, cand))
        if found:
            return max(found)[1]
    return -1


@dataclass(frozen=True)
class Move:
    preset: str
    mode: str
    token: str
    before: ColorScale
    after_lightness: int
    flip: bool

    @property
    def delta(self) -> int:
        return abs(self.after_lightness - self.before.lightness)

    @property
    def after(self) -> ColorScale:
        return ColorScale(self.before.h, self.before.s, self.after_lightness)

    def is_nudge(self, max_delta: int) -> bool:
        return not self.flip and self.delta <= max_delta


def collect_moves() -> list[Move]:
    moves: list[Move] = []
    for name in sorted(THEME_PRESETS):
        for mode in MODES:
            tokens = getattr(THEME_PRESETS[name], mode)
            for token in sorted(LABEL_TOKENS):
                surf_names = pairs_for(token)
                surfaces = [(getattr(tokens, bg), mn) for bg, mn in surf_names]
                colour = getattr(tokens, token)
                light = solve_lightness(colour, surfaces)
                if light is None:
                    continue
                if light == -1:
                    raise SystemExit(f"{name}/{mode}: no lightness fixes {token}")
                mean = sum(s.lightness for s, _ in surfaces) / len(surfaces)
                flip = (colour.lightness >= mean) != (light >= mean)
                moves.append(Move(name, mode, token, colour, light, flip))
    return moves


# ---------------------------------------------------------------- source edit

_LINE = (
    r"^(\s*{token}=ColorScale\()(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?),\s*(\d+(?:\.\d+)?)"
    r"(\),)([ \t]*#.*)?$"
)
_HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _block_span(text: str, mode: str) -> tuple[int, int]:
    start = re.search(rf"^{mode.upper()} = ThemeTokens\(\n", text, re.M)
    if not start:
        raise SystemExit(f"no {mode.upper()} = ThemeTokens( block")
    end = re.search(r"^\)\n", text[start.end() :], re.M)
    assert end is not None
    return start.end(), start.end() + end.start()


def apply_moves(moves: list[Move]) -> int:
    """Rewrite the literal ``token=ColorScale(h, s, l)`` lines of each theme file.
    All edits are computed first and written last, so a failure edits nothing. A
    trailing comment that quotes the old hex is refreshed to the new colour."""
    by_file: dict[str, list[Move]] = {}
    for m in moves:
        path = os.path.join(THEMES_DIR, f"{m.preset}.py")
        if not os.path.exists(path):
            raise SystemExit(f"no theme source for preset {m.preset!r}: {path}")
        by_file.setdefault(path, []).append(m)
    edited: dict[str, str] = {}
    for path, file_moves in by_file.items():
        with open(path) as fh:
            text = fh.read()
        for m in file_moves:
            lo, hi = _block_span(text, m.mode)
            block = text[lo:hi]
            pattern = re.compile(_LINE.format(token=m.token), re.M)
            hit = pattern.search(block)
            if not hit:
                raise SystemExit(f"{path}: no literal {m.token}=ColorScale(...) in {m.mode}")
            h, s, light = (float(g) for g in hit.groups()[1:4])
            if (h, s, light) != (m.before.h, m.before.s, m.before.lightness):
                raise SystemExit(f"{path}: {m.mode} {m.token} source disagrees with the preset")

            def rewrite(x: re.Match[str], m: Move = m) -> str:
                comment = x.group(6) or ""
                comment = _HEX.sub(m.after.to_hex().upper(), comment)
                return f"{x.group(1)}{x.group(2)}, {x.group(3)}, {m.after_lightness}{x.group(5)}{comment}"

            text = text[:lo] + pattern.sub(rewrite, block, count=1) + text[hi:]
        edited[path] = text
    for path, text in edited.items():
        with open(path, "w") as fh:
            fh.write(text)
    return len(edited)


def prune_exemptions() -> int:
    """Delete A11Y_EXEMPTIONS rows whose pair now passes (the stale-exemption gate
    fails on them). Measures the theme sources as loaded, so ``--apply`` runs it
    in a fresh interpreter (``--prune``) after editing them."""
    from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS

    stale = []
    for (preset, mode, fg, bg), _reason in A11Y_EXEMPTIONS.items():
        minimum = next(m for f, b, m, _ in CONTRAST_PAIRS if f == fg and b == bg)
        tokens = getattr(THEME_PRESETS[preset], mode)
        if _ratio(getattr(tokens, fg), getattr(tokens, bg)) >= minimum:
            stale.append((preset, mode, fg, bg))
    with open(EXEMPTIONS_PATH) as fh:
        text = fh.read()
    removed = 0
    for preset, mode, fg, bg in stale:
        block = re.compile(
            rf'^    \(\n        "{preset}",\n        "{mode}",\n        "{fg}",\n        "{bg}",\n    \): "[^"\n]*",\n',
            re.M,
        )
        text, n1 = block.subn("", text)
        row = re.compile(rf'^    \("{preset}", "{mode}", "{fg}", "{bg}"\): [\d.]+,\n', re.M)
        text, n2 = row.subn("", text)
        if n1 + n2 != 1:
            raise SystemExit(
                f"could not locate exactly one exemption row for {(preset, mode, fg, bg)}"
            )
        removed += 1
    with open(EXEMPTIONS_PATH, "w") as fh:
        fh.write(text)
    return removed


# --------------------------------------------------------------------- report


def unfixable_by_text() -> dict[str, list[tuple[str, str, str, str, float, float]]]:
    """Failures no label move can reach, grouped by why."""
    groups: dict[str, list] = {"as-text": [], "border": []}
    for name in sorted(THEME_PRESETS):
        for mode in MODES:
            tokens = getattr(THEME_PRESETS[name], mode)
            for fg, bg, mn, _label in CONTRAST_PAIRS:
                if fg in LABEL_TOKENS:
                    continue
                ratio = _ratio(getattr(tokens, fg), getattr(tokens, bg))
                if ratio < mn:
                    kind = "border" if fg == "input" else "as-text"
                    groups[kind].append((name, mode, fg, bg, ratio, mn))
    return groups


def _css(c: ColorScale) -> str:
    return f"hsl({c.h}deg {c.s}% {c.lightness}%)"


def _chip(fg: ColorScale, bg: ColorScale, label: str = "Aa label") -> str:
    return (
        f'<span class="chip" style="background:{_css(bg)};color:{_css(fg)}">{html.escape(label)}'
        f"<small>{_ratio(fg, bg):.2f}</small></span>"
    )


def write_html(path: str, moves: list[Move], max_delta: int) -> None:
    applied = [m for m in moves if m.is_nudge(max_delta)]
    substantial = [m for m in moves if not m.is_nudge(max_delta)]
    groups = unfixable_by_text()

    def rows(subset: list[Move]) -> str:
        out = []
        for m in subset:
            tokens = getattr(THEME_PRESETS[m.preset], m.mode)
            cells = []
            for bg, _mn in pairs_for(m.token):
                surface = getattr(tokens, bg)
                cells.append(
                    f'<span class="pair"><em>on {bg}</em>{_chip(m.before, surface)}'
                    f"{_chip(m.after, surface)}</span>"
                )
            kind = "polarity flip" if m.flip else f"lightness {m.delta} pts"
            out.append(
                f"<tr><td>{m.preset}</td><td>{m.mode}</td><td><code>{m.token}</code></td>"
                f"<td>{''.join(cells)}</td><td>{m.before.lightness} &rarr; {m.after_lightness}"
                f"<br><small>{kind}</small></td></tr>"
            )
        return "\n".join(out)

    def border_rows() -> str:
        out = []
        for name, mode, fg, bg, ratio, _mn in groups["border"]:
            tokens = getattr(THEME_PRESETS[name], mode)
            surface = getattr(tokens, bg)
            colour = getattr(tokens, fg)
            light = solve_lightness(colour, [(surface, 3.0)])
            after = ColorScale(colour.h, colour.s, light)
            out.append(
                f"<tr><td>{name}</td><td>{mode}</td>"
                f'<td><span class="box" style="background:{_css(surface)};border:2px solid {_css(colour)}">'
                f"input <small>{ratio:.2f}</small></span>"
                f'<span class="box" style="background:{_css(surface)};border:2px solid {_css(after)}">'
                f"input <small>{_ratio(after, surface):.2f}</small></span></td>"
                f"<td>{colour.lightness} &rarr; {light}</td></tr>"
            )
        return "\n".join(out)

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Theme text contrast #2885</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ font: 14px/1.4 system-ui, sans-serif; margin: 2rem auto; max-width: 1100px; padding: 0 1rem; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
td, th {{ border-bottom: 1px solid #ddd; padding: .35rem .5rem; text-align: left; vertical-align: top; }}
.chip {{ display: inline-block; padding: .3rem .6rem; margin: 0 .35rem .2rem 0; border-radius: 6px;
  font-weight: 600; border: 1px solid rgba(128,128,128,.35); }}
.chip small {{ font-weight: 400; margin-left: .5rem; opacity: .85; }}
.box {{ display: inline-block; padding: .4rem .8rem; margin-right: .6rem; border-radius: 6px; color: #666; }}
.pair {{ display: block; }} .pair em {{ font-size: 11px; color: #777; margin-right: .4rem; }}
h2 {{ margin-top: 2.5rem; }}
</style></head><body>
<h1>Theme text contrast (#2885): before / after</h1>
<p>Each pair of chips is the token as it ships (left) and as solved (right); the number is the WCAG ratio.
Only the label (text) colour moves, in lightness, hue and saturation kept.</p>
<h2>1. Applied: text nudges ({len(applied)}), no polarity flip, at most {max_delta} lightness points</h2>
<table><tr><th>preset</th><th>mode</th><th>token</th><th>before / after</th><th>L</th></tr>
{rows(applied)}</table>
<h2>2. SUBSTANTIAL, not applied: polarity flips or larger moves ({len(substantial)})</h2>
<table><tr><th>preset</th><th>mode</th><th>token</th><th>before / after</th><th>L</th></tr>
{rows(substantial)}</table>
<h2>3. SUBSTANTIAL, not applied: input border, 3:1 non-text ({len(groups["border"])})</h2>
<table><tr><th>preset</th><th>mode</th><th>before / after</th><th>L</th></tr>
{border_rows()}</table>
</body></html>"""
    with open(path, "w") as fh:
        fh.write(page)


def summary(moves: list[Move], max_delta: int) -> None:
    nudges = [m for m in moves if m.is_nudge(max_delta)]
    flips = [m for m in moves if m.flip]
    big = [m for m in moves if not m.flip and m.delta > max_delta]
    groups = unfixable_by_text()
    print(f"label/link pairs failing, fixable by a lightness move: {len(moves)} token moves")
    print(f"  nudges (no flip, <= {max_delta} pts): {len(nudges)}")
    print(f"  SUBSTANTIAL polarity flips: {len(flips)}")
    print(f"  SUBSTANTIAL same-side moves > {max_delta} pts: {len(big)}")
    print(f"not a label move: as-text (primary, status): {len(groups['as-text'])} pairs")
    print(f"not a label move: input border (3:1): {len(groups['border'])} pairs")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--max-delta", type=int, default=DEFAULT_MAX_DELTA)
    parser.add_argument("--proposals", action="store_true", help="list every solved move")
    parser.add_argument("--html", metavar="OUT", help="write a before/after swatch page")
    parser.add_argument("--apply", action="store_true", help="write the nudges into themes/*.py")
    parser.add_argument(
        "--include-substantial",
        action="store_true",
        help="with --apply: ALSO write polarity flips and large moves (owner-approved only)",
    )
    parser.add_argument("--prune", action="store_true", help="drop stale A11Y_EXEMPTIONS rows")
    parser.add_argument("--check", action="store_true", help="exit 1 if a nudge is still pending")
    args = parser.parse_args()

    if args.prune:
        print(f"pruned {prune_exemptions()} now-stale A11Y_EXEMPTIONS rows")
        return 0
    moves = collect_moves()
    if args.html:
        write_html(args.html, moves, args.max_delta)
        print(f"wrote {args.html}")
    if args.proposals:
        for m in moves:
            kind = "NUDGE" if m.is_nudge(args.max_delta) else ("FLIP" if m.flip else "LARGE")
            print(
                f"{kind:5} {m.preset}/{m.mode} {m.token}: L {m.before.lightness} -> "
                f"{m.after_lightness} ({m.delta} pts)"
            )
    if args.check:
        pending = [m for m in moves if m.is_nudge(args.max_delta)]
        for m in pending:
            print(
                f"pending nudge: {m.preset}/{m.mode} {m.token} L {m.before.lightness} -> {m.after_lightness}"
            )
        return 1 if pending else 0
    if args.apply:
        chosen = (
            moves if args.include_substantial else [m for m in moves if m.is_nudge(args.max_delta)]
        )
        files = apply_moves(chosen)
        print(f"applied {len(chosen)} token moves across {files} theme files")
        subprocess.run([sys.executable, os.path.abspath(__file__), "--prune"], check=True)
        return 0
    if not (args.html or args.proposals):
        summary(moves, args.max_delta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
