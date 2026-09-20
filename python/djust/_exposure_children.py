"""Internal ADR-038 server-state adapter for a declared child slot.

This is not lifecycle integration and does not enable explicit child mounts.
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
from ._exposure_sessions import ServerStateSession, StateBinding


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
        self.key = "_djust_explicit_child_" + _digest([binding.view, list(slots)])
