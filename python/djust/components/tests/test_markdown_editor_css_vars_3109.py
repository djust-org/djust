"""MarkdownEditor's documented custom properties are the ones the CSS reads (#3109).

The docstring listed ``--dj-md-editor-min-height`` but not
``--dj-md-editor-height``, the variable that actually sizes the editing
surface, so setting the documented name left the editor at 20rem. Both are
read — by different files, for different boxes — and both are now listed.
The sets are derived from the files, in both directions.
"""

from __future__ import annotations

import re
from pathlib import Path

from djust.components.components.markdown_editor import MarkdownEditor

STATIC = Path(__file__).resolve().parents[1] / "static" / "djust_components"
GUIDE = Path(__file__).resolve().parents[4] / "docs" / "website" / "guides" / "markdown-editor.md"
_VAR = re.compile(r"var\((--dj-md-editor-[a-z-]+)")


def _read_by_css() -> set[str]:
    names: set[str] = set()
    for name in ("components.css", "markdown-editor.css"):
        names.update(_VAR.findall((STATIC / name).read_text()))
    return names


def _documented() -> set[str]:
    return set(re.findall(r"(--dj-md-editor-[a-z-]+):", MarkdownEditor.__doc__ or ""))


def test_every_documented_property_is_read_by_a_shipped_stylesheet():
    assert _documented() - _read_by_css() == set()


def test_every_property_the_stylesheets_read_is_documented():
    assert _read_by_css() - _documented() == set()


def test_the_height_property_is_documented_as_a_fixed_size():
    doc = MarkdownEditor.__doc__ or ""
    entry = doc[doc.index("--dj-md-editor-height:") :]
    assert "fixed size, not a minimum" in entry.split("--dj-md-editor-", 2)[1]
    guide = GUIDE.read_text()
    assert "`--dj-md-editor-min-height`" in guide
    assert "fixed size, not a minimum" in guide
