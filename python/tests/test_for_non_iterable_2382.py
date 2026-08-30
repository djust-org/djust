"""`{% for %}` over a non-iterable is REFUSED, as Django refuses it (#2382).

The defect
----------
``ForNode.render`` decides an operand's fate in three steps, not one::

    values = self.sequence.resolve(context, ignore_failures=True)
    if values is None:                       # -> the empty branch
        values = []
    if not hasattr(values, "__len__"):       # -> list(), which can RAISE
        values = list(values)
    len_values = len(values)
    if len_values < 1:                       # -> the empty branch
        return self.nodelist_empty.render(context)

djust rendered the ``{% empty %}`` block for every operand that was not a
sequence, which collapses the second step's two answers into the first's::

    {% for x in p %}[{{ x }}]{% empty %}E{% endfor %}

      p=True            django: TypeError    djust: 'E'
      p=42              django: TypeError    djust: 'E'
      p=1.5             django: TypeError    djust: 'E'
      p=Decimal("2.5")  django: TypeError    djust: 'E'
      p=None            django: 'E'          djust: 'E'   <- agrees

So it is not about bools and not about falsiness. ``None`` — and an operand
that does not resolve, which ``ignore_failures=True`` turns into ``None`` —
reaches the empty branch in Django too, and every other non-iterable raises.

The decision, and why it is not this file's to make from scratch
----------------------------------------------------------------
The issue lists four options: raise (full parity), raise only under ``DEBUG``,
warn, or leave it. Three precedents landed in the same week and all three chose
Django's answer over silent degradation, in development and in production
alike — #2328 (an unparseable or unresolvable filter argument raises), #2387
(``{% for %}``'s own unpack arity), #2400 (a wrong argument count). #2328's
maintainer considered a ``DEBUG``-only split explicitly and rejected it, since
a divergence that exists only in production is a new axis to maintain and the
one nobody tests.

So: raise. What djust rendered instead was not "less" — it was the WRONG
branch, with no signal anywhere that the operand was a scalar.

What this deliberately does NOT close
--------------------------------------
The WIRE RESIDUE. ``Value`` has no date variant and no opaque-object variant,
so a Python ``date``, ``datetime``, ``time``, ``timedelta``, ``set`` or plain
``object()`` reaches this renderer as its ``str()`` — and a string is a
sequence, so it iterates CHARACTER BY CHARACTER where Django raises. That is a
boundary defect (the #2214 / #2366 family), not a ``{% for %}`` one: the type
is already gone by the time ``Node::For`` sees the value, and no arm here can
recover it. Pinned in ``TestTheWireResidueIsNamed`` and filed separately.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import importlib.util
import pathlib

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

SRC = "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"

DIFFERENTIAL = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"
)


def django_out(ctx: dict) -> str:
    try:
        return DjangoTemplate(SRC).render(DjangoContext(dict(ctx)))
    except Exception as exc:  # noqa: BLE001
        return f"<<EXC {type(exc).__name__}: {exc}>>"


def djust_out(ctx: dict) -> str:
    try:
        return _rust.render_template(SRC, dict(ctx))
    except Exception as exc:  # noqa: BLE001
        return f"<<EXC {type(exc).__name__}: {exc}>>"
    except BaseException as exc:  # noqa: BLE001 — a Rust panic is not a refusal
        return f"<<PANIC {type(exc).__name__}>>"


#: The scalar shapes swept. Which of them Django REFUSES is measured below,
#: never listed here — that is the half this file must not assume. The list
#: itself is a person's choice, which is the `input-shape` limit the corpus
#: manifest declares and cannot close.
SCALARS: dict[str, object] = {
    "bool True": True,
    "bool False": False,
    "int": 42,
    "int zero": 0,
    "int past i64": 12345678901234567890,
    "float": 1.5,
    "float zero": 0.0,
    "float inf": float("inf"),
    "float nan": float("nan"),
    "Decimal": decimal.Decimal("2.5"),
    "Decimal zero": decimal.Decimal("0"),
}

#: Shapes Django ITERATES or answers with the empty branch, unchanged here.
SEQUENCES: dict[str, object] = {
    "str": "ab",
    "str empty": "",
    "list": [1, 2],
    "list empty": [],
    "tuple": (1, 2),
    "tuple empty": (),
    "dict": {"k": 1},
    "dict empty": {},
    "bytes": b"ab",
    "range": range(2),
}


def django_refuses(value: object) -> bool:
    return django_out({"p": value}).startswith("<<EXC ")


# ---------------------------------------------------------------------------
# The premise, measured rather than listed
# ---------------------------------------------------------------------------


class TestWhichShapesDjangoRefuses:
    """The requirement side, taken from Django and not from this file."""

    def test_every_scalar_is_refused_by_django(self) -> None:
        """If a row here stops being refused, the sweep below is asserting the
        wrong thing for it and must be re-measured rather than patched."""
        not_refused = {name for name, v in SCALARS.items() if not django_refuses(v)}
        assert not not_refused, not_refused

    def test_no_sequence_is_refused_by_django(self) -> None:
        refused = {name for name, v in SEQUENCES.items() if django_refuses(v)}
        assert not refused, refused

    def test_none_and_an_ABSENT_operand_take_the_empty_branch(self) -> None:
        """`ignore_failures=True` turns an unresolvable operand into `None`,
        and `None` becomes `[]` — which is why this class is about
        non-iterables and not about falsiness."""
        assert django_out({"p": None}) == "E"
        assert django_out({}) == "E"


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------


class TestBothEnginesRefuseANonIterable:
    @pytest.mark.parametrize("name", sorted(SCALARS))
    def test_djust_refuses_every_shape_django_refuses(self, name: str) -> None:
        out = djust_out({"p": SCALARS[name]})
        assert out.startswith("<<EXC "), f"djust rendered {out!r}"
        assert not out.startswith("<<PANIC"), out

    @pytest.mark.parametrize("name", sorted(SCALARS))
    def test_the_message_is_djangos_own(self, name: str) -> None:
        """The exception CLASS still differs — every djust render error crosses
        PyO3 as a `RuntimeError` — so the assertion is on the wording, which
        djust carries verbatim under a `Template error: ` prefix."""
        value = SCALARS[name]
        with pytest.raises(TypeError) as django_exc:
            DjangoTemplate(SRC).render(DjangoContext({"p": value}))
        with pytest.raises(Exception) as djust_exc:
            _rust.render_template(SRC, {"p": value})
        assert str(djust_exc.value).endswith(str(django_exc.value)), (
            f"{name}: django={str(django_exc.value)!r} djust={str(djust_exc.value)!r}"
        )

    def test_the_type_name_is_pythons_not_the_rust_variants(self) -> None:
        """Four Rust variants, four Python names — and two of the four spell
        something the variant's own name does not: a `Value::BigInt` is a
        Python `int`, and a `Decimal` is qualified because `decimal` is not a
        builtin."""
        expected = {
            True: "bool",
            42: "int",
            12345678901234567890: "int",
            1.5: "float",
            decimal.Decimal("2.5"): "decimal.Decimal",
        }
        for value, type_name in expected.items():
            out = djust_out({"p": value})
            assert out.endswith(f"'{type_name}' object is not iterable>>"), out

    @pytest.mark.parametrize("name", sorted(SCALARS))
    def test_the_refusal_never_puts_the_operand_on_the_page(self, name: str) -> None:
        out = djust_out({"p": SCALARS[name]})
        assert str(SCALARS[name]) not in out, out

    def test_a_nested_loop_refuses_too(self) -> None:
        src = "{% for x in p %}{% for y in x %}[{{ y }}]{% endfor %}{% endfor %}"
        ctx = {"p": [42]}
        try:
            DjangoTemplate(src).render(DjangoContext(dict(ctx)))
            pytest.fail("premise: Django must refuse the inner loop")
        except TypeError as exc:
            django_message = str(exc)
        with pytest.raises(Exception) as djust_exc:
            _rust.render_template(src, dict(ctx))
        assert str(djust_exc.value).endswith(django_message)

    def test_a_filtered_operand_that_resolves_to_a_scalar_refuses(self) -> None:
        """`{% for x in p|length %}` — the shape the operand sweeps reach it
        by, and the one `test_filtered_operands_and_slice_2325_2326.py`'s
        residue classifier used to file under `django-raised`."""
        src = "{% for x in p|length %}[{{ x }}]{% empty %}E{% endfor %}"
        ctx = {"p": ["a", "b"]}
        with pytest.raises(TypeError):
            DjangoTemplate(src).render(DjangoContext(dict(ctx)))
        with pytest.raises(Exception):
            _rust.render_template(src, dict(ctx))


class TestTheAnswersThatMustNotMove:
    """Everything Django does NOT refuse still agrees, byte for byte."""

    @pytest.mark.parametrize("name", sorted(SEQUENCES))
    def test_a_sequence_operand_is_untouched(self, name: str) -> None:
        value = SEQUENCES[name]
        assert djust_out({"p": value}) == django_out({"p": value})

    def test_none_still_takes_the_empty_branch(self) -> None:
        assert djust_out({"p": None}) == "E"

    def test_an_ABSENT_operand_still_takes_the_empty_branch(self) -> None:
        """The arm that is NOT folded into the refusal, and the reason it is
        separate: `Value::Missing` is Django's `ignore_failures` answer, and
        collapsing it into the raise would 500 every template whose loop
        operand is simply not in the context."""
        assert djust_out({}) == "E"
        assert django_out({}) == "E"

    def test_an_empty_sequence_still_takes_the_empty_branch(self) -> None:
        for value in ([], (), {}, ""):
            assert djust_out({"p": value}) == "E"
            assert django_out({"p": value}) == "E"


# ---------------------------------------------------------------------------
# What this does NOT close
# ---------------------------------------------------------------------------


class TestTheWireResidueIsNamed:
    """A value with no `Value` variant reaches the renderer as its `str()`.

    So it is a SEQUENCE by the time `Node::For` sees it and iterates character
    by character, where Django raises. No arm here can recover the type; it is
    the boundary's defect (#2214 / #2366 family), filed separately.

    These go red the day the boundary carries the type, which is the signal to
    rewrite them as parity assertions.
    """

    #: Django REFUSES these — no `__len__`, not iterable — and djust iterates
    #: the `str()` it received instead.
    RESIDUE: dict[str, object] = {
        "date": datetime.date(2020, 1, 2),
        "datetime": datetime.datetime(2020, 1, 2, 3, 4),
        "time": datetime.time(3, 4),
        "timedelta": datetime.timedelta(days=1),
        "object()": object(),
    }

    #: And the other direction of the SAME mechanism, which a residue list of
    #: refusals alone would have missed: a `set` IS iterable in Django, so it
    #: renders its elements — and djust renders the characters of `"{1}"`.
    #: The wire loses the type either way; only which side is wrong changes.
    ITERABLE_RESIDUE: dict[str, object] = {"set": {1}}

    @pytest.mark.parametrize("name", sorted(RESIDUE))
    def test_django_refuses_and_djust_iterates_the_string(self, name: str) -> None:
        value = self.RESIDUE[name]
        assert django_refuses(value), f"Django moved for {name}"
        out = djust_out({"p": value})
        assert not out.startswith("<<EXC "), f"{name}: djust now refuses — close this pin"
        assert out.startswith("["), out

    @pytest.mark.parametrize("name", sorted(ITERABLE_RESIDUE))
    def test_django_iterates_the_value_and_djust_the_string(self, name: str) -> None:
        value = self.ITERABLE_RESIDUE[name]
        assert not django_refuses(value), f"Django moved for {name}"
        assert djust_out({"p": value}) != django_out({"p": value})
        assert djust_out({"p": value}) == djust_out({"p": str(value)})

    def test_it_is_the_STRING_that_is_iterated_and_not_something_else(self) -> None:
        """The mechanism, stated so the next reader does not re-derive it: the
        rendered output is exactly what the same loop over `str(value)`
        renders."""
        value = datetime.date(2020, 1, 2)
        assert djust_out({"p": value}) == djust_out({"p": str(value)})


class TestTheOneShapeDjustIsNowSTRICTERAbout:
    """An `IntFlag` member is iterable in Python 3.11+, and Django iterates it.

    It reaches this renderer as a plain `Value::Integer` — its int value — so
    djust cannot iterate it correctly whatever this arm does. It rendered the
    empty branch before and refuses now: the cell diverged then and diverges
    now, in the STRICTER direction rather than the permissive one, which is
    the direction to fail in. Named rather than left as a surprise.
    """

    class Colour(enum.IntFlag):
        A = 1
        B = 2

    def test_django_iterates_it_and_djust_refuses(self) -> None:
        value = self.Colour.A | self.Colour.B
        assert django_out({"p": value}) == "[1][2]"
        assert djust_out({"p": value}).startswith("<<EXC ")

    def test_it_diverged_before_this_change_too(self) -> None:
        """Non-regression, stated as the property rather than as a claim about
        history: whatever djust answers, it is not Django's, and the previous
        answer (`'E'`) was not Django's either."""
        value = self.Colour.A | self.Colour.B
        assert django_out({"p": value}) not in ("E", djust_out({"p": value}))


