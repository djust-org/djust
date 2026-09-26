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
import operator
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


# Stands in for a view instance when binding a method: compilation reads only
# the declaration, and a bound method shares the runtime's cache entry.
DECLARATION_OWNER = object()


def declaration_method(member: Any, function: Any, cls: type) -> Any:
    """The callable shape dispatch compiles for a declaration: bound unless a
    staticmethod, never to a constructed view."""
    if isinstance(member, staticmethod):
        return function
    if isinstance(member, classmethod):
        return types.MethodType(function, cls)
    return types.MethodType(function, DECLARATION_OWNER)


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
    decorated: bool = False,
) -> Iterator[DeclaredHandler]:
    """The public event handlers ``cls`` declares, nearest first, in MRO order.

    The same resolution dispatch uses, over the class alone: a nearer attribute
    shadows a farther one even when it is not a handler, and only Python's own
    method types count. Nothing is instantiated and no descriptor is called.
    ``server_functions`` also yields ``@server_function`` methods, and
    ``decorated`` any public method carrying djust decorator metadata (a
    ``@debounce`` without ``@event_handler`` still publishes client metadata).
    """
    for name, (member, owner) in _class_members(cls, stop).items():
        if name.startswith("_") or type(member) not in _METHOD_TYPES:
            continue
        function = _function(member)
        if (
            is_event_handler(function)
            or (server_functions and is_server_function(function))
            or (decorated and "_djust_decorators" in vars(function))
        ):
            yield DeclaredHandler(name, member, function, owner)


def _mro_lookup(cls: type, name: str) -> Any:
    """The class-level half of ``inspect.getattr_static``: the first entry
    for ``name`` in ``cls.__mro__``, or ``_ABSENT``."""
    for base in cls.__mro__:
        found = base.__dict__.get(name, _ABSENT)
        if found is not _ABSENT:
            return found
    return _ABSENT


def _is_data_descriptor(value: Any) -> bool:
    """``inspect.getattr_static``'s test: ``__get__`` plus ``__set__`` OR
    ``__delete__`` on the value's type. Either one makes the class attribute
    win over the instance dictionary."""
    kind = type(value)
    return _mro_lookup(kind, "__get__") is not _ABSENT and (
        _mro_lookup(kind, "__set__") is not _ABSENT
        or _mro_lookup(kind, "__delete__") is not _ABSENT
    )


# ``Py_TPFLAGS_IMMUTABLETYPE``: built-in and extension types whose attributes
# cannot be set, so a snapshot of them can never go stale.
_IMMUTABLE_TYPE = 1 << 8


def _dict_snapshot(cls: type) -> tuple[type, tuple[str, ...], tuple[Any, ...]]:
    namespace = cls.__dict__
    return (cls, tuple(namespace), tuple(namespace.values()))


class _ClassPlan:
    """Everything ``_event_methods`` needs from the CLASSES, resolved once.

    ``inspect.getattr_static`` walks the MRO for every public name, and the
    manifest is rebuilt on every render (#3075): for a typical LiveView that
    was ~1 ms per frame, longer than the render itself. Only the instance
    storage varies between calls, so the class half is resolved here and the
    instance half is re-applied per call, exactly as ``getattr_static`` would.

    ``fresh()`` re-checks both MROs (a reassigned ``__bases__``) and every
    class dict in them, keys in order and values by identity, plus the classes
    of the descriptors the plan resolved. A hot view replacement (a new
    class), a monkeypatched, added, deleted or swapped attribute therefore
    rebuilds the plan instead of serving a stale one.
    """

    __slots__ = ("entries", "mros", "names", "snapshot")

    def __init__(self, owner_type: type, declaration_type: type, bound_component: bool) -> None:
        # Snapshot BEFORE resolving: a class changed while the plan is being
        # built then fails the very next ``fresh()`` rather than never.
        self.mros = ((owner_type, owner_type.__mro__), (declaration_type, declaration_type.__mro__))
        classes = dict.fromkeys(owner_type.__mro__ + declaration_type.__mro__)
        snapshot = [_dict_snapshot(cls) for cls in classes if not cls.__flags__ & _IMMUTABLE_TYPE]
        # The one class-level discovery (ADR-037 D1), shared with the checks.
        stop = component_stop if bound_component else view_stop
        members = {
            name: member for name, (member, _) in _class_members(declaration_type, stop).items()
        }
        entries = []
        for name, member in members.items():
            if name.startswith("_"):
                continue
            wrapper = _mro_lookup(owner_type, name)
            data = wrapper is not _ABSENT and _is_data_descriptor(wrapper)
            entries.append((name, member, wrapper, data))
        self.entries = tuple(entries)
        self.names = frozenset(members)
        # The data-descriptor verdicts depend on the descriptors' own classes
        # (their dicts AND their MROs). Immutable types (``str``, ``function``,
        # ``property``, ...) can never gain ``__set__``/``__delete__``, so they
        # are left out; they were most of the check's cost.
        mros = list(self.mros)
        for _name, _member, wrapper, _data in entries:
            if wrapper is _ABSENT:
                continue
            kind = type(wrapper)
            if kind.__flags__ & _IMMUTABLE_TYPE or kind in classes:
                continue
            mros.append((kind, kind.__mro__))
            for cls in kind.__mro__:
                if cls not in classes and not cls.__flags__ & _IMMUTABLE_TYPE:
                    classes[cls] = None
                    snapshot.append(_dict_snapshot(cls))
        self.mros = tuple(mros)
        self.snapshot = tuple(snapshot)

    def fresh(self) -> bool:
        for cls, mro in self.mros:
            if cls.__mro__ != mro:
                return False
        for cls, keys, values in self.snapshot:
            current = cls.__dict__
            if (
                len(current) != len(values)
                or tuple(current) != keys
                or not all(map(operator.is_, current.values(), values))
            ):
                return False
        return True


