"""#4265: an embedded ``{% live_render %}`` child whose ``template_name`` file includes a partial by a
relative ``./`` / ``../`` name keeps that partial's ``dj-model`` fields in its allowlist.

The allowlist is derived from the child's template SOURCE by a walker that has no origin, so the
relative name did not resolve and the fields were refused (fail closed, silently). The child's
``template_name`` now names the includes first.
"""

from __future__ import annotations


import pytest

from djust import LiveView
from djust.templatetags.live_tags import _record_child_dj_model_allowlist
from djust.tests.test_component_hybrid_rawtext_and_live_templates_3913_3914 import (  # noqa: F401
    rel_files as _engines_files,
)


@pytest.fixture
def rel_files(_engines_files):  # noqa: F811
    """A template directory under each engine: ``write(name, source)``."""
    return _engines_files


def _child(**attrs):
    return type("Child", (LiveView,), {"exposure_policy": "explicit", **attrs})()


def test_a_relative_include_keeps_the_partials_fields(rel_files) -> None:
    rel_files("sub/main.html", '<div dj-root>{% include "./part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/main.html")
    _record_child_dj_model_allowlist(child)
    assert child._dj_model_fields == frozenset({"title"})


def test_a_dot_dot_include_keeps_the_partials_fields(rel_files) -> None:
    rel_files("sub/deep/main.html", '<div dj-root>{% include "../part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/deep/main.html")
    _record_child_dj_model_allowlist(child)
    assert child._dj_model_fields == frozenset({"title"})


def test_the_absolute_name_is_unchanged(rel_files) -> None:
    rel_files("sub/main.html", '<div dj-root>{% include "sub/part.html" %}</div>')
    rel_files("sub/part.html", '<input dj-model="title">')
    child = _child(template_name="sub/main.html")
    _record_child_dj_model_allowlist(child)
    assert child._dj_model_fields == frozenset({"title"})


def test_a_relative_name_that_escapes_the_hierarchy_fails_closed(rel_files, caplog) -> None:
    rel_files(
        "a/main.html", '<div dj-root><input dj-model="own">{% include "../../x.html" %}</div>'
    )
    rel_files("x.html", '<input dj-model="title">')
    child = _child(template_name="a/main.html")
    _record_child_dj_model_allowlist(child)  # logs and never raises
    assert "title" not in getattr(child, "_dj_model_fields", frozenset())
    assert "failing closed" in caplog.text
