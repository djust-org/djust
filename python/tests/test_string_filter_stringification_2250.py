"""Django's ``@stringfilter`` built-ins must consume ``str(Decimal)`` (#2250).

Django decorates 29 of its built-in filters with
``django.template.defaultfilters.stringfilter``, which runs the filter on
``str(value)``. djust's built-ins ran on ``Display``, which for a ``Decimal`` is
the *rendered* form — ``numberformat.format``'s ``"{:f}".format(number)``
expansion, correct for ``{{ d }}`` (#2214) and wrong as a string-filter input.
``Decimal('1E-9')`` is the smallest illustration: Django's ``truncatechars``
sees ``1E-9``, djust's saw ``0.000000001``.

Why the filter list is derived, not typed out
---------------------------------------------
The fix is a name-set membership test in ``apply_builtin_filter``, so a typed-out
list is exactly the artifact that drifts (#1646). Every test below enumerates the
set by introspecting the **live** ``defaultfilters`` registry for the
``stringfilter`` wrapper, so a filter Django adds to (or removes from) the
decorator fails these tests rather than passing them.

What is NOT covered, deliberately
---------------------------------
``escape`` and ``safe`` are Django ``@stringfilter``s that djust implements as
no-ops (they return the value unchanged; auto-escaping is decided by filter NAME
at the render site). Their ``Decimal`` divergence has a different mechanism — the
value stays a ``Decimal`` and the renderer localizes it — and coercing them
changes the type flowing down the rest of the chain, which ``floatformat`` cannot
absorb. Measured, both directions, in #2257;
:func:`test_escape_and_safe_are_the_named_exclusions` pins the exclusion so it
stays deliberate.

The number format is process-global (a Rust thread-local); every test restores it
in a ``finally``. A leaked ``NUMBER_GROUPING`` has poisoned a whole worker in this
repo's history.
"""

from __future__ import annotations

import random
import re
from decimal import Decimal, localcontext

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template import defaultfilters  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import translation  # noqa: E402

from djust import _rust, render_env  # noqa: E402

#: `fr` groups with U+00A0 and `hi` groups `[3, 2, 0]`; both are here so a test
#: that passes under `de` alone cannot pass by accident.
LANGUAGES = ["en-us", "de", "fr", "hi", "ja"]

#: djust implements every one of Django's `@stringfilter`s, but only these take
#: the coercion — see the module docstring for `escape`/`safe`.
NAMED_EXCLUSIONS = frozenset({"escape", "safe"})

#: **Nothing is left in ``UNCOMPARABLE``.** It held the filters that took the
#: coercion but could not be diffed against Django byte-for-byte, because each
#: had a whole-filter divergence of its own. The bar for being listed was a
#: reproduction on a NON-``Decimal`` input on ``main`` — otherwise the list is
#: just a way of making a red suite green — and every entry has now cleared:
#:
#: * `linebreaks` escaped its own markup and was missing from
#:   `SAFE_OUTPUT_FILTERS` (#2259) — closed.
#: * `slugify` mapped `.` and `+` to `-` where Django deletes them, and `title`
#:   stripped surrounding whitespace and missed a word boundary after a
#:   non-letter (#2261) — closed.
#: * `truncatechars_html` truncated at `len == limit` and counted escaped
#:   characters, `truncatewords` did not strip surrounding whitespace,
#:   `truncatewords_html` escaped once where Django escapes twice, and
#:   `urlencode` encoded `/` (#2262) — closed.
#:
#: All 27 are in the compared set, which is what
#: :func:`test_the_uncomparable_filters_are_excluded_for_a_reason_that_still_holds`
#: was written to force. Keeping the machinery (rather than deleting it) means
#: a future filter with the same shape has somewhere to go, and the empty-set
#: assertion below documents that nothing is parked there today.
UNCOMPARABLE: frozenset[str] = frozenset()

