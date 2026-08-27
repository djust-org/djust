"""A scientific-form `Decimal`'s coefficient must be localized as Django does (#2242).

Split out of #2240. Past Django's ``>200``-digit cutoff a ``Decimal`` renders in
scientific form, and ``localize_number_with`` bailed on any string holding an
``e`` — so under ``de`` ``Decimal('1.230E-250')`` rendered ``1.230e-250`` where
Django gives ``1,230e-250``.

**Not a regression**: before #2214 the value was an f64 and rendered further from
Django than the current output. This is a residual gap inside a strict
improvement.

Why a randomized differential and not a table
---------------------------------------------
The issue carries a five-row table. A curated table samples the axis its author
thought of and is blind on the next one (v1.1.1-2 retro) — here the axes are
locale, grouping flag, exponent sign, sign, and which side of the cutoff the
value falls on, and their product is not something to enumerate by hand. Django
is importable, so the reference answer is a call away.

What this file does NOT cover, deliberately
-------------------------------------------
The issue's comment folds in two more consequences — ``truncatechars`` and
``make_list|first`` seeing the scientific form where Django's string filters see
``str(Decimal)`` — on the premise that "one fix covers all three". Measured after
this fix, that premise was **false**, and the measurement is pinned below by
:func:`test_the_string_filter_divergence_was_a_separate_root_cause`: those
filters never reach ``localize_number`` at all, and their divergence was not
even confined to values past the cutoff. Fixed separately in #2250 (#1079) by a
coercion at the filter boundary; that test now asserts the parity rather than
the divergence, and per-filter coverage lives in
``test_string_filter_stringification_2250.py``.

The number format is process-global (a Rust thread-local), so every test here
restores it in a ``finally``. A reset fixture leaking ``NUMBER_GROUPING=0`` into
every later test in the worker is a real incident from this repo's history.
"""

from __future__ import annotations

import random
from decimal import Decimal, localcontext

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import translation  # noqa: E402

from djust import _rust, render_env  # noqa: E402

#: `fr` is in the list because it separates thousands with U+00A0, which a test
#: written with a plain space would pass while shipping the wrong byte; `hi`
#: because its grouping is `[3, 2, 0]` rather than three-at-a-time.
LANGUAGES = ["en-us", "de", "fr", "hi", "ru", "ja", "es", "pl"]

#: Named cases: the issue's own rows, plus the boundaries around them.
NAMED = [
    Decimal("1.230E-250"),  # the issue's first row
    Decimal("-1.5E+300"),  # the issue's second row — negative, positive exponent
    Decimal("1234.56"),  # ordinary; must not change
    Decimal("19.99"),  # money; must not change
    Decimal("1E-9"),  # exponent form that does NOT reach the cutoff
    Decimal("0E-250"),  # zero coefficient past the cutoff
    Decimal("-0E-250"),
    Decimal("1" + "0" * 205),  # 206 digits, no exponent in the source
    Decimal("9" * 201),
    Decimal("1E-201"),  # just past
    Decimal("1E-199"),  # just short
    Decimal("1.5E+1000"),
    Decimal("-1.5E-1000"),
]


def _sample(rng: random.Random) -> Decimal:
    """A value from one of the shapes that reach — or nearly reach — the cutoff."""
    kind = rng.randrange(6)
    sign = "-" if rng.random() < 0.4 else ""
    if kind == 0:  # money-shaped
        return Decimal(
            f"{sign}{rng.randrange(10 ** rng.randrange(1, 12))}.{rng.randrange(100):02d}"
        )
    if kind == 1:  # plain integer, grouping-relevant
        return Decimal(f"{sign}{rng.randrange(10 ** rng.randrange(1, 15))}")
    if kind == 2:  # straddling the cutoff, both exponent signs
        coeff = "".join(str(rng.randrange(10)) for _ in range(rng.randrange(1, 12)))
        exp = rng.choice([-1, 1]) * rng.randrange(180, 220)
        return Decimal(f"{sign}{coeff or '1'}E{exp:+d}")
    if kind == 3:  # deep scientific
        coeff = "".join(str(rng.randrange(10)) for _ in range(rng.randrange(1, 30)))
        exp = rng.choice([-1, 1]) * rng.randrange(200, 5000)
        return Decimal(f"{sign}{coeff or '1'}E{exp:+d}")
    if kind == 4:  # `0.xxx` forms and zeros — the shape #2240 round four missed
        return Decimal(
            rng.choice(
                [
                    f"{sign}0.{'0' * rng.randrange(0, 8)}{rng.randrange(1, 999)}",
                    f"{sign}0E{rng.choice([-1, 1]) * rng.randrange(0, 400)}",
                    f"{sign}0.00",
                ]
            )
        )
    with localcontext() as ctx:  # a high-precision division, i.e. ordinary code
        ctx.prec = rng.randrange(2, 260)
        return Decimal(rng.randrange(1, 10**6)) / Decimal(rng.randrange(1, 10**6))


