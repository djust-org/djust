"""`bool(set())` is `False` on both engines — the falsiness rule reaches the
conversion (#2466).

The divergence
--------------
::

    {% if p %}T{% else %}F{% endif %}    p = set()         python False   django F   djust T
                                        p = frozenset()   python False   django F   djust T

A `set` has no `Value` variant, so `FromPyObject for Value` landed it on its
final `Ok(Value::String(ob.str()?))` and it arrived as the non-empty string
`"set()"`. `Value::String`'s `is_truthy` is `!s.is_empty()`, so it was truthy.

This is the #2458 shape one level up: a Python-falsy object arriving as a
non-empty display string. #2458's fix gave `Value::Encoded` a `truthy` field
and does not reach it, because a `set` never becomes an `Encoded`.

The class is SEVEN shapes, not two, and it is OPEN
---------------------------------------------------
The issue names `set` and `frozenset` and asks the right question — *"which
other Python-falsy objects have no variant?"* Swept against live Django over
32 container and scalar shapes, the answer is:

===========================  ==========================================
shape                        why it was truthy here
===========================  ==========================================
``set()``                    the ``str()`` fallback
``frozenset()``              the ``str()`` fallback
``complex(0)``               the ``str()`` fallback (no ``f64`` extraction)
``{}.keys()``                the ``str()`` fallback — the ``DictView``
``{}.values()``              variant exists, but only the template's own
``{}.items()``               ``d.keys`` access ever built one
``__len__`` → 0              the ``str()`` fallback
``__bool__`` → ``False``     the ``str()`` fallback
``__len__`` → 0 WITH attrs   the ``__dict__`` bulk-dump arm — NOT the
                             ``str()`` fallback, and NOT closed here
                             (filed as #2478 and closed there, once
                             #2481 gave the carrier an attribute map)
===========================  ==========================================

The last two rows are user classes, so the set cannot be enumerated: that is
the argument for the issue's option 2 (carry `bool(o)`) over its option 1 (give
`set` a variant). A one-type fix here is the shape #2129 took five rounds over.

Nineteen shapes that were ALREADY right are pinned too — `{}`, `[]`, `()`,
`""`, `0`, `Decimal("0")`, `timedelta(0)`, `b""`, `range(0)`, `deque()`,
`memoryview(b"")`, … — because they are what a fix that over-reached would
break, and because "which have a variant" is the question the issue asks.

Where the fix lives, and the mechanism it does NOT add
-------------------------------------------------------
`Encoded` already IS the carrier this needs: a Python object held by its
`type_name` / `display` / `json` / `truthy` spellings, because the object
itself cannot cross. #2448 built it for the four `DjangoJSONEncoder` types and
#2458 added the truthiness bit. `falsy_opaque` widens the set of objects that
use it and adds no new carrier — a new `Value` variant would be a second one
for a single question (#1646), and would have to be classified at every
wildcard `match` arm in the workspace.

One new bit is carried, `sized_empty`, and it is not about truthiness. Django's
`ForNode` reads `len` when the object has one and calls `list()` only when it
does not, so `{% for x in set() %}` renders the `{% empty %}` branch while
`{% for x in complex(0) %}` raises. Without that bit a truthiness-only fix
would have made every one of these REFUSE, which is the direction this class of
change must not move — and the first version of this fix did exactly that,
caught by re-running the axis rather than by inspection.

The parallel path it exposed
-----------------------------
`{% for %}` does not call `filters::iter_values`; the `Node::For` arm has its
own match that normalises `String` / `Object` / `DictView` into a `List` and
lets everything else fall to the refusal arm. Two implementations of "what does
this value iterate to" (#1646). They agreed before this change and had to be
moved together; `renderer.rs::for_iterability_agrees_with_iter_values` pins the
equivalence in both directions so a future shape cannot be added to one alone.

Every expectation here is LIVE Django and LIVE Python, never a transcription.

Refs #2466, #2458, #2448, #2464, #1646, #1079.
"""

