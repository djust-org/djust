"""Tests for #2057: enforce design-system style vocabularies at construction.

PR #2056's review found ``card_hover="glow"`` — compiled, tested green, and
visually appeared to work (via a *different* field's — ``AnimationStyle.
hover_effect`` — parallel "glow" branch) while being a silent no-op on its
own seam: ``pack_css_generator.py``'s ``card_hover`` if/elif chain has no
"glow" branch, so it falls through to an empty rule. ``_types.py`` documents
per-field vocabularies (e.g. ``card_hover``: "lift"/"scale"/"border"/
"shadow"/"none"; icon ``weight``: "thin"/"regular"/"bold") that nothing
enforced.

This suite pins the fix (frozensets + ``__post_init__`` validation added to
``python/djust/theming/_types.py``):

(a) an out-of-vocabulary value raises ``ValueError`` naming the field, the
    bad value, and the full valid set — one case per validated field.
(b) all 68 built-in themes (``python/djust/theming/themes/*.py``) AND the
    legacy ``theme_packs.py`` module-level presets (which is where the
    dogfood pass actually caught a second real instance of this bug class —
    ``SurfaceStyle(surface_treatment="textured")`` on the registered
    ``DESIGN_RETRO`` design system) construct clean under the new
    validation.
(c) the ``card_hover`` and ``button_hover`` vocab frozensets are pinned
    against ``pack_css_generator.py``'s actual dispatch branches by parsing
    its source — not just documented — so future drift between the
    generator and the vocab (#1859: "would this go red if the thing it
    pins actually drifted?") is caught mechanically.

Gate-off (#1468): each bad-value test in (a) was manually verified to go
red when its corresponding ``_check_vocab`` call was temporarily commented
out in ``_types.py`` (and to pass again once restored) — see the PR
description for the transcript. The (a) tests are inherently non-tautological
by construction: they assert a ``ValueError`` IS raised for a value that is
not a member of the vocab, which can only pass if the validation code
actually runs and rejects it.
"""

import importlib
import inspect
import pkgutil
import re

import pytest

from djust.theming import _types, pack_css_generator
from djust.theming._types import ColorScale, ThemeTokens

pytestmark = pytest.mark.theming


def _make_theme_tokens() -> ThemeTokens:
    """Build a minimally-valid ThemeTokens (every ColorScale field the same
    value) without hand-listing all 32 fields — keeps this file resilient to
    ThemeTokens growing new fields."""
    swatch = ColorScale(0, 0, 50)
    return ThemeTokens(**{f: swatch for f in ThemeTokens.__dataclass_fields__})


_TOKENS = _make_theme_tokens()


# ---------------------------------------------------------------------------
# (a) Bad-value rejection — one case per validated (dataclass, field) pair.
# ---------------------------------------------------------------------------

