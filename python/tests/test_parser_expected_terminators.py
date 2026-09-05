"""Unknown and misplaced tags report the active block's expected terminators."""

import pytest
from django.template import Engine, Library, TemplateSyntaxError

from djust.template import DjustTemplateBackend

register = Library()


@register.simple_block_tag(end_name="endpanel")
def panel(content):
    return content


@pytest.mark.parametrize(
    "source",
    [
        "{% unknown %}",
        "line one\n{% unknown %}",
        "{% endif %}",
        "{% if value %}{% else %}",
        "{% if value %}\n{% elif other %}",
        "{% if value %}\n{% elif other %}\n{% else %}",
        "{% if value %}{% else %}{% else %}{% endif %}",
        "{% if value %}{% else %}{% elif other %}{% endif %}",
        "{% for item in items %}{% empty %}{% empty %}{% endfor %}",
        "{% for item in items %}",
        "{% for item in items %}{% empty %}",
        "{% with item=value %}",
        "{% spaceless %}",
        "{% ifchanged value %}\n{% else %}",
        "{% if value %}{% endblock %}{% endif %}",
        "{% if value %}{% unknown %}{% endif %}",
        "{% if value %}{% else %}{% unknown %}{% endif %}",
        "{% for item in items %}{% unknown %}{% endfor %}",
        "{% for item in items %}{% empty %}{% unknown %}{% endfor %}",
        "{% block content %}{% unknown %}{% endblock %}",
        "{% with item=value %}{% unknown %}{% endwith %}",
        "{% spaceless %}{% unknown %}{% endspaceless %}",
        "{% autoescape off %}{% unknown %}{% endautoescape %}",
        "{% load diagnostics %}{% panel %}{% wrongend %}",
        "{% load diagnostics %}\n{% panel %}unclosed",
        "{% load diagnostics %}{% panel %}{% if value %}{% wrongend %}",
    ],
)
def test_expected_terminators_match_django(source):
    libraries = {"diagnostics": __name__}
    reference = Engine(libraries=libraries)
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": libraries}}
    )
    with pytest.raises(TemplateSyntaxError) as expected:
        reference.from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert str(actual.value).removeprefix("Template error: ") == str(expected.value)
