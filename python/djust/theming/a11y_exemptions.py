"""The a11y contract for built-in theme presets: the canonical WCAG AA
text-contrast matrix plus documented, reason-carrying exemptions.

Two consumers enforce the same contract from this one module (#1646: one
shared definition — the matrix was previously copy-pasted and had drifted
between three sites):

1. ``python/djust/tests/test_theming_contrast_all_presets_2060.py`` gates
   every registered preset in ``djust.theming.presets.THEME_PRESETS``
   against ``CONTRAST_PAIRS`` (every pair x 2 modes; the 5 newest themes are
   gated strictly, with no exemptions permitted, in
   ``test_theming_new_themes_v11.py::TestContrast``).
2. ``djust.theming.checks.check_preset_contrast`` (``djust_theming.W001``)
   skips pairs listed here so that ``manage.py check`` stays warning-clean
   on shipped presets while user-authored presets get full validation
   (#2874: W001 previously had no exemption mechanism, so every shipped
   preset warned on every run and the ``djust new`` scaffold silenced the
   check outright).

Scope discipline (#1079): the legacy palettes listed here are NOT
redesigned. Every (preset, mode, fg_token, bg_token) pair that failed AA when
its gate was introduced is documented here rather than silently left ungated
or force-fixed — recolouring brand palettes (dracula, catppuccin, nord,
solarized, ...) to satisfy a ratio would erase their identity. The
``default``/``blue``/``shadcn``/``slate`` status-label fixes (#2874) are the
exception that proves the rule: those palettes were fixed, and their now-stale
entries removed. So was ``djust`` (#2996), because djust owns that identity:
its labels on the bright fills became dark ink, and its 17 entries went.
``scripts/report_theme_contrast.py`` prints the catastrophic (<3.0) ones that
remain; the branded-palette remediation issue (#2885) owns every entry below.

#2885 moved the text TOKENS first: ``scripts/fix_theme_text_contrast.py``
solves each failing ``*_foreground`` label, ``link`` and ``link_hover`` in
lightness only (hue and saturation kept) and writes the small, same-side
moves, which removed their rows. The entries that remain for those tokens are
polarity flips, large moves and tokens whose source documents an exact hex,
all of which wait for the owner's review (``--proposals`` lists them), and
``--check`` fails while a small move is still pending.

Entries were auto-generated from ``scripts/report_theme_contrast.py
--python-dict`` (2026-07, #2060; 2026-09, #2874) — do not hand-edit
ratios; regenerate the affected entry by re-running the report if a
palette changes.

**Load-bearing invariant (#1859):** every entry here must still be NEEDED.
``test_theming_contrast_all_presets_2060.py`` asserts that any exemption
whose pair now (post-palette-edit) meets its WCAG minimum is a "stale
exemption" and fails the suite until the entry is removed. This keeps the
list from silently drifting into false protection — an exemption for a pair
that already passes is decorative, not documentation.

To grandfather a NEW deliberate exception (e.g. a newly authored legacy-
matching palette), add an entry here with a specific reason (not the
generic "grandfathered..." string used for the bulk imports) citing
why the ratio is acceptable (brand identity, large-text-only usage, etc.)
and the tracking issue.
"""

from __future__ import annotations

