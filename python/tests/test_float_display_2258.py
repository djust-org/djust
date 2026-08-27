"""``{{ f }}`` is ``numberformat.format``, not Rust's ``{}`` (#2258).

Django's ``{{ }}`` path for a float is two steps, both of which Rust's ``{}``
skips::

    if isinstance(number, float) and "e" in str(number).lower():
        number = Decimal(str(number))
    if isinstance(number, Decimal):   # >200-digit cut-off, else "{:f}"
    else:                             str_number = str(number)

Rust never uses exponent notation and spells the non-finite values ``NaN``/
``inf`` where Python gives ``nan``/``inf``. The ``{:.1}`` guard #2203 added was
a partial hand-port of the trailing-``.0`` case and could see neither.

**The issue's premise needed checking before it could be built on.** It states
that Django renders ``1e300`` as ``1e+300``, which is true — but
``python_float_repr``'s own doc-comment (written in #2253) states that
``{{ 1e20 }}`` correctly renders ``100000000000000000000``, which is ALSO true.
Both hold: Django's rule is the DIGIT COUNT, not the exponent form, so the same
value spells itself two ways depending on which side of the 200-digit cut-off it
falls. Rendering ``repr`` verbatim — the obvious reading of the issue — would
have regressed every float between ``1e16`` and ``1e200``. That is the case this
file's ``test_the_cut_off_is_the_digit_count`` exists for, and it was measured
against a live Django render rather than reasoned about.

The other half is the FILTER boundary, which the issue also names: Django's
``@stringfilter`` consumes ``str(value)``, which is ``repr`` — so ``{{ f }}``
and ``{{ f|upper }}`` legitimately disagree, and djust has to disagree the same
way.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

# Every magnitude band, both signs, both non-finite spellings, subnormals, and
# both sides of each of the two thresholds Django's rule has (the 200-digit
# cut-off, and CPython's own 1e-4 / 1e16 repr switch).
FLOATS = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    0.5,
    19.99,
    2.675,
    1e-5,
    1e-4,
    1e-3,
    1e15,
    1e16,
    1e17,
    1e20,
    1e100,
    1e199,
    1e200,
    1e201,
    1e300,
    -1e300,
    1.7976931348623157e308,
    1e-199,
    1e-200,
    1e-201,
    1e-300,
    5e-324,
    2.2250738585072014e-308,
    float(2**53),
    float(2**63),
    1.5e18,
    123456789.123456789,
    float("nan"),
    float("inf"),
    float("-inf"),
]


def render_both(source: str, value: Any) -> tuple[str, str]:
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


@pytest.mark.parametrize("value", FLOATS, ids=repr)
def test_a_bare_render_agrees_with_django(value: float) -> None:
    django_out, djust_out = render_both("{{ p }}", value)
    assert djust_out == django_out


@pytest.mark.parametrize(
    "source",
    ["{{ p|upper }}", "{{ p|lower }}", "{{ p|truncatechars:40 }}", "{{ p|center:40 }}"],
)
@pytest.mark.parametrize("value", FLOATS, ids=repr)
def test_the_string_filters_agree_too(source: str, value: float) -> None:
    """The other half of the issue: ``@stringfilter`` consumes ``str(value)``.

    So ``{{ 1e20 }}`` is ``100000000000000000000`` while ``{{ 1e20|upper }}`` is
    ``1E+20`` — Django really does spell one float two ways, and matching it
    means the renderer keeps ``Display`` and the string filters get ``repr``.
    Before #2258 the coercion could not have helped, because ``Display`` was
    Rust's ``{}``, which is neither spelling.
    """
    django_out, djust_out = render_both(source, value)
    assert djust_out == django_out


def test_the_three_shapes_the_issue_named() -> None:
    """The issue's own table, verbatim."""
    assert render_both("{{ p }}", 1e300) == ("1e+300", "1e+300")
    assert render_both("{{ p }}", float("nan")) == ("nan", "nan")
    assert render_both("{{ p }}", float("inf")) == ("inf", "inf")
    # And the 400-byte grouped-zeroes symptom it describes: `localize_if_number`
    # saw 301 digits where Django sees six characters.
    assert len(render_both("{{ p }}", 1e300)[1]) == 6


def test_the_cut_off_is_the_digit_count_and_not_the_exponent_form() -> None:
    """The premise-check that stops this fix from being a regression.

    ``str(1e20)`` IS exponent form, and rendering it verbatim would have been
    the obvious reading of "match Python's repr". Django expands it, because
    ``abs(20) + 1`` digits is under the cut-off. Only past 200 does the exponent
    survive. Every value here is a live Django render.
    """
    for value, expect_exponent in [
        (1e16, False),
        (1e20, False),
        (1e100, False),
        (1e199, False),
        (1e200, True),
        (1e300, True),
        (1e-199, False),
        (1e-200, True),
        (5e-324, True),
    ]:
        django_out, djust_out = render_both("{{ p }}", value)
        assert djust_out == django_out, f"{value!r}"
        assert ("e" in django_out) is expect_exponent, f"{value!r} -> {django_out!r}"


@pytest.mark.parametrize("n", [1, 2, 3, 5, 12])
@pytest.mark.parametrize("value", FLOATS, ids=repr)
def test_get_digit_reads_int_of_the_value_not_the_rendering(n: int, value: float) -> None:
    """The consequence of #2258 that a set comparison against ``main`` caught.

    Django's ``get_digit`` is ``int(str(int(value))[-arg])``: it indexes
    ``int(value)``, NOT the rendered string. Reading the rendering instead was
    invisible while ``Display`` expanded every float — it answered ``0`` from
    the 200 zeros of ``1e-200`` — and became WRONG the moment ``{{ 1e-200 }}``
    started rendering ``1e-200``, whose third-from-last character is ``2``.

    A fix's blast radius is not only what it fixes, which is why the
    non-regression check is a set comparison against a ``main`` build rather
    than a re-run of the new tests.
    """
    source = "{{ p|get_digit:" + str(n) + " }}"
    try:
        django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    except (OverflowError, ValueError):
        # Django's `except` here covers only the `int(value)` ValueError and the
        # `str(value)[-arg]` IndexError, so `int(inf)`'s OverflowError and the
        # `int('-')` a negative value's sign position produces both escape and
        # 500 the page. djust renders rather than 500ing on a value it used to
        # render — a documented divergence, asserted rather than skipped so it
        # cannot silently become a fabricated digit.
        djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
        assert djust_out == str(value), f"{value!r} -> {djust_out!r}"
        return
    _, djust_out = render_both(source, value)
    assert djust_out == django_out


@pytest.mark.parametrize("value", FLOATS, ids=repr)
def test_a_float_nested_in_a_list_agrees_too(value: float) -> None:
    """Containers render their elements through ``repr``, not ``Display``.

    ``str([1e300])`` is ``[1e+300]`` — Python's list repr uses ``repr`` on the
    element, so a nested float takes the OTHER spelling, and the two must not be
    confused. Included because ``py_repr`` delegates to ``Display`` for a float
    and that delegation is the thing that could quietly become wrong.
    """
    django_out, djust_out = render_both("{{ p }}", [value])
    assert djust_out == django_out
