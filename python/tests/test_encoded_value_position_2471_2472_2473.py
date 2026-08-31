"""``Value::Encoded`` in the VALUE position: comparison, ``repr`` and ``int()``
(#2471, #2472, #2473).

One value class, three holes, one PR
------------------------------------
``Value::Encoded`` (#2448) carries the four ``datetime`` types across the PyO3
boundary so the type stops being erased. #2458 gave it ``bool(o)``; #2464
defined its falsiness in one place. Three sinks were still reading it as a
string, or not reading it at all, and all three were made *measurable* by the
same corpus row — #2474 (#2469) putting a ``timedelta`` into ``INPUTS``:

* **#2471** — ``values_equal`` and ``try_compare`` had no ``Encoded`` arm, so
  two of them fell to ``_ => false`` / ``None``: a datetime was **not equal to
  itself**, on every operator. Exactly #2335's list bug, one variant later.
* **#2472** — ``pprint`` spelled ``repr(str(o))`` and ``Value::py_repr``
  delegated to ``Display`` (``str(o)``), so ``{{ p|pprint }}``,
  ``{{ p|stringformat:"r" }}`` and a datetime NESTED in a list or dict all
  rendered the ``str`` spelling where Django renders the constructor form.
* **#2473** — ``python_int_value`` had no ``Encoded`` arm, so ``int(datetime)``
  answered ``ValueError`` — the one exception ``get_digit``'s body catches —
  and the filter echoed the datetime onto the page where Django raises
  ``TypeError``. The value-position twin of #2366's argument rule.

Where the issues were wrong, and it matters
--------------------------------------------
#2471's own suggestion was *"an (Encoded, Encoded) arm in both functions"*
keyed on something derived. Running it shows **neither carried string can
answer**:

* ``display`` (``str(o)``) does not order — ``"0:01:30"`` sorts after
  ``"10 days, 0:00:00"`` — and does not answer ``==`` either: two aware
  datetimes naming the same instant in different zones ARE equal in Python
  (:func:`test_aware_datetimes_at_the_same_instant_are_equal`) and have
  different ``str()``.
* ``json`` (``DjangoJSONEncoder.default(o)``) TRUNCATES a datetime's
  microseconds to milliseconds, so two datetimes 1 µs apart encode identically
  — a string compare would call them **equal**, the direction that silently
  shows the wrong branch (:func:`test_a_microsecond_apart_is_not_equal`).

So the fix carries the answer instead of deriving it, exactly as #2458 carries
``bool(o)``: ``Encoded`` grows ``repr`` and a ``CmpKey`` measured from the live
object, and ONE function — ``Encoded::python_partial_cmp`` — is read by
``values_equal``, ``try_compare`` and ``dictsort``'s ordering, so ``==`` and
``<`` cannot drift (#1646).

The equality semantics, stated
-------------------------------
``a == b`` iff both carry a key, the keys are in the SAME comparison domain,
and the ``(hi, lo)`` limbs are equal. A domain is "the set of values Python
will compare this one with": ``timedelta``, ``date``, naive ``datetime``,
aware ``datetime``, naive ``time``. FIVE, not six — an aware ``time`` never
becomes an ``Encoded`` at all (:class:`TestAnAwareTimeIsNotAnEncodedAtAll`).
Everything Django answers falls out of that one rule rather than needing its
own:

===============================  ==============  ==========================
pair                             Python          this rule
===============================  ==============  ==========================
same value, same type            ``==`` True     same domain, same limbs
``date`` vs ``datetime``         ``==`` False,   domains 2 vs 3 → not equal,
                                 ``<`` raises    not ordered
naive vs aware ``datetime``      ``==`` False,   domains 3 vs 4 → same
                                 ``<`` raises
aware, same instant, other zone  ``==`` True     both normalised to UTC
1 µs apart                       ``==`` False    limbs differ
``timedelta`` vs ``int``         ``==`` False    no ``(Encoded, Encoded)``
                                                 match; the wildcard answers
===============================  ==============  ==========================

Django's ``{% if %}`` swallows the ``TypeError`` (``smart_if``'s
``except Exception: return False``), so "Python raises" and "this answers
``None``" produce the same rendered output — which is what every cross-domain
row below asserts against the live engine rather than from this table.

Which path this is on — BOTH, since #2475
------------------------------------------
Everything below renders through the RAW ``render_template`` path, where a live
Python ``datetime`` reaches Rust and becomes a ``Value::Encoded``; that is what
``scripts/filter-parity-differential.py`` does and it is why every helper here
passes the context dict UNNORMALISED.

An earlier version of this docstring said the LiveView path was a separate
question — ``normalize_django_value`` (#2462/#2468) spelled a datetime as its
``DjangoJSONEncoder`` string BEFORE the conversion, so it arrived as a
``Value::String`` and compared as text. **That stopped being true while this
branch was open**: #2475 closed #2467 by removing the Python-side flattening,
so the LiveView path now carries the object to Rust and builds the same
``Value::Encoded``. All three fixes therefore reach both paths, which
:class:`TestBothPathsCarryTheSameEncoded` asserts rather than assumes — and
``test_sequence_op_chokepoint_2451.py``'s pin, which had been rewritten into a
PATH split, is rewritten again as CLOSED.

The lesson is the merge's, not the fix's: a premise about a NEIGHBOURING path
has the same shelf life as a premise about the cited one (#1516).

Sites decided but NOT changed (#1079)
--------------------------------------
Every other sink that matches on ``Value`` and lets an ``Encoded`` reach a
wildcard was enumerated; these were measured as ALREADY AGREEING with Django
and are pinned in :class:`TestTheOtherEncodedSinksWereDecidedNotForgotten` so
"we checked" is asserted rather than claimed: ``python_len`` (``len()`` raises
→ ``|length`` is 0), ``iter_values`` / ``{% for %}`` (not iterable → both
refuse), ``python_getitem`` / ``|first`` (not subscriptable → both refuse),
``python_lower`` / ``|phone2numeric`` (no ``.lower()`` → both refuse),
``filesize_to_int`` (``TypeError`` is caught → ``0 bytes``), ``int_body`` /
``float_body`` / ``char_of`` in ``stringformat`` (reject → empty),
``floatformat`` (``Decimal(str(o))`` fails, ``float(o)`` raises → empty),
``add`` (both branches raise → empty), ``apply_slice`` (returns the input),
``value_to_json`` (already has its own arm).

Four are known divergences left alone, all predating this variant:

* ``{{ p }}`` on a BARE date/datetime/time — Django LOCALIZES (``Jan. 1, 2020,
  3:04 a.m.``) and djust renders ``str(o)``. Declared out of scope in
  ``Value::Encoded``'s own doc, and ``|slice`` and ``|join`` inherit it by
  handing the value back. Pinned in
  :func:`test_the_localization_divergence_is_still_the_known_one`, with a
  ``timedelta`` control that AGREES on the identical templates.
* ``ObjectKey`` flattens an ``Encoded`` DICT KEY to its ``display``, so two
  aware datetimes at the same instant are different keys. Same false-negative
  class as the ``display`` compare; needs an ``ObjectKey`` variant, not an arm.
* ``value_to_arg_string`` — the CUSTOM TAG / ``{% regroup %}`` argument channel
  — hands an ``Encoded`` through as ``str(o)``, because that channel is text
  by construction (#2042/#2203/#2416/#2423) and a scalar goes through
  ``Display`` there. Unchanged by this fix: it was the same string before
  #2448, when the value WAS a ``Value::String(str(o))``.
* ``IntoPyObject`` returns an ``Encoded`` to Python as its ``display`` string
  rather than rebuilding the object — documented on that arm as a deliberate
  #2458-era decision, and a type change rather than a spelling one.
* A timezone-aware ``time`` is not an ``Encoded`` at all, so none of the three
  fixes reaches it — see :class:`TestAnAwareTimeIsNotAnEncodedAtAll`.
"""

