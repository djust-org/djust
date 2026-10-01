"""The framework's own ``djust`` preset passes every AA pair with no
exemptions (#2996, part 3).

It failed 17 of its 28 ``CONTRAST_PAIRS`` checks, all a white ``*_foreground``
on a bright mid-tone fill. ``a11y_exemptions`` keeps brand palettes as they
are to protect their identity, but djust owns this one: the fills keep their
hue, the labels become dark ink, and the light-mode greens are deepened so
white still reads on them.
"""

from __future__ import annotations

import pytest

from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS, CONTRAST_PAIRS, NEW_PAIR_KEYS
from djust.theming.accessibility import AccessibilityValidator
from djust.theming.presets import THEME_PRESETS

_validator = AccessibilityValidator()

# The 14 ``*_foreground``-on-fill pairs this file gated in #2996. The pairs
# #3281 / #3165 added are checked below, where djust's remaining debt is pinned.
BASE_PAIRS = [p for p in CONTRAST_PAIRS if (p[0], p[1]) not in NEW_PAIR_KEYS]


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,minimum,_label", BASE_PAIRS, ids=[p[3] for p in BASE_PAIRS])
def test_every_pair_meets_its_minimum(mode, fg, bg, minimum, _label):
    tokens = getattr(THEME_PRESETS["djust"], mode)
    ratio = _validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))
    assert ratio >= minimum, f"djust/{mode}: {fg} on {bg} = {ratio:.2f} < {minimum}"


def test_no_label_exemption_is_left_for_the_djust_preset():
    assert not [
        key
        for key in A11Y_EXEMPTIONS
        if key[0] == "djust" and (key[2], key[3]) not in NEW_PAIR_KEYS
    ]


def test_djust_light_primary_text_is_documented_not_recoloured():
    """#3165: ``primary`` as TEXT on the page is 2.65:1 in light mode and stays that
    way. The full set of djust rows is pinned in ``test_theming_contrast_coverage_3281``. It is the brand orange and it carries the dark ink labels of #2996, so
    darkening it to 4.5:1 would change the identity. ``link``, the colour that is
    actually used as body text, passes on every surface (#3165's first half)."""
    for surface in ("background", "card"):
        assert ("djust", "light", "primary", surface) in A11Y_EXEMPTIONS
    for mode in ("light", "dark"):
        tokens = getattr(THEME_PRESETS["djust"], mode)
        for surface in ("background", "card"):
            ratio = _validator.calculate_contrast_ratio(tokens.link, getattr(tokens, surface))
            assert ratio >= 4.5, f"djust/{mode}: link on {surface} = {ratio:.2f}"
    assert ("djust", "dark", "primary", "background") not in A11Y_EXEMPTIONS


def test_the_brand_fills_keep_their_hue():
    """The orange and the Django greens are the identity; only lightness moved."""
    light = THEME_PRESETS["djust"].light
    dark = THEME_PRESETS["djust"].dark
    assert (light.primary.h, dark.primary.h, light.brand.h) == (28, 28, 28)
    assert (light.secondary.h, light.accent.h) == (154, 157)
    assert (dark.primary.lightness, dark.primary.s) == (55, 80)
