"""Three filter arms whose BRANCH answer diverged from Django (#2399, #2401, #2403).

Each is the same shape one level down: a filter body with more than one exit,
where djust implemented the computing exit and got the OTHER one wrong.

* **#2401 ``yesno``** — Django returns the VALUE for an argument with fewer
  than two comma-parts, and picks ``bits[1]`` (not ``bits[2]``) for the
  ``None`` case whenever the split is not exactly three. djust ran its own
  three-way branch over a mix of the argument and the built-in defaults.
* **#2399 ``timesince`` / ``timeuntil``** — Django's ``if not value: return ""``
  guard, and an ``AttributeError`` that neither ``except`` catches for every
  truthy non-datetime. djust ECHOED the input for both halves.
* **#2403 ``get_digit``** — Django's ``except ValueError: return value`` hands
  back the INPUT OBJECT, ``SafeData`` and all; djust escaped it. And its
  ``if arg < 1: return value`` returns the value Django has ALREADY rebound to
  ``int(value)`` — which djust returned unconverted.

Every expectation here is LIVE Django, never a transcription. Three of the
issues' own stated premises were measured wrong and are corrected in
``TestTheIssuesOwnClaims``; that class exists so a reader can see which half of
each issue survived contact with a running Django.
"""

import datetime
import decimal
import itertools

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaultfilters import get_digit as django_get_digit
from django.template.defaultfilters import yesno as django_yesno
from django.utils.safestring import mark_safe

from djust import _rust
from djust.mixins.rust_bridge import _collect_safe_keys

MARKED = mark_safe("<b>x</b>")
HOSTILE = "<script>alert(1)</script>"


def _django(source: str, ctx: dict) -> str:
    """Django's answer, or a comparable marker for its raise."""
    try:
        return DjangoTemplate(source).render(DjangoContext(dict(ctx)))
    except Exception as exc:  # noqa: BLE001 — a raise is a comparable outcome
        return f"<<EXC {type(exc).__name__}: {exc}>>"


def _djust(source: str, ctx: dict) -> str:
    keys = []
    for name, value in ctx.items():
        keys.extend(_collect_safe_keys(value, name))
    try:
        return _rust.render_template_with_dirs(source, ctx, [], keys)
    except Exception as exc:  # noqa: BLE001
        return f"<<EXC {type(exc).__name__}: {exc}>>"
    except BaseException as exc:  # noqa: BLE001 — a PyO3 panic is not an Exception
        return f"<<PANIC {type(exc).__name__}: {exc}>>"


def _both(source: str, ctx: dict) -> tuple[str, str]:
    return _django(source, ctx), _djust(source, ctx)


def _raised(out: str) -> bool:
    return out.startswith("<<EXC ") or out.startswith("<<PANIC ")


#: The value shapes each filter branches on. Both a falsy and a truthy member of
#: every Python type these arms can see, plus the two payload carriers.
VALUES: dict[str, object] = {
    "str": "abc",
    "empty-str": "",
    "none": None,
    "true": True,
    "false": False,
    "zero": 0,
    "one": 1,
    "float": 1.5,
    "zero-float": 0.0,
    "decimal": decimal.Decimal("1.5"),
    "zero-decimal": decimal.Decimal("0"),
    "list": ["a"],
    "empty-list": [],
    "dict": {"a": 1},
    "empty-dict": {},
    "datetime": datetime.datetime(2020, 1, 1, 12, 0),
    "marked": MARKED,
    "hostile": HOSTILE,
}


# ---------------------------------------------------------------------------
# The issues' own rows, and the premises that did not survive measurement
# ---------------------------------------------------------------------------


