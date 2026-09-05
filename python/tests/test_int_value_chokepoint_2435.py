"""``int(value)`` — the VALUE half of #2328's argument chokepoint (#2435).

What the issue said, and what running it showed
-----------------------------------------------
#2435 was filed as a ``{% widthratio %}`` bug: *"``{% widthratio %}``'s FIRST
operand is converted with ``float()`` by Django's ``WidthRatioNode.render``,
and a value that will not convert is a refusal"*, with the fix located in
``renderer.rs``'s ``Node::WidthRatio`` arm.

Both halves of that are wrong, and ``TestTheIssuesOwnClaims`` measures each
against a live Django:

1. ``float(value)`` failing is a **ValueError inside the second ``try``**,
   which Django catches and answers with the empty string. It is NOT a
   refusal, and djust already agreed — ``{% widthratio p 10 100 %}`` over
   ``p="abc"`` renders ``""`` on both engines, and did before this change.
2. Django's ``TemplateSyntaxError`` comes from the FIRST ``try``, around
   ``self.val_expr.resolve(context)`` — so it fires when a **filter** raises
   ``ValueError``/``TypeError`` while the operand is being resolved, at any of
   the three positions. The issue's own example, ``p|divisibleby:"2"``, raises
   from ``int(value)`` inside ``divisibleby``; ``float()`` never runs.

So the 4,222 divergent cells the issue measured are real and the diagnosis is
not: they are the shadow of djust's FILTERS failing soft where Django's raise.
Attributing each cell to the filter whose Django body raises splits them:

===========================  =====  ============================================
filter                       cells  Django's body
===========================  =====  ============================================
``divisibleby``              2,132  ``int(value) % int(arg)`` — nothing caught
``get_digit``                  988  ``except ValueError`` only
``escapeseq`` ``safeseq``
``first`` ``last``
``unordered_list``           1,100  iteration / subscript ``TypeError``
``json_script``                  2  tracked at #2429
===========================  =====  ============================================

This closes the ``int(value)`` column — 3,120 of 4,222 — through ONE
chokepoint, because ``int()`` was spelled four different ways in the crate
(``int_digits_of``, ``python_int_from_str``, ``divisibleby``'s own inline
``parse::<i64>()``, and ``renderer::py_int``) and none of them said WHICH
exception Python raises. The sequence/iteration column is a different
mechanism and is filed separately.

Which exception matters, because Django's four value-side ``int()`` call sites
each catch a different subset — see ``TestEachDjangoCatchIsReproduced``.
"""

import decimal
import pathlib
import random
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaultfilters import register as django_filter_registry

from djust import _rust

FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)
RENDERER_RS = FILTERS_RS.with_name("renderer.rs")

#: The message every ``int(value)`` refusal carries, so a test can tell this
#: chokepoint's raise from `wordwrap`'s width guard or `divisibleby:"0"`'s
#: ZeroDivisionError.
CHOKEPOINT = "calls int() on its value"


def _django(source: str, ctx: dict) -> str:
    try:
        return DjangoTemplate(source).render(DjangoContext(dict(ctx)))
    except Exception as exc:  # noqa: BLE001 — a raise is a comparable outcome
        return f"<<EXC {type(exc).__name__}>>"


def _djust(source: str, ctx: dict) -> str:
    try:
        return _rust.render_template(source, ctx)
    except Exception as exc:  # noqa: BLE001
        return f"<<EXC {type(exc).__name__}>>"
    except BaseException as exc:  # noqa: BLE001 — a PyO3 panic is not an Exception
        return f"<<PANIC {type(exc).__name__}>>"


def _both(source: str, ctx: dict) -> tuple[str, str]:
    return _django(source, ctx), _djust(source, ctx)


def _refused(out: str) -> bool:
    return out.startswith("<<EXC ") or out.startswith("<<PANIC ")


def _agree_on_refusal(source: str, ctx: dict) -> bool:
    """Both engines answer, identically — or both refuse.

    djust raises every template error as ``RuntimeError``; Django distinguishes
    ``ValueError`` / ``TypeError`` / ``OverflowError`` / ``TemplateSyntaxError``.
    That difference is a property of the engine's error channel and not of this
    fix, so the comparison is on WHETHER the template is refused.
    """
    dj, du = _both(source, ctx)
    if _refused(dj) or _refused(du):
        return _refused(dj) and _refused(du)
    return dj == du


