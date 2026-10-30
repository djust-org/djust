"""Field controls render in the page font, not the browser's form-control default.

Browsers do not inherit ``font-family`` into ``<input>``, ``<select>``,
``<textarea>`` or ``<button>``: a ``.form-input`` rendered in Arial and a
``.form-textarea`` in monospace next to page text in the theme font. Every
text-bearing djust field control now sets ``font-family: inherit`` and keeps its
own size and line-height. The code-style controls that are meant to be
monospace (the prompt editor, the cron input, the colour hex input, the
markdown editor) keep their monospace stack.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming import _types

pytestmark = pytest.mark.theming

ROOT = Path(_types.__file__).resolve().parents[1]
COMPONENTS_CSS = ROOT / "components/static/djust_components/components.css"
THEME_CSS = ROOT / "theming/static/djust_theming/css/components.css"

# ``font`` / ``font-family`` declarations, in source order.
_FONT_DECL = re.compile(r"(?<![\w-])(font|font-family)\s*:\s*([^;]*)")


def _rule(css_path: Path, selector: str) -> tuple[int, str]:
    css = css_path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css))
    assert len(matches) == 1, (selector, len(matches))
    return matches[0].start(), matches[0].group(1)


def _family(body: str) -> str | None:
    """The ``font-family`` the rule ends up with, or None if it sets none."""
    family = None
    for prop, value in _FONT_DECL.findall(body):
        value = value.strip()
        if prop == "font-family" or value == "inherit":
            family = value
        else:  # the ``font`` shorthand: the family is what follows the size
            family = re.split(r"\d[\w.%]*(?:/[\w.%]+)?\s+", value, maxsplit=1)[-1]
    return family


def test_the_family_reader_reads_the_last_declaration():
    """Non-vacuous: ``_family`` follows the cascade inside one rule."""
    assert _family(" font-size: 1rem; color: red;") is None
    assert _family(" font-family: inherit; font-size: 1rem;") == "inherit"
    assert _family(" font: inherit; font-size: 0.875rem;") == "inherit"
    assert _family(" font-family: inherit; font-family: monospace;") == "monospace"
    assert _family(" font: 0.75rem/1.4 ui-monospace, monospace;") == "ui-monospace, monospace"


# Text-bearing field controls in the component stylesheet. ``.combobox-input`` is
# rendered as ``combobox-input form-input`` and is covered by ``.form-input``;
# ``.dj-mminput__text`` and ``.dj-md-textarea__input`` already set ``font: inherit``.
COMPONENT_FIELD_RULES = (
    ".form-input",
    ".form-select",
    ".form-textarea",
    ".table-search",
    ".data-table-search input",
    ".data-table-filter",
    ".data-table-cell-editing input",
    ".data-table-row-editing td input",
    ".multi-select-search",
    ".otp-digit",
    ".number-stepper-input",
    ".tag-input-field",
    ".dj-search-input__input",
    ".rich-select-trigger",
    ".rich-select-search-input",
    ".dj-dependent-select__control select",
    ".dj-currency-input__field",
    ".dj-filter-bar__select",
    ".dj-mminput__text",
    ".dj-mentions__input",
    ".dj-md-textarea__input",
    ".dj-form-array__input",
    ".dj-import-wizard__field-select",
    ".dj-signature-pad__typed-input",
)

THEME_FIELD_RULES = (".input", ".select", ".textarea")


@pytest.mark.parametrize("selector", COMPONENT_FIELD_RULES)
def test_component_field_controls_inherit_the_page_font(selector: str):
    _, body = _rule(COMPONENTS_CSS, selector)
    assert _family(body) == "inherit", selector


@pytest.mark.parametrize("selector", THEME_FIELD_RULES)
def test_theme_field_classes_inherit_the_page_font(selector: str):
    _, body = _rule(THEME_CSS, selector)
    assert _family(body) == "inherit", selector
    assert "font-size" in body and "line-height" in body, selector


# Controls that are code surfaces, and the rule that makes each one monospace.
MONOSPACE_RULES = (
    ".dj-prompt-editor__textarea",
    ".dj-cron-input__input",
    ".color-hex-input",
    ".dj-md-editor__textarea",
)


@pytest.mark.parametrize("selector", MONOSPACE_RULES)
def test_code_controls_stay_monospace(selector: str):
    _, body = _rule(COMPONENTS_CSS, selector)
    family = _family(body)
    assert family and "monospace" in family, (selector, family)


def test_colour_hex_input_monospace_still_wins_over_form_input():
    """``color-hex-input form-input``: both are one class, so the later rule wins."""
    form_input_at, _ = _rule(COMPONENTS_CSS, ".form-input")
    hex_at, _ = _rule(COMPONENTS_CSS, ".color-hex-input")
    assert hex_at > form_input_at


def test_the_combobox_input_is_a_form_input():
    """The combobox input gets its font from ``.form-input``, so it must carry it."""
    from djust.components.components.combobox import Combobox

    html = Combobox(name="team", options=[{"value": "a", "label": "A"}]).render()
    assert re.search(r'<input class="combobox-input form-input"', str(html))
