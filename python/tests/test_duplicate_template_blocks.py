"""Block names are unique within one template, across all parsed bodies."""

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust.template import DjustTemplateBackend


@pytest.fixture
def backend(tmp_path):
    return DjustTemplateBackend(
        {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {"debug": True}}
    )


@pytest.mark.parametrize(
    "source",
    [
        "{% block content %}one{% endblock %}{% block content %}two{% endblock %}",
        '{% extends "missing" %}{% block content %}one{% endblock %}{% block content %}two{% endblock %}',
        "{% block content %}{% block content %}{% endblock %}{% endblock %}",
        "{% if condition %}{% block content %}one{% endblock %}{% else %}{% block content %}two{% endblock %}{% endif %}",
        "{% for item in items %}{% block content %}one{% endblock %}{% empty %}{% block content %}two{% endblock %}{% endfor %}",
        "{% block content %}one{% endblock %}{% with value=1 %}{% block content %}two{% endblock %}{% endwith %}",
        "{% block content %}one{% endblock %}{% autoescape off %}{% block content %}two{% endblock %}{% endautoescape %}",
        "{% block content %}one{% endblock %}{% block content %}{% unknown_tag %}{% endblock %}",
    ],
)
def test_duplicate_blocks_rejected_during_compilation(backend, source):
    with pytest.raises(TemplateSyntaxError) as expected:
        Engine().from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert str(expected.value) in str(actual.value)
    assert "appears more than once" in str(actual.value)


def test_duplicate_error_points_to_second_declaration(backend):
    source = "{% block content %}one{% endblock %}\n{% block content %}two{% endblock %}"
    with pytest.raises(TemplateSyntaxError) as error:
        backend.from_string(source)
    assert error.value.template_debug["line"] == 2
    assert error.value.template_debug["during"] == "{% block content %}"


def test_parent_and_child_have_separate_block_names(backend, tmp_path):
    (tmp_path / "base").write_text("[{% block content %}parent{% endblock %}]")
    source = '{% extends "base" %}{% block content %}child{% endblock %}'
    expected = Engine(dirs=[str(tmp_path)]).from_string(source).render(Context())
    assert backend.from_string(source).render({}) == expected == "[child]"


def test_comments_and_failed_compilation_do_not_reserve_names(backend):
    with pytest.raises(TemplateSyntaxError):
        backend.from_string("{% block content %}{% block content %}{% endblock %}{% endblock %}")
    source = "{% comment %}{% block content %}{% endblock %}{% endcomment %}{% block content %}ok{% endblock %}"
    for _ in range(2):
        assert backend.from_string(source).render({}) == "ok"
