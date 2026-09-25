"""Staged ADR-036 strict signature contract (not a dispatch-policy switch).

This module owns conversion, binding and value-free metadata together. Callers
must supply an already-bound callable and extract trusted dispatch context first.
It never invokes the callable, reads settings, logs payloads or resolves objects.
Legacy validation remains in validation.py until transport parity is implemented.
"""

import inspect
import math
import re
import types
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints
from uuid import UUID


# Fixed, transport-independent bounds for the initial strict contract. Wire byte
# limits still apply separately. Any bypasses type validation, not container bounds.
MAX_DEPTH = 32
MAX_NODES = 10000
MAX_COLLECTION = 1024
MAX_TEXT = 65536
MAX_NUMERIC_TEXT = 1024
MAX_INTEGER_BITS = 4096
MAX_DECIMAL_EXPONENT = 10000
_ASCII_SPACE = " \t\n\r\v\f"
_INTEGER = re.compile(r"[+-]?[0-9]+\Z")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SERVER_DEFAULT = object()


class ContractError(ValueError):
    """An unsupported developer-owned handler declaration."""


class ParameterError(ValueError):
    """An invalid invocation; messages contain no client keys or values.

    ``public_message`` is the text a client may be shown. It is set from the
    framework-written message at the raise site, so error envelopes read it
    rather than formatting the exception object.
    """

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


@dataclass(frozen=True)
class _Type:
    kind: Any
    label: str
    child: "_Type | None" = None

    @property
    def reduced_checking(self) -> bool:
        return self.kind is Any or bool(self.child and self.child.reduced_checking)


def _compile_type(annotation: Any, depth: int = 0) -> _Type:
    if depth > MAX_DEPTH:
        raise ContractError("Parameter annotation exceeds the nesting limit.")
    if annotation is Any:
        return _Type(Any, "Any")
    if any(annotation is item for item in (str, int, float, bool, Decimal, UUID, date)):
        return _Type(annotation, annotation.__name__)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType) and len(args) == 2 and type(None) in args:
        child = _compile_type(next(arg for arg in args if arg is not type(None)), depth + 1)
        return _Type("optional", f"Optional[{child.label}]", child)
    if origin is list and len(args) == 1:
        child = _compile_type(args[0], depth + 1)
        return _Type(list, f"list[{child.label}]", child)
    raise ContractError("Unsupported parameter annotation; use a supported type or explicit Any.")


def _check_budget(params: dict[str, Any], positional: list[Any] | tuple[Any, ...]) -> None:
    nodes = 0
    text_size = 0
    active: set[int] = set()

    def visit(value: Any, depth: int) -> None:
        nonlocal nodes, text_size
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ParameterError("Event arguments exceed the structural resource limit.")
        if issubclass(type(value), (list, tuple, dict, str, int)) and type(value) not in (
            list,
            tuple,
            dict,
            str,
            int,
            bool,
        ):
            # Any is an escape hatch, but a built-in subclass must not evade
            # bounds or execute attacker-defined iteration/conversion methods.
            raise ParameterError("Use built-in payload types rather than subclasses.")
        if type(value) is str:
            text_size += len(value)
            if text_size > MAX_TEXT:
                raise ParameterError("Event arguments exceed the text resource limit.")
        elif type(value) is int and value.bit_length() > MAX_INTEGER_BITS:
            raise ParameterError("Event arguments exceed the integer resource limit.")
        elif type(value) in (list, tuple, dict):
            if len(value) > MAX_COLLECTION or id(value) in active:
                raise ParameterError("Event arguments contain an oversized or cyclic collection.")
            active.add(id(value))
            try:
                if type(value) is dict:
                    for key, item in value.items():
                        visit(key, depth + 1)
                        visit(item, depth + 1)
                else:
                    for item in value:
                        visit(item, depth + 1)
            finally:
                active.remove(id(value))

    visit(params, 0)
    visit(positional, 0)


def _convert(value: Any, spec: _Type, coerce: bool) -> Any:
    kind = spec.kind
    if kind is Any:
        return value
    if kind == "optional":
        if value is None:
            return None
        assert spec.child is not None
        return _convert(value, spec.child, coerce)
    if kind is list:
        if type(value) is not list:
            raise ValueError
        assert spec.child is not None
        return [_convert(item, spec.child, coerce) for item in value]
    if type(value) is kind:
        if kind is float and not math.isfinite(value):
            raise ValueError
        if kind is Decimal:
            _check_decimal(value)
        return value
    if not coerce:
        raise ValueError
    if kind is float and type(value) is int:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
        return result
    if kind is Decimal and type(value) is int:
        result = Decimal(value)
        _check_decimal(result)
        return result
    if type(value) is not str or kind is str:
        raise ValueError
    text = value.strip(_ASCII_SPACE)
    if kind in (int, float, Decimal) and len(text) > MAX_NUMERIC_TEXT:
        raise ValueError
    if kind is int and _INTEGER.fullmatch(text):
        return int(text)
    if kind in (float, Decimal) and _NUMBER.fullmatch(text):
        if kind is float:
            number = float(text)
            if math.isfinite(number):
                return number
        else:
            decimal = Decimal(text)
            _check_decimal(decimal)
            return decimal
    if kind is bool:
        text = text.lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    if kind is UUID:
        return UUID(text)
    if kind is date and _DATE.fullmatch(text):
        return date.fromisoformat(text)
    raise ValueError


