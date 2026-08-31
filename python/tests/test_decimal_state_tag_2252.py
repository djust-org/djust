"""A `Decimal` survives the state round trip as a `Decimal` (#2252).

#2239 gave each of `normalize_django_value`'s three destinations the
representation it needs, and named the third — a round trip back ONTO the view
— as the one it could satisfy neither way. It kept `float` there, on the
grounds that this was "today's behaviour exactly, today's loss exactly".

**Measuring what that costs is what changed the answer.** The issue frames the
residue as precision loss "past ~15 significant digits". It is not. `float` is
wrong for ordinary four-digit money too, in two ways that need no precision
loss at all — and both are measured here, not asserted:

* `Decimal('19.99')` comes back a `float`, so `self.price + Decimal('1')` —
  an ordinary handler line — raises `TypeError` after a reconnect and not
  before one (`test_the_float_roundtrip_changes_the_type`).
* `Decimal('19.90')` comes back `19.9` and renders `19.9` where Django renders
  `19.90`. Over {19.90, 0.00, 100.00, 2.50, 19.99} x four idioms, **8 of 20**
  cases disagree with Django through the float round trip against **0 of 20**
  through the tagged one (`test_the_tagged_roundtrip_agrees_with_django_far_more_often`).

  Both numbers moved after this file was written, and in the same direction:
  they were 10 and 2, the residual 2 being the separate `floatformat` gap
  (#2253). PR #2263 closed that gap for EVERY input type in the same drain, so
  the tagged column went to 0 — and two of the float column's cells were
  `floatformat` cells too, so it improved to 8. The float round trip is still
  wrong 8 times out of 20; that is what this measurement is for.

So the fix is the tagged round trip the issue proposes: write
`{"__djust_decimal__": "19.99"}` at the one encode chokepoint
(`decimal_for_state_roundtrip`), and `decode_state_roundtrip` it back at every
restore site.

**The decode is not optional.** An undecoded tag is strictly WORSE than the
float it replaces — a dict in the template rather than a wrong number — which
`test_an_undecoded_tag_is_worse_than_the_float` measures at 20/20 disagreements.
That is why the restore sites are found by grepping the SINK (`safe_setattr`
and the `_restore_*` hooks) rather than by mirroring the write sites, and why
the set is pinned mechanically in `TestTheDecodeSiteInventory`.

**The issue's read-side list was wrong in both directions**, which is why the
grep matters: it names `mixins/rust_bridge.py`, which has no restore path at
all, and omits three that do — `runtime.py`'s `_restore_snapshot` call (the
signed back-navigation snapshot), `time_travel.py`'s replay restores, and
`_restore_component_state`.

Every template assertion is a differential against real Django, and the curated
matrix is paired with a randomized sweep (v1.1.1-2 retro: a table samples the
axis you thought of).
"""

from __future__ import annotations

import ast
import json
import pathlib
import random
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("django")

from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402
from django.core.signing import JSONSerializer as DjangoSessionSerializer  # noqa: E402
from django.db import models  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from djust import LiveView  # noqa: E402
from djust import _rust  # noqa: E402
from djust.decorators import event_handler  # noqa: E402
from djust.serialization import (  # noqa: E402
    STATE_DECIMAL_TAG,
    StateRoundtripJSONEncoder,
    decimal_for_state_roundtrip,
    decimal_tags_to_strings,
    decode_state_roundtrip,
    django_json_datetime,
    normalize_django_value,
)

#: 29 significant digits — a binary double holds ~15, so the loss is visible.
HUGE = Decimal("12345678901234567890.123456789")

#: Ordinary money. Every one of these round-trips through `float` with NO
#: precision loss and is still wrong, which is the point of this issue.
MONEY = [
    Decimal("19.90"),
    Decimal("0.00"),
    Decimal("100.00"),
    Decimal("2.50"),
    Decimal("19.99"),
]

TEMPLATES = [
    "{{ p }}",
    "{{ p|floatformat }}",
    "{{ p|floatformat:2 }}",
    "{{ p|stringformat:'s' }}",
]

_PKG = pathlib.Path(__file__).resolve().parents[1] / "djust"
_REPO = _PKG.parents[1]

