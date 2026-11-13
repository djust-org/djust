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
legacy signed back-navigation snapshot, removed in 2.0), `time_travel.py`'s
replay restores, and `_restore_component_state` (removed in 2.0 with the
component session state).

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
        """The snapshot capture uses the ENCODER adapter, not the normalizer."""
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
    exposure_policy = "legacy"
    template = "<div dj-root><span>{{ price }}</span></div>"

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
class TestTheSnapshotCapture:
    """`live_view.py`'s capture (`_capture_snapshot_state`, read by time-travel
    recording) goes through the ENCODER adapter, a different serializer from
    the session's, so it needs the tag too (the #1646 shape)."""

    def test_the_capture_tags_a_decimal_and_the_decode_restores_it(self) -> None:
        view = _PriceView()
        view.mount(_with_session(RequestFactory().get("/price-2252/")))
        captured = view._capture_snapshot_state()
        assert captured["price"] == {STATE_DECIMAL_TAG: "19.90"}

        # Plain JSON by this point: a bare dumps with no encoder carries it.
        raw = json.dumps(captured, sort_keys=True, separators=(",", ":"))
        restored = decode_state_roundtrip(json.loads(raw))
        assert isinstance(restored["price"], Decimal)
        assert str(restored["price"]) == "19.90"
        assert restored["huge"] == HUGE


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
    # #3252: restore the lazy child's server-held public/private session state,
    # tagged at save_http_lazy's encoder-less JSON boundary.
    ("_lazy_containers.py", "mount_http_lazy"),
    # The body of `_restore_private_state`, which enters its `lifecycle` step around it (#3646).
    ("live_view.py", "_apply_private_state"),
    # 2.0 removed the page-POST fallback's session restore and the component
    # session restore, with their decodes (``mixins/request.py``,
    # ``mixins/components.py``).
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


def _explicit_child_json_assignments(tree):
    """Only the exact gated child JSON-primitives restore is not a Decimal sink."""
    expected = ast.parse(
        "if explicit_values is not None:\n"
        "    for name, value in explicit_values.items():\n"
        "        safe_setattr(child, name, value, allow_private=False, raise_on_blocked=True)\n"
    ).body[0]
    matches = [
        node
        for function in tree.body
        if isinstance(function, ast.FunctionDef) and function.name == "live_render"
        for node in ast.walk(function)
        if ast.dump(node) == ast.dump(expected)
    ]
    assert len(matches) == 1, "Review the explicit child restore codec boundary"
    return {
        node
        for node in ast.walk(matches[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "safe_setattr"
    }


def _explicit_root_json_assignments(tree):
    """``runtime.py``'s one restore site: ``apply_restored_state``, which the
    reconnect mount and the over-limit rollback (#4095) both call. It assigns
    declared fields from the explicit codecs, which accept JSON primitives only
    and are never tagged. Identified by function AND call shape, exactly once."""
    expected = (
        ast.parse("safe_setattr(view, key, value, allow_private=False, raise_on_blocked=True)")
        .body[0]
        .value
    )
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "apply_restored_state"
    ]
    assert len(functions) == 1, "Review runtime.py's explicit restore codec boundary"
    calls = {
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Call) and ast.dump(node) == ast.dump(expected)
    }
    assert len(calls) == 1, "Review runtime.py's explicit restore codec boundary"
    return calls


def _explicit_self_json_assignments(tree):
    """``mixins/request.py``'s one restore: an explicit view's declared server
    fields from the explicit codec, which accepts JSON primitives only."""
    expected = (
        ast.parse("safe_setattr(self, key, value, allow_private=False, raise_on_blocked=True)")
        .body[0]
        .value
    )
    return {
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.dump(node) == ast.dump(expected)
    }


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
            rel = path.relative_to(_PKG).as_posix()
            # ``_state_restore.restore_public_state`` is the shared sink: it
            # applies what its caller passes, so the CALLER must decode.
            if rel != "_state_restore.py" and "restore_public_state(" in src:
                restoring.add(rel)
                continue
            if "safe_setattr(" not in src:
                continue
            if rel in (
                "security/attribute_guard.py",
                "security/__init__.py",
                "websocket.py",
                "_state_restore.py",
            ):
                continue
            if rel == "templatetags/live_tags.py":
                # ADR-038's explicit codec accepts JSON primitives only. Running
                # the legacy decoder here would corrupt ordinary tag-shaped
                # dictionaries. Exempt the exact assignment block, not the module.
                tree = ast.parse(src)
                calls = {
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "safe_setattr"
                }
                if calls == _explicit_child_json_assignments(tree):
                    continue
            if rel == "runtime.py":
                # The legacy session restore (which decoded) was removed in
                # 2.0. What is left assigns the explicit codecs' JSON
                # primitives; the same exemption, by exact call shape.
                tree = ast.parse(src)
                calls = {
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "safe_setattr"
                }
                if calls and calls == _explicit_root_json_assignments(tree):
                    continue
            if rel == "mixins/request.py":
                # The page-POST fallback's legacy session restore (which
                # decoded) was removed in 2.0. What is left assigns an explicit
                # view's declared server fields, JSON primitives from the
                # explicit codec; exempted by exact call shape.
                tree = ast.parse(src)
                calls = {
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "safe_setattr"
                }
                if calls and calls == _explicit_self_json_assignments(tree):
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

    def test_child_codec_exception_does_not_hide_an_added_assignment(self) -> None:
        source = (_PKG / "templatetags/live_tags.py").read_text()
        tree = ast.parse(source + "\nsafe_setattr(child, key, restored_value)\n")
        exempt = _explicit_child_json_assignments(tree)
        calls = {
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "safe_setattr"
        }
        assert len(calls - exempt) == 1

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
