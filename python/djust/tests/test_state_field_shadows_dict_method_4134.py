"""#4134 review: a component State field may be named after a dict method.

``TypedState`` is a ``dict`` whose fields are properties, so a field named
``items``, ``values``, ``keys`` ... replaces that method on the instance. djust
used to call those methods on a state (``state.items()``) when it saved, encoded
or walked it, so a view declaring such a component crashed, under explicit
exposure on the page GET. djust now reads a state through the unbound ``dict``
methods (``dict.items(state)``), so every such name works.

Only ``get`` and ``update`` are refused, when the class is created: TypedState
calls those two on itself, so a field of either name cannot work at all (on
``main`` neither did).

Each allowed name is a field of a real component on a real view, under both
exposure policies, through: a page GET that saves state, a fallback POST that
sends an event, a WebSocket commit and a reconnect, and a JSON round trip.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, _automatic_state
from djust.components.descriptors.base import LiveComponent, bound_state
from djust.components.mixins.base import TypedState
from djust.decorators import event_handler
from djust.tests import _per_turn_harness as harness

MODULE = __name__

#: Every dict method name a field may take (all of them but ``get`` and ``update``).
NAMES = ("items", "keys", "values", "copy", "pop", "clear", "fromkeys", "popitem", "setdefault")
POLICIES = ("explicit",)


def _component(name: str) -> type:
    state = type("State", (TypedState,), {"__annotations__": {name: list}, name: ()})

    def shadow_add(self: Any, **kwargs: Any) -> None:
        current = bound_state(self)
        current[name] = [*dict.get(current, name, ()), 1]

    return type(
        "Shadow_%s" % name,
        (LiveComponent,),
        {
            "State": state,
            "Meta": type("Meta", (), {"events": ("shadow_add",)}),
            "shadow_add": event_handler(shadow_add),
            "__module__": MODULE,
        },
    )


def _view(name: str, policy: str) -> type:
    cls = type(
        "Shadow_%s_%s" % (name, policy),
        (LiveView,),
        {
            "exposure_policy": policy,
            "template": "<div dj-root><p>n={{ w.%s|length }}</p></div>" % name,
            "w": _component(name)(),
            "mount": lambda self, request, **kwargs: None,
            "__module__": MODULE,
        },
    )
    globals()[cls.__name__] = cls  # importable by dotted path, for the socket mount
    return cls


VIEWS = {(name, policy): _view(name, policy) for name in NAMES for policy in POLICIES}
CASES = sorted(VIEWS)
IDS = ["%s-%s" % case for case in CASES]


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


def _request(method: str, key: Any, event: str = "", params: Any = None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(harness.PATH)
    else:
        request = factory.post(
            harness.PATH,
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=harness.TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


def _saved(key: str) -> list:
    return [n for n in SessionStore(key).keys() if n.startswith("_djust_auto_")]


@pytest.mark.django_db
@pytest.mark.parametrize("name, policy", CASES, ids=IDS)
def test_a_page_get_and_a_fallback_post(name, policy):
    view_class = VIEWS[(name, policy)]
    with harness.ws_settings(MODULE):
        first = _request("GET", None)
        page = view_class().get(first)
        assert page.status_code == 200
        assert "n=0" in page.content.decode()
        key = first.session.session_key
        assert _saved(key), "the GET saved nothing"
        view = view_class()
        response = view.post(_request("POST", key, "shadow_add"))
        assert response.status_code == 200, response.content
    assert list(dict.get(view.w.state, name)) == [1]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("name, policy", CASES, ids=IDS)
async def test_a_websocket_commit_and_a_reconnect(name, policy):
    view_class = VIEWS[(name, policy)]
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, mount = await harness.mount_view(view_class, key)
        try:
            assert mount["type"] != "error", mount
            frame = await harness.event(comm, "shadow_add")
            assert frame["type"] != "error", frame
        finally:
            await comm.disconnect()
        again, remount = await harness.mount_view(view_class, key)
        await again.disconnect()
    assert remount["type"] != "error", remount
    # The saved state is restored on the reconnect.
    assert "n=1" in remount["html"], remount["html"]


@pytest.mark.parametrize("name", NAMES)
def test_a_json_round_trip_through_the_plain_dict(name):
    """``json.dumps`` walks a dict subclass with its own ``items()``, which the
    field replaces; ``state_dict()`` is the plain dict djust encodes instead."""
    from djust.components.mixins.base import state_dict

    state_cls = _component(name).State
    state = state_cls(**{name: [1, 2]})
    if name == "items":
        with pytest.raises(TypeError):
            json.dumps(state)  # the stdlib's encoder calls state.items()
    text = json.dumps(state_dict(state))
    again = state_cls.from_dict(json.loads(text))
    assert dict.get(again, name) == [1, 2]
    assert getattr(again, name) == [1, 2]


@pytest.mark.parametrize("name", ["get", "update"])
def test_get_and_update_are_refused_when_the_class_is_created(name):
    with pytest.raises(TypeError) as raised:
        type("Bad", (TypedState,), {"__annotations__": {name: list}, name: ()})
    message = str(raised.value)
    assert "Bad.%s: a TypedState field cannot be named %r" % (name, name) in message
    assert "self.%s()" % name in message


@pytest.mark.parametrize("name", NAMES)
def test_every_other_dict_method_name_is_a_field(name):
    state = _component(name).State(**{name: [3]})
    assert getattr(state, name) == [3]
    assert dict.items(state) and dict(state) == {name: [3]}


# --- every generic walker a state can reach ----------------------------------


def _both() -> Any:
    """A state whose fields shadow BOTH ``items`` and ``values``."""
    cls = type(
        "BothState",
        (TypedState,),
        {"__annotations__": {"items": list, "values": list}, "items": (), "values": ()},
    )
    return cls(items=[1, 2], values=[3])


def _walkers() -> dict:
    import importlib

    from djust import serialization, template_bridge_floor, template_libraries

    live_view = importlib.import_module("djust.live_view")  # ``djust.live_view`` is also a name
    from djust.mixins import context, rust_bridge, sticky
    from djust.template import serialization as template_serialization

    plain = {"items": [1, 2], "values": [3]}

    def collect(state):
        found: set = set()
        rust_bridge._collect_sub_ids(state, found)
        return id(dict.get(state, "items")) in found

    return {
        "normalize_django_value": (lambda s: serialization.normalize_django_value(s) == plain),
        "normalize_django_value(state_roundtrip)": (
            lambda s: serialization.normalize_django_value(s, state_roundtrip=True) == plain
        ),
        "_protect_sidecar_tree": lambda s: serialization._protect_sidecar_tree(s) == plain,
        "_filter_floor_keys": (
            lambda s: (
                serialization._filter_floor_keys(s, lambda k: k != "values") == {"items": [1, 2]}
            )
        ),
        "holds_form": lambda s: serialization.holds_form(s) is False,
        "encode_private_model_refs": lambda s: serialization.encode_private_model_refs(s) == plain,
        "decode_private_model_refs": lambda s: serialization.decode_private_model_refs(s) == plain,
        "live_view._holds_model": lambda s: live_view._holds_model(s) is False,
        "rust_bridge._collect_sub_ids": collect,
        "context._holds_db_value": lambda s: context._holds_db_value(s) is False,
        "context._contains_orm_value": lambda s: context._contains_orm_value(s, set()) is False,
        "context._is_json_serializable": lambda s: context._is_json_serializable(s) is True,
        "template.serialization.serialize_value": (
            lambda s: template_serialization.serialize_value(s) == plain
        ),
        "sticky._canonical_kwarg": lambda s: isinstance(sticky._canonical_kwarg(s), str),
        "template_libraries._materialize_lazy": (
            lambda s: template_libraries._materialize_lazy(s) == plain
        ),
        "template_bridge_floor._same_contents": (
            lambda s: template_bridge_floor._same_contents(s, dict(plain)) is True
        ),
        "json.dumps(normalize_django_value)": (
            lambda s: (
                json.loads(json.dumps(serialization.normalize_django_value({"w": s})))
                == {"w": plain}
            )
        ),
    }


@pytest.mark.parametrize("walker", sorted(_walkers()))
def test_a_walker_reads_a_state_whose_fields_shadow_items_and_values(walker):
    assert _walkers()[walker](_both()), walker


def test_a_multivaluedict_keeps_its_own_items():
    """The helpers fall back to ``dict.items`` only for a property: Django's
    ``MultiValueDict.items()`` (the last value of each key) is still called."""
    from django.utils.datastructures import MultiValueDict

    from djust._mapping import mapping_items, mapping_values

    data = MultiValueDict({"a": ["1", "2"]})
    assert list(mapping_items(data)) == [("a", "2")]
    assert list(mapping_values(data)) == ["2"]
