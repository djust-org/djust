"""Two opaque ``Value::Encoded``s compare by Python's CONTRACT (#2480).

The divergence
--------------
``opaque_value`` set ``cmp_key: None``, so ``Encoded::python_partial_cmp``
answered ``None`` for every pair either side of which came from that arm —
never equal, never ordered::

    {% if p == q %}   p = q = set()          django Y    djust N
    {% if p == q %}   two equal {'a'}s       django Y    djust N
    {% if p <= q %}   p = q = set()          django Y    djust N
    {% if p == q %}   p = q = complex(0)     django Y    djust N

#2476 moved the FALSY half off ``Value::String`` (where two ``set()``s had
compared equal by TEXT, Django's answer reached by accident) and #2477/#2489
moved the TRUTHY half, so the class this closes is **eight shapes**, four
already wrong and four widened into it — the accounting
``test_opaque_collections_2477_2489.py::TestTheComparisonAxisThisWIDENS``
carried until this fix deleted its rows.

The rule, and why no single carried field is it
-----------------------------------------------
Nothing already on the struct decides it, in EITHER direction — measured, in
``test_no_single_carried_field_could_have_decided_this``:

* ``set() == frozenset() == {}.keys() == {}.items()`` is True ACROSS
  ``type_name``s;
* ``LenZero() == LenZero()`` on two DISTINCT instances is False WITHIN one;
* ``set() == {}.values()`` is **False** while ``set() == {}.keys()`` is True,
  and both views carry the same (empty) ``items``.

So a fourth fact is measured at the conversion — ``Encoded::eq_class``, the
PROTOCOL Python itself dispatches on:

===========================  ===============================================
``EqClass::Set``             ``isinstance(o, collections.abc.Set)``.  The ABC
                             defines ``__eq__`` and ``__le__`` by containment,
                             so equality AND a real subset PARTIAL order both
                             fall out of the carried ``items``.
``EqClass::Number``          ``isinstance(o, numbers.Number)``, as
                             ``complex(o)``.  **Equality only.**
``EqClass::Identity``        default ``__eq__`` AND default ``__repr__`` — so
                             ``repr`` carries the address and IS a token.
                             **Equality only.**
``None``                     everything else: never equal, never ordered —
                             the answer this carrier already gave.
===========================  ===============================================

The trap this had to avoid
--------------------------
Equality must NOT be reached by giving these values a ``cmp_key``.  Python's
answers for the two operators come apart inside this family::

    same object          ==   <=   >=   <    >
      set()      django   Y    Y    Y    N    N     (subset order)
      complex(0) django   Y    N    N    N    N     (`<` RAISES)

so the one-line version of this fix — a key, reached through
``python_partial_cmp`` — buys eight cells and sells a new divergence at
``{% if p <= q %}`` on a ``complex``.  ``TestTheOrderingTrap`` pins both halves.

What is DECLINED, pinned in the diverging direction
---------------------------------------------------
Three shapes stay unequal and are ``TestWhatThisDeliberatelyDoesNOTClose``:

* a class overriding ``__eq__`` — only Python can run it;
* a class with default ``__eq__`` and a CUSTOM ``__repr__`` (a ``dict_values``
  is the builtin case): two distinct empty ones share the spelling
  ``dict_values([])``, so the repr is not a token and using it would be a NEW
  wrong answer rather than an unfixed cell;
* an ``Encoded`` against a ``Decimal`` or a big ``int`` — both exact types an
  ``f64`` cannot answer.
"""

from __future__ import annotations

import collections
import collections.abc
import numbers
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CORE_RS = REPO / "crates" / "djust_core" / "src" / "lib.rs"
RENDERER_RS = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"
FILTERS_RS = REPO / "crates" / "djust_templates" / "src" / "filters.rs"

