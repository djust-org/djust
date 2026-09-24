"""Bounded server-only slot index and atomic local staging for child saves.

Index entries are slot paths, never arbitrary session keys. Deletion keys are
derived again from the authorized request route. Local rollback is not a
transaction or a guarantee about an uncertain backend commit.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.base import SessionBase

from ._exposure import ExposureError, clone_json_state
from ._exposure_sessions import _SERVER_SESSION_TYPES


def _index_key(route: str) -> str:
    return "_djust_explicit_slots_" + hashlib.sha256(route.encode()).hexdigest()


def _read_paths(raw: Any) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    if raw is None:
        return set(), set()
    value = clone_json_state(raw)
    if (
        type(value) is not dict
        or set(value) != {"version", "paths", "shell_paths"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["paths"]) is not list
        or len(value["paths"]) > 256
        or type(value["shell_paths"]) is not list
        or len(value["shell_paths"]) > 256
    ):
        raise ExposureError("Invalid child state index")
    for path in [*value["paths"], *value["shell_paths"]]:
        if (
            type(path) is not list
            or not 1 <= len(path) <= 16
            or any(type(slot) is not str or not 1 <= len(slot) <= 128 for slot in path)
        ):
            raise ExposureError("Invalid child state index path")
    paths = {tuple(path) for path in value["paths"]}
    shell_paths = {tuple(path) for path in value["shell_paths"]}
    if len(paths) != len(value["paths"]):
        raise ExposureError("Duplicate child state index path")
    if not shell_paths <= paths or len(shell_paths) != len(value["shell_paths"]):
        raise ExposureError("Invalid shell child state index")
    return paths, shell_paths


def _index(paths: set[tuple[str, ...]], shell_paths: set[tuple[str, ...]]) -> dict[str, Any]:
    raw = {
        "version": 1,
        "paths": [list(path) for path in sorted(paths)],
        "shell_paths": [list(path) for path in sorted(shell_paths)],
    }
    _read_paths(raw)
    return raw


@contextmanager
def staged_updates(
    session: SessionBase, updates: dict[str, Any], removed: set[str]
) -> Iterator[SessionBase]:
    """Restore local entries/modified flag if storage fails or is cancelled."""
    missing = object()
    previous = {key: session.get(key, missing) for key in updates.keys() | removed}
    was_modified = session.modified
    completed = False
    try:
        for key in removed:
            session.pop(key, None)
        for key, value in updates.items():
            session[key] = value
        yield session
        completed = True
    finally:
        if not completed:
            for key, value in previous.items():
                if value is missing:
                    session.pop(key, None)
                else:
                    session[key] = value
            session.modified = was_modified


def _child_update(adapter: Any, values: dict[str, Any]) -> dict[str, Any]:
    envelope = adapter._capture(values)
    key = _index_key(adapter.route)
    paths, shell_paths = _read_paths(adapter.session.get(key))
    adapter._check_session()
    paths.add(adapter.slots)
    return {adapter.key: envelope, key: _index(paths, shell_paths)}


def save_indexed_child(adapter: Any, values: dict[str, Any]) -> None:
    """Save a single child and record its scope, including initial mounts."""
    try:
        updates = _child_update(adapter, values)
        with staged_updates(adapter.session, updates, set()) as session:
            session.save()
    except Exception:  # noqa: BLE001
        raise ExposureError("Child state persistence unavailable") from None


async def asave_indexed_child(adapter: Any, values: dict[str, Any]) -> None:
    """Async equivalent without lazy synchronous session I/O on the event loop."""
    try:
        updates = await sync_to_async(_child_update)(adapter, values)
        adapter._check_session()
        with staged_updates(adapter.session, updates, set()) as session:
            await session.asave()
    except Exception:  # noqa: BLE001
        raise ExposureError("Child state persistence unavailable") from None


def prepare_child_batch(
    captured: list[tuple[Any, dict[str, Any]]], root: Any, request: Any
) -> tuple[SessionBase, dict[str, Any], set[str]] | None:
    """Prepare a current-route batch and prune only its previously indexed slots."""
    from ._exposure_children import child_state_key

    session = getattr(request, "session", None)
    if type(session) not in _SERVER_SESSION_TYPES or not session.session_key:
        if captured:
            raise ExposureError("Child state session unavailable")
        return None
    route = request.path
    child_state_key(route, ("validation",))  # validate the trusted route even for an empty tree
    key = _index_key(route)
    session_key = session.session_key
    previous, previous_shell = _read_paths(session.get(key))
    if session.session_key != session_key:
        raise ExposureError("Child state session changed")
    paths: set[tuple[str, ...]] = set()
    updates = {}
    for adapter, envelope in captured:
        if adapter.session is not session or adapter.route != route:
            raise ExposureError("Child state batch identity changed")
        adapter._check_session()
        paths.add(adapter.slots)
        updates[adapter.key] = envelope
    regions = getattr(root, "_explicit_child_render_regions", {})
    shell_paths = {path for path in paths if path[0] in regions and regions[path[0]][1] == "shell"}
    if getattr(root, "_explicit_child_render_scope", None) != "full" and not getattr(
        root, "_explicit_child_rendered_full", False
    ):
        registry = getattr(root, "_child_views", {})
        retained_shell = {path for path in previous_shell - paths if path[0] not in registry}
        paths |= retained_shell
        shell_paths |= retained_shell
    if not paths and not previous:
        return None
    removed = {child_state_key(route, path) for path in previous - paths}
    if paths:
        updates[key] = _index(paths, shell_paths)
    else:
        removed.add(key)
    return session, updates, removed
