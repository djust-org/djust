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

#3230: the same must hold for a plain ``.btn`` (no variant) when
``djust_components/components.css`` is loaded after the theme, as
``{% theme_head %}`` does. That stylesheet's ``.btn`` sets ``color``; while it
sat outside any ``@layer`` it beat the theme's ``@layer components``
``.btn:hover`` colour whatever the specificity, so the hover background
applied and the text did not. The cascade tests below resolve each property
the way CSS does -- layer order, then specificity, then source order -- over
the three stylesheets a themed page loads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import djust

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


# -- #3230: a plain .btn with djust_components/components.css loaded ----------------

_STATIC = Path(djust.__file__).parent
# The stylesheets {% theme_head %} emits, in its order: the generated theme
# (css_block), the theming package's components.css, then djust-components'.
_THEMING_COMPONENTS_CSS = _STATIC / "theming/static/djust_theming/css/components.css"
_COMPONENTS_CSS = _STATIC / "components/static/djust_components/components.css"
# A hovered, enabled ``<button class="btn">``: the selectors that match it.
# ``:where(.btn)`` matches too, with zero specificity.
_PLAIN_BTN_HOVER = re.compile(r"^(?:\.btn|:where\(\.btn\))(?::hover|:not\(:disabled\))*$")
_PLAIN_BTN_REST = re.compile(r"^(?:\.btn|:where\(\.btn\))(?::not\(:disabled\))*$")