# Each entry: (test id, kwargs factory, field name to break, expected valid set)
BAD_VALUE_CASES = [
    (
        "SurfaceTreatment.style",
        lambda bad: _types.SurfaceTreatment(style=bad),
        "style",
        _types.VALID_SURFACE_TREATMENT_STYLES,
    ),
    (
        "ThemePreset.default_mode",
        lambda bad: _types.ThemePreset(
            name="x", display_name="X", light=_TOKENS, dark=_TOKENS, default_mode=bad
        ),
        "default_mode",
        _types.VALID_THEME_PRESET_DEFAULT_MODES,
    ),
    (
        "LayoutStyle.button_shape",
        lambda bad: _types.LayoutStyle(name="x", button_shape=bad),
        "button_shape",
        _types.VALID_LAYOUT_SHAPES,
    ),
    (
        "LayoutStyle.card_shape",
        lambda bad: _types.LayoutStyle(name="x", card_shape=bad),
        "card_shape",
        _types.VALID_LAYOUT_SHAPES,
    ),
    (
        "LayoutStyle.input_shape",
        lambda bad: _types.LayoutStyle(name="x", input_shape=bad),
        "input_shape",
        _types.VALID_LAYOUT_SHAPES,
    ),
    (
        "SurfaceStyle.border_style",
        lambda bad: _types.SurfaceStyle(name="x", border_style=bad),
        "border_style",
        _types.VALID_SURFACE_BORDER_STYLES,
    ),
    (
        "SurfaceStyle.surface_treatment",
        lambda bad: _types.SurfaceStyle(name="x", surface_treatment=bad),
        "surface_treatment",
        _types.VALID_SURFACE_TREATMENTS,
    ),
    (
        "IconStyle.style",
        lambda bad: _types.IconStyle(name="x", style=bad, weight="regular"),
        "style",
        _types.VALID_ICON_STYLES,
    ),
    (
        "IconStyle.weight",
        lambda bad: _types.IconStyle(name="x", style="outlined", weight=bad),
        "weight",
        _types.VALID_ICON_WEIGHTS,
    ),
    (
        "AnimationStyle.entrance_effect",
        lambda bad: _types.AnimationStyle(name="x", entrance_effect=bad),
        "entrance_effect",
        _types.VALID_ANIMATION_ENTRANCE_EFFECTS,
    ),
    (
        "AnimationStyle.exit_effect",
        lambda bad: _types.AnimationStyle(name="x", exit_effect=bad),
        "exit_effect",
        _types.VALID_ANIMATION_EXIT_EFFECTS,
    ),
    (
        "AnimationStyle.hover_effect",
        lambda bad: _types.AnimationStyle(name="x", hover_effect=bad),
        "hover_effect",
        _types.VALID_ANIMATION_HOVER_EFFECTS,
    ),
    (
        "AnimationStyle.click_effect",
        lambda bad: _types.AnimationStyle(name="x", click_effect=bad),
        "click_effect",
        _types.VALID_ANIMATION_CLICK_EFFECTS,
    ),
    (
        "AnimationStyle.loading_style",
        lambda bad: _types.AnimationStyle(name="x", loading_style=bad),
        "loading_style",
        _types.VALID_ANIMATION_LOADING_STYLES,
    ),
    (
        "AnimationStyle.transition_style",
        lambda bad: _types.AnimationStyle(name="x", transition_style=bad),
        "transition_style",
        _types.VALID_ANIMATION_TRANSITION_STYLES,
    ),
    (
        "InteractionStyle.button_hover",
        lambda bad: _types.InteractionStyle(name="x", button_hover=bad),
        "button_hover",
        _types.VALID_INTERACTION_BUTTON_HOVERS,
    ),
    (
        "InteractionStyle.link_hover",
        lambda bad: _types.InteractionStyle(name="x", link_hover=bad),
        "link_hover",
        _types.VALID_INTERACTION_LINK_HOVERS,
    ),
    (
        # The exact #2056 bug: card_hover="glow" must now raise.
        "InteractionStyle.card_hover",
        lambda bad: _types.InteractionStyle(name="x", card_hover=bad),
        "card_hover",
        _types.VALID_INTERACTION_CARD_HOVERS,
    ),
    (
        "InteractionStyle.button_click",
        lambda bad: _types.InteractionStyle(name="x", button_click=bad),
        "button_click",
        _types.VALID_INTERACTION_BUTTON_CLICKS,
    ),
    (
        "InteractionStyle.focus_style",
        lambda bad: _types.InteractionStyle(name="x", focus_style=bad),
        "focus_style",
        _types.VALID_INTERACTION_FOCUS_STYLES,
    ),
    (
        "InteractionStyle.cursor_style",
        lambda bad: _types.InteractionStyle(name="x", cursor_style=bad),
        "cursor_style",
        _types.VALID_INTERACTION_CURSOR_STYLES,
    ),
    (
        "PatternStyle.background_pattern",
        lambda bad: _types.PatternStyle(name="x", background_pattern=bad),
        "background_pattern",
        _types.VALID_PATTERN_BACKGROUND_PATTERNS,
    ),
    (
        "PatternStyle.surface_style",
        lambda bad: _types.PatternStyle(name="x", surface_style=bad),
        "surface_style",
        _types.VALID_PATTERN_SURFACE_STYLES,
    ),
    (
        "IllustrationStyle.image_filter",
        lambda bad: _types.IllustrationStyle(name="x", image_filter=bad),
        "image_filter",
        _types.VALID_ILLUSTRATION_IMAGE_FILTERS,
    ),
]