class TestTheIssuesOwnClaims:
    """Every row each issue asserts about DJANGO, checked against a live one."""

    @pytest.mark.parametrize(
        ("value", "django_says"),
        [
            (True, "True"),
            (False, "False"),
            (None, "None"),
            ("", ""),
            ("abc", "abc"),
            (0, "0"),
            (1, "1"),
        ],
    )
    def test_2401_one_part_argument_returns_the_value(self, value, django_says) -> None:
        """#2401's table: every shape, Django returns the value itself."""
        assert _django('{{ p|yesno:"only" }}', {"p": value}) == django_says

    def test_2401_claims_the_escaping_half_is_already_correct_and_it_is_not(self) -> None:
        """The issue says "both engines escape". Measured: Django emits LIVE.

        The ``len(bits) < 2`` branch returns the INPUT OBJECT, so a ``mark_safe``
        input comes back ``SafeData`` and ``render_value_in_context`` does not
        escape it. The safety half was only "already correct" while djust never
        took the branch at all.
        """
        assert _django('{{ p|yesno:"only" }}', {"p": MARKED}) == "<b>x</b>"

    def test_2401_a_four_part_argument_uses_bits_1_for_none_not_bits_3(self) -> None:
        """The unpack raises for len 4 as well as len 2 — same fallback."""
        assert django_yesno(None, "a,b,c,d") == "b"
        assert django_yesno(None, "a,b") == "b"
        assert django_yesno(None, "a,b,c") == "c"

    @pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
    @pytest.mark.parametrize("value", ["abc", 5, 1.5, True, ["a"], {"a": 1}])
    def test_2399_truthy_non_datetimes_raise_attributeerror(self, filter_name, value) -> None:
        """#2399's table: neither ``except`` catches ``AttributeError``."""
        out = _django("{{ p|%s }}" % filter_name, {"p": value})
        assert out.startswith("<<EXC AttributeError:"), out

    @pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
    @pytest.mark.parametrize("value", ["", None, 0, False, []])
    def test_2399_falsy_values_are_the_empty_string(self, filter_name, value) -> None:
        """#2399's table: the ``if not value`` guard, for both filters."""
        assert _django("{{ p|%s }}" % filter_name, {"p": value}) == ""

    def test_2403_the_pass_through_branch_keeps_the_inputs_grant(self) -> None:
        """#2403's cell, and its neighbours that the issue says already agree."""
        assert _django("{{ p|get_digit:1 }}", {"p": MARKED}) == "<b>x</b>"
        assert _django("{{ p|slice:':2' }}", {"p": MARKED}) == "<b"
        assert _django('{{ p|add:"1" }}', {"p": MARKED}) == "<b>x</b>1"

    def test_2403_calls_arg_lt_1_the_same_answer_as_the_valueerror_and_it_is_not(
        self,
    ) -> None:
        """The issue quotes both ``return value`` statements as one branch.

        They are not. ``value = int(value)`` runs BEFORE ``if arg < 1``, so that
        exit returns the CONVERTED int, and only the ``except ValueError`` exit
        returns the input object. The distinction is exactly what decides
        whether the safety grant applies, so it cannot be papered over:
        ``int(False)`` is ``0`` and a bare ``False`` is not.
        """
        assert django_get_digit(False, 0) == 0
        assert django_get_digit(1.5, -1) == 1
        assert django_get_digit("abc", 1) == "abc"
        assert _django("{{ p|get_digit:0 }}", {"p": False}) == "0"
        # …and the grant is NOT on that exit: an int is never SafeData.
        assert _django("{{ p|get_digit:0 }}", {"p": mark_safe("123")}) == "123"


# ---------------------------------------------------------------------------
# #2401 — yesno
# ---------------------------------------------------------------------------

#: Every argument shape Django's ``bits = arg.split(",")`` can produce, at each
#: length its unpack branches on, plus the no-argument spelling.
YESNO_ARGS = [
    None,
    '""',
    '"only"',
    '"a,b"',
    '"a,b,c"',
    '"a,b,c,d"',
    '","',
    '",,"',
    '"<b>,<i>,<u>"',
]


def _yesno_source(arg: str | None) -> str:
    return "{{ p|yesno }}" if arg is None else "{{ p|yesno:%s }}" % arg


def yesno_bits(arg: str | None) -> int:
    """``len(arg.split(","))`` as Django computes it, from the SOURCE spelling."""
    if arg is None:
        return 3  # the `gettext("yes,no,maybe")` default
    return len(arg.strip('"').split(","))


#: The one value shape a return-the-input branch cannot agree on, and not for
#: any reason belonging to these arms: a `datetime` is a `Value::String` of its
#: ISO spelling by the time any filter sees it, so handing the INPUT back hands
#: back that spelling rather than Django's localized `Jan. 1, 2020, noon`.
#: `TestTheDatetimeRowMeasuresTheBOUNDARY` proves that mechanically rather than
#: asserting it, so this exclusion cannot quietly grow to cover a real defect.
BOUNDARY_RESIDUE = "datetime"


