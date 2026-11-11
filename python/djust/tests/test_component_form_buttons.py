"""``toggle_group`` / ``rating`` buttons do not submit their form, and can post a field; ``dj_radio``
ids are valid.

A ``<button>`` without a ``type`` is a submit button, so a toggle group or a star rating placed
inside a ``<form dj-submit>`` submitted the form on every click, and neither posted its value with
the form. Each renderer (the Django template tag, the Rust engine's inline handler and the
component class) now writes ``type="button"``; ``toggle_group form_field=True`` and
``rating name=...`` add hidden inputs that carry the value. ``dj_radio`` built its id from the raw
choice value, so a value with a space produced an id with a space in it.
"""

from __future__ import annotations

import re

import pytest
from django.template import engines
from django.utils.safestring import mark_safe

from djust.components.components.rating import Rating
from djust.components.components.toggle_group import ToggleGroup

pytestmark = [pytest.mark.pr_tier]

OPTIONS = [{"value": "day", "label": "Day"}, {"value": "week", "label": "Week"}]


def _django(body: str, **context) -> str:
    return engines["django"].from_string("{% load djust_components %}" + body).render(context)


def _rust(body: str, **context) -> str:
    from djust._rust import RustLiveView

    rv = RustLiveView("{% load djust_components %}<div dj-root>" + body + "</div>")
    rv.update_state(context)
    html = rv.render()
    return html[len("<div dj-root>") : -len("</div>")]


def _buttons(html: str) -> list:
    return re.findall(r"<button\b[^>]*>", html)


def _hidden(html: str) -> list:
    return re.findall(r'<input type="hidden" name="([^"]*)" value="([^"]*)">', html)


RENDERERS = [_django, _rust]
RENDERER_IDS = ["django", "rust"]


@pytest.mark.parametrize("render", RENDERERS, ids=RENDERER_IDS)
class TestToggleGroup:
    def test_every_button_is_type_button(self, render):
        html = render(
            '{% toggle_group name="period" options=options value="day" %}', options=OPTIONS
        )
        buttons = _buttons(html)
        assert len(buttons) == 2
        assert all('type="button"' in b for b in buttons)

    def test_no_form_field_by_default(self, render):
        html = render(
            '{% toggle_group name="period" options=options value="day" %}', options=OPTIONS
        )
        assert _hidden(html) == []

    def test_form_field_posts_the_value(self, render):
        html = render(
            '{% toggle_group name="period" options=options value="week" form_field=True %}',
            options=OPTIONS,
        )
        assert _hidden(html) == [("period", "week")]

    def test_multi_mode_posts_each_value(self, render):
        html = render(
            '{% toggle_group name="days" options=options value=selected mode="multi" form_field=True %}',
            options=OPTIONS,
            selected=["day", "week"],
        )
        assert _hidden(html) == [("days", "day"), ("days", "week")]

    def test_hidden_value_is_escaped(self, render):
        html = render(
            '{% toggle_group name="p" options=options value=v form_field=True %}',
            options=OPTIONS,
            v='"><script>x</script>',
        )
        assert "<script>" not in html
        assert len(_hidden(html)) == 1

    def test_a_marked_safe_name_cannot_leave_the_attribute(self, render):
        # ``name`` is written into an attribute: it is escaped whatever it is marked as (#3898).
        html = render(
            '{% toggle_group name=n options=options value="day" form_field=True %}',
            options=OPTIONS,
            n=mark_safe('x"><script>y</script>'),
        )
        # The hidden input this change adds (the buttons' ``data-name`` is a separate slot).
        hidden = re.findall(r'<input type="hidden"[^>]*>', html)
        assert len(hidden) == 1 and "<script>" not in hidden[0]
        assert len(_hidden(html)) == 1


@pytest.mark.parametrize("render", RENDERERS, ids=RENDERER_IDS)
class TestRating:
    def test_every_star_is_type_button(self, render):
        html = render("{% rating value=3 %}")
        buttons = _buttons(html)
        assert len(buttons) == 5
        assert all('type="button"' in b for b in buttons)

    def test_name_posts_the_value(self, render):
        assert _hidden(render('{% rating value=4 name="score" %}')) == [("score", "4")]
        assert _hidden(render('{% rating value=3.5 name="score" %}')) == [("score", "3.5")]

    def test_a_marked_safe_name_cannot_leave_the_attribute(self, render):
        html = render("{% rating value=4 name=n %}", n=mark_safe('x"><script>y</script>'))
        assert "<script>" not in html
        assert len(_hidden(html)) == 1

    def test_no_name_no_field(self, render):
        assert _hidden(render("{% rating value=4 %}")) == []

    def test_readonly_has_no_buttons(self, render):
        assert _buttons(render("{% rating value=4 readonly=True %}")) == []


def test_component_classes_render_type_button():
    rating = Rating(value=2).render()
    toggle = ToggleGroup(name="p", options=OPTIONS, value="day").render()
    for html, count in ((rating, 5), (toggle, 2)):
        buttons = _buttons(str(html))
        assert len(buttons) == count
        assert all('type="button"' in b for b in buttons)


class TestDjRadioIds:
    def test_a_value_with_a_space_gives_a_valid_id(self):
        html = _django('{% dj_radio name="plan" value="two words" label="Two" %}')
        ids = re.findall(r' id="([^"]*)"', html)
        assert ids == ["plan_two-words"]
        assert 'for="plan_two-words"' in html
        assert 'value="two words"' in html

    def test_explicit_id(self):
        html = _django('{% dj_radio name="plan" value="a b" label="A" id="plan-a" %}')
        assert 'id="plan-a"' in html and 'for="plan-a"' in html

    def test_plain_value_is_unchanged(self):
        assert 'id="plan_basic"' in _django('{% dj_radio name="plan" value="basic" label="B" %}')
