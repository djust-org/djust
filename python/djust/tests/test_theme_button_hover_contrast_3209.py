"""A hovered theme button must not paint its text in its own background (#3209 review).

#3209 wrapped the outlined button's hover ``color`` in ``hsl()`` so it stopped
being dropped. The rule's ``background: currentColor`` then resolved to that
new colour -- the page background -- and text, fill and border all became the
page colour: the button vanished on hover (checked in headless Chromium). The
syntax scan in ``test_theming_hsl_token_usage_3209`` cannot see that, because
the rule is valid CSS. This test computes what the hovered button paints.

For every design system and colour preset, it takes the generated ``.btn`` and
``.btn:hover`` rules, resolves ``currentColor`` the way CSS does (to the
element's computed ``color``) and ``hsl(var(--token))`` to the preset's light
and dark triplets, and fails if the hovered text colour equals the hovered
background.
"""

from __future__ import annotations

import re

import pytest

from djust.theming.css_generator import ThemeCSSGenerator
from djust.theming.presets import THEME_PRESETS
from djust.theming.theme_css_generator import CompleteThemeCSSGenerator
from djust.theming.theme_packs import get_all_design_systems

pytestmark = pytest.mark.theming

_TRIPLET = re.compile(r"--([\w-]+):\s*(-?[\d.]+(?:deg)?\s+[\d.]+%\s+[\d.]+%)\s*;")
_HSL_TOKEN = re.compile(r"^hsl\(var\(--([\w-]+)\)\)$")


def _rule(css: str, selector: str) -> dict:
    """The declarations of the first rule whose selector is exactly ``selector``."""
    match = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    if not match:
        return {}
    decls = {}
    for decl in match.group(1).split(";"):
        if ":" in decl:
            prop, value = decl.split(":", 1)
            decls[prop.strip()] = value.strip()
    return decls


def _hovered(css: str) -> dict:
    """``color`` and ``background`` of a hovered ``.btn`` (hover over base)."""
    decls = {**_rule(css, ".btn"), **_rule(css, ".btn:hover")}
    color = decls.get("color", "inherit")
    background = decls.get("background", decls.get("background-color", "transparent"))
    if background.lower() == "currentcolor":
        background = color  # CSS: currentColor is the element's computed color
    return {"color": color, "background": background}


def _modes(preset: str) -> list:
    """``{token: triplet}`` for the preset's light and dark modes."""
    generator = ThemeCSSGenerator(preset)
    return [
        dict(_TRIPLET.findall(generator._generate_light_mode())),
        dict(_TRIPLET.findall(generator._generate_dark_mode())),
    ]


def _resolve(value: str, tokens: dict) -> str:
    match = _HSL_TOKEN.match(value)
    return tokens[match.group(1)] if match else value


def _cases():
    for name in sorted(get_all_design_systems()):
        css = CompleteThemeCSSGenerator(name)._generate_component_styles()
        if _rule(css, ".btn:hover"):
            yield name, css


CASES = list(_cases())


def test_some_design_system_emits_a_hover_rule_that_sets_a_colour():
    """Guards against the parametrization silently covering nothing."""
    assert any("color" in _rule(css, ".btn:hover") for _, css in CASES)


@pytest.mark.parametrize("name,css", CASES, ids=[name for name, _ in CASES])
def test_hovered_button_text_differs_from_its_background(name, css):
    hovered = _hovered(css)
    if hovered["color"] == "inherit" or hovered["background"] == "transparent":
        return  # nothing painted over the text's own surroundings
    assert hovered["color"] != hovered["background"], (name, hovered)
    for preset in THEME_PRESETS:
        for tokens in _modes(preset):
            color = _resolve(hovered["color"], tokens)
            background = _resolve(hovered["background"], tokens)
            assert color != background, (name, preset, hovered)


def test_the_check_catches_the_vanishing_button():
    """Canary: the rule #3209 first shipped resolves to text == background."""
    css = ".btn { border: 2px solid currentColor; background: transparent; }\n.btn:hover { background: currentColor; color: hsl(var(--background)); }"
    hovered = _hovered(css)
    assert hovered["color"] == hovered["background"]
