"""#2745 — an inline-if COMPOUND condition must reach that node's deps.

``{{ a if x > y else b }}`` handed its condition, ``x > y``, to
``extract_from_operand`` — the helper #2738 converged every TAG OPERAND onto.
That helper splits a FILTER CHAIN; for a pipe-free operand it degrades to
``extract_from_variable``, which splits on ``.`` only. A compound condition has
neither a pipe nor a dot, so the whole expression was entered as ONE bogus key
(``x > y``) and both real names were lost. ``render_nodes_partial`` then
skipped the node when only ``y`` changed and emitted STALE bytes, with no
error — Django semantics have no "missing dependency" signal.

A condition is an EXPRESSION, not an operand. The cure routes it through
``extract_from_expression`` — the helper ``{% if %}``'s condition already
uses, which tokenizes on the operator set — the same shape the RENDERER
evaluates the condition with (``evaluate_condition_for_if``). The
``true_expr`` / ``false_expr`` arms stay on ``extract_from_operand``: they are
operands. (No pipe can reach any of the three: the parser splits the filter
chain off the whole ``{{ }}`` before it looks for ``if``.)

Sibling of ``test_tag_operand_deps_2738.py``; the harness is the same.
"""

from __future__ import annotations

import pathlib

import pytest

from djust import _rust


def _body(html: str) -> str:
    """The rendered body, so the two entry points compare on content alone."""
    return html.split('<body dj-id="2">', 1)[-1].split("</body>", 1)[0]


def _partial_then_reference(source: str, initial: dict, change: dict) -> tuple[str, str]:
    """Render, mutate ONLY *change*, re-render through the partial path.

    Returns ``(what the partial render produced, what a fresh view produces)``.
    """
    view = _rust.RustLiveView(source)
    for key, value in initial.items():
        view.set_state(key, value)
    view.render_with_diff()

    for key, value in change.items():
        view.set_state(key, value)
    view.set_changed_keys(list(change))
    partial, _, _ = view.render_with_diff()

    reference = _rust.RustLiveView(source)
    for key, value in {**initial, **change}.items():
        reference.set_state(key, value)
    want, _, _ = reference.render_with_diff()
    return _body(partial), _body(want)


class TestTheCompoundConditionReachesTheDependencySet:
    """The extraction half: the real names in, the bogus key out."""

    @pytest.mark.parametrize(
        ("source", "wanted", "unwanted"),
        [
            # A comparison: `x > y` was one key; neither name was recorded.
            ("{{ a if x > y else b }}", {"a", "b", "x", "y"}, {"x > y"}),
            # A boolean operator: same shape.
            ("{{ a if x and y else b }}", {"a", "b", "x", "y"}, {"x and y"}),
            # The simple condition was already sound; the control.
            ("{{ a if x else b }}", {"a", "b", "x"}, set()),
            # A dotted operand inside the comparison keeps its ROOT.
            ("{{ a if x.n > y else b }}", {"a", "b", "x", "y"}, {"x.n > y", "x.n"}),
            # No else branch: the condition is still an expression.
            ("{{ a if x > y }}", {"a", "x", "y"}, {"x > y"}),
        ],
    )
    def test_a_compound_condition_contributes_every_name(
        self, source: str, wanted: set, unwanted: set
    ) -> None:
        found = set(_rust.extract_template_variables(source))
        assert wanted <= found, f"{sorted(wanted - found)} missing from {sorted(found)}"
        assert not (unwanted & found), f"bogus keys {sorted(unwanted & found)} in {sorted(found)}"

    def test_the_operators_themselves_are_not_names(self) -> None:
        """``and`` / ``not`` / ``or`` are keywords, not context keys — the
        ``{% if %}`` tokenizer already treats them as names, and this test
        pins that the inline-if condition inherits nothing WORSE than that:
        every real name is present, and no operator-glued key appears."""
        found = set(_rust.extract_template_variables("{{ a if not x or y else b }}"))
        assert {"a", "b", "x", "y"} <= found
        assert not {k for k in found if " " in k}, sorted(found)

    def test_the_arms_are_still_operands(self) -> None:
        """The arms may carry a dotted path; they must keep going through the
        operand helper, and a quoted arm is a literal, not a name."""
        found = set(_rust.extract_template_variables("{{ p.name if x > y else 'none' }}"))
        assert {"p", "x", "y"} <= found
        assert not {k for k in found if "none" in k}


class TestTheConditionRoutesThroughTheExpressionHelper:
    """A structural pin (#1125/#1859): inside the ``Node::InlineIf`` arm of
    ``extract_from_nodes``, the condition goes to ``extract_from_expression``.
    Goes red the moment the arm folds the condition back in with the operands.
    """

    @staticmethod
    def _inline_if_arm() -> str:
        parser = (
            pathlib.Path(__file__).resolve().parents[2]
            / "crates"
            / "djust_templates"
            / "src"
            / "parser.rs"
        )
        src = parser.read_text(encoding="utf-8")
        body = src.split("\nfn extract_from_nodes(", 1)[1].split("\nfn ", 1)[0]
        arms = body.split("Node::InlineIf {", 1)
        assert len(arms) == 2, "Node::InlineIf arm not found in extract_from_nodes"
        # Up to the next match arm.
        return arms[1].split("\n            Node::", 1)[0]

    def test_the_condition_goes_through_extract_from_expression(self) -> None:
        arm = self._inline_if_arm()
        assert "extract_from_expression(condition" in arm, (
            "the inline-if condition does not reach extract_from_expression; "
            f"a compound condition will be filed as one bogus key (#2745):\n{arm}"
        )


class TestThePartialRenderNoLongerEmitsStaleBytes:
    """The end-to-end half — the defect as a user sees it (the issue's table)."""

    def test_changing_only_the_right_operand_flips_the_comparison(self) -> None:
        got, want = _partial_then_reference(
            "{{ a if x > y else b }}",
            {"a": "A", "b": "B", "x": 5, "y": 10},
            {"y": 1},
        )
        assert got == want == "A"

    def test_changing_only_the_left_operand_flips_the_comparison(self) -> None:
        got, want = _partial_then_reference(
            "{{ a if x > y else b }}",
            {"a": "A", "b": "B", "x": 5, "y": 10},
            {"x": 50},
        )
        assert got == want == "A"

    def test_changing_only_one_conjunct_flips_the_boolean(self) -> None:
        got, want = _partial_then_reference(
            "{{ a if x and y else b }}",
            {"a": "A", "b": "B", "x": True, "y": False},
            {"y": True},
        )
        assert got == want == "A"

    def test_the_simple_condition_control_still_updates(self) -> None:
        """Updated correctly BEFORE the fix; if this goes red the harness is
        broken rather than the feature."""
        got, want = _partial_then_reference(
            "{{ a if x else b }}",
            {"a": "A", "b": "B", "x": False},
            {"x": True},
        )
        assert got == want == "A"
