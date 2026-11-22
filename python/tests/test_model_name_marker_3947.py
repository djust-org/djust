"""#3947 -- the model marker survives a state round trip and is not minted from data.

``serialization.model_identity`` stamps ``__model__`` as a ``ModelName`` (a ``str`` subclass);
the Rust engine reads it as ``Value::ModelName`` and only that makes a map a model. The binary
state encoding tags it, so a model map read back from the state backend is still a model; a
user dict shaped like the tag is escaped and comes back as itself.
"""

from __future__ import annotations

import html as html_lib
import re

import pytest

from djust import serialization
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


class ModelName(str):
    """A user's own ``str`` subclass that happens to share the marker's NAME (#3947 review S4)."""

    __slots__ = ()


def _forged_rows() -> dict:
    from django.utils.safestring import mark_safe

    return {
        "safe-text": {"__str__": "x", "__model__": mark_safe("Thing"), "pk": 1},
        "user-modelname": {"__str__": "x", "__model__": ModelName("Thing"), "pk": 1},
    }


def _entry_render(entry: str, source: str, ctx: dict) -> str:
    from djust import _rust

    if entry == "raw":
        return html_lib.unescape(_rust.render_template(source, ctx))
    if entry == "backend":
        from djust.template import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        return html_lib.unescape(str(backend.from_string(source).render(context=ctx)))
    from djust.live_view import LiveView
    from djust.testing import LiveViewTestClient

    def mount(self, request, **kwargs):
        for name, value in ctx.items():
            setattr(self, name, value)

    view = type(
        "ForgedView",
        (LiveView,),
        {"exposure_policy": "legacy", "template": f"<div dj-root>{source}</div>", "mount": mount},
    )
    client = LiveViewTestClient(view)
    client.mount()
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", client.render(), re.S)
    assert match is not None
    return html_lib.unescape(match.group(1))


@pytest.mark.parametrize("entry", ["liveview", "backend", "raw"])
@pytest.mark.parametrize("forged", ["safe-text", "user-modelname"])
def test_a_str_subclass_marker_that_is_not_ModelName_stays_a_dict(db, entry, forged):
    """The Rust ``FromPyObject`` arm checks the EXACT cached ``ModelName`` type: a safe string or a
    user class of the same name in ``__model__`` leaves the map a dict on every entry (a model map
    would ``pprint`` as its ``__str__``, ``x``)."""
    row = _forged_rows()[forged]
    got = _entry_render(entry, "{{ d|pprint }}", {"d": row})
    assert "__str__" in got and "Thing" in got, (entry, forged, got)
    # non-vacuity: the real marker makes the same map a model, spelled as its text
    real = dict(row, __model__=serialization.ModelName("Thing"))
    if entry == "raw":
        assert _entry_render(entry, "{{ d|pprint }}", {"d": real}) == "x"
