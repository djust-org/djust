"""#4124 — an inline tag without ``as`` no longer redraws an unchanged ``{% for %}``.

``render_nodes_partial`` used ONE signal, the ``"*"`` dependency, for two
questions: "does this node read keys we cannot name?" (re-render the node
itself) and "may it bind names a later sibling reads?" (re-render every later
sibling). Every bridged tag answered yes to both, so a ``{% url %}``,
``{% static %}``, ``{% now %}``, a ``simple_tag`` or a component tag anywhere
before a loop redrew the loop on every partial render, even when nothing the
loop reads had changed.

Each test below observes the skip directly: the loop's input is mutated but
NOT reported as changed, so a reused fragment still shows the OLD rows (gate
off), and reporting it shows the NEW rows (gate on). A tag that CAN bind
(``as <name>``, a raw ``@register.tag``, ``{% include %}``) must keep
latching, so the later loop is redrawn even though only ``count`` changed.
"""

from __future__ import annotations

import re

import pytest
from django.http import HttpResponse
from django.template import Library, Node
from django.test import override_settings
from django.urls import path

from djust import _rust
from djust.template_libraries import LibraryTagHandler, _binds_only_as_var

urlpatterns = [path("x4124/", lambda request: HttpResponse("x"), name="x4124")]


def _plain(html: str) -> str:
    """Drop the VDOM ids so assertions read the markup alone."""
    return re.sub(r' dj-id="[^"]*"', "", html)


LOOP = "<ul>{% for e in entries %}<li>{{ e }}</li>{% endfor %}</ul>"

library = Library()


@library.simple_tag
def plain4124(value=""):
    return "plain:%s" % value


@library.simple_tag(takes_context=True)
def ctx4124(context):
    return "ctx"


@library.inclusion_tag("missing4124.html")
def incl4124():
    return {}


class _SetsName(Node):
    def render(self, context):
        context["bound4124"] = "raw"
        return ""


@library.tag
def raw4124(parser, token):
    return _SetsName()


class ComponentStandIn:
    """A component-style inline handler: plain ``str`` return, no bindings."""

    def render(self, args, context):
        return "<b>component</b>"


@pytest.fixture(autouse=True, scope="module")
def _handlers():
    names = []
    for name in ("plain4124", "ctx4124", "raw4124"):
        _rust.register_tag_handler(name, LibraryTagHandler("lib4124", name, library.tags[name]))
        names.append(name)
    _rust.register_tag_handler("component4124", ComponentStandIn())
    names.append("component4124")
    with override_settings(ROOT_URLCONF=__name__):
        yield
    for name in names:
        _rust.unregister_tag_handler(name)


def _rows_after_count_change(tag: str, report_entries: bool) -> str:
    """Render ``<p>{{ count }}</p>{tag}{LOOP}``, then change ``count`` AND
    ``entries`` while reporting only ``count`` (plus ``entries`` when asked)."""
    view = _rust.RustLiveView("<div dj-root><p>{{ count }}</p>" + tag + LOOP + "</div>", [])
    view.update_state({"count": 1, "entries": ["old-a", "old-b"]})
    view.render_with_diff()
    view.update_state({"count": 2, "entries": ["new-z"]})
    view.set_changed_keys(["count", "entries"] if report_entries else ["count"])
    html, _patches, _version = view.render_with_diff()
    html = _plain(html)
    assert "<p>2</p>" in html, html
    return html


NON_BINDING = [
    "{% url 'x4124' %}",
    "{% static 'a.css' %}",
    "{% now 'Y' %}",
    "{% plain4124 'v' %}",
    "{% component4124 %}",
    "{% if count %}{% url 'x4124' %}{% endif %}",
]


@pytest.mark.parametrize("tag", NON_BINDING)
def test_inline_tag_without_as_does_not_redraw_an_unchanged_loop(tag):
    html = _rows_after_count_change(tag, report_entries=False)
    # The loop's fragment was reused: its input was not reported as changed.
    assert "old-a" in html and "new-z" not in html, html


@pytest.mark.parametrize("tag", NON_BINDING)
def test_inline_tag_without_as_still_redraws_a_changed_loop(tag):
    html = _rows_after_count_change(tag, report_entries=True)
    assert "new-z" in html and "old-a" not in html, html


BINDING = [
    "{% url 'x4124' as u %}",
    "{% static 'a.css' as s %}",
    "{% now 'Y' as y %}",
    "{% plain4124 'v' as p %}",
    "{% ctx4124 %}",
    "{% raw4124 %}",
    "{% if count %}{% url 'x4124' as u %}{% endif %}",
    "{% firstof count as f %}",
]


@pytest.mark.parametrize("tag", BINDING)
def test_a_tag_that_may_bind_still_redraws_later_siblings(tag):
    html = _rows_after_count_change(tag, report_entries=False)
    assert "new-z" in html and "old-a" not in html, html


def test_an_as_binding_reaches_a_later_sibling_through_partial_render():
    view = _rust.RustLiveView(
        "<div dj-root><p>{{ count }}</p>{% url 'x4124' as u %}<i>{{ u }}</i></div>", []
    )
    view.update_state({"count": 1})
    view.render_with_diff()
    view.update_state({"count": 2})
    view.set_changed_keys(["count"])
    html = _plain(view.render_with_diff()[0])
    assert "<i>/x4124/</i>" in html, html


def test_only_context_free_simple_and_inclusion_tags_bind_only_as():
    assert _binds_only_as_var(library.tags["plain4124"])
    assert _binds_only_as_var(library.tags["incl4124"])
    assert not _binds_only_as_var(library.tags["ctx4124"])
    assert not _binds_only_as_var(library.tags["raw4124"])
