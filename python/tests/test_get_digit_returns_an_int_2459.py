"""``get_digit`` answers an ``int``, not a one-character string (#2459).

The divergence
--------------
Django's body ends ``return int(str(value)[-arg])`` and its docstring says so
in as many words — *"output is always an integer"*. djust's arm answered
``Value::String((*b as char).to_string())``, and its ``except IndexError`` exit
answered ``Value::String("0")``.

The **text** was identical, which is the whole of why it survived: every
assertion at the Rust arm read ``.to_string()``, and every differential cell
that renders the digit alone agreed. The **type** is what a consumer reads, and
a ``str`` iterates, subscripts, has a ``len()`` and is truthy at ``"0"`` where
an ``int`` does none of those::

    {{ p|get_digit:"1"|pprint }}      p = 42   django  2
                                               djust   '2'

Not five chains — three classes, and only one of them raises
-------------------------------------------------------------
The issue names five consumers (``safeseq``, ``escapeseq``, ``unordered_list``,
``first``, ``last``) and calls them the cost. Re-derived by sweeping Django's
LIVE registry rather than transcribed, the set is larger and splits three ways:

* **refuses in Django, rendered here** — ``safeseq``, ``escapeseq``,
  ``unordered_list`` (``iter``); ``first``, ``last``, ``random``
  (``__getitem__`` / ``len``); ``phone2numeric`` (``.lower()``); and
  ``{% for %}`` over the digit. Seven filters and a tag, not five filters;
* **renders on both, DIFFERENT text** — ``pprint`` (``2`` vs ``'2'``),
  ``length`` (Django's ``len(int)`` raises into its own ``except`` and answers
  ``0``; a string answers ``1``), ``stringformat:"d"`` (``"%d" % "2"`` is a
  ``TypeError`` Django's ``except`` swallows, so djust answered ``""``). A
  ``django-refuses / djust-renders`` count cannot see any of these;
* **silently takes the wrong branch** — ``{% if p|get_digit:"9" %}`` and
  ``{{ p|get_digit:"9"|yesno }}``, because the ``IndexError`` exit is ``0``,
  which is falsy, and ``"0"`` is not.

The third class is the one worth the issue: no exception, no visible difference
at the digit itself, a template gate that opens where Django's closes.

The pin that is not a transcription
------------------------------------
A hand-written list of affected consumers is one short by construction — the
issue's was three short, and the #2216 → #2227 → #2228 chain is the same lesson
three times. So the load-bearing test here asserts an IDENTITY instead of a
list (:class:`TestTheOutputIsIndistinguishableFromDjangosOwnReturn`): for every
filter Django registers, and for each of ``get_digit``'s four exits,

    ``{{ p|get_digit:<n>|F }}``  over the SUBJECT
      ==
    ``{{ q|F }}``               over ``django.template.defaultfilters.get_digit(subject, n)``

on djust as well as on Django. That is the whole claim of this fix — the arm
hands on the object Django's function returns — and any type drift breaks it,
in either direction, without anyone having to have thought of the consumer.

Nothing new is added below the filter
-------------------------------------
#2451's ``ValueOpError`` / ``value_op_error`` chokepoint was **already right**
about every one of the eight refusal cells — an ``int`` is not iterable and not
subscriptable, and it refuses when it is given one. It was being handed a
``str``. So this fix adds no mechanism: it corrects one arm's return type and
the existing chokepoint does the rest. :class:`TestNoSecondMechanismWasAdded`
pins that, because a guard added at the CONSUMER instead of at the subject is
how this class comes back (#1646).

Refs #2451, #2435, #2403, #2260, #1079.
"""

from __future__ import annotations

import ast
import datetime
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import get_digit as django_get_digit  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FILTERS_RS = REPO / "crates" / "djust_templates" / "src" / "filters.rs"
DIFFERENTIAL = REPO / "scripts" / "filter-parity-differential.py"