class TestTheIssuesOwnClaims:
    """Each premise #2435 states, measured rather than inherited."""

    def test_a_nonnumeric_FIRST_operand_is_the_empty_string_not_a_refusal(self) -> None:
        """The issue's headline. ``float("abc")`` is caught by Django's SECOND
        ``try`` and answered with ``""`` — the cited code was already right,
        and a "fix" there would have introduced a divergence."""
        for operand in ("{% widthratio p 10 100 %}", "{% widthratio 10 p 100 %}"):
            dj, du = _both(operand, {"p": "abc"})
            assert (dj, du) == ("", ""), f"{operand} -> {dj!r} / {du!r}"

    def test_the_THIRD_operand_is_the_one_int_refuses(self) -> None:
        """``int(max_width)`` is inside the FIRST ``try``, so its ValueError IS
        the TemplateSyntaxError — and ``int()`` rejects a string ``float()``
        accepts, which is the whole reason the positions differ."""
        dj, du = _both("{% widthratio 10 2 p %}", {"p": "100.6"})
        assert dj.startswith("<<EXC TemplateSyntaxError"), dj
        assert du.startswith("<<EXC TemplateSyntaxError"), du
        # Same value, first position: `float("100.6")` is fine.
        assert _both("{% widthratio p 2 100 %}", {"p": "100.6"}) == ("5030", "5030")

    def test_the_refusal_the_issue_saw_comes_from_the_FILTER_at_any_position(self) -> None:
        """``p|divisibleby:"2"`` raises during ``resolve()``, so ALL THREE
        positions refuse — not only the first the issue names."""
        for source in (
            '{% widthratio p|divisibleby:"2" 10 100 %}',
            '{% widthratio 10 p|divisibleby:"2" 100 %}',
            '{% widthratio 10 2 p|divisibleby:"2" %}',
        ):
            dj, du = _both(source, {"p": "abc"})
            assert dj.startswith("<<EXC TemplateSyntaxError"), f"{source} -> {dj}"
            assert du.startswith("<<EXC RuntimeError"), f"{source} -> {du}"

    def test_a_bool_operand_is_not_refused_which_is_the_control(self) -> None:
        """The issue reads the ``divisibleby`` result — a ``bool`` — as the
        thing djust coerced to ``0``. ``float(False)`` is ``0.0``, so a bool
        operand renders ``0`` in BOTH engines; the refusal was never about it.
        """
        assert _both("{% widthratio p 10 100 %}", {"p": False}) == ("0", "0")
        assert _both("{% widthratio p 10 100 %}", {"p": True}) == ("10", "10")
        # And the same expression whose value CAN be computed still renders.
        assert _both('{% widthratio p|divisibleby:"2" 10 100 %}', {"p": 4}) == ("10", "10")
        assert _both('{% widthratio p|divisibleby:"2" 10 100 %}', {"p": 5}) == ("0", "0")


