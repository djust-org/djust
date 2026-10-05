#!/usr/bin/env python
"""Propose, apply and check text-colour fixes for the built-in theme presets (#2885).

``scripts/report_theme_contrast.py`` lists every (preset, mode, pair) of the
canonical matrix (``a11y_exemptions.CONTRAST_PAIRS``) that misses its WCAG
minimum. This script acts on the ones a TEXT-COLOUR TOKEN move can fix, before
any fill, background or border TOKEN is touched:

* Text tokens: the ``*_foreground`` labels, ``link`` and ``link_hover``. Each is
  solved ONLY in lightness (hue and saturation are kept), to the nearest
  integer lightness that clears its minimum, with a small safety margin, on
  every surface it is checked against (``muted_foreground`` on ``muted`` and
  ``background``; ``link`` and ``link_hover`` on ``background`` and ``card``).
  A pair that already passes is never moved. ``link_hover`` is not in
  ``CONTRAST_PAIRS`` (adding it would change ``djust_theming.W001`` for
  user-authored presets, an owner decision); the script checks it itself.
* ``link`` and ``link_hover`` move together. ``link_hover`` keeps the side of
  ``link`` it started on (a hover that darkened the link still darkens it, and
  never equals it); if the pair cannot be moved as nudges, neither is.
* A solved move is a NUDGE when the colour stays on the same side of its
  surface (no light-on-dark <-> dark-on-light polarity flip), moves by at most
  ``--max-delta`` lightness points (default 15), is not held back (below), and
  its ``link``/``link_hover`` partner is a nudge too. Nudges are what
  ``--apply`` writes. Everything else is listed (``--proposals``, ``--html``)
  and written only with ``--include-substantial``.
* Held back: a token whose source line documents an exact hex colour (for
  example ``# Purple #ae81ff``) is a stated brand value, so any move of it is
  an identity decision for the owner, never a nudge.

"Text token" is about the token, not about every place the CSS paints it:
``--muted-foreground`` is also the fill or stroke of status dots, switch
tracks, scrollbar thumbs, spinners and skeletons (components.css), so a moved
``muted_foreground`` shifts those by the same step, always away from the page.

``primary``, ``brand`` and the status colours read AS text on a page or a tint;
they are also fills, so they are not moved here: the derived ``*_text`` tokens
(``destructive_text`` #3320, then ``primary_text`` ... #2885) carry that, solved by
``ThemeTokens``. The remaining failure that is NOT a text-colour fix is reported,
never applied: the ``input`` border is a non-text UI edge.

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
from collections.abc import Callable
from dataclasses import dataclass, replace

import django
from django.conf import settings

if not settings.configured:
    settings.configure(DEFAULT_AUTO_FIELD="django.db.models.BigAutoField")
django.setup()

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "python"))

from djust.theming._types import ColorScale, ThemeTokens  # noqa: E402
from djust.theming.a11y_exemptions import CONTRAST_PAIRS  # noqa: E402
from djust.theming.accessibility import AccessibilityValidator  # noqa: E402
from djust.theming.presets import THEME_PRESETS  # noqa: E402

MODES = ("light", "dark")
#: The fills that are also painted as text, each with a derived ``<fill>_text`` colour.
DERIVED_FILLS = ("primary", "brand", "info", "success", "warning", "destructive")
THEMES_DIR = os.path.join(ROOT, "python", "djust", "theming", "themes")
EXEMPTIONS_PATH = os.path.join(ROOT, "python", "djust", "theming", "a11y_exemptions.py")

#: Default cap, in HSL lightness points, on a move ``--apply`` writes.
DEFAULT_MAX_DELTA = 15

_validator = AccessibilityValidator()

# Foreground tokens whose colour IS text, so a lightness move restyles text only
# (``link_hover`` is checked here, not in ``CONTRAST_PAIRS``; see the docstring).
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
HOVER_TOKEN = "link_hover"
TEXT_TOKENS = LABEL_TOKENS | {HOVER_TOKEN}
HOVER_PAIRS: list[tuple[str, str, float, str]] = [
    (HOVER_TOKEN, "background", 4.5, "link hover on background"),
    (HOVER_TOKEN, "card", 4.5, "link hover on card"),
]

#: A move aims this far above the minimum, so a one-step rounding of the HSL
#: integers cannot put a fixed pair back under it. A pair is only MOVED when it
#: is below the bare minimum.
SOLVE_MARGIN = 0.05


def _ratio(fg: ColorScale, bg: ColorScale) -> float:
    return _validator.calculate_contrast_ratio(fg, bg)


def pairs_for(token: str) -> list[tuple[str, float]]:
    """``[(surface token, minimum)]`` the matrix (plus the hover pairs) checks ``token`` against."""
    return [(bg, mn) for fg, bg, mn, _label in CONTRAST_PAIRS + HOVER_PAIRS if fg == token]


def margin(colour: ColorScale, surfaces: list[tuple[ColorScale, float]]) -> float:
    """Worst ``ratio - minimum`` of ``colour`` over the surfaces (>= 0 passes)."""
    return min(_ratio(colour, surface) - mn for surface, mn in surfaces)


def solve_lightness(
    colour: ColorScale,
    surfaces: list[tuple[ColorScale, float]],
    accept: Callable[[int], bool] | None = None,
) -> int | None:
    """Nearest integer lightness (hue and saturation kept) that passes every
    surface by ``SOLVE_MARGIN``, ``None`` when ``colour`` already passes (and
    ``accept`` allows its lightness), ``-1`` when no lightness in 0..100 does.
    ``accept`` is an extra constraint on the lightness. The tie between two
    equally near lightnesses goes to the one with more margin."""
    allowed = accept or (lambda _light: True)
    if margin(colour, surfaces) >= 0 and allowed(colour.lightness):
        return None
    for step in range(1, 101):
        found = []
        for sign in (1, -1):
            cand = colour.lightness + sign * step
            if 0 <= cand <= 100 and allowed(cand):
                m = margin(ColorScale(colour.h, colour.s, cand), surfaces)
                if m >= SOLVE_MARGIN:
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
    #: Why this move is never a nudge ("" when it can be one).
    hold: str = ""

    @property
    def delta(self) -> int:
        return abs(self.after_lightness - self.before.lightness)

    @property
    def after(self) -> ColorScale:
        return ColorScale(self.before.h, self.before.s, self.after_lightness)

    def is_nudge(self, max_delta: int) -> bool:
        return not self.flip and not self.hold and self.delta <= max_delta

    def kind(self, max_delta: int = DEFAULT_MAX_DELTA) -> str:
        if self.hold:
            return self.hold
        if self.flip:
            return "polarity flip"
        return "nudge" if self.delta <= max_delta else f"large move ({self.delta} pts)"


def _single_move(
    name: str,
    mode: str,
    token: str,
    colour: ColorScale,
    surfaces: list[tuple[ColorScale, float]],
    comment: str,
    accept: Callable[[int], bool] | None = None,
) -> Move | None:
    light = solve_lightness(colour, surfaces, accept)
    if light is None:
        return None
    if light == -1:
        raise SystemExit(f"{name}/{mode}: no lightness fixes {token}")
    mean = sum(s.lightness for s, _ in surfaces) / len(surfaces)
    flip = (colour.lightness >= mean) != (light >= mean)
    hold = "documented hex" if _HEX.search(comment) else ""
    return Move(name, mode, token, colour, light, flip, hold)


def moves_for_mode(
    name: str,
    mode: str,
    tokens: ThemeTokens,
    comments: Callable[[str], str] | None = None,
    max_delta: int = DEFAULT_MAX_DELTA,
) -> list[Move]:
    """Every solved move for one preset mode. ``comments(token)`` returns the
    trailing source comment of a token's line (default: read the theme file)."""
    note = comments or (lambda token: source_comment(name, mode, token))

    def surfaces_of(token: str) -> list[tuple[ColorScale, float]]:
        return [(getattr(tokens, bg), mn) for bg, mn in pairs_for(token)]

    moves: list[Move] = []
    for token in sorted(LABEL_TOKENS - {"link"}):
        move = _single_move(
            name, mode, token, getattr(tokens, token), surfaces_of(token), note(token)
        )
        if move:
            moves.append(move)

    link = tokens.link
    link_move = _single_move(name, mode, "link", link, surfaces_of("link"), note("link"))
    link_after = link_move.after if link_move else link
    hover = tokens.link_hover
    # The hover keeps the side of the resting link it started on, and never
    # equals it: a hover that darkened a link must still darken it.
    side = (hover.lightness > link.lightness) - (hover.lightness < link.lightness)

    def keeps_side(light: int) -> bool:
        return side == 0 or (light > link_after.lightness) - (light < link_after.lightness) == side

    hover_move = _single_move(
        name, mode, HOVER_TOKEN, hover, surfaces_of(HOVER_TOKEN), note(HOVER_TOKEN), keeps_side
    )
    pair = [m for m in (link_move, hover_move) if m]
    if link_move and not all(m.is_nudge(max_delta) for m in pair):
        # They move together or not at all: a nudge whose partner is not one is held.
        pair = [
            replace(m, hold="paired with a held link/link_hover move")
            if m.is_nudge(max_delta)
            else m
            for m in pair
        ]
    moves.extend(pair)
    return moves


