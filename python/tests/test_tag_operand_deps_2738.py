"""#2738 — a filter ARGUMENT inside a TAG operand must reach that node's deps.

``{{ }}`` was already sound: the parser splits the variable from its filter
chain into separate ``Node::Variable`` fields, and ``extract_from_nodes``
routes each filter argument through ``extract_from_filter_arg``. Every TAG
operand (``{% if %}`` condition, ``{% for %}`` iterable, ``{% with %}`` value,
``{% widthratio %}``, ``{% cycle %}``, ``{% firstof %}``, …) was instead handed
to the extractor as a RAW string still carrying its filter chain, and was split
on ``.`` only — or, for ``{% if %}``, tokenized on a separator set containing
``|`` but not ``:``.

Two consequences, both silent:

* the filter ARGUMENT never entered the dependency set, so
  ``render_nodes_partial`` skipped a node whose only changed dependency was
  that name — the node kept its cached HTML and the render emitted STALE
  bytes, with no error (Django semantics have no "missing dependency" signal);
* for ``{% for %}`` and ``{% with %}`` the whole operand became ONE bogus key
  (``items|slice:n``), so even the BASE variable was untracked.

The cure converges tag-operand extraction onto the renderer's own splitter,
``filter_lexer::split_pipes`` — the quote-aware split ``renderer::get_value_safe``
already uses to RESOLVE these operands — so the analysis splits an operand
exactly the way the renderer resolves it (#1646).

**Scope, stated because the framing is easy to overstate.** What is retired is
operand FILTER CHAINS. A compound EXPRESSION in an inline-if condition —
``{{ a if x > y else b }}`` — still files ``x > y`` as one key and loses both
names, because a compound expression needs ``extract_from_expression`` (what
``{% if %}``'s condition uses) rather than the operand splitter. That is
pre-existing rather than introduced here, and is tracked at **#2745**; the tests
below deliberately do not claim it.
"""

from __future__ import annotations

import re

import pytest

from djust import _rust


def _body(html: str) -> str:
    """The rendered body, so the two entry points compare on content alone."""
    return html.split('<body dj-id="2">', 1)[-1].split("</body>", 1)[0]


