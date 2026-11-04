"""An attribute NAME is written into a tag as given; it must be a legal name (#3774).

``build_tag`` escapes every attribute VALUE, but a name was interpolated raw::

    >>> live_input("text", handler="h", event="dj-input onfocus=alert(1) autofocus y")
    <input ... dj-input onfocus=alert(1) autofocus y="h" />

``event=`` is a template ARGUMENT (``{% live_input "text" handler="h" event=var %}``), so a name
can come from data. Escaping cannot repair a name (the payload has nothing to escape), so the one
chokepoint every tag builder shares, ``djust._html.attr_name``, checks it. This was found while
auditing ``live_input``, whose ``mark_safe(html)`` the taint guard flags for a different reason.

Refs #3774, #3766, #650.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template

from djust._html import attr_name, build_tag
from djust.frameworks import PlainAdapter
from djust.templatetags.live_tags import live_input

HOSTILE_NAMES = [
    "dj-input onfocus=alert(1) autofocus y",  # the reproducer: a space ends the name
    "dj-input autofocus",  # ASCII space only: no quote, no "=", nothing but \s separates the words
    "dj-input ",  # a trailing space
    "dj-input\n",  # a trailing newline (``$`` would accept it; ``fullmatch`` must not)
    'a"b',
    "a'b",
    "a>b",
    "a<b",
    "a=b",
    "a/b",
    "a\tb",
    "a\nb",
    "a\x00b",
    "a\u0085b",  # NEL: a control character a browser treats as a break
    "a b",  # NBSP is whitespace to str.isspace(), not to the HTML spec: stay conservative
    "",
]

#: Names a template author really writes: every one must still render byte-identically.
LEGAL_NAMES = [
    "type",
    "dj-input",
    "dj-keydown.enter",
    "dj-value-row",
    "aria-label",
    "data-x_y",
    "@click",
    ":class",
    "x-on:click.prevent",
    "hx-post",
]


class TestAttrName:
    @pytest.mark.parametrize("name", LEGAL_NAMES)
    def test_a_legal_name_is_returned_unchanged(self, name):
        assert attr_name(name) == name

    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_a_hostile_name_is_refused(self, name):
        with pytest.raises(ValueError, match="not a valid HTML attribute name"):
            attr_name(name)

    @pytest.mark.parametrize("name", [None, 3, b"type", ("a",)])
    def test_a_name_that_is_not_text_is_refused(self, name):
        with pytest.raises(ValueError):
            attr_name(name)


class TestBuildTag:
    @pytest.mark.parametrize("name", HOSTILE_NAMES)
    def test_a_hostile_key_never_reaches_the_markup(self, name):
        with pytest.raises(ValueError):
            build_tag("input", {name: "1"})

    @pytest.mark.parametrize("name", LEGAL_NAMES)
    def test_a_legal_key_renders_exactly_as_before(self, name):
        assert build_tag("input", {name: "v"}) == f'<input {name}="v" />'

    def test_a_boolean_attribute_is_checked_too(self):
        with pytest.raises(ValueError):
            build_tag("input", {"disabled onfocus=alert(1) x": True})

    def test_an_omitted_attribute_is_not_judged(self):
        # ``None`` / ``False`` mean "not present": nothing is written, so there is nothing to check.
        assert (
            build_tag("input", {"x onfocus=1": None, "y z": False, "ok": "1"}) == '<input ok="1" />'
        )


class TestLiveInputEvent:
    """The data-reachable path: ``event=`` becomes an attribute name."""

    PAYLOAD = "dj-input onfocus=alert(1) autofocus y"

    def test_the_python_helper_refuses_a_hostile_event(self):
        with pytest.raises(ValueError, match="not a valid HTML attribute name"):
            live_input("text", handler="h", event=self.PAYLOAD)

    @pytest.mark.parametrize("field_type", ["text", "textarea", "select", "checkbox", "radio"])
    def test_every_field_type_refuses_it(self, field_type):
        extra = {"choices": [("a", "A")]} if field_type in ("select", "radio") else {}
        with pytest.raises(ValueError):
            live_input(field_type, handler="h", event=self.PAYLOAD, **extra)

    def test_a_variable_in_the_template_is_refused_at_render(self):
        tpl = Template('{% load live_tags %}{% live_input "text" handler="h" event=evt %}')
        with pytest.raises(ValueError):
            tpl.render(Context({"evt": self.PAYLOAD}))

    def test_a_passthrough_key_is_refused(self):
        with pytest.raises(ValueError):
            live_input("text", handler="h", **{"x onfocus=alert(1) y": "v"})

    @pytest.mark.parametrize("event", ["input", "change", "blur", "dj-input", "dj-keydown.enter"])
    def test_a_real_event_still_renders(self, event):
        out = str(live_input("text", handler="h", event=event))
        name = event if event.startswith("dj-") else f"dj-{event}"
        assert f'{name}="h"' in out


class TestFrameworkAdapter:
    """``FormMixin.as_live`` builds its tags through the adapter's own ``_build_tag``."""

    def test_the_adapter_refuses_a_hostile_key(self):
        with pytest.raises(ValueError):
            PlainAdapter()._build_tag("input", {"x onfocus=alert(1) y": "v"})

    def test_the_adapter_still_renders_a_legal_one(self):
        out = PlainAdapter()._build_tag("input", {"name": "n", "dj-change": "validate_field"})
        assert out == '<input name="n" dj-change="validate_field" />'