from __future__ import annotations

import datetime
import pathlib
import random
import re

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover - import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
        INSTALLED_APPS=[],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE = REPO / "crates" / "djust_core" / "src" / "lib.rs"
RENDERER = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"
FILTERS = REPO / "crates" / "djust_templates" / "src" / "filters.rs"
PPRINT = REPO / "crates" / "djust_templates" / "src" / "pprint.rs"

#: Every operator ``{% if %}`` supports for two operands. All six, because a
#: corpus with only ``==`` cannot tell "no equality arm" from "no ordering
#: arm" — and #2471's measurement was that BOTH were missing.
OPS = ("==", "!=", "<", ">", "<=", ">=")

UTC = datetime.timezone.utc


def both(src: str, ctx: dict) -> tuple[str, str]:
    """``(django, djust)`` for one cell, with a raise recorded as a comparable
    outcome rather than an abort.

    ``ctx`` reaches ``render_template`` UNNORMALISED, which is what makes the
    value a ``Value::Encoded`` — see the module docstring's "which path".
    """
    try:
        d = DjangoTemplate(src).render(DjangoContext(dict(ctx)))
    except BaseException as exc:  # noqa: BLE001 - a raise is an outcome
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        u = _rust.render_template(src, dict(ctx))
    except BaseException as exc:  # noqa: BLE001
        u = f"<<EXC {type(exc).__name__}>>"
    return d, u


def django_raises(src: str, ctx: dict) -> bool:
    try:
        DjangoTemplate(src).render(DjangoContext(dict(ctx)))
    except Exception:  # noqa: BLE001
        return True
    return False


def djust_raises(src: str, ctx: dict) -> bool:
    try:
        _rust.render_template(src, dict(ctx))
    except Exception:  # noqa: BLE001
        return True
    return False


def branch(op: str, a: object, b: object) -> tuple[str, str]:
    """``{% if p <op> q %}`` on both engines, rendered as ``Y``/``N``.

    ``Y``/``N`` rather than the operands, so the assertion is about which
    BRANCH was taken — the thing #2471 is about, and the thing an operand's
    own rendering (which localizes, see the module docstring) would confuse.
    """
    return both("{%% if p %s q %%}Y{%% else %%}N{%% endif %%}" % op, {"p": a, "q": b})


# ---------------------------------------------------------------------------
# The randomized corpus.
#
# A curated table samples one axis and blinds you on the next (v1.1.1-2 canon),
# and Django is importable here — so the reference answer is a call away rather
# than a transcription. Every generator below produces a value whose Python
# semantics differ from its neighbours in a way one of the carried strings gets
# WRONG, which is what makes the sweep able to fail:
#
#   * microseconds beyond a millisecond   -> `json` truncates them away
#   * a non-UTC offset                    -> `display` keeps the local clock
#   * a day count past one digit          -> `str(timedelta)` stops sorting
#   * a negative timedelta                -> `str` normalises to `-1 day, ...`
# ---------------------------------------------------------------------------


def _rand_timedelta(rng: random.Random) -> datetime.timedelta:
    return datetime.timedelta(
        days=rng.randint(-15, 15),
        seconds=rng.randint(0, 86399),
        microseconds=rng.choice([0, 1, 500, 999_999, rng.randint(0, 999_999)]),
    )


def _rand_tz(rng: random.Random) -> datetime.timezone:
    return datetime.timezone(
        datetime.timedelta(minutes=rng.choice([-750, -300, -30, 0, 45, 330, 840]))
    )


def _rand_datetime(rng: random.Random, *, aware: bool) -> datetime.datetime:
    base = datetime.datetime(
        rng.randint(1, 9999),
        rng.randint(1, 12),
        rng.randint(1, 28),
        rng.randint(0, 23),
        rng.randint(0, 59),
        rng.randint(0, 59),
        rng.choice([0, 1, 999_999, rng.randint(0, 999_999)]),
    )
    return base.replace(tzinfo=_rand_tz(rng)) if aware else base


def _rand_date(rng: random.Random) -> datetime.date:
    return datetime.date(rng.randint(1, 9999), rng.randint(1, 12), rng.randint(1, 28))


def _rand_time(rng: random.Random, *, aware: bool) -> datetime.time:
    t = datetime.time(
        rng.randint(0, 23),
        rng.randint(0, 59),
        rng.randint(0, 59),
        rng.choice([0, 1, 999_999, rng.randint(0, 999_999)]),
    )
    return t.replace(tzinfo=_rand_tz(rng)) if aware else t


#: One generator per COMPARISON DOMAIN, so the sweep builds both same-domain
#: and cross-domain pairs. Naming them here is what makes "every domain is
#: reachable from the corpus" checkable rather than hoped for — and it is why
#: there are five and not six: an AWARE ``time`` is not an ``Encoded`` at all
#: (:class:`TestAnAwareTimeIsNotAnEncodedAtAll`), so a sixth domain for it
#: would be an unreachable arm (#1859).
GENERATORS = {
    "timedelta": lambda rng: _rand_timedelta(rng),
    "date": lambda rng: _rand_date(rng),
    "datetime-naive": lambda rng: _rand_datetime(rng, aware=False),
    "datetime-aware": lambda rng: _rand_datetime(rng, aware=True),
    "time-naive": lambda rng: _rand_time(rng, aware=False),
}


def _corpus(seed: int, per_domain: int) -> list[tuple[str, object]]:
    rng = random.Random(seed)
    out: list[tuple[str, object]] = []
    for name, make in GENERATORS.items():
        for _ in range(per_domain):
            out.append((name, make(rng)))
    return out