#: A model with a `DecimalField` — the ordinary way a `Decimal` reaches view
#: state at all.
_PricedThing = type(
    "D2252PricedThing",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "price": models.DecimalField(max_digits=40, decimal_places=9, default=Decimal("0")),
        "__str__": lambda self: f"thing({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _session_roundtrip(value):
    """Push *value* through the REAL Django session serializer and back.

    `django.core.signing.JSONSerializer` is what `SESSION_SERIALIZER` defaults
    to — `json.dumps` with no `cls=`. Using it rather than a bare `json.dumps`
    keeps the harness on the production path (#1650).
    """
    ser = DjangoSessionSerializer()
    return ser.loads(ser.dumps(normalize_django_value(value, state_roundtrip=True)))


# ===========================================================================
# 1. The round trip IS the assertion.
# ===========================================================================


class TestTheRoundTripIsLossless:
    @pytest.mark.parametrize("value", [*MONEY, HUGE, Decimal("-3.5"), Decimal("1E-9")])
    def test_a_decimal_comes_back_a_decimal_with_every_digit(self, value: Decimal) -> None:
        restored = decode_state_roundtrip(_session_roundtrip({"p": value}))["p"]
        assert isinstance(restored, Decimal), f"{value} came back {type(restored).__name__}"
        assert restored == value
        # `==` alone would pass for Decimal('19.90') vs Decimal('19.9'):
        # Decimal compares by value, not by exponent. Pin the digits too.
        assert str(restored) == str(value)

    def test_the_snapshot_encoder_round_trips_losslessly_too(self) -> None:
        """The signed snapshot uses the ENCODER adapter, not the normalizer."""
        for value in (*MONEY, HUGE):
            wire = json.dumps({"p": value}, cls=StateRoundtripJSONEncoder)
            restored = decode_state_roundtrip(json.loads(wire))["p"]
            assert isinstance(restored, Decimal)
            assert str(restored) == str(value)

    def test_nested_containers_are_reached(self) -> None:
        payload = {
            "rows": [{"total": HUGE}, {"total": Decimal("0.00")}],
            "deep": {"a": {"b": [Decimal("2.50")]}},
        }
        back = decode_state_roundtrip(_session_roundtrip(payload))
        assert back["rows"][0]["total"] == HUGE
        assert str(back["rows"][1]["total"]) == "0.00"
        assert str(back["deep"]["a"]["b"][0]) == "2.50"

    def test_the_template_render_after_restoration_matches_django(self) -> None:
        """The whole point of destination 3: the restored value lands in the
        template context on the very next render."""
        mismatches = []
        for value in MONEY:
            restored = decode_state_roundtrip(_session_roundtrip({"p": value}))
            for source in TEMPLATES:
                want = DjangoTemplate(source).render(DjangoContext({"p": value}))
                got = _rust.render_template(source, restored)
                if want != got:
                    mismatches.append((str(value), source, want, got))
        # NO survivors. When this was written two remained — the separate
        # `floatformat` gap (#2253), which a Decimal in the context hit
        # identically and so was never this boundary's to fix. #2263 closed it
        # (PR #2263, same drain), so the round trip is now exact end to end.
        #
        # Updated rather than relaxed: an empty list is a stronger assertion
        # than the two-cell one it replaces, and it reddens if either fix
        # regresses.
        assert mismatches == [], f"unexpected divergence after restore: {mismatches!r}"


# ===========================================================================
# 2. Why `float` was not good enough — measured, not asserted.
# ===========================================================================


class TestWhatTheFloatRoundTripCost:
    def test_the_float_roundtrip_changes_the_type(self) -> None:
        """The cost the issue does NOT name, and the one that crashes.

        No precision is lost for `Decimal('19.99')` at all — the type is.
        """
        as_float = float(Decimal("19.99"))
        assert not isinstance(as_float, Decimal)
        with pytest.raises(TypeError):
            as_float + Decimal("1")
        # The tagged round trip keeps the type, so the same line works.
        restored = decode_state_roundtrip(_session_roundtrip({"p": Decimal("19.99")}))["p"]
        assert restored + Decimal("1") == Decimal("20.99")

    def test_the_tagged_roundtrip_agrees_with_django_far_more_often(self) -> None:
        """The 5x4 matrix, run both ways. 8/20 disagreements become 0/20.

        Both numbers were higher when written (10 and 2). The residual 2 were
        the `floatformat` gap (#2253), which PR #2263 closed for every input
        type in the same drain — so the tagged column went to 0, and two of the
        float column's cells were `floatformat` cells too, taking it to 8.
        """
        float_bad = tagged_bad = 0
        for value in MONEY:
            as_float = float(value)
            restored = decode_state_roundtrip(_session_roundtrip({"p": value}))["p"]
            for source in TEMPLATES:
                want = DjangoTemplate(source).render(DjangoContext({"p": value}))
                if _rust.render_template(source, {"p": as_float}) != want:
                    float_bad += 1
                if _rust.render_template(source, {"p": restored}) != want:
                    tagged_bad += 1
        assert (float_bad, tagged_bad) == (8, 0), (
            f"float={float_bad}/20 tagged={tagged_bad}/20 — the measurement in "
            "the module docstring and the CHANGELOG is now wrong"
        )

    def test_the_precision_loss_the_issue_does_name_is_also_gone(self) -> None:
        assert float(HUGE) != HUGE
        assert decode_state_roundtrip(_session_roundtrip({"p": HUGE}))["p"] == HUGE


# ===========================================================================
# 3. Why the decode is mandatory at EVERY site.
# ===========================================================================


class TestTheDecodeIsMandatory:
    def test_an_undecoded_tag_is_worse_than_the_float(self) -> None:
        """A missed decode does not degrade to the old behaviour — it degrades
        BELOW it. The float was a wrong number; the tag is a dict.

        This is the whole reason the restore sites are grepped from the sink
        and pinned as a set rather than trusted to symmetry.
        """
        bad = 0
        for value in MONEY:
            undecoded = _session_roundtrip({"p": value})
            for source in TEMPLATES:
                want = DjangoTemplate(source).render(DjangoContext({"p": value}))
                if _rust.render_template(source, undecoded) != want:
                    bad += 1
        assert bad == 20, f"expected every case to break undecoded, got {bad}/20"
        # And it is visibly a dict, not merely a wrong number.
        rendered = _rust.render_template("{{ p }}", _session_roundtrip({"p": HUGE}))
        assert STATE_DECIMAL_TAG in rendered.replace("&#x27;", "'")

    def test_the_python_tag_is_the_rust_tag(self) -> None:
        """One name across the two halves of the framework.

        The Rust `visit_map` decodes this exact shape for the binary state
        backend (#2214). If the two drifted, a Decimal that survived the
        session would still be mangled by a msgpack cache hit.
        """
        rust_src = (_REPO / "crates" / "djust_core" / "src" / "lib.rs").read_text()
        assert f'DECIMAL_TAG: &str = "{STATE_DECIMAL_TAG}"' in rust_src, (
            "the Python STATE_DECIMAL_TAG must equal the Rust DECIMAL_TAG; "
            f"Python has {STATE_DECIMAL_TAG!r}"
        )

    def test_a_tagged_value_survives_the_rust_binary_state_backend(self) -> None:
        """End-to-end across the language boundary: restore, then let the Rust
        state backend msgpack it and read it back."""
        restored = decode_state_roundtrip(_session_roundtrip({"p": HUGE}))
        view = _rust.RustLiveView("{{ p }}")
        view.update_state(restored)
        cloned = _rust.RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert cloned.render() == str(HUGE)


# ===========================================================================
# 4. The collision hazard — the same near misses the Rust side pins.
# ===========================================================================


class TestTheCollisionHazard:
    """`decode_state_roundtrip` reads a one-key map under the tag as a
    `Decimal`, so a user dict of that exact shape is misread. Mirrors
    `crates/djust_core/tests/test_decimal_value_2214.rs::
    the_decimal_tag_does_not_capture_an_ordinary_dict`, plus a fourth case the
    Rust side does not need."""

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({"price": "19.99"}, id="a-different-key"),
            pytest.param({STATE_DECIMAL_TAG: 5}, id="a-non-string-payload"),
            pytest.param({STATE_DECIMAL_TAG: "1", "other": 2}, id="the-key-alongside-another"),
            pytest.param({STATE_DECIMAL_TAG: None}, id="a-null-payload"),
        ],
    )
    def test_a_near_miss_stays_a_dict(self, value) -> None:
        assert decode_state_roundtrip(value) == value
        assert isinstance(decode_state_roundtrip(value), dict)

    def test_an_unparseable_payload_fails_soft(self) -> None:
        """The fourth rule, Python-only: `Decimal('hi')` RAISES where Rust just
        stores the string. A colliding user dict must not crash a reconnect."""
        collide = {STATE_DECIMAL_TAG: "not a number"}
        assert decode_state_roundtrip(collide) == collide

    def test_the_documented_collision_really_does_collide(self) -> None:
        """State the hazard honestly rather than claim it away: a user dict
        that IS exactly the tag shape with a numeric string IS misread."""
        assert decode_state_roundtrip({STATE_DECIMAL_TAG: "19.99"}) == Decimal("19.99")

    def test_a_collision_nested_deep_is_still_only_that_shape(self) -> None:
        payload = {"a": [{"b": {STATE_DECIMAL_TAG: "1", "c": 2}}]}
        assert decode_state_roundtrip(payload) == payload


