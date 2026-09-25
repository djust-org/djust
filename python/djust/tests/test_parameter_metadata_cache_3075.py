"""The handler-discovery plan is cached per class (#3075).

``parameter_contract_manifest`` runs on every render. Resolving each public
name with ``inspect.getattr_static`` cost ~1 ms per frame on an ordinary
LiveView, longer than the render. The class half is now resolved once per
class and re-validated each call; these tests pin that the cached discovery
returns exactly what the uncached one did, and that no class change is missed.
"""

import inspect
import types
from typing import Any

import pytest

from djust import LiveView, event_handler
from djust import _parameter_metadata as pm
from djust._parameter_metadata import _ABSENT, _instance_dict, parameter_contract_manifest
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import is_event_handler


# The discovery exactly as it was before the cache (main @ d23acbf9f), kept
# verbatim as the oracle the cached one must agree with.
def reference_event_methods(owner: Any) -> dict[str, Any]:
    from djust.components.base import BoundComponent, LiveComponent

    bound_component = isinstance(owner, BoundComponent)
    storage = _instance_dict(owner)
    declaration = storage["_descriptor"] if bound_component else owner
    members: dict[str, Any] = {}
    for cls in type(declaration).__mro__:
        if bound_component and (
            cls is LiveComponent or cls.__dict__.get("_djust_framework_component_base")
        ):
            break
        for name, member in cls.__dict__.items():
            members.setdefault(name, member)
    for name, member in storage.items():
        members.setdefault(name, member)

    methods = {}
    for name in members:
        if name.startswith("_"):
            continue
        # Dispatch resolves real wrapper attributes before forwarding to the
        # descriptor. This includes None and instance-assigned callables.
        wrapper_member = inspect.getattr_static(owner, name, _ABSENT)
        member = members[name] if wrapper_member is _ABSENT else wrapper_member
        binding_class = type(declaration) if wrapper_member is _ABSENT else type(owner)
        if type(member) in (types.FunctionType, staticmethod, classmethod):
            function = member.__func__ if type(member) in (staticmethod, classmethod) else member
            if not is_event_handler(function):
                continue
            # Only Python's known method descriptors are executed, never an
            # application property or custom descriptor during discovery.
            if name in storage:
                method = member
            else:
                method = member.__get__(owner, binding_class)
        elif type(member) is types.MethodType:
            method = member
        else:
            continue
        if is_event_handler(method):
            methods[name] = method

    if bound_component:
        meta = inspect.getattr_static(type(declaration), "Meta", None)
        event = inspect.getattr_static(meta, "event", None) if isinstance(meta, type) else None
        if (
            isinstance(event, str)
            and not event.startswith("_")
            and event not in members
            and inspect.getattr_static(owner, event, _ABSENT) is _ABSENT
        ):
            methods[event] = owner._meta_event_handler(event)
    return methods


class Base(LiveView):
    @event_handler()
    def legacy(self, value: str = "", **kwargs):
        pass

    @event_handler(parameter_policy="strict")
    def strict(self, value: int):
        pass

    @event_handler(parameter_policy="strict")
    @staticmethod
    def static_strict(value: int):
        pass

    def undecorated(self):
        pass

    @property
    def prop(self):
        raise AssertionError("discovery must not evaluate a property")

    title = "plain class attribute"


class Child(Base):
    @event_handler(parameter_policy="strict")
    def legacy(self, value: int):  # overrides a base handler
        pass

    @property
    def strict(self):  # a shadowing property hides the base handler
        raise AssertionError("discovery must not evaluate a property")


class Menu(LiveComponent):
    class State(TypedState):
        selected: int = 0

    @event_handler(parameter_policy="strict")
    def choose(self, value: int):
        pass

    @event_handler(parameter_policy="strict")
    def get(self, value: int):  # hidden by the wrapper's own ``get``
        pass


class Page(LiveView):
    menu = Menu()

    @event_handler()
    def save(self, **kwargs):
        pass


@event_handler(parameter_policy="strict")
def assigned(value: str):
    pass


def owners():
    plain, child, page = Base(), Child(), Page()
    shadowed_none = Base()
    shadowed_none.legacy = None
    shadowed_fn = Child()
    shadowed_fn.title = assigned
    storage_only = Base()
    storage_only.extra = assigned
    storage_only.not_a_handler = lambda: None
    bound = Page().menu
    object.__setattr__(bound, "choose", None)
    return [plain, child, page, page.menu, shadowed_none, shadowed_fn, storage_only, bound]


def comparable(methods: dict) -> list:
    return [(name, getattr(m, "__func__", m)) for name, m in methods.items()]


@pytest.mark.parametrize("index", range(len(owners())))
def test_cached_discovery_matches_the_uncached_oracle(index):
    owner = owners()[index]
    expected = comparable(reference_event_methods(owner))
    assert comparable(pm._event_methods(owner)) == expected
    # Again from the warm plan, which is the per-render path.
    assert comparable(pm._event_methods(owner)) == expected


def test_a_warm_manifest_does_not_walk_the_mro_per_name(monkeypatch):
    """Regression for #3075: fails on the uncached discovery, which called
    ``inspect.getattr_static`` once per public name on every render."""
    view = Child()
    parameter_contract_manifest(view)  # builds the plan
    calls = []
    real = inspect.getattr_static
    monkeypatch.setattr(
        pm.inspect, "getattr_static", lambda *a, **k: calls.append(a[1]) or real(*a, **k)
    )
    for _ in range(3):
        parameter_contract_manifest(view)
    # Only the instance-storage probes (``__dict__``) remain; the uncached
    # discovery made one probe per public name, ~150 here, on every call.
    assert set(calls) == {"__dict__"} and len(calls) <= 2 * 3


def test_a_monkeypatched_handler_is_seen_at_once(monkeypatch):
    view = Base()
    assert "added" not in pm._event_methods(view)

    @event_handler(parameter_policy="strict")
    def added(self, value: int):
        pass

    monkeypatch.setattr(Base, "added", added, raising=False)
    assert "added" in pm._event_methods(view)

    @event_handler(parameter_policy="strict")
    def replacement(self, value: str):
        pass

    monkeypatch.setattr(Base, "strict", replacement)
    data = parameter_contract_manifest(view)
    assert data["owners"][0]["handlers"]["strict"]["parameters"][0]["type"] == "str"
    monkeypatch.delattr(Base, "strict")
    assert "strict" not in pm._event_methods(view)


def test_a_change_on_a_base_class_reaches_a_warm_subclass_plan(monkeypatch):
    view = Child()
    pm._event_methods(view)

    @event_handler()
    def from_base(self, **kwargs):
        pass

    monkeypatch.setattr(LiveView, "from_base", from_base, raising=False)
    assert "from_base" in pm._event_methods(view)


def test_hot_view_replacement_swaps_the_class_and_the_plan():
    view = Base()
    assert "strict" in pm._event_methods(view)

    class Replaced(LiveView):
        @event_handler()
        def only_new(self, **kwargs):
            pass

    view.__class__ = Replaced
    methods = pm._event_methods(view)
    assert "only_new" in methods and "strict" not in methods


def test_the_plan_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(pm, "_PLAN_LIMIT", 4)
    monkeypatch.setattr(pm, "_PLANS", {})
    for index in range(10):
        view = type(f"View{index}", (Base,), {})()
        pm._event_methods(view)
        assert len(pm._PLANS) <= 4
