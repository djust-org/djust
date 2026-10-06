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
moves, which removed their rows. The owner then approved the polarity flips and
the large moves of the label and link tokens (2026-10-06): a label that failed
on its fill flips between light and dark ink; no fill token moves (``muted_foreground`` also paints dots and tracks, which follow it). Two groups
are exempt BY DECISION, not as debt (owner, 2026-10-06): the 131 ``input`` border rows
(non-text UI components, 3:1 not applied to the shipped presets, revisit on request:
``INPUT_BORDER_EXEMPTION_REASON``) and the 6 rows of tokens whose source documents an
exact brand hex that the owner keeps (monokai and stripe ``link``, github and stripe
``muted_foreground``: ``BRAND_HEX_EXEMPTIONS``). The other 17 label rows are
``accent_foreground``, which ``.status-badge-accent`` paints as a background; that
one is still undecided (``--proposals --include-substantial`` lists it).
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
        "aurora",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.32",
    (
        "aurora",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.52",
    (
        "catppuccin",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.77",
    (
        "cyberpunk",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.77",
    (
        "cyberpunk",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.77",
    (
        "docs",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.89",
    (
        "dracula",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 4.48",
    (
        "ember",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.46",
    (
        "gruvbox",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.38",
    (
        "nord",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.22",
    (
        "ocean_deep",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.74",
    (
        "outrun",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.08",
    (
        "outrun",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 3.08",
    (
        "rose_pine",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.65",
    (
        "solarized",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.90",
    (
        "solarized",
        "dark",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 2.90",
    (
        "tokyo_night",
        "light",
        "accent_foreground",
        "accent",
    ): "grandfathered at gate introduction (2026-07, #2060); ratio 1.91",
    # --- Bulk grandfathering: W001 matrix reconciliation (2026-09, #2874) ---
    # The W001 matrix checks 13 token pairs; pairs outside the original
    # #2060 six had never been gated, so their legacy failures surface
    # here as documented debt (134 of these are catastrophic <3.0 —
    # tracked in the #2874 follow-up for branded-palette remediation).
    (
        "github",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 4.46",
    (
        "stripe",
        "light",
        "muted_foreground",
        "muted",
    ): "grandfathered at W001 matrix reconciliation (2026-09, #2874); ratio 3.81",
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
    ("gruvbox", "light", "input", "background"): 1.57,
    ("gruvbox", "dark", "input", "background"): 1.56,
    ("handcraft", "light", "input", "background"): 1.09,
    ("handcraft", "dark", "input", "background"): 1.38,
    ("ink", "light", "input", "background"): 1.05,
    ("ink", "dark", "input", "background"): 1.13,
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
    ("nord", "dark", "input", "background"): 1.26,
    ("notion", "light", "input", "background"): 1.16,
    ("notion", "dark", "input", "background"): 1.46,
    ("obsidian", "light", "input", "background"): 1.47,
    ("obsidian", "dark", "input", "background"): 1.32,
    ("ocean_deep", "light", "input", "background"): 1.49,
    ("ocean_deep", "dark", "input", "background"): 1.43,
    ("one_dark", "light", "input", "background"): 1.15,
    ("one_dark", "dark", "input", "background"): 1.43,
    ("orange", "light", "input", "background"): 1.31,
    ("orange", "dark", "input", "background"): 1.25,
    ("outrun", "light", "input", "background"): 1.04,
    ("outrun", "dark", "input", "background"): 1.03,
    ("paper", "light", "input", "background"): 1.07,
    ("paper", "dark", "input", "background"): 1.25,
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
    ("rose_pine", "light", "input", "background"): 1.00,
    ("rose_pine", "dark", "input", "background"): 1.12,
    ("sakura", "light", "input", "background"): 1.34,
    ("sakura", "dark", "input", "background"): 1.49,
    ("shadcn", "light", "input", "background"): 1.27,
    ("shadcn", "dark", "input", "background"): 1.34,
    ("slate", "light", "input", "background"): 1.00,
    ("slate", "dark", "input", "background"): 1.05,
    ("solarized", "light", "input", "background"): 1.48,
    ("solarized", "dark", "input", "background"): 1.54,
    ("solarpunk", "light", "input", "background"): 1.49,
    ("solarpunk", "dark", "input", "background"): 1.61,
    ("stripe", "light", "link", "background"): 4.44,
    ("stripe", "light", "input", "background"): 1.19,
    ("stripe", "dark", "input", "background"): 1.60,
    ("sunrise", "light", "input", "background"): 1.19,
    ("sunrise", "dark", "input", "background"): 1.29,
    ("supabase", "light", "input", "background"): 1.17,
    ("supabase", "dark", "input", "background"): 1.32,
    ("swiss", "light", "input", "background"): 1.32,
    ("swiss", "dark", "input", "background"): 1.55,
    ("synthwave", "light", "input", "background"): 1.04,
    ("synthwave", "dark", "input", "background"): 1.16,
    ("tailwind", "light", "input", "background"): 1.19,
    ("tailwind", "dark", "input", "background"): 1.55,
    ("terminal", "light", "input", "background"): 1.09,
    ("terminal", "dark", "input", "background"): 1.10,
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

# Two groups the owner decided to EXEMPT deliberately (John, 2026-10-06, #2885). They are
# not debt waiting for a fix: they are documented decisions, each with its reason, and
# they stay in the exact ratchet pin.
#
# 1. The ``input`` border on the background (WCAG 1.4.11, 3:1). An input border is a
#    non-text UI component and the shipped palettes keep it subtle on purpose, so the 3:1
#    is not applied to the shipped presets. User-authored presets are still measured by
#    W001. Revisit on request.
INPUT_BORDER_EXEMPTION_REASON = (
    "input borders are non-text UI components; 3:1 not applied to the shipped presets "
    "(owner decision 2026-10-06, #2885); revisit on request; ratio {ratio:.2f}"
)
A11Y_EXEMPTIONS.update(
    {
        key: INPUT_BORDER_EXEMPTION_REASON.format(ratio=ratio)
        for key, ratio in _PAIR_DEBT_2885.items()
        if (key[2], key[3]) == ("input", "background")
    }
)

# 2. Tokens whose source documents an exact brand hex. The hex is the palette's identity,
#    so the owner keeps it and the pair stays below 4.5:1: key -> the documented hex.
BRAND_HEX_EXEMPTIONS: dict[tuple[str, str, str, str], str] = {
    ("github", "light", "muted_foreground", "muted"): "#656D76",
    ("monokai", "dark", "link", "card"): "#ae81ff",
    ("monokai", "light", "link", "background"): "#ae81ff",
    ("monokai", "light", "link", "card"): "#ae81ff",
    ("stripe", "light", "link", "background"): "#635BFF",
    ("stripe", "light", "muted_foreground", "muted"): "#697386",
}
A11Y_EXEMPTIONS.update(
    {
        key: (
            f"keeps its documented brand hex {hex_}, which is the palette's identity "
            "(owner decision 2026-10-06, #2885); exempt, not debt"
        )
        for key, hex_ in BRAND_HEX_EXEMPTIONS.items()
    }
)


def get_exemption_reason(preset: str, mode: str, fg_token: str, bg_token: str) -> str | None:
    """Return the documented exemption reason for this pair, or ``None`` if not exempt."""
    return A11Y_EXEMPTIONS.get((preset, mode, fg_token, bg_token))
