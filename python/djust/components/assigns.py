"""Declarative assign & slot DSL for djust components.

Matches Phoenix.Component's `attr` / `slot` macros. Used by both
`LiveComponent` subclasses (via class-level `assigns`/`slots` attribute) and
function components (via `@component(assigns=[...], slots=[...])`).

Example::

    from djust import Assign, Slot, LiveComponent

    class Button(LiveComponent):
        assigns = [
            Assign("variant", type=str, default="default",
                   values=["default", "primary", "danger"]),
            Assign("size", type=str, default="md"),
            Assign("disabled", type=bool, default=False),
        ]
        slots = [Slot("inner_block", required=True)]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


class AssignValidationError(ValueError):
    """Raised when an assign fails declarative validation."""


# Sentinel for "no default value declared" (distinct from `None`).
_MISSING = object()


@dataclass
class Assign:
    """Declarative description of a component assign.

    Args:
        name: Assign key (the kwarg name the component receives).
        type: Expected Python type (``str``, ``int``, ``bool``, ``float`` or
            any other type for passthrough). Coercion is attempted for the
            four primitive types.
        default: Default value applied when the caller omits the assign.
            Use ``_MISSING`` (the module default) for "no default".
        required: If True, validation errors when the assign is missing and
            no default is provided.
        values: Optional list of allowed values (enum check).
        description: Free-form documentation string.
    """

    name: str
    type: Any = None
    default: Any = _MISSING
    required: bool = False
    values: Optional[Sequence[Any]] = None
    description: str = ""


@dataclass
class Slot:
    """Declarative description of a named slot.

    Args:
        name: Slot name (matches ``{% slot name %}``).
        required: If True, validation fails when the slot is absent.
        multiple: If True, the slot may be supplied multiple times and is
            exposed to the template as a list.
        attrs: Optional list of ``Assign`` describing the slot's own
            attributes (e.g. ``<:col label="Name">`` -> ``attrs=[Assign("label", str)]``).
    """

    name: str
    required: bool = False
    multiple: bool = False
    attrs: list[Assign] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Coercion + validation helpers
# ---------------------------------------------------------------------------


_TRUTHY_STRINGS = {"true", "yes", "1", "on"}
_FALSY_STRINGS = {"false", "no", "0", "off", ""}


def _coerce(value: Any, target_type: Any) -> Any:
    """Best-effort coercion of ``value`` to ``target_type``.

    Pass-through when ``target_type`` is falsy/``None`` or already matches.
    Raises ``AssignValidationError`` when coercion fails.
    """

    if target_type is None or target_type is Any:
        return value
    if isinstance(value, target_type):
        return value

    # Primitive coercions from str.
    if isinstance(value, str):
        if target_type is int:
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise AssignValidationError(f"Cannot coerce {value!r} to int") from exc
        if target_type is float:
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise AssignValidationError(f"Cannot coerce {value!r} to float") from exc
        if target_type is bool:
            lowered = value.strip().lower()
            if lowered in _TRUTHY_STRINGS:
                return True
            if lowered in _FALSY_STRINGS:
                return False
            raise AssignValidationError(f"Cannot coerce {value!r} to bool")
        if target_type is str:
            return value

    # int -> float is a common widening we accept.
    if target_type is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)

    # bool -> int (Python already treats True/False as ints, but be explicit).
    if target_type is int and isinstance(value, bool):
        return int(value)

    # Fall through: if the types don't line up and we can't coerce, raise.
    raise AssignValidationError(
        f"Value {value!r} is not of type {getattr(target_type, '__name__', target_type)}"
    )


def validate_assigns(declarations: Sequence[Assign], provided: dict[str, Any]) -> dict[str, Any]:
    """Validate & coerce ``provided`` against ``declarations``.

    Returns a new dict with defaults applied and values coerced. Unknown keys
    are passed through (they may be handled by the component itself, e.g.
    HTML passthrough attrs). Missing required assigns raise
    ``AssignValidationError``.
    """

    result: dict[str, Any] = {}
    declared_names: set[str] = set()

    for decl in declarations:
        declared_names.add(decl.name)
        if decl.name in provided:
            raw = provided[decl.name]
            value = _coerce(raw, decl.type)
        elif decl.default is not _MISSING:
            value = decl.default
        elif decl.required:
            raise AssignValidationError(f"Required assign '{decl.name}' was not provided")
        else:
            continue

        if decl.values is not None and value not in decl.values:
            raise AssignValidationError(
                f"Assign '{decl.name}' value {value!r} not in allowed set {list(decl.values)!r}"
            )
        result[decl.name] = value

    # Preserve unknown kwargs (HTML passthrough, extra args).
    for key, val in provided.items():
        if key not in declared_names:
            result[key] = val

    return result


def validate_slots(
    declarations: Sequence[Slot], provided: dict[str, list[Any]]
) -> dict[str, list[Any]]:
    """Validate slot presence/multiplicity.

    ``provided`` is the already-collected ``{slot_name: [entry, ...]}`` map.
    Returns ``provided`` unchanged when valid; raises
    ``AssignValidationError`` on required-missing or multiplicity violations.
    """

    for decl in declarations:
        entries = provided.get(decl.name, [])
        if decl.required and not entries:
            raise AssignValidationError(f"Required slot '{decl.name}' was not provided")
        if not decl.multiple and len(entries) > 1:
            raise AssignValidationError(
                f"Slot '{decl.name}' was provided {len(entries)} times but multiple=False"
            )
    return provided


def merge_assign_declarations(cls: type) -> list[Assign]:
    """Walk ``cls``'s MRO and merge declared ``assigns`` lists.

    Later declarations in the MRO (i.e. child-class) override parent
    declarations by ``Assign.name``.
    """

    by_name: dict[str, Assign] = {}
    for klass in reversed(cls.__mro__):
        decls = klass.__dict__.get("assigns")
        if not decls:
            continue
        for decl in decls:
            if isinstance(decl, Assign):
                by_name[decl.name] = decl
    return list(by_name.values())


def merge_slot_declarations(cls: type) -> list[Slot]:
    """Walk ``cls``'s MRO and merge declared ``slots`` lists."""

    by_name: dict[str, Slot] = {}
    for klass in reversed(cls.__mro__):
        decls = klass.__dict__.get("slots")
        if not decls:
            continue
        for decl in decls:
            if isinstance(decl, Slot):
                by_name[decl.name] = decl
            elif isinstance(decl, str):
                # Tolerate ``slots = ['inner_block']`` shorthand.
                by_name[decl] = Slot(decl, required=True)
    return list(by_name.values())
