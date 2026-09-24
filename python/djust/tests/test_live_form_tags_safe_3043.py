"""``{% live_form %}`` / ``{% live_field %}`` / ``{% live_errors %}`` render
markup, not escaped text (#3043).

The three tags are ``simple_tag``s. They returned a plain ``str``, so
``SimpleNode.render`` escaped it and the page showed ``<div class="mb-3">…``
as text, on the Django engine and (since #3042 bridged them) the Rust one.
The markup the tags generate is now returned safe; every value in it that can
come from a user — field values, error messages, choice labels and values —
stays escaped. Output is compared with the real Django engine.
"""

from __future__ import annotations

import pytest
from django import forms
from django.core.exceptions import ValidationError
from django.template import engines
from django.utils.safestring import SafeString

from djust import LiveView
from djust.forms import FormMixin

pytestmark = pytest.mark.django_db

HOSTILE = '"><script>alert(1)</script>'


class _EchoForm(forms.Form):
    name = forms.CharField(max_length=100, label="Name <required>")
    colour = forms.ChoiceField(
        choices=[('r"x', "Red <b>"), ("g", "Green")],
        widget=forms.RadioSelect,
        required=False,
    )

    def clean_name(self):
        value = self.cleaned_data["name"]
        if "<" in value:
            raise ValidationError(f"{value} is not allowed")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("name") is None:
            raise ValidationError(f"form-level: {self.data.get('name')}")
        return cleaned


class _View(FormMixin, LiveView):
    form_class = _EchoForm
    template = "<div dj-root></div>"


def _view(name: str = HOSTILE) -> _View:
    view = _View()
    view.mount(None)
    view.submit_form(name=name)
    return view


def _django(body: str, view) -> str:
    return engines["django"].from_string("{% load live_tags %}" + body).render({"view": view})


def _rust(body: str, view) -> str:
    from djust._rust import RustLiveView

    src = "{% load live_tags %}<div dj-root>" + body + "</div>"
    rv = RustLiveView(src)
    rv.update_state({"view": view})
    html = rv.render()
    return html[len("<div dj-root>") : -len("</div>")]


FORM_TAGS = [
    "{% live_form view %}",
    '{% live_field view "name" %}',
    '{% live_field view "colour" %}',
    '{% live_errors view "name" %}',
    "{% live_errors view %}",
]


@pytest.mark.parametrize("body", FORM_TAGS)
def test_markup_is_not_escaped_on_the_django_engine(body):
    out = _django(body, _view())
    assert out.lstrip().startswith("<div"), out[:120]
    assert "&lt;div" not in out


@pytest.mark.parametrize("body", FORM_TAGS)
def test_user_values_stay_escaped(body):
    out = _django(body, _view())
    assert "<script>" not in out
    assert '"><script' not in out


@pytest.mark.parametrize("body", FORM_TAGS)
def test_rust_render_matches_django(body):
    view = _view()
    assert _rust(body, view) == _django(body, view)


def test_escaping_is_exact_where_it_matters():
    view = _view()
    form = _django("{% live_form view %}", view)
    # the field value, in an attribute
    assert 'value="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in form
    # the label, as text
    assert "Name &lt;required&gt;" in form
    # a choice value in both the input's value and the label's for=
    assert 'value="r&quot;x"' in form and 'for="id_colour_r&quot;x"' in form
    # a choice label
    assert "Red &lt;b&gt;" in form
    errors = _django('{% live_errors view "name" %}', view)
    assert errors == (
        '<div class="invalid-feedback d-block"><div>'
        "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt; is not allowed</div></div>"
    )
    assert _django("{% live_errors view %}", view) == (
        '<div class="alert alert-danger"><div>form-level: '
        "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;</div></div>"
    )


def test_wrapper_class_argument_is_escaped():
    out = (
        engines["django"]
        .from_string('{% load live_tags %}{% live_field view "name" wrapper_class=cls %}')
        .render({"view": _view("ok"), "cls": 'x" onmouseover="alert(1)'})
    )
    assert out.startswith('<div class="x&quot; onmouseover=&quot;alert(1)">'), out[:80]


def test_as_live_returns_safe_markup():
    view = _view("ok")
    assert isinstance(view.as_live(), SafeString)
    assert isinstance(view.as_live_field("name"), SafeString)
    out = engines["django"].from_string("{{ form_html }}").render({"form_html": view.as_live()})
    assert out.startswith("<div")


def test_no_errors_render_nothing():
    view = _View()
    view.mount(None)
    assert _django('{% live_errors view "name" %}', view) == ""
    assert _django("{% live_errors view %}", view) == ""
