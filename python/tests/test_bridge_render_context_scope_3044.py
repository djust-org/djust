"""Only djust's own bridged tags share the per-render ``RenderContext`` (#3044).

``library_render_scope`` gives ``{% djust_skeleton %}`` Django's one-``<style>``
-per-render behaviour. The #3053 review found the first version shared the
context with EVERY bridged node: the bridge caches one node per argument
tuple, so a third-party tag keeping state in ``render_context[self]`` merged
the state of two sites with the same arguments and of every loop iteration
(``A1 B2 L3L4L5``). Third-party tags keep their 1.2.0 per-call context.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from test_load_imports_django_libraries_2547 import liveview_render  # noqa: E402

SRC = (
    "{% load lib3053_state %}A{% counter3053 %} B{% counter3053 %} "
    "{% for i in xs %}L{% counter3053 %}{% endfor %}"
)


def test_third_party_tag_keeps_its_per_call_context(monkeypatch):
    from djust import template_libraries as tl

    # The bare LiveView entry resolves `{% load %}` against the libraries the
    # djust backend registered (`register_backend_libraries`).
    monkeypatch.setitem(tl._extra_libraries, "lib3053_state", "lib2547.templatetags.lib3053_state")
    out = liveview_render(SRC, {"xs": [1, 2, 3]})
    assert out == "A1 B1 L1L1L1"  # as in 1.2.0; not merged across sites


def test_scope_is_restored_after_a_nested_render():
    """A component rendered inside a view render opens its own scope and
    restores the outer one on exit."""
    from djust.template_libraries import _render_scope, library_render_scope

    assert _render_scope.get() is None
    with library_render_scope():
        outer = _render_scope.get()
        with library_render_scope():
            inner = _render_scope.get()
            assert inner is not outer and inner is not None
        assert _render_scope.get() is outer
    assert _render_scope.get() is None
