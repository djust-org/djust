"""Buttons and other clickable controls render in the page font.

Browsers do not inherit ``font-family`` into ``<button>``, and no djust button
rule set one, so every djust button rendered in the browser default (Arial):
the modal close ×, primary and secondary actions, tab triggers, pagination,
menu items rendered as buttons, split buttons, toggle groups. #3591 fixed the
text fields; this does the same for buttons.

The definition of "button-like" is mechanical: a rule that declares
``cursor: pointer``. Every such rule in the shipped stylesheets sets a font
family (``inherit``, or an intentional one), so the sweep below fails on a new
clickable rule that forgets it. Controls with no text (checkboxes, radios, the
range input, colour swatches, carousel dots) and pseudo-element rules are the
named exemptions. Monospace surfaces stay monospace: their buttons inherit
from the monospace container.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.tests.test_field_controls_inherit_font import _family, _rule

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_CSS = ROOT / "components/static/djust_components/components.css"
COMPONENT_CLASSES_CSS = ROOT / "components/static/djust_components/components-classes.css"
THEME_CSS = ROOT / "theming/static/djust_theming/css/components.css"
SCAFFOLD_CSS = ROOT / "theming/static/djust_theming/css/scaffold.css"
AUTH_CSS = ROOT / "auth/static/djust_auth/auth.css"

SWEPT = (COMPONENTS_CSS, COMPONENT_CLASSES_CSS, THEME_CSS, SCAFFOLD_CSS, AUTH_CSS)

# Clickable rules with no text to render, and a modifier whose base rule carries the font.
EXEMPT = frozenset(
    {
        ".form-checkbox",
        ".form-radio",
        ".checkbox",
        ".radio",
        ".form-check-input",
        ".data-table-checkbox",
        ".data-table-select-all",
        ".dj-slider__input",
        ".dropzone-input",
        ".color-swatch",
        ".carousel-dot",
        ".wizard-step.completed",
    }
)

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_POINTER = re.compile(r"cursor\s*:\s*pointer")


def _pointer_rules(css_path: Path) -> list[tuple[str, str]]:
    """Every innermost rule that declares ``cursor: pointer``, as (selector, body)."""
    css = re.sub(r"/\*.*?\*/", "", css_path.read_text(encoding="utf-8"), flags=re.S)
    return [
        (" ".join(m.group(1).split()), m.group(2))
        for m in _RULE.finditer(css)
        if _POINTER.search(m.group(2))
    ]


def test_the_sweep_sees_the_clickable_rules():
    """Non-vacuous: the parser finds the rules it claims to check."""
    found = {sel for path in SWEPT for sel, _ in _pointer_rules(path)}
    assert len(found) > 150, len(found)
    assert ".btn" in found and ".dj-modal__close" in found and ".dj-btn" in found
    assert EXEMPT <= found, EXEMPT - found  # an exemption that no longer exists must go


@pytest.mark.parametrize("css_path", SWEPT, ids=lambda p: p.name)
def test_every_clickable_rule_sets_a_font_family(css_path: Path):
    missing = [
        sel
        for sel, body in _pointer_rules(css_path)
        if "::" not in sel and sel not in EXEMPT and _family(body) is None
    ]
    assert not missing, f"{css_path.name}: add `font-family: inherit` to {missing}"


# The buttons the Point Guard port found in the browser default font, by stylesheet.
NAMED = (
    (COMPONENTS_CSS, ".btn"),
    (COMPONENTS_CSS, ".dj-modal__close"),
    (COMPONENTS_CSS, ".tabs-trigger, .dj-tab"),
    (COMPONENTS_CSS, ".accordion-trigger, .dj-accordion__trigger"),
    (COMPONENTS_CSS, ".dropdown-trigger"),
    (COMPONENTS_CSS, ".dropdown-item"),
    (COMPONENTS_CSS, ".dj-dropdown-menu__trigger"),
    (COMPONENTS_CSS, ".pagination-btn, .dj-pagination__btn, .dj-table__page-btn"),
    (COMPONENTS_CSS, ".toggle-group-btn"),
    (COMPONENTS_CSS, ".split-btn-primary"),
    (COMPONENTS_CSS, ".split-btn-toggle"),
    (COMPONENTS_CSS, ".split-btn-option"),
    (COMPONENTS_CSS, ".ctx-item"),
    (COMPONENTS_CSS, ".combobox-option"),
    (COMPONENTS_CSS, ".rich-select-option"),
    (COMPONENTS_CSS, ".multi-select-option"),
    (COMPONENTS_CSS, ".toast-close"),
    (COMPONENTS_CSS, ".sheet-close"),
    (COMPONENTS_CSS, ".alert-close"),
    (COMPONENTS_CSS, ".dj-confirm-dialog__btn"),
    (COMPONENTS_CSS, ".dj-popconfirm__btn"),
    (COMPONENTS_CSS, ".data-table-row-actions button"),
    (COMPONENTS_CSS, ".number-stepper-btn"),
    (COMPONENT_CLASSES_CSS, ".dj-btn"),
    (THEME_CSS, ".btn"),
    (THEME_CSS, ".modal-close"),
    (THEME_CSS, ".tab"),
    (THEME_CSS, ".dropdown-trigger"),
    (THEME_CSS, ".dropdown-item"),
    (THEME_CSS, ".theme-mode-btn"),
    (SCAFFOLD_CSS, ".btn"),
    (SCAFFOLD_CSS, ".modal-close"),
)


def _base_rule(css_path: Path, selector: str) -> str:
    """The first rule for ``selector``: its layout rule. Later ones only recolour it."""
    if css_path is COMPONENTS_CSS and selector in _COLOUR_RULES:
        css = css_path.read_text(encoding="utf-8")
        return re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
    return _rule(css_path, selector)[1]


# Selectors that components.css styles twice: a layout rule, then a colour rule.
_COLOUR_RULES = frozenset({".btn", ".split-btn-primary", ".split-btn-toggle"})


@pytest.mark.parametrize(("css_path", "selector"), NAMED, ids=lambda v: getattr(v, "name", v))
def test_named_buttons_inherit_the_page_font(css_path: Path, selector: str):
    body = _base_rule(css_path, selector)
    assert _family(body) == "inherit", (css_path.name, selector)


def test_the_button_rule_keeps_its_own_weight():
    """``inherit`` replaces only the family: ``.btn`` keeps its font-weight."""
    _, body = _rule(THEME_CSS, ".btn")
    assert "font-weight: 500" in body


# CSS djust generates in Python.
def _generated_body(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert len(matches) == 1, (selector, len(matches))
    return matches[0]


def test_theme_css_generator_buttons_inherit():
    from djust.theming.css_generator import ThemeCSSGenerator

    css = ThemeCSSGenerator("default")._generate_utilities()
    for selector in (".btn-primary", ".btn-secondary", ".btn-destructive"):
        assert _family(_generated_body(css, selector)) == "inherit", selector


def test_tailwind_apply_examples_buttons_inherit():
    from djust.theming.tailwind import generate_tailwind_apply_examples

    css = generate_tailwind_apply_examples()
    for selector in (".btn-primary", ".btn-secondary", ".btn-destructive"):
        assert _family(_generated_body(css, selector)) == "inherit", selector


def test_design_system_buttons_inherit():
    from djust.theming.design_system_css import generate_design_system_css

    css = generate_design_system_css("material", "default")
    assert _family(_generated_body(css, ".btn, button")) == "inherit"


def test_interactive_utility_inherits():
    from djust.theming.design_tokens import get_interactive_utilities

    assert _family(_generated_body(get_interactive_utilities(), ".interactive")) == "inherit"


def test_theme_pack_buttons_inherit():
    from djust.theming.pack_css_generator import ThemePackCSSGenerator

    css = ThemePackCSSGenerator("corporate").generate_css()
    assert _family(_generated_body(css, "button, a, .clickable")) == "inherit"


def test_theme_switcher_controls_inherit():
    from djust.theming.context_processors import _render_theme_switcher
    from djust.theming.manager import ThemeState

    state = ThemeState(theme="material", preset="default", mode="light", resolved_mode="light")
    presets = [{"name": "default", "display_name": "Default", "is_active": True}]
    html = _render_theme_switcher(state, presets)
    for selector in (".theme-mode-btn", ".theme-preset-select"):
        assert _family(_generated_body(html, selector)) == "inherit", selector