#: Every operator ``{% if %}`` accepts for a pair, so a fix that moves one and
#: not the others is visible rather than plausible.
OPS = {
    "==": "{% if p == q %}Y{% else %}N{% endif %}",
    "!=": "{% if p != q %}Y{% else %}N{% endif %}",
    "<=": "{% if p <= q %}Y{% else %}N{% endif %}",
    ">=": "{% if p >= q %}Y{% else %}N{% endif %}",
    "<": "{% if p < q %}Y{% else %}N{% endif %}",
    ">": "{% if p > q %}Y{% else %}N{% endif %}",
}


def instance(name: str, **namespace: object) -> object:
    """A fresh instance of a fresh class, with NOTHING defaulted.

    Deliberately unlike ``test_opaque_collections_2477_2489.instance``, which
    pins ``__repr__`` so its fixtures render deterministically.  The default
    ``object.__repr__`` — the one carrying a MEMORY ADDRESS — is exactly the
    thing under test here, so a fixture that overrode it would measure the
    wrong arm.  ``__module__`` is still set, so the repr's prefix is stable.
    """
    namespace.setdefault("__module__", "djust_eq_fixtures")
    return type(name, (), namespace)()


def _shapes() -> dict[str, tuple[object, object]]:
    """``name -> (a, b)``: two DISTINCT instances of the same shape.

    Built fresh on each call.  The pair is what separates the two questions
    Django answers differently — ``p is q`` and "two objects Python compares" —
    and every sweep below runs both.
    """
    return {
        # --- EqClass::Set: the ABC, across four type names ------------------
        "set-empty": (set(), set()),
        "set-plain": ({"a"}, {"a"}),
        "set-two": ({"a", "b"}, {"b", "a"}),
        "frozenset-empty": (frozenset(), frozenset()),
        "frozenset-plain": (frozenset({"a"}), frozenset({"a"})),
        "dictkeys-empty": ({}.keys(), {}.keys()),
        "dictkeys-plain": ({"a": 1}.keys(), {"a": 1}.keys()),
        "dictitems-empty": ({}.items(), {}.items()),
        "dictitems-plain": ({"a": 1}.items(), {"a": 1}.items()),
        "odict-keys": (
            collections.OrderedDict({"a": 1}).keys(),
            collections.OrderedDict({"a": 1}).keys(),
        ),
        # --- EqClass::Number ------------------------------------------------
        "complex-zero": (complex(0), complex(0)),
        "complex-one": (complex(1), complex(1)),
        "complex-imag": (complex(0, 1), complex(0, 1)),
        # --- EqClass::Identity: default __eq__ AND default __repr__ ---------
        "lenzero": (
            instance("LenZero", __len__=lambda s: 0),
            instance("LenZero", __len__=lambda s: 0),
        ),
        "boolfalse": (
            instance("BoolFalse", __bool__=lambda s: False),
            instance("BoolFalse", __bool__=lambda s: False),
        ),
        # --- DECLINED: no class, three ways ---------------------------------
        # Default `__eq__`, CUSTOM `__repr__` — the builtin case.
        "dictvalues-empty": ({}.values(), {}.values()),
        "dictvalues-plain": ({"a": 1}.values(), {"a": 1}.values()),
        # ...and the user-class case, same protocol facts.
        "lenzero-customrepr": (
            instance("LenZeroR", __len__=lambda s: 0, __repr__=lambda s: "LenZeroR()"),
            instance("LenZeroR", __len__=lambda s: 0, __repr__=lambda s: "LenZeroR()"),
        ),
        # A custom `__eq__`: only Python can run it.
        "eq-always-true": (
            instance("EqTrue", __len__=lambda s: 0, __eq__=lambda s, o: True, __hash__=lambda s: 1),
            instance("EqTrue", __len__=lambda s: 0, __eq__=lambda s, o: True, __hash__=lambda s: 1),
        ),
    }


