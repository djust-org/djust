import pytest
from django.template import TemplateSyntaxError
from djust.template.backend import DjustTemplateBackend

backend = DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


def render(src, ctx=None):
    return str(backend.from_string(src).render(ctx or {}))


def test_named_cycle_advancement():
    template = '{% cycle "a" "b" "c" as cycle_var %} {% cycle cycle_var %} {% cycle cycle_var %}'
    result = render(template)
    assert result == "a b c"


def test_blocktranslate_nested_block_error():
    template = (
        "{% load i18n %}{% blocktrans %}Hello {% block b %}world{% endblock %}{% endblocktrans %}"
    )
    with pytest.raises(TemplateSyntaxError) as exc_info:
        render(template)
    assert "doesn't allow other block tags" in str(exc_info.value)


def test_i18n_translation_basic():
    template = '{% load i18n %}{% trans "Hello" %}'
    result = render(template)
    assert result == "Hello"
