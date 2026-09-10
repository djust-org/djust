"""`json_script` stays PERMISSIVE where `json.dumps` refuses — decided (#2429).

#2429 asks one question about two positions: `json.dumps` raises `TypeError`
for an unserialisable dict KEY and for an unserialisable VALUE, djust emits a
Python `str()` for both, and #2425 deliberately left the refusal half open
because closing it in the key position alone would make the two positions
disagree.

**The decision is to stay permissive in both, and this file is the record.**
Not a preference — three measurements, each kept here as a test so the next
reader answers the question by running it rather than by re-reading this
paragraph.

Measurement 1 — the divergent set, re-derived
----------------------------------------------
21 key-position types and 26 value-position types run through live Django and
live djust (:class:`TestTheDivergentSetReDerived`). It is **wider and shaped
differently** than #2429's table:

* the issue's `{"a": b"k"}` row says djust emits `{"a": "b'k'"}`. When this
  file was written it emitted ``{"a": [107]}`` — a JSON **array of byte
  values**, because PyO3 extracted `bytes` as a sequence long before any
  `str()` fallback. #2704 moved every non-`list` sequence onto the carrier, so
  the issue's row is now the true one and this correction has expired;
* `range(2)` emits ``{"a": "range(0, 2)"}`` for the same reason — it was
  ``{"a": [0, 1]}`` before #2704 — and a generator emits its repr, and is
  CONSUMED on the way, so the value is gone afterwards;
* an object carrying a populated ``__dict__`` emits its ``str()`` like any
  other object (``{"a": "WITHDICT"}``). Until ADR-027 it emitted a nested JSON
  **object** built from the instance dict; that arm was deleted with the
  kill-switch in Step 5 (#2628);
* the asymmetry the issue notes for `date` is **seven types wide**, not one:
  `tuple` / `Decimal` / `date` / `datetime` / `time` / `timedelta` / `UUID` are
  refused by Django as KEYS and accepted by it as VALUES, because
  `DjangoJSONEncoder.default` never sees a key — CPython coerces keys before
  the encoder hook. Django is *itself* inconsistent between the two positions.

Measurement 2 — djust DOES raise on data shape, so "no precedent" is false
--------------------------------------------------------------------------
The tempting argument for staying permissive is that djust never raises
because of a context VALUE, only because of a template-source error (arity, a
bad filter argument). Running it says otherwise
(:class:`TestDjustDoesRaiseWhereItCanSeeTheShape`): `{% for x in p %}` over an
`int` raises ``'int' object is not iterable`` here exactly as it does in
Django (#2382), and a `__str__` that raises propagates. So a data-driven raise
is an established djust behaviour, and this decision cannot lean on its
absence.

Measurement 3 — the VALUE position is not decidable, which is what decides it
-----------------------------------------------------------------------------
:class:`TestTheValuePositionCannotSeeTheTypeAtAll` is the load-bearing one.
For every value Django refuses, djust's output is **byte-identical** to its
output for an ordinary serialisable stand-in::

    {"a": Obj()}            and {"a": "OBJ"}              -> {"a": "OBJ"}
    {"a": WithDict()}       and {"a": "WITHDICT"}         -> {"a": "WITHDICT"}
    {"a": frozenset({1})}   and {"a": "frozenset({1})"}   -> {"a": "frozenset({1})"}
    {"a": b"k"}             and {"a": "b'k'"}             -> {"a": "b'k'"}

The PyO3 boundary (`FromPyObject for Value`) converts an arbitrary object to an
`Encoded` carrying a live handle, whose display is its `str()` —
*deliberately*, because that is what makes `{{ obj.name }}` work at all. By the time any filter runs, the Python type Django refuses on no longer
exists. A value-position refusal would therefore have to refuse the
**stand-in** too: an ordinary dict of ordinary strings.

The same object even arrives as two different `Value`s depending on route
(:meth:`TestTheValuePositionCannotSeeTheTypeAtAll.test_one_object_two_routes_two_values`):
`{{ p.keys|json_script:"i" }}` reaches the filter as a `DictView` and renders
empty, while `p = d.keys()` bound in Python reaches it as a `String` and emits
the repr. Route-dependence is the same erasure seen from the other side.

Why that settles it
-------------------
#2429's own scope line requires one decision applied to BOTH positions. The
key position is decidable — `ObjectKey` keeps the type (#2339) — and the value
position is not, short of a new `Value` variant threaded through the ~460
`Value::String` sites in `crates/**/src` and every filter, renderer and
serializer that matches on `Value`. That is an architectural change to the boundary that
exists so ordinary attribute access works, undertaken so that one filter can
turn a rendering page into a 500.

And a 500 is what it buys. A template that runs under Django's engine has
never carried these values — Django would have raised — so refusing helps
nobody porting *to* djust and breaks exactly the djust-native pages written
against the behaviour that has shipped for every release so far. The output is
escaped in both positions (`json_string_body`, pinned since #2241), so this is
a correctness divergence and not an injection.

So the two positions end up consistent the only way they can: permissive in
both, stated rather than left as a silence.

What this does NOT decide
-------------------------
Not "djust never refuses". `{% for %}` still raises. Not the SPELLING of a
value both engines emit — `datetime` and `timedelta` disagree there
(`"2020-01-01 03:04:05"` vs Django's isoformat, `"0:01:30"` vs `P0DT00H01M30S`)
and that is a live defect in the emitting direction, filed as **#2448** per
CLAUDE.md #1079. Not `unordered_list` or `first`, which are over-permissive in
the same direction on their own filters and — unlike this — are DECIDABLE,
because they turn on a `Value`'s shape rather than on an erased Python type;
filed as **#2449**.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import uuid

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover — import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        INSTALLED_APPS=[],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

TPL = '{{ p|json_script:"d" }}'


class _E(enum.Enum):
    A = "a"


class _Obj:
    """No instance attributes."""

    def __str__(self) -> str:
        return "OBJ"


class _WithDict:
    """A populated `__dict__`. Until ADR-027 the boundary turned it into a
    mapping; the object now crosses as `Encoded` and the boundary sees its
    `str()` (#2539 movement 3; the escape hatch that kept the old arm was
    deleted in Step 5, #2628).

    ``__str__`` is defined for the same reason `_Obj`'s is: without it the
    answer carries the instance ADDRESS, and no row of a recorded table can
    compare against an address.
    """

    def __init__(self) -> None:
        self.name = "n"

    def __str__(self) -> str:
        return "WITHDICT"

    def __hash__(self) -> int:  # hashable, so it can also be a KEY
        return 1


def _django_refuses(p: object) -> bool:
    try:
        DjangoTemplate(TPL).render(DjangoContext({"p": p}))
    except TypeError:
        return True
    return False


def _djust(p: object) -> str:
    return _rust.render_template(TPL, {"p": p})


def _body(rendered: str) -> str:
    return rendered.split(">", 1)[1].rsplit("<", 1)[0]


#: Every candidate, hashable so each can occupy either position.
_BOTH_POSITIONS: tuple[tuple[str, object], ...] = (
    ("tuple", (1, "t")),
    ("frozenset", frozenset({1})),
    ("bytes", b"k"),
    ("Decimal", decimal.Decimal("1.5")),
    ("date", datetime.date(2020, 1, 1)),
    ("datetime", datetime.datetime(2020, 1, 1, 3, 4)),
    ("time", datetime.time(3, 4)),
    ("timedelta", datetime.timedelta(seconds=90)),
    ("UUID", uuid.UUID(int=1)),
    ("complex", complex(1, 2)),
    ("Enum", _E.A),
    ("object", _Obj()),
    ("object-with-dict", _WithDict()),
    ("bigint", 10**30),
    ("float-nan", float("nan")),
    ("float-inf", float("inf")),
    ("bool", True),
    ("None", None),
    ("int", 3),
    ("str", "s"),
    ("float", 1.5),
)

#: Unhashable, so value position only.
_VALUE_ONLY: tuple[tuple[str, object], ...] = (
    ("set", {1}),
    ("list", [1]),
    ("dict", {"a": 1}),
    ("range", range(2)),
)

#: Measured, not transcribed: Django refuses these as a KEY, djust emits.
DIVERGENT_KEYS = frozenset(
    {
        "tuple",
        "frozenset",
        "bytes",
        "Decimal",
        "date",
        "datetime",
        "time",
        "timedelta",
        "UUID",
        "complex",
        "Enum",
        "object",
        "object-with-dict",
    }
)

#: Measured: Django refuses these as a VALUE, djust emits.
DIVERGENT_VALUES = frozenset(
    {
        "frozenset",
        "bytes",
        "complex",
        "Enum",
        "object",
        "object-with-dict",
        "set",
        "range",
    }
)


# ---------------------------------------------------------------------------
# Measurement 1 — the divergent set
# ---------------------------------------------------------------------------


class TestTheDivergentSetReDerived:
    """The SET, pinned — so the decision cannot be reversed silently.

    Not a floor: a type that stops diverging (djust grew a refusal) and a type
    that starts diverging (a new emitting path) each turn a row red. That is
    what makes this a decision record rather than a comment.
    """

    def test_the_divergent_KEY_set_is_exactly_this(self) -> None:
        got = {
            name
            for name, obj in _BOTH_POSITIONS
            if _django_refuses({obj: "v"}) and _djust({obj: "v"}).startswith("<script")
        }
        assert got == DIVERGENT_KEYS, (
            "the key-position divergence moved. If djust now REFUSES one of "
            f"these, #2429's decision was reversed: missing={DIVERGENT_KEYS - got}, "
            f"new={got - DIVERGENT_KEYS}"
        )

    def test_the_divergent_VALUE_set_is_exactly_this(self) -> None:
        got = {
            name
            for name, obj in _BOTH_POSITIONS + _VALUE_ONLY
            if _django_refuses({"a": obj}) and _djust({"a": obj}).startswith("<script")
        }
        assert got == DIVERGENT_VALUES, (
            "the value-position divergence moved: "
            f"missing={DIVERGENT_VALUES - got}, new={got - DIVERGENT_VALUES}"
        )

    def test_the_two_positions_are_consistently_permissive(self) -> None:
        """#2429's actual requirement: one decision, both positions.

        Every type Django refuses in EITHER position is emitted by djust in
        EVERY position it can occupy. A key-only refusal — the shape #2425
        declined — turns this red.
        """
        for name, obj in _BOTH_POSITIONS:
            if name not in DIVERGENT_KEYS and name not in DIVERGENT_VALUES:
                continue
            assert _djust({obj: "v"}).startswith("<script"), f"{name} refused as a KEY"
            assert _djust({"a": obj}).startswith("<script"), f"{name} refused as a VALUE"

    @pytest.mark.parametrize(
        "name",
        ["tuple", "Decimal", "date", "datetime", "time", "timedelta", "UUID"],
    )
    def test_django_itself_disagrees_between_the_positions(self, name: str) -> None:
        """The issue notes this for `date`; it is seven types wide.

        `DjangoJSONEncoder.default` serialises these as VALUES and never sees
        them as KEYS, because CPython coerces keys before the encoder hook. So
        "match Django" does not mean "treat the two positions alike" — Django
        does not.
        """
        obj = dict(_BOTH_POSITIONS)[name]
        assert _django_refuses({obj: "v"}), f"{name} is no longer refused as a Django KEY"
        assert not _django_refuses({"a": obj}), f"{name} is now refused as a Django VALUE"

    def test_the_issues_bytes_row_became_right_when_the_mechanism_moved(self) -> None:
        """A premise correction, and then the correction expiring — both
        halves kept, because the pair IS the finding (CLAUDE.md, v1.1.1-2
        retro; the chain convention, #2142).

        #2429 tabulated `{"a": b"k"}` as emitting `{"a": "b'k'"}`. That was
        WRONG when #2425 measured it: PyO3 extracted `bytes` as a sequence
        long before any `str()` fallback, so the answer was a JSON array of
        byte values. #2704 declines every non-`list` sequence at the
        conversion — `{{ v }}` over a `bytes` is `b'k'` in Django and was
        `[107]` here — so the boundary now hands `json_script` the carrier's
        display and the issue's original row is the true one.

        Arriving at the issue's answer by a different mechanism is not the
        same as the issue having been right about the mechanism, which is why
        this row keeps both sentences rather than being deleted.
        """
        assert _body(_djust({"a": b"k"})) == '{"a": "b\'k\'"}'

    def test_an_object_with_attributes_emits_the_objects_string(self) -> None:
        """Not sampled by the issue, and the sharper half of the value story.

        `json_script` writes `str(o)` — not a JSON object built from the
        instance dict, which is what the deleted `__dict__` bulk-dump arm
        wrote before ADR-027 (#2539 movement 3; #2628). Worth pinning by name
        in THIS file rather than only in the ADR-027 net, because
        `json_script` writes into the PAGE.

        What this row does NOT say is which direction that is. `_WithDict`
        has a `__str__` returning a constant, so it can only ever look like a
        narrowing — which is exactly why it cannot be the only pin. See
        `TestTheDirectionIsShapeDependent` below for the shapes that widen;
        the honest summary is that the change swaps one disclosure surface for
        another, and which is larger depends on the object.
        """
        assert _body(_djust({"a": _WithDict()})) == '{"a": "WITHDICT"}'


# ---------------------------------------------------------------------------
# Measurement 2 — the precedent, which points the OTHER way
# ---------------------------------------------------------------------------


class TestDjustDoesRaiseWhereItCanSeeTheShape:
    """ "djust never raises on data" would be a convenient premise. It is false.

    Kept because the decision has to survive its strongest counter-argument
    rather than rest on a claim nobody ran. Both rows are behaviour djust
    chose deliberately (#2382), and both stay true under this decision — which
    is what makes it a scoped decision about an undecidable position rather
    than a blanket "never refuse".
    """

    def test_a_for_loop_over_a_non_iterable_raises_on_both_engines(self) -> None:
        src = "{% for x in p %}{{ x }}{% endfor %}"
        with pytest.raises(TypeError, match="not iterable"):
            DjangoTemplate(src).render(DjangoContext({"p": 3}))
        with pytest.raises(RuntimeError, match="object is not iterable"):
            _rust.render_template(src, {"p": 3})

    def test_a_raising_dunder_str_propagates_on_both_engines(self) -> None:
        class Boom:
            def __str__(self) -> str:
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            DjangoTemplate("{{ p }}").render(DjangoContext({"p": Boom()}))
        with pytest.raises(ValueError, match="boom"):
            _rust.render_template("{{ p }}", {"p": Boom()})


# ---------------------------------------------------------------------------
# Measurement 3 — why the value position cannot be closed
# ---------------------------------------------------------------------------


class TestTheValuePositionCannotSeeTheTypeAtAll:
    """The reason, as a test rather than as prose.

    If a future change makes the boundary carry the Python type, these rows go
    red — and #2429 should be reopened, because the fact this decision rests on
    will have stopped being true.
    """

    @pytest.mark.parametrize(
        "refused,stand_in",
        [
            ({"a": _Obj()}, {"a": "OBJ"}),
            # The object crosses as `Encoded` and the boundary sees `str(o)`,
            # so the stand-in that is byte-identical to it is a STRING rather
            # than the instance dict (#2539 movement 3; the dict arm was
            # deleted with the kill-switch, #2628).
            ({"a": _WithDict()}, {"a": "WITHDICT"}),
            ({"a": frozenset({1})}, {"a": "frozenset({1})"}),
            ({"a": {1}}, {"a": "{1}"}),
            ({"a": complex(1, 2)}, {"a": "(1+2j)"}),
            # Both stand-ins were a JSON ARRAY of the enumerated items until
            # #2704; a non-`list` sequence now crosses as a carrier and the
            # boundary sees `str(o)`, so the byte-identical stand-in is a
            # STRING. WHICH stand-in it is changes; that there IS one — the
            # property this class exists to measure — does not.
            ({"a": b"k"}, {"a": "b'k'"}),
            ({"a": range(2)}, {"a": "range(0, 2)"}),
        ],
        ids=["object", "object-with-dict", "frozenset", "set", "complex", "bytes", "range"],
    )
    def test_a_refused_value_is_byte_identical_to_a_serialisable_stand_in(
        self, refused: dict, stand_in: dict
    ) -> None:
        assert _django_refuses(refused), "this row proves nothing unless Django refuses it"
        assert not _django_refuses(stand_in), "the stand-in must be one Django accepts"
        assert _djust(refused) == _djust(stand_in), (
            "the boundary now distinguishes them — a value-position refusal "
            "has become possible and #2429 should be reopened"
        )

    def test_one_object_two_routes_two_values(self) -> None:
        """Route-dependence: the same `dict_keys`, two djust answers.

        Reached by attribute inside the template it is a `Value::DictView` and
        the filter renders nothing (#2340); bound in Python it is a
        `Value::String` and the filter emits the repr. Django raises for both.
        Pinned because `filters.rs`'s own comment claimed the arm "refuses the
        whole filter before reaching here", which is true only of the first
        route.
        """
        d = {"a": 1}
        assert _django_refuses(d.keys())
        assert _rust.render_template('{{ p.keys|json_script:"i" }}', {"p": d}) == ""
        emitted = _rust.render_template('{{ p|json_script:"i" }}', {"p": d.keys()})
        assert _body(emitted) == "\"dict_keys(['a'])\""


# ---------------------------------------------------------------------------
# Measurement 4 — the DIRECTION of the movement-3 change, falsified
# ---------------------------------------------------------------------------
#
# PR #2620 claimed, in four places including ADR-027's erratum and the
# changelog fragment that ships to users, that the flip is a *reduction* in
# what an unreviewed `{{ o }}` / `{{ o|json_script }}` puts on the page — a
# security improvement. The Stage 11 review falsified that. It is not
# monotone: the pre-flip `__dict__` dump FILTERED underscore-prefixed
# attributes, and `str(o)` filters nothing.
#
# The claim had exactly one pin, `_WithDict`, whose `__str__` this PR ADDED
# and which returns a constant — the one shape that structurally cannot
# exhibit the widening (#1867: the citation is real, the invariant it asserts
# is false, and the fixture was built so it could not notice). These are the
# falsifying cases, so the next reader finds the direction measured rather
# than hoped. The OFF-path (`__dict__` dump) halves of these rows went with
# the kill-switch (#2628); what remains pins what `str(o)` puts on the page.


@dataclasses.dataclass
class _DataclassCreds:
    """Python's own `@dataclass` repr prints EVERY field, `_private` included.

    `attrs`, `pydantic` and any hand-written debug `__repr__` behave the same
    way, so this is not an exotic shape — it is one of the most common ways a
    presenter object is spelled.
    """

    user: str
    password: str
    _session_token: str
    api_key: str


class _StrNamesPrivateState:
    """A `__str__` that interpolates an attribute the old dump filtered."""

    def __init__(self) -> None:
        self.label = "innocuous"
        self._secret = "SSN-123-45-6789"

    def __str__(self) -> str:
        return f"{type(self).__name__}(label={self.label}, secret={self._secret})"


class _AttributesButNoStr:
    """Attributes and no `__str__` — the shape where the flip DID narrow."""

    def __init__(self) -> None:
        self.password = "hunter2"
        self.username = "u"


class TestTheDirectionIsShapeDependent:
    """The movement-3 change was a change of SHAPE, not a narrowing.

    Each row pins what `str(o)` emits for a shape the deleted `__dict__` dump
    handled differently: the dump FILTERED underscore-prefixed attributes,
    `str(o)` filters nothing (wider for a dataclass or a leaky `__str__`),
    and an object with attributes but no `__str__` emits only its default
    repr (narrower). The escape-hatch halves of these rows were deleted with
    the flag (#2628); the surviving assertions are the ones about the page.
    """

    def test_a_dataclass_emits_MORE_under_the_default(self) -> None:
        """WIDER. The deleted dump filtered `_session_token`; the dataclass repr
        does not."""
        obj = _DataclassCreds("u", "pw", "TOK-abc123", "AK-9")
        default = _body(_djust({"a": obj}))
        assert "_session_token" in default, default
        assert "TOK-abc123" in default, (
            "the private dataclass field is no longer emitted — if the sink learned "
            "to filter, ADR-027's erratum item 4 can be restated as a narrowing after "
            f"all: {default!r}"
        )

    def test_a_leaky_str_emits_MORE_under_the_default(self) -> None:
        """WIDER. The deleted dump saw only `label`; `str(o)` names `_secret`."""
        obj = _StrNamesPrivateState()
        default = _body(_djust({"a": obj}))
        assert "SSN-123-45-6789" in default, default

    def test_the_same_widening_reaches_a_BARE_variable_not_only_json_script(self) -> None:
        """`{{ o }}` moves with `{{ o|json_script }}`, and the erratum named
        only the filter. The bare spelling is the more common one."""
        obj = _StrNamesPrivateState()
        default = _rust.render_template("{{ p }}", {"p": obj})
        assert "SSN-123-45-6789" in default, default

    def test_an_object_with_no_str_emits_LESS_under_the_default(self) -> None:
        """NARROWER — the direction the PR claimed for every shape, true for
        this one (the deleted dump emitted `password`). Kept beside the two
        above so the row that supports the original wording and the rows that
        refute it are read together."""
        obj = _AttributesButNoStr()
        default = _body(_djust({"a": obj}))
        assert "hunter2" not in default, default
        assert "_AttributesButNoStr object at" in default, default
