"""Dict iteration (#2334), sequence comparison (#2335), regroup operands (#2333).

Three divergences the #2325 sink sweep found in mechanisms that PR did not
touch. All three are SILENT — no exception, no warning, no log line — and two
of them fail in the direction that hides content.

#2334 — ``{% for %}`` over a dict rendered nothing
--------------------------------------------------
``{% for key, value in mydict.items %}`` is one of the most common Django loop
idioms there is, and it rendered an empty region. Two independent gaps, both
of them "match Python's iteration protocol":

1. ``.items`` / ``.keys`` / ``.values`` are METHODS, not keys, and
   ``Context::get``'s nested walk only ever does ``obj.get(part)``. Django
   reaches them through ``Variable._resolve_lookup``'s attribute step plus its
   auto-call. Resolved now in ``Context::resolve`` — AFTER the ``get``, so a
   dict that HAS a key named ``items`` still resolves to that key's value,
   which is Django's own mapping-before-attribute order.
2. ``Node::For`` had no ``Value::Object`` arm, so a dict fell to the ``_ =>``
   wildcard and rendered ``{% empty %}``. Python iterates a dict's KEYS, which
   is the same argument #2325's string normalisation already made — so it is
   the same normalisation, one variant wider, and the loop body is shared
   rather than copied (CLAUDE.md #1646).

``{{ d|length }}``, ``{{ d|join }}`` and ``{% if k in d %}`` have agreed with
Django on a dict all along. ``{% for %}`` was the one iteration sink that did
not — which is why the fix is at the sink and not at ``{% for %}``'s caller.

#2335 — two sequences were never equal, not even to themselves
--------------------------------------------------------------
``values_equal`` had no structural arm, so ``{% if a == b %}`` over two equal
lists answered False and the template took the ``{% else %}`` branch. Same
hole in ``compare_values`` from the ordering side. Both now recurse, so the
numeric widening (``[1] == [1.0]``, ``[True] == [1]``) comes along rather than
being re-implemented.

Two things a curated table would plausibly get wrong, and which the randomised
differential below settles by running Django:

* ``[1] == (1,)`` is **False** in Python. A "both are sequences" arm would be
  wrong in exactly the direction a hand-written table is least likely to probe.
* Python walks a sequence with ``==`` and ONLY AN EQUAL PAIR CONTINUES the
  walk. ``[{}, 1] < [{}, 2]`` is True even though two dicts cannot be ordered
  — they never need to be, because they are equal. And an unequal pair
  DECIDES, including one that cannot be ordered at all (a ``TypeError`` in
  Python, False in Django, 0 here). The first draft asked "is this pair
  ordered?" first and continued on a 0, which conflates the two and falls
  through to the length tie-break: ``[[], 'a', ('b',)] > [1]`` answered True
  because three elements beat one. The randomised sweep found it in 27 of
  28,500 cells; no curated case here had the shape.

#2333 — ``{% regroup %}`` dropped a filter on its source
--------------------------------------------------------
The fourth and last operand channel. #2325 pointed the renderer's four sites at
``get_value``; ``{% regroup %}`` is a Python-side assign tag whose operand
arrives through ``RESOLVE_ARG_POSITIONS`` + a JSON hop, so it kept asking for a
variable literally NAMED ``cities|dictsort:"country"``, missing, and handing
the handler the template's own source text.

Method
------
Curated tables sample one axis and blind you on the next, so the load-bearing
assertions are randomised differentials against LIVE Django. The curated cells
that remain are doc-claim pins, one per sentence above.
"""

from __future__ import annotations

import copy
import random
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402

#: A live payload, so every cell doubles as a permissiveness probe.
XSS = "<img src=x onerror=alert(1)>"


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src).render(DjangoContext(ctx))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, ctx)


