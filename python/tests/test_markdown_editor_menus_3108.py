"""MarkdownEditor ``bubble_menu`` / ``floating_menu`` options (#3108).

Every public renderer — the Python component, the Django ``{% markdown_editor %}``
tag, the Rust-engine tag handler, and the ``markdown_controls.html`` include in
both template engines — must emit the same host attributes, because the hook
reads nothing else. The attribute values are fixed literals: caller text never
reaches the markup.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template, Variable

from djust import _rust
from djust.components.components.markdown_editor import MarkdownEditor, menu_attrs, menu_flag
from djust.components.rust_handlers import MarkdownEditorHandler
from djust.components.templatetags._dev_tools import MarkdownEditorNode

CONTROLS = (
    Path(__file__).resolve().parents[1]
    / "djust"
    / "components"
    / "templates"
    / "djust_components"
    / "markdown_controls.html"
)
_HOST = re.compile(r'data-bubble-menu="([^"]*)" data-floating-menu="([^"]*)"')


def _host(output: str) -> tuple[str, str]:
    found = _HOST.findall(str(output))
    assert len(found) == 1, output
    return found[0]


def _component(**kw: object) -> str:
    return str(MarkdownEditor(name="body", mode="visual", **kw))


def _django_tag(**kw: object) -> str:
    context = {f"v_{k}": v for k, v in kw.items()}
    node = MarkdownEditorNode(
        {"name": "body", "mode": "visual", **{k: Variable(f"v_{k}") for k in kw}}
    )
    return str(node.render(Context(context)))


def _rust_tag(**kw: object) -> str:
    context = {f"v_{k}": v for k, v in kw.items()}
    args = ['name="body"', 'mode="visual"', *(f"{k}=v_{k}" for k in kw)]
    return str(MarkdownEditorHandler().render(args, context))


def _controls_django(**kw: object) -> str:
    return Template(CONTROLS.read_text()).render(Context({"mode": "visual", **kw}))


def _controls_rust(**kw: object) -> str:
    body = CONTROLS.read_text().split("#}\n", 1)[1]
    return _rust.render_template(body, {"mode": "visual", **kw})


RENDERERS = {
    "component": _component,
    "django_tag": _django_tag,
    "rust_tag": _rust_tag,
    "controls_django": _controls_django,
    "controls_rust": _controls_rust,
}


@pytest.mark.parametrize("render", RENDERERS.values(), ids=RENDERERS.keys())
def test_bubble_menu_defaults_on_and_floating_menu_defaults_off(render):
    assert _host(render()) == ("true", "false")


@pytest.mark.parametrize("render", RENDERERS.values(), ids=RENDERERS.keys())
@pytest.mark.parametrize(
    "kw, expected",
    [
        ({"bubble_menu": False}, ("false", "false")),
        ({"bubble_menu": "false"}, ("false", "false")),
        ({"floating_menu": True}, ("true", "true")),
        ({"floating_menu": "true"}, ("true", "true")),
        ({"bubble_menu": False, "floating_menu": True}, ("false", "true")),
        ({"bubble_menu": True, "floating_menu": False}, ("true", "false")),
    ],
)
def test_every_renderer_emits_the_same_menu_switches(render, kw, expected):
    assert _host(render(**kw)) == expected


@pytest.mark.parametrize(
    "literal, expected",
    [
        ("", ("true", "false")),
        ("bubble_menu=False", ("false", "false")),
        ('bubble_menu="false"', ("false", "false")),
        ("floating_menu=True", ("true", "true")),
    ],
)
def test_tag_literals_through_both_template_engines(literal, expected):
    tag = '{% markdown_editor name="b" ' + literal + " %}"
    django_out = Template("{% load djust_components %}" + tag).render(Context())
    rust_out = _rust.render_template(tag, {})
    assert _host(django_out) == expected
    assert _host(rust_out) == expected


def test_component_keeps_resolved_booleans():
    editor = MarkdownEditor(bubble_menu="off", floating_menu="yes")
    assert editor.bubble_menu is False
    assert editor.floating_menu is True


@pytest.mark.parametrize(
    "value, default, expected",
    [
        (None, True, True),
        ("", True, True),
        ("", False, False),
        (False, True, False),
        (0, True, False),
        ("False", True, False),
        ("OFF", True, False),
        ("no", True, False),
        ("0", True, False),
        (True, False, True),
        ("true", False, True),
        ("1", False, True),
        ("maybe", False, False),
        ("maybe", True, True),
        (2, False, False),
    ],
)
def test_menu_flag_reads_template_and_python_spellings(value, default, expected):
    assert menu_flag(value, default) is expected


SPELLINGS = [None, "", True, False, 0, 1, 2, "true", "True", "TRUE", "false", "False",
             "0", "1", "yes", "no", "on", "off", "OFF", " off ", "maybe"]  # fmt: skip


@pytest.mark.parametrize("render", RENDERERS.values(), ids=RENDERERS.keys())
@pytest.mark.parametrize("value", SPELLINGS, ids=repr)
def test_every_renderer_reads_a_spelling_exactly_as_menu_flag_does(render, value):
    """The include parses flags in template syntax; the others call menu_flag.
    The expected value is derived from menu_flag, so the two cannot drift."""
    expected = (
        "true" if menu_flag(value, True) else "false",
        "true" if menu_flag(value, False) else "false",
    )
    assert _host(render(bubble_menu=value, floating_menu=value)) == expected


HOSTILE = ['" onmouseover="alert(1)', "<script>alert(1)</script>", "' autofocus onfocus='x"]


@pytest.mark.parametrize("value", HOSTILE)
@pytest.mark.parametrize("render", RENDERERS.values(), ids=RENDERERS.keys())
def test_caller_text_never_reaches_the_menu_attributes(render, value):
    output = str(render(bubble_menu=value, floating_menu=value))
    assert value not in output
    assert "onmouseover" not in output and "onfocus" not in output
    assert "<script>" not in output
    assert _host(output)[0] in {"true", "false"}
    assert _host(output)[1] in {"true", "false"}


def test_menu_attrs_is_a_fixed_literal():
    assert menu_attrs() == ' data-bubble-menu="true" data-floating-menu="false"'
    assert menu_attrs(False, True) == ' data-bubble-menu="false" data-floating-menu="true"'
