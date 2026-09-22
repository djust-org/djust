"""Internal ADR-038 projection primitives, not an enabled LiveView policy.

Callers must select declarations, authenticate storage, bind identities and
check freshness separately. A schema digest is NOT a signature. These helpers
never discover ordinary attributes, invoke a fallback serializer, or mutate a
view during restore. Legacy runtime exporters deliberately do not use them yet.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from django.core.exceptions import ImproperlyConfigured

Destination = Literal["server", "client", "snapshot", "debug"]
Persistence = Literal["server", "client"] | None
_DESTINATIONS = ("server", "client", "snapshot", "debug")
_RESTORABLE = ("server", "snapshot")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z", re.ASCII)
_UNSAFE_KEYS = frozenset({"__proto__", "constructor", "prototype"})
_CODEC_VERSION = "json-primitives-v1"


class ExposureConfigurationError(ImproperlyConfigured):
    """A view's exposure configuration is invalid.

    Raised by the constructor guard. Its messages are framework-authored and
    carry no view values, so the protected HTTP entry (ADR-038 D-a) lets it
    reach the developer instead of turning it into a generic 500.
    """


class ExposureError(ValueError):
    """Invalid declaration or payload; messages deliberately omit input values."""


@dataclass(frozen=True)
class FieldExposure:
    """Independent permissions for one declared state field."""

    persist: Persistence = None
    client: bool = False

    def __post_init__(self) -> None:
        if self.persist is not None and (
            type(self.persist) is not str or self.persist not in ("server", "client")
        ):
            raise ExposureError("Invalid state persistence destination")
        if type(self.client) is not bool:
            raise ExposureError("State client permission must be a boolean")
        if self.persist == "client" and not self.client:
            raise ExposureError("Client persistence requires client exposure permission")

    def permits(self, destination: Destination) -> bool:
        """Return whether the destination may receive this field's raw value."""
        if destination == "server":
            return self.persist == "server"
        if destination == "snapshot":
            return self.persist == "client"
        if destination in ("client", "debug"):
            return self.client
        raise ExposureError("Unknown state projection destination")


@dataclass(frozen=True)
class StateLimits:
    """Bounds for a complete projection, including keys and JSON punctuation."""

    max_bytes: int = 65536
    max_nodes: int = 10000
    max_depth: int = 16

    def __post_init__(self) -> None:
        for value, ceiling in (
            (self.max_bytes, 16 * 1024 * 1024),
            (self.max_nodes, 1000000),
            (self.max_depth, 64),
        ):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ExposureError("Invalid state serialization resource limit")


_DEFAULT_LIMITS = StateLimits()


def _read_exposure_policy(view: Any) -> Any:
    """Default only an undeclared policy, never a failing policy descriptor."""
    try:
        return view.exposure_policy
    except AttributeError:
        from inspect import getattr_static

        missing = object()
        if getattr_static(view, "exposure_policy", missing) is not missing:
            raise
        return "legacy"


def uses_legacy_exposure(view: Any) -> bool:
    """Only the exact legacy policy may select a reflective export/restore path."""
    try:
        policy = _read_exposure_policy(view)
        return type(policy) is str and policy == "legacy"
    except Exception:  # noqa: BLE001 — an unreadable policy cannot grant legacy access
        return False


def service_worker_cache_eligible(view: Any) -> bool:
    """Whether a page's HTML may be written to the worker's VDOM/shell caches.

    ADR-038 D-b: only a legacy view whose registered children are all legacy
    is eligible. The rendered HTML includes every child, so one nonlegacy
    child makes the page ineligible. Unreadable children fail closed.
    """
    if not uses_legacy_exposure(view):
        return False
    getter = getattr(view, "_get_all_child_views", None)
    if getter is None:
        return True
    try:
        children = getter()
        return all(uses_legacy_exposure(child) for child in dict(children).values())
    except Exception:  # noqa: BLE001 — unknown children cannot prove eligibility
        return False


def explicit_debug_projection(view: Any) -> dict[str, Any] | None:
    """Return bounded explicit debug data, or None only for the legacy policy.

    Diagnostic failures must not retry using reflection, repr, or context. Even
    a permitted field's factory can raise an exception containing secrets, so
    this debug-only boundary deliberately emits neither exception text nor a
    traceback. Invalid/unknown policies fail closed as unavailable debug state.
    The ordinary runtime projection API still raises validation errors.
    """
    try:
        policy = _read_exposure_policy(view)
        if type(policy) is str and policy == "legacy":
            return None
        if type(policy) is str and policy == "explicit":
            return ExposureContract.from_view_class(type(view)).project_view(view, "debug")
    except Exception:  # noqa: BLE001 — diagnostic boundary; never expose exception values
        return {"_djust_projection_error": "State unavailable"}
    return {"_djust_projection_error": "State unavailable"}