# ===========================================================================
# 5. Non-regression — every other type these paths carry.
# ===========================================================================

#: The types the flag genuinely MOVES, and the reason it has to (#2467).
#:
#: ``normalize_django_value`` carries these through UNCONVERTED for the Rust
#: renderer — the ``Decimal`` split (#2239) applied to the datetime family, so
#: ``Value::Encoded`` (#2448) is built on the LiveView path as it already was
#: on the raw one. Django's session serializer passes no encoder and refuses
#: every one of them, which is precisely the boundary this flag exists for, so
#: the flag CANNOT be a no-op here. ``test_the_flag_DOES_move_a_carried_through_value``
#: and ``test_the_carry_through_types_are_exactly_the_ones_the_boundary_must_convert``
#: below assert both halves rather than exempting them.
#: Each row carries the STORED spelling the flag converts it to, because
#: there is no longer one converter: #2467's four take
#: `django_json_datetime` and #2477's `set` takes the sorted list #626 gave
#: it. Pairing the value with its spelling keeps the assertion exact for both
#: rather than weakening it to "some string".
_CARRIED_THROUGH = [
    pytest.param(date(2024, 6, 15), django_json_datetime(date(2024, 6, 15)), id="date"),
    pytest.param(
        datetime(2024, 6, 15, 12, 30, 45),
        django_json_datetime(datetime(2024, 6, 15, 12, 30, 45)),
        id="datetime",
    ),
    pytest.param(time(12, 30, 45), django_json_datetime(time(12, 30, 45)), id="time"),
    pytest.param(
        timedelta(days=2, hours=3),
        django_json_datetime(timedelta(days=2, hours=3)),
        id="timedelta",
    ),
    # #2477/#2489. A `set` was in `_UNTOUCHED` until the conversion learned to
    # carry one: `normalize_django_value` handed the renderer a sorted LIST,
    # which is subscriptable where a set is not, so `{{ tags|first }}` rendered
    # an element where Django raises. It takes the same treatment `Decimal` and
    # the datetime family take — carried through for the renderer, converted at
    # THIS boundary, because `json.dumps` refuses a set.
    pytest.param({1, 2, 3}, [1, 2, 3], id="set"),
]