from __future__ import annotations

import array
import collections
import datetime
import decimal
import json
import pathlib
from fractions import Fraction

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE_RS = REPO / "crates" / "djust_core" / "src" / "lib.rs"
FILTERS_RS = REPO / "crates" / "djust_templates" / "src" / "filters.rs"
RENDERER_RS = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"

IF = "{% if p %}T{% else %}F{% endif %}"
BARE = "{{ p }}"
LENGTH = "{{ p|length }}"
FOR = "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"


def django_render(source: str, context: dict) -> str:
    try:
        return DjangoTemplate(source).render(DjangoContext(dict(context)))
    except Exception:  # noqa: BLE001 — the refusal IS the answer
        return "<<REFUSED>>"


def djust_render(source: str, context: dict) -> str:
    try:
        return _rust.render_template(source, dict(context))
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


class LenZero:
    """`bool()` via `__len__`, and no instance attributes."""

    def __len__(self) -> int:
        return 0


class BoolFalse:
    """`bool()` via `__bool__`, and NO `__len__` — so Django's `for` raises."""

    def __bool__(self) -> bool:
        return False


class LenZeroWithAttrs:
    """Falsy AND carrying attributes — the one member NOT closed here."""

    def __init__(self) -> None:
        self.a = 1

    def __len__(self) -> int:
        return 0


class Truthy:
    """A no-variant object Python calls TRUTHY. The control."""


def empty_generator():
    return
    yield


#: The shapes this fix closes. Named rather than derived, because each one is a
#: DIFFERENT reason the value had no variant — and the two user classes are the
#: proof that the set is open.
CLOSED = [
    pytest.param(set(), id="set"),
    pytest.param(frozenset(), id="frozenset"),
    pytest.param(complex(0), id="complex-zero"),
    pytest.param({}.keys(), id="dict_keys-empty"),
    pytest.param({}.values(), id="dict_values-empty"),
    pytest.param({}.items(), id="dict_items-empty"),
    pytest.param(LenZero(), id="user-len-zero"),
    pytest.param(BoolFalse(), id="user-bool-false"),
]

#: Falsy shapes that were ALREADY right, because they DO reach a variant. A fix
#: that over-reached takes these with it, so they are swept, not assumed.
ALREADY_RIGHT = [
    pytest.param(None, id="None"),
    pytest.param(False, id="False"),
    pytest.param(0, id="int-zero"),
    pytest.param(0.0, id="float-zero"),
    pytest.param("", id="str-empty"),
    pytest.param([], id="list-empty"),
    pytest.param((), id="tuple-empty"),
    pytest.param({}, id="dict-empty"),
    pytest.param(decimal.Decimal("0"), id="Decimal-zero"),
    pytest.param(datetime.timedelta(0), id="timedelta-zero"),
    pytest.param(b"", id="bytes-empty"),
    pytest.param(bytearray(), id="bytearray-empty"),
    pytest.param(range(0), id="range-empty"),
    pytest.param(collections.deque(), id="deque-empty"),
    pytest.param(collections.OrderedDict(), id="OrderedDict-empty"),
    pytest.param(collections.Counter(), id="Counter-empty"),
    pytest.param(array.array("i"), id="array-empty"),
    pytest.param(Fraction(0), id="Fraction-zero"),
    pytest.param(memoryview(b""), id="memoryview-empty"),
]

#: Python-TRUTHY values, the control on the other side of the gate.
TRUTHY = [
    pytest.param({1}, id="set-nonempty"),
    pytest.param(frozenset({1}), id="frozenset-nonempty"),
    pytest.param(complex(1), id="complex-nonzero"),
    pytest.param({"a": 1}.keys(), id="dict_keys-nonempty"),
    pytest.param(Truthy(), id="user-plain"),
    pytest.param(empty_generator(), id="generator"),
    pytest.param(iter([]), id="list_iterator"),
    pytest.param("x", id="str"),
    pytest.param(1, id="int"),
    pytest.param([1], id="list"),
]