def collect_moves(max_delta: int = DEFAULT_MAX_DELTA) -> list[Move]:
    moves: list[Move] = []
    for name in sorted(THEME_PRESETS):
        for mode in MODES:
            moves.extend(
                moves_for_mode(name, mode, getattr(THEME_PRESETS[name], mode), max_delta=max_delta)
            )
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


def source_comment(preset: str, mode: str, token: str) -> str:
    """The trailing comment of ``token``'s line in the preset's theme file ("" when
    the file or a literal line is missing)."""
    path = os.path.join(THEMES_DIR, f"{preset}.py")
    if not os.path.exists(path):
        return ""
    with open(path) as fh:
        text = fh.read()
    lo, hi = _block_span(text, mode)
    hit = re.compile(_LINE.format(token=token), re.M).search(text[lo:hi])
    return (hit.group(6) or "") if hit else ""


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def apply_moves(moves: list[Move]) -> int:
    """Rewrite the literal ``token=ColorScale(h, s, l)`` lines of each theme file.
    All edits are computed first and the files written last (each replaced
    atomically), so a refused edit changes no file. A trailing comment that
    quotes a hex is refreshed to the new colour (held moves never reach here
    unless ``--include-substantial`` was passed)."""
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
        _write_atomic(path, text)
    return len(edited)


