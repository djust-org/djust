"""RichTextEditor renders its value as cleaned HTML.

The editor's value is itself HTML (usually stored rich text), so it is not
escaped like other content slots: formatting tags survive, while scripts,
event-handler attributes and non-http(s)/mailto URLs are removed. Values
marked safe pass through untouched. Both the component class and the
``{% rich_text_editor %}`` tag (which the Rust handler delegates to) follow
the same rule.
"""

from __future__ import annotations

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from djust.components.components.rich_text_editor import RichTextEditor
from djust.components.utils import rich_html

_ENGINE = Engine(libraries={"djust_components": "djust.components.templatetags.djust_components"})


def _tag(value) -> str:
    return _ENGINE.from_string("{% load djust_components %}{% rich_text_editor value=v %}").render(
        Context({"v": value})
    )


def _cls(value) -> str:
    return str(RichTextEditor(value=value).render())


RENDERERS = [pytest.param(_tag, id="tag"), pytest.param(_cls, id="class")]


@pytest.mark.parametrize("render", RENDERERS)
def test_formatting_is_kept(render):
    out = render("<p>Hello <strong>bold</strong> and <em>it</em></p><ul><li>one</li></ul>")
    assert "<strong>bold</strong>" in out
    assert "<em>it</em>" in out
    assert "<li>one</li>" in out
    assert "&lt;strong&gt;" not in out


@pytest.mark.parametrize("render", RENDERERS)
def test_script_elements_are_removed(render):
    out = render("<p>ok</p><script>alert(1)</script>")
    assert "alert(1)" not in out
    assert "<p>ok</p>" in out


@pytest.mark.parametrize("render", RENDERERS)
def test_event_handler_attributes_are_removed(render):
    out = render('<img src="https://example.com/a.png" onerror="alert(1)">')
    assert "onerror" not in out
    assert 'src="https://example.com/a.png"' in out


@pytest.mark.parametrize("render", RENDERERS)
def test_non_web_link_schemes_are_removed(render):
    out = render('<a href="javascript:alert(1)">x</a> <a href="https://djust.org">ok</a>')
    assert "javascript:" not in out
    assert 'href="https://djust.org"' in out


@pytest.mark.parametrize("render", RENDERERS)
def test_marked_safe_value_passes_through(render):
    value = mark_safe('<p style="color:red" data-x="1">kept</p>')
    out = render(value)
    assert '<p style="color:red" data-x="1">kept</p>' in out


def test_helper_handles_none_and_plain_text():
    assert rich_html(None) == ""
    assert rich_html("plain & simple") == "plain &amp; simple"