class TestPythonsOwnAnswerIsRunNotTranscribed:
    """`bool()` for every shape, called rather than written down.

    The issue's table lists `False` for `set()` and `frozenset()`. The point of
    calling it is the OTHER nineteen rows: `bool(memoryview(b""))`,
    `bool(array.array("i"))` and `bool(Fraction(0))` are not obvious, and a
    transcribed table is how #2451's issue named five filters that were not in
    its class.
    """

    @pytest.mark.parametrize("value", CLOSED + ALREADY_RIGHT)
    def test_python_calls_it_falsy(self, value) -> None:
        assert bool(value) is False, value

    @pytest.mark.parametrize("value", TRUTHY)
    def test_python_calls_it_truthy(self, value) -> None:
        assert bool(value) is True, value


class TestTheGateAgreesWithPython:
    """`{% if p %}` — the cell the issue cites, over the whole axis."""

    @pytest.mark.parametrize("value", CLOSED)
    def test_a_falsy_no_variant_object_is_falsy(self, value) -> None:
        assert django_render(IF, {"p": value}) == "F"
        assert djust_render(IF, {"p": value}) == "F"

    def test_the_cited_cells_verbatim(self) -> None:
        for value in (set(), frozenset()):
            assert bool(value) is False
            assert django_render(IF, {"p": value}) == "F"
            assert djust_render(IF, {"p": value}) == "F"

    @pytest.mark.parametrize("value", ALREADY_RIGHT)
    def test_a_falsy_value_WITH_a_variant_is_unchanged(self, value) -> None:
        assert django_render(IF, {"p": value}) == "F"
        assert djust_render(IF, {"p": value}) == "F"

    @pytest.mark.parametrize("value", TRUTHY)
    def test_a_truthy_value_is_untouched(self, value) -> None:
        assert django_render(IF, {"p": value}) == "T"
        assert djust_render(IF, {"p": value}) == "T"


class TestTheForArmDoesNotBecomeSTRICTERThanDjango:
    """The direction this change must not move, and the one it first did.

    A truthiness-only fix routed every one of these through a carrier
    `iter_values` calls not-iterable, so `{% for x in set() %}` REFUSED where
    Django renders the `{% empty %}` block. That is a permissiveness regression
    in the strict direction and the corpus's `djust REFUSES & Django RENDERS`
    column is exactly the number it would have grown. `Encoded::sized_empty` is
    what makes the two answers Django's own.
    """

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(set(), id="set"),
            pytest.param(frozenset(), id="frozenset"),
            pytest.param({}.keys(), id="dict_keys"),
            pytest.param({}.values(), id="dict_values"),
            pytest.param({}.items(), id="dict_items"),
            pytest.param(LenZero(), id="user-len-zero"),
        ],
    )
    def test_an_object_with_a_zero_len_renders_the_empty_branch(self, value) -> None:
        """`ForNode` reads `len` when the object has one — it never calls `list`."""
        assert django_render(FOR, {"p": value}) == "E"
        assert djust_render(FOR, {"p": value}) == "E"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(complex(0), id="complex-zero"),
            pytest.param(BoolFalse(), id="user-bool-false"),
        ],
    )
    def test_an_object_with_NO_len_refuses_on_both_engines(self, value) -> None:
        """No `__len__`, so `ForNode` reaches `list(values)` and raises."""
        assert django_render(FOR, {"p": value}) == "<<REFUSED>>"
        assert djust_render(FOR, {"p": value}) == "<<REFUSED>>"

    @pytest.mark.parametrize(
        "value,type_name",
        [
            pytest.param(complex(0), "complex", id="complex-zero"),
            pytest.param(BoolFalse(), "BoolFalse", id="user-bool-false"),
        ],
    )
    def test_and_the_refusal_names_the_REAL_type(self, value, type_name: str) -> None:
        """CPython's own wording, which the pre-fix `Value::String` could not give.

        Before this the value was a `str`, so the message would have said
        `'str' object is not iterable` — a statement false of every `str`,
        which is what a self-contradicting answer looks like from outside.
        """
        with pytest.raises(Exception) as caught:  # noqa: PT011
            _rust.render_template(FOR, {"p": value})
        assert f"'{type_name}' object is not iterable" in str(caught.value)

    def test_a_datetime_still_refuses_where_django_refuses(self) -> None:
        """The carrier's OTHER members must not have moved.

        `timedelta(0)` is falsy AND an `Encoded` already, so it is the value
        most likely to have been swept into the new arm. It has no `__len__`,
        so `sized_empty` is false and `{% for %}` still raises on both.
        """
        value = datetime.timedelta(0)
        assert django_render(FOR, {"p": value}) == "<<REFUSED>>"
        assert djust_render(FOR, {"p": value}) == "<<REFUSED>>"
        assert djust_render(IF, {"p": value}) == "F"  # #2458, unchanged