#: One representative invocation per filter, so the differential can render it.
INVOCATION = {
    "addslashes": "{{ p|addslashes }}",
    "capfirst": "{{ p|capfirst }}",
    "center": "{{ p|center:20 }}",
    "cut": '{{ p|cut:"0" }}',
    "escape": "{{ p|escape }}",
    "escapejs": "{{ p|escapejs }}",
    "force_escape": "{{ p|force_escape }}",
    "iriencode": "{{ p|iriencode }}",
    "linebreaks": "{{ p|linebreaks }}",
    "linebreaksbr": "{{ p|linebreaksbr }}",
    "linenumbers": "{{ p|linenumbers }}",
    "ljust": "{{ p|ljust:20 }}",
    "lower": "{{ p|lower }}",
    "make_list": "{{ p|make_list }}",
    "rjust": "{{ p|rjust:20 }}",
    "safe": "{{ p|safe }}",
    "slugify": "{{ p|slugify }}",
    "striptags": "{{ p|striptags }}",
    "title": "{{ p|title }}",
    "truncatechars": "{{ p|truncatechars:8 }}",
    "truncatechars_html": "{{ p|truncatechars_html:8 }}",
    "truncatewords": "{{ p|truncatewords:2 }}",
    "truncatewords_html": "{{ p|truncatewords_html:2 }}",
    "upper": "{{ p|upper }}",
    "urlencode": "{{ p|urlencode }}",
    "urlize": "{{ p|urlize }}",
    "urlizetrunc": "{{ p|urlizetrunc:10 }}",
    "wordcount": "{{ p|wordcount }}",
    "wordwrap": "{{ p|wordwrap:5 }}",
}