# Plans hold the classes (and their functions, whose ``__class__`` cells point
# back at the class), so a weak-keyed cache could never let a replaced class
# go. A bounded plain dict does: hot view replacement strands at most this many
# old plans, and an app with more (owner, declaration) pairs than this merely
# rebuilds plans, which is the pre-cache cost, never a wrong answer.
_PLAN_LIMIT = 512
_PLANS: dict[tuple[type, type, bool], _ClassPlan] = {}


def _class_plan(owner_type: type, declaration_type: type, bound_component: bool) -> _ClassPlan:
    key = (owner_type, declaration_type, bound_component)
    plan = _PLANS.get(key)
    if plan is None or not plan.fresh():
        plan = _ClassPlan(owner_type, declaration_type, bound_component)
        if len(_PLANS) >= _PLAN_LIMIT:
            _PLANS.clear()
        _PLANS[key] = plan
    return plan


def _resolve_method(
    owner: Any, name: str, member: Any, in_storage: bool, binding_class: type
) -> Any:
    """The dispatchable event handler ``member`` resolves to, or None."""
    if type(member) in _METHOD_TYPES:
        function = member.__func__ if type(member) in (staticmethod, classmethod) else member
        if not is_event_handler(function):
            return None
        # Only Python's known method descriptors are executed, never an
        # application property or custom descriptor during discovery.
        method = member if in_storage else member.__get__(owner, binding_class)
    elif type(member) is types.MethodType:
        method = member
    else:
        return None
    return method if is_event_handler(method) else None


def _event_methods(owner: Any) -> dict[str, Any]:
    from .components.base import BoundComponent

    bound_component = isinstance(owner, BoundComponent)
    storage = _instance_dict(owner)
    declaration = storage["_descriptor"] if bound_component else owner
    owner_type, declaration_type = type(owner), type(declaration)
    plan = _class_plan(owner_type, declaration_type, bound_component)

    methods = {}
    for name, member, wrapper, wrapper_is_data in plan.entries:
        # Dispatch resolves real wrapper attributes before forwarding to the
        # descriptor. This includes None and instance-assigned callables.
        # Same precedence as ``inspect.getattr_static(owner, name)``: a data
        # descriptor on the class, then the instance storage, then the class.
        in_storage = name in storage
        if in_storage and not wrapper_is_data:
            wrapper = storage[name]
        if wrapper is _ABSENT:
            method = _resolve_method(owner, name, member, in_storage, declaration_type)
        else:
            method = _resolve_method(owner, name, wrapper, in_storage, owner_type)
        if method is not None:
            methods[name] = method
    # Names only the instance holds. Anything but a function or method there
    # can never resolve to a handler, so only those take the full lookup.
    for name, value in storage.items():
        if (
            name.startswith("_")
            or name in plan.names
            or type(value) not in (_METHOD_TYPES + (types.MethodType,))
        ):
            continue
        wrapper = inspect.getattr_static(owner, name, _ABSENT)
        member = value if wrapper is _ABSENT else wrapper
        binding_class = declaration_type if wrapper is _ABSENT else owner_type
        method = _resolve_method(owner, name, member, True, binding_class)
        if method is not None:
            methods[name] = method

    if bound_component:
        meta = inspect.getattr_static(declaration_type, "Meta", None)
        event = inspect.getattr_static(meta, "event", None) if isinstance(meta, type) else None
        if (
            isinstance(event, str)
            and not event.startswith("_")
            and event not in plan.names
            and event not in storage
            and inspect.getattr_static(owner, event, _ABSENT) is _ABSENT
        ):
            methods[event] = owner._meta_event_handler(event)
    return methods


def handler_metadata(method: Any) -> dict[str, Any]:
    """The decorator metadata a bound handler publishes to the client.

    A strict handler's parameters come from its compiled contract (no server
    default values, and a copy, never the shared decorator dictionaries); a
    legacy handler's are the decorator's own.
    """
    decorators = method._djust_decorators
    if get_handler_parameter_policy(method) != "strict":
        return decorators
    metadata = dict(decorators)
    key = "event_handler" if "event_handler" in metadata else "server_function"
    if key in metadata:
        parameters = list(get_strict_handler_contract(method).metadata())
        item = dict(metadata[key])
        item.update(
            parameter_policy="strict",
            params=parameters,
            param_names=[p["name"] for p in parameters],
            required=[p["name"] for p in parameters if p["required"]],
            optional=[p["name"] for p in parameters if not p["required"]],
        )
        metadata[key] = item
    return metadata


def published_handlers(view: Any) -> dict[str, Any]:
    """Every decorated method a view publishes client metadata for, bound, by name.

    Event handlers are exactly the ones dispatch resolves (``_event_methods``);
    server functions and other decorated methods come from the same
    class-level discovery. No property or other descriptor is evaluated.
    """
    methods = dict(_event_methods(view))
    storage = _instance_dict(view)
    for handler in declared_handlers(type(view), view_stop, server_functions=True, decorated=True):
        if handler.name not in methods and handler.name not in storage:
            methods[handler.name] = handler.member.__get__(view, type(view))
    return {name: methods[name] for name in sorted(methods)}


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