def _check_decimal(value: Decimal) -> None:
    if not value.is_finite():
        raise ValueError
    parts = value.as_tuple()
    if len(parts.digits) > MAX_NUMERIC_TEXT or abs(int(parts.exponent)) > MAX_DECIMAL_EXPONENT:
        raise ValueError


@dataclass(frozen=True)
class ParameterContract:
    """Compiled once from server-owned declarations; independent of transport.

    bind() returns inspect.BoundArguments, preserving positional-only and
    keyword-only semantics. No default value or annotation repr is published.
    """

    signature: inspect.Signature
    types: tuple[tuple[str, _Type], ...]

    @classmethod
    def compile(cls, handler: Callable[..., Any]) -> "ParameterContract":
        try:
            signature = inspect.signature(handler)

            # An input contract must not evaluate a return annotation (which
            # may refer to a containing class or an optional output provider).
            def inputs() -> None:
                pass

            inputs.__annotations__ = {
                name: parameter.annotation
                for name, parameter in signature.parameters.items()
                if parameter.annotation is not inspect.Parameter.empty
            }
            origin = inspect.unwrap(handler)
            namespace = getattr(origin, "__globals__", None)
            hints = get_type_hints(inputs, globalns=namespace, include_extras=True)
        except (TypeError, ValueError, NameError, AttributeError):
            raise ContractError(
                "Cannot resolve the handler's signature and parameter annotations."
            ) from None
        if len(signature.parameters) > MAX_COLLECTION:
            raise ContractError("Handler declares too many parameters.")
        compiled = []
        for name, param in signature.parameters.items():
            annotation = hints.get(name, inspect.Parameter.empty)
            if annotation is inspect.Parameter.empty:
                if param.kind in (param.VAR_KEYWORD, param.VAR_POSITIONAL):
                    annotation = Any
                else:
                    raise ContractError(
                        f"Parameter '{name}' requires an annotation; use Any for unchecked input."
                    )
            compiled.append((name, _compile_type(annotation)))
        # Binding only needs to know whether a default exists. Retaining the
        # actual object here would let a function-keyed cache retain its owner
        # through a default/owner/function cycle. Python applies real defaults
        # when the handler is called; never apply_defaults() to this call plan.
        binding_signature = signature.replace(
            parameters=[
                parameter.replace(
                    annotation=inspect.Parameter.empty,
                    default=(
                        _SERVER_DEFAULT
                        if parameter.default is not inspect.Parameter.empty
                        else inspect.Parameter.empty
                    ),
                )
                for parameter in signature.parameters.values()
            ],
            return_annotation=inspect.Signature.empty,
        )
        return cls(binding_signature, tuple(compiled))

    def metadata(self) -> tuple[dict[str, Any], ...]:
        """Value-free public contract; defaults stay exclusively on the server."""
        return tuple(
            {
                "name": name,
                "kind": self.signature.parameters[name].kind.name.lower(),
                "type": spec.label,
                "required": self.signature.parameters[name].default is inspect.Parameter.empty
                and self.signature.parameters[name].kind
                not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD),
                "reduced_checking": spec.reduced_checking,
            }
            for name, spec in self.types
        )

    def bind(
        self,
        params: dict[str, Any],
        positional: list[Any] | tuple[Any, ...] = (),
        *,
        coerce: bool = True,
    ) -> inspect.BoundArguments:
        if type(params) is not dict or type(positional) not in (list, tuple):
            raise ParameterError("Supply a parameter object and positional array.")
        _check_budget(params, positional)
        if any(type(key) is not str for key in params):
            raise ParameterError("Parameter names must be strings.")
        try:
            bound = self.signature.bind(*positional, **params)
        except TypeError:
            # inspect's message can include an arbitrary client-supplied key.
            raise ParameterError(
                "Arguments do not match the handler signature; check required, extra, duplicate and positional values."
            ) from None
        for name, spec in self.types:
            if name not in bound.arguments:
                continue  # Python applies the server-owned default at invocation.
            value = bound.arguments[name]
            try:
                kind = self.signature.parameters[name].kind
                if kind is inspect.Parameter.VAR_POSITIONAL:
                    result = tuple(_convert(item, spec, coerce) for item in value)
                elif kind is inspect.Parameter.VAR_KEYWORD:
                    result = {key: _convert(item, spec, coerce) for key, item in value.items()}
                else:
                    result = _convert(value, spec, coerce)
                bound.arguments[name] = result
            except (ValueError, TypeError, OverflowError, InvalidOperation):
                raise ParameterError(
                    f"Parameter '{name}' requires {spec.label}; supply a valid declared value."
                ) from None
        return bound
