"""A tuple keeps its identity across a state round trip (#2276).

**The issue's headline claim is false, and checking it first changed the fix.**
It says `Value::Tuple` is "unreachable from a view context" and that
`{{ (1.0,) }}` renders `[1.0]`. Measured: it renders `(1.0,)`, exactly as Django
does. The variant is reachable and rendering was never broken — #2203 added it
so a tuple would render with parentheses, and that works.

The issue's second claim is also false in the direction that matters:
`normalize_django_value` does flatten a tuple to a list, but so does **Django's
own** `DjangoJSONEncoder` — `json.dumps` has no tuple type. Both emit `[1.0]`.
That is parity, not a divergence.

What IS broken is narrower and was not named: the **round trip**. msgpack has no
tuple either, so `Value::Tuple` serialized as an array and came back a `list` —
a view attribute changed type across a reconnect, and `(1, 2)` rendered `[1, 2]`
after one and not before. Same class as the `Decimal` loss #2214 fixed with a
binary tag, and fixed the same way.

So the human-readable arm deliberately stays an array (that is Django parity)
and only the binary arm is tagged.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("django")

from django.core.serializers.json import DjangoJSONEncoder as UpstreamEncoder  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust._rust import RustLiveView  # noqa: E402
from djust.serialization import DjangoJSONEncoder as DjustEncoder  # noqa: E402

TUPLES = [(1, 2), (1.0,), (), ("a", "b"), ((1, 2), (3,)), (1, [2, 3], {"k": 4})]


def _roundtrip(value: object) -> RustLiveView:
    view = RustLiveView("{{ p }}")
    view.set_state("p", value)
    return RustLiveView.deserialize_msgpack(view.serialize_msgpack())


@pytest.mark.parametrize("value", TUPLES)
def test_rendering_a_tuple_already_matched_django(value: object) -> None:
    """The claim the issue leads with, checked before fixing anything.

    `Value::Tuple` is reachable and correct. Pinned so a future reader does not
    re-derive the false premise from the issue text.
    """
    ctx = {"p": value}
    assert _rust.render_template("{{ p }}", ctx) == DjangoTemplate("{{ p }}").render(
        DjangoContext(ctx)
    )


@pytest.mark.parametrize("value", TUPLES)
def test_a_tuple_survives_the_state_round_trip(value: object) -> None:
    """The defect that was real: before the tag, `(1, 2)` came back `[1, 2]`."""
    view = RustLiveView("{{ p }}")
    view.set_state("p", value)
    before = view.render()
    restored = _roundtrip(value)
    assert restored.render() == before, f"{value!r} changed shape across the round trip"
    assert isinstance(restored.get_state()["p"], tuple), (
        f"{value!r} came back a {type(restored.get_state()['p']).__name__}"
    )


def test_the_json_arm_stays_an_array_because_django_does() -> None:
    """Deliberate asymmetry, not a gap.

    `json.dumps` has no tuple, and Django's own encoder emits an array. Tagging
    the human-readable arm would be a divergence dressed as a fix.
    """
    for value in ((1.0,), (1, 2), (), ("a",)):
        assert json.dumps({"p": value}, cls=DjustEncoder) == json.dumps(
            {"p": value}, cls=UpstreamEncoder
        )
    assert json.dumps({"p": (1.0,)}, cls=DjustEncoder) == '{"p": [1.0]}'


def test_a_list_is_still_a_list_after_the_round_trip() -> None:
    """The tag must not capture the neighbour it sits next to."""
    restored = _roundtrip([1, 2])
    assert restored.render() == "[1, 2]"
    assert isinstance(restored.get_state()["p"], list)


def test_a_user_dict_shaped_like_the_tag_is_misread() -> None:
    """The documented collision hazard, asserted rather than claimed away.

    Identical to the one `DECIMAL_TAG` carries (#2214) and for the same reason:
    a one-key map under that exact name is indistinguishable from the encoding.
    Pinned so the trade is visible — if this ever needs closing, it needs a
    different encoding, not a guard.
    """
    restored = _roundtrip({"__djust_tuple__": [1, 2]})
    assert restored.render() == "(1, 2)"
    assert isinstance(restored.get_state()["p"], tuple)


@pytest.mark.parametrize(
    "payload",
    [
        {"__djust_tuple__": "not a list"},
        {"__djust_tuple__": [1], "other": 2},
        {"__djust_tuplex__": [1]},
        {},
    ],
)
def test_the_near_misses_stay_dicts(payload: dict) -> None:
    """Three discriminations plus the empty case: one key, that key, a list payload."""
    restored = _roundtrip(payload)
    assert isinstance(restored.get_state()["p"], dict), payload