def _partial_then_reference(source: str, initial: dict, change: dict) -> tuple[str, str]:
    """Render, mutate ONLY *change*, re-render through the partial path.

    Returns ``(what the partial render produced, what a fresh view produces)``.
    Both go through ``render_with_diff`` so the wrapper markup is identical and
    only the body can differ.
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


class TestTheFilterArgumentReachesTheDependencySet:
    """The extraction half: the real names in, the bogus key out."""

    @pytest.mark.parametrize(
        ("source", "wanted", "unwanted"),
        [
            # {% if %} — the condition is tokenized; `|` used to split and `:`
            # did not, so `default:b` survived as one bogus token.
            ("{% if a|default:b %}x{% endif %}", {"a", "b"}, {"default:b"}),
            # {% for %} — the iterable was split on `.` only, so the whole
            # operand became one key and BOTH real names were lost.
            (
                "{% for x in items|slice:n %}{{ x }}{% endfor %}",
                {"items", "n"},
                {"items|slice:n"},
            ),
            # {% with %} — same shape as {% for %}.
            (
                "{% with v=y|default:z %}{{ v }}{% endwith %}",
                {"y", "z"},
                {"y|default:z"},
            ),
            # A dotted filter argument must keep its ROOT, not the dotted text.
            (
                "{% if a|default:b.c %}x{% endif %}",
                {"a", "b"},
                {"default:b.c", "b.c"},
            ),
            # A filter chain: every argument in the chain counts.
            (
                "{% if a|default:b|add:c %}x{% endif %}",
                {"a", "b", "c"},
                {"default:b", "add:c"},
            ),
        ],
    )
    def test_a_tag_operand_contributes_its_filter_arguments(
        self, source: str, wanted: set, unwanted: set
    ) -> None:
        found = set(_rust.extract_template_variables(source))
        assert wanted <= found, f"{sorted(wanted - found)} missing from {sorted(found)}"
        assert not (unwanted & found), f"bogus keys {sorted(unwanted & found)} in {sorted(found)}"

    def test_the_variable_path_was_already_sound_and_stays_sound(self) -> None:
        """The control. ``{{ }}`` never had the defect; it must not acquire one.

        This is what makes the parametrised cases above non-vacuous: the same
        ``|default:`` spelling resolved correctly here throughout, which is how
        the defect was isolated to tag operands rather than to filters.
        """
        found = set(_rust.extract_template_variables("{{ x|add:y|default:z }}"))
        assert {"x", "y", "z"} <= found

    def test_a_quoted_filter_argument_is_not_a_dependency(self) -> None:
        """A literal is not a name. Routing through the renderer's splitter must
        not start treating ``"none"`` as a context key."""
        found = set(_rust.extract_template_variables('{% if a|default:"none" %}x{% endif %}'))
        assert "a" in found
        assert not {k for k in found if "none" in k}

    def test_a_filtered_iterable_transfers_body_paths_to_its_base_name(self) -> None:
        """The SECOND mechanism, independently reachable (#1125/#2135).

        ``Node::For`` also transfers the loop variable's dotted paths onto the
        iterable, and that transfer did its own ``split('.')`` on the RAW
        operand — so ``rows|slice:':5'`` became the key the paths were filed
        under. Two consequences: the body's paths never reached ``rows``, and a
        bogus key was minted a second time, by a different line than the one
        the cases above cover.

        Gate-off: removing only the pipe-strip in the transfer block leaves the
        cases above green (they assert the extractor's own output) and fails
        this one.
        """
        found = _rust.extract_template_variables(
            "{% for r in rows|slice:':5' %}{{ r.name }}{% endfor %}"
        )
        assert "name" in found.get("rows", []), (
            f"the loop body's path was not transferred to `rows`: {found}"
        )
        assert not [k for k in found if "|" in k], f"bogus key still minted: {sorted(found)}"

    def test_a_pipe_inside_a_quoted_filter_argument_does_not_split(self) -> None:
        """``split_pipes`` is quote-aware; ``str::split('|')`` was not.

        ``{% if a|cut:"x|y" %}`` is ONE filter whose argument contains a pipe,
        not two filters.

        Asserted as an EXACT set, deliberately. The weaker "no key contains a
        pipe, and neither `x` nor `y` is a key" spelling passed *before* the
        fix too: the pipe-splitting tokenizer produced the fragments ``cut:"x``
        and ``y"``, neither of which contains a `|` and neither of which is
        exactly ``x`` or ``y``. It looked like a regression test and was a
        tautology (#1200/#2233).
        """
        found = set(_rust.extract_template_variables('{% if a|cut:"x|y" %}t{% endif %}'))
        assert found == {"a"}, f"expected exactly {{'a'}}, got {sorted(found)}"


class TestEveryOperandSiteRoutesThroughTheOneHelper:
    """A structural pin, not a count (#1125/#1859).

    A count would still pass if a NEW site were added calling the old
    primitive while an existing one were removed. The invariant that actually
    matters is mechanical and absolute: inside ``extract_from_nodes`` — the
    walk over template nodes — nothing calls ``extract_from_variable``
    directly any more. Every operand goes through ``extract_from_operand``,
    which is the one place the filter chain is split.

    This goes red the moment someone adds a node arm that reaches for the old
    primitive, which is exactly how the defect got in.
    """

    @staticmethod
    def _extract_from_nodes_body() -> str:
        import pathlib

        parser = (
            pathlib.Path(__file__).resolve().parents[2]
            / "crates"
            / "djust_templates"
            / "src"
            / "parser.rs"
        )
        src = parser.read_text(encoding="utf-8")
        after = src.split("\nfn extract_from_nodes(", 1)
        assert len(after) == 2, "extract_from_nodes not found — did the fn get renamed?"
        # Up to the next top-level `fn `, which is the end of this function.
        return after[1].split("\nfn ", 1)[0]

    def test_no_operand_site_calls_the_unsplit_primitive(self) -> None:
        body = self._extract_from_nodes_body()
        assert "extract_from_operand(" in body, "the helper is not called at all — wrong body?"
        offenders = [
            line.strip()
            for line in body.splitlines()
            if "extract_from_variable(" in line and not line.strip().startswith("//")
        ]
        assert not offenders, (
            "these operand sites bypass extract_from_operand and will lose a "
            f"filter argument (#2738): {offenders}"
        )

    def test_the_if_tokenizer_does_not_split_on_the_filter_pipe(self) -> None:
        """`|` back in the separator set is the other way the class returns."""
        import pathlib

        parser = (
            pathlib.Path(__file__).resolve().parents[2]
            / "crates"
            / "djust_templates"
            / "src"
            / "parser.rs"
        )
        src = parser.read_text(encoding="utf-8")
        body = src.split("\nfn extract_from_expression(", 1)[1].split("\nfn ", 1)[0]
        # The separator SET is the string literal handed to `.contains(c)`.
        # Match only that literal: the same line carries `|c: char|` (closure
        # syntax) and `||` (logical or), and a naive substring check on the
        # whole line reports those as separators. Asking the question about
        # the literal is what makes this pin mean what it says.
        seps = re.findall(r'"([^"]*)"\.contains\(c\)', body)
        assert seps, "separator set not found in extract_from_expression"
        assert all("|" not in literal for literal in seps), (
            f"`|` is a separator again, so a filter chain is torn apart (#2738): {seps}"
        )


class TestThePartialRenderNoLongerEmitsStaleBytes:
    """The end-to-end half — the defect as a user sees it.

    Each case changes ONLY the filter argument, so the node re-renders only if
    that name reached its dependency set.
    """

    def test_if_condition_filter_argument(self) -> None:
        got, want = _partial_then_reference(
            "{% if a|default:b %}Y{% else %}N{% endif %}",
            {"a": None, "b": ""},
            {"b": "NEW"},
        )
        assert got == want == "Y"

    def test_for_iterable_filter_argument(self) -> None:
        got, want = _partial_then_reference(
            "{% for x in items|slice:n %}{{ x }}{% endfor %}",
            {"items": [1, 2, 3], "n": ":1"},
            {"n": ":3"},
        )
        assert got == want == "123"

    def test_with_value_filter_argument(self) -> None:
        got, want = _partial_then_reference(
            "{% with v=y|default:z %}{{ v }}{% endwith %}",
            {"y": None, "z": "OLD"},
            {"z": "NEW"},
        )
        assert got == want == "NEW"

    def test_for_iterable_base_variable_is_tracked_too(self) -> None:
        """``{% for x in items|slice:n %}`` lost ``items`` as well as ``n``.

        Changing the BASE must also re-render — a case the cited report's three
        rows did not cover, because they all varied the argument.
        """
        got, want = _partial_then_reference(
            "{% for x in items|slice:n %}{{ x }}{% endfor %}",
            {"items": [1, 2, 3], "n": ":3"},
            {"items": [7, 8, 9]},
        )
        assert got == want == "789"

    def test_the_variable_path_control_still_updates(self) -> None:
        """The non-vacuity sibling of the four above.

        ``{{ a|default:b }}`` updated correctly BEFORE the fix. If this ever
        goes red, the harness is broken rather than the feature — which is what
        made it usable as the control during diagnosis.
        """
        got, want = _partial_then_reference("{{ a|default:b }}", {"a": None, "b": ""}, {"b": "NEW"})
        assert got == want == "NEW"
