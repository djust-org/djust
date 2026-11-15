"""In-memory reuse identity; never a persistence envelope or authorization grant."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from django.contrib.sessions.backends.base import SessionBase

from ._exposure import ExposureContract, ExposureError, clone_json_state
from ._exposure_sessions import _request_principal


@dataclass(frozen=True, eq=False, repr=False)
class ChildReuseIdentity:
    """Detached value identity plus exact Python classes and optional root owner.

    A sessionless or mixed-policy subtree cannot transfer its transient state
    between root instances. Holding the root reference avoids id() reuse and
    comparing by 'is' avoids application-defined equality hooks.
    """

    digest: str
    classes: tuple[type, ...]
    owner: Any

    def matches(self, other: "ChildReuseIdentity") -> bool:
        return (
            self.digest == other.digest
            and self.owner is other.owner
            and len(self.classes) == len(other.classes)
            and all(left is right for left, right in zip(self.classes, other.classes))
        )


def child_reuse_identity(
    child_cls: type,
    parent: Any,
    request: Any,
    slot: str,
    mount_inputs: dict[str, Any],
    *,
    carry: bool = False,
) -> ChildReuseIdentity:
    """Compile identity from trusted render inputs without evaluating state.

    Authentication/tenant rules are shared with server persistence. No session
    is created, saved or decoded here. A cookie session can identify transient
    reuse, but cannot acquire server-state storage through this helper.

    ``carry=True`` compiles the identity a sticky child keeps across a
    ``live_redirect`` (#4149): the same child schema and class, slot, mount
    inputs, session, user, tenant and nesting depth, but neither the route nor
    the ancestors' classes and inputs. The ancestry is still validated, and an
    owner-bound subtree (no session, or a legacy ancestor) still matches only
    under the same root instance, so it never carries to another page.
    """
    try:
        policy = getattr(child_cls, "exposure_policy", None)
        if type(policy) is not str or policy != "explicit":
            raise ExposureError("Explicit child required")
        if type(slot) is not str or not 1 <= len(slot) <= 128 or type(mount_inputs) is not dict:
            raise ExposureError("Invalid child reuse inputs")
        contract = ExposureContract.from_view_class(child_cls)
        inputs = clone_json_state(mount_inputs, limits=contract.limits)
        user, tenant = _request_principal(request)
        route = request.path
        if type(route) is not str or not 1 <= len(route) <= 2048:
            raise ExposureError("Invalid child reuse route")
        session = getattr(request, "session", None)
        if session is not None and not isinstance(session, SessionBase):
            raise ExposureError("Invalid child reuse session")
        key = session.session_key if session is not None else None
        if key is not None and (type(key) is not str or not 1 <= len(key) <= 65536):
            raise ExposureError("Invalid child reuse session key")
        # Never retain a raw cookie or session identifier in runtime metadata.
        session_digest = hashlib.sha256(key.encode()).hexdigest() if key is not None else None
        owner_bound = key is None
        lineage = []
        classes = [child_cls]
        if session is not None:
            classes.append(type(session))
        carried_classes = tuple(classes)
        seen: set[int] = set()
        current = parent
        root = None
        while current is not None:
            if id(current) in seen or len(seen) >= 16:
                raise ExposureError("Invalid child reuse ancestry")
            seen.add(id(current))
            classes.append(type(current))
            policy = getattr(current, "exposure_policy", None)
            if type(policy) is not str or policy not in ("explicit", "legacy"):
                raise ExposureError("Invalid ancestor exposure policy")
            legacy = policy == "legacy"
            owner_bound = owner_bound or legacy
            schema = None if legacy else ExposureContract.from_view_class(type(current)).schema
            ancestor = getattr(current, "_parent_view", None)
            current_slot = None
            ancestor_inputs = None
            if ancestor is not None:
                current_slot = getattr(current, "_view_id", None)
                registry = getattr(ancestor, "_child_views", None)
                if (
                    type(current_slot) is not str
                    or not 1 <= len(current_slot) <= 128
                    or type(registry) is not dict
                    or registry.get(current_slot) is not current
                ):
                    raise ExposureError("Unregistered child reuse ancestry")
                if not legacy:
                    encoded = getattr(current, "_explicit_child_mount_inputs", None)
                    if type(encoded) is not str or len(encoded) > contract.limits.max_bytes:
                        raise ExposureError("Invalid ancestor mount inputs")
                    ancestor_inputs = clone_json_state(json.loads(encoded))
            lineage.append([schema, current_slot, ancestor_inputs])
            root = current
            current = ancestor
        if root is None:
            raise ExposureError("Missing child reuse owner")
        if carry:
            classes = list(carried_classes)
            parts = ["carry", contract.schema, slot, inputs, user, tenant, session_digest]
            parts.append(len(lineage))
        else:
            parts = [contract.schema, slot, inputs, user, tenant, route, session_digest, lineage]
        identity = clone_json_state(parts, limits=contract.limits)
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ChildReuseIdentity(digest, tuple(classes), root if owner_bound else None)
    except Exception:  # noqa: BLE001 — no private values or provider exceptions
        raise ExposureError("Child reuse identity unavailable") from None


def child_can_reuse(
    child: Any, child_cls: type, parent: Any, request: Any, slot: str, mount_inputs: dict[str, Any]
) -> bool:
    """Require exact type, recorded schema/inputs and current ownership scope."""
    current = child_reuse_identity(child_cls, parent, request, slot, mount_inputs)
    recorded = getattr(child, "_explicit_child_reuse_identity", None)
    policy = getattr(child, "exposure_policy", None)
    if (
        type(child) is not child_cls
        or type(recorded) is not ChildReuseIdentity
        or type(policy) is not str
        or policy != "explicit"
    ):
        return False
    return recorded.matches(current)


def carried_reuse_identity(
    child: Any, child_cls: type, parent: Any, request: Any, slot: str, mount_inputs: dict[str, Any]
) -> ChildReuseIdentity | None:
    """The reuse identity a preserved sticky child would take under ``parent`` (#4149).

    Compares the carry identity recorded at the child's mount with the one the
    destination's ``{% live_render %}`` compiles now. On a match, returns the
    child's full reuse identity for its new route and parent; otherwise
    ``None``. Pure: the caller records it only after the destination's
    authorization passed (commit-or-rollback).
    """
    current = child_reuse_identity(child_cls, parent, request, slot, mount_inputs, carry=True)
    recorded = getattr(child, "_explicit_child_carry_identity", None)
    policy = getattr(child, "exposure_policy", None)
    if (
        type(child) is not child_cls
        or type(recorded) is not ChildReuseIdentity
        or type(policy) is not str
        or policy != "explicit"
        or not recorded.matches(current)
    ):
        return None
    return child_reuse_identity(child_cls, parent, request, slot, mount_inputs)
