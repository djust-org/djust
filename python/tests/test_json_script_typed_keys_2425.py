"""`json_script` spells a dict KEY the way `json.dumps` spells it (#2425).

The defect
----------
`json.dumps` does NOT call `str()` on a non-`str` dict key. It has its own
five-entry coercion table, and CPython's `c_make_encoder` writes it in this
order: `str` unchanged, then the three JSON literals for `True` / `False` /
`None`, then `float.__repr__`, then `int.__repr__`, then `TypeError`. djust
routed every key through `ObjectKey::to_display_string()` — Python's `str()` —
so two of the five arms were wrong::

    {{ p|json_script:"d" }}     p = {True: "b", None: "c"}

    django  <script id="d" …>{"true": "b", "null": "c"}</script>
    djust   <script id="d" …>{"True": "b", "None": "c"}</script>

What the re-derivation added to the issue's table
-------------------------------------------------
#2425 tabulated `bool` and `None` and wrote *"the `int` and `float` arms agree
by coincidence, because `str(0)` and `str(1.5)` are already the JSON forms"*.
That is true of the two floats it sampled and false in **two** further bands::

    p = {float("inf"): "v"}     django {"Infinity": "v"}   djust {"inf": "v"}
    p = {float("nan"): "v"}     django {"NaN": "v"}        djust {"nan": "v"}
    p = {1e16: "v"}             django {"1e+16": "v"}      djust {"10000000000000000": "v"}
    p = {1e-5: "v"}             django {"1e-05": "v"}      djust {"0.00001": "v"}

The last two are the sharper correction, because they say the premise itself
was wrong rather than incomplete: the old key coercion was NOT `str()`. It was
`ObjectKey::to_display_string()`, which is the TEMPLATE display of a float —
`{% for k in d %}{{ k }}{% endfor %}` writes `10000000000000000` — and that
parts company with `float.__repr__` well before infinity. Only the middle band
of small finite values coincided, and that is the band the issue sampled.

Found by running `json.dumps` over EVERY key type rather than inheriting the
issue's list (`TestEveryKeyTypeReDerived` below is that sweep, kept as a test
so the next key-type question is answered by running it), and the exponent band
by the gate-off rather than by the sweep — deleting the `Float` arm failed
`1e16` and `1e-05`, which nothing had predicted.

The corpus scope claim in `test_json_script_ensure_ascii_and_element_id_2413.py`
was not wrong: `scripts/filter-parity-differential.py` carries no non-finite
and no exponent-form float key, so the claim was true of the corpus and silent
about the axis — the curated-table-samples-one-axis shape (CLAUDE.md, v1.1.1-2
retro).

What this does NOT close
------------------------
`json.dumps` REFUSES a `tuple` / `bytes` / `frozenset` / `Decimal` / `date` /
`Enum` / arbitrary-object key with `TypeError`, and djust emits its `str()`.
That half stayed open on purpose, because **djust does not refuse an
unserialisable VALUE either** — refusing in the key position alone would make
the two positions disagree, which is a new inconsistency rather than a fix.
#2429 decided it: djust stays permissive in BOTH positions, because the value
position cannot see the Python type at all once `FromPyObject for Value` has
converted the object. Measured in `TestTheRefusalHalfIsADecidedLimit` here,
recorded in full in `python/tests/test_json_script_refusal_decision_2429.py`.

Every expectation here is LIVE `json.dumps` / LIVE Django, never a
transcription.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import json

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

TPL = '{{ p|json_script:"d" }}'


def both(ctx: dict) -> tuple[str, str]:
    """(django, djust) for one context — each engine gets its own dict."""
    return (
        DjangoTemplate(TPL).render(DjangoContext({"p": dict(ctx)})),
        _rust.render_template(TPL, {"p": dict(ctx)}),
    )


def body_of(script_html: str) -> str:
    start = script_html.index(">") + 1
    return script_html[start : script_html.rindex("</script>")]


class _Obj:
    def __repr__(self) -> str:
        return "OBJ"


class _E(enum.Enum):
    A = 1


class _IE(enum.IntEnum):
    A = 1


class _SE(str, enum.Enum):
    A = "a"


class TestABoolKeyIsTheJSONLiteral:
    """Mechanism 1 — the `Bool` arm of `json_key_body`.

    `str(True)` is `True`; `json.dumps`'s key table writes `true`. Both
    polarities, because `False` takes a different match arm from `True` and a
    fix that only handled the truthy one would pass a `True`-only test.
    """

    @pytest.mark.parametrize("key,want", [(True, "true"), (False, "false")])
    def test_it_matches_django_byte_for_byte(self, key: bool, want: str) -> None:
        dj, du = both({key: "v"})
        assert dj == du, f"django={dj!r} djust={du!r}"
        assert body_of(du) == json.dumps({key: "v"}) == '{"%s": "v"}' % want

    def test_a_nested_bool_key_is_reached_too(self) -> None:
        """The same arm, one level down — `value_to_json` recurses."""
        dj, du = both({"a": {False: 1, True: 2}})
        assert dj == du, f"django={dj!r} djust={du!r}"
        assert body_of(du) == json.dumps({"a": {False: 1, True: 2}})


class TestANoneKeyIsNull:
    """Mechanism 2 — the `None` arm.

    Separate from `Bool` because the two are distinct `ObjectKey` variants and
    a fix could reach one without the other.
    """

    def test_it_matches_django_byte_for_byte(self) -> None:
        dj, du = both({None: "c"})
        assert dj == du, f"django={dj!r} djust={du!r}"
        assert body_of(du) == json.dumps({None: "c"}) == '{"null": "c"}'


class TestAFloatKeyTakesTheFloatSpelling:
    """Mechanism 3 — the `Float` arm, routed through `json_float_body`.

    The axis #2425's table missed, and it turned out to have TWO halves rather
    than the one this class was first written for. Measured — `str()` is on the
    left, `ObjectKey::to_display_string()` (what the key used to take) in the
    middle:

    | key      | `str()`  | `to_display_string()`  | `json.dumps` |
    |----------|----------|------------------------|--------------|
    | `1.5`    | `1.5`    | `1.5`                  | `1.5`        |
    | `1e16`   | `1e+16`  | `10000000000000000`    | `1e+16`      |
    | `1e-5`   | `1e-05`  | `0.00001`              | `1e-05`      |
    | `inf`    | `inf`    | `inf`                  | `Infinity`   |
    | `nan`    | `nan`    | `nan`                  | `NaN`        |

    So the old arm was wrong on the non-finite floats AND on every float whose
    `repr` is in exponent form — `to_display_string` is the TEMPLATE display
    (`{% for k in d %}{{ k }}{% endfor %}` writes the expanded decimal), not
    `float.__repr__`. Only the middle band of small finite values coincided,
    which is the band #2425's table sampled.

    The spelling is #2270's `json_float_body`: `float.__repr__`, plus
    `Infinity` / `-Infinity` / `NaN` — the same three names the float VALUE arm
    emits, knowing `JSON.parse` rejects them, because that is what Django
    writes.
    """

    @pytest.mark.parametrize(
        "key,want",
        [
            (float("inf"), "Infinity"),
            (float("-inf"), "-Infinity"),
            (float("nan"), "NaN"),
        ],
        ids=["inf", "-inf", "nan"],
    )
    def test_a_non_finite_key_is_the_JSON_name(self, key: float, want: str) -> None:
        dj, du = both({key: "v"})
        assert dj == du, f"django={dj!r} djust={du!r}"
        assert body_of(du) == json.dumps({key: "v"}) == '{"%s": "v"}' % want

    @pytest.mark.parametrize(
        "key,want",
        [(1e16, "1e+16"), (1e-5, "1e-05"), (1e20, "1e+20"), (1e-300, "1e-300")],
        ids=["1e16", "1e-5", "1e20", "1e-300"],
    )
    def test_an_exponent_form_key_is_the_repr_not_the_expansion(
        self, key: float, want: str
    ) -> None:
        """The half found by the gate-off, not by the issue.

        Deleting the `Float` arm fails these as well as the non-finite ones,
        which is what showed `to_display_string` and `float.__repr__` part
        company well before infinity.
        """
        dj, du = both({key: "v"})
        assert dj == du, f"django={dj!r} djust={du!r}"
        assert body_of(du) == json.dumps({key: "v"}) == '{"%s": "v"}' % want

    @pytest.mark.parametrize("key", [0.0, -0.0, 1.0, 1.5, -2.25, 1e15])
    def test_a_coinciding_float_key_is_unmoved(self, key: float) -> None:
        """The band that agreed BEFORE the fix must still agree after it.

        `json_float_body` and `to_display_string` produce the same bytes here,
        so these rows cannot distinguish the two arms — they are here to pin
        that swapping the arm did not buy the other two bands by moving this
        one. `1e15` is the last value whose `repr` is still expanded
        (`1000000000000000.0`), so it sits one step below the boundary the
        method above sits above.
        """
        dj, du = both({key: "v"})
        assert dj == du, f"django={dj!r} djust={du!r}"
        assert body_of(du) == json.dumps({key: "v"})


class TestTheCollidingKeysRoundTripAsDjangosDo:
    """`{True: "a", 1: "b"}` has ONE key in Python — before djust ever sees it.

    Worth pinning because the fix makes a `bool` key spell `true` where an
    `int` key spells `1`, which reads like it could newly collide two JSON keys
    that used not to. It cannot: the collision, when there is one, happens in
    CPython's dict and both engines receive the already-collapsed mapping.

    The genuinely interesting rows are the ones where the JSON forms collide
    while the PYTHON keys did not — `{None: "a", "null": "b"}` is two Python
    keys and one JSON key. `json.dumps` emits the duplicate happily (it does no
    key-uniqueness check), so byte-parity is the whole requirement and djust
    must emit the duplicate too rather than de-duplicating.
    """

    @pytest.mark.parametrize(
        "pairs",
        [
            [(True, "a"), (1, "b")],  # one Python key — `True == 1`
            [(1, "a"), (True, "b")],  # …and the reverse insertion order
            [(0, "x"), (False, "y")],  # `False == 0`
            [(1.0, "a"), (1, "b")],  # `1.0 == 1`
            [(None, "a"), ("None", "b")],  # two Python keys, distinct JSON keys
            [("null", "a"), (None, "b")],  # two Python keys, DUPLICATE JSON key
            [(True, "a"), ("true", "b")],  # …and the bool spelling of the same
        ],
        ids=["true-1", "1-true", "0-false", "1.0-1", "None-str", "null-dup", "true-dup"],
    )
    def test_it_matches_django_byte_for_byte(self, pairs: list) -> None:
        # Built from pairs rather than written as a dict literal: half of these
        # ARE a repeated key by Python's own hashing, which is the point, and a
        # literal is a ruff F601.
        ctx = dict(pairs)
        dj, du = both(ctx)
        assert dj == du, f"{pairs!r}\n  django={dj!r}\n  djust ={du!r}"
        assert body_of(du) == json.dumps(ctx)


class TestEveryKeyTypeReDerived:
    """The scope claim, RUN over every key type rather than inherited (#2425).

    The issue's table listed nine key types. This sweeps twenty-five through
    live `json.dumps`, live Django and djust in one pass, and asserts the
    divergent set is EXACTLY the types `json.dumps` refuses — so a new spelling
    divergence cannot hide in a type nobody tabulated, and closing #2429 empties
    the set.

    Three rows exist only because they are the ones a `str`/`int` check written
    from the prose would get wrong: an `IntEnum` and a `str`-Enum ARE accepted
    by `json.dumps` (they are `int` / `str` subclasses, and CPython calls
    `int.__repr__` / passes the `str` through), while a plain `Enum` is not.
    """

    #: (label, key). Every hashable shape a Python dict key can take that this
    #: encoder can reach.
    KEYS = [
        ("str", "a"),
        ("str empty", ""),
        ("int", 0),
        ("int negative", -3),
        ("int oversized", 12345678901234567890),
        ("float", 1.5),
        ("float integral", 1.0),
        ("float exponent-form", 1e16),
        ("float tiny", 1e-5),
        ("float inf", float("inf")),
        ("float -inf", float("-inf")),
        ("float nan", float("nan")),
        ("True", True),
        ("False", False),
        ("None", None),
        ("str subclass", type("S", (str,), {})("s")),
        ("int subclass", type("I", (int,), {})(7)),
        ("IntEnum", _IE.A),
        ("str Enum", _SE.A),
        # …and the ones `json.dumps` refuses.
        ("tuple", (1, "t")),
        ("tuple empty", ()),
        ("frozenset", frozenset({1})),
        ("bytes", b"k"),
        ("object", _Obj()),
        ("Decimal", decimal.Decimal("1.5")),
        ("complex", complex(1, 2)),
        ("date", datetime.date(2020, 1, 1)),
        ("Enum", _E.A),
        ("range", range(2)),
    ]

    @staticmethod
    def _outcome(fn) -> str:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — a raise IS a comparable outcome
            return f"<<{type(exc).__name__}>>"

    def test_the_only_divergent_key_types_are_the_ones_json_dumps_refuses(self) -> None:
        refused, diverging = set(), set()
        for label, key in self.KEYS:
            accepted = self._outcome(lambda k=key: json.dumps({k: "v"})).startswith("{")
            if not accepted:
                refused.add(label)
            dj = self._outcome(
                lambda k=key: DjangoTemplate(TPL).render(DjangoContext({"p": {k: "v"}}))
            )
            du = self._outcome(lambda k=key: _rust.render_template(TPL, {"p": {k: "v"}}))
            if dj != du:
                diverging.add(label)

        assert refused == {
            "tuple",
            "tuple empty",
            "frozenset",
            "bytes",
            "object",
            "Decimal",
            "complex",
            "date",
            "Enum",
            "range",
        }, refused
        assert diverging == refused, (
            f"a key type diverges for a reason other than the #2429 refusal: {diverging ^ refused}"
        )

    def test_every_accepted_key_type_is_byte_identical_to_json_dumps(self) -> None:
        """The positive half, stated as bytes rather than as "they agree".

        `diverging == refused` above would also hold if djust and Django were
        BOTH wrong in the same way. This compares the rendered body to
        `json.dumps` directly, which cannot.
        """
        for label, key in self.KEYS:
            try:
                want = json.dumps({key: "v"})
            except TypeError:
                continue
            _, du = both({key: "v"})
            assert body_of(du) == want, f"{label}: got {body_of(du)!r} want {want!r}"


class TestTheRefusalHalfIsADecidedLimit:
    """djust is more permissive than Django in BOTH positions — decided (#2429).

    Left open by #2425 per CLAUDE.md #1079, and #2429 answered it: djust stays
    permissive in both positions and does NOT refuse. The reason is the second
    method's, taken one step further — the value position cannot see the type
    at all. `FromPyObject for Value` converts an arbitrary object to its
    `__dict__` or its `str()` at the boundary, so by the time this filter runs
    an unserialisable value is byte-identical to an ordinary serialisable one,
    and a refusal would have to refuse the ordinary one too. The key position
    IS decidable (`ObjectKey` keeps the type, #2339), so refusing there alone
    is the disagreement #2425 declined.

    Kept here as a limit rather than deleted: this is where the divergence was
    first written down, and both methods must stay green. The full decision
    record — the re-derived divergent set, the `{% for %}` raise that proves
    djust does refuse on data shape elsewhere, and the byte-identity
    measurement the decision rests on — is
    `python/tests/test_json_script_refusal_decision_2429.py`.
    """

    @pytest.mark.parametrize(
        "key",
        [(1, "t"), frozenset({1}), b"k", decimal.Decimal("1.5"), datetime.date(2020, 1, 1), _E.A],
        ids=["tuple", "frozenset", "bytes", "Decimal", "date", "Enum"],
    )
    def test_an_unserialisable_KEY_is_emitted_where_django_raises(self, key: object) -> None:
        with pytest.raises(TypeError, match="keys must be str, int, float, bool or None"):
            DjangoTemplate(TPL).render(DjangoContext({"p": {key: "v"}}))
        assert _rust.render_template(TPL, {"p": {key: "v"}}).startswith("<script")

    @pytest.mark.parametrize(
        "value",
        [_Obj(), b"k", {1}, frozenset({1}), complex(1, 2), range(2)],
        ids=["object", "bytes", "set", "frozenset", "complex", "range"],
    )
    def test_an_unserialisable_VALUE_is_emitted_too_which_is_why(self, value: object) -> None:
        """The consistency argument, run rather than asserted.

        If this method ever raises, the value position has been made strict —
        the fact #2429's decision rests on has stopped being true, and that
        decision should be revisited rather than the key position quietly
        following.
        """
        with pytest.raises(TypeError, match="is not JSON serializable"):
            DjangoTemplate(TPL).render(DjangoContext({"p": {"a": value}}))
        assert _rust.render_template(TPL, {"p": {"a": value}}).startswith("<script")