class TestComparisonAgreesWithDjango2471:
    """The headline: ``{% if a == b %}`` on a datetime against itself."""

    @pytest.mark.parametrize("op", OPS)
    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_a_value_against_itself_answers_as_django_does(self, domain: str, op: str) -> None:
        """The exact cell #2471 opens with, for every domain and every
        operator — including ``<`` and ``>``, which agreed BEFORE the fix
        (both False) and so cannot carry the assertion alone."""
        rng = random.Random(20471)
        for _ in range(12):
            v = GENERATORS[domain](rng)
            dj, du = branch(op, v, v)
            assert du == dj, f"{domain} {op} itself ({v!r}): django={dj!r} djust={du!r}"

    @pytest.mark.parametrize("op", OPS)
    def test_a_randomised_sweep_of_every_pair_agrees(self, op: str) -> None:
        """Every ordered pair of a 6-domain corpus, so same-domain ordering and
        every cross-domain refusal are swept together. Cross-domain is the half
        that was ALREADY right, and a fix that widened equality to "both are
        Encoded" would break it — which is why the sweep is not filtered to
        like-for-like."""
        corpus = _corpus(seed=2471, per_domain=4)
        bad = []
        for _, a in corpus:
            for _, b in corpus:
                dj, du = branch(op, a, b)
                if du != dj:
                    bad.append((a, b, dj, du))
        assert not bad, f"{len(bad)}/{len(corpus) ** 2} cells diverge on {op}: {bad[:5]}"

    @pytest.mark.parametrize("op", OPS)
    def test_a_randomised_sweep_of_NEAR_aware_pairs_agrees(self, op: str) -> None:
        """The sweep above cannot see an offset error, and finding that out is
        the reason this test exists.

        ``GENERATORS["datetime-aware"]`` draws a random YEAR, so any two of its
        values are years apart and a ``utcoffset()`` bounded to ±24h can never
        flip their order. Gating the UTC normalisation off reddened exactly ONE
        test — the hand-built same-instant pair — and the 400-cell randomised
        sweep stayed green over a genuinely wrong engine.

        So this one draws pairs whose wall clocks are within ±36h, which is the
        window where the offset decides. Same lesson as v1.1.1-2's: a
        randomised corpus is only as good as the axis its generator varies.
        """
        rng = random.Random(24710)
        bad = []
        for _ in range(300):
            base = datetime.datetime(
                2020, rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), rng.randint(0, 59)
            )
            a = base.replace(tzinfo=_rand_tz(rng))
            b = (base + datetime.timedelta(minutes=rng.randint(-2160, 2160))).replace(
                tzinfo=_rand_tz(rng)
            )
            dj, du = branch(op, a, b)
            if du != dj:
                bad.append((a, b, dj, du))
        assert not bad, f"{len(bad)}/300 near-instant aware cells diverge on {op}: {bad[:5]}"

    def test_a_microsecond_apart_is_not_equal(self) -> None:
        """The direction ``json`` would get wrong. ``DjangoJSONEncoder`` writes
        ``r[:23] + r[26:]`` for a datetime, truncating microseconds to
        milliseconds — so these two encode IDENTICALLY and a compare on that
        string would answer equal. Python says not equal."""
        a = datetime.datetime(2020, 1, 1, 3, 4, 5, 1)
        b = datetime.datetime(2020, 1, 1, 3, 4, 5, 2)
        assert a != b
        assert branch("==", a, b) == ("N", "N")
        assert branch("!=", a, b) == ("Y", "Y")
        assert branch("<", a, b) == ("Y", "Y")

    def test_aware_datetimes_at_the_same_instant_are_equal(self) -> None:
        """The direction ``display`` would get wrong. Two spellings of one
        instant: Python compares aware datetimes by their UTC value, so these
        are equal while their ``str()`` differ in every character after the
        date."""
        a = datetime.datetime(2020, 1, 1, 0, 0, tzinfo=UTC)
        b = datetime.datetime(
            2019, 12, 31, 19, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
        )
        assert a == b
        assert str(a) != str(b)
        assert branch("==", a, b) == ("Y", "Y")
        assert branch("<=", a, b) == ("Y", "Y")
        assert branch(">=", a, b) == ("Y", "Y")
        assert branch("!=", a, b) == ("N", "N")

    def test_ten_days_is_not_less_than_two_days(self) -> None:
        """The ordering direction ``display`` gets wrong: ``str(timedelta)``
        writes the day count UNPADDED, so ``"10 days, 0:00:00"`` sorts before
        ``"2 days, 0:00:00"`` lexicographically while ten days is the larger
        duration. ``json``'s ``duration_iso_string`` has the identical hole
        (``P10DT…`` vs ``P2DT…``), so neither carried string can order this
        pair — which is the argument for the key."""
        ten, two = datetime.timedelta(days=10), datetime.timedelta(days=2)
        assert str(ten) < str(two)  # the trap, spelled out
        assert branch("<", ten, two) == ("N", "N")
        assert branch(">", ten, two) == ("Y", "Y")

    def test_a_sub_second_timedelta_sorts_under_a_whole_one(self) -> None:
        """``duration_iso_string`` appends ``.ffffff`` only when non-zero, so
        ``P0DT00H00M01.500000S`` sorts BEFORE ``P0DT00H00M01S`` (``.`` is
        0x2E, ``S`` is 0x53) while 1.5s is the larger duration."""
        one_five = datetime.timedelta(seconds=1, microseconds=500_000)
        one = datetime.timedelta(seconds=1)
        assert branch(">", one_five, one) == ("Y", "Y")
        assert branch("<", one_five, one) == ("N", "N")

    def test_a_negative_timedelta_orders_below_zero(self) -> None:
        """Python normalises a negative delta to ``days=-1, seconds=86399``,
        which is what makes ``(days, micros-in-day)`` order correctly as a
        pair — a total-microseconds ``i64`` would overflow at
        ``timedelta.max``, and a compare on ``str`` sees ``"-1 day, …"``."""
        neg = datetime.timedelta(seconds=-1)
        zero = datetime.timedelta(0)
        assert branch("<", neg, zero) == ("Y", "Y")
        assert branch(">", neg, zero) == ("N", "N")
        assert branch("==", neg, datetime.timedelta(seconds=-1)) == ("Y", "Y")

    def test_the_widest_timedeltas_order(self) -> None:
        """``timedelta.max`` is ~8.64e19 microseconds and ``i64::MAX`` is
        ~9.22e18, so a single-limb microsecond key would wrap here. Both
        extremes, so a mutation to either limb is reachable."""
        assert branch("<", datetime.timedelta.min, datetime.timedelta.max) == ("Y", "Y")
        assert branch("==", datetime.timedelta.max, datetime.timedelta.max) == ("Y", "Y")
        assert branch("==", datetime.timedelta.min, datetime.timedelta.max) == ("N", "N")

    @pytest.mark.parametrize("op", OPS)
    def test_a_date_never_compares_to_a_datetime(self, op: str) -> None:
        """``datetime`` IS a ``date`` subclass and CPython still refuses:
        ``date(2020,1,1) == datetime(2020,1,1)`` is False and ``<`` raises.
        A "both are Encoded, compare the strings" rule answers True for the
        first — #2471's issue says so in as many words."""
        d = datetime.date(2020, 1, 1)
        dt = datetime.datetime(2020, 1, 1, 0, 0)
        dj, du = branch(op, d, dt)
        assert du == dj
        assert du == ("Y" if op == "!=" else "N")

    @pytest.mark.parametrize("op", OPS)
    def test_naive_never_compares_to_aware(self, op: str) -> None:
        naive = datetime.datetime(2020, 1, 1, 3, 4, 5)
        aware = datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=UTC)
        dj, du = branch(op, naive, aware)
        assert du == dj
        assert du == ("Y" if op == "!=" else "N")

    @pytest.mark.parametrize("op", OPS)
    @pytest.mark.parametrize(
        "other", [0, 1, "0:00:00", "2020-01-01", None, [datetime.timedelta(0)], 0.0, True]
    )
    def test_an_encoded_against_a_non_encoded_is_unchanged(self, op: str, other: object) -> None:
        """The arm must not widen. Every one of these already agreed with
        Django through the wildcard before the fix, including the string that
        IS the datetime's own ``display`` — ``timedelta(0) == "0:00:00"`` is
        False in Python."""
        td = datetime.timedelta(0)
        dj, du = branch(op, td, other)
        assert du == dj, f"timedelta(0) {op} {other!r}: django={dj!r} djust={du!r}"

    def test_dictsort_orders_a_datetime_column(self) -> None:
        """``compare_sort_values`` reads the same function, so a ``dictsort``
        over a ``DateTimeField`` column sorts. It answered all-Equal — i.e.
        did not sort at all — for the same missing-arm reason."""
        rows = [{"k": datetime.datetime(2020, 3, 1)}, {"k": datetime.datetime(2020, 1, 1)}]
        dj, du = both(
            "{% for r in p|dictsort:'k' %}[{{ r.k|date:'Y-m-d' }}]{% endfor %}", {"p": rows}
        )
        assert du == dj
        assert du == "[2020-01-01][2020-03-01]"

    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_dictsort_orders_every_domain_as_python_sorts_it(self, domain: str) -> None:
        """One column per domain, checked against Python's own ``sorted()`` —
        so ``compare_sort_values``' arm is exercised for every key shape rather
        than for the one that happened to be written first (#1104)."""
        rng = random.Random(24711)
        src = "{% for r in p|dictsort:'k' %}[{{ r.i }}]{% endfor %}"
        for _ in range(8):
            rows = [{"k": GENERATORS[domain](rng), "i": i} for i in range(5)]
            order = [r["i"] for r in sorted(rows, key=lambda r: r["k"])]
            dj, du = both(src, {"p": rows})
            assert du == dj
            assert du == "".join(f"[{i}]" for i in order), f"{domain}: {rows!r}"