#: Which arm each shape lands on, asserted in BOTH directions by
#: ``test_the_protocol_facts_are_what_the_rule_reads``.  A name list would have
#: been wrong here and the entries say why.
EXPECTED_CLASS: dict[str, str] = {
    "set-empty": "Set",
    "set-plain": "Set",
    "set-two": "Set",
    "frozenset-empty": "Set",
    "frozenset-plain": "Set",
    "dictkeys-empty": "Set",
    "dictkeys-plain": "Set",
    "dictitems-empty": "Set",
    "dictitems-plain": "Set",
    "odict-keys": "Set",
    "complex-zero": "Number",
    "complex-one": "Number",
    "complex-imag": "Number",
    "lenzero": "Identity",
    "boolfalse": "Identity",
    # A `dict_values` is NOT a Set — Python says `set() != {}.values()` — and
    # its `__repr__` is its own, so neither of the first three arms claims it.
    "dictvalues-empty": "none",
    "dictvalues-plain": "none",
    "lenzero-customrepr": "none",
    "eq-always-true": "none",
}

#: The shapes this fix DOES NOT answer, and the reason.  Every one is pinned in
#: the DIVERGING direction below, so widening one is a decision rather than an
#: accident.
DECLINED: dict[str, str] = {
    "dictvalues-empty": "default __eq__ but a custom __repr__: the spelling is not a token",
    "dictvalues-plain": "default __eq__ but a custom __repr__: the spelling is not a token",
    "lenzero-customrepr": "default __eq__ but a custom __repr__: the spelling is not a token",
    "eq-always-true": "a custom __eq__: only Python can run it",
}


def render(source: str, a: object, b: object, engine: str) -> str:
    """One cell, on one engine.  Django is CALLED, never transcribed."""
    try:
        if engine == "django":
            return DjangoTemplate(source).render(DjangoContext({"p": a, "q": b}))
        if engine == "raw":
            return _rust.render_template(source, {"p": a, "q": b})
        return _rust.render_template(source, normalize_django_value({"p": a, "q": b}))
    except Exception as exc:  # noqa: BLE001 — a refusal IS the answer
        found = re.search(r"raises (\w+Error)", str(exc))
        return f"<<{found.group(1)}>>" if found else f"<<{type(exc).__name__}>>"


def eq_class_of(value: object) -> str:
    """The arm ``equality_class`` will pick, computed from Python.

    A transcription of the Rust in Python, deliberately: it is what lets
    ``EXPECTED_CLASS`` be checked against the PROTOCOL rather than against a
    remembered table, so a fixture whose facts change is caught.
    """
    if isinstance(value, collections.abc.Set):
        return "Set"
    if isinstance(value, numbers.Number):
        return "Number"
    if type(value).__eq__ is object.__eq__ and type(value).__repr__ is object.__repr__:
        return "Identity"
    return "none"