def production(path: Path) -> str:
    """A crate module's source with every ``#[cfg(test)]`` block removed.

    The same reader :mod:`test_sequence_op_chokepoint_2451` uses, and for the
    same reason: ``filters.rs`` carries five inline test modules, so splitting
    on the first one drops most of the file.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    keep, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "#[cfg(test)]":
            while i < len(lines) and not lines[i].startswith("}"):
                i += 1
            i += 1
            continue
        keep.append(lines[i])
        i += 1
    return "\n".join(keep)


def filter_args() -> dict[str, str]:
    """``FILTER_ARGS`` read out of the differential rather than re-typed.

    A private copy would drift, and every entry in it was chosen so the filter
    RUNS rather than raising on its argument — which is the difference between
    sweeping the consumer axis and sweeping the arity axis. The first version of
    this file swept with no arguments at all and reported 29 "divergences" that
    were every argument-taking filter's ``TemplateSyntaxError``.
    """
    tree = ast.parse(DIFFERENTIAL.read_text(encoding="utf-8"))
    node = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "FILTER_ARGS"
    )
    return {
        ast.literal_eval(k): ast.literal_eval(v)
        for k, v in zip(node.keys, node.values, strict=True)
    }


FILTER_ARGS = filter_args()


def spec(name: str) -> str:
    """``name`` with one valid argument if the filter needs one."""
    arg = FILTER_ARGS.get(name)
    return f"{name}:{arg}" if arg else name


def dj(source: str, value) -> str:
    """Django's answer, or the name of the exception it raises."""
    try:
        return DjangoTemplate(source).render(DjangoContext({"p": value}))
    except Exception as exc:  # noqa: BLE001 — the refusal IS the answer
        return f"<<{type(exc).__name__}>>"


#: djust models Python's exception CLASS inside one ``RuntimeError`` message,
#: and it writes that message in two shapes: the filter chokepoint's
#: ``filter 'F' raises TypeError: …`` and the ``{% for %}`` refusal arm's bare
#: ``'int' object is not iterable``. Reading only the first is how the tag
#: positions in the first version of this file reported a disagreement between
#: two engines that both refuse.
_CLASS_FROM_MESSAGE = (
    (re.compile(r"raises (\w+Error)"), lambda m: m.group(1)),
    # `int_value_error`'s shape — `filter 'F' calls int() on its value, and
    # that conversion is a TypeError Django does not catch`. A DIFFERENT
    # sentence from the `value_op_error` one above, and this table could not
    # read it: every cell that reaches it answered `<<RuntimeError>>` and so
    # read as a disagreement with Django's `<<TypeError>>` even though both
    # engines refuse. Unreachable until #2473 gave `python_int_value` its
    # `Encoded` arm — before that no `int(value)` refusal had a subject this
    # file sweeps.
    (re.compile(r"conversion is an? (\w+Error)"), lambda m: m.group(1)),
    (re.compile(r"object is not (iterable|subscriptable)"), lambda m: "TypeError"),
    (re.compile(r"object of type '[^']+' has no len"), lambda m: "TypeError"),
    (re.compile(r"object has no attribute"), lambda m: "AttributeError"),
)


def du(source: str, value) -> str:
    """djust's answer, or the Python exception CLASS its message models.

    The class and not the message: Django says ``TypeError: 'int' object is not
    iterable`` and djust says ``RuntimeError: Template error: filter 'safeseq'
    raises TypeError: …``, and comparing the two TEXTS would mark every
    agreeing refusal as a disagreement — the defect #2454 is about, one layer
    down.
    """
    try:
        return _rust.render_template(source, normalize_django_value({"p": value}))
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        for pattern, name in _CLASS_FROM_MESSAGE:
            found = pattern.search(message)
            if found:
                return f"<<{name(found)}>>"
        return f"<<{type(exc).__name__}>>"


#: ``get_digit``'s four exits, as ``(subject, arg)`` pairs, each named for the
#: line of Django's body it reaches. One value with two digits reaches three of
#: them; the fourth needs a subject ``int()`` refuses.
EXITS = {
    "digit-hit": (42, "1"),  # `return int(str(value)[-arg])`
    "index-error": (42, "9"),  # `except IndexError: return 0`
    "arg-below-one": (42, "0"),  # `if arg < 1: return value` — the CONVERTED int
    "value-error": ("abc", "1"),  # `except ValueError: return value` — the INPUT
}

#: The subject every cell uses unless it says otherwise.
SUBJECT = 42


