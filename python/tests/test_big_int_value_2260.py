"""A Python ``int`` past ``i64`` keeps its digits (#2260).

``Value::Integer`` is an ``i64``; a Python ``int`` is arbitrary-precision. Past
``2**63 - 1`` the ``i64`` arm of ``FromPyObject`` failed and the next arm that
matched was ``extract::<f64>()``, so ``12345678901234567890`` reached the
renderer as a binary double and ``{{ p }}`` rendered ``12345678901234567000``.
Reachable from a ``Sum()`` aggregate, a nanosecond timestamp product, or an id
from an external system — and every string filter inherited it, which is how the
#2250 sweep surfaced it.

Everything here is a **differential against real Django**, because Django is
importable in this suite and the answer to "what does Django do" is a subprocess
away (v1.1.1-2 retro). The variant's own behaviour — encodings, ``Display``, the
round trip — is pinned in ``crates/djust_core/tests/test_bigint_value_2260.rs``;
what only Python can see is the PyO3 boundary and the trip back out.

**#2260 and #2265 are the same shape at two layers, and neither fix subsumes the
other.** #2260 is the REPRESENTATION losing digits before any filter runs;
#2265 is a filter throwing away digits that arrived intact. Fixing only the
boundary leaves ``{{ p|stringformat:"d" }}`` printing ``9223372036854775807``;
fixing only the filter leaves ``{{ p }}`` printing ``…567000``. The pair is
asserted in ``test_the_two_layers_are_independent`` below.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

I64_MAX = 2**63 - 1
I64_MIN = -(2**63)
I128_MAX = 2**127 - 1

# Values that do NOT fit an i64, at every boundary that matters.
PAST_I64 = [
    I64_MAX + 1,
    I64_MAX + 2,
    I64_MIN - 1,
    2**64,
    2**64 + 1,
    12345678901234567890,
    -12345678901234567890,
    I128_MAX,
    I128_MAX + 1,
    -(2**127) - 1,
    1234567890123456789012345,
    int("9" * 60),
    -int("9" * 60),
    10**300,
]

# Values that DO fit, and must therefore stay `Value::Integer`.
INSIDE_I64 = [0, 1, -1, 2**53, 2**53 + 1, I64_MAX, I64_MIN]


def render_both(source: str, value: Any) -> tuple[str, str]:
    """``(django, djust)`` for one cell, rendering the SAME value through both."""
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


@pytest.mark.parametrize("value", PAST_I64 + INSIDE_I64, ids=repr)
def test_a_bare_render_agrees_with_django(value: int) -> None:
    """``{{ p }}`` is where the issue's own reproducer lives."""
    django_out, djust_out = render_both("{{ p }}", value)
    assert djust_out == django_out
    assert djust_out == str(value)


@pytest.mark.parametrize(
    "source",
    [
        "{{ p|upper }}",
        "{{ p|lower }}",
        "{{ p|truncatechars:30 }}",
        "{{ p|make_list|length }}",
        '{{ p|stringformat:"s" }}',
        '{{ p|stringformat:"d" }}',
        "{{ p|add:1 }}",
        "{{ p|get_digit:1 }}",
        "{{ p|pprint }}",
        "{{ p|slice:':4' }}",
        "{{ p|center:30 }}",
        "{% if p > 10 %}gt{% else %}le{% endif %}",
    ],
)
@pytest.mark.parametrize("value", [12345678901234567890, -12345678901234567890], ids=repr)
def test_every_filter_that_reads_the_digits_agrees(source: str, value: int) -> None:
    """The issue's point: this is not a filter boundary problem.

    Every string filter inherited the truncation because the VALUE was already
    lossy, so a boundary fix is what closes all of them at once. Included here
    rather than in each filter's own file because the shared cause is the point.
    """
    django_out, djust_out = render_both(source, value)
    assert djust_out == django_out


def test_the_two_layers_are_independent() -> None:
    """#2260 and #2265 are the same shape at two layers; neither subsumes the other.

    The boundary fix (#2260) is what makes ``{{ p }}`` exact. The filter fix
    (#2265) is what stops ``stringformat:"d"`` from saturating digits that
    arrived intact — which it does for a ``Decimal`` whether or not the ``int``
    boundary is fixed, and did for the ``int`` too before both landed.
    """
    from decimal import Decimal

    # Layer 1: the value itself. A Decimal already arrived exact (#2214), so
    # this cell is about the int boundary alone.
    assert render_both("{{ p }}", 12345678901234567890)[1] == "12345678901234567890"
    # Layer 2: the filter. This value never went near the int boundary.
    django_out, djust_out = render_both(
        '{{ p|stringformat:"d" }}', Decimal("12345678901234567890.123456789")
    )
    assert djust_out == django_out == "12345678901234567890"


