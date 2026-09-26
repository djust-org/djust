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

from djust.theming.a11y_exemptions import A11Y_EXEMPTIONS, CONTRAST_PAIRS
from djust.theming.accessibility import AccessibilityValidator
from djust.theming.presets import THEME_PRESETS

_validator = AccessibilityValidator()


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,minimum,_label", CONTRAST_PAIRS, ids=[p[3] for p in CONTRAST_PAIRS])
def test_every_pair_meets_its_minimum(mode, fg, bg, minimum, _label):
    tokens = getattr(THEME_PRESETS["djust"], mode)
    ratio = _validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, bg))
    assert ratio >= minimum, f"djust/{mode}: {fg} on {bg} = {ratio:.2f} < {minimum}"


def test_no_exemption_is_left_for_the_djust_preset():
    assert not [key for key in A11Y_EXEMPTIONS if key[0] == "djust"]


def test_the_brand_fills_keep_their_hue():
    """The orange and the Django greens are the identity; only lightness moved."""
    light = THEME_PRESETS["djust"].light
    dark = THEME_PRESETS["djust"].dark
    assert (light.primary.h, dark.primary.h, light.brand.h) == (28, 28, 28)
    assert (light.secondary.h, light.accent.h) == (154, 157)
    assert (dark.primary.lightness, dark.primary.s) == (55, 80)
