"""#4265: an embedded ``{% live_render %}`` child whose ``template_name`` file includes a partial by a
relative ``./`` / ``../`` name keeps that partial's ``dj-model`` fields in its allowlist and
re-renders.

The allowlist is derived from the child's template SOURCE by a walker that has no origin, and the
re-render (``render_embedded_child_html``) compiles that source with ``from_string``, which has
none either, so the relative name did not resolve: the fields were refused (fail closed,
silently) and the first ``dj-model`` update that re-rendered the child failed. Both now take the
source from ``_child_source_with_named_includes``, which names the includes against the child's
``template_name``.
"""

from __future__ import annotations

from djust import LiveView
from djust.templatetags.live_tags import _record_child_dj_model_allowlist
from djust.websocket import render_embedded_child_html


# The child's rendered output: the allowlist is the template's static bindings
# narrowed to the ones the render emitted.
_TITLE = '<div dj-root><input dj-model="title"></div>'


def _child(**attrs):
    return type("Child", (LiveView,), {"exposure_policy": "explicit", **attrs})()


def test_a_relative_include_keeps_the_partials_fields(rel_files) -> None:
    rel_files("sub/main.html", '<div dj-root>{% include "./part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/main.html")
    _record_child_dj_model_allowlist(child, _TITLE)
    assert child._dj_model_fields == frozenset({"title"})


def test_a_dot_dot_include_keeps_the_partials_fields(rel_files) -> None:
    rel_files("sub/deep/main.html", '<div dj-root>{% include "../part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/deep/main.html")
    _record_child_dj_model_allowlist(child, _TITLE)
    assert child._dj_model_fields == frozenset({"title"})


def test_the_absolute_name_is_unchanged(rel_files) -> None:
    rel_files("sub/main.html", '<div dj-root>{% include "sub/part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/main.html")
    _record_child_dj_model_allowlist(child, _TITLE)
    assert child._dj_model_fields == frozenset({"title"})


def test_a_relative_name_that_escapes_the_hierarchy_fails_closed(rel_files, caplog) -> None:
    rel_files(
        "a/main.html", '<div dj-root><input dj-model="own">{% include "../../x.html" %}</div>'
    )
    rel_files("x.html", '<input dj-model="title">')
    child = _child(template_name="a/main.html")
    _record_child_dj_model_allowlist(child, _TITLE)  # logs and never raises
    assert "title" not in getattr(child, "_dj_model_fields", frozenset())
    assert "failing closed" in caplog.text


def test_the_rerender_of_the_child_resolves_the_name_and_records_the_fields(rel_files) -> None:
    """The WS / runtime re-render (the first ``dj-model`` update) compiles the same source."""
    rel_files("sub/main.html", '<div dj-root>{% include "./part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/main.html")
    html = render_embedded_child_html(child)
    assert '<input dj-model="title">' in html, html
    assert child._dj_model_fields == frozenset({"title"})


def test_an_inline_template_child_is_left_alone(rel_files) -> None:
    child = _child(template='<div dj-root><input dj-model="own"></div>', template_name="sub/x.html")
    assert "own" in render_embedded_child_html(child)
    assert child._dj_model_fields == frozenset({"own"})


def test_a_relative_include_in_a_flattened_parent_does_not_widen_the_allowlist(rel_files) -> None:
    """The child's dj-root is only in its parent, and the PARENT includes ``./p.html``: the source
    ``get_template()`` returns is the flattened one, whose include is already named against the
    parent's directory. Resolving it against the CHILD's directory would read ``sub/p.html``
    (here a decoy with another dj-model name) and widen the allowlist (CWE-915)."""
    rel_files(
        "base/layout.html", '<div dj-root>{% block b %}{% endblock %}{% include "./p.html" %}</div>'
    )
    rel_files("base/p.html", '<input dj-model="good">')
    rel_files("sub/p.html", '<input dj-model="evil">')
    rel_files("sub/main.html", '{% extends "base/layout.html" %}{% block b %}x{% endblock %}')
    child = _child(template_name="sub/main.html")
    _record_child_dj_model_allowlist(child, '<input dj-model="good"><input dj-model="evil">')
    assert child._dj_model_fields == frozenset({"good"})
