"""Nested blocks in a rendered parent body retain child overrides."""

import pytest
from django.template import Context, Engine
from django.test import override_settings
from django.urls import NoReverseMatch

from djust.template import DjustTemplateBackend

urlpatterns = []


@pytest.mark.parametrize("body", ["child-inner", '{% url "missing-name" %}'])
def test_parent_body_applies_nested_override(tmp_path, body):
    templates = {
        "base.html": "{% block outer %}base[{% block inner %}original{% endblock %}]{% endblock %}",
        "child.html": '{% extends "base.html" %}{% block outer %}child[{{ block.super }}]{% endblock %}'
        "{% block inner %}" + body + "{% endblock %}",
    }
    for name, source in templates.items():
        (tmp_path / name).write_text(source)
    django = Engine(dirs=[str(tmp_path)])
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    )
    with override_settings(ROOT_URLCONF=__name__):
        if body.startswith("{% url"):
            for engine in [django, backend]:
                with pytest.raises(NoReverseMatch):
                    engine.get_template("child.html").render(Context())
        else:
            expected = django.get_template("child.html").render(Context())
            assert backend.get_template("child.html").render({}) == expected