@pytest.mark.parametrize("value", PAST_I64, ids=repr)
def test_the_value_comes_back_out_of_the_state_round_trip_as_a_python_int(value: int) -> None:
    """The half a shared ``Value::Decimal`` could NOT have done.

    A handler that put an ``int`` in the context must read an ``int`` back out
    of it, or every ``isinstance(x, int)`` downstream changes answer. This is
    the exact path ``InMemoryStateBackend.get()`` takes — the msgpack trip that
    the binary tag exists for, and the reason a ``Decimal`` here would have been
    a silent type change that leaves the process.

    It exercises BOTH directions at once: the tag has to be written on the way
    in and recognised on the way out. #2214 shipped with an encode-only
    assertion that stayed green through exactly this gap (#2135).
    """
    from djust._rust import RustLiveView

    view = RustLiveView("{{ p }}")
    view.set_state("p", value)
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())

    back = restored.get_state()["p"]
    assert isinstance(back, int), f"came back as {type(back).__name__}: {back!r}"
    assert not isinstance(back, bool)
    assert back == value
    # And it still renders, which a `Value::String` would also have done —
    # which is why the type assertion above is the load-bearing one.
    assert restored.render() == str(value)


def test_a_bool_is_still_a_bool_and_not_a_one_digit_int() -> None:
    """``bool`` is an ``int`` SUBCLASS, so the new arm must not claim it.

    It cannot in practice — the ``bool`` arm runs first and every bool fits an
    ``i64`` anyway — but the rule is pinned rather than left to that accident,
    because the digit extraction narrows through ``int(ob)`` for exactly this
    class of subclass.
    """
    assert render_both("{{ p }}", True) == ("True", "True")
    assert render_both("{{ p }}", False) == ("False", "False")


def test_an_int_subclass_with_its_own_str_still_yields_digits() -> None:
    """``str(ob)`` would have been the obvious extraction and is wrong.

    An ``int`` subclass may spell itself any way it likes; ``int(ob)`` narrows
    to a plain ``int`` first, so the digits are the value's and not its
    ``__str__``'s. Django renders the subclass's ``__str__`` here, so djust
    does NOT agree on this cell — what is asserted is that the digits are not
    silently corrupted into something that would parse back as a different
    number.
    """

    class Weird(int):
        def __str__(self) -> str:  # pragma: no cover - exercised via render
            return "not-a-number"

    value = Weird(2**70)
    _, djust_out = render_both("{{ p }}", value)
    assert djust_out == str(int(value))


def test_a_big_int_nested_in_a_list_renders_bare_digits() -> None:
    """``repr(int)`` is the digits; ``repr(Decimal('1'))`` is ``Decimal('1')``.

    The concrete reason this is not ``Value::Decimal``: containers render their
    elements through ``repr``, so sharing the variant would have printed
    ``[Decimal('12345678901234567890')]`` where Django prints the digits.
    """
    django_out, djust_out = render_both("{{ p }}", [12345678901234567890])
    assert djust_out == django_out == "[12345678901234567890]"


def test_the_comparison_operators_still_work() -> None:
    """The #2244 hole, one variant over.

    A big int arrived as a ``Float`` before this variant and took
    ``compare_values``' ``(Float, Integer)`` arm. As a variant with no arm it
    fell to the numeric-pair wildcard, which admitted only
    {Integer, Float, Decimal}, returned ``None``, and yielded 0 — "equal", so
    BOTH ``>`` and ``<`` were false and the template silently took the wrong
    branch. Caught as a regression by the #2260 differential, not by inspection.
    """
    for value in (12345678901234567890, 2**200):
        assert render_both("{% if p > 10 %}gt{% else %}le{% endif %}", value) == ("gt", "gt")
        assert render_both("{% if p < 10 %}lt{% else %}ge{% endif %}", value) == ("ge", "ge")
    assert render_both("{% if p > 10 %}gt{% else %}le{% endif %}", -(2**70)) == ("le", "le")


def test_add_is_arbitrary_precision() -> None:
    """Django's first ``add`` branch is ``int(value) + int(arg)``, unbounded.

    Every fixed width tried here has been wrong somewhere: ``i64`` saturated
    (#2253), ``i128`` gives up at 39 digits. The 60-digit case below was
    CORRECT on main only by coincidence — the value had arrived as the double
    ``1e60``, whose expansion is exactly the sum the filter was declining to
    compute, which is why the set comparison caught it and inspection would not.
    """
    for value in (int("9" * 60), 2**200, I64_MAX, I128_MAX):
        django_out, djust_out = render_both("{{ p|add:1 }}", value)
        assert djust_out == django_out == str(value + 1)
