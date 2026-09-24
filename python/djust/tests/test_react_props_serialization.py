"""React component props survive the ``data-react-props`` attribute intact.

The attribute is single-quoted, so every writer must entity-escape the JSON
it puts there, and every reader must decode it again. A prop value holding
quotes, apostrophes, angle brackets or backslashes has to come back unchanged.
"""

import html
import json
import re

import pytest

from djust._rust import render_template
from djust.mixins.post_processing import PostProcessingMixin
from djust.react import ReactComponentRegistry, react_components

AWKWARD = 'it\'s "quoted" <b>&amp;</b> back\\slash\ttab\nline\x01'

ATTR = re.compile(r"data-react-props='([^']*)'")


def _props(markup: str) -> dict:
    match = ATTR.search(markup)
    assert match, markup
    return json.loads(html.unescape(match.group(1)))


def _attribute_is_closed(markup: str) -> None:
    """The props attribute is one attribute: nothing leaks past its quotes."""
    match = ATTR.search(markup)
    assert match, markup
    raw = match.group(1)
    assert "'" not in raw and "<" not in raw and ">" not in raw
    assert markup[match.end() :].startswith(">"), markup


@pytest.mark.parametrize(
    "template",
    ['<div><Greeting who="{{ name }}" /></div>', '<div><Greeting who="name" /></div>'],
)
def test_renderer_props_round_trip_from_context(template):
    out = render_template(template, {"name": AWKWARD})
    _attribute_is_closed(out)
    assert _props(out) == {"who": AWKWARD}


def test_renderer_literal_props_round_trip():
    out = render_template('<Greeting who="it\'s" />', {})
    _attribute_is_closed(out)
    assert _props(out) == {"who": "it's"}


class _View(PostProcessingMixin):
    def __init__(self, context):
        self._context = context

    def get_context_data(self):
        return self._context


@pytest.fixture
def registered_greeting():
    react_components.register("Greeting")(lambda props, children: "<span>hi</span>")
    yield
    react_components._components.pop("Greeting", None)


def test_hydration_keeps_resolved_values_literal(registered_greeting):
    # A value the renderer resolved is data; if it reads like a template
    # expression, it stays that text and is not looked up again.
    rendered = render_template('<div><Greeting who="{{ name }}" /></div>', {"name": "{{ secret }}"})
    out = _View({"secret": "s3cret"})._hydrate_react_components(rendered)
    _attribute_is_closed(out)
    assert _props(out) == {"who": "{{ secret }}"}
    assert "s3cret" not in out
    assert "<span>hi</span>" in out


def test_hydration_reads_renderer_escaped_props(registered_greeting):
    rendered = render_template('<div><Greeting who="{{ name }}" /></div>', {"name": AWKWARD})
    out = _View({})._hydrate_react_components(rendered)
    _attribute_is_closed(out)
    assert _props(out) == {"who": AWKWARD}


def test_registry_render_round_trips_props():
    registry = ReactComponentRegistry()
    out = registry.render("Greeting", {"who": AWKWARD + " &quot;"}, "")
    match = re.search(r'data-react-props="([^"]*)"', out)
    assert match, out
    assert json.loads(html.unescape(match.group(1))) == {"who": AWKWARD + " &quot;"}
