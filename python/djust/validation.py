"""
Event handler parameter validation utilities.

Provides runtime validation of event handler signatures including:
- The strict contract: only parameters the signature declares are accepted
- Required parameter checking and unexpected parameter refusal
- Conversion to the declared type hints (``coerce_types``)
- Clear error message generation
"""

import inspect
import logging
import types
import threading
import weakref
from typing import Any, Callable, Dict, Optional, Union, get_type_hints, get_origin, get_args

from djust.security import sanitize_for_log
from ._parameter_contract import ROUTED_COMPONENT_PARAMETER, ParameterContract

logger = logging.getLogger(__name__)

_STRICT_CONTRACTS: weakref.WeakKeyDictionary[
    Any, Dict[tuple[bool, frozenset[str]], ParameterContract]
] = weakref.WeakKeyDictionary()
_STRICT_CONTRACT_LOCK = threading.RLock()
_MISSING_POSITIONAL = object()
_SIGNATURE_OWNER = object()


def declared_framework_arguments(handler: Callable) -> frozenset[str]:
    """The framework-supplied parameters a framework-owned handler declares (#4185).

    Only the component ``Meta.event`` alias declares one, ``component_id``
    (``components/base.py``), as a server-only function attribute. Its value
    is still a client string, checked against the view's declared component
    names (:func:`bind_declared_component_name`). Any other name in the
    marker is ignored, so nothing but ``component_id`` can become a
    framework-supplied argument this way.
    """
    function = getattr(handler, "__func__", handler)
    names = getattr(function, "_djust_framework_arguments", None)
    if type(names) is not frozenset:
        return frozenset()
    return names & {ROUTED_COMPONENT_PARAMETER}


def get_strict_handler_contract(
    handler: Callable, trusted: Optional[frozenset[str]] = None
) -> ParameterContract:
    """Cache declarations, never bound methods or their live owner instances.

    ``trusted`` names framework-injected parameters (ADR-036 D5); see
    ``ParameterContract.compile``. Client-callable handlers use none, except
    the framework-supplied ones a framework-owned handler declares
    (:func:`declared_framework_arguments`), which are the default.
    """
    if trusted is None:
        trusted = declared_framework_arguments(handler)
    key = (inspect.ismethod(handler), frozenset(trusted))
    function = handler.__func__ if inspect.ismethod(handler) else handler
    if not inspect.isfunction(function):
        return ParameterContract.compile(handler, key[1])
    with _STRICT_CONTRACT_LOCK:
        variants = _STRICT_CONTRACTS.setdefault(function, {})
        if key not in variants:
            variants[key] = ParameterContract.compile(handler, key[1])
        return variants[key]


def get_handler_coercion(handler: Callable) -> bool:
    """One coercion setting for events and deliberately exposed server functions."""
    decorators = getattr(handler, "_djust_decorators", {})
    metadata = decorators.get("event_handler", decorators.get("server_function", {}))
    return bool(metadata.get("coerce_types", True))


def validated_call_arguments(validation: Dict[str, Any]) -> tuple[tuple[Any, ...], Dict[str, Any]]:
    """Consume the validated call plan."""
    if not validation.get("valid"):
        raise ValueError("Cannot invoke an invalid handler call.")
    bound = validation.get("bound_arguments")
    if bound is not None:
        return bound.args, bound.kwargs
    return (), validation.get("coerced_params", {})


def bind_declared_component_name(handler: Callable, params: Any) -> tuple[Any, Dict[str, Any]]:
    """Take the event's ``component_id`` out of ``params`` for a handler that
    declares it framework-supplied (the component ``Meta.event`` alias, #4185).

    The ``component_id`` is a string the CLIENT sent, not a server object and
    not a value the server minted. What makes it safe to pass to the alias is
    only this check: it must be a ``str`` that is either empty (the alias then
    resolves the single component of its type) or one of the component names
    the handler's owner class declares (``_component_descriptors``), the rule
    ``_component_state.resolve_component_target`` applies before an event
    reaches the alias over WebSocket, SSE and the HTTP fallback. Any other
    value is refused, as strict refuses a routing target the route did not
    consume. The alias still resolves the name itself, against the same
    declared names.

    Returns the remaining payload and ``{"component_id": <checked name>}``.
    """
    from ._parameter_contract import ParameterError

    if type(params) is not dict:
        return params, {ROUTED_COMPONENT_PARAMETER: ""}
    remaining = dict(params)
    target = remaining.pop(ROUTED_COMPONENT_PARAMETER, "")
    if target is None:
        target = ""
    owner = getattr(handler, "__self__", None)
    declared = getattr(type(owner), "_component_descriptors", None) if owner is not None else None
    if type(target) is not str or (
        target and (not isinstance(declared, dict) or target not in declared)
    ):
        raise ParameterError("The event's routing target is not handled by this route.")
    return remaining, {ROUTED_COMPONENT_PARAMETER: target}