# ---------------------------------------------------------------------------
# The corpus gap
# ---------------------------------------------------------------------------


class TestTheCorpusGapThatHidTheShapesFromTheDifferential:
    """`for-bare` existed; the INPUTS to put through it did not.

    The corpus built `{% for x in p %}` over every input all along, and every
    scalar input was an `int` or a `float` — so it could reach the refusal arm
    for two of the four Rust variants that land there and for neither of the
    two shapes the issue's own table leads with (`True` / `False`).
    """

    @staticmethod
    def _module():
        spec = importlib.util.spec_from_file_location("_fpd_2382", DIFFERENTIAL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_corpus_carries_an_input_of_every_refused_python_type(self) -> None:
        """Derived from DJANGO: which types Django refuses is measured, and
        the corpus must carry one of each.

        Not read from djust's own `python_type_name_for_iteration` — a
        requirement taken from the code under test is satisfiable by the
        omission it exists to detect.
        """
        required = {type(v).__name__ for v in SCALARS.values() if django_refuses(v)}
        carried = {type(v).__name__ for v in self._module().INPUTS.values() if django_refuses(v)}
        assert required <= carried, sorted(required - carried)

    def test_the_axis_requires_djangos_three_outcomes(self) -> None:
        module = self._module()
        assert set(module._required_for_outcomes()) == {
            "empty-branch",
            "refused",
            "iterated",
        }
        assert set(module._required_for_outcomes()) <= module._swept_for_outcomes()

    def test_the_axis_goes_red_when_the_non_iterable_inputs_go(self) -> None:
        """The empirical canary: with every input Django refuses removed, the
        axis reports `refused` MISSING rather than reporting clean."""
        module = self._module()
        module.INPUTS = {k: v for k, v in module.INPUTS.items() if not django_refuses(v)}
        assert "refused" not in module._swept_for_outcomes()

    def test_the_axis_goes_red_when_the_bare_for_shape_goes(self) -> None:
        """And the other half: the shape, not just the inputs. Read out of the
        shapes the corpus BUILDS, so deleting `for-bare` is noticed."""
        module = self._module()
        module.PATH_SHAPES = {k: v for k, v in module.PATH_SHAPES.items() if v != SRC}
        assert module._swept_for_outcomes() == set()

    def test_the_branch_patterns_still_match_djangos_source(self) -> None:
        """`_required_for_outcomes` raises rather than silently shrinking when
        Django rewrites `ForNode.render` — the `_ARG_ERROR_MARK` lesson."""
        module = self._module()
        assert len(module._FOR_OUTCOME_BRANCHES) == 3
        module._required_for_outcomes()  # must not raise