class TestEachDjangoCatchIsReproduced:
    """One row per (filter, exception), because the four ``except`` clauses
    differ and a rule of "refuse a bad value" would pass half of them wrongly.

    ``int(float("nan"))`` is a **ValueError** and ``int(float("inf"))`` an
    **OverflowError** — the pair a reader reliably gets backwards, and the pair
    that makes ``filesizeformat`` render for one and refuse for the other.
    """

    #: (source, value, must-refuse). Every expectation is checked against a
    #: live Django in ``test_django_agrees_with_every_row``, so the table
    #: cannot drift from the engine it claims to describe.
    ROWS = [
        # divisibleby: `int(value) % int(arg)`, nothing caught.
        ('{{ p|divisibleby:"2" }}', "abc", True),  # ValueError
        ('{{ p|divisibleby:"2" }}', None, True),  # TypeError
        ('{{ p|divisibleby:"2" }}', [1], True),  # TypeError
        ('{{ p|divisibleby:"2" }}', {"k": 1}, True),  # TypeError
        ('{{ p|divisibleby:"2" }}', float("nan"), True),  # ValueError
        ('{{ p|divisibleby:"2" }}', float("inf"), True),  # OverflowError
        ('{{ p|divisibleby:"2" }}', decimal.Decimal("NaN"), True),  # ValueError
        ('{{ p|divisibleby:"2" }}', decimal.Decimal("Infinity"), True),  # OverflowError
        ('{{ p|divisibleby:"2" }}', 4, False),
        # get_digit: `except ValueError` ONLY.
        ('{{ p|get_digit:"1" }}', "abc", False),  # ValueError -> the input
        ('{{ p|get_digit:"1" }}', float("nan"), False),  # ValueError -> the input
        ('{{ p|get_digit:"1" }}', None, True),  # TypeError escapes
        ('{{ p|get_digit:"1" }}', [1], True),  # TypeError escapes
        ('{{ p|get_digit:"1" }}', float("inf"), True),  # OverflowError escapes
        ('{{ p|get_digit:"1" }}', 42, False),
        # add: `except (ValueError, TypeError)`.
        ('{{ p|add:"1" }}', "abc", False),  # ValueError -> concatenation
        ('{{ p|add:"1" }}', None, False),  # TypeError -> ""
        ('{{ p|add:"1" }}', float("nan"), False),  # ValueError -> ""
        ('{{ p|add:"1" }}', float("inf"), True),  # OverflowError escapes
        ('{{ p|add:"1" }}', 41, False),
        # filesizeformat: `except (TypeError, ValueError, UnicodeDecodeError)`.
        ("{{ p|filesizeformat }}", "abc", False),
        ("{{ p|filesizeformat }}", None, False),
        ("{{ p|filesizeformat }}", float("nan"), False),
        ("{{ p|filesizeformat }}", float("inf"), True),  # OverflowError escapes
        ("{{ p|filesizeformat }}", decimal.Decimal("Infinity"), True),
        ("{{ p|filesizeformat }}", 2048, False),
    ]

    @pytest.mark.parametrize("source,value,refuses", ROWS, ids=repr)
    def test_django_agrees_with_every_row(self, source, value, refuses) -> None:
        """The table is a claim about Django; this is the claim's check."""
        assert _refused(_django(source, {"p": value})) is refuses

    @pytest.mark.parametrize("source,value,refuses", ROWS, ids=repr)
    def test_djust_refuses_exactly_where_django_does(self, source, value, refuses) -> None:
        out = _djust(source, {"p": value})
        assert _refused(out) is refuses, f"{source} over {value!r} -> {out!r}"
        if refuses:
            assert _refused(_djust(source, {"p": value})) is True
        else:
            assert _agree_on_refusal(source, {"p": value}), _both(source, {"p": value})

    def test_the_refusal_names_the_chokepoint(self) -> None:
        """The message distinguishes an ``int(value)`` refusal from the two
        neighbouring raises the same filter can make, so a future reader (and
        the tests above) can tell which mechanism fired."""
        with pytest.raises(RuntimeError, match=re.escape(CHOKEPOINT)):
            _rust.render_template('{{ p|divisibleby:"2" }}', {"p": "abc"})
        with pytest.raises(RuntimeError, match="ZeroDivisionError"):
            _rust.render_template('{{ p|divisibleby:"0" }}', {"p": 10})
        with pytest.raises(RuntimeError, match="needs an integer argument"):
            _rust.render_template('{{ p|divisibleby:"nope" }}', {"p": 10})


class TestTheValueReaderIsPythonsInt:
    """The answers, not only the refusals. ``divisibleby``'s reader was
    ``Value::Integer`` plus a ``parse::<i64>()``, so five value shapes Django
    computes an answer for were silently ``False``.
    """

    @pytest.mark.parametrize(
        "value",
        [
            2.5,  # int(2.5) == 2
            False,  # int(False) == 0
            True,
            decimal.Decimal("4.5"),
            10**30,  # past i64
            -(10**30),
            "1_0",  # int() accepts `_` between digits
            "  4  ",
            "+4",
            "007",
            0,
        ],
        ids=repr,
    )
    def test_every_shape_int_accepts_gets_djangos_answer(self, value) -> None:
        for divisor in ("2", "3", "7"):
            source = "{{ p|divisibleby:'%s' }}" % divisor
            dj, du = _both(source, {"p": value})
            assert dj == du, f"{source} over {value!r} -> {dj!r} / {du!r}"

    def test_both_parities_occur_so_the_sweep_is_not_always_true(self) -> None:
        """A reader that answered ``True`` unconditionally would pass the sweep
        above just as well as a correct one."""
        answers = {_djust("{{ p|divisibleby:'%s' }}" % d, {"p": 10**30}) for d in ("2", "3", "7")}
        assert answers == {"True", "False"}, answers


