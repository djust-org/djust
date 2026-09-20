"""Authorized parent-driven saves for the staged ADR-038 child registry.

Only declared server state is captured. This is not rendered-slot discovery or
pruning: callers must separately reconcile removed slots after complete renders.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.base import SessionBase

from ._child_state_index import prepare_child_batch, staged_updates
from ._exposure import ExposureContract, ExposureError, clone_json_state
from ._exposure_children import ChildStateSession, child_event_adapter
from .auth.core import check_view_auth, enforce_object_permission


def _capture_children(root: Any, request: Any) -> list[tuple[ChildStateSession, dict[str, Any]]]:
    """Validate the entire owned tree before changing any session entries."""
    try:
        contract = ExposureContract.from_view_class(type(root))
        if root.exposure_policy != "explicit" or getattr(root, "_djust_child_disposed", False):
            raise ExposureError("Invalid child persistence owner")
        pending = [root]
        seen: set[int] = set()
        captured: list[tuple[ChildStateSession, dict[str, Any]]] = []
        children = []
        registries = []
        while pending:
            parent = pending.pop()
            if id(parent) in seen or len(seen) >= 257:
                raise ExposureError("Invalid child persistence tree")
            seen.add(id(parent))
            registry = getattr(parent, "_child_views", {})
            if type(registry) is not dict or len(registry) > 256:
                raise ExposureError("Invalid child persistence registry")
            entries = tuple(registry.items())
            registries.append((parent, entries))
            for slot, child in entries:
                if (
                    getattr(child, "_parent_view", None) is not parent
                    or getattr(child, "_view_id", None) != slot
                    or getattr(child, "_djust_child_disposed", False)
                ):
                    raise ExposureError("Invalid child persistence ownership")
                adapter = child_event_adapter(child, root, request)
                child.request = request
                if check_view_auth(child, request) is not None:
                    raise ExposureError("Child persistence authorization denied")
                enforce_object_permission(child, request)
                children.append((child, adapter))
                pending.append(child)
                if len(children) > 256:
                    raise ExposureError("Child persistence tree exceeds resource limit")
                if adapter is not None:
                    values = adapter.contract.project_view(child, "server")
                    captured.append((adapter, adapter._capture(values)))
        # One total budget, not an independently multiplying budget per child.
        clone_json_state([envelope for _, envelope in captured], limits=contract.limits)
        # Defaults and application authorization hooks may alter other children.
        # Recheck every recorded scope after capture, before the first write.
        for child, adapter in children:
            if getattr(child, "_djust_child_disposed", False):
                raise ExposureError("Child persistence owner was disposed")
            if check_view_auth(child, request) is not None:
                raise ExposureError("Child persistence authorization changed")
            enforce_object_permission(child, request)
            current = child_event_adapter(child, root, request)
            if (current.binding if current else None) != (adapter.binding if adapter else None):
                raise ExposureError("Child persistence identity changed")
        for parent, entries in registries:
            registry = getattr(parent, "_child_views", {})
            if (
                getattr(parent, "_djust_child_disposed", False)
                or type(registry) is not dict
                or len(registry) != len(entries)
                or any(registry.get(slot) is not child for slot, child in entries)
            ):
                raise ExposureError("Child persistence tree changed during capture")
        if captured:
            session = captured[0][0].session
            session.get(captured[0][0].key)  # force lazy load before binding check
            for adapter, _ in captured:
                if adapter.session is not session:
                    raise ExposureError("Child persistence session changed")
                adapter._check_session()
        return captured
    except Exception:  # noqa: BLE001 — provider/default errors may contain secrets
        raise ExposureError("Child state persistence unavailable") from None


def _capture_batch(root: Any, request: Any) -> tuple[SessionBase, dict[str, Any], set[str]] | None:
    try:
        batch = prepare_child_batch(_capture_children(root, request), root, request)
        root._explicit_child_state_tracked = batch is not None
        return batch
    except Exception:  # noqa: BLE001
        raise ExposureError("Child state persistence unavailable") from None


def save_child_states(root: Any, request: Any) -> None:
    """Capture authorized descendants and flush their server envelopes once."""
    batch = _capture_batch(root, request)
    if batch:
        try:
            with staged_updates(*batch) as session:
                session.save()
        except Exception:  # noqa: BLE001 — HTTP errors must not expose backend details
            raise ExposureError("Child state persistence unavailable") from None


async def asave_child_states(root: Any, request: Any) -> None:
    """Async storage flush; validation/projection stays in the Django thread."""
    batch = await sync_to_async(_capture_batch)(root, request)
    if batch:
        try:
            with staged_updates(*batch) as session:
                await session.asave()
        except Exception:  # noqa: BLE001 — never expose private backend errors
            raise ExposureError("Child state persistence unavailable") from None