class TestYesnoIsDjangosBody:
    @pytest.mark.parametrize("arg", YESNO_ARGS)
    @pytest.mark.parametrize("key", sorted(set(VALUES) - {BOUNDARY_RESIDUE}))
    def test_every_value_by_every_argument_shape(self, arg, key) -> None:
        source = _yesno_source(arg)
        expected, got = _both(source, {"p": VALUES[key]})
        assert got == expected, f"{source} over {key}: django={expected!r} djust={got!r}"

    @pytest.mark.parametrize("arg", YESNO_ARGS)
    def test_the_datetime_row_agrees_wherever_the_argument_is_VALID(self, arg) -> None:
        """The exclusion above is the return-the-input branch only.

        Every argument with two or more parts builds its answer from the parts,
        so the boundary spelling never reaches the page and the row agrees.
        """
        if yesno_bits(arg) < 2:
            pytest.skip("the return-the-input branch — covered by the boundary test")
        source = _yesno_source(arg)
        expected, got = _both(source, {"p": VALUES[BOUNDARY_RESIDUE]})
        assert got == expected

    def test_an_absent_variable_takes_the_FALSE_arm_not_the_none_arm(self) -> None:
        """``string_if_invalid`` substitutes ``""`` BEFORE the filter runs.

        So a missing name is falsy-but-not-None and Django answers ``no``. This
        is the row a curated test writes as ``maybe`` from the docstring's
        table, which is what djust had.
        """
        expected, got = _both('{{ absent|yesno:"a,b,c" }}', {})
        assert expected == "b"
        assert got == expected

    def test_the_one_part_branch_returns_the_value_and_its_grant(self) -> None:
        expected, got = _both('{{ p|yesno:"only" }}', {"p": MARKED})
        assert expected == "<b>x</b>"
        assert got == expected

    def test_the_one_part_branch_does_not_grant_an_UNMARKED_value(self) -> None:
        """The grant is the INPUT's, never minted — an unmarked payload escapes."""
        expected, got = _both('{{ p|yesno:"only" }}', {"p": HOSTILE})
        assert "<script>" not in got
        assert got == expected

    def test_a_computed_branch_never_carries_the_grant(self) -> None:
        """``yes``/``no``/``maybe`` come from the ARGUMENT, which is a plain str.

        ``SafeString.split(",")`` yields plain ``str``s, so even a ``mark_safe``d
        value picking a markup-bearing part is escaped by both engines.
        """
        expected, got = _both('{{ p|yesno:"<b>,<i>,<u>" }}', {"p": MARKED})
        assert "<b>" not in got
        assert got == expected


class TestAnArgumentThatIsPythonNone:
    """``if arg is None`` is an IDENTITY test, and ``str(None)`` is ``"None"``.

    Found by the two-build differential, as a REGRESSION the first pass of the
    ``len(bits) < 2`` early return introduced: ``{{ p|yesno:None }}`` used to
    agree with Django by coincidence — the old three-way branch read
    ``bits[1]``/``bits[2]`` off the defaults when the argument had one part —
    and the early return made it hand back the value instead.

    A spelling test cannot separate the three shapes below, which is why the
    argument's resolved TYPE is threaded (``ArgType::is_none``) rather than
    sniffed off the text. The third row is the one that makes the distinction
    load-bearing: a context variable holding the STRING ``"None"`` really does
    take the return-the-value branch.
    """

    @pytest.mark.parametrize("key", sorted(VALUES))
    def test_a_bare_None_literal_argument_means_the_DEFAULT_triple(self, key) -> None:
        expected, got = _both("{{ p|yesno:None }}", {"p": VALUES[key]})
        assert got == expected, f"over {key}: django={expected!r} djust={got!r}"

    @pytest.mark.parametrize("key", sorted(VALUES))
    def test_a_bound_None_argument_means_the_DEFAULT_triple_too(self, key) -> None:
        expected, got = _both("{{ p|yesno:q }}", {"p": VALUES[key], "q": None})
        assert got == expected, f"over {key}: django={expected!r} djust={got!r}"

    @pytest.mark.parametrize("key", sorted(set(VALUES) - {BOUNDARY_RESIDUE}))
    def test_the_STRING_None_is_a_one_part_argument_and_returns_the_value(self, key) -> None:
        """The row a spelling fallback gets wrong, in both directions.

        ``datetime`` is excluded for the reason ``BOUNDARY_RESIDUE`` names: this
        is a return-the-input branch, and ``{{ p }}`` alone already diverges
        there.
        """
        expected, got = _both("{{ p|yesno:q }}", {"p": VALUES[key], "q": "None"})
        assert got == expected, f"over {key}: django={expected!r} djust={got!r}"

    def test_the_three_spellings_do_not_all_agree_with_each_other(self) -> None:
        """Non-vacuity: if they did, the type bit would be unobservable."""
        as_literal = _django("{{ p|yesno:None }}", {"p": None})
        as_string = _django("{{ p|yesno:q }}", {"p": None, "q": "None"})
        assert as_literal == "maybe"
        assert as_string == "None"
        assert as_literal != as_string

    def test_a_None_argument_carries_no_return_the_input_grant(self) -> None:
        """It took the DEFAULT triple, so the result is built from parts."""
        expected, got = _both("{{ p|yesno:None }}", {"p": MARKED})
        assert expected == "yes"
        assert got == expected

    def test_a_quoted_None_is_a_literal_string_not_python_None(self) -> None:
        expected, got = _both('{{ p|yesno:"None" }}', {"p": "abc"})
        assert expected == "abc"
        assert got == expected


