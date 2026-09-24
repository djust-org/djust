"""``{% badge %}`` ships no CSS, and says so (#3025, 1.2.1 part).

The tag renders BEM classes (``dj-badge--error``, ``dj-badge__dot``, …) that no
stylesheet djust ships styles. Shipping styles changes how existing pages look,
so it is 1.3; 1.2.1 documents it. These tests keep the documentation true: when
a shipped stylesheet gains a rule for one of the classes, they fail, and the
docstring and the guide section must change with it.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.template import engines

DJUST_ROOT = Path(__file__).resolve().parents[2]
GUIDE = DJUST_ROOT.parents[1] / "docs" / "website" / "guides" / "components.md"


def _badge_classes() -> set:
    html = (
        engines["django"]
        .from_string(
            '{% load djust_components %}{% badge label="Failed" status="error" pulse=True %}'
        )
        .render({})
    )
    classes = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        classes.update(attr.split())
    return classes - {"sr-only"}


def test_the_badge_classes_are_the_documented_ones():
    assert _badge_classes() == {
        "dj-badge",
        "dj-badge--error",
        "dj-badge__dot",
        "dj-badge__dot--pulse",
        "dj-badge__label",
    }


def test_no_shipped_stylesheet_styles_them():
    css = "\n".join(
        p.read_text(errors="ignore")
        for p in DJUST_ROOT.rglob("*.css")
        if "node_modules" not in p.parts
    )
    hits = re.findall(r"\.dj-badge(?:--|__)[\w-]*", css)
    assert not hits, f"shipped CSS now styles {sorted(set(hits))}: update the docs (#3025)"


def test_docstring_and_guide_say_so():
    from djust.components.templatetags.djust_components import badge

    doc = badge.__doc__ or ""
    assert "Styling is yours" in doc and "theme_badge" in doc
    guide = GUIDE.read_text()
    assert "`{% badge %}` ships no CSS" in guide
    assert "dj-badge__dot--pulse" in guide