BAD_KEY = "x onfocus=alert(1) autofocus y"


class TestOtherAttributeNameWriters:
    """The same class as ``build_tag``, in the writers that did not go through it (review of #3821).

    Every one of these writes ``<prefix><key>=`` from a mapping key or a keyword name. None is
    reachable from template DATA today (a key is a literal or a dict key the developer wrote), which
    is why they are the parallel-path twin of ``build_tag``'s name rather than a new hole.
    """

    def test_render_icon_refuses_a_hostile_attribute_name(self):
        from djust.components.icons import render_icon

        with pytest.raises(ValueError, match="not a valid HTML attribute name"):
            render_icon("check", **{BAD_KEY: "v"})

    def test_render_icon_still_renders_aria_label(self):
        from djust.components.icons import render_icon

        assert 'aria-label="Close"' in render_icon("check", aria_label="Close")

    def test_button_refuses_a_hostile_data_key(self):
        from djust.components.components.button import Button

        with pytest.raises(ValueError):
            Button("Go", data={BAD_KEY: "1"}).render()

    def test_card_refuses_a_hostile_data_key(self):
        from djust.components.components.card import Card

        with pytest.raises(ValueError):
            Card("body", data={BAD_KEY: "1"}).render()

    def test_button_and_card_still_render_a_data_key(self):
        from djust.components.components.button import Button
        from djust.components.components.card import Card

        assert 'data-id="7"' in Button("Go", data={"id": 7}).render()
        assert 'data-id="7"' in Card("body", data={"id": 7}).render()

    def test_pagination_click_refuses_a_hostile_data_key(self):
        from djust.components.data.pagination import PaginationComponent

        with pytest.raises(ValueError):
            PaginationComponent(total_pages=3)._click("go", **{BAD_KEY: "1"})

    def test_pagination_click_still_writes_a_data_key(self):
        from djust.components.data.pagination import PaginationComponent

        assert ' data-page="2"' in PaginationComponent(total_pages=3)._click("go", page=2)

    def test_event_attrs_refuses_a_hostile_value_key_and_trigger(self):
        from djust.components.components.button import Button

        button = Button("Go")
        with pytest.raises(ValueError):
            button.event_attrs("go", **{BAD_KEY: "1"})
        with pytest.raises(ValueError):
            button.event_attrs("go", trigger="click onfocus=alert(1) y")

    def test_event_attrs_still_writes_typed_values(self):
        from djust.components.components.button import Button

        out = Button("Go").event_attrs("go", item_id=3)
        assert out == 'dj-click="go" dj-value-item-id:int="3"'