#: The types the flag does not touch — the set the no-op claim still holds for.
_UNTOUCHED = [
    pytest.param(None, id="none"),
    pytest.param(True, id="bool"),
    pytest.param(42, id="int"),
    pytest.param(1.5, id="float"),
    pytest.param("hello", id="str"),
    pytest.param(uuid.UUID("12345678-1234-5678-1234-567812345678"), id="uuid"),
    pytest.param([1, "two", None, {"three": 3}], id="nested-list"),
    pytest.param({"a": {"b": [1, 2, {"c": "d"}]}}, id="nested-dict"),
    pytest.param((1, "two"), id="tuple"),
]

#: Both halves. The two claims below that hold across the WHOLE set — the
#: decode is the identity, and the real session serializer round-trips it —
#: keep sweeping it, because #2467 changed which types the flag converts and
#: not whether the stored form survives.
_OTHER_TYPES = _UNTOUCHED + [pytest.param(row.values[0], id=row.id) for row in _CARRIED_THROUGH]


class TestEveryOtherTypeIsUntouched:
    @pytest.mark.parametrize("value", _UNTOUCHED)
    def test_the_flag_changes_nothing_for_a_CARRY_FREE_value(self, value) -> None:
        """NARROWED by #2467, and the narrower rule is the durable one.

        This asserted *"the state-roundtrip flag's ONLY effect is the `Decimal`
        branch"*, which was true when #2252 wrote it. #2467 gave the datetime
        family the same treatment `Decimal` has — carried through unconverted
        for the Rust renderer, converted at this boundary — so the flag now has
        two branches, and the honest invariant is not a list of exempt types
        but the RULE behind them:

            the flag changes nothing for a value that holds no type
            `normalize_django_value` carries through unconverted.

        That is strictly stronger than an exemption list, because the two sets
        are the same set by construction: a future type added to the
        carry-through side and forgotten at this boundary would leave a value
        the session serializer refuses, and
        `test_the_carry_through_types_are_exactly_the_ones_the_boundary_must_convert`
        goes red rather than the gap being silent.
        """
        assert normalize_django_value(value, state_roundtrip=True) == normalize_django_value(value)

    @pytest.mark.parametrize("value,expected", _CARRIED_THROUGH)
    def test_the_flag_DOES_move_a_carried_through_value(self, value, expected) -> None:
        """Non-vacuity for the narrowing above (#1468/#1859).

        Without this, moving ids out of one parametrisation reads as an
        exemption — and an exemption nobody can distinguish from a bug is the
        failure mode this file's own `RAISE_BIT_NOT_CLOSED` rule is about. So
        each is asserted POSITIVELY: unflagged is the object, flagged is the
        boundary's own spelling, and they differ.
        """
        assert normalize_django_value(value) is value
        stored = normalize_django_value(value, state_roundtrip=True)
        assert stored == expected
        assert stored != normalize_django_value(value)

    @pytest.mark.parametrize("value,expected", _CARRIED_THROUGH)
    def test_the_carry_through_types_are_exactly_the_ones_the_boundary_must_convert(
        self, value, expected
    ) -> None:
        """WHY the flag is not a no-op for these, run rather than asserted.

        Django's session serializer passes no encoder. It refuses the carried
        value and accepts the converted one — which is the entire reason
        `state_roundtrip` exists, and the reason "carry it through on both
        sides" was not an option for #2467.
        """
        with pytest.raises(TypeError):
            DjangoSessionSerializer().dumps({"p": normalize_django_value(value)})
        assert DjangoSessionSerializer().dumps(
            {"p": normalize_django_value(value, state_roundtrip=True)}
        )

    @pytest.mark.parametrize("value", _UNTOUCHED)
    def test_an_untouched_value_needed_no_conversion_in_the_first_place(self, value) -> None:
        """The other side of the same coin, and what makes the split a RULE
        rather than two hand-written lists: every type the flag leaves alone is
        one the session serializer already accepts unflagged.

        A type that reached `_UNTOUCHED` while actually needing conversion
        would fail here, so the two lists cannot drift apart silently.
        """
        assert DjangoSessionSerializer().dumps({"p": normalize_django_value(value)})

    @pytest.mark.parametrize("value", _OTHER_TYPES)
    def test_the_decode_is_the_identity_on_it(self, value) -> None:
        normalized = normalize_django_value(value, state_roundtrip=True)
        assert decode_state_roundtrip(normalized) == normalized

    @pytest.mark.parametrize("value", _OTHER_TYPES)
    def test_it_still_survives_the_real_session_serializer(self, value) -> None:
        before = normalize_django_value(value, state_roundtrip=True)
        assert decode_state_roundtrip(_session_roundtrip(value)) == before

    def test_the_encoder_still_matches_django_on_every_other_type(self) -> None:
        from django.core.serializers.json import DjangoJSONEncoder as RealDjangoEncoder

        for value in (
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            date(2024, 6, 15),
            datetime(2024, 6, 15, 12, 30, 45),
            time(12, 30, 45),
            {"nested": [1, "two", None]},
        ):
            assert json.dumps(value, cls=StateRoundtripJSONEncoder) == json.dumps(
                value, cls=RealDjangoEncoder
            )

    def test_a_model_instance_still_serializes_the_same_way(self) -> None:
        """Models take `_serialize_model_safely`, which this change does not
        touch — except that a `DecimalField` on one now carries the tag through
        the state boundary instead of a float."""
        obj = _PricedThing(name="widget", price=Decimal("19.90"))
        obj.pk = obj.id = 3
        flagged = normalize_django_value(obj, state_roundtrip=True)
        plain = normalize_django_value(obj)
        assert set(flagged) == set(plain), "the FIELD SET must be unchanged"
        for key in flagged:
            if key == "price":
                continue
            assert flagged[key] == plain[key], f"field {key} changed"
        assert flagged["price"] == {STATE_DECIMAL_TAG: "19.90"}
        assert plain["price"] == Decimal("19.90")
        # And it survives the real session serializer, which the raw Decimal
        # in `plain` would not.
        restored = decode_state_roundtrip(_session_roundtrip(obj))
        assert restored["price"] == Decimal("19.90")
        assert str(restored["price"]) == "19.90"

    def test_an_untagged_float_from_an_older_release_passes_through(self) -> None:
        """Backward compatibility in the useful direction: a session written
        before this change restores exactly as it did."""
        old = {"p": 1.2345678901234567e19, "n": 3, "s": "x", "d": {"k": [1.5]}}
        assert decode_state_roundtrip(old) == old

    #: Leaves that need no conversion, and the ones that do (#2467).
    #: Kept as two lists rather than one with `date` deleted: dropping the
    #: carried-through leaf would have made the sweep pass by shrinking, which
    #: is the failure this file's own randomized-corpus argument is against.
    @staticmethod
    def _carry_free_leaf(rng: random.Random):
        return rng.choice(
            [
                None,
                rng.randint(-(10**9), 10**9),
                rng.random(),
                "s" * rng.randint(0, 5),
                True,
                uuid.UUID(int=rng.getrandbits(128)),
            ]
        )

    @staticmethod
    def _carried_leaf(rng: random.Random):
        return rng.choice(
            [
                date(2024, 1, 1 + rng.randint(0, 27)),
                datetime(2024, 1, 1 + rng.randint(0, 27), rng.randint(0, 23), 30),
                time(rng.randint(0, 23), 30),
                timedelta(seconds=rng.randint(0, 10**5)),
            ]
        )

    @classmethod
    def _build(cls, rng: random.Random, leaf, depth: int = 0):
        if depth >= 3 or rng.random() < 0.4:
            return leaf(rng)
        if rng.random() < 0.5:
            return [cls._build(rng, leaf, depth + 1) for _ in range(rng.randint(0, 3))]
        return {f"k{i}": cls._build(rng, leaf, depth + 1) for i in range(rng.randint(0, 3))}

    def test_a_randomized_CARRY_FREE_corpus_is_bit_identical(self) -> None:
        """The curated list samples the shapes someone thought of.

        `date` moved out of the leaf set with #2467 — it is a carried-through
        type now, so a corpus containing one is not carry-free and the claim
        this test makes is not the claim to make about it. The sibling below
        sweeps those instead of dropping them.
        """
        rng = random.Random(2252)
        for _ in range(500):
            value = self._build(rng, self._carry_free_leaf)
            flagged = normalize_django_value(value, state_roundtrip=True)
            assert flagged == normalize_django_value(value)
            assert decode_state_roundtrip(flagged) == flagged

    def test_a_randomized_corpus_of_CARRIED_types_still_reaches_the_session(self) -> None:
        """The half the sweep above stopped covering, asserted on the property
        that actually matters for them: whatever the flag writes has to survive
        Django's encoder-less session serializer, at any nesting depth.

        Non-vacuity is built in — the same values WITHOUT the flag are fed to
        the same serializer and must be refused, so a change that quietly made
        the flag a no-op again would fail here rather than pass quietly.
        """
        rng = random.Random(2467)
        refused = 0
        for _ in range(500):
            value = self._build(rng, self._carried_leaf)
            flagged = normalize_django_value(value, state_roundtrip=True)
            assert DjangoSessionSerializer().dumps({"p": flagged})
            assert decode_state_roundtrip(flagged) == flagged
            try:
                DjangoSessionSerializer().dumps({"p": normalize_django_value(value)})
            except TypeError:
                refused += 1
        assert refused > 100, (
            f"only {refused} of 500 unflagged values were refused — the corpus is "
            "generating containers with no carried leaf in them, so the sweep is "
            "measuring nothing"
        )


