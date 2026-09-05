"""An included template resolves its inheritance using the include's context."""

import pytest
from django.template import Context, Engine

from djust import _rust
from djust.template import DjustTemplateBackend


def outcome(render):
    try:
        return "ok", str(render())
    except Exception as exc:
        return type(exc), str(exc).removeprefix("Template error: ")


@pytest.mark.parametrize("entry", ["backend", "native"])
@pytest.mark.parametrize(
    "include_args",
    [
        "",
        " with parent='b.html'",
        " with parent='b.html' only",
        " only",
        " with parent=missing",
        " with parent=missing only",
    ],
)
def test_include_inheritance_uses_scoped_context(tmp_path, entry, include_args):
    templates = {
        "a.html": "{% block body %}a{% endblock %}",
        "b.html": "{% block body %}b{% endblock %}",
        "child.html": "{% extends parent %}{% block body %}child-{{ block.super }}{% endblock %}",
    }
    for name, content in templates.items():
        (tmp_path / name).write_text(content)
    source = "{% include 'child.html'" + include_args + " %}"
    context = {"parent": "a.html"}
    reference = Engine(dirs=[str(tmp_path)]).from_string(source)
    expected = outcome(lambda: reference.render(Context(context)))
    if entry == "native":
        actual = outcome(lambda: _rust.render_template_with_dirs(source, context, [str(tmp_path)]))
    else:
        backend = DjustTemplateBackend(
            {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
        )
        actual = outcome(lambda: backend.from_string(source).render(context))
    assert actual == expected
