"""#3947 -- the model marker survives a state round trip and is not minted from data.

``serialization.model_identity`` stamps ``__model__`` as a ``ModelName`` (a ``str`` subclass);
the Rust engine reads it as ``Value::ModelName`` and only that makes a map a model. The binary
state encoding tags it, so a model map read back from the state backend is still a model; a
user dict shaped like the tag is escaped and comes back as itself.
"""

from __future__ import annotations

from djust._rust import RustLiveView
from djust.serialization import model_identity


class _Row:
    pk = 7

    def __str__(self) -> str:
        return "row seven"


def _identity() -> dict:
    return {**model_identity(_Row()), "name": "n7"}


def _round_trip(view: RustLiveView) -> RustLiveView:
    return RustLiveView.deserialize_msgpack(view.serialize_msgpack())


def test_state_round_trip_keeps_model_marker() -> None:
    view = RustLiveView("{{ obj|pprint }}|{{ obj.items|length }}")
    view.set_state("obj", _identity())
    before = view.render()
    assert before == "row seven|0", before
    assert _round_trip(view).render() == before


def test_a_forged_identity_dict_stays_a_dict_through_a_round_trip() -> None:
    forged = {"__str__": "label", "__model__": "Thing", "pk": 1}
    tag_shaped = {"__str__": "label", "__model__": {"__djust_model_name__": "Thing"}, "pk": 1}
    for value in (forged, tag_shaped):
        view = RustLiveView("{{ d.items|length }}")
        view.set_state("d", value)
        assert view.render() == "3"
        assert _round_trip(view).render() == "3", value
