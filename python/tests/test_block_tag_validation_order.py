"""Block signatures, bodies, and arguments are validated in Django's order."""

import pytest
from django.template import Engine, Library, TemplateSyntaxError

from djust.template import DjustTemplateBackend

register = Library()


@register.simple_block_tag
def needs_argument(content, value):
    return content + str(value)


@register.simple_block_tag
def invalid_signature(value):
    return value


@register.simple_block_tag(takes_context=True)
def invalid_context_signature(context, value):
    return value


@pytest.mark.parametrize(
    "body",
    [
        "{% needs_argument %}",
        "{% needs_argument %}body{% endneeds_argument %}",
        "{% needs_argument %}{% unknown_tag %}{% endneeds_argument %}",
        "{% needs_argument %}{{ x|unknown_filter }}{% endneeds_argument %}",
        "{% needs_argument %}{% if x %}{% endneeds_argument %}",
        "{% needs_argument %}{% needs_argument %}{% endneeds_argument %}",
        "{% invalid_signature %}",
        "{% invalid_signature %}{% unknown_tag %}",
        "{% invalid_context_signature %}",
        "{% needs_argument x y %}{% endneeds_argument %}",
    ],
)
def test_block_validation_error_matches_django(body):
    libraries = {"validation_order": __name__}
    source = "{% load validation_order %}" + body
    django_engine = Engine(libraries=libraries)
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": libraries}}
    )
    with pytest.raises(TemplateSyntaxError) as reference:
        django_engine.from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert str(actual.value).removeprefix("Template error: ") == str(reference.value)