def _exemption_keys(text: str) -> list[tuple[str, str, str, str]]:
    """The (preset, mode, fg, bg) of every row in the exemptions file, in both
    shapes it uses: the literal blocks and the ``_PAIR_DEBT_2885`` lines."""
    block = re.compile(
        r'^    \(\n        "([^"]+)",\n        "([^"]+)",\n        "([^"]+)",\n        "([^"]+)",\n    \): "',
        re.M,
    )
    row = re.compile(r'^    \("([^"]+)", "([^"]+)", "([^"]+)", "([^"]+)"\): [\d.]+,$', re.M)
    return [m.groups() for m in block.finditer(text)] + [m.groups() for m in row.finditer(text)]


def prune_exemptions() -> int:
    """Delete exemption rows whose pair now passes (the stale-exemption gate fails
    on them). Reads the rows from ``EXEMPTIONS_PATH`` and measures the theme
    sources as loaded, so ``--apply`` runs it in a fresh interpreter (``--prune``)
    after editing them."""
    with open(EXEMPTIONS_PATH) as fh:
        text = fh.read()
    stale = []
    for preset, mode, fg, bg in _exemption_keys(text):
        minimum = next(m for f, b, m, _ in CONTRAST_PAIRS if f == fg and b == bg)
        tokens = getattr(THEME_PRESETS[preset], mode)
        if _ratio(getattr(tokens, fg), getattr(tokens, bg)) >= minimum:
            stale.append((preset, mode, fg, bg))
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
    _write_atomic(EXEMPTIONS_PATH, text)
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
            kind = m.kind(max_delta)
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

    def derived_rows() -> tuple[str, int, int]:
        """Rows for the derived ``*_text`` colours (a fill read as text), skipping the
        presets whose fill already reads (their text colour IS the fill)."""
        out, changed, kept = [], 0, 0
        for name in sorted(THEME_PRESETS):
            first = True
            for mode in MODES:
                tokens = getattr(THEME_PRESETS[name], mode)
                for fill in DERIVED_FILLS:
                    colour, text = getattr(tokens, fill), getattr(tokens, f"{fill}_text")
                    if colour == text:
                        kept += 1
                        continue
                    changed += 1
                    surfaces = ["background", "card"]
                    if fill in ("info", "success", "warning", "destructive"):
                        surfaces.append(f"{fill}_tint")
                    cells = "".join(
                        f'<span class="pair"><em>on {sf}</em>{_chip(colour, getattr(tokens, sf))}'
                        f"{_chip(text, getattr(tokens, sf))}</span>"
                        for sf in surfaces
                    )
                    anchor = f' id="derived-{name}"' if first else ""
                    first = False
                    out.append(
                        f"<tr{anchor}><td>{name}</td><td>{mode}</td><td><code>{fill}_text</code></td>"
                        f'<td>{cells}<span class="fill">fill (unchanged): '
                        f'<i style="background:{_css(colour)}"></i> '
                        f"{colour.lightness}</span></td>"
                        f"<td>{colour.lightness} &rarr; {text.lightness}</td></tr>"
                    )
        return "\n".join(out), changed, kept

    applied_html = (
        f"<h2>1. Text nudges still pending ({len(applied)}), no polarity flip, at most "
        f"{max_delta} lightness points</h2>\n<table><tr><th>preset</th><th>mode</th><th>token</th>"
        f"<th>before / after</th><th>L</th></tr>\n{rows(applied)}</table>"
        if applied
        else "<p>Section 1 (text nudges) is empty: they merged in #3372.</p>"
    )
    derived_html, derived_changed, derived_kept = derived_rows()
    index = " ".join(
        f'<a href="#derived-{n}">{n}</a>'
        for n in sorted(THEME_PRESETS)
        if f'id="derived-{n}"' in derived_html
    )

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
.fill {{ display: block; font-size: 11px; color: #777; }}
.fill i {{ display: inline-block; width: 1.4em; height: .8em; border: 1px solid rgba(128,128,128,.4); vertical-align: middle; }}
nav.index a {{ margin-right: .5rem; }}
h2 {{ margin-top: 2.5rem; }}
</style></head><body>
<h1>Theme text contrast (#2885): before / after</h1>
<p>Each pair of chips is the token as it ships (left) and as solved (right); the number is the WCAG ratio.
Only text tokens move, in lightness, hue and saturation kept.</p>
<h2>0. Applied: derived readable text colours ({derived_changed} moved, {derived_kept} fills already read and are unchanged)</h2>
<p>The FILL (what buttons, badges, dots and borders paint) is never touched: it is shown as a swatch under each row.
Only the colour used AS TEXT (links, <code>.text-primary</code>, alert/badge/status text) moves, left chip = the fill used as text today,
right chip = the derived text colour. Same hue and saturation; lightness only.</p>
<nav class="index">{index}</nav>
<table><tr><th>preset</th><th>mode</th><th>token</th><th>before / after</th><th>L</th></tr>
{derived_html}</table>
{applied_html}
<h2>2. SUBSTANTIAL, not applied: polarity flips, larger moves, documented-hex and paired moves ({len(substantial)})</h2>
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
    held = [m for m in moves if m.hold and not m.flip]
    big = [m for m in moves if not m.flip and not m.hold and m.delta > max_delta]
    groups = unfixable_by_text()
    print(f"text pairs failing, fixable by a lightness move: {len(moves)} token moves")
    print(f"  nudges (no flip, <= {max_delta} pts): {len(nudges)}")
    print(f"  SUBSTANTIAL polarity flips: {len(flips)}")
    print(f"  SUBSTANTIAL same-side moves > {max_delta} pts: {len(big)}")
    print(f"  SUBSTANTIAL held back (documented hex / link pair): {len(held)}")
    print(f"not a text move: as-text (primary, status): {len(groups['as-text'])} pairs")
    print(f"not a text move: input border (3:1): {len(groups['border'])} pairs")


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
    moves = collect_moves(args.max_delta)
    if args.html:
        write_html(args.html, moves, args.max_delta)
        print(f"wrote {args.html}")
    if args.proposals:
        for m in moves:
            print(
                f"{m.kind(args.max_delta).upper():14} {m.preset}/{m.mode} {m.token}: "
                f"L {m.before.lightness} -> {m.after_lightness} ({m.delta} pts)"
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
