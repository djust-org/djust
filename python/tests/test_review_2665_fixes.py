"""Regression tests for the review findings on PR #2665.

Each test names the finding it pins and states what the pre-fix behaviour
was, so a future reader can tell a real regression from a deliberate change.
"""

import inspect

import pytest
import djust.mixins.jit as jit_module


def _jit_mixin():
    """The mixin that owns `_get_template_content`, found by capability."""
    for obj in vars(jit_module).values():
        if inspect.isclass(obj) and "_get_template_content" in vars(obj):
            return obj
    raise AssertionError("no class in djust.mixins.jit defines _get_template_content")


class TestFinding1JitExtendsStub:
    """Finding 1 (HIGH): `mixins/template.py` now keeps `_full_template` as a
    bare `{% extends %}` stub so inheritance stays intact at render time. The
    JIT variable extractor *preferred* `_full_template`, so every
    inheritance-using view fed it a stub with zero variable paths — which
    silently disabled queryset optimization (the N+1 protection the JIT path
    exists to provide). A stub must fall through to the resolver."""

    STUB = '{% extends "posts/list.html" %}'
    REAL = "{% for p in posts %}{{ p.author.name }}{% endfor %}"

    def _view(self, full_template, template=None):
        view = _jit_mixin().__new__(_jit_mixin())
        view._full_template = full_template
        view.template = template
        view.template_name = None
        return view

    def test_extends_stub_does_not_short_circuit(self):
        view = self._view(self.STUB, template=self.REAL)
        # Pre-fix: returned the stub. The resolver chain is what yields paths.
        assert view._get_template_content() == self.REAL

    @pytest.mark.parametrize(
        "stub",
        [
            '{% extends "a.html" %}',
            "{%extends 'a.html'%}",
            '  {% extends "a.html" %}\n',
            "{% extends parent_var %}",
        ],
    )
    def test_stub_shapes_all_fall_through(self, stub):
        view = self._view(stub, template=self.REAL)
        assert view._get_template_content() == self.REAL

    def test_resolved_source_is_still_preferred(self):
        """The fix must not regress the case the preference exists for: a
        genuinely resolved `_full_template` (has content beyond `extends`)
        still wins over `template`."""
        resolved = '{% extends "base.html" %}{% block c %}' + self.REAL + "{% endblock %}"
        view = self._view(resolved, template="{{ unrelated }}")
        assert view._get_template_content() == resolved