class TestEveryOtherAxisOfTheClosedShapes:
    """Truthiness was the cited axis; these are the ones it drags along."""

    @pytest.mark.parametrize("value", CLOSED)
    def test_the_bare_display_is_unchanged_and_agrees_with_django(self, value) -> None:
        """`{{ p }}` rendered `str(o)` before and renders `str(o)` now.

        This is the axis a new carrier is most likely to break, since the
        display moves from `Value::String` into `Encoded::display`.
        """
        assert djust_render(BARE, {"p": value}) == django_render(BARE, {"p": value}), value

    @pytest.mark.parametrize("value", CLOSED)
    def test_length_agrees_with_django(self, value) -> None:
        """It counted the REPR's characters — `{{ set()|length }}` was 5."""
        assert djust_render(LENGTH, {"p": value}) == django_render(LENGTH, {"p": value}), value

    @pytest.mark.parametrize("value", CLOSED)
    def test_json_script_is_byte_identical_to_the_pre_fix_output(self, value) -> None:
        """The one axis that deliberately does NOT move.

        `Encoded::json` is `str(o)` for these, which is exactly what the
        `Value::String` path wrote. Django REFUSES `json_script` over a `set`
        (`Object of type set is not JSON serializable`); that divergence is
        #2429's declined refusal direction, unchanged here rather than grown.
        """
        rendered = djust_render('{{ p|json_script:"x" }}', {"p": value})
        assert rendered != "<<REFUSED>>"
        # The payload is the JSON string `str(o)`, which is byte-for-byte what
        # the `Value::String` path wrote. Parsed rather than substring-matched,
        # so the assertion cannot pass on a payload of the wrong SHAPE.
        payload = rendered.split(">", 1)[1].rsplit("<", 1)[0]
        assert json.loads(payload) == str(value), (payload, value)