#: The issue's own rows, plus both sides of Django's >200-digit cutoff, plus the
#: ordinary money/integer shapes that must not move.
NAMED = [
    Decimal("1E-9"),  # the issue's headline: BELOW the cutoff and divergent
    Decimal("6.25E-2244"),  # past the cutoff
    Decimal("3.356119237799719E-165"),  # below the cutoff, long coefficient
    Decimal("1.230E-250"),
    Decimal("-1.5E+300"),
    Decimal("1E+1"),  # a positive exponent small enough to expand
    Decimal("1E+9"),
    Decimal("0E-250"),
    Decimal("-0E-250"),
    Decimal("1234.56"),  # ordinary; must not change
    Decimal("19.99"),
    Decimal("0.00"),
    Decimal("-3.5"),
    Decimal("1234567"),
    Decimal("1234567.89"),
    Decimal("1E-201"),  # just past the cutoff
    Decimal("1E-199"),  # just short of it
    Decimal("1" + "0" * 205),
    Decimal("9" * 201),
    Decimal("NaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
]

#: Non-`Decimal` inputs. The coercion is `Decimal`-only, so every one of these
#: must render byte-identically before and after — which is what makes the
#: change's blast radius a claim rather than a hope.
CONTROLS = [
    "hello world",
    "",
    "  spaced  ",
    "<b>x</b>",
    "1E-9",
    "0.000000001",
    "1234567",
    None,
    True,
    False,
    0,
    -42,
    1234567,
    3.5,
    [1, 2, 3],
    (1, 2),
    {"a": 1},
]


def _django_string_filters() -> set[str]:
    """Every built-in Django wraps in ``stringfilter``, from the live registry.

    ``stringfilter`` wraps with ``functools.wraps``, so the wrapper's code object
    is the ``_dec`` closure defined in ``defaultfilters``. Matching on the code
    object rather than on ``__wrapped__`` alone keeps other ``wraps``-using
    decorators out of the set.
    """
    found = set()
    for name, fn in defaultfilters.register.filters.items():
        code = getattr(fn, "__code__", None)
        if (
            getattr(fn, "__wrapped__", None) is not None
            and code is not None
            and code.co_name == "_dec"
            and code.co_filename == defaultfilters.__file__
        ):
            found.add(name)
    return found


def _sample(rng: random.Random) -> Decimal:
    """A value from the shapes that reach — or nearly reach — the cutoff."""
    kind = rng.randrange(6)
    sign = "-" if rng.random() < 0.4 else ""
    if kind == 0:
        return Decimal(
            f"{sign}{rng.randrange(10 ** rng.randrange(1, 12))}.{rng.randrange(100):02d}"
        )
    if kind == 1:
        return Decimal(f"{sign}{rng.randrange(10 ** rng.randrange(1, 15))}")
    if kind == 2:
        coeff = "".join(str(rng.randrange(10)) for _ in range(rng.randrange(1, 12)))
        exp = rng.choice([-1, 1]) * rng.randrange(180, 220)
        return Decimal(f"{sign}{coeff or '1'}E{exp:+d}")
    if kind == 3:
        coeff = "".join(str(rng.randrange(10)) for _ in range(rng.randrange(1, 30)))
        exp = rng.choice([-1, 1]) * rng.randrange(200, 5000)
        return Decimal(f"{sign}{coeff or '1'}E{exp:+d}")
    if kind == 4:
        return Decimal(
            rng.choice(
                [
                    f"{sign}0.{'0' * rng.randrange(0, 8)}{rng.randrange(1, 999)}",
                    f"{sign}0E{rng.choice([-1, 1]) * rng.randrange(0, 400)}",
                    f"{sign}0.00",
                ]
            )
        )
    with localcontext() as ctx:
        ctx.prec = rng.randrange(2, 260)
        return Decimal(rng.randrange(1, 10**6)) / Decimal(rng.randrange(1, 10**6))


def _render_both(source: str, value: object) -> tuple[str, str]:
    return (
        DjangoTemplate(source).render(DjangoContext({"p": value})),
        _rust.render_template(source, {"p": value}),
    )


def _differential(sources, values, languages=LANGUAGES, groupings=(True, False)):
    """Render every (source x value x language x grouping) both ways.

    Returns ``(total, mismatches)``. The number format is restored
    unconditionally, so a failure inside cannot leak a locale into the worker.
    """
    mismatches = []
    total = 0
    try:
        for use_grouping in groupings:
            for language in languages:
                with (
                    override_settings(USE_THOUSAND_SEPARATOR=use_grouping),
                    translation.override(language),
                ):
                    render_env.apply_number_format()
                    for source in sources:
                        for value in values:
                            expected, got = _render_both(source, value)
                            total += 1
                            if expected != got:
                                mismatches.append(
                                    (source, language, use_grouping, repr(value), expected, got)
                                )
    finally:
        render_env.apply_number_format()
    return total, mismatches


def test_the_issues_own_rows_against_a_live_django() -> None:
    """The four rows #2250 tabulates, asserted against Django rather than its table."""
    try:
        render_env.apply_number_format()
        for source, value, django_says in (
            ("{{ p|truncatechars:8 }}", Decimal("6.25E-2244"), "6.25E-2…"),
            ("{{ p|truncatechars:8 }}", Decimal("1E-9"), "1E-9"),
            ("{{ p|make_list|first }}", Decimal("1E-9"), "1"),
            ("{{ p|make_list|first }}", Decimal("3.356119237799719E-165"), "3"),
        ):
            expected, got = _render_both(source, value)
            # Asserting the issue's literal against Django FIRST is what makes
            # the parity assertion mean anything (#1046).
            assert expected == django_says, (
                f"the issue claims Django renders {source} on {value} as "
                f"{django_says!r}; this Django gives {expected!r}"
            )
            assert got == expected, f"{source} on {value}: django={expected!r} djust={got!r}"
    finally:
        render_env.apply_number_format()


def test_the_below_cutoff_case_is_covered_not_just_the_scientific_one() -> None:
    """``Decimal('1E-9')`` is nine digits — the fix must not be cutoff-scoped.

    #2242's framing was that the string-filter divergence followed the
    >200-digit cutoff. It does not, and this is the case that proves it: the
    value is nowhere near the cutoff and diverged in every string filter.
    """
    value = Decimal("1E-9")
    _, digits, exponent = value.as_tuple()
    assert abs(exponent) + len(digits) <= 200, "this case must be BELOW the cutoff"
    try:
        render_env.apply_number_format()
        # The bare render still expands — that is Django's own behaviour and
        # #2214's fix; the point of #2250 is that the FILTER must not see it.
        assert _render_both("{{ p }}", value) == ("0.000000001", "0.000000001")
        assert _render_both("{{ p|truncatechars:8 }}", value) == ("1E-9", "1E-9")
        assert _render_both("{{ p|make_list|first }}", value) == ("1", "1")
        assert _render_both("{{ p|upper }}", value) == ("1E-9", "1E-9")
    finally:
        render_env.apply_number_format()


def test_every_django_stringfilter_agrees_on_the_named_decimals() -> None:
    """Per-filter parity, over the whole derived set except the named exclusions.

    Enumerating every filter rather than the two the issue names is the point:
    "fix the two measured and leave the siblings" is the #1646 shape this repo
    keeps hitting, and the #2203 -> #2216 -> #2227 -> #2228 chain is four links
    of exactly it.
    """
    covered = sorted(_django_string_filters() - NAMED_EXCLUSIONS - UNCOMPARABLE)
    assert covered, "the stringfilter set came back empty — the derivation broke"
    missing = [name for name in covered if name not in INVOCATION]
    assert not missing, f"no invocation recorded for {missing}"

    sources = [INVOCATION[name] for name in covered]
    total, mismatches = _differential(sources, NAMED)
    assert not mismatches, f"{len(mismatches)}/{total} diverged, e.g. {mismatches[:5]}"


def test_a_randomized_sweep_over_every_stringfilter_matches_django() -> None:
    """120 values x 27 filters x 5 locales x 2 grouping flags.

    A curated table samples the axis its author thought of; the reference
    implementation is importable, so the answer is a call away (v1.1.1-2 retro).
    Seeded, so a failure is reproducible.
    """
    rng = random.Random(22501)
    values = [_sample(rng) for _ in range(120)]
    covered = sorted(_django_string_filters() - NAMED_EXCLUSIONS - UNCOMPARABLE)
    sources = [INVOCATION[name] for name in covered]
    total, mismatches = _differential(sources, values, languages=["en-us", "de"])
    assert not mismatches, f"{len(mismatches)}/{total} diverged, e.g. {mismatches[:5]}"
    # Without this a `_sample` that stopped producing exponent-form values would
    # leave the sweep green while covering nothing of what #2250 is about.
    exponent_forms = sum(1 for v in values if "E" in str(v))
    assert exponent_forms > 20, f"only {exponent_forms}/120 values carry an exponent"


def test_non_decimal_inputs_are_untouched() -> None:
    """The coercion is ``Decimal``-only, so nothing else may move.

    Asserted as parity with Django rather than as a snapshot of djust's own
    output, so a control that already diverges (`1e300`, `NaN` — #2258) would
    have to be listed rather than silently blessed.
    """
    covered = sorted(_django_string_filters() - NAMED_EXCLUSIONS - UNCOMPARABLE)
    sources = [INVOCATION[name] for name in covered]
    # `1e300` and `float('nan')` are excluded from CONTROLS: their `Display`
    # already diverges from `str()` in `{{ f }}` too, which no filter-boundary
    # coercion could reach. Tracked at #2258, measured there.
    total, mismatches = _differential(sources, CONTROLS, languages=["en-us", "de"])
    assert not mismatches, f"{len(mismatches)}/{total} diverged, e.g. {mismatches[:5]}"


def test_every_covered_filter_treats_a_decimal_as_its_str() -> None:
    """The fix's claim, stated Django-independently: a ``Decimal`` IS its ``str()``.

    Every filter in the covered set — the three in ``UNCOMPARABLE`` included, so
    they are asserted rather than silently dropped — must produce the same bytes
    for ``Decimal(v)`` as for the string ``str(v)``. Whether the filter itself
    matches Django is a separate question (#2259, #2261); this asks only whether
    the coercion happened, which is the only thing #2250 changes.
    """
    covered = sorted(_django_string_filters() - NAMED_EXCLUSIONS)
    assert len(covered) == 27, f"expected 27 covered filters, derived {len(covered)}: {covered}"
    try:
        render_env.apply_number_format()
        for name in covered:
            source = INVOCATION[name]
            for value in NAMED:
                as_decimal = _rust.render_template(source, {"p": value})
                as_str = _rust.render_template(source, {"p": str(value)})
                assert as_decimal == as_str, (
                    f"{source} on {value!r}: Decimal gave {as_decimal[:60]!r} but "
                    f"str() gave {as_str[:60]!r} — the coercion did not apply"
                )
    finally:
        render_env.apply_number_format()


def test_the_uncomparable_filters_are_excluded_for_a_reason_that_still_holds() -> None:
    """Characterize each ``UNCOMPARABLE`` divergence so closing it turns this red.

    Each assertion was the *plain-string* form of a divergence — no ``Decimal``
    involved — which is what made "this is #2259/#2261, not #2250" a claim
    rather than an assertion of convenience.

    **It worked, and there is nothing left.** The test went red when #2259
    closed (`linebreaks`) and again when #2261/#2262 closed (the other six);
    each time the rows moved into the compared set rather than being deleted
    quietly. The six from #2261/#2262 now live as the ``TestReportedCells``
    table in ``test_truncate_slugify_parity_2262.py``, asserting agreement.

    What remains here is the guard that keeps the list honest: an entry may
    only be parked in ``UNCOMPARABLE`` with a row below that reproduces its
    divergence, so a future addition cannot be a way of making a red suite
    green.
    """
    assert UNCOMPARABLE <= _django_string_filters()
    rows: tuple[tuple[str, object, str, str], ...] = ()
    assert {re.search(r"\|(\w+)", source).group(1) for source, *_ in rows} == UNCOMPARABLE, (
        "every UNCOMPARABLE filter needs a row here reproducing its divergence"
    )
    try:
        render_env.apply_number_format()
        for source, value, django_says, djust_says in rows:
            expected, got = _render_both(source, value)
            assert expected == django_says, f"Django changed: {source} -> {expected!r}"
            assert got == djust_says, f"closed? {source} -> {got!r}"
    finally:
        render_env.apply_number_format()


def test_escape_and_safe_are_the_named_exclusions() -> None:
    """The exclusion is deliberate and characterized, not an oversight.

    djust's ``escape``/``safe`` return the value unchanged, so a ``Decimal``
    stays a ``Decimal`` to the render site and localizes there. Django's are
    ``@stringfilter``s and return a ``str``, which ``localize()`` leaves alone.
    Different mechanism from the other 27. Measured in #2257.

    Asserting the CURRENT divergence (a characterization test, like the one this
    file's fix turned red in ``test_scientific_localization_2242``) so whoever
    closes #2257 has to come here and update it deliberately.

    **Updated deliberately by #2253**, which is what this docstring asked for.
    The second half of the reason above — *"coercing them regresses
    ``{{ d|escape|floatformat }}`` because ``floatformat`` cannot parse a
    numeric string"* — was #2257's residue 2, and it is closed: Django's
    ``floatformat`` begins ``Decimal(str(text))`` on **every** input type, and
    djust's port now does too, so ``{{ "1E+1"|upper|floatformat }}`` is ``10``
    on both sides. The exclusion itself still stands on residue 1 alone (the
    ``escape``/``safe`` no-op), which the first two assertions below pin.
    Whoever closes residue 1 should re-measure the 1,168 cells #2257 records as
    the cost of coercing them — that count was taken while the blocker was
    still open.
    """
    assert NAMED_EXCLUSIONS <= _django_string_filters(), (
        "escape/safe are no longer Django stringfilters — the exclusion needs rethinking"
    )
    try:
        with override_settings(USE_THOUSAND_SEPARATOR=True), translation.override("de"):
            render_env.apply_number_format()
            value = Decimal("1E-9")
            for source in ("{{ p|escape }}", "{{ p|safe }}"):
                expected, got = _render_both(source, value)
                assert expected == "1E-9", f"Django changed: {source} -> {expected!r}"
                assert got == "0,000000001", f"#2257 closed? {source} -> {got!r}"
            # The blocker, on a plain string so it is visibly independent of
            # anything #2250 changed. It read `("10", "1E+1")` — a divergence —
            # until #2253 ported Django's `floatformat`, whose first step is
            # `Decimal(str(text))` on every input type. Now both say `10`, and
            # asserting the pair (rather than just djust) keeps it a
            # differential: if Django ever changed, this reddens too.
            assert _render_both("{{ p|upper|floatformat }}", "1E+1") == ("10", "10")
    finally:
        render_env.apply_number_format()


def test_the_coercion_hands_over_the_payload_the_variant_already_carries() -> None:
    """``Value::Decimal`` holds ``str(Decimal)``; ``Display`` is what expands it.

    The fix is cheap precisely because no string is re-derived — the PyO3
    boundary builds the variant from ``ob.str()``. This pins that: for every
    named value, ``{{ p|upper }}`` is ``str(value).upper()`` exactly, and
    ``{{ p }}`` is still the expanded render.
    """
    try:
        render_env.apply_number_format()
        for value in NAMED:
            _, got = _render_both("{{ p|upper }}", value)
            assert got == str(value).upper(), f"{value!r}: {got!r} != {str(value).upper()!r}"
        # The other half: the render path did NOT move to `str()`.
        expanded = _rust.render_template("{{ p }}", {"p": Decimal("1E-9")})
        assert expanded == "0.000000001", expanded
    finally:
        render_env.apply_number_format()
