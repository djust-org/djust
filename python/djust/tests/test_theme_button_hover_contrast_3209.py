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

import functools
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

# -- A small selector matcher, so element rules compete too (#3238 review) ----
#
# The element is ``<TAG class="btn">`` (a ``<button type="button">`` or an
# ``<a href>``), enabled, inside ``<html><body>``, hovered or not. A selector
# is a descendant chain of compounds; the last compound must match the
# element and every earlier one must match an ancestor (``html``/``body``/
# ``:root``). Anything the matcher does not understand (``:focus``,
# ``::before``, ``>``) does not match, which only ever hides a rule.

_TOKEN = re.compile(
    r"\*|[a-zA-Z][\w-]*|\.[\w-]+|\[[^\]]*\]|::?[\w-]+(?:\((?:[^()]|\([^()]*\))*\))?"
)


def _split_list(text: str) -> list:
    """Split a selector list on top-level commas."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


def _compound(text: str, element: dict):
    """``(matches, (a, b, c) specificity)`` of one compound, or None if unparsable."""
    tokens = _TOKEN.findall(text)
    if "".join(tokens) != text:
        return None
    spec = [0, 0, 0]
    ok = True
    for token in tokens:
        if token == "*":
            continue
        if token.startswith("."):
            spec[1] += 1
            ok &= token[1:] in element["classes"]
        elif token.startswith("["):
            spec[1] += 1
            m = re.fullmatch(r"\[\s*([\w-]+)\s*(?:=\s*['\"]?([^'\"\]]*)['\"]?)?\s*\]", token)
            ok &= bool(m) and (
                m.group(1) in element["attrs"]
                and (m.group(2) is None or element["attrs"][m.group(1)] == m.group(2))
            )
        elif token.startswith("::"):
            return None
        elif token.startswith(":"):
            name, _, arg = token[1:].partition("(")
            arg = arg[:-1]
            if name == "hover":
                spec[1] += 1
                ok &= element["hover"]
            elif name in ("where", "is", "not"):
                results = [_compound(part, element) for part in _split_list(arg)]
                if any(r is None for r in results):
                    return None
                hit = any(r[0] for r in results)
                ok &= (not hit) if name == "not" else hit
                if name != "where":
                    best = max(r[1] for r in results)
                    spec = [x + y for x, y in zip(spec, best)]
            elif name == "disabled":
                spec[1] += 1
                ok &= False
            elif name == "root":
                spec[1] += 1
                ok &= element["tag"] == "html"
            else:
                return None
        else:
            spec[2] += 1
            ok &= token.lower() == element["tag"]
    return ok, tuple(spec)


def _match(selector: str, element: dict):
    """Specificity of ``selector`` if it matches ``element``, else None."""
    compounds, depth, current = [], 0, ""
    for ch in selector:
        depth += ch == "("
        depth -= ch == ")"
        if ch.isspace() and depth == 0:
            if current:
                compounds.append(current)
            current = ""
        else:
            current += ch
    if current:
        compounds.append(current)
    last = _compound(compounds[-1], element)
    if last is None or not last[0]:
        return None
    spec = list(last[1])
    for ancestor in compounds[:-1]:
        hit = next(
            (
                r
                for tag in ("html", "body")
                for r in [_compound(ancestor, {**element, "tag": tag, "classes": set()})]
                if r is not None and r[0]
            ),
            None,
        )
        if hit is None:
            return None
        spec = [x + y for x, y in zip(spec, hit[1])]
    return tuple(spec)


_PROPERTY_ALIASES = {
    "background-color": "background",
    "text-decoration-line": "text-decoration",
}


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
            for selector in _split_list(prelude):
                out.append((layer, " ".join(selector.split()), decls))
        i = end
    return out, order


@functools.lru_cache(maxsize=None)
def _parsed(css: str) -> tuple:
    return _cascade_rules(css)


def _cascade(sheets: list, tag: str = "button", hovered: bool = True) -> dict:
    """The winning declaration of each property on ``<tag class="btn">``.

    Values are ``(value, sheet index, layer)``. Precedence as CSS orders it:
    ``!important`` first (and then earlier layers win, unlayered last);
    otherwise unlayered beats every layer and later layers beat earlier
    ones; then specificity; then source order.
    """
    element = {
        "tag": tag,
        "classes": {"btn"},
        "hover": hovered,
        "attrs": {"class": "btn", **({"type": "button"} if tag == "button" else {"href": "#"})},
    }
    rules = []  # (sheet index, layer, selector, decls)
    layers: list = []
    for index, css in enumerate(sheets):
        found, order = _parsed(css)
        layers.extend(name for name in order if name not in layers)
        rules.extend((index, layer, selector, decls) for layer, selector, decls in found)
    winners: dict = {}
    for position, (index, layer, selector, decls) in enumerate(rules):
        spec = _match(selector, element)
        if spec is None:
            continue
        rank = len(layers) if layer is None else layers.index(layer)
        for prop, value in decls.items():
            important = value.endswith("!important")
            value = value[: -len("!important")].strip() if important else value
            prop = _PROPERTY_ALIASES.get(prop, prop)
            key = (important, -rank if important else rank, spec, position)
            if prop not in winners or key >= winners[prop][0]:
                winners[prop] = (key, (value, index, layer))
    return {prop: won[1] for prop, won in winners.items()}


def _cascaded_plain_btn_hover(sheets: list, hovered: bool = True, tag: str = "button") -> dict:
    """``color`` and ``background`` of a plain ``.btn``, by the cascade."""
    won = _cascade(sheets, tag=tag, hovered=hovered)
    color = won.get("color", ("inherit",))[0]
    background = won.get("background", ("transparent",))[0]
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


@functools.lru_cache(maxsize=None)
def _theme_css(name: str) -> str:
    return CompleteThemeCSSGenerator(name).generate_css()


def _themed_page_sheets(name: str) -> list:
    return [
        _theme_css(name),
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
    theme_css = CompleteThemeCSSGenerator(name)._generate_component_styles()
    sheets = _themed_page_sheets(name)
    for hovered in (False, True):
        own = {
            prop: value
            for prop, (value, _, _) in _cascade(
                ["@layer components { %s }" % theme_css], hovered=hovered
            ).items()
        }
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


# -- #3238 review: layering the paint must not hand over the box model ---------------
#
# The first fix moved the whole ``.btn`` rule into ``@layer components``. In
# headless Chrome that underlined and link-coloured every hovered
# ``<a class="btn">`` (the theme's layered ``a:hover``), swapped the radius for
# the theming package's unlayered ``.btn`` in 63 design systems, and zeroed the
# padding under Tailwind's unlayered preflight. The cases below model those
# competitors. Excerpts of the two resets the review rendered:
_RESETS = {
    "none": "",
    "tailwind-preflight": (
        "a{color:inherit;text-decoration:inherit}"
        "button,input,optgroup,select,textarea{font-family:inherit;font-size:100%;"
        "font-weight:inherit;line-height:inherit;color:inherit;margin:0;padding:0}"
        "button,[type='button'],[type='reset'],[type='submit']{-webkit-appearance:button;"
        "background-color:transparent;background-image:none}"
    ),
    "site-reset": "a{color:rgb(0,0,238);text-decoration:underline} button{border-radius:0;padding:1px 6px}",
}
_BOX_MODEL = ("padding", "border-radius", "font-size", "font-weight", "text-decoration", "display")


@pytest.mark.parametrize("name", DESIGN_SYSTEMS)
def test_btn_box_model_stays_with_components_css(name):
    """padding, radius, font and underline of a ``.btn`` are components.css's,
    under every reset, for ``<button>`` and ``<a>``, at rest and hovered."""
    components = _COMPONENTS_CSS.read_text(encoding="utf-8")
    expected = {
        prop: _cascade([components], tag="button", hovered=False)[prop][0] for prop in _BOX_MODEL
    }
    assert expected["text-decoration"] == "none" and "var(" in expected["padding"]
    for reset_name, reset in _RESETS.items():
        sheets = [*_themed_page_sheets(name), reset]
        for tag in ("button", "a"):
            for hovered in (False, True):
                won = _cascade(sheets, tag=tag, hovered=hovered)
                got = {prop: won[prop][0] for prop in _BOX_MODEL}
                assert got == expected, (name, reset_name, tag, hovered)


@pytest.mark.parametrize("name", DESIGN_SYSTEMS)
def test_hovered_link_button_is_not_link_coloured(name):
    """The theme's ``a:hover`` colour must not reach a hovered ``<a class="btn">``;
    where the theme repaints a hovered ``.btn`` (the inverted designs), the
    anchor gets that colour like a ``<button>`` does."""
    sheets = _themed_page_sheets(name)
    anchor = _cascaded_plain_btn_hover(sheets, tag="a")
    assert "link" not in anchor["color"], (name, anchor)
    theme_hover = _cascade(["@layer components { %s }" % _theme_css(name)], tag="a").get("color")
    if theme_hover and "link" not in theme_hover[0]:
        # A .btn:hover colour (not the a:hover one) is the theme's intent.
        assert anchor["color"] == _cascaded_plain_btn_hover(sheets)["color"], (name, anchor)


def test_face_is_scoped_to_form_buttons():
    """``<a class="btn">`` has no UA face, so it keeps a transparent background."""
    sheets = _themed_page_sheets("default")
    assert _cascaded_plain_btn_hover(sheets, hovered=False, tag="a")["background"] == "transparent"
    assert "muted" in _cascaded_plain_btn_hover(sheets, hovered=False)["background"]


def test_the_cascade_model_sees_element_rules():
    """Canary: the #3238 round-1 shape (whole ``.btn`` layered) loses to resets."""
    theme = "@layer components;\n@layer components { a:hover { text-decoration: underline; } }"
    layered = "@layer components { .btn { padding: 8px; text-decoration: none; } }"
    reset = "button { padding: 0 }"
    assert _cascade([theme, layered, reset])["padding"][0] == "0"
    assert _cascade([theme, layered], tag="a")["text-decoration"][0] == "underline"
    unlayered = ".btn { padding: 8px; text-decoration: none; }"
    assert _cascade([theme, unlayered, reset])["padding"][0] == "8px"
    assert _cascade([theme, unlayered], tag="a")["text-decoration"][0] == "none"
