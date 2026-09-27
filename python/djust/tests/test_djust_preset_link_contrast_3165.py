"""The ``djust`` preset's link colours pass WCAG AA as text (#3165).

Light mode's ``link`` was the brand orange fill (28 80% 53%), 2.66:1 on the
page background: ``.link`` / ``.text-link`` render it as body text, so it
needs 4.5:1. The pair is not in ``CONTRAST_PAIRS`` yet — adding it there
fails dozens of legacy presets, whose remediation #2885 owns — so this file
pins the framework's own preset directly. ``primary`` keeps the bright fill:
it carries dark ink labels (#2996), and darkening it would break them.
"""

from __future__ import annotations

import pytest

from djust.theming.accessibility import AccessibilityValidator
from djust.theming.presets import THEME_PRESETS

_validator = AccessibilityValidator()


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("fg", ["link", "link_hover"])
@pytest.mark.parametrize("surface", ["background", "card", "muted"])
def test_link_text_meets_aa_on_every_text_surface(mode, fg, surface):
    tokens = getattr(THEME_PRESETS["djust"], mode)
    ratio = _validator.calculate_contrast_ratio(getattr(tokens, fg), getattr(tokens, surface))
    assert ratio >= 4.5, f"djust/{mode}: {fg} on {surface} = {ratio:.2f} < 4.5"


def test_the_link_keeps_the_brand_hue_and_hover_is_distinct():
    for mode in ("light", "dark"):
        tokens = getattr(THEME_PRESETS["djust"], mode)
        assert tokens.link.h == tokens.primary.h == 28
        assert tokens.link_hover.lightness != tokens.link.lightness
