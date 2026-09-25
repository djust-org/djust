"""Versioned public parameter contracts addressed by native dispatch owner.

This module is also the one discovery of the handlers a class declares
(ADR-037 D1): dispatch's ``_event_methods`` and every check, audit and tool
that lists handlers resolve names through ``declared_handlers``.

Only declarations are inspected. No template context, defaults, properties,
mounts or event handlers are evaluated. This manifest is advisory client input,
never an authorization decision or a replacement for server validation.
"""

import inspect
import json
import types
from collections.abc import Callable, Iterator
from typing import Any, NamedTuple

from ._parameter_contract import ContractError
from .decorators import is_event_handler, is_server_function
from .validation import (
    get_handler_coercion,
    get_handler_parameter_policy,
    get_strict_handler_contract,
)

_ABSENT = object()


def _instance_dict(owner: Any) -> dict[str, Any]:
    descriptor = inspect.getattr_static(owner, "__dict__", _ABSENT)
    if descriptor is _ABSENT:
        return {}
    if type(descriptor) not in (types.GetSetDescriptorType, types.MemberDescriptorType):
        raise ContractError("Public contracts require ordinary instance storage.")
    return descriptor.__get__(owner, type(owner))


_METHOD_TYPES = (types.FunctionType, staticmethod, classmethod)


class DeclaredHandler(NamedTuple):
    """One handler a class exposes, as its declaration resolves it."""

    name: str
    member: Any  # The class attribute: a function, staticmethod or classmethod.
    function: types.FunctionType
    owner: type  # The class in the MRO whose attribute resolves the name.


def view_stop(klass: type) -> bool:
    """Views resolve handlers through their whole MRO."""
    return False


def component_stop(klass: type) -> bool:
    """Component handlers stop at the framework component bases."""
    from .components.base import LiveComponent

    return klass is LiveComponent or bool(klass.__dict__.get("_djust_framework_component_base"))


def _class_members(cls: type, stop: Callable[[type], bool]) -> dict[str, tuple[Any, type]]:
    """Every attribute ``cls`` declares up to ``stop``, nearest class first."""
    members: dict[str, tuple[Any, type]] = {}
    for klass in cls.__mro__:
        if stop(klass):
            break
        for name, member in klass.__dict__.items():
            members.setdefault(name, (member, klass))
    return members


def _function(member: Any) -> Any:
    return member.__func__ if type(member) in (staticmethod, classmethod) else member


def declared_handlers(
    cls: type,
    stop: Callable[[type], bool] = view_stop,
    *,
    server_functions: bool = False,
) -> Iterator[DeclaredHandler]:
    """The public event handlers ``cls`` declares, nearest first, in MRO order.

    The same resolution dispatch uses, over the class alone: a nearer attribute
    shadows a farther one even when it is not a handler, and only Python's own
    method types count. Nothing is instantiated and no descriptor is called.
    ``server_functions`` also yields ``@server_function`` methods.
    """
    for name, (member, owner) in _class_members(cls, stop).items():
        if name.startswith("_") or type(member) not in _METHOD_TYPES:
            continue
        function = _function(member)
        if is_event_handler(function) or (server_functions and is_server_function(function)):
            yield DeclaredHandler(name, member, function, owner)


def _event_methods(owner: Any) -> dict[str, Any]:
    from .components.base import BoundComponent

    bound_component = isinstance(owner, BoundComponent)
    storage = _instance_dict(owner)
    declaration = storage["_descriptor"] if bound_component else owner
    stop = component_stop if bound_component else view_stop
    members = {
        name: member for name, (member, _) in _class_members(type(declaration), stop).items()
    }
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
        if type(member) in _METHOD_TYPES:
            function = _function(member)
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


def _handlers(owner: Any) -> dict[str, Any]:
    result = {}
    for name, method in _event_methods(owner).items():
        policy = get_handler_parameter_policy(method)
        if policy == "legacy":
            result[name] = {"policy": "legacy"}
        else:
            result[name] = {
                "policy": "strict",
                "coerce_types": get_handler_coercion(method),
                "parameters": list(get_strict_handler_contract(method).metadata()),
            }
    return result


def parameter_contract_manifest(root: Any) -> dict[str, Any] | None:
    """Snapshot currently registered native owners for a mount frame.

    The root uses (None, None); root components use (None, component_id);
    registered embedded children use (view_id, None). Nested component routing
    is not implied: the current child dispatcher resolves handlers on the child.
    Rebuild on each call; no owner or mutable manifest is globally retained.
    """
    owners = []
    total_handlers = 0

    def add(owner: Any, view_id: str | None, component_id: str | None) -> None:
        nonlocal total_handlers
        handlers = _handlers(owner)
        total_handlers += len(handlers)
        owners.append({"view_id": view_id, "component_id": component_id, "handlers": handlers})

    add(root, None, None)
    storage = _instance_dict(root)
    for key, component in storage.get("_components", {}).items():
        add(component, None, key)
    for key, child in storage.get("_child_views", {}).items():
        if _instance_dict(child).get("_djust_child_disposed", False):
            continue
        add(child, key, None)
    if not any(h["policy"] == "strict" for o in owners for h in o["handlers"].values()):
        return None
    if len(owners) > 1024 or total_handlers > 10000:
        raise ContractError("Public parameter contract manifest exceeds the declaration limit.")
    if any(
        value is not None and (type(value) is not str or not value)
        for owner in owners
        for value in (owner["view_id"], owner["component_id"])
    ):
        raise ContractError("Invalid registered parameter contract owner.")
    root_id = storage.get("_view_id")
    if any(
        (owner["view_id"] is None and owner["component_id"] is None)
        or (root_id is not None and owner["view_id"] == root_id)
        for owner in owners[1:]
    ):
        raise ContractError("Ambiguous registered parameter contract owner.")
    manifest = {"version": 1, "owners": owners}
    if len(json.dumps(manifest, separators=(",", ":")).encode("utf-8")) > 65536:
        raise ContractError("Public parameter contract metadata limit exceeded.")
    return manifest