@pytest.mark.parametrize(
    "case_id,build,field_name,valid_set",
    BAD_VALUE_CASES,
    ids=[c[0] for c in BAD_VALUE_CASES],
)
def test_bad_value_raises_with_field_and_valid_set(case_id, build, field_name, valid_set):
    bad_value = "__not_a_real_vocab_value__"
    with pytest.raises(ValueError) as exc_info:
        build(bad_value)

    message = str(exc_info.value)
    assert field_name in message, f"{case_id}: error message missing field name {field_name!r}"
    assert bad_value in message, f"{case_id}: error message missing the bad value {bad_value!r}"
    for valid_value in valid_set:
        assert valid_value in message, (
            f"{case_id}: error message missing valid value {valid_value!r} from {valid_set!r}"
        )


def test_bad_value_cases_cover_every_check_vocab_call_site():
    """Structural pin: BAD_VALUE_CASES must have one entry per _check_vocab
    call in _types.py, so a future field validation added without a test
    is caught (mirrors #1125's count-canary pattern)."""
    source = inspect.getsource(_types)
    # Exclude the `def _check_vocab(` definition line itself — only count
    # actual call sites.
    call_count = len(re.findall(r"(?<!def )\b_check_vocab\(", source))
    assert call_count == len(BAD_VALUE_CASES), (
        f"_types.py has {call_count} _check_vocab(...) call sites but "
        f"BAD_VALUE_CASES only covers {len(BAD_VALUE_CASES)} — add a case for "
        "the new field (or remove one if a call site was deleted)."
    )


def test_valid_values_are_accepted_without_error():
    """Every value in each frozenset must construct cleanly — a sanity
    check that the valid sets and the constructors actually agree."""
    for _case_id, build, _field_name, valid_set in BAD_VALUE_CASES:
        for value in valid_set:
            build(value)  # must not raise


# ---------------------------------------------------------------------------
# (b) Dogfood gate (#1060): every built-in theme + the legacy theme_packs.py
# module-level presets must construct clean under the new validation.
# ---------------------------------------------------------------------------


def test_all_builtin_theme_modules_reimport_clean():
    """Force every python/djust/theming/themes/*.py module to *re-execute*
    (via importlib.reload) so its top-level IconStyle/AnimationStyle/
    InteractionStyle/LayoutStyle/SurfaceStyle/... construction re-runs
    __post_init__ validation — a plain `import_module` would silently hit
    sys.modules and prove nothing, since Django app startup already
    imported every theme once before this test runs."""
    from djust.theming import themes as themes_pkg

    mod_names = sorted(
        m.name for m in pkgutil.iter_modules(themes_pkg.__path__) if not m.name.startswith("_")
    )
    assert len(mod_names) >= 60, (
        f"expected ~68 built-in theme modules, discovered only {len(mod_names)} — "
        "theme discovery may be broken, not just this test"
    )

    failures = []
    for name in mod_names:
        mod = importlib.import_module(f"djust.theming.themes.{name}")
        try:
            importlib.reload(mod)
        except ValueError as exc:
            failures.append((name, str(exc)))

    assert not failures, (
        f"style-vocab validation rejected real built-in theme values in "
        f"{len(failures)} module(s): {failures}"
    )


