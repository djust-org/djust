"""The ``hidden`` attribute hides themed components (#3290).

``.progress-wrapper`` sets ``display: flex``. An author ``display`` outranks the
user-agent ``[hidden] { display: none }``, so toggling ``hidden`` on a progress
bar, for example to show it only during an upload, did nothing. Every component
class that sets a ``display`` other than ``none`` has the same problem, so the
fix is one guard in ``components.css`` (the stylesheet ``{% theme_head %}``
always links) rather than a ``:not([hidden])`` on each class.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.theming

COMPONENTS_CSS = (
    Path(__file__).resolve().parents[1]
    / "theming"
    / "static"
    / "djust_theming"
    / "css"
    / "components.css"
)

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _rules() -> list[tuple[str, str]]:
    css = _COMMENT.sub("", COMPONENTS_CSS.read_text(encoding="utf-8"))
    return [(" ".join(m.group(1).split()), m.group(2)) for m in _RULE.finditer(css)]


def _displays_something(body: str) -> bool:
    return any(
        value.strip().lower() not in {"none", "contents"}
        for value in re.findall(r"(?<![\w-])display\s*:\s*([^;!]+)", body)
    )


def test_a_class_that_sets_display_exists():
    # The reproduction in the issue: this is what an attribute alone cannot beat.
    flex_classes = [s for s, b in _rules() if s == ".progress-wrapper" and "flex" in b]
    assert flex_classes


def test_hidden_attribute_guard_beats_every_display_rule():
    guards = [
        body
        for selector, body in _rules()
        if selector.startswith("[hidden]") and "until-found" in selector
    ]
    assert guards, "components.css has no [hidden] guard"
    assert re.search(r"display\s*:\s*none\s*!important", guards[0])


def test_every_display_setting_class_is_covered_by_the_one_guard():
    # Document the blast radius: these all lose to `hidden` without the guard.
    affected = sorted(
        {
            selector
            for selector, body in _rules()
            if _displays_something(body) and not selector.startswith(("@", "[hidden]"))
        }
    )
    assert ".progress-wrapper" in affected
    assert len(affected) > 20
