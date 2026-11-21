"""A character reference split between a URL literal and its slot is judged as the browser reads
it (#3923).

The browser decodes an attribute value as it is printed: the literal text, then the slot's
escaped text. Escaping touches only ``& < > " '``, so a literal that ends in an unfinished
character reference (``&``, ``&#10``, ``&#x6``, ``javascript&``) is completed by the slot's text
(``6;avascript:...``, ``colon;...``). The hybrid path decoded each literal on its own and judged
the slot's text raw, so the joined value was never seen.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser

import pytest

from djust.components import _hybrid_attrs as hybrid
from djust.components.base import Component
from djust.components.utils import url_allowed

pytestmark = pytest.mark.components


@pytest.fixture(autouse=True)
def _bootstrap(settings):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}


def _draw(template: str, **context) -> str:
    class Box(Component):
        pass

    Box.template = template
    Box.get_context_data = lambda self: dict(context)  # type: ignore[method-assign]
    return str(Box().render())


def _href(markup: str) -> str | None:
    found: dict[str, str | None] = {}

    class P(HTMLParser):
        def handle_starttag(self, t, attrs):
            if t == "a" and not found:
                found.update(attrs)

    P(convert_charrefs=True).feed(markup)
    return found.get("href")


#: ``(template, slot value)``: each printed a value the browser reads as ``javascript:``.
SPLIT = [
    ('<a href="&#10{{ s }}">', "6;avascript:alert(1)"),
    ('<a href="&{{ s }}">', "#106;avascript:alert(1)"),
    ('<a href="&#x6{{ s }}">', "A;avascript:alert(1)"),
    ('<a href="javascript&{{ s }}">', "colon;alert(1)"),
    ('<a href="java&#11{{ s }}">', "5;cript:alert(1)"),
]


@pytest.mark.parametrize("marked", [False, True], ids=["plain", "mark_safe"])
@pytest.mark.parametrize(("template", "value"), SPLIT)
def test_a_reference_the_slot_completes_is_judged(template, value, marked):
    from django.utils.safestring import mark_safe

    out = _draw(template, s=mark_safe(value) if marked else value)
    href = (_href(out) or "").translate(hybrid._DROP_WS)
    assert not href.strip().lower().startswith(("javascript:", "vbscript:", "data:")), out
    assert url_allowed(href), out


@pytest.mark.parametrize("template", ['<a href="&{{ s }}">', '<a href="javascript&{{ s }}">'])
def test_a_literal_ending_in_an_open_reference_does_not_fix_the_value(template):
    assert len(hybrid.plan(template).groups) == 1


def test_a_good_value_after_an_open_reference_is_kept():
    """Guard (passes before and after): no over-refusal of a value the browser reads as fine."""
    assert 'href="&#10/path"' in _draw('<a href="&#10{{ s }}">', s="/path")
    # a query string's ``&`` after a literal that already fixed the value stays fixed
    assert hybrid.plan('<a href="?a=1&{{ q }}">').groups == ()


def test_an_unjudged_slot_after_an_open_reference_is_listed(caplog):
    template = '<a href="javascript&{{ l.url }}">'
    assert [u[:2] for u in hybrid.plan(template).unchecked_urls] == [("a", "href")]

    class Listed(Component):
        pass

    Listed.template = template
    Listed.get_context_data = lambda self: {"l": {"url": "/x"}}  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="djust.components._hybrid_attrs"):
        Listed().render()
    assert any("Listed" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("template", "value", "read"),
    [
        ('<a href="&#10{{ s }}">', "4;x", "hx"),
        ('<a href="&#x6{{ s }}">', "8;elp", "help"),
        ('<a href="java&#11{{ s }}">', "5;x", "javasx"),
    ],
)
def test_the_judged_value_matches_what_the_browser_reads(template, value, read):
    """The value judged is the printed value decoded: literal and escaped slot text joined, then
    character references decoded (an allowed value prints unchanged)."""
    out = _draw(template, s=value)
    assert _href(out) == read
    (group,) = hybrid.plan(template).groups
    ((key, _name),) = group.slots
    for path in group.paths:
        assert not hybrid._refused(group, path, {key: value})
