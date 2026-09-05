"""Django nodes receive the settings of the backend currently rendering them."""

import pytest
from django.template import Context, Engine, Library

from djust.template import DjustTemplateBackend
from djust.template_libraries import LibraryTagHandler, rendering_with_backend


@pytest.mark.parametrize("invalid", ["", "INVALID", "missing:%s"])
@pytest.mark.parametrize("operand", ["missing", 'missing|default:"fallback"'])
def test_static_missing_operand_uses_backend_setting(invalid, operand, settings):
    settings.STATIC_URL = "/assets/"
    source = "{% load static %}{% static " + operand + " %}"
    reference = (
        Engine(string_if_invalid=invalid, libraries={"static": "django.templatetags.static"})
        .from_string(source)
        .render(Context())
    )
    backend = DjustTemplateBackend(
        {
            "NAME": "test",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"string_if_invalid": invalid},
        }
    )
    assert backend.from_string(source).render({}) == reference


def test_cached_inline_node_follows_current_render_backend():
    library = Library()

    @library.simple_tag(takes_context=True)
    def inspect_options(context, value):
        return f"{value}|{context.template.engine.debug}"

    handler = LibraryTagHandler("test", "inspect_options", library.tags["inspect_options"])
    args = ["missing"]
    handler.validate_at_parse(args)

    def backend(invalid, debug):
        return DjustTemplateBackend(
            {
                "NAME": "test",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"string_if_invalid": invalid, "debug": debug},
            }
        )

    with rendering_with_backend(backend("outer:%s", True)):
        assert handler.render(args, {})[0] == "outer:missing|True"
        with rendering_with_backend(backend("inner", False)):
            assert handler.render(args, {})[0] == "inner|False"
        assert handler.render(args, {})[0] == "outer:missing|True"
    assert handler.render(args, {})[0] == "|False"