class TestTheTwoIterabilityQuestionsAreTwoQuestions:
    """`{% for %}` reads `__len__`; the filters call `iter()`. Django too.

    The first version of this fix carried ONE bit for both and was caught by
    running the axis rather than by inspection: `{{ LenZero()|safeseq }}`
    rendered `[]` where Django raises, and `{{ LenZero()|join:"," }}` rendered
    `""` where Django returns the value. `Encoded` now carries `sized_empty`
    (`len(o) == 0`) and `iterable` (`iter(o)` succeeds) separately, and
    `python_len` / `iter_values` read one each.

    Django's own asymmetry, verbatim:

    * `ForNode.render` — `if not hasattr(values, "__len__"): values = list(values)`
    * `safeseq` — `[mark_safe(obj) for obj in value]`, a comprehension
    """

    #: `set()` and friends are `sized_empty` AND `iterable`; `LenZero()` is
    #: `sized_empty` and NOT iterable. That one row is what makes the pair of
    #: bits distinguishable from a single bit.
    ITERABLE_EMPTIES = [
        pytest.param(set(), id="set"),
        pytest.param(frozenset(), id="frozenset"),
        pytest.param({}.keys(), id="dict_keys"),
    ]

    @pytest.mark.parametrize("value", ITERABLE_EMPTIES)
    @pytest.mark.parametrize(
        "source",
        ['{{ p|join:"," }}', "{{ p|safeseq }}", "{{ p|escapeseq }}", "{{ p|unordered_list }}"],
    )
    def test_an_iterable_empty_agrees_on_every_iterating_filter(self, source, value) -> None:
        assert djust_render(source, {"p": value}) == django_render(source, {"p": value})

    def test_a_len_only_class_renders_the_empty_branch_AND_refuses_the_filters(self) -> None:
        """The row that needs two bits. Both halves, against live Django."""
        value = LenZero()
        # `{% for %}` — `__len__` is 0, so Django renders the empty block.
        assert django_render(FOR, {"p": value}) == "E"
        assert djust_render(FOR, {"p": value}) == "E"
        # `|length` — `len(o)` is 0 on both.
        assert django_render(LENGTH, {"p": value}) == djust_render(LENGTH, {"p": value}) == "0"
        # …and the comprehension filters call `iter()`, which raises.
        for source in ("{{ p|safeseq }}", "{{ p|escapeseq }}", "{{ p|unordered_list }}"):
            assert django_render(source, {"p": value}) == "<<REFUSED>>", source
            assert djust_render(source, {"p": value}) == "<<REFUSED>>", source
        # `join` is the one whose Django body CATCHES that TypeError and
        # returns the value — so it renders, and renders the same text.
        joined = '{{ p|join:"," }}'
        assert djust_render(joined, {"p": value}) == django_render(joined, {"p": value})
        assert djust_render(joined, {"p": value}) != "<<REFUSED>>"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(set(), id="set"),
            pytest.param(frozenset(), id="frozenset"),
            pytest.param(LenZero(), id="user-len-zero"),
        ],
    )
    def test_the_unpack_ARITY_message_counts_pythons_len(self, value) -> None:
        """`python_len`'s arm, reached where `|length` cannot distinguish it.

        `{{ p|length }}` answers 0 for these whether or not `python_len` names
        the carrier, because `length`'s own fallback is `unwrap_or(0)` — so a
        mutation dropping that arm is a semantic NO-OP there, which is the
        gate-off failure mode the v1.1.1-2 canon names (a valid mutation that
        computes the identical answer for every value under test).

        `{% for a, b in … %}` is where the two differ: `ForNode` reports the
        item's LENGTH in its refusal, and `except TypeError: len_item = 1` is
        the fallback. Django says `got 0.`; without the arm djust would say
        `got 1.` — the same refusal, the wrong number.
        """
        source = "{% for a, b in p %}[{{ a }}{{ b }}]{% endfor %}"
        assert django_render(source, {"p": [value]}) == "<<REFUSED>>"
        with pytest.raises(Exception) as caught:  # noqa: PT011
            _rust.render_template(source, {"p": [value]})
        assert "Need 2 values to unpack in for loop; got 0. " in str(caught.value)

    def test_the_refusal_names_the_type_for_a_filter_too(self) -> None:
        with pytest.raises(Exception) as caught:  # noqa: PT011
            _rust.render_template("{{ p|safeseq }}", {"p": LenZero()})
        assert "'LenZero' object is not iterable" in str(caught.value)