# ---------------------------------------------------------------------------
# #2399 — timesince / timeuntil
# ---------------------------------------------------------------------------

#: Django's ``if not value`` guard is Python truthiness, so these are the rows
#: that must render ``""`` rather than raise.
FALSY = [
    "empty-str",
    "none",
    "false",
    "zero",
    "zero-float",
    "zero-decimal",
    "empty-list",
    "empty-dict",
]
#: Truthy and not a datetime — ``timesince()`` reaches ``value.year``.
TRUTHY_NON_DATETIME = [
    "str",
    "true",
    "one",
    "float",
    "decimal",
    "list",
    "dict",
    "marked",
    "hostile",
]


class TestTimesinceRefusesWhereDjangoRefuses:
    @pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
    @pytest.mark.parametrize("key", FALSY)
    def test_a_falsy_value_is_the_empty_string_on_both_engines(self, filter_name, key) -> None:
        source = "{{ p|%s }}" % filter_name
        expected, got = _both(source, {"p": VALUES[key]})
        assert expected == ""
        assert got == expected, f"{source} over {key}: django={expected!r} djust={got!r}"

    @pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
    @pytest.mark.parametrize("key", TRUTHY_NON_DATETIME)
    def test_a_truthy_non_datetime_is_refused_by_BOTH_engines(self, filter_name, key) -> None:
        """Django raises ``AttributeError``; djust raises its own render error.

        The exception CLASS differs — every djust render error crosses PyO3 as
        ``RuntimeError`` — so what is asserted is the property the issue is
        about: neither engine puts a page on the screen. Returning ``""`` here
        would be a THIRD answer, neither engine's.
        """
        source = "{{ p|%s }}" % filter_name
        expected, got = _both(source, {"p": VALUES[key]})
        assert _raised(expected), expected
        assert _raised(got), f"{source} over {key}: django refused, djust rendered {got!r}"

    @pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
    def test_the_echo_is_gone_for_every_truthy_non_datetime(self, filter_name) -> None:
        """No shape of the input reaches the page — the whole of the defect."""
        source = "{{ p|%s }}" % filter_name
        for key in TRUTHY_NON_DATETIME:
            got = _djust(source, {"p": VALUES[key]})
            assert _raised(got), f"{source} over {key} still rendered {got!r}"

    @pytest.mark.parametrize("filter_name", ["timesince", "timeuntil"])
    def test_a_real_datetime_still_measures(self, filter_name) -> None:
        """The refusal is on the unreadable branch only."""
        source = "{{ p|%s }}" % filter_name
        expected, got = _both(source, {"p": VALUES["datetime"]})
        assert not _raised(expected)
        assert got == expected

    def test_the_argument_form_still_measures_against_its_argument(self) -> None:
        """#2344's fix is untouched: two datetimes still compare."""
        ctx = {
            "p": datetime.datetime(2020, 1, 1, 12, 0),
            "q": datetime.datetime(2021, 3, 1, 12, 0),
        }
        expected, got = _both("{{ p|timesince:q }}", ctx)
        assert not _raised(expected)
        assert got == expected

    def test_an_absent_variable_is_the_empty_string(self) -> None:
        """``string_if_invalid`` is ``""``, which the guard catches."""
        expected, got = _both("{{ absent|timesince }}", {})
        assert expected == ""
        assert got == expected