def both(src: str, ctx: dict) -> tuple[str, str]:
    """Both engines, with a raise recorded as a comparable outcome."""
    try:
        d = django_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = djust_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = both(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


# ===========================================================================
# #2334 — dict iteration
# ===========================================================================


class TestTheIssueTable:
    """Every cell the three issues quote, verbatim."""

    def test_for_over_dict_items_unpacks_key_and_value(self) -> None:
        assert_agrees(
            "{% for k, v in p.items %}{{ k }}={{ v }} {% endfor %}", {"p": {"a": 1, "b": 2}}
        )
        assert (
            djust_render("{% for k, v in p.items %}{{ k }}={{ v }} {% endfor %}", {"p": {"a": 1}})
            == "a=1 "
        )

    def test_for_over_a_bare_dict_iterates_its_keys(self) -> None:
        assert_agrees("{% for k in p %}{{ k }} {% endfor %}", {"p": {"a": 1, "b": 2}})
        assert (
            djust_render("{% for k in p %}{{ k }} {% endfor %}", {"p": {"a": 1, "b": 2}}) == "a b "
        )

    def test_keys_and_values_resolve_too(self) -> None:
        for src in (
            "{% for x in p.keys %}{{ x }} {% endfor %}",
            "{% for x in p.values %}{{ x }} {% endfor %}",
        ):
            assert_agrees(src, {"p": {"a": 1, "b": 2}})

    def test_sequence_equality_in_an_if_condition(self) -> None:
        assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": ["a"], "q": ["a"]})
        assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": ["a", "b"], "q": ["a", "b"]})

    def test_regroup_resolves_a_filtered_source(self) -> None:
        assert_agrees(
            '{% regroup p|dictsort:"k" by k as g %}{{ g|length }}', {"p": [{"k": 2}, {"k": 1}]}
        )
        assert (
            djust_render(
                '{% regroup p|dictsort:"k" by k as g %}{{ g|length }}', {"p": [{"k": 2}, {"k": 1}]}
            )
            == "2"
        )


class TestDictIterationDetails:
    """One test per claim the module docstring makes about #2334."""

    def test_a_real_items_key_still_wins_over_the_method(self) -> None:
        """Django's ``Variable._resolve_lookup`` tries mapping-item access
        FIRST and attribute access second, so the dict's own key wins.

        This is why the resolution lives after ``Context::get`` rather than
        inside its walk: putting it first would shadow the key.
        """
        for name in ("items", "keys", "values"):
            assert_agrees("{{ p.%s }}" % name, {"p": {name: 5}})
            assert djust_render("{{ p.%s }}" % name, {"p": {name: 5}}) == "5"
        assert_agrees("{% for k, v in p.items %}{{ k }}{% endfor %}", {"p": {"items": [[1, 2]]}})

    def test_iteration_order_is_pythons_insertion_order(self) -> None:
        """Not a hash order. A ``HashMap`` here would make ``{% for k in d %}``
        nondeterministic across renders of the same template and thrash the
        VDOM, so the order is asserted rather than assumed.
        """
        d = {"z": 1, "m": 2, "a": 3, "0": 4}
        assert djust_render("{% for k in p %}{{ k }}{% endfor %}", {"p": d}) == "zma0"
        # Stable across renders, which is the property that matters.
        renders = {djust_render("{% for k in p %}{{ k }}{% endfor %}", {"p": d}) for _ in range(50)}
        assert renders == {"zma0"}

    def test_the_normalisation_shares_the_whole_loop_body(self) -> None:
        """``{% empty %}``, ``reversed`` and nesting, not a parallel copy."""
        assert_agrees("{% for k in p %}{{ k }}{% empty %}E{% endfor %}", {"p": {}})
        assert_agrees("{% for k in p reversed %}{{ k }}{% endfor %}", {"p": {"a": 1, "b": 2}})
        assert_agrees(
            "{% for k, v in p.items %}{% for k2, v2 in v.items %}{{ k }}.{{ k2 }}={{ v2 }} "
            "{% endfor %}{% endfor %}",
            {"p": {"a": {"x": 1}, "b": {"y": 2}}},
        )

    def test_the_view_reaches_the_filter_channel_too(self) -> None:
        """``get_value_safe`` splits on the pipe and resolves the base through
        the same path, so a filter on a dict view is one expression, not two.
        """
        d = {"a": 1, "b": 2}
        for src in (
            "{{ p.items|length }}",
            "{{ p.keys|length }}",
            "{{ p.keys|join:',' }}",
            "{% if p.items %}Y{% else %}N{% endif %}",
        ):
            assert_agrees(src, {"p": d})
        assert_agrees("{% if p.items %}Y{% else %}N{% endif %}", {"p": {}})

    def test_a_dict_reached_through_a_filter_iterates_too(self) -> None:
        """``slice`` of a dict is the dict (Django's filter catches the
        ``TypeError``), so this cell needs the ``Value::Object`` arm even
        though the operand carries a filter.
        """
        assert_agrees(
            "{% for x in p|slice:':2' %}[{{ x }}]{% empty %}E{% endfor %}", {"p": {"a": 1}}
        )

    def test_a_non_dict_still_answers_exactly_what_it_answered(self) -> None:
        """The arm added is for ``Value::Object`` only; every other operand
        shape must be untouched.

        ``5`` is the pre-existing ``django-raised`` shape: Python raises
        ``TypeError`` for a non-iterable and djust renders ``{% empty %}``.
        Asserted as the CURRENT answer rather than as agreement, so this test
        pins that the new arm did not change it.
        """
        src = "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"
        for value in (["a", "b"], ("a", "b"), "ab", [], "", None):
            assert_agrees(src, {"p": value})
        for value in (5, 1.5):
            d, r = both(src, {"p": value})
            assert d.startswith("<<EXC TypeError")
            assert r == "E"


class TestDictIterationRandomised:
    """The load-bearing assertion for #2334.

    A randomised sweep over dicts of mixed shapes, against live Django. The
    residue is a MECHANICAL predicate — djust's answer is Django's answer for
    the same template over ``list(view)`` — not a name list, so it states
    exactly one modelling choice and cannot silently absorb a second defect.
    """

    SHAPES = [
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
        "{% for x in p.keys %}[{{ x }}]{% empty %}E{% endfor %}",
        "{% for x in p.values %}[{{ x }}]{% empty %}E{% endfor %}",
        "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}",
        "{% for x in p reversed %}[{{ x }}]{% empty %}E{% endfor %}",
        "{{ p.items|length }}",
        "{{ p.keys|length }}",
        "{{ p.values|length }}",
        "{{ p.keys|join:'-' }}",
        "{% if p.items %}Y{% else %}N{% endif %}",
        "{% with q=p.items %}[{{ q|length }}]{% endwith %}",
        # The two shapes where the list MODEL is observable: Python's view has
        # its own repr, and is not subscriptable so Django's `slice` returns it
        # unchanged. Both are in the sweep rather than only in a curated cell,
        # so the residue predicate is exercised over random dicts.
        "[{{ p.items }}]",
        "[{{ p.keys }}]",
        "{% for x in p.items|slice:':2' %}[{{ x }}]{% empty %}E{% endfor %}",
    ]
    KEYS = ["a", "b", "0", "1", "items", "keys", "values", XSS, "", "é"]
    VALUES = [1, 0, "a", "", XSS, None, True, 1.5, ["x"], {"n": 1}, ()]

    @classmethod
    def _dict(cls, rng: random.Random) -> dict:
        n = rng.randint(0, 4)
        return {k: rng.choice(cls.VALUES) for k in rng.sample(cls.KEYS, k=n)}

    def test_random_dicts_agree_with_django(self) -> None:
        rng = random.Random(2334)
        cells, unexplained = 0, []
        residue: dict[str, int] = {}
        for _ in range(400):
            d = self._dict(rng)
            for shape in self.SHAPES:
                cells += 1
                dj, du = both(shape, {"p": d})
                if dj == du:
                    continue
                kind = self._classify(shape, d, dj, du)
                if kind is None:
                    unexplained.append((shape, d, dj, du))
                else:
                    residue[kind] = residue.get(kind, 0) + 1
        assert cells == 400 * len(self.SHAPES)
        assert not unexplained, (
            f"{len(unexplained)}/{cells} disagree outside the documented residue "
            f"(residue so far: {residue}), first three: {unexplained[:3]!r}"
        )
        assert set(residue) == {"django-raised", "dict-view-modelled-as-a-list"}, (
            "the documented residue shapes are no longer the ones that occur, so "
            f"one classifier is a dead exemption: {residue}"
        )

    @classmethod
    def _classify(cls, shape: str, d: dict, dj: str, du: str) -> str | None:
        """Two documented shapes, both MECHANICAL predicates rather than name
        lists — a name list would quietly absorb a real defect.

        1. Django RAISED (a non-iterable value reached ``{% for %}``, or a
           2-unpack over something that is not a 2-sequence) and djust
           rendered the ``{% empty %}`` block. Pre-existing, unrelated to the
           dict arm, and not a permissiveness question.
        2. djust models a dict view as a plain LIST, so its answer IS Django's
           answer for the same template over ``list(view)``.
        """
        if dj.startswith("<<EXC ") and not du.startswith("<<EXC "):
            return "django-raised"
        if cls._is_the_view_model_residue(shape, d, du):
            return "dict-view-modelled-as-a-list"
        return None

    @staticmethod
    def _is_the_view_model_residue(shape: str, d: dict, djust_out: str) -> bool:
        """djust models a dict view as a plain LIST, so the only divergence
        left is exactly that substitution.

        Mechanical rather than a name list: it asserts djust's output IS
        Django's output for the same template with ``p.items`` replaced by a
        variable bound to ``list(d.items())``. A second, unrelated defect
        would not satisfy it.
        """
        for name in ("items", "keys", "values"):
            token = f"p.{name}"
            if token not in shape:
                continue
            model = {"items": list(d.items()), "keys": list(d), "values": list(d.values())}[name]
            try:
                want = django_render(shape.replace(token, "pl"), {"pl": model})
            except Exception:  # noqa: BLE001
                return False
            return djust_out == want
        return False


class TestTheDictViewModelIsAList:
    """The one deliberate modelling divergence, measured and filed (#2340).

    Python's ``dict_items`` differs from ``list(d.items())`` in exactly two
    observable ways, and both are pinned here so the exemption cannot quietly
    grow a third:

    1. ``str()`` reads ``dict_items([...])`` rather than ``[...]``.
    2. It is not subscriptable and not JSON-serializable, so Django RAISES on
       ``|first`` / ``|last`` / ``|json_script`` and returns the value
       UNCHANGED from ``|slice``.

    Modelling them faithfully needs a new ``Value`` variant threaded through
    every ``Value::List | Value::Tuple`` or-pattern in the workspace — a wide
    edit in escaping machinery, bought for a debug-only repr. Neither residue
    is more permissive than Django: djust escapes the same characters, and
    rendering a container where Django raises is the shape #2325 already
    classified and accepted.
    """

    D = {"a": 1, "b": 2}

    def test_the_container_repr_is_a_list_not_a_dict_view(self) -> None:
        for name in ("items", "keys", "values"):
            dj, du = both("{{ p.%s }}" % name, {"p": self.D})
            assert dj != du, (
                f"{{{{ p.{name} }}}} now agrees with Django — djust models the view "
                "faithfully, so delete this test and the residue predicate in "
                "TestDictIterationRandomised"
            )
            assert f"dict_{name}(" in dj
            assert du == django_render("{{ pl }}", {"pl": list(getattr(self.D, name)())})

    def test_the_view_is_indexable_where_pythons_is_not(self) -> None:
        """Django raises; djust answers. The ``django-raised`` residue shape."""
        for spec in ("first", "last"):
            dj, du = both("{{ p.items|%s }}" % spec, {"p": self.D})
            assert dj.startswith("<<EXC "), dj
            assert not du.startswith("<<EXC "), du

    def test_neither_residue_emits_anything_django_does_not(self) -> None:
        """The residue is a container repr, never an escaping difference."""
        d = {XSS: XSS}
        for name in ("items", "keys", "values"):
            du = djust_render("{{ p.%s }}" % name, {"p": d})
            assert "<img" not in du
            assert "onerror=" not in du or "&lt;img" in du


# ===========================================================================
# #2335 — sequence comparison
# ===========================================================================


class TestSequenceComparisonDetails:
    """One test per claim the module docstring makes about #2335."""

    def test_two_equal_sequences_of_the_same_kind_are_equal(self) -> None:
        for a in ([], ["a"], [1, 2], ["a", ["b"]], (1,), ("a", "b")):
            assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": a, "q": copy.deepcopy(a)})
            assert (
                djust_render(
                    "{% if p == q %}Y{% else %}N{% endif %}", {"p": a, "q": copy.deepcopy(a)}
                )
                == "Y"
            )

    def test_a_list_is_not_equal_to_a_tuple(self) -> None:
        """Python's ``[1] == (1,)`` is False. A "both are sequences" arm would
        be wrong here, and this is the cell a curated table skips.
        """
        assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": [1], "q": (1,)})
        assert djust_render("{% if p == q %}Y{% else %}N{% endif %}", {"p": [1], "q": (1,)}) == "N"

    def test_elements_widen_the_way_scalars_do(self) -> None:
        """Recursion, not element-wise ``==``: the numeric widening arms are
        reached rather than re-implemented (#2243 / #2244).
        """
        for a, b in (([1], [1.0]), ([True], [1]), ([False], [0]), ([None], [None])):
            assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": a, "q": b})
            assert djust_render("{% if p == q %}Y{% else %}N{% endif %}", {"p": a, "q": b}) == "Y"

    def test_dicts_compare_by_pairs_and_ignore_order(self) -> None:
        assert_agrees(
            "{% if p == q %}Y{% else %}N{% endif %}",
            {"p": {"a": 1, "b": 2}, "q": {"b": 2, "a": 1}},
        )
        assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": {"a": 1}, "q": {"a": 2}})

    def test_ordering_is_lexicographic(self) -> None:
        for src, ctx in (
            ("{% if p < q %}Y{% else %}N{% endif %}", {"p": [1], "q": [2]}),
            ("{% if p > q %}Y{% else %}N{% endif %}", {"p": [1, 2], "q": [1]}),
            ("{% if p < q %}Y{% else %}N{% endif %}", {"p": ["a"], "q": ["b"]}),
            ("{% if p < q %}Y{% else %}N{% endif %}", {"p": [1, 2], "q": [1, 3]}),
            ("{% if p < q %}Y{% else %}N{% endif %}", {"p": (1,), "q": (2,)}),
        ):
            assert_agrees(src, ctx)
            assert djust_render(src, ctx) == "Y"

    def test_equal_but_unorderable_elements_do_not_stop_the_walk(self) -> None:
        """Python compares with ``==`` first: ``[{}, 1] < [{}, 2]`` is True
        even though two dicts cannot be ordered.
        """
        ctx = {"p": [{}, 1], "q": [{}, 2]}
        assert_agrees("{% if p < q %}Y{% else %}N{% endif %}", ctx)
        assert djust_render("{% if p < q %}Y{% else %}N{% endif %}", ctx) == "Y"

    def test_an_unorderable_unequal_element_makes_the_whole_thing_false(self) -> None:
        """The regression the randomised sweep caught in the first draft.

        ``[[], 'a', ('b',)] > [1]`` must be False. Reading the incomparable
        first pair as a TIE falls through to the length tie-break — 3 beats 1
        — and answers True, which Django does not.
        """
        for src in (
            "{% if p > q %}Y{% else %}N{% endif %}",
            "{% if p < q %}Y{% else %}N{% endif %}",
        ):
            ctx = {"p": [[], "a", ("b",)], "q": [1]}
            assert_agrees(src, ctx)
            assert djust_render(src, ctx) == "N"

    def test_the_in_operator_shares_the_same_equality(self) -> None:
        """``in`` is the third caller of ``values_equal`` — the arm that would
        otherwise be the one this fix missed (#1646).
        """
        ctx = {"p": [1], "q": [[1], [2]]}
        assert_agrees("{% if p in q %}Y{% else %}N{% endif %}", ctx)
        assert djust_render("{% if p in q %}Y{% else %}N{% endif %}", ctx) == "Y"

    def test_scalar_comparison_is_untouched(self) -> None:
        for src, ctx in (
            ("{% if p == q %}Y{% else %}N{% endif %}", {"p": 1, "q": 1.0}),
            ("{% if p == q %}Y{% else %}N{% endif %}", {"p": "a", "q": "a"}),
            ("{% if p == q %}Y{% else %}N{% endif %}", {"p": True, "q": 1}),
            ("{% if p > q %}Y{% else %}N{% endif %}", {"p": 2, "q": 1}),
            ("{% if p == q %}Y{% else %}N{% endif %}", {"p": ["a"], "q": "a"}),
        ):
            assert_agrees(src, ctx)


class TestSequenceComparisonRandomised:
    """The load-bearing assertion for #2335.

    Randomised over nested mixed-type sequences, which is where the ordering
    rules interact — and where the first draft's tie-break bug lived, in a
    shape no curated case here has.
    """

    ATOMS = [1, 0, 2, -1, 1.0, 0.0, 2.5, True, False, None, "a", "b", "", "1", XSS]
    OPS = ["==", "!=", "<", ">", "in"]

    @classmethod
    def _value(cls, rng: random.Random, depth: int = 0):
        if depth >= 2 or rng.random() < 0.45:
            return rng.choice(cls.ATOMS)
        n = rng.randint(0, 3)
        kind = rng.choice(["list", "tuple", "dict"])
        if kind == "dict":
            keys = rng.sample(["a", "b", "c", "0"], k=min(n, 4))
            return {k: cls._value(rng, depth + 1) for k in keys}
        items = [cls._value(rng, depth + 1) for _ in range(n)]
        return items if kind == "list" else tuple(items)

    def test_random_comparisons_agree_with_django(self) -> None:
        rng = random.Random(2335)
        cells, bad, exempt = 0, [], 0
        for _ in range(1500):
            p, q = self._value(rng), self._value(rng)
            for op in self.OPS:
                cells += 1
                src = "{%% if p %s q %%}Y{%% else %%}N{%% endif %%}" % op
                dj, du = both(src, {"p": p, "q": q})
                if dj == du:
                    continue
                if self._is_the_stringified_dict_needle(op, p, q):
                    exempt += 1
                else:
                    bad.append((op, p, q, dj, du))
        assert cells == 1500 * len(self.OPS)
        assert not bad, (
            f"{len(bad)}/{cells} disagree outside the one documented exemption "
            f"({exempt} exempt), first three: {bad[:3]!r}"
        )
        assert exempt, (
            "the exemption never fired — it is a dead classifier carrying an "
            "allowance nothing needs"
        )

    @staticmethod
    def _is_the_stringified_dict_needle(op: str, p, q) -> bool:
        """The ONE documented exemption, and a mechanical predicate for it.

        ``in`` over a dict stringifies the needle before ``contains_key``, so
        ``{% if 0 in d %}`` is True for a dict keyed ``"0"`` where Python says
        False. It is not fixed here, and not because it is small: djust's wire
        format coerces every dict key to a string, so the coercion is the only
        thing keeping ``{% if pk in d %}`` working against a view's own
        ``{pk: ...}`` mapping — a behaviour ``#2221`` pins deliberately.
        Removing it moves djust towards Django for one shape and away for the
        other, which is a wire-format decision rather than a comparison fix
        (#2339).

        Narrow by construction: it fires only for ``in``, only for a dict
        right-hand side, and only where the needle's ``str()`` IS a key.
        """
        return op == "in" and isinstance(q, dict) and not isinstance(p, str) and str(p) in q

    def test_equal_but_distinct_objects_compare_equal(self) -> None:
        """A deep copy on the right-hand side, so nothing can pass by identity
        — the gate that makes the sweep above a structural test rather than an
        identity one.
        """
        rng = random.Random(23350)
        cells = 0
        for _ in range(600):
            p = self._value(rng)
            q = copy.deepcopy(p)
            assert p is not q or not isinstance(p, (list, dict))
            cells += 1
            assert_agrees("{% if p == q %}Y{% else %}N{% endif %}", {"p": p, "q": q})
        assert cells == 600


# ===========================================================================
# #2333 — the regroup operand channel
# ===========================================================================


class TestRegroupOperandChannel:
    def test_a_filtered_source_groups_the_filtered_sequence(self) -> None:
        src = (
            '{% regroup p|dictsort:"k" by k as g %}'
            "{% for grp in g %}{{ grp.grouper }}:{% for i in grp.list %}{{ i.k }}{% endfor %} "
            "{% endfor %}"
        )
        ctx = {"p": [{"k": 2}, {"k": 1}, {"k": 1}]}
        assert_agrees(src, ctx)
        assert djust_render(src, ctx) == "1:11 2:2 "

    def test_an_unfiltered_source_is_unchanged(self) -> None:
        assert_agrees("{% regroup p by k as g %}{{ g|length }}", {"p": [{"k": 2}, {"k": 1}]})

    def test_the_keyword_operands_stay_literal(self) -> None:
        """The contract that makes the pipe the guard: an arg this channel
        cannot resolve is passed through as the RAW TOKEN, which is what lets
        ``by`` / ``<attr>`` / ``as`` / ``<var>`` survive. ``get_value``'s
        literal arms have no way to say "unresolved" — they would answer
        ``Bool(true)`` for an operand spelled ``True``.
        """
        assert_agrees("{% regroup p by k as g %}{{ g|length }}", {"p": [{"k": 1}], "k": "nope"})
        assert_agrees(
            '{% regroup p|dictsort:"k" by k as g %}{{ g|length }}',
            {"p": [{"k": 2}, {"k": 1}], "k": "nope"},
        )

    def test_a_filtered_source_that_resolves_to_nothing_keeps_the_raw_token(self) -> None:
        """A miss anywhere in the chain leaves ``Value::Missing``, which is
        this channel's "did not resolve" — the same outcome an unknown bare
        name has always had, rather than a new empty-string arm.
        """
        assert djust_render(
            '{% regroup nope|dictsort:"k" by k as g %}[{{ g|length }}]', {}
        ) == django_render('{% regroup nope|dictsort:"k" by k as g %}[{{ g|length }}]', {})

    def test_chained_filters_on_the_source(self) -> None:
        src = '{% regroup p|dictsort:"k"|slice:":2" by k as g %}{{ g|length }}'
        ctx = {"p": [{"k": 3}, {"k": 1}, {"k": 2}]}
        assert_agrees(src, ctx)


# ===========================================================================
# Permissiveness
# ===========================================================================


class TestNotMorePermissiveThanDjango:
    """Four live XSSes were fixed in this machinery in one week, so the bar for
    a functional fix here is that it grants no capability Django does not.
    """

    def test_dict_keys_reaching_the_page_are_escaped(self) -> None:
        d = {XSS: "v"}
        for src in (
            "{% for k in p %}{{ k }}{% endfor %}",
            "{% for k in p.keys %}{{ k }}{% endfor %}",
            "{% for k, v in p.items %}{{ k }}{% endfor %}",
            "{{ p.keys }}",
        ):
            du = djust_render(src, {"p": d})
            assert "<img" not in du, f"{src} emitted an unescaped key: {du!r}"

    def test_the_loop_safe_key_mapping_is_not_registered_for_a_dict(self) -> None:
        """The live XSS the ``Value::Object`` arm would have opened.

        ``_collect_safe_keys`` writes a dict's paths BY NAME (``p.1``), while
        the loop mapping asserts the loop variable IS ``p.<INDEX>``. Give a
        dict a key spelled ``"1"`` whose value is ``mark_safe`` and put the
        payload at index 1: the mapping resolves the mark belonging to an
        entirely different, attacker-controlled string and emits it raw.

        Uses the production collector verbatim, not a transcription, so this
        measures the channel a real render uses.
        """
        d = {"1": mark_safe("<b>ok</b>"), XSS: "v"}
        safe_keys = _collect_safe_keys(d, "p")
        assert "p.1" in safe_keys, (
            "the premise is gone: the collector no longer writes a dict path by "
            f"name, so this test proves nothing. got {safe_keys!r}"
        )
        out = _rust.render_template_with_dirs(
            "{% for k in p %}[{{ k }}]{% endfor %}", {"p": d}, [], safe_keys
        )
        assert "<img" not in out, f"a dict KEY was emitted unescaped: {out!r}"
        assert "&lt;img" in out, out

    def test_the_same_holds_for_a_string_operand(self) -> None:
        """A string cannot collide the same way — ``_collect_safe_keys`` never
        descends into a ``str`` — but the ``item IS <iterable>.<index>``
        correspondence is just as false, so one condition excludes both
        normalised shapes.
        """
        out = _rust.render_template_with_dirs(
            "{% for c in p %}[{{ c }}]{% endfor %}", {"p": "<>"}, [], ["p.0", "p.1"]
        )
        assert out == "[&lt;][&gt;]", out

    def test_a_genuine_list_keeps_its_item_safety(self) -> None:
        """The gate is on NORMALISATION, not on loops generally: a real list
        operand must keep resolving its per-item marks, or this fix would have
        broken #2287 while closing #2334.
        """
        out = _rust.render_template_with_dirs(
            "{% for x in p %}[{{ x }}]{% endfor %}",
            {"p": [mark_safe("<b>a</b>"), "<b>b</b>"]},
            [],
            ["p.0"],
        )
        assert out == "[<b>a</b>][&lt;b&gt;b&lt;/b&gt;]", out

    def test_a_filtered_regroup_source_is_not_emitted_live(self) -> None:
        d = [{"k": XSS}]
        out = djust_render(
            '{% regroup p|dictsort:"k" by k as g %}{% for grp in g %}{{ grp.grouper }}{% endfor %}',
            {"p": d},
        )
        assert "<img" not in out, out

    def test_the_sweep_of_hostile_dicts_grants_nothing_django_does_not(self) -> None:
        """A randomised permissiveness probe rather than a table: for every
        cell, any live fragment djust emits must also be in Django's output.
        """
        rng = random.Random(233435)
        payloads = [XSS, "</script><script>alert(1)</script>", '" onmouseover="x']
        fragments = ["<img", "onerror=", "<script", "</script", ' onmouseover="']
        shapes = TestDictIterationRandomised.SHAPES
        cells, leaks = 0, []
        for _ in range(150):
            d = {
                rng.choice(payloads + ["a", "1"]): rng.choice(payloads + [1, None, ["x"]])
                for _ in range(rng.randint(1, 3))
            }
            for shape in shapes:
                cells += 1
                dj, du = both(shape, {"p": d})
                extra = {f for f in fragments if f in du} - {f for f in fragments if f in dj}
                if extra:
                    leaks.append((shape, d, sorted(extra), dj, du))
        assert cells == 150 * len(shapes)
        assert not leaks, f"{len(leaks)}/{cells} more permissive, first: {leaks[:2]!r}"


# ===========================================================================
# Structural pins
# ===========================================================================


class TestEveryOperandChannelIsAccountedFor:
    """#2325 pinned the renderer's four sites; this pins the fifth.

    Mechanical, not a prose claim: the assign-tag / block-custom-tag channel
    has exactly ONE operand resolver, and it is filter-aware. A future tag
    that grows its own ``context.get`` for an operand fails this.
    """

    RENDERER = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "renderer.rs"
    )

    def test_resolve_tag_arg_delegates_every_lookup_to_one_resolver(self) -> None:
        src = self.RENDERER.read_text()
        start = src.index("fn resolve_tag_arg(")
        body = src[start : src.index("\nfn ", start + 10)]
        assert "context.get(" not in body, (
            "resolve_tag_arg looks up the context directly again — the #2333 fix "
            "routes every operand through resolve_tag_operand so the filter "
            "chain cannot be dropped at one of its branches"
        )
        assert body.count("resolve_tag_operand(") == 2, (
            "resolve_tag_arg has exactly two operand branches (bare and the "
            "`key=value` right-hand side); a third that does not delegate is "
            "the #1646 drift this pin exists for"
        )

    def test_the_operand_resolver_is_filter_aware(self) -> None:
        src = self.RENDERER.read_text()
        start = src.index("fn resolve_tag_operand(")
        body = src[start : src.index("\n}", start)]
        assert "get_value(" in body, body


