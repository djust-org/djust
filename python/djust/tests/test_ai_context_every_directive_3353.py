"""``djust_ai_context`` writes every directive in the schema (#3353).

`_section_directives` iterated a hand-kept category list that left out
`performance`, `animation` and `recovery`, so eleven directives (`dj-prefetch`,
`dj-key`, `dj-lazy`, `dj-virtual`, `dj-track-static`, `dj-transition`,
`dj-remove`, `dj-transition-group`, `dj-view-transitions`, `dj-flip`,
`dj-auto-recover`) were readable through `djust_schema` and the MCP server but
absent from the CLAUDE.md / .cursorrules / Copilot files, the surface that
misled AI assistants in the first place. The category list is now derived from
`DIRECTIVES`; these tests keep it that way.
"""

from __future__ import annotations

import pytest

from djust.management.commands.djust_ai_context import (
    _FULL_CATEGORIES,
    _category_order,
    _generate_content,
    _section_directives,
)
from djust.schema import DIRECTIVES, get_framework_schema

FORMATS = ["claude", "cursor", "copilot"]

#: Directives deliberately left out of the generated files: name -> reason.
#: Empty on purpose; add an entry here, with the reason, rather than dropping a
#: directive silently.
EXCLUDED: dict[str, str] = {}

#: The eleven the issue named; they must stay in categories the old list skipped
#: so this file keeps covering the regression, not just the general rule.
FORMERLY_MISSING = {
    "dj-prefetch": "performance",
    "dj-key": "performance",
    "dj-lazy": "performance",
    "dj-virtual": "performance",
    "dj-track-static": "performance",
    "dj-transition": "animation",
    "dj-remove": "animation",
    "dj-transition-group": "animation",
    "dj-view-transitions": "animation",
    "dj-flip": "animation",
    "dj-auto-recover": "recovery",
}


def _bullet(name: str) -> str:
    return "- **`%s`** = `" % name


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_schema_directive_is_in_the_generated_file(fmt: str) -> None:
    content = _generate_content(get_framework_schema(), {}, fmt)
    missing = [
        d["name"]
        for d in DIRECTIVES
        if d["name"] not in EXCLUDED and _bullet(d["name"]) not in content
    ]
    assert not missing, f"{fmt} context is missing directives from DIRECTIVES: {missing}"


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_directive_is_emitted_once_with_its_description(fmt: str) -> None:
    content = _generate_content(get_framework_schema(), {}, fmt)
    for d in DIRECTIVES:
        if d["name"] in EXCLUDED:
            continue
        assert content.count(_bullet(d["name"])) == 1, d["name"]
        assert " ".join(str(d["description"]).split()) in " ".join(content.split()), d["name"]


def test_exclusions_name_real_directives_and_give_a_reason() -> None:
    names = {d["name"] for d in DIRECTIVES}
    for name, reason in EXCLUDED.items():
        assert name in names, f"{name} is excluded but is not in DIRECTIVES"
        assert reason.strip(), f"{name} is excluded without a reason"


def test_the_eleven_named_in_3353_are_in_the_categories_the_old_list_skipped() -> None:
    by_name = {d["name"]: d.get("category") for d in DIRECTIVES}
    for name, category in FORMERLY_MISSING.items():
        assert by_name.get(name) == category, name
        assert category not in _FULL_CATEGORIES


def test_category_order_is_derived_and_stable() -> None:
    present = {d.get("category", "other") for d in DIRECTIVES}
    order = _category_order(present)
    assert set(order) == present, "a category in DIRECTIVES is not emitted"
    assert len(order) == len(set(order))
    # The full-format categories keep their documented order, first.
    full = [c for c in _FULL_CATEGORIES if c in present]
    assert order[: len(full)] == full
    # Everything else follows, alphabetically, whatever order the set iterates in.
    assert order[len(full) :] == sorted(present - set(_FULL_CATEGORIES))
    assert _category_order(reversed(sorted(present))) == order


def test_a_category_added_to_the_schema_is_emitted_without_editing_the_command() -> None:
    framework = {
        "directives": [
            {
                "name": "dj-brand-new",
                "category": "zz-future",
                "description": "A directive in a category nobody listed.",
                "value": "x",
                "example": '<div dj-brand-new="x">',
            },
            {
                "name": "dj-no-category",
                "description": "Falls back to 'other'.",
                "value": "y",
            },
        ],
    }
    text = _section_directives(framework)
    assert _bullet("dj-brand-new") in text and "### Zz-Future" in text
    assert _bullet("dj-no-category") in text and "### Other" in text
    assert text.index("### Other") < text.index("### Zz-Future")


def test_extra_categories_are_compact_and_full_ones_unchanged() -> None:
    text = _section_directives(get_framework_schema())
    # Full format: a fenced example under the directive.
    assert '  ```html\n  <input dj-input="search" name="query">\n  ```' in text
    # Compact format: one inline example line, no fence.
    assert '  Example: `<a href="/reports/" dj-prefetch>View reports</a>`' in text
    performance = text[text.index("### Performance") : text.index("### Recovery")]
    assert "```" not in performance


def test_compact_format_falls_back_to_a_fence_for_an_example_that_cannot_be_inline() -> None:
    framework = {
        "directives": [
            {
                "name": "dj-odd",
                "category": "zz",
                "description": "d",
                "value": "v",
                "example": "<div>\n  `tick`\n</div>",
            }
        ]
    }
    text = _section_directives(framework)
    assert "```html" in text and "Example:" not in text


def test_the_extra_categories_stay_a_small_share_of_the_generated_file() -> None:
    """The file is already long; a few dozen lines is the budget for these categories."""
    full = _generate_content(get_framework_schema(), {}, "claude")
    only_full = [d for d in DIRECTIVES if d.get("category") in _FULL_CATEGORIES]
    base = _generate_content({**get_framework_schema(), "directives": only_full}, {}, "claude")
    added = len(full.splitlines()) - len(base.splitlines())
    assert 0 < added <= 60, f"the extra categories added {added} lines"
