"""Public contracts retain the native dispatch owner, without reading state."""

import json
from types import SimpleNamespace

import pytest

from djust import LiveView, event_handler
from djust.decorators import server_function
from djust.components.descriptors.base import LiveComponent, TypedState


class StrictOwner:
    @event_handler(parameter_policy="strict")
    def choose(self, value: int, *, enabled: bool = False):
        raise AssertionError("must not execute")

    @property
    def secret(self):
        raise AssertionError("must not inspect a property")


class DefaultedOwner:
    @event_handler
    def choose(self, value: str = "SECRET_DEFAULT", **kwargs):
        raise AssertionError("must not execute")


def manifest(root):
    from djust._parameter_metadata import parameter_contract_manifest

    return parameter_contract_manifest(root)


def owners_of(data):
    from djust._parameter_metadata import expand_parameter_manifest

    return expand_parameter_manifest(data)


def test_same_named_handlers_remain_owned_and_defaults_are_absent():
    left, right = StrictOwner(), DefaultedOwner()
    root = SimpleNamespace(_components={"left": left, "right": right}, _child_views={})
    data = manifest(root)
    owners = {(o["view_id"], o["component_id"]): o["handlers"] for o in owners_of(data)}
    assert owners[(None, "left")]["choose"]["policy"] == "strict"
    assert owners[(None, "right")]["choose"]["policy"] == "strict"
    assert owners[(None, "right")]["choose"]["parameters"][0]["type"] == "str"
    assert owners[(None, "left")]["choose"]["parameters"][0]["type"] == "int"
    assert "SECRET" not in json.dumps(data)


def test_embedded_view_is_distinct_from_root_and_component():
    root = StrictOwner()
    root._components = {"same": DefaultedOwner()}
    root._child_views = {"same": DefaultedOwner()}
    data = manifest(root)
    assert [(o["view_id"], o["component_id"]) for o in owners_of(data)] == [
        (None, None),
        (None, "same"),
        ("same", None),
    ]


def test_rpc_and_undecorated_methods_are_not_dom_event_contracts():
    class Owner(StrictOwner):
        @server_function(parameter_policy="strict")
        def rpc(self, value: int):
            pass

        def ordinary(self):
            pass

    assert set(owners_of(manifest(Owner()))[0]["handlers"]) == {"choose"}


def test_shadowing_and_instance_handlers_match_python_resolution():
    class Owner(StrictOwner):
        @property
        def choose(self):
            raise AssertionError("must not evaluate shadowing property")

    assert manifest(Owner()) is None
    owner = Owner()
    owner.__dict__["another"] = event_handler(parameter_policy="strict")(lambda value: None)
    # Unsupported strict declarations fail, rather than falling back to legacy.
    from djust._parameter_contract import ContractError

    with pytest.raises(ContractError):
        manifest(owner)


def test_bound_descriptor_methods_use_bound_signature_without_state_values():
    class Menu(LiveComponent):
        class State(TypedState):
            selected: int = 0

        @event_handler(parameter_policy="strict")
        def choose(self, value: int):
            pass

    class Page(LiveView):
        exposure_policy = "explicit"
        left = Menu()
        right = Menu()

    page = Page()
    left, right = page.left, page.right
    assert left is not right
    data = manifest(page)
    components = [entry for entry in owners_of(data) if entry["component_id"]]
    assert len(components) == 2
    for entry in components:
        assert [p["name"] for p in entry["handlers"]["choose"]["parameters"]] == ["value"]


def test_disposed_children_are_not_advertised():
    root = StrictOwner()
    child = StrictOwner()
    child._djust_child_disposed = True
    root._child_views = {"gone": child}
    assert len(owners_of(manifest(root))) == 1


def test_wrapper_method_precedence_hides_descriptor_handler():
    class Menu(LiveComponent):
        class State(TypedState):
            selected: int = 0

        @event_handler(parameter_policy="strict")
        def get(self, value: int):
            raise AssertionError("not the callable resolved by dispatch")

    class Page(LiveView):
        exposure_policy = "explicit"
        menu = Menu()

    page = Page()
    from djust.decorators import is_event_handler

    assert not is_event_handler(page.menu.get)
    assert all(
        "get" not in owner["handlers"]
        for owner in owners_of(manifest(page))
        if owner["component_id"] == "menu"
    )


def test_rebuilding_manifest_tracks_removal_and_returns_detached_metadata():
    root = StrictOwner()
    root._components = {"menu": StrictOwner()}
    first = manifest(root)
    first["contracts"][0]["parameters"][0]["type"] = "CORRUPTED"
    root._components.clear()
    second = manifest(root)
    assert len(second["owners"]) == 1
    assert owners_of(second)[0]["handlers"]["choose"]["parameters"][0]["type"] == "int"


def test_bound_instance_shadowing_including_none_matches_dispatch():
    class Menu(LiveComponent):
        class State(TypedState):
            selected: int = 0

        @event_handler(parameter_policy="strict")
        def choose(self, value: int):
            pass

    class Page(LiveView):
        exposure_policy = "explicit"
        menu = Menu()

    page = Page()
    bound = page.menu
    object.__setattr__(bound, "choose", None)
    assert all(
        "choose" not in owner["handlers"]
        for owner in owners_of(manifest(page))
        if owner["component_id"] == "menu"
    )

    @event_handler(parameter_policy="strict")
    def replacement(value: str):
        pass

    object.__setattr__(bound, "choose", replacement)
    data = manifest(page)
    menu = next(o for o in owners_of(data) if o["component_id"] == "menu")
    assert menu["handlers"]["choose"]["parameters"][0]["type"] == "str"


@pytest.mark.parametrize(
    "registry,key", [("_components", None), ("_child_views", None), ("_child_views", "root")]
)
def test_ambiguous_owner_identity_is_not_advertised(registry, key):
    from djust._parameter_contract import ContractError

    root = StrictOwner()
    root._view_id = "root"
    setattr(root, registry, {key: StrictOwner()})
    with pytest.raises(ContractError, match="Ambiguous"):
        manifest(root)


def test_discovery_does_not_evaluate_a_shadowed_instance_dictionary():
    from djust._parameter_contract import ContractError

    calls = []

    class Owner(StrictOwner):
        @property
        def __dict__(self):
            calls.append(True)
            raise RuntimeError("SECRET_STORAGE")

    with pytest.raises(ContractError, match="ordinary instance storage"):
        manifest(Owner())
    assert calls == []
