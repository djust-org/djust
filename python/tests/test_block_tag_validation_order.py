"""Block signatures, bodies, and arguments are validated in Django's order."""

import django
import pytest
from django.template import Engine, Library, TemplateSyntaxError

from djust.template import DjustTemplateBackend

register = Library()


@register.simple_block_tag
def needs_argument(content, value):
    return content + str(value)


def invalid_signature(value):
    return value


def invalid_context_signature(context, value):
    return value


# Django <= 6.0 validates these when a template compiles. Django 6.1 moved
# validation to registration, so invalid tags cannot enter its library.
if django.VERSION < (6, 1):
    register.simple_block_tag(invalid_signature)
    register.simple_block_tag(invalid_context_signature, takes_context=True)


@pytest.mark.skipif(django.VERSION < (6, 1), reason="Django validates on registration in 6.1+")
@pytest.mark.parametrize(
    ("tag", "takes_context", "message"),
    [
        (invalid_signature, False, "first argument of 'content'"),
        (invalid_context_signature, True, "second argument of 'content'"),
    ],
)
def test_invalid_block_signature_is_rejected_at_registration(tag, takes_context, message):
    with pytest.raises(TemplateSyntaxError, match=message):
        Library().simple_block_tag(tag, takes_context=takes_context)


@pytest.mark.parametrize(
    "body",
    [
        "{% needs_argument %}",
        "{% needs_argument %}body{% endneeds_argument %}",
        "{% needs_argument %}{% unknown_tag %}{% endneeds_argument %}",
        "{% needs_argument %}{{ x|unknown_filter }}{% endneeds_argument %}",
        "{% needs_argument %}{% if x %}{% endneeds_argument %}",
        "{% needs_argument %}{% needs_argument %}{% endneeds_argument %}",
        pytest.param(
            "{% invalid_signature %}",
            marks=pytest.mark.skipif(
                django.VERSION >= (6, 1), reason="invalid tag cannot register"
            ),
        ),
        pytest.param(
            "{% invalid_signature %}{% unknown_tag %}",
            marks=pytest.mark.skipif(
                django.VERSION >= (6, 1), reason="invalid tag cannot register"
            ),
        ),
        pytest.param(
            "{% invalid_context_signature %}",
            marks=pytest.mark.skipif(
                django.VERSION >= (6, 1), reason="invalid tag cannot register"
            ),
        ),
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