def _render_both(source: str, value: Decimal) -> tuple[str, str]:
    return (
        DjangoTemplate(source).render(DjangoContext({"p": value})),
        _rust.render_template(source, {"p": value}),
    )


def _differential(values, languages=LANGUAGES, groupings=(True, False)):
    """Render every (value x language x grouping) both ways; return the mismatches.

    The number format is a Rust thread-local — restored unconditionally, so a
    failure inside cannot leak a locale into the rest of the worker.
    """
    mismatches = []
    scientific = 0
    total = 0
    try:
        for use_grouping in groupings:
            for language in languages:
                with (
                    override_settings(USE_THOUSAND_SEPARATOR=use_grouping),
                    translation.override(language),
                ):
                    render_env.apply_number_format()
                    for value in values:
                        expected, got = _render_both("{{ p }}", value)
                        total += 1
                        if "e" in expected:
                            scientific += 1
                        if expected != got:
                            mismatches.append((language, use_grouping, str(value), expected, got))
    finally:
        render_env.apply_number_format()
    return total, scientific, mismatches


def test_the_issues_own_rows() -> None:
    """The two rows the issue reports, against a live Django rather than its table."""
    try:
        with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
            render_env.apply_number_format()
            for value, django_says in (
                (Decimal("1.230E-250"), "1,230e-250"),
                (Decimal("-1.5E+300"), "-1,5e+300"),
            ):
                expected, got = _render_both("{{ p }}", value)
                # The literal is the issue's claim; asserting it against Django
                # first is what makes the second assertion mean anything.
                assert expected == django_says, (
                    f"the issue claims Django renders {value} as {django_says!r}; "
                    f"this Django renders {expected!r}"
                )
                assert got == expected, f"{value}: django={expected!r} djust={got!r}"
    finally:
        render_env.apply_number_format()


def test_named_cases_match_django_in_every_locale() -> None:
    total, scientific, mismatches = _differential(NAMED)
    assert not mismatches, f"{len(mismatches)}/{total} diverged, e.g. {mismatches[:5]}"
    assert scientific, "no case reached the scientific form — the suite proves nothing"


def test_a_randomized_sweep_matches_django() -> None:
    """400 values x 8 locales x 2 grouping flags.

    Seeded, so a failure is reproducible; the seed is arbitrary and any other
    would do.
    """
    rng = random.Random(20242)
    values = [_sample(rng) for _ in range(400)]
    total, scientific, mismatches = _differential(values)
    assert not mismatches, f"{len(mismatches)}/{total} diverged, e.g. {mismatches[:5]}"
    # Not a decoration: without it a `_sample` that stopped producing
    # past-the-cutoff values would leave this green while covering nothing.
    assert scientific > total // 10, f"only {scientific}/{total} cases reached scientific form"


def test_the_exponent_is_not_localized() -> None:
    """Django rejoins with the exponent verbatim — it never re-enters `format()`.

    The distinguishing case is an exponent long enough to be grouped: a
    localized exponent would read `e+1.234` under `de`. No `Decimal` can carry
    one that long (CPython's `MAX_EMAX` is 999999999999999999, but the rendered
    exponent for a real value stays small), so this is asserted at the string
    boundary in `crates/djust_core/tests/test_scientific_localization_2242.rs`;
    what is checkable here is that the sign and digits survive untouched.
    """
    try:
        with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
            render_env.apply_number_format()
            for value in (Decimal("1.5E+1000"), Decimal("1.5E-1000")):
                expected, got = _render_both("{{ p }}", value)
                assert got == expected
                # The exponent as the value itself carries it, sign included —
                # byte-for-byte what comes out the far side of the rejoin.
                assert got.split("e")[1] == str(value).split("E")[1]
    finally:
        render_env.apply_number_format()