class TestWidthRatioReadsIntAndFloatAsPythonDoes:
    """The two coercions ``{% widthratio %}`` itself performs, which were the
    fourth and fifth spellings of Python's ``int()`` / ``float()``.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "1_0",  # int("1_0") == 10 and float("1_0") == 10.0
            10**30,  # exact past i64; the old i64 cast SATURATED
            -(10**30),
            "1" * 30,
            decimal.Decimal("1e40"),
            "  5  ",
            "+5",
            5,
            True,
            2.5,
        ],
        ids=repr,
    )
    @pytest.mark.parametrize("position", [0, 1, 2])
    def test_every_operand_position_agrees(self, value, position) -> None:
        operands = ["10", "2", "100"]
        operands[position] = "p"
        source = "{%% widthratio %s %%}" % " ".join(operands)
        assert _agree_on_refusal(source, {"p": value}), _both(source, {"p": value})

    def test_a_magnitude_past_i64_is_not_a_fabricated_number(self) -> None:
        """The old ``as i64`` cast SATURATED, so a 31-digit third operand
        rendered ``46116860184273879040`` — a number that appears nowhere in
        the calculation. Named because "it disagreed" understates it."""
        dj, du = _both("{% widthratio 10 2 p %}", {"p": 10**30})
        assert dj == du == "4999999999999999817948147482624"
        assert "46116860184273879040" not in du


class TestTheChokepointIsTheOnlyIntValueReader:
    """The structural pin. A comment is not a guard (#1859).

    ``int(value)`` had FOUR readings in this crate and they disagreed with
    Python and with each other, which is the same drift #2328 fixed on the
    argument axis. This pins the SET of call sites (not a floor, #1125): a
    fifth filter that grows its own ``parse`` on the value goes red here.
    """

    #: Django's four value-side ``int()`` call sites, as
    #: ``filter -> the exceptions its own ``except`` does NOT catch``. A SET:
    #: deleting a row fails this too.
    EXPECTED_CALLERS = {
        "divisibleby": "Value, Type, Overflow",
        "get_digit": "Type, Overflow",
        "add": "Overflow",
        "filesizeformat": "Overflow",
    }

    @staticmethod
    def _source(path: pathlib.Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_every_dispatch_arm_that_calls_int_on_a_value_goes_through_it(self) -> None:
        """``python_int_value`` is called from exactly the arms Django calls
        ``int(value)`` from, plus the renderer's ``{% widthratio %}``."""
        source = self._source(FILTERS_RS)
        dispatch = source.split("fn apply_builtin_filter(", 1)[1].split("\n}\n", 1)[0]
        arms = re.findall(r'"(\w+)" => \{(.*?)\n        \}', dispatch, re.S)
        found = {name for name, body in arms if "python_int_value(" in body}
        # add preserves both operands and delegates integer conversion to its helper.
        add_body = next(body for name, body in arms if name == "add")
        if "add_values(" in add_body:
            helper = source.split("fn add_values(", 1)[1].split("\n}\n", 1)[0]
            assert "python_int_value(value)" in helper
            assert "python_int_value(arg)" in helper
            found.add("add")
        assert found == set(self.EXPECTED_CALLERS), (
            "the set of dispatch arms reading int(value) through the chokepoint "
            f"changed: new {sorted(found - set(self.EXPECTED_CALLERS))}, "
            f"gone {sorted(set(self.EXPECTED_CALLERS) - found)}. Every one is a "
            "place Python's int() raises and Django catches a DIFFERENT subset "
            "(#2435) — add it here with the subset its Django source lets "
            "through, or route it through the chokepoint."
        )

    def test_the_renderer_reads_widthratios_operand_through_it_too(self) -> None:
        """``renderer::py_int`` was the fourth spelling; it is now a wrapper."""
        body = re.search(
            r"fn py_int\(value: &Value\) -> Option<f64> \{(.*?)\n\}",
            self._source(RENDERER_RS),
            re.S,
        )
        assert body, "renderer::py_int changed shape"
        assert "python_int_value(" in body.group(1), body.group(1)
        assert "parse::<i64>" not in body.group(1), body.group(1)

    def test_no_dispatch_arm_reparses_a_value_string_as_an_int(self) -> None:
        """The pre-#2435 shape, spelled out so it cannot come back quietly."""
        offenders = [
            line.strip()
            for line in self._source(FILTERS_RS).splitlines()
            if "s.trim().parse::<i64>()" in line or "value.trim().parse::<i64>()" in line
        ]
        assert not offenders, offenders


class TestRandomisedDifferential:
    """Not a curated table: Django is importable, so ask it (v1.1.1-2 canon).

    The table above samples the axis somebody noticed. This sweeps the four
    chokepoint filters and all three ``{% widthratio %}`` positions over
    randomly composed value shapes, and asserts refusal-parity on every cell.
    """

    #: Shapes drawn per cell. Deliberately includes the spellings ``int()`` and
    #: ``float()`` disagree about (``"2.5"``), the ones only ``int()`` accepts
    #: with a separator (``"1_0"``), and both non-finite families.
    @staticmethod
    def _value(rng: random.Random):
        return rng.choice(
            [
                rng.randint(-(10**6), 10**6),
                rng.randint(2**63, 2**80),
                -rng.randint(2**63, 2**80),
                rng.random() * 10 ** rng.randint(-6, 6),
                rng.choice([True, False, None, 0, 0.0, "", [], (), {}]),
                rng.choice([float("nan"), float("inf"), float("-inf")]),
                rng.choice(
                    [
                        decimal.Decimal("2.5"),
                        decimal.Decimal("NaN"),
                        decimal.Decimal("Infinity"),
                        decimal.Decimal("-Infinity"),
                        decimal.Decimal("1e40"),
                    ]
                ),
                rng.choice(["abc", "2.5", "1_0", "  7  ", "+9", "007", "0x10", "1" * 25]),
                [1, 2],
                {"a": 1},
            ]
        )

    SOURCES = [
        '{{ p|divisibleby:"2" }}',
        '{{ p|divisibleby:"3" }}',
        '{{ p|get_digit:"1" }}',
        '{{ p|get_digit:"2" }}',
        '{{ p|add:"1" }}',
        "{{ p|filesizeformat }}",
        "{% widthratio p 2 100 %}",
        "{% widthratio 10 p 100 %}",
        "{% widthratio 10 2 p %}",
        '{% widthratio p|divisibleby:"2" 10 100 %}',
        '{% widthratio 10 2 p|get_digit:"1" %}',
    ]

    #: The two divergences the sweep DOES find, each pre-existing, each a
    #: different mechanism from ``int(value)``, and each named rather than
    #: dropped from the corpus — a shape removed from a sweep is a shape
    #: nobody measures again.
    #:
    #: Both are asserted to actually OCCUR below, so neither can rot into an
    #: exemption that silently covers a real regression.
    @staticmethod
    def _classify(source: str, value, dj: str, du: str) -> str | None:
        if isinstance(value, decimal.Decimal) and not value.is_finite():
            # DJANGO cannot render a non-finite Decimal AT ALL: `{{ p }}` alone
            # raises `TypeError: bad operand type for abs(): 'str'` from
            # `numberformat.format`. So every branch that hands the value back
            # — `get_digit`'s `except ValueError: return value` here — inherits
            # that raise, and the disagreement is at the rendering boundary
            # rather than at `int()`. Asserted as its own premise below.
            return "django-cannot-render-a-nonfinite-decimal"
        if "filesizeformat" in source and du.startswith("0"):
            # `filesize_to_int` carries an `i128`; Python's `int` has no
            # ceiling, so a magnitude past ~1.7e38 gives up and renders
            # `0 bytes` where Django scales it. Pre-existing and untouched by
            # this change, which only adds the OverflowError refusal for a
            # genuine infinity.
            return "filesizeformat-past-i128"
        return None

    def test_the_sweep_agrees_on_every_cell(self) -> None:
        rng = random.Random(2435)
        bad, cells, refused = [], 0, 0
        explained: dict[str, int] = {}
        for _ in range(1500):
            source = rng.choice(self.SOURCES)
            value = self._value(rng)
            cells += 1
            dj, du = _both(source, {"p": value})
            refused += _refused(dj)
            if _agree_on_refusal(source, {"p": value}):
                continue
            reason = self._classify(source, value, dj, du)
            if reason is None:
                bad.append((source, value, dj, du))
            else:
                explained[reason] = explained.get(reason, 0) + 1
        assert cells == 1500
        assert refused > 100, f"only {refused} refusing cells — the sweep went vacuous"
        assert not bad, f"{len(bad)}/{cells} disagree, first three: {bad[:3]!r}"
        assert set(explained) == {
            "django-cannot-render-a-nonfinite-decimal",
            "filesizeformat-past-i128",
        }, f"the named residues are no longer the ones that occur: {explained}"

    def test_django_itself_cannot_render_a_nonfinite_decimal(self) -> None:
        """The premise of the first exemption, PROBED rather than declared.

        If Django ever learns to render these, the exemption stops being about
        the boundary and starts hiding a real cell — and this goes red first.
        """
        for value in (decimal.Decimal("NaN"), decimal.Decimal("Infinity")):
            assert _django("{{ p }}", {"p": value}).startswith("<<EXC TypeError>>"), value
            assert _djust("{{ p }}", {"p": value}) == str(value)

    def test_the_chokepoint_filters_are_still_in_djangos_registry(self) -> None:
        """Guards the sweep against a Django release renaming one of them out
        from under it — the sources above would then test nothing."""
        for name in ("divisibleby", "get_digit", "add", "filesizeformat"):
            assert name in django_filter_registry.filters, name