# ===========================================================================
# 6. The real paths. Reproduction fidelity (#1650): a real session, real
#    view.get()/view.post(), real restore helpers — not a proxy.
# ===========================================================================


class _PriceView(LiveView):
    template = "<div dj-root><span>{{ price }}</span></div>"
    enable_state_snapshot = True

    def mount(self, request, **kwargs):
        self.price = Decimal("19.90")
        self.huge = HUGE
        self._secret_price = Decimal("0.00")
        self._user_private_keys = {"_secret_price"}

    @event_handler()
    def bump(self, **kwargs):
        self.price = self.price + Decimal("0.10")

    def get_context_data(self, **kwargs):
        return {"price": self.price, "huge": self.huge}


def _with_session(request):
    SessionMiddleware(lambda x: None).process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
class TestTheRealHTTPPostRoundTrip:
    """`mixins/request.py` — the GET writes the session, a FRESH view's POST
    reads it back through `safe_setattr`. Two decode sites in one path: the
    public `saved_state` loop and `_restore_private_state`."""

    def _seed(self):
        factory = RequestFactory()
        get_request = _with_session(factory.get("/price-2252/"))
        _PriceView().get(get_request)
        return factory, get_request

    def test_a_decimal_attr_survives_the_post_restore_as_a_decimal(self) -> None:
        factory, get_request = self._seed()
        fresh = _PriceView()
        post = factory.post(
            "/price-2252/",
            data='{"event":"bump","params":{}}',
            content_type="application/json",
        )
        post.session = get_request.session
        response = fresh.post(post)
        assert response.status_code == 200, response.content
        # `bump` adds Decimal('0.10') — which RAISES TypeError if the restored
        # attr came back a float, and produces 20.00 (not 20.0) only if the
        # restored value kept its exponent.
        assert isinstance(fresh.price, Decimal)
        assert str(fresh.price) == "20.00"
        assert fresh.huge == HUGE

    def test_the_private_decimal_survives_too(self) -> None:
        """`_restore_private_state` is the shared decode point for all three
        of its callers."""
        factory, get_request = self._seed()
        fresh = _PriceView()
        post = factory.post(
            "/price-2252/",
            data='{"event":"bump","params":{}}',
            content_type="application/json",
        )
        post.session = get_request.session
        fresh.post(post)
        assert isinstance(fresh._secret_price, Decimal)
        assert str(fresh._secret_price) == "0.00"

    def test_the_session_bytes_really_carry_the_tag(self) -> None:
        """Pin the wire form, not only the behaviour — so a future change that
        keeps the round trip working by a DIFFERENT mechanism is visible."""
        _, get_request = self._seed()
        saved = get_request.session["liveview_/price-2252/"]
        assert saved["price"] == {STATE_DECIMAL_TAG: "19.90"}
        assert saved["huge"] == {STATE_DECIMAL_TAG: str(HUGE)}


