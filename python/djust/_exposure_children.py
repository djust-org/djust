"""Internal ADR-038 server-state adapter for a declared child slot.

The eager child mount path uses this behind the production construction gate.
It does not enable explicit child mounts for applications.
Callers must derive the parent contracts, slot ancestry, mount inputs and request
binding from current authorized server rendering/registration, never an event
payload. Load returns validated values; the caller reconstructs transient
dependencies and performs current object authorization before dispatch/render.
"""

import hashlib
import json
from dataclasses import replace
from typing import Any

from django.contrib.sessions.backends.base import SessionBase

from ._exposure import ExposureContract, ExposureError, clone_json_state
from ._exposure_sessions import ServerStateSession, StateBinding, server_state_adapter


def child_state_adapter(
    child: Any,
    parent: Any,
    request: Any,
    slot: str,
    mount_inputs: dict[str, Any],
    *,
    create: bool = False,
) -> "ChildStateSession | None":
    """Derive a child scope from the current server-owned parent registry.

    The child need not be registered yet (fresh mount). Ancestors must already
    belong to their claimed parent, with inputs recorded by the render lifecycle.
    Mixed-policy ancestry is not implicitly upgraded to an exposure contract.
    """
    try:
        policy = getattr(child, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            raise ExposureError("Explicit child policy required")
        contract = ExposureContract.from_view_class(type(child))
        if type(slot) is not str or not 1 <= len(slot) <= 128 or type(mount_inputs) is not dict:
            raise ExposureError("Invalid child slot or mount inputs")
        current_inputs = clone_json_state(mount_inputs, limits=contract.limits)
        if not any(field.persist == "server" for field in contract.fields.values()):
            return None
        parents = []
        slots = [slot]
        inputs = [current_inputs]
        current = parent
        seen: set[int] = set()
        while current is not None:
            if id(current) in seen or len(seen) >= 16:
                raise ExposureError("Invalid child ancestry")
            seen.add(id(current))
            policy = getattr(current, "exposure_policy", None)
            if type(policy) is not str or policy != "explicit":
                raise ExposureError("Explicit children require explicit ancestry")
            parents.append(ExposureContract.from_view_class(type(current)))
            ancestor = getattr(current, "_parent_view", None)
            if ancestor is None:
                break
            current_slot = getattr(current, "_view_id", None)
            registry = getattr(ancestor, "_child_views", None)
            if (
                type(current_slot) is not str
                or type(registry) is not dict
                or registry.get(current_slot) is not current
            ):
                raise ExposureError("Child ancestry is no longer registered")
            encoded_inputs = getattr(current, "_explicit_child_mount_inputs", None)
            if type(encoded_inputs) is not str:
                raise ExposureError("Missing child mount identity")
            inputs.append(clone_json_state(json.loads(encoded_inputs)))
            slots.append(current_slot)
            current = ancestor
        base = server_state_adapter(child, request, create=create)
        if base is None:
            return None
        return ChildStateSession(
            base.session,
            tuple(reversed(parents)),
            base.contract,
            base.binding,
            tuple(reversed(slots)),
            {"ancestry": list(reversed(inputs))},
        )
    except ExposureError:
        raise
    except Exception:  # noqa: BLE001 — do not expose provider/default exception values
        raise ExposureError("Child state provider unavailable") from None


def record_child_mount_inputs(child: Any, mount_inputs: dict[str, Any]) -> None:
    """Record detached, bounded server-render inputs for nested ownership."""
    if type(mount_inputs) is not dict:
        raise ExposureError("Invalid child mount inputs")
    child._explicit_child_mount_inputs = json.dumps(
        clone_json_state(mount_inputs), sort_keys=True, separators=(",", ":")
    )
    child._explicit_child_schema = ExposureContract.from_view_class(type(child)).schema


def child_event_adapter(child: Any, root: Any, request: Any) -> "ChildStateSession | None":
    """Resolve a mounted child only through current ownership and mount binding."""
    try:
        if request is None:
            raise ExposureError("Missing authorized event request")
        current = child
        seen: set[int] = set()
        while current is not root:
            if id(current) in seen or len(seen) >= 16:
                raise ExposureError("Invalid child event ancestry")
            seen.add(id(current))
            parent = getattr(current, "_parent_view", None)
            slot = getattr(current, "_view_id", None)
            registry = getattr(parent, "_child_views", None)
            if type(registry) is not dict or registry.get(slot) is not current:
                raise ExposureError("Child event owner is not registered")
            current = parent
        contract = ExposureContract.from_view_class(type(child))
        if contract.schema != getattr(child, "_explicit_child_schema", None):
            raise ExposureError("Child declarations changed")
        inputs = json.loads(child._explicit_child_mount_inputs)
        from ._exposure_child_identity import child_can_reuse

        if not child_can_reuse(
            child, type(child), child._parent_view, request, child._view_id, inputs
        ):
            raise ExposureError("Child event reuse identity changed")
        adapter = child_state_adapter(child, child._parent_view, request, child._view_id, inputs)
        expected = getattr(child, "_explicit_child_mount_binding", None)
        if (adapter.binding if adapter is not None else None) != expected:
            raise ExposureError("Child event identity changed")
        return adapter
    except Exception:  # noqa: BLE001 — no values from providers in event errors
        raise ExposureError("Child event state unavailable") from None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def child_state_key(route: str, slots: tuple[str, ...]) -> str:
    """Derive a storage key from a server route and validated slot ancestry."""
    if (
        type(route) is not str
        or not 1 <= len(route) <= 2048
        or type(slots) is not tuple
        or not 1 <= len(slots) <= 16
        or any(type(slot) is not str or not 1 <= len(slot) <= 128 for slot in slots)
    ):
        raise ExposureError("Invalid child state key scope")
    return "_djust_explicit_child_" + _digest(clone_json_state([route, list(slots)]))


class ChildStateSession(ServerStateSession):
    """Reuse the server envelope with an exact, bounded child ownership binding.

    One parent contract and slot name per ancestry level distinguish nested
    children and same-type siblings. Parent/child schema or mount-input changes
    invalidate old values in the same storage slot instead of accumulating an
    unreachable entry for every revision. Mount inputs are bounded JSON
    identities, not ORM instances, and only their digest enters the envelope.

    This adapter does not store context or expose browser snapshots, inspect
    arbitrary component attributes, or apply restored values. The enclosing
    provider lifecycle must prune removed slots; session expiry alone is not
    the complete provider cleanup contract.
    """

    def __init__(
        self,
        session: SessionBase,
        parents: tuple[ExposureContract, ...],
        contract: ExposureContract,
        binding: StateBinding,
        slots: tuple[str, ...],
        mount_inputs: dict[str, Any],
        *,
        max_age: int = 3600,
    ) -> None:
        if (
            type(contract) is not ExposureContract
            or type(binding) is not StateBinding
            or type(parents) is not tuple
            or not 1 <= len(parents) <= 16
            or any(type(parent) is not ExposureContract for parent in parents)
            or type(slots) is not tuple
            or len(slots) != len(parents)
            or any(type(slot) is not str or not 1 <= len(slot) <= 128 for slot in slots)
            or type(mount_inputs) is not dict
        ):
            raise ExposureError("Invalid child state provider identity")
        identity = clone_json_state(
            {
                "route": binding.view,
                "parents": [[parent.owner, parent.schema] for parent in parents],
                "slots": list(slots),
                "inputs": mount_inputs,
            },
            limits=contract.limits,
        )
        child_binding = replace(binding, view="child:" + _digest(identity))
        super().__init__(session, contract, child_binding, max_age=max_age)
        # The logical route/slot key deliberately excludes class/schema/inputs.
        # They remain in the validated envelope binding, so replacement meets
        # and rejects prior state rather than hiding it at a fresh storage key.
        self.route = binding.view
        self.slots = slots
        self.key = child_state_key(binding.view, slots)

    def save(self, values: dict[str, Any]) -> None:
        """Write server state and its scoped slot index with sanitized failures."""
        from ._child_state_index import save_indexed_child

        save_indexed_child(self, values)

    async def asave(self, values: dict[str, Any]) -> None:
        """Async equivalent, including index tracking and local rollback."""
        from ._child_state_index import asave_indexed_child

        await asave_indexed_child(self, values)