class TestTheCrossProduct:
    """The reproducer, as a sweep: every shape against every shape, six
    operators, both engines, with Django as the oracle.

    A curated table samples one axis and blinds you on the next (v1.1.1-2
    rule 2).  The axes here are: which shape, which shape it is compared TO
    (including ACROSS classes), which operator, whether the two operands are
    the SAME object, and which engine renders it.
    """

    #: A cell is EXPECTED to diverge exactly when Python calls the two operands
    #: equal AND at least one of them carries no equality class — which is the
    #: whole of what this fix declines, stated as a rule rather than as a list
    #: of names.  Every OTHER cell must agree, and the counts below pin that
    #: the exceptions are the size they are measured to be.
    @staticmethod
    def _expected_divergent(a: object, b: object, op: str) -> bool:
        if op not in ("==", "!="):
            # No ordering cell diverges: the Set class carries a real partial
            # order and every other class answers `None`, which is Django's
            # own answer for a pair Python refuses.
            return False
        try:
            equal = bool(a == b)
        except Exception:  # noqa: BLE001 — pragma: no cover
            return False
        return equal and "none" in {eq_class_of(a), eq_class_of(b)}

    def _sweep(self, pairs) -> tuple[list, int]:
        """Every operator over every pair; returns the offenders and the count
        of cells that diverged AS EXPECTED."""
        offenders: list[tuple[str, str, str, str]] = []
        expected = 0
        for label, a, b in pairs:
            for op, source in OPS.items():
                dj = render(source, a, b, "django")
                raw = render(source, a, b, "raw")
                if self._expected_divergent(a, b, op):
                    expected += 1
                    if dj == raw:
                        offenders.append((f"{label} (CLOSED, delete its row)", op, dj, raw))
                elif dj != raw:
                    offenders.append((label, op, dj, raw))
        return offenders, expected

    def test_same_object_agrees_with_django_on_every_operator(self) -> None:
        offenders, expected = self._sweep((name, a, a) for name, (a, _b) in _shapes().items())
        assert not offenders, self._explain(offenders)
        # Four shapes × `==` and `!=`: the two `dict_values`, the
        # custom-`__repr__` class and the custom-`__eq__` one.
        assert expected == 8, expected

    def test_two_distinct_instances_agree_with_django_on_every_operator(self) -> None:
        offenders, expected = self._sweep((name, a, b) for name, (a, b) in _shapes().items())
        assert not offenders, self._explain(offenders)
        # Only the custom-`__eq__` class: two distinct `dict_values` and two
        # distinct custom-`__repr__` instances are Python-UNEQUAL, so `N` is
        # the RIGHT answer for them here.
        assert expected == 2, expected

    def test_every_shape_against_every_other_shape(self) -> None:
        """The CROSS-class half, which is where a per-type rule would break.

        ``set() == frozenset()`` is True across type names and
        ``set() == {}.values()`` is False across classes; both are here, and so
        is every pair neither table names.
        """
        names = list(_shapes())
        offenders, expected = self._sweep(
            (f"{left} vs {right}", _shapes()[left][0], _shapes()[right][1])
            for left in names
            for right in names
            if left != right
        )
        assert not offenders, self._explain(offenders)
        # The custom-`__eq__` class against each OTHER shape, both directions,
        # on `==` and `!=` — and nothing else, which is the statement: no
        # cross-CLASS pair diverges, so `set() == {}.values()`,
        # `set() == complex(0)` and `set() < complex(0)` are all right.
        assert expected == (len(names) - 1) * 2 * 2, expected

    def test_the_liveview_path_answers_the_same(self) -> None:
        """Both djust paths, because the normalizer runs before the conversion
        on one of them and used to flatten these values (#2477/#2489).
        """
        offenders = []
        for name, (a, b) in _shapes().items():
            for op, source in OPS.items():
                raw = render(source, a, b, "raw")
                live = render(source, a, b, "live")
                if raw != live:
                    offenders.append((name, op, raw, live))
        assert not offenders, self._explain(offenders)

    @staticmethod
    def _explain(offenders: list[tuple[str, str, str, str]]) -> str:
        return f"{len(offenders)} cells disagree with Django:\n" + "\n".join(
            f"  {name:24s} {op:3s} django={dj!r} djust={du!r}"
            for name, op, dj, du in offenders[:20]
        )


