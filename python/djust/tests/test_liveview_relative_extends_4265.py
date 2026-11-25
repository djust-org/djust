"""#4265: a ``template_name`` file whose ``{% extends %}`` / ``{% include %}`` names are relative
(``./`` / ``../``) resolves each name against the file that holds it, as Django does, when the
Rust ``resolve_template_inheritance`` flattens it: a LiveView's ``get_template()``, an embedded
``{% live_render %}`` child, the JIT extraction and ``runtime.template_source``.

Before, the flattening was given no name for the child (``build_inheritance_chain`` started from
``None``), so ``{% extends "./base.html" %}`` searched the template roots and raised
``Template not found: ./base.html``. The differential: what Django renders for the file, and what
the engine renders from the flattened source, agree.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string

from djust import LiveView
from djust._rust import render_template_with_dirs, resolve_template_inheritance
from djust.utils import get_template_dirs

#: ``name -> files``: the chain shapes. ``decoy`` files sit where a name resolved against the
#: wrong directory (the template root, or the child's directory) would find them.
SHAPES = {
    "extends-dot": (
        "sub/c.html",
        {
            "sub/c.html": '{% extends "./base.html" %}{% block b %}child {{ v }}{% endblock %}',
            "sub/base.html": "<main>{% block b %}{% endblock %}</main>",
            "base.html": "WRONG",
        },
    ),
    "extends-dotdot": (
        "sub/deep/c.html",
        {
            "sub/deep/c.html": '{% extends "../base.html" %}{% block b %}child {{ v }}{% endblock %}',
            "sub/base.html": "<main>{% block b %}{% endblock %}</main>",
            "base.html": "WRONG",
        },
    ),
    "three-level-chain": (
        "dir1/one.html",
        {
            "dir1/one.html": '{% extends "./dir2/two.html" %}{% block b %}1 {{ v }}{% endblock %}',
            "dir1/dir2/two.html": '{% extends "../three.html" %}{% block c %}2{% endblock %}',
            "dir1/three.html": "<main>{% block b %}3{% endblock %}|{% block c %}3c{% endblock %}</main>",
            "three.html": "WRONG",
        },
    ),
    "include-in-child-block": (
        "a/c.html",
        {
            "a/c.html": '{% extends "../base/layout.html" %}{% block b %}{% include "./p.html" %}{% endblock %}',
            "a/p.html": "child-p {{ v }}",
            "base/layout.html": "<main>{% block b %}{% endblock %}</main>",
            "base/p.html": "WRONG",
            "p.html": "WRONG",
        },
    ),
    "include-in-parent": (
        "a/c.html",
        {
            "a/c.html": '{% extends "../base/layout.html" %}{% block b %}x{% endblock %}',
            "base/layout.html": '<main>{% block b %}{% endblock %}{% include "./q.html" %}</main>',
            "base/q.html": "parent-q {{ v }}",
            "a/q.html": "WRONG",
            "q.html": "WRONG",
        },
    ),
    "include-two-levels-up": (
        "l1/l2/l3.html",
        {
            "l1/l2/l3.html": '{% extends "../l2b/mid.html" %}{% block b %}leaf{% endblock %}',
            "l1/l2b/mid.html": '{% extends "../top.html" %}{% block c %}{% include "./part.html" %}{% endblock %}',
            "l1/l2b/part.html": "mid-part {{ v }}",
            "l1/top.html": "<main>{% block b %}{% endblock %}{% block c %}{% endblock %}</main>",
            "l1/l2/part.html": "WRONG",
        },
    ),
}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_flattened_source_renders_what_django_renders(rel_files, shape) -> None:
    name, files = SHAPES[shape]
    for path, source in files.items():
        rel_files(path, source)
    dirs = get_template_dirs()
    expected = render_to_string(name, {"v": "hi"})
    assert "WRONG" not in expected, expected
    flattened = resolve_template_inheritance(name, dirs)
    out = render_template_with_dirs(flattened, {"v": "hi"}, dirs)
    assert _squash(out) == _squash(expected), (flattened, out, expected)


#: The include names a flattened source must hold, absolute: ``{% include %}`` is not inlined in a
#: source, so the decoy's text cannot show up there; the NAME is what resolved.
INCLUDES = {
    "include-in-child-block": ["a/p.html"],
    "include-in-parent": ["base/q.html"],
    "include-two-levels-up": ["l1/l2b/part.html"],
}


INLINED = {
    "include-in-child-block": ["child-p"],
    "include-in-parent": ["parent-q"],
    "include-two-levels-up": ["mid-part"],
}


def _view(name: str):
    return type("V", (LiveView,), {"exposure_policy": "explicit", "template_name": name})()


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_liveview_template_name_resolves_the_chain(rel_files, shape) -> None:
    name, files = SHAPES[shape]
    for path, source in files.items():
        rel_files(path, source)
    source = _view(name).get_template()
    assert "WRONG" not in source
    for included in INCLUDES.get(shape, []):
        assert '{% include "' + included + '" %}' in source, source


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_jit_and_runtime_sources_resolve_the_chain(rel_files, shape) -> None:
    """The other callers of the resolver: the runtime's flattened source and the JIT extraction
    source (``_jit_template_content``, which calls ``JITMixin._get_template_content``)."""
    from djust.mixins.jit import _jit_template_content
    from djust.runtime import template_source

    name, files = SHAPES[shape]
    for path, source in files.items():
        rel_files(path, source)
    view = _view(name)
    view.get_template()
    flattened = template_source(name)
    assert "WRONG" not in flattened, flattened
    for included in INCLUDES.get(shape, []):
        assert '{% include "' + included + '" %}' in flattened, flattened
    # the JIT source inlines each include, so the RIGHT partial's text is there, never the decoy's
    jit = _jit_template_content(view)
    assert jit and "WRONG" not in jit, jit
    for text in INLINED.get(shape, []):
        assert text in jit, jit


def test_two_views_with_one_source_in_different_directories_each_get_their_own_parent(
    rel_files,
) -> None:
    """``a/c.html`` and ``b/c.html`` are byte-identical and differ only in the ``./base.html``
    they mean: nothing keyed by source text or by name may serve one view the other's parent."""
    from djust.mixins.jit import _jit_template_content
    from djust.runtime import template_source
    from djust.websocket import render_embedded_child_html

    for d in ("a", "b"):
        rel_files(f"{d}/c.html", '{% extends "./base.html" %}{% block b %}x{% endblock %}')
        rel_files(
            f"{d}/base.html", "<div dj-root>" + d.upper() + "{% block b %}{% endblock %}</div>"
        )
    for d in ("a", "b", "a"):  # the second "a" is served from whatever the first filled
        name = f"{d}/c.html"
        view = _view(name)
        other = "B" if d == "a" else "A"
        assert d.upper() in template_source(name) and other not in template_source(name)
        jit = _jit_template_content(view)
        assert jit is not None and d.upper() in jit and other not in jit, jit
        html = render_embedded_child_html(_view(name))
        assert d.upper() in html and other not in html, html


