"""Private fixed-binding adapter for the verified legacy signed snapshot path.

This is not a signature verifier or an explicit-exposure permission grant.
The transport verifies the envelope and view/session binding before restoring.
Only current fixed declarations are resolved; no payload-supplied class,
configuration or callback is executed. Debug capture uses its separate schema.
"""

from __future__ import annotations

import inspect

from ._component_subscriptions import compile_subscriptions
from .components._interactive import DropdownMenu, _binding_values
from .components.base import LiveComponent
from .live_view import LiveView


SNAPSHOT_KEY = "__interactive_bindings__"


def capture_bindings(view: LiveView) -> dict[str, object] | None:
    bindings: dict[str, dict[str, object]] = {}
    for name, bound in view._component_bindings.items():
        if not isinstance(bound, DropdownMenu):
            continue
        if name != bound.key:
            raise ValueError("Aliased declaration in interactive binding snapshot")
        if len(bindings) >= 256:
            raise ValueError("Interactive binding snapshot exceeds its record limit")
        identity, opened, selected = _binding_values(bound._dump_binding())
        bindings[name] = {"binding_id": identity, "open": opened, "selected": selected}
    if not bindings:
        return None
    return {"version": 1, "bindings": bindings}


def restore_bindings(view: LiveView, payload: object) -> None:
    """Validate the entire fixed-binding manifest before changing any binding."""
    if (
        type(payload) is not dict
        or set(payload) != {"version", "bindings"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or type(payload["bindings"]) is not dict
        or len(payload["bindings"]) > 256
    ):
        raise ValueError("Invalid interactive binding snapshot")
    compile_subscriptions(type(view))
    plan: list[tuple[str, DropdownMenu, dict[str, object]]] = []
    identities: set[str] = set()
    previous_states: list[tuple[DropdownMenu, str, dict[str, object]]] = []
    for name, record in payload["bindings"].items():
        if type(name) is not str or not name.isidentifier() or name.startswith("_"):
            raise ValueError("Invalid interactive binding snapshot")
        declaration = inspect.getattr_static(type(view), name, None)
        if (
            not isinstance(declaration, DropdownMenu)
            or declaration._name != name
            or declaration._owner not in type(view).__mro__
        ):
            raise ValueError("Unknown declaration in interactive binding snapshot")
        identity, opened, selected = _binding_values(record)
        existing = view._component_bindings.get(name)
        occupied = view._components.get(identity)
        if identity in identities or (occupied is not None and occupied is not existing):
            raise ValueError("Conflicting identity in interactive binding snapshot")
        identities.add(identity)
        if existing is not None:
            if not isinstance(existing, DropdownMenu) or existing._declaration is not declaration:
                raise ValueError("Stale declaration in interactive binding snapshot")
            existing._bound_owner()
            previous_states.append((existing, existing.component_id, existing.state))
        plan.append(
            (name, declaration, {"binding_id": identity, "open": opened, "selected": selected})
        )

    previous_bindings = dict(view._component_bindings)
    previous_registry = dict(view._components)
    try:
        for _, declaration, state in plan:
            bound = declaration.__get__(view, type(view))
            bound._restore_binding(state)
    except Exception:
        # A constructor/registration error must not leave partially installed
        # snapshot identities behind when the transport falls back to mount.
        for name, cached in view._component_bindings.items():
            if cached is not previous_bindings.get(name):
                LiveComponent.unmount(cached)
        view._component_bindings.clear()
        view._component_bindings.update(previous_bindings)
        view._components.clear()
        view._components.update(previous_registry)
        for component, identity, state in previous_states:
            component.component_id = identity
            component._restore_state(state)
        raise