@pytest.mark.django_db
class TestTheSignedSnapshotRoundTrip:
    """`live_view.py`'s capture, `runtime.py`'s restore. A DIFFERENT serializer
    from the session (a bare `json.dumps` + HMAC, not Django's session
    JSONSerializer) — the #1646 shape, so both need the treatment."""

    def test_the_capture_tags_and_the_signed_blob_carries_it(self) -> None:
        from djust.security import sign_snapshot, unsign_snapshot

        view = _PriceView()
        view.mount(_with_session(RequestFactory().get("/price-2252/")))
        captured = view._capture_snapshot_state(strict=True)
        assert captured["price"] == {STATE_DECIMAL_TAG: "19.90"}

        # The emit path verbatim (`runtime.py`'s state_snapshot_signed block):
        # a bare json.dumps with NO encoder, which is why the tag must already
        # be plain JSON by this point.
        state_json = json.dumps(captured, sort_keys=True, separators=(",", ":"))
        signed = sign_snapshot(state_json, "app.PriceView", "sess-2252")
        raw = unsign_snapshot(signed, "app.PriceView", "sess-2252")
        assert raw is not None

        restored = decode_state_roundtrip(json.loads(raw))
        target = _PriceView()
        target._restore_snapshot(restored)
        assert isinstance(target.price, Decimal)
        assert str(target.price) == "19.90"
        assert target.huge == HUGE

    def test_the_bare_dumps_would_refuse_an_untagged_decimal(self) -> None:
        """The premise, run rather than assumed — this emit path has no
        encoder at all, so it is a second reason the raw Decimal is impossible
        here and not only in the session."""
        with pytest.raises(TypeError):
            json.dumps({"p": HUGE}, sort_keys=True, separators=(",", ":"))

    def test_the_decode_happens_before_the_user_override_point(self) -> None:
        """`_restore_snapshot` is a DOCUMENTED subclass-override hook. A user
        override must not have to know the tag shape exists, so `runtime.py`
        decodes at the CALLER. Pinned structurally, because the caller is deep
        in an async mount."""
        src = (_PKG / "runtime.py").read_text()
        block = src[src.index("_should_restore_snapshot\n") :]
        block = block[: block.index("_restore_snapshot)(state_dict)")]
        assert "decode_state_roundtrip(state_dict)" in block, (
            "runtime.py must decode BEFORE calling the user-overridable "
            "_restore_snapshot — see the docstring on LiveView._restore_snapshot"
        )
        assert (
            "decode_state_roundtrip"
            not in (_PKG / "live_view.py")
            .read_text()
            .split("def _restore_snapshot")[1]
            .split("def _should_restore_snapshot")[0]
        ), (
            "_restore_snapshot itself must stay decode-free: a subclass that "
            "overrides it would skip an in-method decode"
        )