# ---------------------------------------------------------------------------
# #2403 — get_digit
# ---------------------------------------------------------------------------

GET_DIGIT_ARGS = ["1", "2", "9", "0", "-1", '"x"', '""']


class TestGetDigitsPassThroughBranch:
    def test_the_issues_cell(self) -> None:
        expected, got = _both("{{ p|get_digit:1 }}", {"p": MARKED})
        assert expected == "<b>x</b>"
        assert got == expected

    @pytest.mark.parametrize("arg", GET_DIGIT_ARGS)
    def test_a_marked_input_that_int_refuses_keeps_its_grant(self, arg) -> None:
        source = "{{ p|get_digit:%s }}" % arg
        expected, got = _both(source, {"p": MARKED})
        assert got == expected, f"{source}: django={expected!r} djust={got!r}"

    @pytest.mark.parametrize("arg", GET_DIGIT_ARGS)
    def test_an_UNMARKED_payload_is_escaped_by_both(self, arg) -> None:
        """The grant is the input's. Without one, nothing is emitted live."""
        source = "{{ p|get_digit:%s }}" % arg
        expected, got = _both(source, {"p": HOSTILE})
        assert "<script>" not in got, f"{source} emitted a live payload: {got!r}"
        assert got == expected

    @pytest.mark.parametrize("key", ["false", "true", "float", "decimal", "one", "zero"])
    @pytest.mark.parametrize("arg", ["0", "-1"])
    def test_the_arg_below_one_exit_returns_the_CONVERTED_int(self, key, arg) -> None:
        """``value = int(value)`` runs before ``if arg < 1``, so ``False`` is ``0``."""
        source = "{{ p|get_digit:%s }}" % arg
        expected, got = _both(source, {"p": VALUES[key]})
        assert got == expected, f"{source} over {key}: django={expected!r} djust={got!r}"

    def test_the_converted_exit_carries_NO_grant(self) -> None:
        """Django's ``int`` is not ``SafeData`` — nothing to hand on."""
        expected, got = _both("{{ p|get_digit:0 }}", {"p": mark_safe("123")})
        assert expected == "123"
        assert got == expected

    @pytest.mark.parametrize("arg", GET_DIGIT_ARGS)
    @pytest.mark.parametrize("key", ["str", "empty-str", "zero", "one", "float", "decimal"])
    def test_every_readable_shape_still_agrees(self, arg, key) -> None:
        source = "{{ p|get_digit:%s }}" % arg
        expected, got = _both(source, {"p": VALUES[key]})
        assert got == expected, f"{source} over {key}: django={expected!r} djust={got!r}"


# ---------------------------------------------------------------------------
# The direction constraint, over the whole of the three arms
# ---------------------------------------------------------------------------


class TestNoArmIsMorePermissiveThanDjango:
    """No cell in any of the three arms emits a live fragment Django does not.

    The sweep is the product of every source shape above with every value, run
    with the payload BOTH marked and unmarked, because a grant fix is exactly
    the change that can turn an escaped cell live.
    """

    @staticmethod
    def _sources():
        for arg in YESNO_ARGS:
            yield _yesno_source(arg)
        for name in ("timesince", "timeuntil"):
            yield "{{ p|%s }}" % name
        for arg in GET_DIGIT_ARGS:
            yield "{{ p|get_digit:%s }}" % arg

    @pytest.mark.parametrize("payload", [HOSTILE, mark_safe(HOSTILE)])
    def test_no_live_payload_django_does_not_also_emit(self, payload) -> None:
        offenders = []
        for source in self._sources():
            expected, got = _both(source, {"p": payload})
            if _raised(got):
                # A refusal is not a page. It must not carry the payload into
                # its message either — an error string reaches logs and the
                # `LiveViewConsumer` error frame — so that is asserted rather
                # than exempted.
                assert "<script>" not in got, f"{source} put the payload in its error: {got!r}"
                continue
            if "<script>" in got and "<script>" not in expected:
                offenders.append((source, expected, got))
        assert not offenders, offenders

    def test_the_sweep_is_not_empty(self) -> None:
        """Assert the harness's own precondition: it built the cells it claims."""
        sources = list(self._sources())
        assert len(sources) == len(YESNO_ARGS) + 2 + len(GET_DIGIT_ARGS)
        assert len(sources) == len(set(sources))

    def test_no_arm_renders_where_django_refuses(self) -> None:
        """The over-permissive direction, stated as its own assertion.

        Scoped to the shapes ``int()`` accepts, because ``get_digit`` over a
        ``None`` / list / dict / datetime is a `TypeError` in Django that djust
        does not raise — the #2366 rule on the VALUE side rather than on the
        argument, untouched here and pinned as out of scope by
        ``TestTheResiduesThisPRDoesNotTouch``.
        """
        offenders = []
        readable = sorted(
            set(VALUES) - {"none", "list", "empty-list", "dict", "empty-dict", BOUNDARY_RESIDUE}
        )
        for source, key in itertools.product(self._sources(), readable):
            expected, got = _both(source, {"p": VALUES[key]})
            if _raised(expected) and not _raised(got):
                offenders.append((source, key, expected[:60], got))
        assert not offenders, offenders