class TestTheRuleIsTheProtocol:
    """Why the fix is a fourth measured fact rather than a rule over the three
    already carried."""

    def test_no_single_carried_field_could_have_decided_this(self) -> None:
        """The issue's central claim, RUN rather than quoted."""
        # ACROSS type names, and True.
        assert set() == frozenset() == {}.keys() == {}.items()
        assert len({type(v).__name__ for v in (set(), frozenset(), {}.keys(), {}.items())}) == 4
        # WITHIN one type name, and False.
        a, b = _shapes()["lenzero"]
        assert type(a).__name__ == type(b).__name__
        assert a != b
        # Same carried ITEMS (both empty), opposite answers — so `items` alone
        # cannot decide it either.
        assert list({}.keys()) == list({}.values()) == []
        assert set() == {}.keys()
        assert set() != {}.values()

    def test_the_protocol_facts_are_what_the_rule_reads(self) -> None:
        """``EXPECTED_CLASS`` checked against the live protocol, both ways."""
        for name, (a, _b) in _shapes().items():
            assert eq_class_of(a) == EXPECTED_CLASS[name], name
        assert set(EXPECTED_CLASS) == set(_shapes())
        # Non-vacuity: all four arms are inhabited, so no arm is dead.
        assert set(EXPECTED_CLASS.values()) == {"Set", "Number", "Identity", "none"}

    def test_a_name_list_would_have_been_wrong_in_both_directions(self) -> None:
        """Why ``type_name`` is not the discriminator (#2485's lesson).

        A user class REGISTERED with the ABC is a Set that no builtin name list
        holds, and a user class NAMED ``set`` is not one — ``type(o).__name__``
        is unqualified, so a name list cannot tell them apart.
        """
        registered = instance("MySet", __len__=lambda s: 0, __iter__=lambda s: iter(()))
        collections.abc.Set.register(type(registered))
        assert isinstance(registered, collections.abc.Set)
        assert type(registered).__name__ not in {"set", "frozenset", "dict_keys", "dict_items"}
        assert eq_class_of(registered) == "Set"

        impostor = instance("set", __len__=lambda s: 0)
        assert type(impostor).__name__ == "set"
        assert not isinstance(impostor, collections.abc.Set)
        assert eq_class_of(impostor) == "Identity"
        # ...and the engine follows the protocol, not the name: two DISTINCT
        # impostors are Python-unequal and must stay unequal here.
        other = instance("set", __len__=lambda s: 0)
        assert impostor != other
        assert render(OPS["=="], impostor, other, "raw") == "N"
        assert render(OPS["=="], impostor, other, "django") == "N"


class TestTheOrderingTrap:
    """``==`` and ``<`` come apart INSIDE this family, so equality could not be
    reached by handing every class a comparison key."""

    def test_a_set_orders_and_a_complex_does_not(self) -> None:
        """The two halves of the trap, side by side and both from Django."""
        s1, s2 = _shapes()["set-empty"]
        c1, c2 = _shapes()["complex-zero"]
        for op in ("==", "<=", ">="):
            assert render(OPS[op], s1, s2, "django") == "Y", op
            assert render(OPS[op], s1, s2, "raw") == "Y", op
        # The same three operators on a complex: equal, and NOT ordered.
        assert render(OPS["=="], c1, c2, "django") == "Y"
        assert render(OPS["=="], c1, c2, "raw") == "Y"
        for op in ("<=", ">=", "<", ">"):
            assert render(OPS[op], c1, c2, "django") == "N", op
            assert render(OPS[op], c1, c2, "raw") == "N", op
        # Python's own reason, so the pin is not a remembered fact.
        with pytest.raises(TypeError):
            _ = c1 <= c2

    def test_an_identity_value_is_equal_to_itself_and_orders_against_nothing(self) -> None:
        obj = _shapes()["lenzero"][0]
        assert render(OPS["=="], obj, obj, "raw") == "Y"
        for op in ("<=", ">=", "<", ">"):
            assert render(OPS[op], obj, obj, "django") == "N", op
            assert render(OPS[op], obj, obj, "raw") == "N", op

    def test_the_set_order_is_PARTIAL_and_incomparable_answers_false_four_ways(
        self,
    ) -> None:
        """``{1} vs {2}``: Python answers False for all four, which is what an
        ``Option<Ordering>`` of ``None`` renders as.  A total-order key could
        not say this."""
        a, b = {"a"}, {"b"}
        assert not (a <= b or a >= b or a < b or a > b)
        for op in ("<=", ">=", "<", ">"):
            assert render(OPS[op], a, b, "django") == "N", op
            assert render(OPS[op], a, b, "raw") == "N", op
        assert render(OPS["=="], a, b, "raw") == "N"

    def test_a_proper_subset_orders_strictly(self) -> None:
        """And the direction that proves the order is real rather than
        all-Equal: ``set() < {'a'}`` is True, across type names too."""
        for smaller, larger in (
            (set(), {"a"}),
            (frozenset(), {"a"}),
            ({}.keys(), {"a"}),
            (set(), {"a": 1}.keys()),
        ):
            assert render(OPS["<"], smaller, larger, "django") == "Y"
            assert render(OPS["<"], smaller, larger, "raw") == "Y"
            assert render(OPS[">"], larger, smaller, "raw") == "Y"
            assert render(OPS["=="], smaller, larger, "raw") == "N"

    def test_set_membership_is_djangos_equality_not_a_structural_one(self) -> None:
        """``{1} == {1.0}`` is True in Python because ``1 == 1.0`` is, so the
        item comparison has to be ``values_equal`` and not a structural one."""
        assert {1} == {1.0}
        assert render(OPS["=="], {1}, {1.0}, "django") == "Y"
        assert render(OPS["=="], {1}, {1.0}, "raw") == "Y"


