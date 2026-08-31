"""The differential corpus can hold an unpicklable and a non-literal row (#2482).

Two consumers of ``scripts/filter-parity-differential.py``'s ``INPUTS`` each
imposed a constraint on what a row may BE, and between them two shapes of
#2466's class were unrepresentable — not "not added yet", but impossible:

1. ``measure``'s ``@cmp`` axis DEEP-COPIES the second operand. That is correct
   and load-bearing: two structurally-equal operands that are not the same
   object is the whole of what ``values_equal`` / ``try_compare`` needed
   (#2335), because Python's ``==`` answers True on identity alone for a list.
   But all three dict VIEWS raise ``TypeError: cannot pickle 'dict_keys'
   object`` under ``deepcopy``, so an empty ``dict_keys`` could not be a corpus
   row at all — and was therefore swept NOWHERE: not on the ``@cmp`` axis it
   breaks, and not on the twenty-odd axes it would have been fine on.
2. ``test_sequence_op_chokepoint_2451.corpus()`` re-evaluates each value from
   the AST.

``INPUTS_LAZY`` answers (1): a row spelled as a zero-argument FACTORY gives
``p`` and ``q`` two independently-constructed objects BY CONSTRUCTION, which is
the property the copy exists to provide and which holds for a ``dict_keys``.
``fresh(key)`` is the one chokepoint — factory row built again, every other row
still deep-copied — so no caller has to decide, and the two spellings cannot
drift apart.

(2) turned out to be **narrower than #2482 states**, and the difference is the
finding rather than a footnote: ``eval`` injects ``__builtins__`` into a globals
mapping that has none, so the AST reader evaluates any self-contained
expression — ``type("C", (), {...})()`` included. What it cannot evaluate is a
reference to a name the SCRIPT defines. So the ``("str-fallback", "falsy")``
exemption, whose stated reason was that a class instance "cannot be a row here
at all", was **wrong rather than stale**, and is deleted.

The canary this file is built around: a row the pre-#2482 harness cannot hold,
held by the new one, producing a real measurement — and the reader-parity pin
that keeps the two AST consumers from sweeping a strict subset of the corpus
the differential sweeps (#1646, one level up).
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import pathlib
import sys

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "filter-parity-differential.py"

#: The rows that exist only because the factory mapping does.
FACTORY_ROWS = ("dv-keys-empty", "dv-keys-plain", "o-falsy-iter")


def _differential():
    """The differential, imported for its corpus and its `fresh` chokepoint.

    Imported rather than AST-read, unlike the two readers this file pins: the
    claims here are about the RUNTIME corpus — what `fresh` returns, what
    `deepcopy` refuses — and an AST copy of the module would be a second corpus
    that could disagree with the one `measure` actually sweeps.
    """
    spec = importlib.util.spec_from_file_location("djust_differential_2482", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def diff():
    return _differential()


class TestTheRowThePreviousHarnessCouldNotHold:
    """The empirical canary (#1459): the same row, refused then measured."""

    @pytest.mark.parametrize("view", [{}.keys(), {}.values(), {}.items()], ids=type)
    def test_every_dict_view_is_refused_by_deepcopy(self, view: object) -> None:
        """The limit itself, run rather than cited.

        This is the exact call the `@cmp` axis made before #2482
        (`copy.deepcopy(INPUTS[kb])`), so a row of this shape did not merely
        measure nothing — it ABORTED the sweep at the first `@cmp` cell.
        """
        with pytest.raises(TypeError, match="cannot pickle"):
            copy.deepcopy(view)

    def test_fresh_builds_the_row_the_copy_refuses(self, diff) -> None:
        """...and the new chokepoint returns a second, independent object."""
        first, second = diff.fresh("dv-keys-empty"), diff.fresh("dv-keys-empty")
        assert first is not second
        assert first == second
        # The property the deep copy existed to provide, stated directly: `p`
        # from the corpus and `q` from the factory are never the same object,
        # so an identity-based `==` is distinguishable from a structural one.
        assert diff.INPUTS["dv-keys-empty"] is not diff.fresh("dv-keys-empty")

    def test_the_row_produces_a_real_cmp_MEASUREMENT(self, diff) -> None:
        """The half a "it no longer raises" assertion would miss.

        A row that crosses to Rust and produces an exception on both sides is
        also "held" by the harness in the weak sense. So this renders the
        actual `@cmp` template through BOTH engines and asserts each answers
        `Y` or `N` — a comparable cell, which is what the corpus is for.
        """
        source = "{% if p == q %}Y{% else %}N{% endif %}"
        ctx = {"p": diff.INPUTS["dv-keys-empty"], "q": diff.fresh("dv-keys-empty")}
        dj = DjangoTemplate(source).render(DjangoContext(dict(ctx)))
        du = diff._rust.render_template(source, ctx)
        assert dj in {"Y", "N"}, dj
        assert du in {"Y", "N"}, du
        # And they DISAGREE, which is the divergence the row exists to expose:
        # Python compares two empty views as equal sets; djust does not.
        assert dj == "Y", "Python compares two empty dict views EQUAL"
        assert dj != du, (
            "the empty dict-view `==` divergence is closed — delete this "
            "assertion and the issue it tracks"
        )

    def test_the_cmp_axis_goes_through_the_chokepoint(self) -> None:
        """A source pin, because the functional tests above cannot see it.

        `fresh()` could be correct and unused: `measure` could still call
        `copy.deepcopy(INPUTS[kb])` and every assertion here would pass while
        the sweep aborted on the first dict-view cell.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        assert '{"p": INPUTS[ka], "q": fresh(kb)}' in source
        assert "copy.deepcopy(INPUTS[kb])" not in source


class TestFreshIsOneChokepointAndNotTwo:
    """`fresh` must serve EVERY row, not just the ones it was added for."""

    def test_a_non_factory_row_is_still_deep_copied(self, diff) -> None:
        """The behaviour #2335 needs, unchanged for the rows that admit it.

        A list is deep-copied, so the nested element is a different object too
        — which is what makes `{% if p == q %}` a structural comparison rather
        than an identity one.
        """
        original = diff.INPUTS["l-nested"]
        built = diff.fresh("l-nested")
        assert built == original
        assert built is not original
        assert built[1] is not original[1], "a SHALLOW copy would share the sublist"

    def test_every_corpus_row_can_be_freshened(self, diff) -> None:
        """The sweep calls `fresh` for every `kb` in `INPUTS`, so a row it
        cannot build is an abort rather than a missing cell."""
        for key in diff.INPUTS:
            built = diff.fresh(key)
            assert built is not None or diff.INPUTS[key] is None, key

    def test_a_factory_row_is_never_deep_copied(self, diff) -> None:
        """Non-vacuity for the split: the factory branch must be the one that
        runs for these three, or `fresh` is just `deepcopy` with extra steps."""
        assert set(FACTORY_ROWS) <= set(diff.INPUTS_LAZY)
        for key in FACTORY_ROWS:
            assert key in diff.INPUTS, f"{key} never reached the eager mapping"
        # The two dict-view rows are the proof: `deepcopy` REFUSES them, so a
        # `fresh` that fell through to the copy branch would raise here.
        for key in ("dv-keys-empty", "dv-keys-plain"):
            with pytest.raises(TypeError, match="cannot pickle"):
                copy.deepcopy(diff.INPUTS[key])
            assert diff.fresh(key) is not diff.INPUTS[key]


class TestTheASTReadersSeeTheWholeCorpus:
    """The drift this mapping could have introduced, pinned (#1646).

    `INPUTS_LAZY` is a SECOND mapping, and the failure mode of a second mapping
    is that some consumer reads only the first. Both AST readers are checked
    against the runtime corpus by KEY SET, so a reader that stopped at the
    literal sweeps a strict subset and fails here rather than silently
    measuring less.
    """

    def test_the_2451_chokepoint_reader_builds_every_row(self, diff) -> None:
        sys.path.insert(0, str(REPO / "python" / "tests"))
        try:
            import test_sequence_op_chokepoint_2451 as chokepoint
        finally:
            sys.path.pop(0)
        assert set(chokepoint.corpus()) == set(diff.INPUTS)

    def test_the_literal_reader_and_the_factory_mapping_partition_the_corpus(self, diff) -> None:
        """The other reader (`_differential_literal` in the escape-chain file)
        reads the LITERAL only, and that stays correct — but only because the
        literal and the factory mapping partition the corpus exactly. A row in
        both would be built twice and a row in neither would be invisible.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        literal_keys = {
            ast.literal_eval(k)
            for node in tree.body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "INPUTS"
            for k in node.value.keys
        }
        lazy_keys = set(diff.INPUTS_LAZY)
        assert not (literal_keys & lazy_keys), sorted(literal_keys & lazy_keys)
        assert literal_keys | lazy_keys == set(diff.INPUTS)


class TestTheExemptionsStatedReasonWasFalse:
    """#2482's second premise, falsification-tested (#1516/#1867).

    The deleted exemption claimed a user-defined class instance "cannot be a
    row here at all" because `corpus()` evaluates values in a three-name
    namespace. Verifying the citation was not enough — the citation was exact
    and the claim it made about the world was wrong.
    """

    def test_an_ast_eval_in_a_names_only_namespace_builds_a_class_instance(self) -> None:
        node = ast.parse('type("C", (), {"__bool__": lambda self: False})()').body[0]
        env: dict = {}
        built = eval(  # noqa: S307 — a literal written in this test
            compile(ast.Expression(node.value), "<probe>", "eval"), env
        )
        assert not built
        assert type(built).__name__ == "C"
        # ...because `eval` supplies builtins to a globals mapping without them.
        assert "__builtins__" in env

    def test_the_exemption_came_BACK_for_a_different_reason(self, diff) -> None:
        """#2482 deleted the exemption; #2477/#2489 restored it, and the two
        reasons are not the same claim.

        #2482's finding stands: a user-defined class instance CAN be a corpus
        row, and the deleted exemption said otherwise. What changed is the ARM
        — `opaque_value` claims a falsy `__iter__` class now, so the row that
        inhabited `str-fallback:falsy` inhabits `opaque_value:falsy`, and the
        member is uninhabited again because every remaining shape on that arm
        is one a corpus row cannot BE.

        Asserted as the pair it is: the old (false) reason is still absent from
        `VALUE_TRUTHINESS_ONE_ANSWER`, and the new one is a whole-arm exemption
        with a reason, which the manifest checks for staleness on every run.
        """
        assert ("str-fallback", "falsy") not in diff.VALUE_TRUTHINESS_ONE_ANSWER
        assert "str-fallback" in diff.VALUE_TRUTHINESS_NOT_INHABITABLE
        reason = diff.VALUE_TRUTHINESS_NOT_INHABITABLE["str-fallback"]
        assert "one-shot" in reason and "cap" in reason, reason
        # `_required_value_truthiness` still LISTS the member — the exemption
        # is subtracted downstream, which is what makes a stale one reportable
        # rather than invisible. So the assertion is on the exempt set.
        for member in ("value:str-fallback:falsy", "arg:str-fallback:falsy"):
            assert member in diff.VALUE_TRUTHINESS_EXEMPT
            assert member not in diff._swept_value_truthiness()

    def test_the_row_still_inhabits_a_member_and_it_is_the_new_one(self, diff) -> None:
        """The half that keeps the row from becoming decorative.

        A row whose member is exempt covers nothing, and a corpus row that
        covers nothing is one nobody would notice deleting. This one is not:
        it lands on `opaque_value`, is falsy, and is what the `#2477` canary in
        `test_differential_reachability_manifest_2345.py` had to account for
        when its gap shrank from four members to two.
        """
        value = diff.INPUTS["o-falsy-iter"]
        assert diff._no_variant_outcome(value) == "opaque_value"
        assert not value
        assert "value:opaque_value:falsy" in diff._swept_value_truthiness()


class TestTheFalsyIterableRowIsTheShape2466Declined:
    """What the row IS, asserted rather than trusted from its name.

    Every property here is one the conversion branches on, so a future edit
    that made the object merely falsy — or gave it a `__len__`, or a public
    attribute — would move it to a different arm and quietly stop inhabiting
    the member it was added for.
    """

    def test_it_is_falsy_iterable_and_unsized(self, diff) -> None:
        value = diff.INPUTS["o-falsy-iter"]
        assert not value
        assert list(value) == ["<img src=x onerror=alert(1)>"]
        with pytest.raises(TypeError, match="has no len"):
            len(value)  # type: ignore[arg-type]
        # No public attribute, so the `__dict__` bulk-dump arm ABOVE
        # `opaque_value` does not claim it — that would make it a
        # `Value::Object` and a different question (#2478).
        assert not [k for k in vars(value) if not k.startswith("_")]

    def test_its_text_is_stable_and_is_not_tag_shaped(self, diff) -> None:
        """The default `object.__repr__` carries a memory address, so every
        cell rendering this row would differ between two runs of the same
        build and `--compare` would report a corpus-wide regression it caused
        itself. And a repr shaped like a tag would match `UNESCAPED_TAG` and
        report itself as a live fragment."""
        value = diff.INPUTS["o-falsy-iter"]
        assert str(value) == "FalsyIterable()"
        assert repr(value) == "FalsyIterable()"
        assert not diff.UNESCAPED_TAG.search(str(value))
        assert str(diff.INPUTS_LAZY["o-falsy-iter"]()) == str(value)

    def test_its_module_is_pinned_so_the_readers_agree(self, diff) -> None:
        """`type()` fills `__module__` from the calling frame's `__name__`,
        which an AST reader's namespace does not have — so without this pin the
        reader-built class has NO `__module__` and the script-built one has
        `"__main__"`. That is not cosmetic: it made
        `normalize_django_value` raise `AttributeError: __module__` for one and
        render for the other.
        """
        assert type(diff.INPUTS["o-falsy-iter"]).__module__ == "djust_differential_corpus"

    def test_the_class_is_the_factory_so_two_instances_share_a_type(self, diff) -> None:
        """A `lambda: type(...)()` spelling would build a NEW CLASS per call,
        so `p` and `q` would differ by TYPE rather than by identity — which is
        a different comparison from the one the `@cmp` axis asks."""
        first, second = diff.fresh("o-falsy-iter"), diff.fresh("o-falsy-iter")
        assert first is not second
        assert type(first) is type(second)
        assert type(first) is type(diff.INPUTS["o-falsy-iter"])