class TestTheResiduesThisPRDoesNotTouch:
    """Two divergences these arms sit next to, named rather than left silent."""

    def test_the_datetime_row_measures_the_extraction_BOUNDARY(self) -> None:
        """``{{ p }}`` ALONE already diverges for a datetime.

        So a return-the-input branch handing back a datetime cannot agree, and
        the exclusion in ``BOUNDARY_RESIDUE`` is about the boundary rather than
        about these arms. Proved by measuring the no-filter cell and a second
        return-the-input filter that predates this PR, not asserted.
        """
        dt = VALUES[BOUNDARY_RESIDUE]
        bare_expected, bare_got = _both("{{ p }}", {"p": dt})
        assert bare_got != bare_expected
        # `default`'s truthy branch is the same return-the-input shape, and it
        # diverges identically — with no change from this PR.
        other_expected, other_got = _both('{{ p|default:"z" }}', {"p": dt})
        assert other_got != other_expected
        assert other_got == bare_got

    @pytest.mark.parametrize("key", ["none", "list", "dict"])
    def test_get_digit_over_a_value_int_refuses_now_raises_too(self, key) -> None:
        """CLOSED by #2435 — this pin read "is still a TypeError gap".

        Django's ``int(value)`` raises TypeError, which its
        ``except ValueError`` misses. The #2366 rule — ``int()`` is a TypeError,
        not a ValueError, for a non-string non-number — applied to the VALUE
        rather than to the argument. It was out of scope per CLAUDE.md #1079
        and pinned so the next reader would see a named gap; the pin went RED
        as a stale pin the day it was closed, which is what it was for.
        """
        expected, got = _both("{{ p|get_digit:1 }}", {"p": VALUES[key]})
        assert expected.startswith("<<EXC TypeError:"), expected
        assert _raised(got), got
        assert "calls int() on its value" in got, got

    def test_get_digit_over_a_datetime_now_raises_too(self) -> None:
        """**CLOSED by #2473**, and this pin read "is still the extraction
        BOUNDARY".

        It said: *"a `datetime` is already a `Value::String` by the time any
        filter sees it (the PyO3 extraction boundary) … no amount of work below
        the boundary can tell those apart, which is why this row stays."* That
        stopped being true when #2448 gave the family `Value::Encoded`; the row
        stayed only because `python_int_value` had no arm for it, which #2473
        wrote. The pin went RED as a stale pin the day it was closed, which is
        what it was for — the same shape as
        `test_get_digit_over_a_value_int_refuses_now_raises_too` above.

        The `BOUNDARY_RESIDUE` exclusion itself is NOT closed and stays: the
        return-the-input branches of `yesno` / `default` still hand back
        djust's `str(o)` where Django hands back its LOCALIZED spelling, which
        is a rendering divergence rather than a typing one
        (`test_the_datetime_row_measures_the_extraction_BOUNDARY` above).
        """
        expected, got = _both("{{ p|get_digit:1 }}", {"p": VALUES[BOUNDARY_RESIDUE]})
        assert expected.startswith("<<EXC TypeError:"), expected
        assert _raised(got), got
        assert "calls int() on its value" in got, got
        assert "TypeError" in got, got
