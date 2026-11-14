"""A string reaches a native component handler as the string it is (#3883), measured against
Django's own tag through both engines.

The engine handed a native handler a resolved string as TEXT, and ``_parse_args`` read the text as
template source: ``"false"`` became ``False``, ``"007"`` became ``7``, ``"1e3"`` became ``1000.0``,
``'"x"'`` lost its quotes, and a word naming another variable became that variable (#3882). The
table is the issue's own (``{% badge label=v %}``), plus an event slot and a block tag.
"""

from __future__ import annotations

import pytest

from djust.tests.test_component_arg_handling_3870_3871_3872 import texts
from djust.tests.test_component_dj_attr_parity_3669 import _django, dj_attrs
from djust.tests.test_components_csp_aria import _rust

VALUES = [
    '"x"',
    "'x'",
    "false",
    "False",
    "True",
    "None",
    "null",
    "007",
    "1e3",
    "1_000",
    "'0'",
    "-0",
    "nan",
    "[1, 2]",
    '{"a": 1}',
    "name",  # the name of another context variable (#3882)
]

SOURCES = [
    "{% badge label=v %}",
    '{% dj_button label="L" event=v %}',
    "{% nav_menu toggle_event=v %}{% endnav_menu %}",
    "{% nav_menu %}{% nav_item label=v event=v %}{% endnav_item %}{% endnav_menu %}",
    "{% card title=v %}b{% endcard %}",
]


def _seen(markup: str) -> tuple[object, list[str]]:
    """The ``dj-*`` attributes and the non-blank text of a rendering (the two engines lay out
    whitespace differently; that is not what this measures)."""
    return dj_attrs(markup), sorted(t.strip() for t in texts(markup) if t.strip())


@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("src", SOURCES)
def test_a_resolved_string_renders_as_django_renders_it(src: str, value: str) -> None:
    ctx = {"v": value, "name": "n"}
    py, rs = _django(src, ctx), _rust(src, ctx)
    assert _seen(py) == _seen(rs), "%s with v=%r\n  python: %s\n  rust:   %s" % (src, value, py, rs)


def test_the_badge_label_keeps_the_text_of_each_shape_in_the_issue() -> None:
    """Absolute, so the parity grid cannot pass with both engines wrong."""
    for value in ('"x"', "false", "007", "1e3", "1_000", "'0'"):
        html = _rust("{% badge label=v %}", {"v": value})
        assert value in texts(html), (value, html)


@pytest.mark.parametrize("value", ["O'Brien", '"x"', "<b>", "&amp"])
def test_avatar_initials_are_cut_from_the_text_not_its_escaping(value: str) -> None:
    """The avatar's initials were sliced from the ESCAPED text, cutting an entity in half
    (``O'Brien`` -> ``O&``). A string keeping its quotes (#3883) reached that more often."""
    html = _rust("{% avatar alt=v %}", {"v": value})
    initials = [t for t in texts(html) if t.strip()]
    assert initials == [value[:2].upper()], (value, html)