class TestTheTypeItself:
    """The claim, stated at the smallest surface that can see it."""

    def test_django_answers_an_int_on_both_numeric_exits(self) -> None:
        """Run, not read off the docstring (#1516).

        Both exits and both types, because ``0`` and ``"0"`` render the same
        and only one of them is falsy.
        """
        assert django_get_digit(42, "1") == 2
        assert type(django_get_digit(42, "1")) is int
        assert django_get_digit(42, "9") == 0
        assert type(django_get_digit(42, "9")) is int
        # …and the exits that hand back the input hand back its type. The
        # `arg < 1` one is an int too, because `value = int(value)` runs first.
        assert type(django_get_digit("abc", "1")) is str
        assert type(django_get_digit("42", "0")) is int

    @pytest.mark.parametrize("exit_name", sorted(EXITS))
    def test_pprint_reads_the_type_straight_off_both_engines(self, exit_name: str) -> None:
        """``pprint`` is ``repr()``, so it prints the type and not the value."""
        subject, arg = EXITS[exit_name]
        source = '{{ p|get_digit:"%s"|pprint }}' % arg
        assert dj(source, subject) == du(source, subject), source

    def test_the_digit_still_renders_as_the_same_TEXT(self) -> None:
        """The half that was already right, held.

        Stated because the fix changes the value's TYPE, and a type change that
        also changed the text would be a regression dressed as a parity fix.
        """
        for arg, expected in (("1", "2"), ("2", "4"), ("9", "0"), ("0", "42")):
            source = '{{ p|get_digit:"%s" }}' % arg
            assert du(source, SUBJECT) == expected, source
            assert dj(source, SUBJECT) == expected, source


class TestTheOutputIsIndistinguishableFromDjangosOwnReturn:
    """**The** test in this file: an identity over the whole live registry.

    For every filter Django registers and every ``get_digit`` exit, running the
    filter AFTER ``get_digit`` must answer what running it directly on the
    object ``django.template.defaultfilters.get_digit`` returns. Nothing here
    is transcribed — the consumer set is the registry, the arguments come from
    the differential, and the expected object comes from Django's own function.

    A list of affected consumers would go green the day a new filter starts
    reading the type. This does not.
    """

    @pytest.mark.parametrize("exit_name", sorted(EXITS))
    def test_djust_agrees_with_the_direct_application(self, exit_name: str) -> None:
        subject, arg = EXITS[exit_name]
        expected = django_get_digit(subject, arg)
        drifted = []
        for name in sorted(register.filters):
            if name == "random":  # draws; its refusal is covered on its own
                continue
            chained = '{{ p|get_digit:"%s"|%s }}' % (arg, spec(name))
            direct = "{{ p|%s }}" % spec(name)
            through, straight = du(chained, subject), du(direct, expected)
            if through != straight:
                drifted.append((name, through, straight))
        assert not drifted, "\n".join(
            f"{n}: through get_digit={t!r}  direct on {expected!r}={s!r}" for n, t, s in drifted
        )

    @pytest.mark.parametrize("exit_name", sorted(EXITS))
    def test_the_identity_holds_on_DJANGO_too(self, exit_name: str) -> None:
        """The premise, so the test above is a claim and not a definition.

        On Django this is true by construction — that is exactly the point.
        Asserting it keeps the pair honest: if Django's own chained and direct
        answers ever differed, the identity djust is held to would be the wrong
        one, and this would say so instead of the other test failing opaquely.
        """
        subject, arg = EXITS[exit_name]
        expected = django_get_digit(subject, arg)
        for name in sorted(register.filters):
            if name == "random":
                continue
            chained = '{{ p|get_digit:"%s"|%s }}' % (arg, spec(name))
            direct = "{{ p|%s }}" % spec(name)
            assert dj(chained, subject) == dj(direct, expected), (name, exit_name)

    def test_the_identity_is_a_canary_in_BOTH_directions(self) -> None:
        """Gate-off, in-suite: the identity must distinguish int from str.

        Action #1859 — a pin that cannot go red is decoration, and an identity
        test can look load-bearing while being satisfied by anything. Rather
        than asserting that in prose, both neighbours of the fixed answer are
        constructed and each is shown to break the identity through a REAL
        consumer:

        * the pre-fix answer (a one-character ``str``) differs at ``pprint``,
          ``length``, ``first`` and the truthiness of ``"0"``;
        * a plausible over-correction (the digit's ``str`` value as a NUMBER of
          the wrong magnitude) differs at the rendered text.
        """
        # Exit `digit-hit`, whose correct answer is the int 2.
        assert du("{{ p|pprint }}", 2) != du("{{ p|pprint }}", "2")
        assert du("{{ p|first }}", 2) != du("{{ p|first }}", "2")
        assert du("{{ p|length }}", 2) != du("{{ p|length }}", "2")
        # Exit `index-error`, whose correct answer is the int 0 — the branch
        # with no visible text difference and a different truth value.
        assert du("{% if p %}Y{% else %}N{% endif %}", 0) != du(
            "{% if p %}Y{% else %}N{% endif %}", "0"
        )
        # …and a wrong integer is caught too, so "it is an int" is not the
        # whole of what the identity checks.
        assert du("{{ p|pprint }}", 2) != du("{{ p|pprint }}", 42)


