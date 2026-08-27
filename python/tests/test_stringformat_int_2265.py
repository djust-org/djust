"""``stringformat:"d"`` prints ``int(value)``, not a saturated ``i64`` (#2265).

Django is ``("%" + arg) % value``, catching ``(ValueError, TypeError)`` and
returning ``""``. The ``d``/``i`` arm computed an ``i64`` through ``as_f64()``,
so it carried BOTH of the losses #2253 had already fixed one filter over: a
double holds ~15 significant digits, so ``int()`` was off by one from 2^53 up,
and ``as i64`` SATURATES, so every value past 2^63 rendered
``9223372036854775807`` — a fabricated constant where an id or a money column
was meant, silently, with no warning.

**The issue's own framing needed one correction.** It calls this "the #2253
defect, one filter over", which is right about the DECIMAL path — but the same
arm also saturated a plain ``float``, and ``"%d" % 1e300`` is the exact binary
expansion, which is neither ``i64::MAX`` nor ``10**300``. Fixing only the
``Decimal`` branch would have left ``{{ 1e300|stringformat:"d" }}`` printing the
fabricated constant the issue calls the sharpest form of the bug.

Its group-3 question ("worth deciding explicitly rather than falling out of the
group-2 fix") is decided here, against CPython rather than by reasoning: ``%d``
raises ``TypeError`` for a ``str`` — a NUMERIC string included, so
``{{ "42"|stringformat:"d" }}`` is empty in Django, which the old
``parse::<i64>()`` fallback disagreed with in the other direction as well as the
``0``-for-garbage one the issue names.

The Rust-side table is ``crates/djust_templates/tests/test_stringformat_int_2265.rs``;
everything here renders the same cells through real Django so the two cannot
drift.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

# Every value CPython's `%d` accepts, across the widths that broke.
ACCEPTED = [
    0,
    1,
    -1,
    2**53,
    2**53 + 1,
    2**63 - 1,
    2**63,
    -(2**63),
    12345678901234567890,
    -12345678901234567890,
    2**127,
    True,
    False,
    1.5,
    -1.5,
    -0.0,
    1e15,
    1e16,
    1e20,
    1e300,
    -1e300,
    float(2**63),
    Decimal("0"),
    Decimal("19.99"),
    Decimal("-19.99"),
    Decimal("9007199254740993"),
    Decimal("12345678901234567890.123456789"),
    Decimal("1E+3"),
    Decimal("1E-3"),
    Decimal("1E+400"),
    Decimal("-1E+400"),
]

# Every value it refuses. `%d` raises TypeError for these; Django's
# `except (ValueError, TypeError)` turns that into `""`.
REFUSED = ["abc", "1.5", "42", "", None, [1, 2], {"a": 1}]

SPECS = ["d", "i", "05d", "8d", "1d"]


def render_both(source: str, value: Any) -> tuple[str, str]:
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


@pytest.mark.parametrize("spec", SPECS)
@pytest.mark.parametrize("value", ACCEPTED, ids=repr)
def test_an_accepted_value_agrees_with_django(spec: str, value: Any) -> None:
    django_out, djust_out = render_both('{{ p|stringformat:"' + spec + '" }}', value)
    assert djust_out == django_out


@pytest.mark.parametrize("spec", SPECS)
@pytest.mark.parametrize("value", REFUSED, ids=repr)
def test_a_refused_value_renders_empty_as_django_does(spec: str, value: Any) -> None:
    """Group 3, decided explicitly.

    djust rendered ``0`` — a fabricated number for an input with no numeric
    reading at all. Django renders nothing, and nothing is the honest answer.
    """
    django_out, djust_out = render_both('{{ p|stringformat:"' + spec + '" }}', value)
    assert django_out == ""
    assert djust_out == django_out


def test_the_three_groups_the_issue_measured() -> None:
    """The issue's own table, verbatim."""
    # Group 1: `int()` through an f64 was off by one from 2^53 up.
    assert render_both('{{ p|stringformat:"d" }}', Decimal("9007199254740993")) == (
        "9007199254740993",
        "9007199254740993",
    )
    # Group 2: past 2^63, `as i64` saturated to a fabricated constant.
    assert render_both('{{ p|stringformat:"d" }}', Decimal("12345678901234567890.123456789")) == (
        "12345678901234567890",
        "12345678901234567890",
    )
    # Group 2, sharpest: a value with no plausible reading as that number.
    django_out, djust_out = render_both('{{ p|stringformat:"d" }}', Decimal("1E+400"))
    assert djust_out == django_out
    assert len(django_out) == 401
    # Group 3.
    for value in ("abc", None, "1.5"):
        assert render_both('{{ p|stringformat:"d" }}', value) == ("", "")