def test_ordinary_values_are_unchanged_by_the_scientific_branch() -> None:
    """The guard on the change: nothing below the cutoff may move.

    Asserted against Django rather than against a remembered djust output, so it
    is a parity claim and not a snapshot of whatever djust happens to do.
    """
    ordinary = [
        Decimal("1234.56"),
        Decimal("19.99"),
        Decimal("0.00"),
        Decimal("-3.5"),
        Decimal("1234567"),
        Decimal("12345678901234567890.123456789"),
        Decimal("1E-9"),
        Decimal("0.000000001"),
    ]
    total, scientific, mismatches = _differential(ordinary)
    assert not mismatches, f"{len(mismatches)}/{total} diverged, e.g. {mismatches[:5]}"
    assert scientific == 0, "these values are not supposed to reach the scientific form"


def test_floatformat_is_untouched_by_the_scientific_branch() -> None:
    """`floatformat` shares `localize_number`, so it is in this change's blast radius.

    It cannot reach the new arm — its input is Rust's `{:.N}` output, which never
    carries an `e` — but "cannot" is worth pinning rather than reasoning about.
    """
    try:
        with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
            render_env.apply_number_format()
            for source, value, expected in (
                ("{{ p|floatformat }}", Decimal("1234.56"), "1.234,6"),
                ("{{ p|floatformat:2 }}", Decimal("1234.567"), "1.234,57"),
                ("{{ p|floatformat:2u }}", Decimal("1234.567"), "1234.57"),
                ("{{ p|floatformat:2 }}", Decimal("1E-250"), "0,00"),
            ):
                got = _rust.render_template(source, {"p": value})
                assert got == expected, f"{source} on {value}: {got!r}"
    finally:
        render_env.apply_number_format()


def test_the_string_filter_divergence_was_a_separate_root_cause() -> None:
    """#2242's comment folded in `truncatechars` / `make_list|first`; #2250 closed them.

    The comment's premise was "one fix covers all three". It was wrong twice —
    a different mechanism (Django's string filters are `@stringfilter` and
    consume ``str(Decimal)``; djust's consumed `Display`, the number-rendered
    form, and neither side goes anywhere near `localize_number`), and not
    confined to the cutoff (``Decimal('1E-9')`` is nine digits and diverged).

    This started life as a *characterization* test asserting that divergence, so
    that whoever fixed the string-filter class would have to turn it red and
    update it deliberately. #2250 did: a `Value::Decimal` reaching one of
    Django's `@stringfilter`s is now coerced to the ``str()`` form the variant
    already carries. The assertions below are the same four cases, flipped from
    "these diverge" to "these agree".

    The *separate-root-cause* half of the claim survives the fix and is what
    this file still needs to state: #2250's coercion is at the filter boundary
    and touches nothing `localize_number` does, which is why the bare-render
    assertion at the bottom is unchanged. Per-filter coverage lives in
    ``test_string_filter_stringification_2250.py``.
    """
    try:
        render_env.apply_number_format()
        # Past the cutoff: the forms differed only in `str()`'s uppercase `E`.
        big = Decimal("6.25E-2244")
        dj, du = _render_both("{{ p|truncatechars:8 }}", big)
        assert dj == "6.25E-2…" and du == dj, (dj, du)

        # BELOW the cutoff, and it diverged too — which #2242's framing
        # ("only values past the 200-digit cutoff") did not predict.
        small = Decimal("1E-9")
        _, digits, exponent = small.as_tuple()
        assert abs(exponent) + len(digits) <= 200, "this case must be BELOW the cutoff"
        dj, du = _render_both("{{ p|truncatechars:8 }}", small)
        assert dj == "1E-9" and du == dj, (dj, du)
        dj, du = _render_both("{{ p|make_list|first }}", small)
        assert dj == "1" and du == dj, (dj, du)

        # And the bare render — what THIS fix covers — still agrees, and still
        # expands rather than showing `str()`. #2250 moved the filter boundary,
        # not the render path.
        for value in (big, small, Decimal("1.230E-250")):
            expected, got = _render_both("{{ p }}", value)
            assert got == expected, f"{value}: django={expected!r} djust={got!r}"
        assert _rust.render_template("{{ p }}", {"p": small}) == "0.000000001"
    finally:
        render_env.apply_number_format()