@pytest.mark.django_db
class TestTheStickyChildRoundTrip:
    """`mixins/sticky.py` — its own save/restore pair, its own session keys."""

    def test_a_sticky_child_decimal_round_trips(self) -> None:
        from djust.mixins.sticky import (
            restore_sticky_child_state,
            save_sticky_child_state_sync,
        )

        class _Child(_PriceView):
            sticky_id = "prices"

        parent = _PriceView()
        child = _Child()
        child.mount(_with_session(RequestFactory().get("/dash/")))
        session: dict = {}
        save_sticky_child_state_sync(child, session, "/dash/")

        fresh = _Child()
        assert restore_sticky_child_state(fresh, parent, session, "/dash/") is True
        assert isinstance(fresh.price, Decimal)
        assert str(fresh.price) == "19.90"
        assert fresh.huge == HUGE


@pytest.mark.django_db
class TestTheComponentRoundTrip:
    """`mixins/components.py::_restore_component_state` — the shared decode
    point for its two callers (`mixins/request.py` POST, `runtime.py` mount)."""

    def test_a_component_decimal_round_trips(self) -> None:
        from djust.components.base import LiveComponent
        from djust.mixins.components import ComponentMixin

        class _Card(LiveComponent):
            template = "<div>{{ amount }}</div>"

            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.amount = Decimal("2.50")

        holder = ComponentMixin()
        target = _Card()
        target.amount = None
        stored = normalize_django_value({"amount": Decimal("2.50")}, state_roundtrip=True)
        assert stored["amount"] == {STATE_DECIMAL_TAG: "2.50"}
        holder._restore_component_state(target, stored)
        assert isinstance(target.amount, Decimal)
        assert str(target.amount) == "2.50"


@pytest.mark.django_db
class TestTheTimeTravelRoundTrip:
    """`time_travel.py` — a restore site the issue's list omits entirely, and
    the one place a tagged value is also DISPLAYED rather than restored."""

    def _snapshot(self):
        from djust.time_travel import EventSnapshot

        view = _PriceView()
        view.mount(_with_session(RequestFactory().get("/price-2252/")))
        return view, EventSnapshot(
            event_name="bump",
            params={},
            ref=1,
            ts=0.0,
            state_before=view._capture_snapshot_state(),
            state_after=view._capture_snapshot_state(),
        )

    def test_restoring_a_recorded_snapshot_gives_back_a_decimal(self) -> None:
        from djust.time_travel import restore_snapshot

        view, snap = self._snapshot()
        view.price = Decimal("999.99")
        assert restore_snapshot(view, snap, "before") is True
        assert isinstance(view.price, Decimal)
        assert str(view.price) == "19.90"

    def test_the_debug_panel_sees_the_digit_string_not_the_tag(self) -> None:
        """`to_dict` is the CLIENT-bound view of the same capture. Destination
        2's rule is the bare digit string, so the panel must not be handed a
        tag shape to render."""
        _, snap = self._snapshot()
        wire = snap.to_dict()
        assert wire["state_before"]["price"] == "19.90"
        assert wire["state_after"]["huge"] == str(HUGE)
        # And the buffered dataclass still carries the restorable form.
        assert snap.state_before["price"] == {STATE_DECIMAL_TAG: "19.90"}

    def test_the_display_helper_leaves_a_near_miss_alone(self) -> None:
        for value in ({STATE_DECIMAL_TAG: 5}, {STATE_DECIMAL_TAG: "1", "x": 2}, {"a": "b"}):
            assert decimal_tags_to_strings(value) == value