class TestTheCorpusGapsThatHidTheseFromTheDifferential:
    """``scripts/filter-parity-differential.py`` reported clean over all of
    #2334 and #2335, for the third time in the same shape.

    Its tag axis writes ``p|<filter>`` as every operand, so it built no dotted
    path and nothing that iterated a dict without a filter in the way; and its
    ``{% if %}`` cells bind only ``p``, so it could not construct a comparison
    at all. Both axes exist now; this pins that they still do, because a
    corpus gap is silent by construction.
    """

    SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"

    def test_the_corpus_builds_a_dict_view_path_cell(self) -> None:
        src = self.SCRIPT.read_text()
        for fragment in ("{% for k, v in p.items %}", "{% for x in p.keys %}", "{{ p.items }}"):
            assert fragment in src, (
                f"the differential builds no {fragment!r} cell — a dotted path "
                "ending in a dict method is a third resolution shape and #2334 "
                "lived entirely in the part it did not measure"
            )

    def test_the_corpus_builds_a_two_operand_comparison_cell(self) -> None:
        src = self.SCRIPT.read_text()
        assert "CMP_OPS" in src and '"q": copy.deepcopy(' in src, (
            "the differential binds no second operand, so values_equal and "
            "compare_values are unmeasured — which is how #2335 shipped"
        )
        for op in ("==", "!=", "<", ">", "<=", ">=", "in"):
            assert f'"{op}"' in src, f"the comparison axis is missing {op!r}"

    def test_the_corpus_carries_a_dict_with_hostile_keys(self) -> None:
        src = self.SCRIPT.read_text()
        assert "d-hostile-key" in src, (
            "every dict input has tame keys, so no cell can show a dict-KEY "
            "escaping defect — and the key is what {% for k in d %} emits"
        )