class TestReprAgreesWithDjango2472:
    """``repr(o)``, at every sink that spells one."""

    #: Every template position whose Django answer is ``repr`` of the value.
    #: ``{{ p }}`` on a BARE datetime is deliberately absent — that one is the
    #: localization divergence, pinned separately below.
    REPR_TEMPLATES = (
        "{{ p|pprint }}",
        '{{ p|stringformat:"r" }}',
        '{{ p|stringformat:"a" }}',
    )

    @pytest.mark.parametrize("src", REPR_TEMPLATES)
    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_a_randomised_sweep_of_reprs_agrees(self, src: str, domain: str) -> None:
        """Randomised rather than tabulated, because Python's ``repr`` for this
        family is NOT a format string: the keyword ``timedelta`` chooses
        (``days=`` / ``seconds=`` / ``microseconds=``, in combination) depends
        on the VALUE, and ``datetime`` prints its zero time fields but not its
        zero microsecond. A curated table samples the shapes you thought of."""
        rng = random.Random(20472)
        for _ in range(60):
            v = GENERATORS[domain](rng)
            dj, du = both(src, {"p": v})
            assert du == dj, f"{src} on {v!r}: django={dj!r} djust={du!r}"

    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_a_nested_value_renders_its_repr(self, domain: str) -> None:
        """The position that matters more, and the one #2472's cited site
        (``Value::py_repr``) actually governs: a container's ``str`` calls
        ``repr`` on each element, so this is the ONLY spelling a nested
        datetime has. ``{{ p }}`` over ``[timedelta(0)]`` rendered
        ``[0:00:00]`` where Django renders ``[datetime.timedelta(0)]`` —
        the ordinary render path, not a filter."""
        rng = random.Random(20473)
        for _ in range(30):
            v = GENERATORS[domain](rng)
            for wrap in ([v], (v,), {"k": v}):
                dj, du = both("{{ p }}", {"p": wrap})
                assert du == dj, f"{{{{ p }}}} on {wrap!r}: django={dj!r} djust={du!r}"

    def test_pprint_over_a_container_agrees(self) -> None:
        v = datetime.timedelta(0)
        for wrap in ([v], (v,), {"k": v}, [[v]]):
            dj, du = both("{{ p|pprint }}", {"p": wrap})
            assert du == dj, f"pprint {wrap!r}: django={dj!r} djust={du!r}"

    def test_the_loop_a_repr_drives_walks_the_same_characters(self) -> None:
        """#2472's own downstream case: ``{% for x in p|pprint %}`` walks the
        21 characters of ``datetime.timedelta(0)`` on Django and walked the 9
        of ``'0:00:00'`` here."""
        dj, du = both(
            "{% for x in p|pprint %}[{{ x }}]{% endfor %}",
            {"p": datetime.timedelta(0)},
        )
        assert du == dj
        assert du.count("[") == len("datetime.timedelta(0)")


#: The value position's copy of #2366's argument table — the same four types,
#: because ``int(value)`` and ``int(arg)`` ask CPython the same question and
#: get the same ``TypeError``. #2473's scope note asks for exactly this list.
TYPED_REFUSALS = {
    "datetime": datetime.datetime(2020, 1, 1, 3, 4, 5),
    "date": datetime.date(2020, 1, 1),
    "time": datetime.time(3, 4, 5),
    "timedelta": datetime.timedelta(seconds=90),
    "timedelta-zero": datetime.timedelta(0),
    "datetime-aware": datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=UTC),
}

#: Every filter whose Django body calls ``int(value)`` and does NOT catch a
#: ``TypeError``. All of them, not ``get_digit`` alone: they share ONE
#: ``python_int_value`` chokepoint, so a per-filter arm would be three copies
#: of one rule and this table is what proves the chokepoint is the one that
#: moved (#1646).
INT_VALUE_FILTERS = {
    "get_digit": '{{ p|get_digit:"1" }}',
    "divisibleby": '{{ p|divisibleby:"2" }}',
}