def _skip_block(css: str, i: int) -> int:
    """Index just past the ``{...}`` block that opens at ``css[i]``."""
    depth = 0
    for j in range(i, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return j + 1
    return len(css)


def _cascade_rules(css: str, layer=None, out=None, order=None):
    """``(layer, selector, decls)`` for every style rule, plus the declared layer order.

    ``@layer x { ... }`` blocks are descended into with ``layer=x``; other
    block at-rules (``@media``, ``@keyframes``, ``@supports``) are skipped, as
    none of them applies to a hovered button under default media.
    """
    out = [] if out is None else out
    order = [] if order is None else order
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i = 0
    while i < len(css):
        brace = css.find("{", i)
        semi = css.find(";", i)
        if brace == -1:
            break
        prelude = css[i:brace].strip()
        if prelude.startswith("@") and semi != -1 and semi < brace:
            statement = css[i:semi].strip()
            if statement.startswith("@layer"):
                for name in statement[len("@layer") :].split(","):
                    if name.strip() and name.strip() not in order:
                        order.append(name.strip())
            i = semi + 1
            continue
        end = _skip_block(css, brace)
        if prelude.startswith("@layer"):
            name = prelude[len("@layer") :].strip()
            if name not in order:
                order.append(name)
            _cascade_rules(css[brace + 1 : end - 1], name, out, order)
        elif not prelude.startswith("@"):
            decls = {}
            for decl in css[brace + 1 : end - 1].split(";"):
                if ":" in decl:
                    prop, value = decl.split(":", 1)
                    decls[prop.strip()] = value.strip()
            for selector in prelude.split(","):
                out.append((layer, " ".join(selector.split()), decls))
        i = end
    return out, order


def _cascaded_plain_btn_hover(sheets: list, hovered: bool = True) -> dict:
    """``color`` and ``background`` of a (hovered) plain ``.btn``, by the cascade."""
    matches = _PLAIN_BTN_HOVER if hovered else _PLAIN_BTN_REST
    rules, layers = [], []
    for css in sheets:
        _cascade_rules(css, out=rules, order=layers)
    winners = {}
    for position, (layer, selector, decls) in enumerate(rules):
        if not matches.match(selector):
            continue
        # Unlayered styles beat every layer; later layers beat earlier ones.
        rank = len(layers) if layer is None else layers.index(layer)
        specificity = (
            selector.startswith(".btn") + selector.count(":hover") + selector.count(":not(")
        )
        for prop, value in decls.items():
            if prop in ("background", "background-color"):
                prop = "background"
            elif prop != "color":
                continue
            key = (rank, specificity, position)
            if prop not in winners or key >= winners[prop][0]:
                winners[prop] = (key, value)
    color = winners.get("color", (None, "inherit"))[1]
    background = winners.get("background", (None, "transparent"))[1]
    if background.split()[0].lower() == "currentcolor":
        background = color
    return {"color": color, "background": background}


def _relative_luminance(triplet: str) -> float:
    """WCAG relative luminance of an ``H S% L%`` triplet."""
    h, s, lightness = (float(x.rstrip("%").replace("deg", "")) for x in triplet.split())
    s, lightness = s / 100, lightness / 100
    a = s * min(lightness, 1 - lightness)

    def channel(n: float) -> float:
        k = (n + h / 30) % 12
        c = lightness - a * max(-1.0, min(k - 3, 9 - k, 1.0))
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(0) + 0.7152 * channel(8) + 0.0722 * channel(4)


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


DESIGN_SYSTEMS = sorted(get_all_design_systems())


def _themed_page_sheets(name: str) -> list:
    return [
        CompleteThemeCSSGenerator(name).generate_css(),
        _THEMING_COMPONENTS_CSS.read_text(encoding="utf-8"),
        _COMPONENTS_CSS.read_text(encoding="utf-8"),
    ]


def test_some_design_system_repaints_a_plain_button_on_hover():
    """Guards against the parametrization below silently checking nothing."""
    painted = [
        name
        for name in DESIGN_SYSTEMS
        if "color"
        in _rule(CompleteThemeCSSGenerator(name)._generate_component_styles(), ".btn:hover")
    ]
    assert painted


@pytest.mark.parametrize("name", DESIGN_SYSTEMS)
def test_plain_button_hover_is_readable_with_components_css(name):
    """#3230: every design system and preset, both modes, as a themed page loads it."""
    hovered = _cascaded_plain_btn_hover(_themed_page_sheets(name))
    assert hovered["background"] != "transparent", (name, hovered)
    assert hovered["color"] != hovered["background"], (name, hovered)
    for preset in THEME_PRESETS:
        for mode, tokens in zip(("light", "dark"), _modes(preset)):
            color = _resolve(hovered["color"], tokens)
            background = _resolve(hovered["background"], tokens)
            assert color != background, (name, preset, mode, hovered)
            if _HSL_TOKEN.match(hovered["color"]) and _HSL_TOKEN.match(hovered["background"]):
                ratio = _contrast(color, background)
                assert ratio >= 3.0, (name, preset, mode, hovered, round(ratio, 2))


@pytest.mark.parametrize("name", DESIGN_SYSTEMS)
def test_the_theme_still_styles_a_plain_button(name):
    """#3230: the fix must not win by overriding the design system.

    Where the theme's own ``.btn`` / ``.btn:hover`` sets ``color`` or
    ``background``, a plain ``.btn`` on a page that also loads components.css
    shows the theme's value: the inverted hover stays inverted, and a theme's
    transparent outlined button stays transparent.
    """
    theme_rules, _ = _cascade_rules(CompleteThemeCSSGenerator(name)._generate_component_styles())
    sheets = _themed_page_sheets(name)
    for hovered in (False, True):
        matches = _PLAIN_BTN_HOVER if hovered else _PLAIN_BTN_REST
        own = {}
        for _, selector, decls in sorted(
            theme_rules, key=lambda rule: ":hover" in rule[1]
        ):  # the generator emits .btn then .btn:hover; hover is more specific
            if matches.match(selector):
                own.update(decls)
        cascaded = _cascaded_plain_btn_hover(sheets, hovered=hovered)
        if "color" in own:
            assert cascaded["color"] == own["color"], (name, hovered, cascaded)
        own_background = own.get("background", own.get("background-color"))
        if own_background and own_background.lower() != "currentcolor":
            assert cascaded["background"] == own_background, (name, hovered, cascaded)


def test_the_cascade_model_catches_the_unlayered_component_rule():
    """Canary: an unlayered ``.btn`` colour outranks the layered hover colour (#3230)."""
    theme = (
        "@layer base, tokens, components;\n"
        "@layer components { .btn:hover { background: hsl(var(--foreground)); "
        "color: hsl(var(--background)); } }"
    )
    unlayered = ".btn { color: hsl(var(--foreground)); }"
    layered = "@layer components { .btn { color: hsl(var(--foreground)); } }"
    broken = _cascaded_plain_btn_hover([theme, unlayered])
    assert broken["color"] == broken["background"]
    fixed = _cascaded_plain_btn_hover([theme, layered])
    assert fixed["color"] == "hsl(var(--background))"


def test_the_cascade_model_ranks_where_below_a_class():
    """``:where(.btn)`` has zero specificity: a theme's ``.btn`` background beats it."""
    theme = "@layer components;\n@layer components { .btn { background: transparent; } }"
    component = "@layer components { :where(.btn) { background: hsl(var(--muted)); } }"
    assert _cascaded_plain_btn_hover([theme, component])["background"] == "transparent"
    assert _cascaded_plain_btn_hover([component])["background"] == "hsl(var(--muted))"


def test_contrast_ratio_matches_wcag_endpoints():
    assert round(_contrast("0 0% 0%", "0 0% 100%"), 1) == 21.0
    assert _contrast("240 10% 3.9%", "240 10% 3.9%") == 1.0