# ===========================================================================
# 7. The inventory — grep the SINK, pin the SET (v1.1.1-2 retro, #1125).
# ===========================================================================

#: Every production restore site, as (module, enclosing function), found by
#: grepping the SINK — `safe_setattr` plus the `_restore_*` hooks — NOT by
#: mirroring the twelve write sites. The two sets do not correspond: the write
#: side has no counterpart for `time_travel.py`, and `mixins/rust_bridge.py`
#: (which #2252 named as a read site) has no restore path at all.
EXPECTED_DECODE_SITES = {
    ("live_view.py", "_restore_private_state"),
    ("mixins/components.py", "_restore_component_state"),
    ("mixins/request.py", "post"),
    ("mixins/sticky.py", "restore_sticky_child_state"),
    ("runtime.py", "dispatch_mount"),
    ("time_travel.py", "restore_snapshot"),
    ("time_travel.py", "restore_component_snapshot"),
    ("time_travel.py", "to_dict"),
}


def _decode_call_sites():
    """Every call of `decode_state_roundtrip` / `decimal_tags_to_strings` in
    the package, as (relative module, enclosing function)."""
    found = set()
    for path in sorted(_PKG.rglob("*.py")):
        if "tests" in path.parts:
            continue
        # The defining module's own recursive self-calls are not call SITES.
        if path.name == "serialization.py" and path.parent == _PKG:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover
            continue
        rel = path.relative_to(_PKG).as_posix()
        stack: list[str] = []

        class Walk(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name in ("decode_state_roundtrip", "decimal_tags_to_strings") and stack:
                    found.add((rel, stack[-1]))
                self.generic_visit(node)

        Walk().visit(tree)
    return found


class TestTheDecodeSiteInventory:
    def test_the_decode_sites_are_exactly_the_expected_set(self) -> None:
        """A SET, not a floor (#1125). A new restore path that forgets the
        decode does not fail here — but a decode ADDED or REMOVED does, which
        is what keeps this list honest against a refactor."""
        assert _decode_call_sites() == EXPECTED_DECODE_SITES

    def test_every_safe_setattr_of_restored_state_is_covered(self) -> None:
        """The sink grep, mechanised: every module that applies session- or
        snapshot-sourced state to a view must also decode. `websocket.py`'s
        `server_push` is excluded by name — it applies a CLIENT frame, which
        never went through `decimal_for_state_roundtrip`."""
        restoring = set()
        for path in sorted(_PKG.rglob("*.py")):
            if "tests" in path.parts:
                continue
            src = path.read_text()
            if "safe_setattr(" not in src:
                continue
            rel = path.relative_to(_PKG).as_posix()
            if rel in ("security/attribute_guard.py", "security/__init__.py", "websocket.py"):
                continue
            restoring.add(rel)
        decoding = {module for module, _ in _decode_call_sites()}
        assert restoring <= decoding, (
            f"these modules apply restored state without decoding: {restoring - decoding}"
        )

    def test_the_sink_check_would_catch_a_missed_module(self) -> None:
        """Empirical canary (#1459): the check above is only worth having if
        it goes red for a module that restores without decoding."""
        restoring = {"live_view.py", "runtime.py", "a_new_restore_path.py"}
        decoding = {module for module, _ in _decode_call_sites()}
        assert not restoring <= decoding

    def test_one_function_decides_each_direction(self, monkeypatch) -> None:
        """Load-bearing single-source pin, not a decorative one (#1859).
        Redefining the encoder must change BOTH adapters."""
        import djust.serialization as ser

        monkeypatch.setattr(ser, "decimal_for_state_roundtrip", lambda d: f"SENTINEL:{d}")
        assert ser.normalize_django_value(Decimal("1.5"), state_roundtrip=True) == "SENTINEL:1.5"
        assert (
            json.loads(json.dumps(Decimal("1.5"), cls=ser.StateRoundtripJSONEncoder))
            == "SENTINEL:1.5"
        )

    def test_the_encode_and_decode_are_inverses_by_construction(self) -> None:
        rng = random.Random(22522)
        for _ in range(1000):
            digits = "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 30)))
            frac = "".join(rng.choice("0123456789") for _ in range(rng.randint(0, 20)))
            sign = rng.choice(["", "-"])
            value = Decimal(f"{sign}{digits}.{frac}" if frac else f"{sign}{digits}")
            wire = json.loads(json.dumps(decimal_for_state_roundtrip(value)))
            back = decode_state_roundtrip(wire)
            assert isinstance(back, Decimal)
            assert str(back) == str(value)