class TestIntValueRefusesTheFamily2473:
    @pytest.mark.parametrize("name", sorted(TYPED_REFUSALS))
    @pytest.mark.parametrize("filt", sorted(INT_VALUE_FILTERS))
    def test_both_engines_refuse(self, filt: str, name: str) -> None:
        src = INT_VALUE_FILTERS[filt]
        ctx = {"p": TYPED_REFUSALS[name]}
        assert django_raises(src, ctx), f"Django no longer raises: {src} with a {name}"
        assert djust_raises(src, ctx), f"{src} with a {name} value did not raise"

    @pytest.mark.parametrize("name", sorted(TYPED_REFUSALS))
    def test_the_refusal_names_TypeError_not_ValueError(self, name: str) -> None:
        """Which exception is the whole of the bug: ``ValueError`` is the ONE
        ``get_digit``'s ``except`` catches, so answering it made the filter
        take the return-the-input branch and echo the datetime onto the page.
        ``divisibleby`` refused either way but named the wrong exception."""
        with pytest.raises(Exception) as exc:
            _rust.render_template('{{ p|divisibleby:"2" }}', {"p": TYPED_REFUSALS[name]})
        assert "TypeError" in str(exc.value), str(exc.value)
        assert "ValueError" not in str(exc.value), str(exc.value)

    @pytest.mark.parametrize("name", sorted(TYPED_REFUSALS))
    def test_the_datetime_is_no_longer_echoed_onto_the_page(self, name: str) -> None:
        """The echo-on-failure class (#2359) stated as an output claim rather
        than as an exception type: ``get_digit``'s echo arm carries a per-call
        SAFETY GRANT (#2403), so the value reached the page live."""
        value = TYPED_REFUSALS[name]
        try:
            out = _rust.render_template('{{ p|get_digit:"1" }}', {"p": value})
        except Exception:  # noqa: BLE001 - the refusal, which is the fix
            return
        pytest.fail(f"get_digit rendered {out!r} for a {name} instead of raising")

    def test_a_tag_operand_refuses_as_django_refuses_at_compile_time(self) -> None:
        """#2473's louder case. Django rejects the whole template
        (``TemplateSyntaxError: widthratio final argument must be a number``)
        where djust rendered empty."""
        src = '{% widthratio p|get_digit:"1" 10 100 %}'
        ctx = {"p": datetime.timedelta(seconds=90)}
        assert django_raises(src, ctx)
        assert djust_raises(src, ctx)

    @pytest.mark.parametrize("value", [0, 42, "7", 7.9, True])
    def test_a_value_int_ACCEPTS_is_untouched(self, value: object) -> None:
        """The arm must not widen: everything ``int()`` really does accept
        still answers a digit."""
        dj, du = both('{{ p|get_digit:"1" }}', {"p": value})
        assert du == dj


class TestTheOtherEncodedSinksWereDecidedNotForgotten:
    """The enumeration, asserted.

    Every sink where a ``Value::Encoded`` reaches a wildcard was measured; the
    ones below already agreed with Django and were deliberately left alone.
    Pinning them is what separates "decided" from "not looked at" — and it is
    the guard that a future ``Encoded`` arm added to one of these does not
    silently move an answer that was correct.
    """

    AGREEING = {
        "length": "{{ p|length }}",
        "for": "{% for x in p %}x{% endfor %}",
        "first": "{{ p|first }}",
        "last": "{{ p|last }}",
        "phone2numeric": "{{ p|phone2numeric }}",
        "filesizeformat": "{{ p|filesizeformat }}",
        "floatformat": "{{ p|floatformat }}",
        "add": '{{ p|add:"1" }}',
        "stringformat-d": '{{ p|stringformat:"d" }}',
        "stringformat-f": '{{ p|stringformat:"f" }}',
        "stringformat-s": '{{ p|stringformat:"s" }}',
        "stringformat-c": '{{ p|stringformat:"c" }}',
        "json_script": "{{ p|json_script:'i' }}",
        "make_list": "{{ p|make_list }}",
        "linebreaks": "{{ p|linebreaks }}",
        "widthratio": "{% widthratio p 10 100 %}",
        "dictsort-nonseq": "{{ p|dictsort:'k' }}",
        "random": "{{ p|random }}",
        "unordered_list": "{{ p|unordered_list }}",
        "in-operator": "{% if 1 in p %}Y{% else %}N{% endif %}",
        "is-operator": "{% if p is None %}Y{% else %}N{% endif %}",
    }

    #: Templates whose Django give-up path hands the VALUE OBJECT back to the
    #: renderer, so the cell measures Django's LOCALIZED spelling against
    #: djust's `str(o)` rather than anything about this fix. Excluded from
    #: AGREEING and pinned below instead, so removing the exclusion cannot pass
    #: silently.
    ECHOES_THE_VALUE = {
        "bare": "{{ p }}",
        "slice": '{{ p|slice:":2" }}',
        "join": '{{ p|join:"," }}',
    }

    @pytest.mark.parametrize("name", sorted(AGREEING))
    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_the_sink_still_agrees_with_django(self, domain: str, name: str) -> None:
        rng = random.Random(20474)
        src = self.AGREEING[name]
        for _ in range(6):
            v = GENERATORS[domain](rng)
            dj, du = both(src, {"p": v})
            # A raise on both engines is agreement about the OUTCOME; the
            # message text is djust's own envelope and is not Django's, which
            # is why `both` records the exception TYPE marker rather than str.
            if dj.startswith("<<EXC") and du.startswith("<<EXC"):
                continue
            assert du == dj, f"{src} on {v!r}: django={dj!r} djust={du!r}"

    @pytest.mark.parametrize("name", sorted(ECHOES_THE_VALUE))
    @pytest.mark.parametrize(
        "value",
        [
            datetime.datetime(2020, 1, 1, 3, 4, 5),
            datetime.date(2020, 1, 1),
            datetime.time(3, 4, 5),
        ],
        ids=["datetime", "date", "time"],
    )
    def test_the_localization_divergence_is_still_the_known_one(
        self, value: object, name: str
    ) -> None:
        """NOT fixed here, and pinned so it cannot be mistaken for part of this
        class — nor silently "fixed" into it.

        Django LOCALIZES a bare date/datetime/time (``Jan. 1, 2020, 3:04
        a.m.``) where djust renders ``str(o)``. ``Value::Encoded``'s own doc
        declares that out of scope: it is a RENDERING change, and moving it on
        a value fix would be a second unrelated behaviour change. Every
        template here reaches it the same way — Django's give-up path hands the
        object back and the renderer localizes it.
        """
        src = self.ECHOES_THE_VALUE[name]
        dj, du = both(src, {"p": value})
        assert du != dj, f"{src} on {value!r} now AGREES — this pin needs revisiting"
        # djust's side is the value's own `str()` in every one of them, which
        # is what makes this ONE divergence reached three ways rather than
        # three separate ones.
        assert du == str(value), f"{src}: djust said {du!r}, not str(value)"

    @pytest.mark.parametrize("name", sorted(ECHOES_THE_VALUE))
    def test_a_timedelta_is_not_localized_so_the_same_templates_agree(self, name: str) -> None:
        """The control for the pin above: a ``timedelta`` has no localized
        spelling, so the identical templates AGREE for it — which is what shows
        the divergence is Django's localization and not djust reading the value
        wrongly."""
        src = self.ECHOES_THE_VALUE[name]
        dj, du = both(src, {"p": datetime.timedelta(seconds=90)})
        assert du == dj