class TestTheCrossCarrierArm:
    """An opaque NUMBER against a real one — a pair no ``(Encoded, Encoded)``
    arm reaches (the issue's §3a)."""

    def test_a_complex_equals_the_int_and_float_it_names(self) -> None:
        for value, other in (
            (complex(0), 0),
            (complex(0), 0.0),
            (complex(1), 1),
            (complex(1), 1.0),
        ):
            assert value == other
            assert render(OPS["=="], value, other, "django") == "Y", (value, other)
            assert render(OPS["=="], value, other, "raw") == "Y", (value, other)
            # ...and the mirrored operand order, which is a separate match arm.
            assert render(OPS["=="], other, value, "raw") == "Y", (other, value)

    def test_a_bool_reaches_the_same_arm_through_the_integer_substitution(self) -> None:
        """``bool_as_int`` runs first, so ``{% if p == True %}`` on a
        ``complex(1)`` lands on the integer arm rather than falling through."""
        assert complex(1) == True  # noqa: E712 — the point is Python's answer
        assert render("{% if p == True %}Y{% else %}N{% endif %}", complex(1), 0, "raw") == "Y"
        assert render("{% if p == False %}Y{% else %}N{% endif %}", complex(0), 0, "raw") == "Y"

    def test_an_imaginary_part_is_not_equal_to_a_real_number(self) -> None:
        assert complex(0, 1) != 0
        assert render(OPS["=="], complex(0, 1), 0, "django") == "N"
        assert render(OPS["=="], complex(0, 1), 0, "raw") == "N"

    def test_the_integer_comparison_is_EXACT_at_the_float_boundary(self) -> None:
        """Python compares an ``int`` to a ``complex`` exactly, so
        ``complex(2**53) == 2**53 + 1`` is False even though the cast rounds.
        A naive ``real == i as f64`` would answer True."""
        big = 2**53
        assert complex(big) == big
        assert complex(big) != big + 1
        assert render(OPS["=="], complex(big), big, "raw") == "Y"
        assert render(OPS["=="], complex(big), big + 1, "django") == "N"
        assert render(OPS["=="], complex(big), big + 1, "raw") == "N"

    def test_a_huge_float_does_not_saturate_onto_an_integer_bound(self) -> None:
        """``real as i64`` SATURATES, so ``complex(1e300)`` would land on
        ``i64::MAX`` and compare equal to it without the second check."""
        bound = 2**63 - 1
        assert complex(1e300) != bound
        assert render(OPS["=="], complex(1e300), bound, "raw") == "N"

    def test_a_set_is_not_equal_to_a_list_or_a_scalar(self) -> None:
        """The control: the cross-carrier arm is NUMERIC and claims nothing
        else.  ``set() == []`` is False in Python."""
        for other in ([], (), "", 0, 0.0):
            assert set() != other
            assert render(OPS["=="], set(), other, "raw") == "N", other