# Keyed by (preset_name, mode, foreground_token, background_token) ->
# human-readable reason, one entry per failing (preset, mode, pair) in the
# canonical CONTRAST_PAIRS matrix below.
# ---------------------------------------------------------------------------
# Canonical WCAG AA text-contrast matrix (#2874, supersedes the three
# copy-pasted 6-pair matrices that previously drifted in
# test_theming_contrast_all_presets_2060.py, scripts/report_theme_contrast.py
# and theming/checks.py — #1646: one shared definition, not N copies).
#
# Every pair is a REAL rendered text surface in the generated component CSS
# (python/djust/theming/static/djust_theming/css/components.css): badges and
# buttons render *_foreground as 14px/semibold or 16px text, which is WCAG
# *normal* text — so AA normal-text 4.5:1 applies to every pair (the 3:1
# large-text carve-out does not: WCAG large text is >=24px regular or
# >=18.66px bold; ``.status-badge`` is 14px/600). ``muted_foreground`` is
# checked against BOTH ``muted`` (the .avatar surface) and ``background``
# (dimmed prose text).
#
# Consumers:
#   - ``djust.theming.checks.check_preset_contrast`` (djust_theming.W001)
#   - ``python/djust/tests/test_theming_contrast_all_presets_2060.py``
#   - ``scripts/report_theme_contrast.py``
CONTRAST_PAIRS: list[tuple[str, str, float, str]] = [
    ("foreground", "background", 4.5, "text on background"),
    ("card_foreground", "card", 4.5, "text on card"),
    ("popover_foreground", "popover", 4.5, "text on popover"),
    ("code_foreground", "code", 4.5, "text on code"),
    ("primary_foreground", "primary", 4.5, "text on primary"),
    ("secondary_foreground", "secondary", 4.5, "text on secondary"),
    ("muted_foreground", "muted", 4.5, "text on muted"),
    ("muted_foreground", "background", 4.5, "muted text on background"),
    ("accent_foreground", "accent", 4.5, "text on accent"),
    ("destructive_foreground", "destructive", 4.5, "text on destructive"),
    ("success_foreground", "success", 4.5, "text on success"),
    ("warning_foreground", "warning", 4.5, "text on warning"),
    ("info_foreground", "info", 4.5, "text on info"),
    ("brand_foreground", "brand", 4.5, "text on brand"),
    # --- Pairs added by #3281 / #3165 -------------------------------------
    # The 14 pairs above are all ``*_foreground`` labels on their fill. These
    # are the other places a theme colour lands on a surface, measured the way
    # the components paint them.
    #
    # A theme colour used AS TEXT. ``link`` is a text token: ``.link`` /
    # ``.text-link`` render ``--link`` (falling back to ``--primary-text``), and
    # ``.btn-link`` and ``.breadcrumb-item a`` render it too (#3165).
    ("link", "background", 4.5, "link text on background"),
    ("link", "card", 4.5, "link text on card"),
    # ``primary``, ``brand``, ``info``, ``success``, ``warning`` and
    # ``destructive`` are FILLS (a ``*_foreground`` label sits on them), so they
    # are often unreadable as text: bright brand colours on white, deep reds on
    # a dark page. Everything that paints them AS TEXT (``.text-primary``,
    # ``.btn-link``, ``.alert-*``, ``.toast-*``, badges, status text) reads the
    # derived ``*_text`` colour instead (``--primary-text``, ``--info-text``,
    # ... #3320, #2885): the same hue and saturation at the lightness that
    # reaches 4.5:1 on the page, a card and the fill's own 10%/15% washes, and
    # the fill unchanged when it already reads. They are solved, not chosen, so
    # these pass for every preset and carry no exemption. ``*_tint`` is the fill
    # at 10% alpha over the page or a card (``.alert-*``, ``.toast-*``), derived
    # by ``ThemeTokens``; it is not a token.
    ("primary_text", "background", 4.5, "primary text on background"),
    ("primary_text", "card", 4.5, "primary text on card"),
    ("brand_text", "background", 4.5, "brand text on background"),
    ("brand_text", "card", 4.5, "brand text on card"),
    ("info_text", "info_tint", 4.5, "info text on its alert tint"),
    ("info_text", "background", 4.5, "info text on background"),
    ("info_text", "card", 4.5, "info text on card"),
    ("success_text", "success_tint", 4.5, "success text on its alert tint"),
    ("success_text", "background", 4.5, "success text on background"),
    ("success_text", "card", 4.5, "success text on card"),
    ("warning_text", "warning_tint", 4.5, "warning text on its alert tint"),
    ("warning_text", "background", 4.5, "warning text on background"),
    ("warning_text", "card", 4.5, "warning text on card"),
    ("destructive_text", "destructive_tint", 4.5, "destructive text on its alert tint"),
    ("destructive_text", "background", 4.5, "error text on background"),
    ("destructive_text", "card", 4.5, "error text on card"),
    # WCAG 1.4.11 non-text contrast: the edge of a text input is the only thing
    # that tells a user where it is, so it needs 3:1 (not the 4.5:1 of text).
    ("input", "background", 3.0, "input border on background"),
]