def test_theme_packs_legacy_module_reimports_clean():
    """theme_packs.py's own module-level SURFACE_*/ICON_*/ANIMATION_*/
    DESIGN_* constants (distinct from both _constants.py and themes/*.py)
    are a THIRD construction site the dogfood pass found a real violation
    in: DESIGN_RETRO's SurfaceStyle(surface_treatment="textured") — a real,
    registered, shipped design system, not a typo. Fixed by adding
    "textured" to VALID_SURFACE_TREATMENTS rather than changing the theme.
    Reload forces re-execution past the sys.modules cache."""
    import djust.theming.theme_packs as theme_packs

    importlib.reload(theme_packs)
    theme_packs._ensure_theme_imports()


def test_registered_legacy_presets_and_registry_modules_import_clean():
    """Other modules that construct ThemePreset(...) directly (default_mode
    is the only validated field there): high_contrast, registry examples,
    palette-derived presets, shadcn. Reload each to force re-execution."""
    import djust.theming.high_contrast as high_contrast
    import djust.theming.palette as palette
    import djust.theming.registry as registry
    import djust.theming.shadcn as shadcn

    for mod in (high_contrast, registry, palette, shadcn):
        importlib.reload(mod)


# ---------------------------------------------------------------------------
# (c) Generator/vocab parity pin (#1859): a decorative pin would compare a
# frozenset to itself. This one parses pack_css_generator.py's actual
# dispatch source, so it goes red if either side drifts from the other.
# ---------------------------------------------------------------------------


def test_card_hover_vocab_matches_generator_dispatch_branches():
    source = inspect.getsource(pack_css_generator)
    consumed = set(re.findall(r'interact\.card_hover == "(\w+)"', source))
    assert consumed, (
        "regex found zero card_hover branches in pack_css_generator.py — "
        "the dispatch shape changed; update this test's regex before trusting it"
    )
    # "none" has no dedicated branch: it's the implicit empty-string
    # fallthrough when no elif matches, which is the correct behaviour for
    # card_hover="none" (not a bug — unlike card_hover="glow", #2056).
    expected = _types.VALID_INTERACTION_CARD_HOVERS - {"none"}
    assert consumed == expected, (
        f"card_hover generator branches {sorted(consumed)} have drifted from "
        f"the vocab {sorted(expected)} (excluding the implicit 'none' "
        "fallthrough) — update VALID_INTERACTION_CARD_HOVERS or the "
        "generator dispatch to match."
    )


def test_button_hover_vocab_matches_generator_dispatch_branches():
    source = inspect.getsource(pack_css_generator)
    consumed = set(re.findall(r'interact\.button_hover == "(\w+)"', source))
    assert consumed, (
        "regex found zero button_hover branches in pack_css_generator.py — "
        "the dispatch shape changed; update this test's regex before trusting it"
    )
    expected = _types.VALID_INTERACTION_BUTTON_HOVERS - {"none"}
    assert consumed == expected, (
        f"button_hover generator branches {sorted(consumed)} have drifted "
        f"from the vocab {sorted(expected)} (excluding the implicit 'none' "
        "fallthrough) — update VALID_INTERACTION_BUTTON_HOVERS or the "
        "generator dispatch to match. (This is the class of drift #2057 "
        'exists to catch: 9 built-in themes shipped button_hover="color", '
        "undocumented and unconsumed here, before this PR fixed them to "
        '"darken".)'
    )


def test_regex_parity_pin_is_non_tautological():
    """Gate-off proof for the parity pins above, expressed structurally
    instead of by mutating source at runtime: the regex must actually
    distinguish a drifted set from the real one. Construct a deliberately
    wrong "vocab" (missing "darken") and confirm the comparison DOES fail,
    proving the assertion in the tests above is load-bearing and not a
    self-comparison (#1859's 'would this go red if it drifted?' check)."""
    source = inspect.getsource(pack_css_generator)
    consumed = set(re.findall(r'interact\.button_hover == "(\w+)"', source))
    drifted_vocab = _types.VALID_INTERACTION_BUTTON_HOVERS - {"none", "darken"}
    assert consumed != drifted_vocab, (
        "the parity pin failed to distinguish a deliberately-drifted vocab "
        "from the generator's real branches — it would never go red"
    )