def explicit_state_projection(view: Any, destination: Destination) -> dict[str, Any]:
    """Project a direct state API without a reflective or repr fallback.

    This returns detached values, not an authenticated restoration envelope.
    Factory failures must not carry their exception values into diagnostics.
    """
    try:
        policy = getattr(view, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            raise ExposureError("Explicit state policy required")
        return ExposureContract.from_view_class(type(view)).project_view(view, destination)
    except Exception:  # noqa: BLE001 — factory exceptions may contain secrets; fail closed
        raise ExposureError("Explicit state projection unavailable") from None


def require_legacy_state_api(view: Any) -> None:
    """Reject legacy persistence/restore helpers before inspecting any payload.

    Explicit persistence must use a bound adapter, not raw dictionaries or
    inferred private/component fields. Unknown policies cannot grant access.
    """
    if not uses_legacy_exposure(view):
        raise ExposureError(
            "Legacy state API requires legacy exposure; use a bound explicit adapter"
        )


def clone_json_state(value: Any, *, limits: StateLimits = _DEFAULT_LIMITS) -> Any:
    """Detach bounded JSON primitives without inspecting arbitrary Python objects.

    Accept exact builtins only. Models, services, custom containers, tuples,
    Decimal/date/UUID and lazy values require a future explicit codec. Integers
    must round-trip through JavaScript without losing precision. No repr/str,
    ORM traversal, __dict__, iterator or JSONEncoder fallback is permitted.
    """
    if type(limits) is not StateLimits:
        raise ExposureError("Invalid state serialization resource limits")
    remaining_bytes = limits.max_bytes
    remaining_nodes = limits.max_nodes

    def consume(size: int) -> None:
        nonlocal remaining_bytes
        remaining_bytes -= size
        if remaining_bytes < 0:
            raise ExposureError("State projection exceeds its encoded size limit")

    def text_size(text: str) -> None:
        if len(text) > remaining_bytes:
            raise ExposureError("State projection exceeds its encoded size limit")
        try:
            encoded = json.dumps(text, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise ExposureError("State contains an unsupported string encoding") from None
        consume(len(encoded))

    def walk(item: Any, depth: int) -> Any:
        nonlocal remaining_nodes
        remaining_nodes -= 1
        if remaining_nodes < 0 or depth > limits.max_depth:
            raise ExposureError("State projection exceeds its structural limits")
        kind = type(item)
        if item is None or kind is bool:
            consume(4 if item is None or item is True else 5)
            return item
        if kind is str:
            text_size(item)
            return item
        if kind is int:
            if not -(2**53 - 1) <= item <= 2**53 - 1:
                raise ExposureError("State integer cannot round-trip through JSON clients")
            consume(len(str(item)))
            return item
        if kind is float:
            if not math.isfinite(item):
                raise ExposureError("State numbers must be finite")
            consume(len(json.dumps(item)))
            return item
        if kind is not list and kind is not dict:
            raise ExposureError("Unsupported state value; an explicit codec is required")
        if len(item) > remaining_nodes:
            raise ExposureError("State projection exceeds its structural limits")
        consume(2 + max(0, len(item) - 1))  # brackets and comma separators
        if kind is list:
            return [walk(child, depth + 1) for child in item]
        result: dict[str, Any] = {}
        for key, child in item.items():
            if type(key) is not str or key in _UNSAFE_KEYS:
                raise ExposureError("State mapping contains an unsupported key")
            text_size(key)
            consume(1)  # colon
            result[key] = walk(child, depth + 1)
        return result

    return walk(value, 0)


@dataclass(frozen=True)
class ExposureContract:
    """Immutable, purpose-specific field selection and schema checking.

    This is internal compiler output, not an application-facing second allowlist.
    Transport adapters must obtain fields from declarations, never inferred
    context. Version changes are required when application field meanings change.
    """

    owner: str
    fields: Mapping[str, FieldExposure]
    version: int = 1
    limits: StateLimits = field(default_factory=StateLimits)
    schema: str = field(init=False)

    @classmethod
    def from_view_class(cls, view_class: type, *, version: int = 1) -> "ExposureContract":
        """Compile descriptors without evaluating defaults, properties or annotations.

        Inherited exposure grants require redeclaring the field in the concrete
        class. Adding an exposed field to a base must not widen its descendants.
        Ordinary inherited reactive fields remain available with no permissions.
        """
        from ._state import StateProperty

        fields: dict[str, FieldExposure] = {}
        seen: set[str] = set()
        for owner in view_class.__mro__:
            for name, descriptor in vars(owner).items():
                if name in seen:
                    continue
                seen.add(name)
                if not isinstance(descriptor, StateProperty):
                    continue
                policy = descriptor.exposure
                if owner is not view_class and policy != FieldExposure():
                    raise ExposureError(
                        "Inherited exposure grants require explicit field redeclaration"
                    )
                fields[name] = policy
        return cls(f"{view_class.__module__}.{view_class.__qualname__}", fields, version=version)

    def __post_init__(self) -> None:
        from .live_view import _FRAMEWORK_INTERNAL_ATTRS, LiveView

        framework_names = set(_FRAMEWORK_INTERNAL_ATTRS)
        for base in LiveView.__mro__:
            framework_names.update(vars(base))

        if type(self.owner) is not str or not self.owner or len(self.owner) > 512:
            raise ExposureError("Invalid state contract owner")
        if type(self.version) is not int or not 0 < self.version <= 2**31 - 1:
            raise ExposureError("State contract version must be a positive integer")
        if type(self.limits) is not StateLimits:
            raise ExposureError("Invalid state contract resource limits")
        copied = dict(self.fields)
        if len(copied) > self.limits.max_nodes:
            raise ExposureError("Too many declared state fields")
        for name, policy in copied.items():
            if (
                type(name) is not str
                or len(name) > 128
                or not _NAME.fullmatch(name)
                or name in framework_names
                or name in _UNSAFE_KEYS
            ):
                raise ExposureError("Invalid or reserved state field name")
            if type(policy) is not FieldExposure:
                raise ExposureError("State fields require explicit exposure metadata")
        object.__setattr__(self, "fields", MappingProxyType(copied))
        specification = {
            "owner": self.owner,
            "version": self.version,
            "codec": _CODEC_VERSION,
            "fields": {name: [policy.persist, policy.client] for name, policy in copied.items()},
        }
        encoded = json.dumps(specification, sort_keys=True, separators=(",", ":")).encode()
        object.__setattr__(self, "schema", hashlib.sha256(encoded).hexdigest())

    def project(self, values: dict[str, Any], destination: Destination) -> dict[str, Any]:
        """Select only permitted fields, then clone using one shared resource budget."""
        if (
            type(destination) is not str
            or destination not in _DESTINATIONS
            or type(values) is not dict
        ):
            raise ExposureError("Invalid state projection input")
        if len(values) > self.limits.max_nodes or any(type(key) is not str for key in values):
            raise ExposureError("Invalid state projection keys")
        selected: dict[str, Any] = {}
        for name, policy in self.fields.items():
            if policy.permits(destination):
                if name not in values:
                    raise ExposureError("Declared state value is missing")
                selected[name] = values[name]
            elif destination == "debug":
                selected[name] = "[redacted]"
        return cast(dict[str, Any], clone_json_state(selected, limits=self.limits))

    def project_view(self, view: Any, destination: Destination) -> dict[str, Any]:
        """Read permitted descriptors only, never ordinary attributes or context.

        Verify the declaration schema again so a stale compiled contract cannot
        read a field that was removed or replaced with an arbitrary property.
        """
        current = type(self).from_view_class(type(view), version=self.version)
        if current.schema != self.schema:
            raise ExposureError("View declarations no longer match the state contract")
        if type(destination) is not str or destination not in _DESTINATIONS:
            raise ExposureError("Unknown state projection destination")
        values = {
            name: getattr(view, name)
            for name, policy in self.fields.items()
            if policy.permits(destination)
        }
        return self.project(values, destination)

    def capture(self, values: dict[str, Any], destination: Destination) -> dict[str, Any]:
        """Build a schema envelope; callers must authenticate and bind storage."""
        if type(destination) is not str or destination not in _RESTORABLE:
            raise ExposureError("This state projection is not restorable")
        return {
            "schema": self.schema,
            "destination": destination,
            "values": self.project(values, destination),
        }

    def prepare_restore(self, envelope: Any, destination: Destination) -> dict[str, Any]:
        """Validate the entire envelope before returning any assignable values.

        Requires already authenticated, identity-bound, unexpired input from a
        transport adapter. Does not authorize a request or assign attributes.
        """
        if (
            type(destination) is not str
            or destination not in _RESTORABLE
            or type(envelope) is not dict
        ):
            raise ExposureError("Invalid state restore envelope")
        if (
            len(envelope) != 3
            or any(type(key) is not str for key in envelope)
            or set(envelope) != {"schema", "destination", "values"}
        ):
            raise ExposureError("Unexpected state restore envelope fields")
        if (
            type(envelope["schema"]) is not str
            or type(envelope["destination"]) is not str
            or envelope["schema"] != self.schema
            or envelope["destination"] != destination
        ):
            raise ExposureError("State restore schema or destination mismatch")
        values = envelope["values"]
        expected = {name for name, policy in self.fields.items() if policy.permits(destination)}
        if (
            type(values) is not dict
            or len(values) != len(expected)
            or any(type(key) is not str for key in values)
            or set(values) != expected
        ):
            raise ExposureError("State restore fields do not match the declared contract")
        return self.project(values, destination)
