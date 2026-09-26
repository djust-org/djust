"""Private ADR-034 declaration compiler; not a public component/emit API.

Only server-owned class declarations participate. Compilation never instantiates
components, evaluates properties or captures bound owner methods. Transport
guards recognize subscriptions independently of the legacy event-security mode.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
import inspect
import types
from typing import TypeVar, Union, get_args, get_origin, get_type_hints


F = TypeVar("F")
_MARKER = "_djust_component_subscriptions"
#: Class attribute holding the owner's ``{name: ComponentDeclaration}`` registry.
DECLARATIONS_ATTR = "_component_declarations"
#: The callback parameter the framework fills with the originating bound
#: component (ADR-034 D1). It is trusted dispatch context (ADR-036 D5): output
#: payloads cannot use the name, and no client key or positional value binds it.
SOURCE_PARAMETER = "component"


@dataclass(frozen=True)
class OutputContract:
    name: str
    payload: tuple[tuple[str, type], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, tuple) or any(
            not isinstance(item, tuple) or len(item) != 2 for item in self.payload
        ):
            raise TypeError("Output payload contracts require immutable name/type pairs")
        if not self.name.isidentifier() or self.name.startswith("_"):
            raise TypeError("Output names must be public Python identifiers")
        names = [name for name, _ in self.payload]
        if len(set(names)) != len(names) or any(
            not name.isidentifier() or name.startswith("_") or name == SOURCE_PARAMETER
            for name in names
        ):
            raise TypeError(
                "Output payload names must be unique public identifiers other than component"
            )
        if any(not isinstance(kind, type) for _, kind in self.payload):
            raise TypeError("Output payload contracts require concrete types")


class ComponentDeclaration:
    """Ownership/contract record to be specialized by concrete bindings.

    This internal base deliberately has no descriptor proxy or transport action.
    Concrete components must implement typed per-owner binding separately.
    """

    def __init__(self, component_type: type, outputs: tuple[OutputContract, ...]) -> None:
        if not isinstance(component_type, type):
            raise TypeError("A component declaration requires a concrete component type")
        if not isinstance(outputs, tuple) or any(
            not isinstance(item, OutputContract) for item in outputs
        ):
            raise TypeError(
                "A component declaration requires an immutable tuple of output contracts"
            )
        if len({output.name for output in outputs}) != len(outputs):
            raise TypeError("Duplicate output declaration")
        self._component_type = component_type
        self._outputs = outputs
        self._owner: type | None = None
        self._name = ""

    @property
    def component_type(self) -> type:
        return self._component_type

    @property
    def outputs(self) -> tuple[OutputContract, ...]:
        return self._outputs

    def __set_name__(self, owner: type, name: str) -> None:
        if self._owner is not None and (self._owner is not owner or self._name != name):
            raise TypeError(
                "Component declarations cannot be aliased or reused by unrelated owners"
            )
        if not name.isidentifier() or name.startswith("_"):
            raise TypeError("Component declarations require a public attribute name")
        self._owner, self._name = owner, name
        # ADR-038 E2-7: record the declaration for the explicit component
        # provider. A separate registry from LiveComponent's
        # ``_component_descriptors`` so the legacy paths that read that one
        # (event fall-through, optimistic rules) see exactly what they did.
        registry = owner.__dict__.get(DECLARATIONS_ATTR)
        if registry is None:
            registry = dict(getattr(owner, DECLARATIONS_ATTR, None) or {})
            setattr(owner, DECLARATIONS_ATTR, registry)
        registry[name] = self


@dataclass(frozen=True)
class _Subscription:
    declaration: ComponentDeclaration
    output: OutputContract


@dataclass(frozen=True)
class SubscriptionBinding:
    """Server-code names only; no instance or callable is retained."""

    component: str
    output: str
    callback: str


def is_component_subscription(callback: object) -> bool:
    if isinstance(callback, types.MethodType):
        callback = callback.__func__
    return isinstance(callback, types.FunctionType) and bool(callback.__dict__.get(_MARKER))


def _transport_exposed(callback: types.FunctionType) -> bool:
    metadata = callback.__dict__.get("_djust_decorators", {})
    return bool(metadata.get("event_handler") or metadata.get("server_function"))


def subscribe(declaration: ComponentDeclaration, output: OutputContract, callback: F) -> F:
    """Attach a trusted declaration token, preserving the original callable.

    Concrete typed output decorators call this private helper. Applications must
    not use it as a string-based alternative to the source-owned namespace.
    """
    original = callback
    if not isinstance(callback, types.FunctionType):
        raise TypeError("A component subscription requires an ordinary instance method")
    if _transport_exposed(callback):
        raise TypeError(
            "A component subscription cannot also be an event handler or server function"
        )
    if not any(candidate is output for candidate in declaration.outputs):
        raise TypeError("Subscription token is not a declared output of this component")
    previous = callback.__dict__.get(_MARKER, ())
    if any(item.declaration is declaration and item.output is output for item in previous):
        raise TypeError("Duplicate subscription on the same callback")
    setattr(callback, _MARKER, previous + (_Subscription(declaration, output),))
    setattr(callback, "alters_data", True)
    return original


def _accepts(annotation: object, expected: type) -> bool:
    # Unknown/Any annotations must not silently remove runtime diagnostics.
    return (
        isinstance(annotation, type)
        and annotation is not types.NoneType
        and issubclass(expected, annotation)
    )


def _valid_result(annotation: object) -> bool:
    if annotation is types.NoneType:
        return True
    origin = get_origin(annotation)
    if origin is Awaitable:
        return get_args(annotation) == (types.NoneType,)
    if origin in (types.UnionType, Union):
        return all(_valid_result(item) for item in get_args(annotation))
    return False


def _validate_callback(
    owner: type,
    binding: SubscriptionBinding,
    declaration: ComponentDeclaration,
    output: OutputContract,
    callback: object,
) -> types.FunctionType:
    location = (
        f"{owner.__qualname__}.{binding.component}.{binding.output} callback {binding.callback}"
    )
    if not isinstance(callback, types.FunctionType):
        raise TypeError(f"{location} must be an ordinary instance method")
    if _transport_exposed(callback):
        raise TypeError(f"{location}: a subscription cannot also be transport-exposed")
    expected = {SOURCE_PARAMETER: declaration.component_type, **dict(output.payload)}
    description = ", ".join(f"{name}: {kind.__name__}" for name, kind in expected.items())
    problem = f"{location} must accept ({description}) and return None or Awaitable[None]"
    try:
        signature = inspect.signature(callback)
        parameters = list(signature.parameters.values())
        if not parameters or parameters[0].kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            raise TypeError(problem)
        signature.bind(None, **dict.fromkeys(expected))
        hints = get_type_hints(callback, localns={**vars(owner), owner.__name__: owner})
        if any(not _accepts(hints.get(name), kind) for name, kind in expected.items()):
            raise TypeError(problem)
        if not _valid_result(hints.get("return")):
            raise TypeError(problem)
    except Exception:
        # Do not echo values/annotation evaluation failures from application code.
        raise TypeError(problem) from None
    return callback


def compile_subscriptions(owner: type) -> tuple[SubscriptionBinding, ...]:
    """Validate effective declarations/methods, including inherited replacements.

    Walk class dictionaries, not getattr, so properties/descriptors are not run.
    Revalidate inherited subscriptions against the effective component and method.
    No partial registry is installed on failure.
    """
    effective: dict[str, object] = {}
    subscriptions: dict[tuple[str, str], SubscriptionBinding] = {}
    for cls in owner.__mro__:
        for name, member in vars(cls).copy().items():  # snapshot (#3151)
            effective.setdefault(name, member)
            # Type checks only: a class attribute may be lazy (``SimpleLazyObject``
            # proxies ``__class__``, so ``isinstance`` would evaluate it).
            function = (
                member.__func__ if issubclass(type(member), (staticmethod, classmethod)) else member
            )
            if not issubclass(type(function), types.FunctionType):
                continue
            for subscription in function.__dict__.get(_MARKER, ()):
                source = subscription.declaration
                if source._owner not in owner.__mro__:
                    raise TypeError(
                        f"{owner.__qualname__}.{name}: subscription uses a foreign declaration"
                    )
                key = (source._name, subscription.output.name)
                binding = SubscriptionBinding(*key, name)
                previous = subscriptions.get(key)
                if previous is not None and previous.callback != name:
                    raise TypeError(
                        f"Duplicate subscription for {owner.__qualname__}.{key[0]}.{key[1]}"
                    )
                subscriptions[key] = binding

    callbacks: dict[types.FunctionType, list[_Subscription]] = {}
    for binding in subscriptions.values():
        declaration = effective.get(binding.component)
        location = f"{owner.__qualname__}.{binding.component}.{binding.output}"
        if not isinstance(declaration, ComponentDeclaration):
            raise TypeError(f"{location}: subscribed component declaration was removed")
        output = next((item for item in declaration.outputs if item.name == binding.output), None)
        if output is None:
            raise TypeError(f"{location}: replacement component does not declare this output")
        callback = _validate_callback(
            owner, binding, declaration, output, effective.get(binding.callback)
        )
        callbacks.setdefault(callback, []).append(_Subscription(declaration, output))

    # Overrides must carry the marker even when they don't repeat the decorator.
    # Only stamp after every check passed. Original declaration tokens remain on
    # their defining function; inherited routing stays attached to the name.
    for callback, inherited in callbacks.items():
        setattr(callback, "alters_data", True)
        if not is_component_subscription(callback):
            setattr(callback, _MARKER, tuple(inherited))
    return tuple(subscriptions.values())
