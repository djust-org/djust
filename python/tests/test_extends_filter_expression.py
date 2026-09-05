"""Parent names are filter expressions, with ordinary resolution errors."""

import pytest
from django.template import Context, Engine, TemplateSyntaxError

from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "operand,context",
    [
        ("'xbase.html'|cut:'x'", {}),
        ('"BASE.HTML"|lower', {}),
        ("parent|lower", {"parent": "BASE.HTML"}),
        ('"base.html"|cut:remove', {"remove": "unused"}),
        ("'base.html'|default:'fallback.html'", {}),
        ("missing|default:'base.html'", {}),
    ],
)
def test_extends_resolves_complete_expression(tmp_path, operand, context):
    (tmp_path / "base.html").write_text("before[{% block content %}parent{% endblock %}]after")
    source = "{% extends " + operand + " %}{% block content %}child{% endblock %}"
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    )
    expected = Engine(dirs=[str(tmp_path)]).from_string(source).render(Context(context))
    assert backend.from_string(source).render(context) == expected == "before[child]after"


def test_parent_resolution_preserves_original_exception(tmp_path):
    error = ZeroDivisionError("parent selection failed")

    class Selector:
        def parent(self):
            raise error

    source = "{% extends selector.parent %}"
    context = {"selector": Selector()}
    with pytest.raises(ZeroDivisionError) as reference:
        Engine().from_string(source).render(Context(context))
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    )
    with pytest.raises(ZeroDivisionError) as actual:
        backend.from_string(source).render(context)
    assert actual.value is reference.value is error


@pytest.mark.parametrize(
    "operand", ["parent|unknown_filter", "parent|add", "parent,other", "parent._private"]
)
def test_invalid_extends_expression_fails_at_compile_time(operand):
    source = "{% extends " + operand + " %}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(TemplateSyntaxError):
        Engine().from_string(source)
    with pytest.raises(TemplateSyntaxError):
        backend.from_string(source)


@pytest.mark.parametrize(
    "spec", ["cut", "join", 'upper:"x"', 'cut:"a":"b"', "nosuchfilter", "date:_y"]
)
def test_extends_rejects_each_differential_refusal_class(spec):
    # Keep a missing source variable from masking a compile-time refusal.
    source = "{% extends missing|" + spec + " %}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(TemplateSyntaxError):
        Engine().from_string(source)
    with pytest.raises(TemplateSyntaxError):
        backend.from_string(source)
