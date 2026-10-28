"""Django 5.x must keep unknown-tag and user-defined-tag semantics."""

import django
import pytest
from django.template import TemplateSyntaxError

from djust.template.backend import DjustTemplateBackend

pytestmark = pytest.mark.skipif(django.VERSION >= (6, 0), reason="Django 6 defines partial tags")


def test_partial_tags_are_not_builtins_before_django_6():
    backend = DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(TemplateSyntaxError):
        backend.from_string("{% partial hello %}")
    with pytest.raises(TemplateSyntaxError):
        backend.from_string("{% partialdef hello %}content{% endpartialdef %}")


def test_user_defined_partial_tag_remains_available_before_django_6():
    from djust._rust import register_tag_handler, render_template, unregister_tag_handler

    class Tag:
        def render(self, args, context):
            return "custom partial"

    register_tag_handler("partial", Tag())
    try:
        assert render_template("{% partial %}", {}) == "custom partial"
    finally:
        unregister_tag_handler("partial")
