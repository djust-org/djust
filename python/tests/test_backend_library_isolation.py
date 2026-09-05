"""One backend's registered libraries must not leak into another backend."""

import pytest
from django.template import Context, Engine, Library, TemplateSyntaxError
from djust.template import DjustTemplateBackend

register = Library()


@register.simple_tag
def isolated_tag():
    return "isolated"


def test_backend_library_names_are_isolated():
    backends = []
    for name in ["isolated_a", "isolated_b"]:
        libraries = {name: __name__}
        backends.append(
            (
                Engine(libraries=libraries),
                DjustTemplateBackend(
                    {
                        "NAME": name,
                        "DIRS": [],
                        "APP_DIRS": False,
                        "OPTIONS": {"libraries": libraries},
                    }
                ),
            )
        )
    for index, (django_engine, backend) in enumerate(backends):
        name = ["isolated_a", "isolated_b"][index]
        source = "{% load " + name + " %}{% isolated_tag %}"
        assert backend.from_string(source).render({}) == django_engine.from_string(source).render(
            Context()
        )
        other = ["isolated_b", "isolated_a"][index]
        with pytest.raises(TemplateSyntaxError):
            backend.from_string("{% load " + other + " %}")