class TestAnAwareTimeIsNotAnEncodedAtAll:
    """The one member of the family that never reaches this variant.

    ``DjangoJSONEncoder.default`` RAISES for a timezone-aware ``time``
    (``ValueError: JSON can't represent timezone-aware times.``), so
    ``django_json_encoded`` fails closed and the value stays the
    ``Value::String(str(o))`` it was before #2448. That is the refusal
    direction #2429 declined; this fix does not change it.

    Pinned because it is the reason ``comparison_key`` has FIVE domains and not
    six, and the reason ``GENERATORS`` above has five entries — an
    aware-``time`` domain would be an arm no test could reach (#1859).
    """

    AWARE_TIME = datetime.time(3, 4, 5, tzinfo=UTC)

    def test_django_itself_refuses_to_encode_it(self) -> None:
        """The premise, run rather than quoted."""
        from django.core.serializers.json import DjangoJSONEncoder

        with pytest.raises(ValueError, match="timezone-aware times"):
            DjangoJSONEncoder().default(self.AWARE_TIME)

    def test_it_still_renders_and_compares_as_a_string(self) -> None:
        """Consequences, stated as measurements: it renders its ``str``
        (which for a `time` Django localizes, so `{{ p }}` diverges as above),
        it is EQUAL to itself (two equal strings), and ``|pprint`` gives the
        quoted display rather than the constructor form."""
        v = self.AWARE_TIME
        assert branch("==", v, v) == ("Y", "Y")
        assert _rust.render_template("{{ p }}", {"p": v}) == str(v)
        # `|pprint` escapes its output, so the quotes arrive as entities; the
        # content is the DISPLAY string's repr, which is what a `Value::String`
        # gives and what an `Encoded` would NOT.
        assert _rust.render_template("{{ p|pprint }}", {"p": v}) == "&#x27;03:04:05+00:00&#x27;"
        # Django spells the constructor form, so `|pprint` is a divergence —
        # NOT this fix's, and it survives it.
        assert "datetime.time(" in DjangoTemplate("{{ p|pprint }}").render(DjangoContext({"p": v}))

    def test_an_aware_time_is_not_equal_to_a_naive_one_on_either_engine(self) -> None:
        """The one answer that still has to be right through the string path:
        the two spellings differ, so the string compare says not-equal, which
        is what Python says."""
        assert branch("==", self.AWARE_TIME, datetime.time(3, 4, 5)) == ("N", "N")