def test_an_embedded_child_renders_and_records_through_a_relative_extends(rel_files) -> None:
    from djust.websocket import render_embedded_child_html

    rel_files(
        "sub/c.html",
        '{% extends "./base.html" %}{% block b %}<input dj-model="title">{% endblock %}',
    )
    rel_files("sub/base.html", "<div dj-root>{% block b %}{% endblock %}</div>")
    child = type("C", (LiveView,), {"exposure_policy": "explicit", "template_name": "sub/c.html"})()
    html = render_embedded_child_html(child)
    assert '<input dj-model="title">' in html, html
    assert child._dj_model_fields == frozenset({"title"})


@pytest.mark.parametrize("up", ["../../base.html", "../../../base.html"])
def test_a_relative_extends_above_the_template_root_is_refused_as_django_refuses(
    rel_files, up
) -> None:
    from django.template import TemplateSyntaxError

    rel_files("a/c.html", '{% extends "' + up + '" %}')
    rel_files("base.html", "x")
    with pytest.raises(TemplateSyntaxError, match="outside the file hierarchy"):
        render_to_string("a/c.html", {})
    # the Rust resolver reports it as a RuntimeError (Django's is a TemplateSyntaxError)
    with pytest.raises(RuntimeError, match="outside the file hierarchy"):
        resolve_template_inheritance("a/c.html", get_template_dirs())