class TestWhatThisDeliberatelyDoesNOTClose:
    """Asserted in the DIVERGING direction, so closing one reddens this."""

    def test_a_falsy_object_WITH_attributes_was_still_truthy_and_is_now_CLOSED(
        self,
    ) -> None:
        """Filed as #2478 and closed there — kept as the CLOSING case.

        The `__dict__` bulk-dump arm, not the `str()` fallback: an object
        carrying attributes became a NON-EMPTY `Value::Object`, whose
        truthiness is the mapping rule. This test was written in the DIVERGING
        direction and asserted the reason it could not be closed HERE — routing
        the object through the `Encoded` carrier would have taken `{{ obj.a }}`
        with it, because a `Value::Encoded` had no attributes.

        #2481 gave it some. #2478 then moved the object onto that carrier and
        the objection is answered rather than worked around: the divergence is
        gone AND `{{ p.a }}` still resolves — the second assertion is the whole
        point of keeping this test rather than deleting it.

        The issue's own suggested remedy, a truthiness override on
        `Value::Object`, would have closed the first assertion and NOT
        `{{ p|length }}` / `{% for %}` / `{{ p }}`, which read the MAPPING and
        not its truthiness. See `python/tests/test_falsy_with_attributes_2478.py`.
        """
        value = LenZeroWithAttrs()
        assert bool(value) is False
        assert django_render(IF, {"p": value}) == "F"
        assert djust_render(IF, {"p": value}) == "F"
        # The reason this could not be closed at #2466, now the thing that
        # proves the fix did not pay for it.
        assert djust_render("{{ p.a }}", {"p": value}) == "1"
        assert django_render("{{ p.a }}", {"p": value}) == "1"

    def test_a_falsy_object_with_a_NONZERO_len_is_still_truthy(self) -> None:
        """`__bool__` False and `__len__` 5 — declined, not guessed.

        Django's `for` renders its five items. This carrier cannot produce them
        without RUNNING the object, so the value keeps its previous
        `Value::String` path unchanged rather than being claimed and answered
        wrong. Nothing becomes stricter than Django, which is the property that
        matters.
        """

        class FalsyButFull:
            def __bool__(self) -> bool:
                return False

            def __len__(self) -> int:
                return 5

        value = FalsyButFull()
        assert bool(value) is False
        assert django_render(IF, {"p": value}) == "F"
        assert djust_render(IF, {"p": value}) == "T"

    def test_a_falsy_object_that_is_ITERABLE_with_no_len_is_still_truthy(self) -> None:
        """`__bool__` False, `__iter__` yielding items, no `__len__` — declined.

        Django's `ForNode` calls `list(values)` for it and renders the items.
        This carrier cannot produce them without RUNNING the object, and
        running an arbitrary iterable at the CONVERSION would consume a
        generator — so the value keeps its previous `Value::String` path.

        The decline is the reason this fix moves nothing into the
        `djust REFUSES & Django RENDERS` column: claiming it would have made
        `{% for %}` refuse where Django renders two items.
        """

        class FalsyIterable:
            def __bool__(self) -> bool:
                return False

            def __iter__(self):
                return iter(["a", "b"])

        value = FalsyIterable()
        assert bool(value) is False
        assert django_render(IF, {"p": value}) == "F"
        assert djust_render(IF, {"p": value}) == "T"
        # …and the property that matters: djust did NOT become stricter.
        assert django_render(FOR, {"p": value}) == "[a][b]"
        assert djust_render(FOR, {"p": value}) != "<<REFUSED>>"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({1}, id="set-nonempty"),
            pytest.param(Truthy(), id="user-plain"),
            pytest.param(iter([]), id="list_iterator"),
        ],
    )
    def test_a_TRUTHY_no_variant_object_still_claims_to_be_a_str(self, value) -> None:
        """`|length` counts repr characters and `{% for %}` iterates them.

        Not a truthiness defect — `{% if %}` agrees for all three. Closing it
        needs an ENUMERATION, and enumerating an arbitrary object means calling
        `list(o)` at the conversion: that consumes a generator and hangs on
        `itertools.count()`. A safe-to-enumerate decision per type is a
        different fix.
        """
        assert djust_render(IF, {"p": value}) == django_render(IF, {"p": value})
        assert djust_render(LENGTH, {"p": value}) != django_render(LENGTH, {"p": value})