class TestTheStateRoundTrip:
    """The class is CARRIED, so one cache hit does not restore the pre-fix
    answer — the reopening ``ENCODED_TAG`` exists to prevent, now for the
    sixth time."""

    @staticmethod
    def _round_trip(source: str, a: object, b: object) -> str:
        from djust._rust import RustLiveView

        view = RustLiveView(source)
        view.set_state("p", a)
        view.set_state("q", b)
        return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()

    def test_every_class_survives_a_msgpack_state_round_trip(self) -> None:
        offenders = []
        for name, (a, b) in _shapes().items():
            for op, source in OPS.items():
                direct = render(source, a, b, "raw")
                after = self._round_trip(source, a, b)
                if direct != after:
                    offenders.append((name, op, direct, after))
        assert not offenders, "state round trip changed the answer:\n" + "\n".join(
            f"  {n} {o}: {d!r} -> {r!r}" for n, o, d, r in offenders[:20]
        )

    def test_the_payload_carries_an_eleventh_slot_and_it_is_a_map(self) -> None:
        """The slot's shape, pinned from Python as well as from Rust: it is
        always a MAP — empty when there is no class — because that is what
        refuses a shifted ten-element payload now that eleven is a real width.
        """
        msgpack = pytest.importorskip("msgpack")

        from djust._rust import RustLiveView

        view = RustLiveView("{{ p }}")
        view.set_state("p", set())
        view.set_state("q", __import__("datetime").timedelta(0))
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        opaque = decoded[1]["p"]["__djust_encoded__"]
        datetime_payload = decoded[1]["q"]["__djust_encoded__"]
        assert len(opaque) == 11, opaque
        assert len(datetime_payload) == 11, datetime_payload
        # A `set()` is the Set class...
        assert opaque[10] == {"eq": [1, 0.0, 0.0]}, opaque[10]
        # ...and a `timedelta` has NO class: it compares by its `cmp_key`, and
        # the slot is an EMPTY MAP rather than a nil.
        assert datetime_payload[10] == {}, datetime_payload[10]


class TestWhatThisDeliberatelyDoesNOTClose:
    """Pinned as still-divergent so a stale exemption goes red (#1859).

    Each row is a shape whose equality this fix declines, with the reason.
    Closing one reddens this class rather than passing silently.
    """

    def test_each_declined_shape_still_diverges_from_django(self) -> None:
        for name, why in DECLINED.items():
            a, b = _shapes()[name]
            # The SAME object: Django says Y (Python's `is` shortcut or the
            # custom `__eq__`), and this answers N.
            assert render(OPS["=="], a, a, "django") == "Y", (name, why)
            assert render(OPS["=="], a, a, "raw") == "N", (
                f"{name} now compares EQUAL — the decline closed, so delete its row ({why})"
            )

    def test_a_dict_values_is_the_builtin_case_and_its_repr_is_why(self) -> None:
        """Two DISTINCT empty ``dict_values`` share a repr, so the identity arm
        must not claim them — using the token would be a NEW wrong answer."""
        a, b = _shapes()["dictvalues-empty"]
        assert type(a).__eq__ is object.__eq__
        assert type(a).__repr__ is not object.__repr__
        assert repr(a) == repr(b) == "dict_values([])"
        assert a != b
        assert render(OPS["=="], a, b, "django") == "N"
        assert render(OPS["=="], a, b, "raw") == "N"

    def test_a_decimal_and_a_big_int_are_not_reached_by_the_numeric_arm(self) -> None:
        """Both are exact types an ``f64`` cannot answer, so they keep the
        pre-fix ``N``."""
        import decimal

        assert complex(0) == decimal.Decimal(0)
        assert render(OPS["=="], complex(0), decimal.Decimal(0), "django") == "Y"
        assert render(OPS["=="], complex(0), decimal.Decimal(0), "raw") == "N"

    def test_a_set_past_the_comparison_cap_declines_rather_than_answering(self) -> None:
        """Containment without a hash is quadratic and a ``set`` states its own
        length, so past ``SET_COMPARE_CAP`` the answer is the pre-fix one.

        A cell left open, pinned so raising the cap is a decision.
        """
        cap = int(
            re.search(r"const SET_COMPARE_CAP: usize = ([0-9_]+);", RENDERER_RS.read_text())
            .group(1)
            .replace("_", "")
        )
        assert cap == 1_000
        under = set(range(cap))
        assert render(OPS["=="], under, set(range(cap)), "raw") == "Y"
        over = set(range(cap + 1))
        assert render(OPS["=="], over, set(range(cap + 1)), "django") == "Y"
        assert render(OPS["=="], over, set(range(cap + 1)), "raw") == "N"