def test_a_float_is_the_binary_value_the_issue_did_not_mention() -> None:
    """The correction to the issue's framing, measured.

    ``int(1e300)`` is the exact expansion of mantissa x 2^exp — NOT ``10**300``,
    and not ``i64::MAX``. Fixing only the ``Decimal`` branch the issue names
    would have left this cell printing the fabricated constant.
    """
    django_out, djust_out = render_both('{{ p|stringformat:"d" }}', 1e300)
    assert djust_out == django_out
    assert len(django_out) == 301
    assert django_out != "1" + "0" * 300, "a binary double is not the decimal literal"
    assert "9223372036854775807" not in django_out


def test_zero_padding_is_sign_aware() -> None:
    """``"%05d" % -1`` is ``-0001``, not ``000-1``.

    A ``{:0>width$}`` on the already-formatted string pads in FRONT of the
    minus. Same arm, same fix pass — reported by the differential, not by the
    issue.
    """
    assert render_both('{{ p|stringformat:"05d" }}', -1) == ("-0001", "-0001")
    assert render_both('{{ p|stringformat:"05d" }}', Decimal("-19.99")) == ("-0019", "-0019")
    assert render_both('{{ p|stringformat:"5d" }}', -1) == ("   -1", "   -1")


def test_a_non_finite_does_not_print_a_fabricated_constant() -> None:
    """``int(nan)`` raises ``ValueError``; ``int(inf)`` raises ``OverflowError``.

    Django catches only the first, so it 500s on the second. djust renders
    ``""`` for both rather than 500ing on a value it previously rendered — a
    documented, one-cell divergence, and strictly better than the
    ``9223372036854775807`` that was there.
    """
    assert render_both('{{ p|stringformat:"d" }}', float("nan")) == ("", "")
    assert render_both('{{ p|stringformat:"d" }}', Decimal("NaN")) == ("", "")
    for value in (float("inf"), float("-inf"), Decimal("Infinity")):
        with pytest.raises(OverflowError):
            DjangoTemplate('{{ p|stringformat:"d" }}').render(DjangoContext({"p": value}))
        assert (
            _rust.render_template('{{ p|stringformat:"d" }}', normalize_django_value({"p": value}))
            == ""
        )


def test_a_decimal_past_the_int_str_digit_limit_is_empty_rather_than_a_hang() -> None:
    """CPython's ``sys.get_int_max_str_digits()`` is what really bounds this.

    Past 4300 digits ``"%d" % d`` raises ``ValueError``, which Django's
    ``except`` DOES catch — so ``""`` is real parity here, not a fail-soft. And
    it is what bounds the allocation: ``Decimal('1E+400000000')`` is twelve
    bytes that would otherwise ask for 400 MB. CPython genuinely hangs on that
    one, so djust returning is a deliberate divergence in djust's favour.
    """
    import sys

    assert sys.get_int_max_str_digits() == 4300, "the constant this fix pins"
    assert render_both('{{ p|stringformat:"d" }}', Decimal("1E+4299"))[1] != ""
    assert render_both('{{ p|stringformat:"d" }}', Decimal("1E+5000")) == ("", "")
    assert (
        _rust.render_template(
            '{{ p|stringformat:"d" }}',
            normalize_django_value({"p": Decimal("1E+400000000")}),
        )
        == ""
    )