class TestTheRefusalClass:
    """The seven refusing filters, five of which the issue names."""

    def test_the_set_of_refusing_filters_is_computed_from_django(self) -> None:
        """Not a list: the set Django refuses over a digit equals the set it
        refuses over a plain ``int``.

        This is the sharpest statement of the fix. Django's refusal set for
        ``{{ p|get_digit:"1"|F }}`` over 42 and for ``{{ p|F }}`` over ``2``
        are the same set BY CONSTRUCTION (``get_digit`` returns ``2``), so
        asserting djust reproduces it is asserting that djust's ``get_digit``
        is transparent to every consumer that refuses.
        """
        django_refuses = {
            n
            for n in register.filters
            if dj('{{ p|get_digit:"1"|%s }}' % spec(n), 42).startswith("<<")
        }
        djust_refuses = {
            n
            for n in register.filters
            if du('{{ p|get_digit:"1"|%s }}' % spec(n), 42).startswith("<<")
        }
        assert djust_refuses == django_refuses, (
            djust_refuses - django_refuses,
            django_refuses - djust_refuses,
        )
        # And it is not vacuously empty — the seven whose Django body performs
        # an unguarded operation on the value (#2451) are all in it.
        assert {
            "escapeseq",
            "first",
            "last",
            "phone2numeric",
            "random",
            "safeseq",
            "unordered_list",
        } <= django_refuses, django_refuses

    def test_the_two_the_issue_missed(self) -> None:
        """``random`` and ``phone2numeric``, named because they were not.

        Both are #2451 chokepoint filters and both were rendering here, so both
        belonged in the issue's list of five. ``random`` reaches ``len(2)``
        before it draws anything, so its refusal is deterministic even though
        its answer would not be.
        """
        for name, cls in (("random", "<<TypeError>>"), ("phone2numeric", "<<AttributeError>>")):
            source = '{{ p|get_digit:"1"|%s }}' % name
            assert dj(source, SUBJECT) == cls, source
            assert du(source, SUBJECT) == cls, source


class TestTheSilentBranchDivergence:
    """The class the issue does not mention, and the one with no error to see.

    The ``except IndexError`` exit is ``0``. As a string it is ``"0"``, which is
    truthy, so a gate on a digit that does not exist opened here and closes in
    Django.
    """

    @pytest.mark.parametrize(
        "source",
        [
            '{% if p|get_digit:"9" %}Y{% else %}N{% endif %}',
            '{{ p|get_digit:"9"|yesno }}',
            '{{ p|get_digit:"9"|yesno:"y,n" }}',
            '{% if p|get_digit:"9" %}Y{% endif %}',
            '{% if not p|get_digit:"9" %}Y{% else %}N{% endif %}',
        ],
    )
    def test_a_missing_digit_is_FALSY_on_both_engines(self, source: str) -> None:
        assert dj(source, SUBJECT) == du(source, SUBJECT), source

    def test_a_present_digit_that_IS_zero_is_falsy_too(self) -> None:
        """``{{ 402|get_digit:"2" }}`` is a real ``0``, not the IndexError one.

        Both numeric exits must answer an int, and this one goes through the
        ``is_ascii_digit`` arm rather than the ``None`` arm — so a fix that
        corrected only the out-of-range exit would fail here.
        """
        source = '{% if p|get_digit:"2" %}Y{% else %}N{% endif %}'
        assert dj(source, 402) == "N"
        assert du(source, 402) == "N"

    def test_a_nonzero_digit_is_still_truthy(self) -> None:
        """The other half, so the parametrisation above is not one-sided."""
        source = '{% if p|get_digit:"1" %}Y{% else %}N{% endif %}'
        assert dj(source, SUBJECT) == "Y"
        assert du(source, SUBJECT) == "Y"


