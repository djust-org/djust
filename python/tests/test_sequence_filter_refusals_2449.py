"""Django's sequence filters RAISE on a non-sequence, and now so does djust (#2449).

The defect
----------
``{{ p|first }}`` over ``p = 3`` rendered ``''`` — which reads as "the list was
empty" — where Django raises ``TypeError: 'int' object is not subscriptable``.
``{{ p|unordered_list }}`` rendered ``'3'`` where Django raises
``'int' object is not iterable``.  Neither filter has a ``try/except``.

Why this was decidable where #2429 was not
-------------------------------------------
#2429 is undecidable in the value position because the PyO3 boundary erases the
Python type before the filter runs.  These are not about a type — they are about
a value's ``Value`` SHAPE, which Rust can see perfectly well.  A
``Value::Integer`` is not a sequence, and ``{% for %}``'s own refusal arm
(#2382) already raised ``'int' object is not iterable`` for exactly that shape,
so the message was already in the engine.

What the re-derivation added to the issue
------------------------------------------
The issue named TWO filters and TWO messages.  Sweeping Django's whole registry
found **six** filters and **three** messages, split by what CPython reaches
first:

===========================================  =====================  ==============================================
filter                                       operation              message
===========================================  =====================  ==============================================
``first`` / ``last``                         ``value[0]``           ``'int' object is not subscriptable``
``unordered_list`` / ``safeseq`` /           iterate                ``'int' object is not iterable``
``escapeseq``
``random``                                   ``v[randbelow(len(v))]``  ``object of type 'int' has no len()``
===========================================  =====================  ==============================================

``random`` needs both of the last two: ``random.choice`` checks ``len(seq)``
FIRST, so a scalar dies on the length while a ``dict_keys`` — which has a length
and no ``__getitem__`` — gets past it and dies on the subscript.  And an EMPTY
view raises ``IndexError`` there, which Django's ``random`` CATCHES, so it
renders ``''``.  Three outcomes from one filter; a single message, or an
unconditional refusal for a view, is wrong for one of them.

``None`` is also in the refusal set — ``'NoneType' object is not subscriptable``
— which the issue did not mention and which is the row most likely to be reached
by a real template.

What this deliberately does NOT close
--------------------------------------
The **KeyError** class.  A ``dict`` IS subscriptable, so Django gets past the
``TypeError`` and raises ``KeyError: 0`` from ``d[0]`` instead.  That is a
different exception class reachable only by implementing the integer-key lookup
(and one that can legitimately SUCCEED, for a dict with an integer key), so it
is filed as #2457 rather than folded in — and pinned as still-divergent below.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import datetime
import decimal
import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)

SUBSCRIPT = ("first", "last")
ITERATE = ("unordered_list", "safeseq", "escapeseq")
CHOICE = ("random",)
ALL_SIX = SUBSCRIPT + ITERATE + CHOICE

#: Every shape with neither `__getitem__` nor `__iter__` nor `__len__`, and
#: CPython's name for it. Swept rather than sampled: the issue named `int`.
SCALARS: dict[str, tuple[object, str]] = {
    "int": (3, "int"),
    "int zero": (0, "int"),
    "int past i64": (12345678901234567890, "int"),
    "float": (3.5, "float"),
    "float zero": (0.0, "float"),
    "float nan": (float("nan"), "float"),
    "bool true": (True, "bool"),
    "bool false": (False, "bool"),
    "None": (None, "NoneType"),
    "Decimal": (decimal.Decimal("1.5"), "decimal.Decimal"),
    "Decimal zero": (decimal.Decimal("0"), "decimal.Decimal"),
    # Reachable only since #2448 gave the datetime family a typed variant; a
    # `Value::String` before that, and so silently sliced.
    "datetime": (datetime.datetime(2020, 1, 1, 3, 4, 5), "datetime.datetime"),
    "date": (datetime.date(2020, 1, 1), "datetime.date"),
    "time": (datetime.time(3, 4, 5), "datetime.time"),
    "timedelta": (datetime.timedelta(seconds=90), "datetime.timedelta"),
}

#: Shapes Django ACCEPTS. A refusal here would be a new divergence in the
#: strict direction, which is still a divergence.
SEQUENCES: dict[str, object] = {
    "str": "abc",
    "str empty": "",
    "list": [1, 2],
    "list empty": [],
    "tuple": (1, 2),
    "tuple empty": (),
}


def django_outcome(src: str, ctx: dict) -> str:
    try:
        return "OK " + DjangoTemplate(src).render(DjangoContext(dict(ctx)))
    except Exception as exc:  # noqa: BLE001
        return f"RAISE {type(exc).__name__}: {exc}"


def djust_outcome(src: str, ctx: dict) -> str:
    try:
        return "OK " + _rust.render_template(src, dict(ctx))
    except Exception as exc:  # noqa: BLE001
        return f"RAISE {type(exc).__name__}: {exc}"


def expected_message(filter_name: str, type_name: str) -> str:
    if filter_name in SUBSCRIPT:
        return "'%s' object is not subscriptable" % type_name
    if filter_name in ITERATE:
        return "'%s' object is not iterable" % type_name
    return "object of type '%s' has no len()" % type_name


class TestTheScalarRefusals:
    """The headline, for all six filters and every scalar shape."""

    @pytest.mark.parametrize("shape", sorted(SCALARS))
    @pytest.mark.parametrize("name", ALL_SIX)
    def test_django_raises_and_so_does_djust(self, name: str, shape: str) -> None:
        value, _ = SCALARS[shape]
        src = "{{ p|%s }}" % name
        d = django_outcome(src, {"p": value})
        r = djust_outcome(src, {"p": value})
        assert d.startswith("RAISE TypeError"), f"Django moved for {shape}/{name}: {d!r}"
        assert r.startswith("RAISE RuntimeError"), f"{shape}/{name}: djust answered {r!r}"

    @pytest.mark.parametrize("shape", sorted(SCALARS))
    @pytest.mark.parametrize("name", ALL_SIX)
    def test_the_message_is_the_one_django_raises(self, name: str, shape: str) -> None:
        """Not merely "both raise": the TEXT is CPython's, and it is read from
        Django in the same assertion rather than transcribed above.

        `expected_message` is checked against Django's actual output, so a table
        that drifted from CPython fails here rather than passing quietly.
        """
        value, type_name = SCALARS[shape]
        src = "{{ p|%s }}" % name
        expected = expected_message(name, type_name)
        assert expected in django_outcome(src, {"p": value}), (
            f"the expectation is not Django's for {shape}/{name}"
        )
        assert expected in djust_outcome(src, {"p": value}), f"{shape}/{name}"

    def test_random_reaches_a_DIFFERENT_message_from_the_other_five(self) -> None:
        """The row a two-message model would have got wrong.

        `random.choice` evaluates `len(seq)` before subscripting, so its
        `TypeError` is CPython's length one and not the subscript one.
        """
        assert expected_message("random", "int") == "object of type 'int' has no len()"
        assert expected_message("first", "int") == "'int' object is not subscriptable"
        assert expected_message("safeseq", "int") == "'int' object is not iterable"
        assert len({expected_message(f, "int") for f in ALL_SIX}) == 3


class TestTheSequencesAreUntouched:
    """The half an unconditional refusal would have destroyed."""

    @pytest.mark.parametrize("shape", sorted(SEQUENCES))
    @pytest.mark.parametrize("name", [n for n in ALL_SIX if n != "random"])
    def test_a_real_sequence_still_agrees_with_django(self, name: str, shape: str) -> None:
        value = SEQUENCES[shape]
        src = "{{ p|%s }}" % name
        assert djust_outcome(src, {"p": value}) == django_outcome(src, {"p": value}), shape

    @pytest.mark.parametrize("shape", sorted(SEQUENCES))
    def test_random_neither_raises_nor_changes_shape(self, shape: str) -> None:
        """`random` picks nondeterministically, so the comparable property is
        that neither engine refuses."""
        value = SEQUENCES[shape]
        src = "{{ p|random }}"
        assert not django_outcome(src, {"p": value}).startswith("RAISE"), shape
        assert not djust_outcome(src, {"p": value}).startswith("RAISE"), shape

    @pytest.mark.parametrize("name", ALL_SIX)
    def test_an_ABSENT_variable_does_not_refuse(self, name: str) -> None:
        """`Missing` is on the sequence side, and that is not a slip.

        Django substitutes `string_if_invalid` — a `str` — for an unresolvable
        variable, so `{{ nope|first }}` renders `''` and `{{ nope|safeseq }}`
        renders `[]`.  Refusing there would be a NEW divergence wearing a fix's
        clothes, and it is the row most likely to be got wrong by a fix written
        from the issue's `p = 3` example alone.
        """
        src = "{{ nope|%s }}" % name
        assert djust_outcome(src, {}) == django_outcome(src, {}), src


class TestTheDictViewSplit:
    """A view has a length and no `__getitem__`, so it splits three ways."""

    @pytest.mark.parametrize("kind", ["keys", "values", "items"])
    @pytest.mark.parametrize("name", SUBSCRIPT + CHOICE)
    def test_a_NON_empty_view_refuses(self, kind: str, name: str) -> None:
        src = "{{ p.%s|%s }}" % (kind, name)
        ctx = {"p": {"a": 1, "b": 2}}
        d, r = django_outcome(src, ctx), djust_outcome(src, ctx)
        assert d.startswith("RAISE TypeError"), d
        assert "'dict_%s' object is not subscriptable" % kind in d, d
        assert "'dict_%s' object is not subscriptable" % kind in r, r

    @pytest.mark.parametrize("kind", ["keys", "values", "items"])
    def test_an_EMPTY_view_renders_nothing_for_random(self, kind: str) -> None:
        """The row an unconditional view-refusal gets wrong.

        `random.choice` raises `IndexError` on an empty sequence, and Django's
        `random` filter catches `IndexError` (and only `IndexError`).  So an
        empty view renders `''` and a full one raises — from ONE filter.
        """
        src = "{{ p.%s|random }}" % kind
        assert django_outcome(src, {"p": {}}) == "OK ", django_outcome(src, {"p": {}})
        assert djust_outcome(src, {"p": {}}) == "OK ", djust_outcome(src, {"p": {}})

    @pytest.mark.parametrize("kind", ["keys", "values", "items"])
    @pytest.mark.parametrize("name", SUBSCRIPT)
    def test_an_EMPTY_view_still_refuses_for_first_and_last(self, kind: str, name: str) -> None:
        """The other half of the emptiness split, and the reason `random`'s
        guard is `Choice`-only rather than a property of views.

        `d.keys()[0]` raises whether or not the view is empty — the subscript
        check comes first — so an emptiness exemption applied to all three
        filters would be wrong for two of them.
        """
        src = "{{ p.%s|%s }}" % (kind, name)
        d, r = django_outcome(src, {"p": {}}), djust_outcome(src, {"p": {}})
        assert "'dict_%s' object is not subscriptable" % kind in d, d
        assert "'dict_%s' object is not subscriptable" % kind in r, r

    @pytest.mark.parametrize("kind", ["keys", "values", "items"])
    @pytest.mark.parametrize("name", ITERATE)
    def test_a_view_still_ITERATES_for_the_iterating_three(self, kind: str, name: str) -> None:
        """A view IS iterable in Python, so these three must not refuse it —
        the direction the subscript rule must not leak into."""
        src = "{{ p.%s|%s }}" % (kind, name)
        ctx = {"p": {"a": 1}}
        assert djust_outcome(src, ctx) == django_outcome(src, ctx), src


class TestWhatThisDeliberatelyDoesNOTClose:
    """Pinned as still-divergent so a stale exemption goes red (#1859)."""

    @pytest.mark.parametrize("name", SUBSCRIPT + CHOICE)
    def test_a_dict_still_raises_KeyError_in_django_and_renders_here(self, name: str) -> None:
        """A dict is SUBSCRIPTABLE, so Django gets past the `TypeError` and
        `d[0]` raises `KeyError: 0` instead.

        A different exception class, reachable only by implementing the
        integer-key lookup — which can legitimately SUCCEED for a dict that has
        an integer key, so it is not a refusal rule at all. Filed as #2457, not folded
        into a `TypeError` fix.
        """
        src = "{{ p|%s }}" % name
        ctx = {"p": {"a": 1}}
        assert django_outcome(src, ctx).startswith("RAISE KeyError"), django_outcome(src, ctx)
        assert djust_outcome(src, ctx).startswith("OK "), djust_outcome(src, ctx)

    def test_an_opaque_object_still_slices_its_str(self) -> None:
        """`object()` has no `Value` variant, so it still arrives as its
        `str()` and IS a sequence by then.

        The datetime family left this residue in #2448; a bare object has not,
        and #2382's `TestTheWireResidueIsNamed` is where it lives.
        """
        value = object()
        assert django_outcome("{{ p|first }}", {"p": value}).startswith("RAISE TypeError")
        # `repr(object())` opens with `<`, and `{{ }}` escapes it.
        assert djust_outcome("{{ p|first }}", {"p": value}) == "OK &lt;"


class TestTheSinkHasExactlyTheCallersItClaims:
    """Grep the SINK, and pin the caller SET rather than a floor (#1125).

    The six filters each ask the same question, so there is one helper and six
    call sites.  Pinned in BOTH directions, with a canary proving each direction
    can actually go red — a structural pin is only worth having if it catches a
    REMOVED arm as well as an added one.
    """

    EXPECTED_CALLERS = {
        "first": "Subscript",
        "last": "Subscript",
        "random": "Choice",
        "unordered_list": "Iterate",
        "safeseq": "Iterate",
        "escapeseq": "Iterate",
    }

    @staticmethod
    def _production() -> str:
        source = FILTERS_RS.read_text(encoding="utf-8")
        head = source.split("#[cfg(test)]", 1)[0]
        return "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("//"))

    @classmethod
    def _dispatch(cls) -> str:
        """The `apply_builtin_filter` region, which is where the arms live.

        Extracted BEFORE any canary mutation, so a mutation cannot land on an
        identically-spelled line elsewhere in the file and then report that
        nothing changed — which is the shape #2129/#2135 warn about: a mutation
        that silently fails to apply reads exactly like a passing pin.
        """
        return cls._production().split("fn apply_builtin_filter", 1)[1]

    @staticmethod
    def _callers(body: str) -> dict[str, str]:
        """Which filter arm calls the helper, and with which `SequenceOp`.

        Each arm's OWN body, delimited by the next arm at the same indent — not
        a fixed character window, which #2340 showed drifts the moment somebody
        adds a comment (see the same technique in
        `test_escape_chain_and_sequence_filters_2281_2283.py`).
        """
        starts = [(m.start(), m.group(1)) for m in re.finditer(r'\n        "(\w+)" =>', body)]
        found = {}
        for i, (pos, name) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(body)
            hit = re.search(r"not_a_sequence_error\(SequenceOp::(\w+)", body[pos:end])
            if hit:
                found[name] = hit.group(1)
        return found

    def test_exactly_these_six_filters_route_through_the_helper(self) -> None:
        assert self._callers(self._dispatch()) == self.EXPECTED_CALLERS

    def test_the_pin_goes_red_when_an_arm_is_ADDED(self) -> None:
        body = self._dispatch()
        anchor = '\n        "join" =>'
        assert body.count(anchor) == 1, body.count(anchor)
        mutated = body.replace(
            anchor,
            anchor + " match not_a_sequence_error(SequenceOp::Iterate, value) {}\n        _ =>",
            1,
        )
        assert mutated != body, "the ADD mutation did not apply"
        callers = self._callers(mutated)
        assert "join" in callers, callers
        assert callers != self.EXPECTED_CALLERS

    def test_the_pin_goes_red_when_an_arm_is_REMOVED(self) -> None:
        """The direction a floor-shaped pin (`>= 6 callers`) cannot see, and
        the one that matters: it is how a refusal gets quietly dropped."""
        body = self._dispatch()
        target = '"first" => match not_a_sequence_error(SequenceOp::Subscript, value)'
        assert body.count(target) == 1, body.count(target)
        mutated = body.replace(target, '"first" => match Option::<()>::None', 1)
        assert mutated != body, "the REMOVE mutation did not apply"
        callers = self._callers(mutated)
        assert "first" not in callers, callers
        assert callers != self.EXPECTED_CALLERS

    def test_the_scalar_classifier_has_no_wildcard(self) -> None:
        """`is_python_scalar` must enumerate every `Value` variant.

        A wildcard would let a new variant silently inherit "this is a
        sequence" and re-open the permissive half of #2449 for its own shape —
        which is exactly how `Value::Encoded` would have slipped through had it
        landed after this fix rather than with it.
        """
        src = self._production()
        body = src.split("fn is_python_scalar(value: &Value) -> bool {", 1)[1].split("\n}", 1)[0]
        assert "_ =>" not in body, body
        variants = set(re.findall(r"Value::(\w+)", body))
        assert variants == {
            "Bool",
            "Integer",
            "BigInt",
            "Float",
            "Decimal",
            "None",
            "Encoded",
            "Missing",
            "String",
            "List",
            "Tuple",
            "Object",
            "DictView",
        }, variants