def _flat_arguments(bound: Any) -> Dict[str, Any]:
    """``bound.arguments`` with a ``**kwargs`` parameter's entries spread into
    the mapping, the shape legacy ``coerced_params`` always had: the params the
    handler received by name, which waiters and hooks are handed (``{"a": 1}``,
    not ``{"kwargs": {"a": 1}}``)."""
    flat: Dict[str, Any] = {}
    parameters = bound.signature.parameters
    for name, value in bound.arguments.items():
        if parameters[name].kind is inspect.Parameter.VAR_KEYWORD:
            flat.update(value)
        elif parameters[name].kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        else:
            flat[name] = value
    return flat


def _validate_strict_handler_params(
    handler: Callable, params: Dict[str, Any], coerce: bool, positional_args: Any
) -> Dict[str, Any]:
    from ._parameter_contract import (
        FRAMEWORK_ARGUMENT_NAMES,
        TRANSPORT_METADATA_KEYS,
        ParameterError,
    )

    contract = get_strict_handler_contract(handler)
    expected = [item["name"] for item in contract.metadata()]
    positional = () if positional_args is _MISSING_POSITIONAL else positional_args
    trusted: Dict[str, Any] = {}
    try:
        if type(params) is dict and any(key in TRANSPORT_METADATA_KEYS for key in params):
            # D5: transport bookkeeping is dispatch context, never an argument.
            params = {k: v for k, v in params.items() if k not in TRANSPORT_METADATA_KEYS}
        if ROUTED_COMPONENT_PARAMETER in contract.trusted:
            params, trusted = bind_declared_component_name(handler, params)
        if type(params) is dict and any(key in FRAMEWORK_ARGUMENT_NAMES for key in params):
            # The route that received this event did not consume its target.
            raise ParameterError("The event's routing target is not handled by this route.")
        if type(params) is dict and "_args" in params:
            if positional_args is not _MISSING_POSITIONAL:
                raise ParameterError("Positional arguments were supplied twice.")
            params = params.copy()
            positional = params.pop("_args")
        bound = contract.bind(params, positional, coerce=coerce, trusted=trusted)
    except ParameterError as exc:
        # Never return the original payload or arbitrary client-supplied keys in
        # an error envelope. Declared names and fixed type labels are sufficient.
        if type(params) is dict and logger.isEnabledFor(logging.DEBUG):
            # Server log only, and only at DEBUG: the names a client sent that
            # the handler does not declare. Never part of the response.
            undeclared = contract.undeclared_keys(params)
            if undeclared:
                logger.debug(
                    "Strict handler %s refused undeclared argument names: %s",
                    getattr(handler, "__qualname__", "<handler>"),
                    sanitize_for_log(", ".join(undeclared[:10]), max_length=200),
                )
        return {
            "valid": False,
            "error": exc.public_message,
            "expected": expected,
            "provided": [],
            "missing": list(exc.missing),
            "type_errors": None,
            "coerced_params": {},
        }
    return {
        "valid": True,
        "error": None,
        "expected": expected,
        "provided": list(_flat_arguments(bound)),
        "type_errors": None,
        "coerced_params": _flat_arguments(bound),
        "bound_arguments": bound,
    }