class TestTheChokepointIsStructural:
    """One function decides the ``Encoded`` half of every comparison operator,
    so ``==`` and ``<`` cannot drift (#1646).

    ``Encoded::python_partial_cmp`` used to have three readers; #2480 wraps it
    in ``encoded_partial_cmp``, which is now its only caller outside its own
    crate.  Pinned by COUNT, in both directions, so a fourth reader that skips
    the wrapper reddens this.
    """

    @staticmethod
    def _production(src: str) -> str:
        """Everything above the in-file ``#[cfg(test)]`` module."""
        marker = "\n#[cfg(test)]\n"
        return src.split(marker)[0] if marker in src else src

    def test_python_partial_cmp_has_exactly_one_caller_outside_its_crate(self) -> None:
        renderer = self._production(RENDERER_RS.read_text(encoding="utf-8"))
        filters = self._production(FILTERS_RS.read_text(encoding="utf-8"))
        assert renderer.count(".python_partial_cmp(") == 1, (
            "a second reader would let `==` and `<` drift; call `encoded_partial_cmp` instead"
        )
        assert filters.count(".python_partial_cmp(") == 0

    def test_every_comparison_reader_goes_through_the_wrapper(self) -> None:
        renderer = self._production(RENDERER_RS.read_text(encoding="utf-8"))
        filters = self._production(FILTERS_RS.read_text(encoding="utf-8"))
        # `values_equal` (through `encoded_equal`), `try_compare`, and the
        # definition itself.
        assert renderer.count("encoded_partial_cmp(") == 3, renderer.count("encoded_partial_cmp(")
        # `dictsort`'s ordering.
        assert filters.count("encoded_partial_cmp(") == 1

    def test_the_counter_goes_red_in_BOTH_directions(self) -> None:
        """The canary: each mutation asserts it APPLIED before its count is
        read, so a no-op edit cannot report a passing number (#2129/#2135)."""
        src = self._production(RENDERER_RS.read_text(encoding="utf-8"))
        baseline = src.count(".python_partial_cmp(")
        assert baseline == 1

        added = src.replace(
            "a.python_partial_cmp(b)",
            "a.python_partial_cmp(b).or(a.python_partial_cmp(b))",
            1,
        )
        assert added != src, "the ADD mutation did not apply"
        assert added.count(".python_partial_cmp(") == baseline + 1

        removed = src.replace("a.python_partial_cmp(b)", "None", 1)
        assert removed != src, "the REMOVE mutation did not apply"
        assert removed.count(".python_partial_cmp(") == baseline - 1

    def test_the_equality_class_is_measured_by_one_function(self) -> None:
        """One producer, one consumer of each half — the drift shape this
        repo keeps paying for (#1646)."""
        core = self._production(CORE_RS.read_text(encoding="utf-8"))
        assert core.count("fn equality_class(") == 1
        assert core.count("equality_class(ob)") == 1
        assert core.count("fn encode_eq_class(") == 1
        assert core.count("fn decode_eq_class(") == 1