class TestTheRenderedButDifferentClass:
    """Three consumers that rendered on BOTH engines and disagreed anyway.

    Invisible to a ``django-refuses / djust-renders`` count, which is why the
    issue's figure omits them, and each named with its pre-fix answer so the
    claim is checkable rather than asserted.
    """

    @pytest.mark.parametrize(
        ("source", "was"),
        [
            ('{{ p|get_digit:"1"|pprint }}', "'2'"),
            ('{{ p|get_digit:"1"|length }}', "1"),
            ('{{ p|get_digit:"1"|stringformat:"d" }}', ""),
        ],
    )
    def test_it_agrees_now(self, source: str, was: str) -> None:
        answer = du(source, SUBJECT)
        assert answer == dj(source, SUBJECT), source
        assert answer != was, f"{source} still answers its pre-fix value"

    def test_one_cell_stops_agreeing_and_it_agreed_by_COINCIDENCE(self) -> None:
        """The only cell in 353,909 that this fix moves the wrong way.

        ``{% if p|length|get_digit:"1" %}`` over a serialized MODEL answered
        ``Y`` on both engines before, and answers ``Y`` on Django and ``N``
        here now. It is not a regression, and the control below is the whole
        argument: ``{{ p|length }}`` over the same value is ``4`` in Django and
        ``0`` here on BOTH builds — a pre-existing, unrelated divergence
        (djust's ``python_len`` tells a serialized model from a genuine dict by
        the ``object_str()`` marker, #2294). ``get_digit`` was propagating it
        all along.

        What changed is only that the propagation stopped being invisible:
        djust's ``0`` used to arrive as the STRING ``"0"``, which is truthy, so
        the branch matched Django's ``Y`` while carrying a different number.
        Two different values agreeing on one boolean is the definition of
        coincidence, and the differential classifies it that way of its own
        accord — `no longer agreeing: 1 / coincidental: 1 / REGRESSIONS: 0`.

        Recorded rather than smoothed over: a fix that made this cell "agree"
        again would have to make a falsy value truthy, which is the bug.
        """
        model = {"id": 7, "pk": 7, "__str__": "x", "__model__": "Doc"}
        # The control: `length` alone already disagrees, with no `get_digit`
        # in the template at all.
        assert dj("{{ p|length }}", model) == "4"
        assert du("{{ p|length }}", model) == "0"
        # …and the branch follows the value each engine actually holds.
        branch = '{% if p|length|get_digit:"1" %}Y{% else %}N{% endif %}'
        assert dj(branch, model) == "Y"
        assert du(branch, model) == "N"
        # The same shape over a value the two engines DO agree on agrees, which
        # is what makes the split above about `length` and not about this fix.
        plain = ["a", "b", "c", "d"]
        assert dj("{{ p|length }}", plain) == du("{{ p|length }}", plain) == "4"
        assert dj(branch, plain) == du(branch, plain) == "Y"


class TestTheTagOperandPositions:
    """The digit as a tag operand, where the refusal has nowhere to fail soft."""

    @pytest.mark.parametrize(
        "shape",
        [
            "{% for x in p|get_digit:@ARG@ %}[{{ x }}]{% empty %}E{% endfor %}",
            "{% with q=p|get_digit:@ARG@ %}[{{ q }}]{% endwith %}",
            "{% if p|get_digit:@ARG@ %}Y{% else %}N{% endif %}",
            "{% widthratio p|get_digit:@ARG@ 10 100 %}",
            "{% widthratio p|get_digit:@ARG@ 10 100 as w %}[{{ w }}]",
            "{% firstof p|get_digit:@ARG@ 'F' %}",
            "{% cycle p|get_digit:@ARG@ 'z' %}",
        ],
    )
    @pytest.mark.parametrize("arg", ['"1"', '"9"', '"0"'])
    def test_the_operand_agrees(self, shape: str, arg: str) -> None:
        source = shape.replace("@ARG@", arg)
        assert dj(source, SUBJECT) == du(source, SUBJECT), source

    def test_regroup_was_a_SEPARATE_divergence_and_is_now_CLOSED(self) -> None:
        """``{% regroup %}`` refuses too, as of #2463.

        This method was written to fail the day that happened, and it did.
        It is flipped rather than deleted, because the three-link evidence
        chain is what makes "#2463 is not ``get_digit``'s cell" checkable —
        and that stays worth checking in the other direction.

        The control is still the proof: ``{% regroup p by k %}`` over a plain
        ``int``, with NO filter in the template at all, diverged the same way,
        which is how the two issues were told apart. ``{% for %}`` — the
        parallel path — refused correctly the whole time, which is what made
        it the #1646 shape: #2451 wired the type-named refusal into the ``for``
        arm and the ``regroup`` handler kept its own older fail-soft until
        #2463 deleted it. Covered in full by
        ``python/tests/test_regroup_non_iterable_2463.py``.
        """
        control = "{% regroup p by k as g %}[{{ g|length }}]"
        assert dj(control, 2) == du(control, 2) == "<<TypeError>>"
        # …and `{% for %}`, the same question one tag over, always agreed.
        loop = "{% for x in p %}[{{ x }}]{% endfor %}"
        assert dj(loop, 2) == du(loop, 2) == "<<TypeError>>"
        # The chained cell answers for the control's reason, not a
        # `get_digit` one: both answer the same thing.
        chained = '{% regroup p|get_digit:"1" by k as g %}[{{ g|length }}]'
        assert du(chained, SUBJECT) == du(control, 2)