def validate_handler_params(
    handler: Callable,
    params: Dict[str, Any],
    event_name: str,
    coerce: Optional[bool] = None,
    positional_args: Any = _MISSING_POSITIONAL,
) -> Dict[str, Any]:
    """Validate event parameters against the handler's strict contract.

    A parameter reaches the handler only if its signature declares it. Values
    are converted to the declared type hints (unless ``coerce`` is false) and
    positional arguments from inline handler syntax such as
    ``dj-click="handler('value')"`` are mapped to parameters by position.

    Args:
        handler: Event handler method to validate against
        params: Parameters provided by the client event
        event_name: Name of the event (for error messages)
        coerce: Whether to convert values; None uses the decorator's
            ``coerce_types`` setting (default True).
        positional_args: Optional list of positional arguments from inline
            handler syntax (e.g. ``['value']``)

    Returns:
        Dict with ``valid``, ``error``, ``expected``, ``provided``,
        ``type_errors``, ``coerced_params`` and, when valid, a server-only
        ``bound_arguments`` (``inspect.BoundArguments``) that invokers consume
        through :func:`validated_call_arguments`; it is never serialized.

    Example:
        >>> class V:
        ...     def my_handler(self, value: str, count: int = 0):
        ...         pass
        >>> result = validate_handler_params(V().my_handler, {}, "my_event")
        >>> assert result["valid"] is False
        >>> result = validate_handler_params(V().my_handler, {}, "my_event", positional_args=["hello"])
        >>> assert result["valid"] is True
        >>> assert result["coerced_params"]["value"] == "hello"
    """
    if coerce is None:
        coerce = get_handler_coercion(handler)
    return _validate_strict_handler_params(handler, params, coerce, positional_args)


def _type_display_name(hint: Any) -> str:
    """Human-readable name for a type hint, including PEP 604 unions.

    >>> _type_display_name(str)
    'str'
    >>> _type_display_name(str | None)
    'str | None'
    >>> _type_display_name(Optional[int])
    'int | None'
    """
    if isinstance(hint, types.UnionType):
        return " | ".join(_single_type_name(arg) for arg in hint.__args__)
    origin = get_origin(hint)
    if origin is Union:
        return " | ".join(_single_type_name(arg) for arg in get_args(hint))
    return _single_type_name(hint)


def _single_type_name(t: Any) -> str:
    """Display name for a single type, mapping NoneType → 'None'."""
    if t is type(None):
        return "None"
    return getattr(t, "__name__", str(t))


def get_handler_signature_info(
    handler: Callable, *, for_declaration: bool = False
) -> Dict[str, Any]:
    """
    Extract comprehensive signature information from handler.

    Used by debug panel and @event_handler decorator.

    Args:
        handler: Event handler method
        for_declaration: Introspect the signature as written (decoration time)
            instead of compiling the strict contract.

    Returns:
        Dict containing:
        - params: List of parameter dicts with name, type, required
        - description: Handler docstring
        - accepts_kwargs: Whether handler accepts **kwargs

    Example:
        >>> def handler(self, value: str = "", count: int = 0, **kwargs):
        ...     '''Search items'''
        ...     pass
        >>> info = get_handler_signature_info(handler)
        >>> assert len(info["params"]) == 2
        >>> assert info["params"][0]["name"] == "value"
        >>> assert info["params"][0]["type"] == "str"
        >>> assert info["params"][0]["required"] is False
        >>> assert info["description"] == "Search items"
        >>> assert info["accepts_kwargs"] is True
    """
    sig = inspect.signature(handler)
    if not for_declaration:
        # Class-level schema/check consumers see unbound methods. Bind only a
        # sentinel, never construct a view or run its mount for introspection.
        target = handler
        first = next(iter(sig.parameters), None)
        if inspect.isfunction(handler) and first in ("self", "cls"):
            target = types.MethodType(handler, _SIGNATURE_OWNER)
        contract = get_strict_handler_contract(target)
        parameters = contract.metadata()
        return {
            "params": [p for p in parameters if p["kind"] not in ("var_keyword", "var_positional")],
            "description": inspect.getdoc(handler) or "",
            "accepts_kwargs": any(p["kind"] == "var_keyword" for p in parameters),
        }

    try:
        type_hints = get_type_hints(handler)
    except Exception:
        type_hints = {}

    params = []
    accepts_kwargs = False

    for name, param in sig.parameters.items():
        if name == "self":
            continue

        if param.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_kwargs = True
            continue

        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        param_info = {
            "name": name,
            "type": _type_display_name(type_hints.get(name, Any)) if name in type_hints else "Any",
            "required": param.default == inspect.Parameter.empty,
        }
        params.append(param_info)

    return {
        "params": params,
        "description": inspect.getdoc(handler) or "",
        "accepts_kwargs": accepts_kwargs,
    }
