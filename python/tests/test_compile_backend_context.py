"""Compilation uses its own engine's library map and restores nested contexts."""

import pytest
from django.template import Engine, TemplateSyntaxError

from djust.template.rendering import DjustTemplate
from djust.template_libraries import rendering_with_backend


@pytest.mark.parametrize("load", ["missing", "from known", "echo from", "missing from unknown"])
def test_compile_load_error_uses_engine_libraries(load):
    engine = Engine(libraries={"known": "django.templatetags.static"})
    source = "{% load " + load + " %}"
    with pytest.raises(TemplateSyntaxError) as expected:
        engine.from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        DjustTemplate(source, engine)
    assert str(actual.value) == str(expected.value)


def test_failed_nested_compilation_restores_backend():
    from djust.template_libraries import _current_backend

    outer = Engine(libraries={"outer": "django.templatetags.static"})
    inner = Engine(libraries={"inner": "django.templatetags.static"})
    with rendering_with_backend(outer):
        with pytest.raises(TemplateSyntaxError, match="inner"):
            DjustTemplate("{% load not_registered %}", inner)
        assert _current_backend.get() is outer
    assert _current_backend.get() is None


def test_cached_source_still_checks_compiling_engine_libraries():
    source = "{% load scoped_static %}"
    enabled = Engine(libraries={"scoped_static": "django.templatetags.static"})
    disabled = Engine(libraries={})
    DjustTemplate(source, enabled)
    with pytest.raises(TemplateSyntaxError):
        DjustTemplate(source, disabled)
