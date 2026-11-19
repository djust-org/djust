"""The shared demo UI components escape every value (#4187).

Each component renders with ``format_html``: a hostile value in any text
argument comes out escaped. A value documented as HTML (``Card.content``,
``Section.content``, ``Button.icon_svg``, ``Button.extra_attrs``) is markup
only when the caller marks it safe.
"""

import pytest
from django.utils.html import format_html

from djust_shared.components.ui import (
    BackButton,
    Button,
    Card,
    CodeBlock,
    FeatureCard,
    FeatureGrid,
    HeroSection,
    Section,
)

#: Breaks out of an attribute and opens two elements when not escaped.
HOSTILE = '"><script>alert(1)</script><x a="'


def _assert_escaped(html):
    assert "<script>" not in html, html
    assert "<x " not in html, html
    assert "&lt;script&gt;" in html, html


@pytest.mark.parametrize(
    "component",
    [
        CodeBlock(code="x", language=HOSTILE, filename=HOSTILE),
        CodeBlock(code=HOSTILE),
        HeroSection(title=HOSTILE, subtitle=HOSTILE, icon=HOSTILE, padding=HOSTILE),
        Card(content=HOSTILE, title=HOSTILE, subtitle=HOSTILE, header_color=HOSTILE),
        FeatureCard(icon=HOSTILE, title=HOSTILE, description=HOSTILE),
        FeatureGrid(features=[FeatureCard(icon="i", title=HOSTILE, description="d")], columns=HOSTILE),
        Button(text=HOSTILE, event=HOSTILE, variant=HOSTILE, icon_svg=HOSTILE, extra_attrs=HOSTILE),
        Section(content=HOSTILE, title=HOSTILE, subtitle=HOSTILE, padding=HOSTILE, max_width=HOSTILE),
        BackButton(href=HOSTILE, text=HOSTILE),
    ],
    ids=lambda c: type(c).__name__,
)
def test_a_hostile_value_is_escaped(component):
    _assert_escaped(component.render())


def test_markup_marked_safe_is_kept():
    assert "<p>hi</p>" in Card(content=format_html("<p>{}</p>", "hi")).render()
    assert "<path " in Button(text="Go", event="go", icon="plus").render()
    grid = FeatureGrid(features=[FeatureCard(icon="i", title="t", description="d")]).render()
    assert '<div class="text-2xl mb-2">i</div>' in grid
