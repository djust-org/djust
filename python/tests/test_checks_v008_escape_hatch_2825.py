"""Tests for #2825 — V008's return-annotation escape hatch was unreachable.

V008's documented non-``noqa`` remedy — annotate the helper's return type
(e.g. ``-> str``) — never silenced the check, for two independent reasons:

1. ``_build_primitive_return_funcs`` only walked ``tree.body`` (module-level
   functions), so a ``-> str`` on a METHOD registered nothing. A method is
   the ordinary shape for the thing a ``mount()`` calls.
2. The collected set held bare names while ``_get_call_name`` returns dotted
   names for attribute calls (``game.claim``), so ``call_name in funcs``
   could never match for any call routed through an object.

Together the two gaps covered both shapes a real ``mount()`` call site takes,
making the escape hatch dead code for everything except bare-name calls to
same-module functions (the one shape the original #393 implementation
happened to honour, which is why the feature looked implemented).

Follows the ``_run_v008`` harness shape of
``test_checks_v008_stdlib_qualified_1628.py``.
"""

import ast
import textwrap
from unittest.mock import patch


def _run_v008(tmp_path, source):
    py_file = tmp_path / "views.py"
    py_file.write_text(textwrap.dedent(source))

    from djust.checks import _check_non_primitive_assignments_in_mount

    errors = []
    with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
        _check_non_primitive_assignments_in_mount(errors)
    return [e for e in errors if e.id == "djust.V008"]


class TestV008AnnotationEscapeHatch2825:
    """#2825: an annotated ``-> str`` helper must silence V008 for every call
    shape ``mount()`` actually sees, and ONLY for annotated helpers."""

    def test_build_primitive_return_funcs_collects_class_methods(self):
        """Helper-level: a ``-> str`` method on a module-level class must be
        collected, not just module-level functions."""
        from djust.checks.components import _build_primitive_return_funcs

        src = textwrap.dedent(
            """\
            class Game:
                def claim(self) -> str:
                    return "p1"

                def raw(self):
                    return object()


            def helper() -> str:
                return "hi"
            """
        )
        funcs = _build_primitive_return_funcs(ast.parse(src))
        assert "claim" in funcs, (
            "annotated method must be collected by _build_primitive_return_funcs"
        )
        assert "helper" in funcs, "annotated module function must still be collected"
        assert "raw" not in funcs, "unannotated method must not be collected"

    def test_annotated_method_called_through_instance_does_not_fire(self, tmp_path):
        """The reporter's exact shape (#2825): ``self.my_role = game.claim(...)``
        where ``Game.claim`` is annotated ``-> str`` in the same module."""
        v008 = _run_v008(
            tmp_path,
            """\
            class Game:
                def claim(self) -> str:
                    return "p1"


            class MyView:
                def mount(self, request, **kwargs):
                    game = Game()
                    self.my_role = game.claim("u")
            """,
        )
        assert v008 == [], (
            "V008 must NOT fire on a call to a same-module method annotated "
            "-> str — the annotation is the documented remedy. Got: %r" % v008
        )

    def test_annotated_self_method_call_does_not_fire(self, tmp_path):
        """A view calling its own annotated helper: ``self.t = self.token()``
        resolves to the dotted call name ``self.token`` — the final segment
        must match the annotated method name."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def token(self) -> str:
                    return "abc"

                def mount(self, request, **kwargs):
                    self.t = self.token()
            """,
        )
        assert v008 == [], (
            "V008 must NOT fire on a call to the view's own annotated "
            "(-> str) helper method. Got: %r" % v008
        )

    def test_dotted_call_to_annotated_module_function_does_not_fire(self, tmp_path):
        """The bare-name set vs dotted call-name mismatch (#2825 gap 2): a
        same-module annotated function reached through an attribute chain
        (``game.helper()``) resolves to the dotted name ``game.helper`` —
        the final-segment comparison must honour the annotation."""
        v008 = _run_v008(
            tmp_path,
            """\
            def helper() -> str:
                return "hi"


            class MyView:
                def mount(self, request, **kwargs):
                    game = object()
                    self.b = game.helper()
            """,
        )
        assert v008 == [], (
            "V008 must NOT fire when the call's final name segment matches a "
            "same-module function annotated -> str. Got: %r" % v008
        )

    def test_annotated_module_function_bare_call_still_honoured(self, tmp_path):
        """Regression guard: the one shape the pre-#2825 implementation
        honoured (bare call to a same-module annotated function) must keep
        working."""
        v008 = _run_v008(
            tmp_path,
            """\
            def helper() -> str:
                return "hi"


            class MyView:
                def mount(self, request, **kwargs):
                    self.c = helper()
            """,
        )
        assert v008 == [], "bare call to annotated module function must stay honoured"

    def test_unannotated_method_still_fires(self, tmp_path):
        """Backstop / gate-off pair for the method fix: WITHOUT the ``-> str``
        annotation the same call must still fire — proves the silence above
        comes from the annotation, not from the dotted-name normalization
        blanket-skipping every dotted call."""
        v008 = _run_v008(
            tmp_path,
            """\
            class Game:
                def claim(self):
                    return "p1"


            class MyView:
                def mount(self, request, **kwargs):
                    game = Game()
                    self.my_role = game.claim("u")
            """,
        )
        assert len(v008) == 1, (
            "V008 must still fire on a call to an UNannotated method "
            "(the annotation is what settles it). Got: %r" % v008
        )

    def test_unannotated_module_function_dotted_call_still_fires(self, tmp_path):
        """Backstop: a dotted call to an UNannotated same-module function
        must still fire — final-segment normalization must not swallow
        unannotated helpers."""
        v008 = _run_v008(
            tmp_path,
            """\
            def helper():
                return object()


            class MyView:
                def mount(self, request, **kwargs):
                    game = object()
                    self.b = game.helper()
            """,
        )
        assert len(v008) == 1, "V008 must still fire on an unannotated dotted call. Got: %r" % v008