class TestOneComparisonChokepoint:
    """The structural half: ``python_partial_cmp`` is read by an EXACT set of
    sites (#1646/#1125).

    Set equality, not a floor — a floor cannot see a REMOVED arm, which is the
    regression this pin exists to catch: #2244, #2243 and #2335 were each a
    ``values_equal`` arm and a ``try_compare`` arm drifting apart, and deleting
    one of the two is exactly how that happens again.
    """

    #: file -> number of CALLS (the definition is excluded). Deriving the
    #: expected set from the source would make the test compare the source to
    #: itself; these are written down.
    EXPECTED_CALLERS = {
        "crates/djust_templates/src/renderer.rs": 2,  # values_equal, try_compare
        "crates/djust_templates/src/filters.rs": 1,  # compare_sort_values
    }

    def _calls(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for path in sorted(REPO.glob("crates/*/src/**/*.rs")):
            src = path.read_text(encoding="utf-8")
            # Calls only: `x.python_partial_cmp(` — the definition is
            # `pub fn python_partial_cmp(`, which has no receiver dot.
            n = len(re.findall(r"\.python_partial_cmp\(", src))
            if n:
                found[str(path.relative_to(REPO))] = n
        return found

    def test_the_caller_set_is_exactly_the_three_comparison_sinks(self) -> None:
        assert self._calls() == self.EXPECTED_CALLERS, (
            "the set of `python_partial_cmp` callers changed. A NEW caller must be "
            "added to EXPECTED_CALLERS with a test; a REMOVED one means a comparison "
            "sink went back to answering an Encoded from a wildcard (#2471)."
        )

    def test_the_definition_is_the_only_one(self) -> None:
        """The other half of the same pin: one definition, so a second copy of
        the rule cannot appear beside it."""
        defs = [
            str(p.relative_to(REPO))
            for p in sorted(REPO.glob("crates/*/src/**/*.rs"))
            if "fn python_partial_cmp(" in p.read_text(encoding="utf-8")
        ]
        assert defs == ["crates/djust_core/src/lib.rs"], defs

    def test_neither_comparison_function_reads_display_or_json_for_an_encoded(self) -> None:
        """The rule the fix is ABOUT, pinned against its most likely wrong
        rewrite. Both #2471's issue and the obvious first draft reach for
        ``e.display``; the module docstring's two measurements say why that is
        wrong in both directions."""
        src = RENDERER.read_text(encoding="utf-8")
        for fn in ("fn values_equal(", "fn try_compare("):
            start = src.index(fn)
            body = src[start : src.index("\n}\n", start)]
            arm = [ln for ln in body.splitlines() if "Value::Encoded" in ln and "//" not in ln]
            assert arm, f"{fn} lost its Encoded arm (#2471)"
            for ln in arm:
                assert "display" not in ln and ".json" not in ln, (
                    f"{fn}'s Encoded arm reads a carried STRING: {ln.strip()}"
                )

    def test_the_encoded_struct_carries_both_new_fields(self) -> None:
        """The msgpack round trip drops whatever the struct does not carry, so
        a field that stops existing reopens the bug after one cache hit — the
        reopening ``ENCODED_TAG`` already documents twice."""
        src = CORE.read_text(encoding="utf-8")
        struct = src[src.index("pub struct Encoded {") : src.index("pub struct CmpKey {")]
        for field in ("pub repr: String", "pub cmp_key: Option<CmpKey>"):
            assert field in struct, f"`Encoded` lost `{field}` (#2471/#2472)"
        # And the tag payload carries them, which is the half a struct-only
        # check cannot see.
        assert "e.repr.as_str()" in src and "e.cmp_key.map(" in src, (
            "the ENCODED_TAG msgpack payload stopped carrying `repr`/`cmp_key`"
        )

    def test_pprint_and_py_repr_read_the_carried_repr(self) -> None:
        assert "Value::Encoded(e) => e.repr.clone()" in PPRINT.read_text(encoding="utf-8")
        assert "Value::Encoded(e) => e.repr.clone()" in CORE.read_text(encoding="utf-8")

    def test_python_int_value_names_the_family_a_TypeError(self) -> None:
        src = FILTERS.read_text(encoding="utf-8")
        start = src.index("pub(crate) fn python_int_value(")
        body = src[start : src.index("\n}\n", start)]
        assert "Value::Encoded(_) => Err(IntValueError::Type)" in body, (
            "`python_int_value` lost its Encoded arm — an Encoded falls back to "
            "the wildcard's ValueError, the one exception Django catches (#2473)"
        )


class TestTheStateRoundTripKeepsTheAnswers:
    """A value that has been through the state backend must still compare and
    still spell its ``repr`` — #2448 and #2458 were each reopened by exactly
    this trip, which is why ``ENCODED_TAG`` exists at all."""

    EQ = "{% if p == q %}Y{% else %}N{% endif %}"
    REPR = "{{ p|pprint }}"

    @staticmethod
    def _round_trip(source: str, value: object) -> str:
        from djust._rust import RustLiveView

        view = RustLiveView(source)
        view.set_state("p", value)
        view.set_state("q", value)
        return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()

    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_a_round_trip_preserves_equality(self, domain: str) -> None:
        rng = random.Random(2475)
        for _ in range(6):
            v = GENERATORS[domain](rng)
            assert self._round_trip(self.EQ, v) == "Y", f"{v!r} stopped equalling itself"

    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_a_round_trip_preserves_the_repr(self, domain: str) -> None:
        rng = random.Random(2476)
        for _ in range(6):
            v = GENERATORS[domain](rng)
            assert self._round_trip(self.REPR, v) == repr(v)

    def test_the_payload_is_what_carries_them(self) -> None:
        """Non-vacuity for the tag, the #2458 shape: without reading the
        payload, the two round trips above would also pass on an
        implementation that kept the value alive some other way, and the two
        new elements could be dropped with the suite green (#2135)."""
        msgpack = pytest.importorskip("msgpack")

        from djust._rust import RustLiveView

        view = RustLiveView("{{ p }}")
        view.set_state("p", datetime.timedelta(0))
        blob = view.serialize_msgpack()
        payload = msgpack.unpackb(blob, raw=False, strict_map_key=False)[1]["p"][
            "__djust_encoded__"
        ]
        assert payload == [
            "datetime.timedelta",
            "0:00:00",
            "P0DT00H00M00S",
            False,
            # #2466's two bits, which sit BEFORE these two because they were
            # appended first — a positional payload only stays readable if
            # every widening appends at the end (#1541).
            False,
            False,
            "datetime.timedelta(0)",
            [1, 0, 0],
            # #2481's attribute map, appended last for the reason every
            # widening before it was: a positional payload only stays readable
            # if nothing moves (#1541). `{{ p.days }}` resolves off this.
            #
            # It gained `total_seconds` in #2485: the map holds Django's whole
            # lookup step 2 — the DATA attributes and the AUTO-CALLED ones —
            # written by two producers into ONE map, in table order. The SLOT
            # is unchanged, which is the compatibility statement; only the map
            # inside it grew, and a reader that does not know the name simply
            # does not ask for it.
            {"days": 0, "seconds": 0, "microseconds": 0, "total_seconds": 0.0},
        ]

    def test_a_shorter_payload_still_reads_without_fabricating_the_answers(self) -> None:
        """A #2448/#2458/#2466-era process's state outlives it — a Redis
        backend hands back a three-, four- or six-element payload on the first
        request after a rolling deploy — so this is a live input.

        It restores WITHOUT INVENTING what the entry never recorded: no
        comparison key (so not equal to itself, the pre-#2471 answer) and
        ``display`` as the repr, which is the honest content rather than a
        constructor form reconstructed from a string. The quoting differs from
        the pre-fix ``|pprint`` by exactly the quotes, because pprint used to
        wrap the display itself; nothing that reads the field can tell a
        recorded repr from a restored one, so the field carries the only
        spelling that entry has.

        Built by TRUNCATING a real nine-element blob, so the test cannot
        drift from the shape the serializer actually writes."""
        msgpack = pytest.importorskip("msgpack")

        from djust._rust import RustLiveView

        def truncated(source: str, n: int) -> str:
            view = RustLiveView(source)
            view.set_state("p", datetime.timedelta(0))
            view.set_state("q", datetime.timedelta(0))
            decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
            assert len(decoded[1]["p"]["__djust_encoded__"]) == 9
            for key in ("p", "q"):
                decoded[1][key]["__djust_encoded__"] = decoded[1][key]["__djust_encoded__"][:n]
            packed = msgpack.packb(decoded, use_bin_type=True)
            return RustLiveView.deserialize_msgpack(packed).render()

        for n in (3, 4, 6):
            assert truncated(self.EQ, n) == "N", f"{n}-element payload gained a key"
            assert truncated(self.REPR, n) == "0:00:00", f"{n}-element payload gained a repr"
        # EIGHT is the #2471/#2472 shape and carries both of these; it is the
        # ATTRIBUTE MAP it lacks, which #2481's own suite pins.
        assert truncated(self.EQ, 8) == "Y", "an 8-element payload lost its key"
        assert truncated(self.REPR, 8) == "datetime.timedelta(0)", (
            "an 8-element payload lost its repr"
        )
        # And the NINE-element payload for the same value answers both the new
        # way, which is what makes the arms above a compatibility read rather
        # than the bug.
        assert self._round_trip(self.EQ, datetime.timedelta(0)) == "Y"
        assert self._round_trip(self.REPR, datetime.timedelta(0)) == "datetime.timedelta(0)"


class TestBothPathsCarryTheSameEncoded:
    """#2475 (#2467) removed the Python-side flattening while this branch was
    open, so the LiveView path now builds the same ``Value::Encoded`` the raw
    path does — and all three fixes reach it.

    Asserted rather than assumed, because the premise moved once already: an
    earlier version of this module's docstring, of the CHANGELOG entry, and of
    ``test_sequence_op_chokepoint_2451.py``'s pin all described a PATH split
    that no longer exists.
    """

    @staticmethod
    def _paths(value: object) -> tuple[dict, dict]:
        from djust.serialization import normalize_django_value

        return {"p": value, "q": value}, normalize_django_value({"p": value, "q": value})

    def test_normalize_no_longer_flattens_a_datetime(self) -> None:
        """The premise itself, run rather than quoted."""
        from djust.serialization import normalize_django_value

        value = datetime.datetime(2020, 1, 1, 12, 0)
        restored = normalize_django_value({"p": value})["p"]
        assert isinstance(restored, datetime.datetime)
        assert restored == value

    @pytest.mark.parametrize("domain", sorted(GENERATORS))
    def test_all_three_fixes_reach_the_liveview_path_too(self, domain: str) -> None:
        rng = random.Random(24750)
        for _ in range(6):
            v = GENERATORS[domain](rng)
            for ctx in self._paths(v):
                # #2471
                assert _rust.render_template("{% if p == q %}Y{% else %}N{% endif %}", ctx) == "Y"
                # #2472
                assert _rust.render_template("{{ p|pprint }}", ctx) == repr(v)
                # #2473
                with pytest.raises(Exception):  # noqa: B017 - the refusal IS the fix
                    _rust.render_template('{{ p|get_digit:"1" }}', ctx)


class TestAFalsyOpaqueEncodedIsNotComparable:
    """#2476 widened ``Value::Encoded`` to carry ``set()``, ``frozenset()``,
    ``complex(0)`` and any falsy user object — so the comparison arm this PR
    adds is REACHED for values whose Python ordering it cannot know.

    It answers ``None`` for every such pair, because ``falsy_opaque`` sets no
    ``cmp_key``: never equal, never ordered. Pinned here for two reasons.

    **It is not this PR's regression, and the pin says whose it is.** Before
    #2476 a ``set()`` was a ``Value::String("set()")`` and two of them compared
    EQUAL through the ``(String, String)`` arm, which is Django's answer.
    #2476 made it an ``Encoded``, which fell to ``_ => false``. This PR's arm
    answers the same ``false`` — measured, not argued: the gate-off mutation
    that reverts ``values_equal``'s arm to ``false`` (M1) leaves every
    assertion in this class green, which is the same-answer proof.

    **No derivable rule exists, which is why it is filed rather than fixed
    (#1079).** ``set() == frozenset()`` is True in Python ACROSS type names,
    and ``LenZero() == LenZero()`` on two distinct instances is False WITHIN
    one — so neither ``type_name`` nor any carried spelling separates them.
    Answering it needs the object's ``__eq__``, i.e. a token the conversion
    would have to invent. Filed as #2480.
    """

    class LenZero:
        def __len__(self) -> int:
            return 0

    class BoolFalse:
        def __bool__(self) -> bool:
            return False

        def __str__(self) -> str:
            return "STR-SPELLING"

        def __repr__(self) -> str:
            return "REPR-SPELLING"

    FALSY_OPAQUE = ("set()", "frozenset()", "complex(0)", "LenZero()", "BoolFalse()")

    def _values(self) -> dict:
        return {
            "set()": set(),
            "frozenset()": frozenset(),
            "complex(0)": complex(0),
            "LenZero()": self.LenZero(),
            "BoolFalse()": self.BoolFalse(),
        }

    @pytest.mark.parametrize("name", FALSY_OPAQUE)
    def test_it_is_never_equal_even_to_itself_which_diverges_from_django(self, name: str) -> None:
        """The divergence, stated as a measurement so it cannot be mistaken for
        an intended answer. Django renders ``Y``; this renders ``N``."""
        v = self._values()[name]
        dj, du = branch("==", v, v)
        assert dj == "Y", "Django stopped answering equal — the pin needs revisiting"
        assert du == "N", f"{name} gained a comparison key — #2480 may be closed"

    @pytest.mark.parametrize("name", FALSY_OPAQUE)
    @pytest.mark.parametrize("op", ["<", ">"])
    def test_the_ordering_operators_agree(self, name: str, op: str) -> None:
        """The half that is RIGHT, and the reason the arm is not simply wrong:
        Python cannot order any of these either (``set`` has only a partial
        order, and ``<`` on two equal sets is False), so ``None`` is Django's
        own answer for the ordering operators."""
        v = self._values()[name]
        dj, du = branch(op, v, v)
        assert du == dj

    def test_a_falsy_opaque_against_a_datetime_is_not_comparable_on_either_engine(self) -> None:
        """The mixed pair the merge made reachable — one operand with a key and
        one without. ``python_partial_cmp``'s ``?`` short-circuits on the
        missing key before the domain check, so this is the same ``None``, and
        Django agrees for every operator."""
        for op in OPS:
            dj, du = branch(op, set(), datetime.timedelta(0))
            assert du == dj

    def test_its_repr_is_MEASURED_and_not_a_copy_of_display(self) -> None:
        """The bug the merge would otherwise have shipped, and the reason
        ``falsy_opaque`` calls ``repr()`` rather than cloning ``display`` the
        way it clones it into ``json``.

        For ``set()``, ``frozenset()`` and ``complex(0)`` the two spellings
        coincide — so every builtin ``falsy_opaque`` was written for would have
        passed a ``display``-copying implementation. A user class defines them
        independently, and ``{{ p|pprint }}`` renders whichever field is
        carried.
        """
        v = self.BoolFalse()
        assert str(v) != repr(v)
        for src, expected in (("{{ p|pprint }}", repr(v)), ("{{ p }}", str(v))):
            dj, du = both(src, {"p": v})
            assert du == expected, f"{src}: {du!r}"
            assert du == dj
        # And nested, which is `py_repr`'s own path.
        dj, du = both("{{ p }}", {"p": [v]})
        assert du == dj == f"[{v!r}]"

    def test_that_one_test_is_the_COMPLETE_set_of_distinguishers(self) -> None:
        """Why gating the measurement off reddens exactly ONE test, and why
        that is completeness rather than thin coverage.

        The gate-off mutation that replaces ``falsy_opaque``'s ``repr()`` call
        with ``display.clone()`` fails
        :func:`test_its_repr_is_MEASURED_and_not_a_copy_of_display` and nothing
        else — confirmed by a crossed run, which named that test and no other.
        A count of one is normally a question (equivalent mutation? shadowed by
        a second mechanism? missing coverage?), so this answers it: for EVERY
        builtin the widening carries, ``str(o)`` and ``repr(o)`` are the same
        string, so no cell built from them can distinguish the two
        implementations. The set of values that CAN is exactly "a user class
        defining both", and the test above is it.

        Asserted rather than asserted-in-prose, so a future builtin joining the
        carrier whose spellings DIFFER reddens here and gets its own case.
        """
        builtins_carried = [set(), frozenset(), complex(0), self.LenZero()]
        for v in builtins_carried:
            assert str(v) == repr(v), (
                f"{type(v).__name__} now spells str() and repr() differently — it "
                f"can distinguish the two implementations, so it belongs in "
                f"test_its_repr_is_MEASURED_and_not_a_copy_of_display"
            )
        # And the one shape that CAN tell them apart is in the class above.
        assert str(self.BoolFalse()) != repr(self.BoolFalse())
