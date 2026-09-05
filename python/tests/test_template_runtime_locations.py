"""Runtime exceptions identify their defining template, even through inheritance."""

import pytest
from django.template import Context, Engine, Library, TemplateDoesNotExist
from djust.template import DjustTemplateBackend

register = Library()
raised = []


@register.simple_tag
def explode():
    error = RuntimeError("tag exploded")
    raised.append(error)
    raise error


def engines(tmp_path, debug):
    libraries = {"failures": __name__}
    return [
        Engine(dirs=[tmp_path], debug=debug, libraries=libraries),
        DjustTemplateBackend(
            {
                "NAME": "runtime",
                "DIRS": [tmp_path],
                "APP_DIRS": False,
                "OPTIONS": {"debug": debug, "libraries": libraries},
            }
        ),
    ]


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("relationship", ["root", "include", "extends"])
@pytest.mark.parametrize(
    "body,exception",
    [
        ("{{ boom }}", ZeroDivisionError),
        ("{% load failures %}{% for x in xs %}{% explode %}{% endfor %}", RuntimeError),
        ("{% for x in five %}x{% endfor %}", (TypeError, RuntimeError)),
        ('{% include "missing.html" %}', TemplateDoesNotExist),
    ],
)
def test_runtime_location(tmp_path, debug, relationship, body, exception):
    source = "Unicode café α\n" + body + "\n"
    if relationship == "extends":
        (tmp_path / "base.html").write_text("{% block main %}base{% endblock %}")
        source = '{% extends "base.html" %}\n{% block main %}' + source + "{% endblock %}"
    (tmp_path / "bad.html").write_text(source)
    errors = []
    for engine in engines(tmp_path, debug):
        template = (
            engine.from_string('{% include "bad.html" %}')
            if relationship == "include"
            else engine.get_template("bad.html")
        )
        with pytest.raises(exception) as caught:
            template.render(Context({"boom": lambda: 1 / 0, "xs": [1], "five": 5}))
        if "explode" in body:
            assert caught.value is raised[-1]
        errors.append(caught.value)
    if debug:
        for key in ["name", "line", "during", "start", "end", "before", "after", "source_lines"]:
            assert errors[1].template_debug[key] == errors[0].template_debug[key]
    else:
        assert not hasattr(errors[1], "template_debug")


def test_identical_tokens_report_executed_location(tmp_path):
    source = "{% load failures %}{% if False %}{% explode %}{% endif %}\n{% explode %}"
    errors = []
    for engine in engines(tmp_path, True):
        with pytest.raises(RuntimeError) as caught:
            engine.from_string(source).render(Context())
        errors.append(caught.value)
    assert errors[1].template_debug["start"] == errors[0].template_debug["start"]
    assert errors[1].template_debug["line"] == 2


def test_compiled_parent_retains_runtime_origin(tmp_path):
    source = "parent\n{{ boom }}"
    (tmp_path / "parent.html").write_text(source)
    backend = engines(tmp_path, True)[1]
    parent = backend.get_template("parent.html")
    child = backend.from_string("{% extends parent %}")
    with pytest.raises(ZeroDivisionError) as caught:
        child.render({"parent": parent, "boom": lambda: 1 / 0})
    assert caught.value.template_debug["name"] == str(tmp_path / "parent.html")
    assert caught.value.template_debug["line"] == 2
    assert caught.value.template_debug["during"] == "{{ boom }}"
