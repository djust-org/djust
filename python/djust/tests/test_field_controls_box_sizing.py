"""Full-width field controls size their padding and border inside their width.

``.form-input`` and ``.form-textarea`` set ``width: 100%`` plus padding and a
border on the default ``content-box`` model, so without a page-wide
``box-sizing`` reset they overflowed their container (a modal, a card) by the
padding and border. Every full-width field control declares
``box-sizing: border-box``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from djust.theming import _types

pytestmark = pytest.mark.theming

ROOT = Path(_types.__file__).resolve().parents[1]
STYLESHEETS = (
    ROOT / "components/static/djust_components/components.css",
    ROOT / "theming/static/djust_theming/css/components.css",
    ROOT / "auth/static/djust_auth/auth.css",
)

_RULE = re.compile(r"(?m)^([^{}\n@/]+)\{([^}]*)\}")
_FIELD_SELECTOR = re.compile(r"input|select|textarea|\.form-", re.I)
_FULL_WIDTH = re.compile(r"(?<![\w-])width\s*:\s*100%")
# Padding, or a border that draws something (not ``border-radius``/``border: none``).
_GROWS = re.compile(
    r"(?<![\w-])padding[\w-]*\s*:|(?<![\w-])border(?:-(?:top|right|bottom|left|inline|block|width))?\s*:(?!\s*none\b)"
)


def _overflowing(css: str) -> list[str]:
    offenders = []
    for match in _RULE.finditer(css):
        selector, body = match.group(1).strip(), match.group(2)
        if not _FIELD_SELECTOR.search(selector) or not _FULL_WIDTH.search(body):
            continue
        if _GROWS.search(body) and not re.search(r"box-sizing\s*:\s*border-box", body):
            offenders.append(selector)
    return offenders


def test_the_sweep_flags_a_padded_full_width_field():
    """Non-vacuous: the shape that shipped is caught; the safe shapes are not."""
    assert _overflowing(".form-input { width: 100%; padding: 4px; }") == [".form-input"]
    assert _overflowing(".x input { width: 100%; border: 1px solid red; }") == [".x input"]
    assert _overflowing(".form-input { width: 100%; padding: 4px; box-sizing: border-box; }") == []
    assert _overflowing(".a-slider input { width: 100%; border-radius: 9px; border: none; }") == []
    assert _overflowing(".card { width: 100%; padding: 4px; }") == []


@pytest.mark.parametrize(
    "path", STYLESHEETS, ids=lambda p: p.name if p.parent.name != "css" else "theming/" + p.name
)
def test_full_width_field_controls_use_border_box(path: Path):
    assert _overflowing(path.read_text(encoding="utf-8")) == []


# Pinned by name too: ``.data-table-filter`` has no field word in its selector,
# so the sweep above cannot see it.
FULL_WIDTH_FIELDS = (
    ".form-input",
    ".form-select",
    ".form-textarea",
    ".data-table-filter",
    ".data-table-row-editing td input",
    ".data-grid-cell-editing input",
    ".dj-search-input__input",
    ".dj-password-input__input",
    ".rich-select-search-input",
    ".dj-dependent-select__control select",
    ".dj-mentions__input",
    ".dj-prompt-editor__textarea",
)


@pytest.mark.parametrize("selector", FULL_WIDTH_FIELDS)
def test_the_full_width_fields_use_border_box(selector: str):
    css = STYLESHEETS[0].read_text(encoding="utf-8")
    rule = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert rule and "box-sizing: border-box" in rule.group(1), selector