class TestKnownAdjacentDivergencesNotFixedHere:
    """Scope discipline (#1079): found, measured, filed — not fixed.

    Pinned so the exclusion cannot quietly become a blind spot, and so whoever
    fixes one is told to delete the pin.
    """

    def test_an_incomparable_pair_still_answers_true_for_le_and_ge(self) -> None:
        """``compare_values`` collapses "incomparable" to 0, so ``<=`` and
        ``>=`` read it as "equal" and answer True where Django answers False.

        Pre-existing for EVERY incomparable pair (``{% if "a" >= 1 %}`` has
        always done this), measured identically on both builds of the #2334 /
        #2335 differential — 842 cells, unchanged. ``try_compare`` now makes
        the fix small; tracked at #2338 rather than widened into a
        sequence-comparison PR.
        """
        for src in (
            "{% if p >= q %}Y{% else %}N{% endif %}",
            "{% if p <= q %}Y{% else %}N{% endif %}",
        ):
            d, r = both(src, {"p": "a", "q": 1})
            assert (d, r) == ("N", "Y"), (
                f"{src} on a string-vs-int pair now agrees — the incomparable-pair "
                "collapse is fixed, so delete this test and close #2338"
            )
            # And it is the same answer for a sequence pair, which is the arm
            # this PR added: no NEW shape of it was introduced.
            d, r = both(src, {"p": [1], "q": (1,)})
            assert (d, r) == ("N", "Y")

    def test_in_over_a_dict_still_stringifies_its_needle(self) -> None:
        """The second arm of ``in``, and the one this PR did NOT change.

        Python's ``0 in {"0": 1}`` is False. djust answers True because it
        stringifies the needle before ``contains_key`` — and that coercion is
        load-bearing for djust specifically: its wire format turns every dict
        key into a string, so a view holding ``{1234567: "x"}`` arrives as
        ``{"1234567": …}`` and the coercion is what keeps
        ``{% if pk in d %}`` resolving. ``#2221``'s
        ``test_localization_does_not_reach_dict_lookup_keys`` pins exactly
        that.

        So removing it moves djust TOWARDS Django for a string-keyed dict and
        AWAY from it for an int-keyed one. That is a decision about the wire
        format, not about comparison, and it is tracked at #2339 rather than
        made in a sequence-equality PR (#1079).
        """
        for needle, d in ((0, {"0": 1}), (1.0, {"1.0": 1}), (None, {"None": 1})):
            dj, du = both("{% if p in q %}Y{% else %}N{% endif %}", {"p": needle, "q": d})
            assert (dj, du) == ("N", "Y"), (
                f"in-over-a-dict on {needle!r} now agrees with Django — #2339 has "
                "been decided, so delete this test and its sweep exemption"
            )
        # The sequence arm of the same operator DOES go through values_equal,
        # which is what makes these two arms worth telling apart.
        assert_agrees("{% if p in q %}Y{% else %}N{% endif %}", {"p": [1], "q": [[1]]})