class TestTheInputExitsAreUntouched:
    """The exits that hand back the INPUT, and the one documented divergence.

    Action #1195: retyping one exit of a multi-exit body is where a
    neighbouring exit silently changes with it. The ``Some(_) => value.clone()``
    arm sits BETWEEN the two exits this fix changed.
    """

    @pytest.mark.parametrize("value", ["abc", "", "1.5", "0x10", "  ", "12,3"])
    def test_an_unparsable_value_comes_back_unchanged_and_still_a_str(self, value: str) -> None:
        source = '{{ p|get_digit:"1"|pprint }}'
        assert dj(source, value) == du(source, value), (source, value)

    @pytest.mark.parametrize("value", [1.5, True, False, "42", 0])
    def test_arg_below_one_still_answers_the_CONVERTED_int(self, value) -> None:
        """``value = int(value)`` runs BEFORE ``if arg < 1`` (#2403)."""
        source = '{{ p|get_digit:"0"|pprint }}'
        assert dj(source, value) == du(source, value), value

    def test_landing_on_the_minus_sign_is_STILL_the_documented_divergence(self) -> None:
        """Django raises on ``int('-')``; djust hands the value back.

        Unchanged by this fix, and asserted rather than assumed: this arm is
        the neighbour of both exits that changed.
        """
        assert dj('{{ p|get_digit:"3" }}', -42) == "<<ValueError>>"
        assert du('{{ p|get_digit:"3" }}', -42) == "-42"

    def test_a_negative_still_indexes_past_the_sign_correctly(self) -> None:
        """``str(-42)`` is four characters and Django indexes into that."""
        for arg, expected in (("1", "2"), ("2", "4")):
            source = '{{ p|get_digit:"%s"|pprint }}' % arg
            assert dj(source, -42) == expected
            assert du(source, -42) == expected

    def test_the_datetime_residue_is_CLOSED_and_now_refuses(self) -> None:
        """**CLOSED by #2473**, and this pin read "is STILL the extraction
        boundary".

        It said: *"``int(value)`` has only display text to read for a datetime,
        so djust answers a ``ValueError`` … unfixable above the boundary and
        unchanged here."* The premise was false from #2448 onward —
        ``Value::Encoded`` carries the TYPE, and ``int(datetime)`` is a
        ``TypeError`` because of the type, not because of the text. The row
        survived only because ``python_int_value`` had no ``Encoded`` arm,
        which #2473 wrote.

        Kept and inverted rather than deleted: this class is about
        ``get_digit``'s INPUT EXITS, and "the datetime exit is now a refusal
        rather than an echo" is exactly what belongs in it.
        """
        value = datetime.datetime(2020, 1, 1, 12, 0, 0)
        rendered = du('{{ p|get_digit:"1" }}', value)
        assert dj('{{ p|get_digit:"1" }}', value) == "<<TypeError>>"
        assert rendered == "<<TypeError>>", rendered
        # The echo is what it stopped doing, and that is the security-relevant
        # half: `get_digit`'s return-the-input arm carries a per-call safety
        # grant (#2403), so the datetime reached the page live.
        assert not rendered.startswith("2020-01-01"), rendered

    def test_the_class_extractor_can_read_the_int_value_refusal(self) -> None:
        """Non-vacuity for the row above.

        `du` compares the exception CLASS rather than the message, and its
        table had no entry for `int_value_error`'s sentence — so before this
        the row above would have answered `<<RuntimeError>>` and read as a
        disagreement between two engines that both refuse. Asserted directly,
        because the row above passing is also what a table that silently
        stopped matching would look like if the message ever changed to one
        the FIRST pattern happens to catch.
        """
        message = (
            "Template error: filter 'get_digit' calls int() on its value, and "
            "that conversion is a TypeError Django does not catch — Django "
            "raises here too"
        )
        matched = [
            name(m) for pattern, name in _CLASS_FROM_MESSAGE if (m := pattern.search(message))
        ]
        assert matched[:1] == ["TypeError"], matched