#: The pairs #3281 / #3165 / #2885 added to the matrix. The legacy palettes'
#: failures on the ones that are not solved are documented in
#: ``A11Y_EXEMPTIONS`` with a reference to #2885, which owns the palette-wide
#: remediation; ``djust`` and the five core presets are gated on the 14 original
#: pairs strictly.
SOLVED_PAIR_KEYS: frozenset[tuple[str, str]] = frozenset(
    {
        (token, surface)
        for token, surfaces in (
            ("primary_text", ("background", "card")),
            ("brand_text", ("background", "card")),
            ("info_text", ("info_tint", "background", "card")),
            ("success_text", ("success_tint", "background", "card")),
            ("warning_text", ("warning_tint", "background", "card")),
            ("destructive_text", ("destructive_tint", "background", "card")),
        )
        for surface in surfaces
    }
)
#: The derived ``*_text`` colours of ``ThemeTokens`` (``destructive_text``,
#: ``primary_text``, ...): fills painted as text, solved by lightness.
DERIVED_TEXT_TOKENS: frozenset[str] = frozenset(token for token, _surface in SOLVED_PAIR_KEYS)

#: Pairs that hold by construction for ANY preset (#3320, #2885): every
#: ``*_text`` colour is solved against exactly these surfaces, so it clears 4.5:1
#: on all of them unless the page and card are so mid-tone that neither black nor
#: white does. No exemption is needed or accepted for them, and a user-authored
#: preset cannot trip W001 on them with ordinary colours.
NEW_PAIR_KEYS: frozenset[tuple[str, str]] = SOLVED_PAIR_KEYS | {
    ("link", "background"),
    ("link", "card"),
    ("input", "background"),
}