class TestTheFalsinessRuleStillHasONEDefinition:
    """#2464 landed the rule "in one definition"; this must not add a second.

    Both assertions are EQUALITIES over an extracted set rather than floors. A
    floor cannot see an arm being REMOVED, and removal is the direction this
    class fails in — #2448 added `Value::Encoded` and neither iterability probe
    named it for two releases, which is the gap `falsy_opaque` had to close.
    """

    def _production(self, path: pathlib.Path) -> str:
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

    def test_there_is_exactly_one_is_truthy_definition(self) -> None:
        source = self._production(CORE_RS)
        assert source.count("pub fn is_truthy(&self) -> bool {") == 1

    def test_falsy_opaque_reads_pythons_bool_and_does_not_re_derive_it(self) -> None:
        """The bit is asked of the object, not computed from the display.

        The derivation this rejects is the one #2458 rejected one level down:
        `display.is_empty()` cannot see a `__bool__` override, and
        `"set()" == display` is a string comparison answering a truthiness
        question.
        """
        source = self._production(CORE_RS)
        start = source.index("pub fn falsy_opaque(")
        body = source[start : source.index("\n}\n", start)]
        assert "ob.is_truthy()" in body, body
        assert "truthy: false," in body, body
        for never in ("display.is_empty()", 'display == "', "type_name =="):
            assert never not in body, (never, body)

    def test_both_iterability_probes_name_the_carrier(self) -> None:
        """`iter_values` and `python_len` must move together (#1646).

        They are the two halves of one question and #2387's own pin says the
        `{% for a, b in %}` unpack arm depends on them agreeing. An arm added
        to one alone is the drift this reddens.
        """
        source = self._production(FILTERS_RS)
        for probe, bit in (
            ("pub fn iter_values(", "iterable"),
            ("pub fn python_len(", "sized_empty"),
        ):
            start = source.index(probe)
            body = source[start : source.index("\n}\n", start)]
            assert f"Value::Encoded(e) if e.{bit}" in body, (probe, body)
        # …and they must read DIFFERENT bits, which is the whole point: one
        # bit for both was the first version of this fix and it made
        # `{{ LenZero()|safeseq }}` render where Django raises.
        iter_body = source[
            source.index("pub fn iter_values(") : source.index(
                "\n}\n", source.index("pub fn iter_values(")
            )
        ]
        assert "e.sized_empty" not in iter_body, iter_body

    def test_the_for_arm_names_it_too_and_the_rust_pin_exists(self) -> None:
        """The THIRD implementation of the same question, and its pin.

        `Node::For` does not call `iter_values`; it normalises shapes itself.
        The equivalence between the two is asserted in Rust, over every
        variant, in both directions — this only checks the pin has not been
        deleted, because a Python test cannot see a Rust test being removed.
        """
        source = self._production(RENDERER_RS)
        assert "Value::Encoded(ref e) if e.sized_empty" in source
        assert "fn for_iterability_agrees_with_iter_values()" in RENDERER_RS.read_text(
            encoding="utf-8"
        )

    def test_the_carrier_is_reused_and_no_new_variant_was_added(self) -> None:
        """The `Value` enum is the same size it was.

        A new variant would be a second carrier for one question, and would
        have to be classified at every wildcard `match` arm in the workspace.
        The count is an equality so a DELETED variant reddens it as well.
        """
        source = self._production(CORE_RS)
        start = source.index("pub enum Value {")
        body = source[start : source.index("\n}\n", start)]
        variants = [
            line.strip().rstrip("(,{").split("(")[0].strip()
            for line in body.splitlines()
            if line.startswith("    ")
            and not line.strip().startswith("//")
            and not line.strip().startswith("///")
            and line.strip()
            and line.strip()[0].isupper()
        ]
        assert sorted(variants) == sorted(
            [
                "Missing",
                "None",
                "Bool",
                "Integer",
                "Float",
                "String",
                "List",
                "Tuple",
                "Object",
                "DictView",
                "Decimal",
                "BigInt",
                "Encoded",
            ]
        ), variants