class TestNoSecondMechanismWasAdded:
    """The fix is a return TYPE. It is not a second copy of the refusal rule.

    #1646: the way this class comes back is a well-meant guard added at the
    consumer instead of at the subject. These pins say the arm reaches the
    integer exit by construction and that no ``get_digit``-shaped special case
    was added anywhere.
    """

    def _arm(self) -> str:
        source = production(FILTERS_RS)
        start = source.index('        "get_digit" => {')
        return source[start : source.index('\n        "iriencode" => {', start)]

    def test_the_arm_builds_an_Integer_and_no_longer_a_String(self) -> None:
        arm = self._arm()
        assert "Value::Integer(i64::from(b - b'0'))" in arm, arm[-900:]
        assert "Value::Integer(0)" in arm, arm[-900:]
        # The two spellings the fix removed. Their absence is what stops the
        # `to_string()`-only assertions elsewhere passing for the wrong reason
        # again.
        assert "Value::String((*b as char).to_string())" not in arm
        assert 'Value::String("0".to_string())' not in arm

    def test_no_new_get_digit_special_case_exists_in_the_crate(self) -> None:
        """The RENDERER does not know this filter by name.

        Asked of ``renderer.rs`` rather than of ``filters.rs``, because in
        ``filters.rs`` the name legitimately appears in doc comments and in its
        own two arms, so "does it appear" answers nothing there. What a
        consumer-side fix would look like is the name turning up in a module
        that has no business knowing it — and the renderer is the nearest such
        module, since it owns the tag operands this fix moves.
        """
        renderer = production(REPO / "crates" / "djust_templates" / "src" / "renderer.rs")
        assert "get_digit" not in renderer, "the renderer should not know this filter by name"

    def test_the_chokepoint_is_still_the_single_refusal_writer(self) -> None:
        """``value_op_error`` remains the one place this message is written.

        The #2451 invariant, re-asserted here because this PR's whole claim is
        that it needed no change: a second constructor appearing alongside it
        would mean the fix leaked into the consumer layer after all.
        """
        source = production(FILTERS_RS)
        assert source.count("pub(crate) fn value_op_error(") == 1
        assert (
            source.count("filter '{filter_name}' raises {}: {} — Django's body does not catch it")
            == 1
        )


class TestTheCountsAreReDerived:
    """The issue's arithmetic, recomputed. Both halves of it are wrong.

    #2459 says *"15 of the 17 surviving cells"*, from *"five chains, each in the
    bare ``{{ }}`` position and in both ``{% widthratio %}`` shapes"*. Neither
    half survives being run:

    * the refusal set is seven filters plus ``{% for %}``, not five — it omits
      ``random`` and ``phone2numeric``, both of them #2451 chokepoint filters;
    * three more consumers diverge WITHOUT refusing, so no ``django-refuses``
      count could have contained them at all;
    * the positions are not two. Measured on the two-build differential, the
      97 cells this fix closes sit on NINE axes — the bare ``{{ }}`` one,
      ``for`` (57 of them), ``with``, ``if``, ``cycle``, ``firstof``,
      ``firstof-as`` and both ``widthratio`` shapes. ``regroup`` is NOT among
      them: its 57 moved cells are #2463's separate defect, and its permissive
      count is 5,118 before and 5,118 after.

    The corpus-wide figure is recorded in the PR body and the CHANGELOG rather
    than asserted here: it is a property of a 353,909-cell two-build run that
    this file cannot rebuild, and a number pinned in a test that cannot
    recompute it is a transcription (#1197).
    """

    def test_the_five_named_chains_are_a_strict_SUBSET_of_the_refusal_set(self) -> None:
        named = {"safeseq", "escapeseq", "unordered_list", "first", "last"}
        actual = {
            n
            for n in register.filters
            if dj('{{ p|get_digit:"1"|%s }}' % spec(n), SUBJECT).startswith("<<")
        }
        assert named < actual, (named, actual)
        assert {"random", "phone2numeric"} <= actual - named
