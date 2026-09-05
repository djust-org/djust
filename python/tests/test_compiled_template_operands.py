"""Compiled template operands preserve lookup, scoping, and escaping behavior."""

import pytest
from django.template import Context, Engine
from django.template.backends.django import Template as BackendTemplate
from djust.template import DjustTemplateBackend


def engines(tmp_path):
    return [
        Engine(dirs=[tmp_path]),
        DjustTemplateBackend(
            {
                "NAME": "operands",
                "DIRS": [tmp_path],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ),
    ]


@pytest.mark.parametrize("operand", ["parent", "parents.1", "parent|default:other"])
@pytest.mark.parametrize("tag", ["include", "extends"])
def test_compiled_operand(tmp_path, operand, tag):
    outputs = []
    for engine in engines(tmp_path):
        parent = engine.from_string("{% block main %}parent {{ value }}{% endblock %}")
        source = "{% " + tag + " " + operand + " %}"
        if tag == "extends":
            source += "{% block main %}child {{ block.super }}{% endblock %}"
        outputs.append(
            engine.from_string(source).render(
                Context(
                    {
                        "parent": parent,
                        "parents": [None, parent],
                        "other": parent,
                        "value": "<x>",
                    }
                )
            )
        )
    assert outputs[1] == outputs[0]


@pytest.mark.parametrize("only", [False, True])
@pytest.mark.parametrize("autoescape", [False, True])
def test_include_scope(tmp_path, only, autoescape):
    outputs = []
    for engine in engines(tmp_path):
        parent = engine.from_string("{{ value }}|{{ outer }}")
        source = (
            "{% include parent with value=inner" + (" only" if only else "") + " %}|{{ value }}"
        )
        outputs.append(
            engine.from_string(source).render(
                Context(
                    {
                        "parent": parent,
                        "value": "original",
                        "inner": "<x>",
                        "outer": "outer",
                    },
                    autoescape=autoescape,
                )
            )
        )
    assert outputs[1] == outputs[0]


@pytest.mark.parametrize("tag", ["include", "extends"])
def test_operand_evaluated_once(tmp_path, tag):
    for engine in engines(tmp_path):
        (tmp_path / "base.html").write_text("base")
        calls = []

        class Selector:
            @property
            def parent(self):
                calls.append(1)
                return "base.html"

        assert (
            engine.from_string("{% " + tag + " selector.parent %}").render(
                Context({"selector": Selector()})
            )
            == "base"
        )
        assert calls == [1]


@pytest.mark.parametrize("tag", ["include", "extends"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_django_template_object(tmp_path, tag, wrapped):
    parent = Engine().from_string("{% block main %}parent{% endblock %}")
    if wrapped:
        parent = BackendTemplate(parent, None)
    backend = engines(tmp_path)[1]
    source = "{% " + tag + " parent %}"
    if tag == "extends":
        source += "{% block main %}child{% endblock %}"
    assert backend.from_string(source).render({"parent": parent}) == (
        "child" if tag == "extends" else "parent"
    )


@pytest.mark.parametrize("tag", ["include", "extends"])
def test_compiled_template_reuses_custom_compiler(tmp_path, tag):
    from django.template import Library, Node
    from unittest.mock import patch
    import sys

    library = Library()
    calls = []

    class Content(Node):
        def render(self, context):
            return "compiled"

    @library.tag
    def compile_once(parser, token):
        calls.append(1)
        return Content()

    with patch.object(sys.modules[__name__], "register", library, create=True):
        parent_engine = DjustTemplateBackend(
            {
                "NAME": "parent",
                "DIRS": [tmp_path],
                "APP_DIRS": False,
                "OPTIONS": {"libraries": {"private_library": __name__}},
            }
        )
        parent = parent_engine.from_string(
            "{% load private_library %}{% block main %}{% compile_once %}{% endblock %}"
        )
        child = engines(tmp_path)[1].from_string("{% " + tag + " parent %}")
        for _ in range(2):
            assert child.render({"parent": parent}) == "compiled"
        assert calls == [1]


def test_include_render_protocol(tmp_path):
    class Renderable:
        def render(self, context):
            assert context.autoescape is False
            assert "outer" not in context
            return "<b>" + context["value"] + "</b>"

    backend = engines(tmp_path)[1]
    template = backend.from_string("{% include part with value='hello' only %}")
    assert (
        template.render(Context({"part": Renderable(), "outer": "hidden"}, autoescape=False))
        == "<b>hello</b>"
    )


def test_compiled_parent_relative_origin(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/base.html").write_text("{% block main %}base{% endblock %}")
    (tmp_path / "nested/parent.html").write_text(
        '{% extends "./base.html" %}{% block main %}parent {{ block.super }}{% endblock %}'
    )
    outputs = []
    for engine in engines(tmp_path):
        parent = engine.get_template("nested/parent.html")
        child = engine.from_string(
            "{% extends parent %}{% block main %}child {{ block.super }}{% endblock %}"
        )
        outputs.append(child.render(Context({"parent": parent})))
    assert outputs[1] == outputs[0] == "child parent base"


def test_compiled_parent_preserves_loader_history(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "base.html").write_text(
        '{% extends "base.html" %}{% block main %}override {{ block.super }}{% endblock %}'
    )
    (second / "base.html").write_text("{% block main %}base{% endblock %}")
    backend = DjustTemplateBackend(
        {"NAME": "history", "DIRS": [first, second], "APP_DIRS": False, "OPTIONS": {}}
    )
    parent = backend.get_template("base.html")
    child = backend.from_string("{% extends parent %}")
    assert child.render({"parent": parent}) == "override base"