A11Y_EXEMPTIONS: dict[tuple[str, str, str, str], str] = {
    (
        "adaptive",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "amber",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "amber",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "aurora",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.59",
    (
        "aurora",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.37",
    (
        "aurora",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.32",
    (
        "aurora",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.37",
    (
        "aurora",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.52",
    (
        "ayu",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.24",
    (
        "ayu",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.64",
    (
        "ayu",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.64",
    (
        "ayu",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.70",
    (
        "candy",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.56",
    (
        "candy",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "candy",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.11",
    (
        "candy",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.82",
    (
        "catppuccin",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.09",
    (
        "catppuccin",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.30",
    (
        "catppuccin",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.77",
    (
        "catppuccin",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.09",
    (
        "catppuccin",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.30",
    (
        "cyberdeck",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.85",
    (
        "cyberdeck",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.29",
    (
        "cyberdeck",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.00",
    (
        "cyberpunk",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "cyberpunk",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.77",
    (
        "cyberpunk",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "cyberpunk",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.77",
    (
        "dashboard",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.02",
    (
        "dashboard",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.42",
    (
        "docs",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.89",
    (
        "docs",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.89",
    (
        "docs",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.30",
    (
        "dracula",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.00",
    (
        "dracula",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.48",
    (
        "dracula",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.92",
    (
        "dracula",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.02",
    (
        "ember",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.11",
    (
        "ember",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.46",
    (
        "ember",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.37",
    (
        "everforest",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.73",
    (
        "everforest",
        "light",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.06",
    (
        "everforest",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.57",
    (
        "forest",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.30",
    (
        "forest",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "forest",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.30",
    (
        "forest",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "forest_floor",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.49",
    (
        "github",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.65",
    (
        "github",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.35",
    (
        "green",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.20",
    (
        "green",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.62",
    (
        "gruvbox",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.51",
    (
        "gruvbox",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.49",
    (
        "gruvbox",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.38",
    (
        "gruvbox",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.49",
    (
        "handcraft",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.96",
    (
        "handcraft",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.36",
    (
        "handcraft",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.46",
    (
        "ink",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.80",
    (
        "kanagawa",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.76",
    (
        "kanagawa",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.97",
    (
        "linear",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.37",
    (
        "linear",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.67",
    (
        "linear",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.82",
    (
        "magazine",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.50",
    (
        "magazine",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.50",
    (
        "magazine",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.19",
    (
        "magazine",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.19",
    (
        "medical",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.04",
    (
        "medical",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.52",
    (
        "medical",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.46",
    (
        "midnight",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.04",
    (
        "midnight",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.32",
    (
        "monokai",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.80",
    (
        "monokai",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.80",
    (
        "monokai",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.80",
    (
        "monokai",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.80",
    (
        "monokai",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.95",
    (
        "natural20",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.63",
    (
        "natural20",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "natural20",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.63",
    (
        "natural20",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.36",
    (
        "nebula",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.41",
    (
        "nebula",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "nebula",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.41",
    (
        "nebula",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "neon_noir",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.40",
    (
        "neon_noir",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.19",
    (
        "neon_noir",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.19",
    (
        "nord",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.02",
    (
        "nord",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.14",
    (
        "nord",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.22",
    (
        "nord",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.02",
    (
        "nord",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.14",
    (
        "notion",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.92",
    (
        "notion",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.18",
    (
        "notion",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "ocean_deep",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.51",
    (
        "ocean_deep",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "ocean_deep",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.74",
    (
        "ocean_deep",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "one_dark",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.36",
    (
        "one_dark",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.20",
    (
        "one_dark",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.20",
    (
        "one_dark",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.70",
    (
        "orange",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.74",
    (
        "orange",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.62",
    (
        "outrun",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.61",
    (
        "outrun",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "outrun",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.08",
    (
        "outrun",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.61",
    (
        "outrun",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "outrun",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.08",
    (
        "paper",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.16",
    (
        "poimandres",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.74",
    (
        "poimandres",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.44",
    (
        "poimandres",
        "dark",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.21",
    (
        "purple",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.62",
    (
        "raycast",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.26",
    (
        "rose",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.62",
    (
        "rose_pine",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.91",
    (
        "rose_pine",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.91",
    (
        "rose_pine",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.65",
    (
        "rose_pine",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.91",
    (
        "rose_pine",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.91",
    (
        "solarized",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.90",
    (
        "solarized",
        "light",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.98",
    (
        "solarized",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.90",
    (
        "solarpunk",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.84",
    (
        "solarpunk",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.42",
    (
        "stripe",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.39",
    (
        "stripe",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.22",
    (
        "sunrise",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.18",
    (
        "sunrise",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.76",
    (
        "sunrise",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "supabase",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.99",
    (
        "supabase",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "supabase",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.39",
    (
        "swiss",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.88",
    (
        "swiss",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.88",
    (
        "synthwave",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.34",
    (
        "synthwave",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "synthwave",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.34",
    (
        "synthwave",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "tailwind",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.86",
    (
        "tailwind",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "tailwind",
        "light",
        "muted_foreground",
        "background",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.45",
    (
        "tailwind",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.86",
    (
        "tailwind",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.78",
    (
        "terminal",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.42",
    (
        "tokyo_night",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.38",
    (
        "tokyo_night",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.64",
    (
        "tokyo_night",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.91",
    (
        "tokyo_night",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.38",
    (
        "tokyo_night",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.64",
    (
        "vercel",
        "light",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.44",
    (
        "vercel",
        "light",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.46",
    (
        "vercel",
        "dark",
        "primary_foreground",
        "primary",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.44",
    (
        "vercel",
        "dark",
        "destructive_foreground",
        "destructive",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.96",
    # --- Bulk grandfathering: W001 matrix reconciliation (2026-09, #2874) ---
    # The W001 matrix checks 13 token pairs; pairs outside the original
    # #2060 six had never been gated, so their legacy failures surface
    # here as documented debt (134 of these are catastrophic <3.0 —
    # tracked in the #2874 follow-up for branded-palette remediation).
    (
        "adaptive",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.20",
    (
        "adaptive",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.29",
    (
        "adaptive",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.26",
    (
        "adaptive",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.42",
    (
        "amber",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "amber",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "art_deco",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.04",
    (
        "art_nouveau",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.21",
    (
        "aurora",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "aurora",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "aurora",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.52",
    (
        "aurora",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.52",
    (
        "ayu",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.95",
    (
        "ayu",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.60",
    (
        "candy",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.05",
    (
        "candy",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.33",
    (
        "candy",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.87",
    (
        "candy",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.92",
    (
        "candy",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.09",
    (
        "candy",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.36",
    (
        "cyberdeck",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.85",
    (
        "cyberdeck",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.45",
    (
        "cyberdeck",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.85",
    (
        "cyberpunk",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.25",
    (
        "cyberpunk",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.14",
    (
        "cyberpunk",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.25",
    (
        "cyberpunk",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.14",
    (
        "dashboard",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.24",
    (
        "dashboard",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.27",
    (
        "dashboard",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.02",
    (
        "dashboard",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.02",
    (
        "docs",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.03",
    (
        "docs",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.67",
    (
        "docs",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.16",
    (
        "docs",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.89",
    (
        "docs",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.17",
    (
        "docs",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.54",
    (
        "dracula",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.40",
    (
        "dracula",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.46",
    (
        "dracula",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.90",
    (
        "dracula",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.92",
    (
        "ember",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.85",
    (
        "ember",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.53",
    (
        "everforest",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.53",
    (
        "everforest",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.40",
    (
        "forest",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "forest",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.92",
    (
        "forest",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "forest",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "forest",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.92",
    (
        "forest",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "forest_floor",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.97",
    (
        "forest_floor",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.77",
    (
        "forest_floor",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.26",
    (
        "forest_floor",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.90",
    (
        "forest_floor",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.03",
    (
        "forest_floor",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.56",
    (
        "github",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.46",
    (
        "github",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.36",
    (
        "github",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.65",
    (
        "github",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.94",
    (
        "green",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.19",
    (
        "green",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.04",
    (
        "green",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "green",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.20",
    (
        "green",
        "dark",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.15",
    (
        "green",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.11",
    (
        "gruvbox",
        "light",
        "code_foreground",
        "code",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.79",
    (
        "gruvbox",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.76",
    (
        "gruvbox",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.43",
    (
        "gruvbox",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.38",
    (
        "handcraft",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.96",
    (
        "handcraft",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.59",
    (
        "handcraft",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.96",
    (
        "handcraft",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.87",
    (
        "handcraft",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "ink",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.72",
    (
        "kanagawa",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.35",
    (
        "kanagawa",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.35",
    (
        "kanagawa",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.50",
    (
        "kanagawa",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.13",
    (
        "kanagawa",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.50",
    (
        "linear",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.26",
    (
        "linear",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.42",
    (
        "linear",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.67",
    (
        "linear",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.67",
    (
        "linear",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.21",
    (
        "magazine",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.95",
    (
        "magazine",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.19",
    (
        "magazine",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.50",
    (
        "magazine",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.53",
    (
        "magazine",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.20",
    (
        "magazine",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.19",
    (
        "medical",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.24",
    (
        "medical",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.04",
    (
        "medical",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.76",
    (
        "medical",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.27",
    (
        "medical",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.52",
    (
        "medical",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.17",
    (
        "midnight",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.92",
    (
        "midnight",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.10",
    (
        "midnight",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.84",
    (
        "midnight",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.07",
    (
        "midnight",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.89",
    (
        "midnight",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.72",
    (
        "mission_control",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.49",
    (
        "mission_control",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.83",
    (
        "monokai",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.99",
    (
        "natural20",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.33",
    (
        "natural20",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.63",
    (
        "natural20",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.63",
    (
        "natural20",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "natural20",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.63",
    (
        "natural20",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.63",
    (
        "nebula",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "nebula",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.41",
    (
        "nebula",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.41",
    (
        "nebula",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.30",
    (
        "nebula",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.41",
    (
        "nebula",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.41",
    (
        "neon_noir",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.52",
    (
        "neon_noir",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.51",
    (
        "neon_noir",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.40",
    (
        "neon_noir",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.40",
    (
        "nord",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.69",
    (
        "nord",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.69",
    (
        "notion",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.66",
    (
        "notion",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.92",
    (
        "notion",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "notion",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.18",
    (
        "notion",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.45",
    (
        "obsidian",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.44",
    (
        "ocean_deep",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.90",
    (
        "ocean_deep",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.51",
    (
        "ocean_deep",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.51",
    (
        "one_dark",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.37",
    (
        "one_dark",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.94",
    (
        "one_dark",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.03",
    (
        "one_dark",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.94",
    (
        "orange",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.19",
    (
        "orange",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.04",
    (
        "orange",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "orange",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "orange",
        "dark",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.15",
    (
        "orange",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.11",
    (
        "outrun",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.37",
    (
        "outrun",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.37",
    (
        "outrun",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.37",
    (
        "outrun",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.37",
    (
        "paper",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.83",
    (
        "poimandres",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.74",
    (
        "poimandres",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.44",
    (
        "poimandres",
        "dark",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.32",
    (
        "poimandres",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.44",
    (
        "purple",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.19",
    (
        "purple",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.04",
    (
        "purple",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "purple",
        "dark",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.15",
    (
        "purple",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.11",
    (
        "raycast",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.33",
    (
        "raycast",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.87",
    (
        "raycast",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.19",
    (
        "raycast",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.19",
    (
        "retro_computing",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.18",
    (
        "retro_computing",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.05",
    (
        "retro_computing",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.30",
    (
        "retro_computing",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.94",
    (
        "rose",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.19",
    (
        "rose",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.04",
    (
        "rose",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "rose",
        "dark",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.15",
    (
        "rose",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.11",
    (
        "rose_pine",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.09",
    (
        "rose_pine",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.09",
    (
        "solarized",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.61",
    (
        "solarized",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.97",
    (
        "solarized",
        "light",
        "warning_foreground",
        "warning",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.62",
    (
        "solarized",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.90",
    (
        "solarized",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.97",
    (
        "solarized",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.90",
    (
        "solarpunk",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.84",
    (
        "solarpunk",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.18",
    (
        "solarpunk",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.84",
    (
        "stripe",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.81",
    (
        "stripe",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.87",
    (
        "stripe",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.39",
    (
        "stripe",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.21",
    (
        "sunrise",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.90",
    (
        "sunrise",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.54",
    (
        "sunrise",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.37",
    (
        "sunrise",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.01",
    (
        "supabase",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "supabase",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.99",
    (
        "supabase",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "supabase",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "supabase",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "swiss",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.18",
    (
        "swiss",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.62",
    (
        "swiss",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.51",
    (
        "swiss",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.88",
    (
        "synthwave",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "synthwave",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.74",
    (
        "tailwind",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.06",
    (
        "tailwind",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "tailwind",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.86",
    (
        "tailwind",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.86",
    (
        "tailwind",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.59",
    (
        "tailwind",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.86",
    (
        "terminal",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.42",
    (
        "terminal",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.20",
    (
        "terminal",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.42",
    (
        "vercel",
        "light",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 2.32",
    (
        "vercel",
        "light",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.44",
    (
        "vercel",
        "light",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.44",
    (
        "vercel",
        "dark",
        "success_foreground",
        "success",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 1.87",
    (
        "vercel",
        "dark",
        "info_foreground",
        "info",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.44",
    (
        "vercel",
        "dark",
        "brand_foreground",
        "brand",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.44",
}

# Failures of the pairs #3281 / #3165 added to ``CONTRAST_PAIRS`` (``NEW_PAIR_KEYS``),
# as ``(preset, mode, fg_token, bg_token) -> measured ratio``. Generated by
# ``scripts/report_theme_contrast.py --debt-table``; do not hand-edit ratios.
# One line per entry rather than the five-line form above: this is a table of
# measurements, and it is ~600 rows. Each ratio is enforced: a palette change
# that moves it, better or worse, fails a test until the row is regenerated.
# The ``legal`` preset was fixed instead of listed here, and so were its four
# older entries. The other presets are NOT recoloured: palette-wide
# remediation, including status colours that fail as
# text on their alert tint and the near-invisible ``--input`` borders of the
# shadcn-derived palettes, is #2885. ``TestExemptionsStillNeeded`` fails any
# row whose pair now passes, so a fix to a palette must delete its rows.
_PAIR_DEBT_2885: dict[tuple[str, str, str, str], float] = {
    ("adaptive", "light", "input", "background"): 1.07,
    ("adaptive", "dark", "input", "background"): 1.20,
    ("amber", "light", "input", "background"): 1.04,
    ("amber", "dark", "input", "background"): 1.11,
    ("art_deco", "light", "input", "background"): 1.14,
    ("art_deco", "dark", "input", "background"): 1.38,
    ("art_nouveau", "light", "input", "background"): 1.40,
    ("art_nouveau", "dark", "input", "background"): 1.63,
    ("aurora", "light", "input", "background"): 1.48,
    ("aurora", "dark", "input", "background"): 1.40,
    ("ayu", "light", "input", "background"): 1.14,
    ("ayu", "dark", "input", "background"): 1.47,
    ("bauhaus", "light", "input", "background"): 1.03,
    ("blue", "light", "input", "background"): 1.24,
    ("blue", "dark", "input", "background"): 1.21,
    ("candy", "light", "input", "background"): 1.14,
    ("candy", "dark", "input", "background"): 1.27,
    ("catppuccin", "light", "link", "background"): 2.04,
    ("catppuccin", "light", "link", "card"): 1.90,
    ("catppuccin", "light", "input", "background"): 1.00,
    ("catppuccin", "dark", "input", "background"): 1.11,
    ("cyberdeck", "light", "input", "background"): 1.47,
    ("cyberdeck", "dark", "input", "background"): 2.46,
    ("cyberpunk", "light", "input", "background"): 1.04,
    ("cyberpunk", "dark", "input", "background"): 1.06,
    ("dashboard", "light", "input", "background"): 1.21,
    ("dashboard", "dark", "input", "background"): 1.21,
    ("default", "light", "input", "background"): 1.27,
    ("default", "dark", "input", "background"): 1.34,
    ("djust", "light", "input", "background"): 1.27,
    ("djust", "dark", "input", "background"): 1.36,
    ("docs", "light", "input", "background"): 1.38,
    ("docs", "dark", "input", "background"): 1.26,
    ("dracula", "light", "input", "background"): 1.42,
    ("dracula", "dark", "input", "background"): 1.58,
    ("dune", "light", "input", "background"): 1.44,
    ("dune", "dark", "input", "background"): 1.54,
    ("ember", "light", "input", "background"): 1.49,
    ("ember", "dark", "input", "background"): 1.48,
    ("everforest", "light", "link", "background"): 2.49,
    ("everforest", "light", "link", "card"): 2.56,
    ("everforest", "light", "input", "background"): 1.12,
    ("everforest", "dark", "input", "background"): 1.49,
    ("forest", "light", "input", "background"): 1.04,
    ("forest", "dark", "input", "background"): 1.16,
    ("forest_floor", "light", "input", "background"): 1.14,
    ("forest_floor", "dark", "input", "background"): 1.26,
    ("github", "light", "input", "background"): 1.37,
    ("github", "dark", "input", "background"): 1.44,
    ("green", "light", "input", "background"): 1.28,
    ("green", "dark", "input", "background"): 1.32,
    ("gruvbox", "light", "link", "background"): 2.32,
    ("gruvbox", "light", "link", "card"): 2.22,
    ("gruvbox", "light", "input", "background"): 1.57,
    ("gruvbox", "dark", "input", "background"): 1.56,
    ("handcraft", "light", "input", "background"): 1.09,
    ("handcraft", "dark", "input", "background"): 1.38,
    ("ink", "light", "input", "background"): 1.05,
    ("ink", "dark", "input", "background"): 1.13,
    ("kanagawa", "light", "link", "background"): 2.55,
    ("kanagawa", "light", "link", "card"): 2.66,
    ("kanagawa", "light", "input", "background"): 1.12,
    ("kanagawa", "dark", "input", "background"): 1.51,
    ("linear", "light", "input", "background"): 1.27,
    ("linear", "dark", "input", "background"): 1.34,
    ("magazine", "light", "input", "background"): 1.25,
    ("magazine", "dark", "input", "background"): 1.28,
    ("medical", "light", "input", "background"): 1.19,
    ("medical", "dark", "input", "background"): 1.21,
    ("midnight", "light", "input", "background"): 1.22,
    ("midnight", "dark", "input", "background"): 1.22,
    ("mission_control", "light", "input", "background"): 1.58,
    ("mission_control", "dark", "input", "background"): 1.37,
    ("mono", "light", "input", "background"): 1.53,
    ("mono", "dark", "input", "background"): 1.48,
    ("monokai", "light", "link", "background"): 2.78,
    ("monokai", "light", "link", "card"): 2.88,
    ("monokai", "light", "input", "background"): 1.14,
    ("monokai", "dark", "link", "card"): 4.36,
    ("monokai", "dark", "input", "background"): 1.54,
    ("natural20", "light", "input", "background"): 1.04,
    ("natural20", "dark", "input", "background"): 1.04,
    ("nebula", "light", "input", "background"): 1.07,
    ("nebula", "dark", "input", "background"): 1.10,
    ("neon_noir", "light", "input", "background"): 1.48,
    ("neon_noir", "dark", "input", "background"): 1.20,
    ("nord", "light", "input", "background"): 1.00,
    ("nord", "dark", "link", "background"): 3.05,
    ("nord", "dark", "link", "card"): 2.43,
    ("nord", "dark", "input", "background"): 1.26,
    ("notion", "light", "input", "background"): 1.16,
    ("notion", "dark", "input", "background"): 1.46,
    ("obsidian", "light", "input", "background"): 1.47,
    ("obsidian", "dark", "input", "background"): 1.32,
    ("ocean_deep", "light", "input", "background"): 1.49,
    ("ocean_deep", "dark", "input", "background"): 1.43,
    ("one_dark", "light", "link", "background"): 2.26,
    ("one_dark", "light", "link", "card"): 2.36,
    ("one_dark", "light", "input", "background"): 1.15,
    ("one_dark", "dark", "input", "background"): 1.43,
    ("orange", "light", "input", "background"): 1.31,
    ("orange", "dark", "input", "background"): 1.25,
    ("outrun", "light", "link", "background"): 2.50,
    ("outrun", "light", "link", "card"): 2.23,
    ("outrun", "light", "input", "background"): 1.04,
    ("outrun", "dark", "input", "background"): 1.03,
    ("paper", "light", "input", "background"): 1.07,
    ("paper", "dark", "input", "background"): 1.25,
    ("poimandres", "light", "link", "background"): 1.87,
    ("poimandres", "light", "link", "card"): 1.96,
    ("poimandres", "light", "input", "background"): 1.13,
    ("poimandres", "dark", "input", "background"): 1.45,
    ("purple", "light", "input", "background"): 1.29,
    ("purple", "dark", "input", "background"): 1.21,
    ("raycast", "light", "input", "background"): 1.15,
    ("raycast", "dark", "input", "background"): 1.34,
    ("retro_computing", "light", "input", "background"): 1.15,
    ("retro_computing", "dark", "input", "background"): 1.23,
    ("rose", "light", "input", "background"): 1.29,
    ("rose", "dark", "input", "background"): 1.21,
    ("rose_pine", "light", "link", "background"): 2.09,
    ("rose_pine", "light", "link", "card"): 1.91,
    ("rose_pine", "light", "input", "background"): 1.00,
    ("rose_pine", "dark", "input", "background"): 1.12,
    ("sakura", "light", "input", "background"): 1.34,
    ("sakura", "dark", "input", "background"): 1.49,
    ("shadcn", "light", "input", "background"): 1.27,
    ("shadcn", "dark", "input", "background"): 1.34,
    ("slate", "light", "input", "background"): 1.00,
    ("slate", "dark", "input", "background"): 1.05,
    ("solarized", "light", "input", "background"): 1.48,
    ("solarized", "dark", "link", "background"): 2.20,
    ("solarized", "dark", "link", "card"): 1.98,
    ("solarized", "dark", "input", "background"): 1.54,
    ("solarpunk", "light", "input", "background"): 1.49,
    ("solarpunk", "dark", "input", "background"): 1.61,
    ("stripe", "light", "link", "background"): 4.44,
    ("stripe", "light", "input", "background"): 1.19,
    ("stripe", "dark", "input", "background"): 1.60,
    ("sunrise", "light", "link", "background"): 3.73,
    ("sunrise", "light", "link", "card"): 3.89,
    ("sunrise", "light", "input", "background"): 1.19,
    ("sunrise", "dark", "input", "background"): 1.29,
    ("supabase", "light", "link", "background"): 1.99,
    ("supabase", "light", "link", "card"): 1.99,
    ("supabase", "light", "input", "background"): 1.17,
    ("supabase", "dark", "input", "background"): 1.32,
    ("swiss", "light", "input", "background"): 1.32,
    ("swiss", "dark", "input", "background"): 1.55,
    ("synthwave", "light", "link", "background"): 2.24,
    ("synthwave", "light", "link", "card"): 2.05,
    ("synthwave", "light", "input", "background"): 1.04,
    ("synthwave", "dark", "input", "background"): 1.16,
    ("tailwind", "light", "input", "background"): 1.19,
    ("tailwind", "dark", "input", "background"): 1.55,
    ("terminal", "light", "input", "background"): 1.09,
    ("terminal", "dark", "input", "background"): 1.10,
    ("tokyo_night", "light", "link", "background"): 2.32,
    ("tokyo_night", "light", "link", "card"): 2.16,
    ("tokyo_night", "light", "input", "background"): 1.00,
    ("tokyo_night", "dark", "input", "background"): 1.16,
    ("vercel", "light", "input", "background"): 1.19,
    ("vercel", "dark", "input", "background"): 1.66,
}

A11Y_EXEMPTIONS.update(
    {
        key: f"grandfathered at the #3281/#3165 matrix extension (2026-09, #2885 owns remediation); ratio {ratio:.2f}"
        for key, ratio in _PAIR_DEBT_2885.items()
    }
)


def get_exemption_reason(preset: str, mode: str, fg_token: str, bg_token: str) -> str | None:
    """Return the documented exemption reason for this pair, or ``None`` if not exempt."""
    return A11Y_EXEMPTIONS.get((preset, mode, fg_token, bg_token))
