"""
Regression tests for issue #397: .flex-between utility CSS class.

These tests are marked xfail because the fix (adding .flex-between to
utilities.css) has not yet been applied on this branch.  Once the fix
is ported from main, remove the xfail markers.
"""

import re
from pathlib import Path

import pytest

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]  # .../djust/


@pytest.mark.xfail(reason="issue #397: .flex-between not yet added to utilities.css on this branch", strict=True)
def test_flex_between_utility_defined_in_utilities_css():
    """utilities.css must define a .flex-between class."""
    css_path = _WORKTREE_ROOT / "examples/demo_project/demo_app/static/css/utilities.css"
    content = css_path.read_text()
    assert ".flex-between" in content, "utilities.css must define .flex-between"


@pytest.mark.xfail(reason="issue #397: .flex-between not yet added to utilities.css on this branch", strict=True)
def test_flex_between_sets_required_properties():
    """The .flex-between rule must include display:flex, flex-direction:row,
    and justify-content:space-between."""
    css_path = _WORKTREE_ROOT / "examples/demo_project/demo_app/static/css/utilities.css"
    content = css_path.read_text()

    match = re.search(r"\.flex-between\s*\{([^}]+)\}", content, re.DOTALL)
    assert match, ".flex-between rule block not found in utilities.css"

    rule = match.group(1)

    assert "display" in rule and "flex" in rule, ".flex-between must set display: flex"
    assert "flex-direction" in rule and "row" in rule, ".flex-between must set flex-direction: row"
    assert (
        "justify-content" in rule and "space-between" in rule
    ), ".flex-between must set justify-content: space-between"
