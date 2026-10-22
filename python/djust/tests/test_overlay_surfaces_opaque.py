"""Floating surfaces are opaque ``--card`` (option lists ``--popover``) by default.

The modal panel (and the sheet, popover, menus, dropdowns, palette, toast and
tooltip) painted ``hsl(var(--card) / 0.95)`` behind a backdrop blur. In light
mode a dialog then read faintly grey next to the white (card) fields inside it.
The blur is a deliberate glass design, so it stays; the surface is opaque unless
a theme sets ``--overlay-surface-opacity``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming import _types

pytestmark = pytest.mark.theming

ROOT = Path(_types.__file__).resolve().parents[1]
COMPONENTS_CSS = ROOT / "components/static/djust_components/components.css"

OVERLAY_SURFACES = (
    ".modal-content, .dj-modal",
    ".sheet",
    ".popover",
    ".dj-popconfirm",
    ".dropdown-menu",
    ".ctx-menu",
    ".palette",
    ".combobox-dropdown",
    ".rich-select-dropdown",
    ".notif-dropdown",
    ".split-btn-menu",
    ".toast",
    ".tooltip, .dj-tooltip__text",
)

# ``background: hsl(var(--card[, fallback]) / <alpha>)``, or an option list's
# ``hsl(var(--popover, var(--card[, fallback])) / <alpha>)``.
_CARD_FILL = re.compile(
    r"(?<![\w-])background\s*:\s*hsl\(var\(--"
    r"(?:popover, var\(--card(?:, [^)]*)?\)|card(?:, [^)]*)?)\)(?:\s*/\s*([^;]+))?\)"
)


def _rule(selector: str) -> str:
    css = COMPONENTS_CSS.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css))
    assert len(matches) == 1, (selector, len(matches))
    return matches[0].group(1)


def test_the_fill_matcher_reads_the_alpha():
    """Non-vacuous: the matcher separates a translucent card from an opaque one."""
    assert _CARD_FILL.search(" background: hsl(var(--card) / 0.95);").group(1) == "0.95"
    assert _CARD_FILL.search(" background: hsl(var(--card, 0 0% 100%) / 0.98);").group(1) == "0.98"
    assert _CARD_FILL.search(" background: hsl(var(--card));").group(1) is None
    popover = " background: hsl(var(--popover, var(--card)) / var(--overlay-surface-opacity, 1));"
    assert _CARD_FILL.search(popover).group(1) == "var(--overlay-surface-opacity, 1)"


@pytest.mark.parametrize("selector", OVERLAY_SURFACES)
def test_overlay_surface_is_opaque_by_default(selector: str):
    fill = _CARD_FILL.search(_rule(selector))
    assert fill, selector
    alpha = fill.group(1)
    assert alpha is not None and alpha.strip() == "var(--overlay-surface-opacity, 1)", (
        selector,
        alpha,
    )


@pytest.mark.parametrize("selector", OVERLAY_SURFACES)
def test_overlay_surface_keeps_its_blur(selector: str):
    """Opaque by default, but the glass blur is still there for a translucent theme."""
    body = _rule(selector)
    if selector.startswith(".tooltip"):
        pytest.skip("the tooltip never had a blur")
    assert "backdrop-filter: blur(" in body, selector


def test_no_floating_surface_is_hardcoded_translucent():
    """Any other ``--card`` fill at a fixed alpha under a blur is a new overlay
    that skipped the variable. ``.carousel-btn`` (a control drawn over an image)
    is translucent on purpose."""
    css = COMPONENTS_CSS.read_text(encoding="utf-8")
    offenders = []
    for match in re.finditer(r"(?m)^([^{}\n]+)\{([^}]*)\}", css):
        selector, body = match.group(1).strip(), match.group(2)
        fill = _CARD_FILL.search(body)
        if not fill or fill.group(1) is None or "backdrop-filter" not in body:
            continue
        if "var(--overlay-surface-opacity" in fill.group(1):
            continue
        offenders.append(selector)
    assert offenders == [".carousel-btn"], offenders
