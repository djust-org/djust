"""
Hot View Replacement (HVR) — v0.6.1

State-preserving Python code reload for djust LiveViews in development.
When a LiveView module changes on disk, the dev server ``importlib.reload()``s
the module and swaps ``__class__`` in place on every live instance of the
changed class, then re-renders via the existing VDOM diff path.

Users keep form input, counter values, active tab, and scroll position —
React Fast Refresh parity for djust. Gated on ``DEBUG=True`` +
``LIVEVIEW_CONFIG["hvr_enabled"]`` (default True). Falls back to a full
reload on a conservative state-compat heuristic (removed handlers, changed
handler signatures, or slot layout drift).

This module is development-only. :func:`enable_hot_reload` early-returns
on ``DEBUG=False`` so none of these code paths are reachable in production.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("djust.hvr")

# Serialize module-reload across multiple watcher threads. Rapid saves
# can fire the watchdog callback from different threads; without this
# lock two reloads of the same module can race importlib's internal
# state.
_hvr_lock = threading.Lock()


@dataclass
class ReloadResult:
    """Result of a successful HVR module reload.

    Attributes:
        module_name: Fully-qualified dotted name of the reloaded module
            (e.g. ``"app.views.dashboard"``).
        class_pairs: List of ``(old_cls, new_cls)`` tuples for every
            LiveView subclass defined directly in the module.
        reload_id: UUID-hex string that consumers use to dedupe
            broadcasts within a rapid-save burst.
    """

    module_name: str
    class_pairs: List[Tuple[type, type]] = field(default_factory=list)
    reload_id: str = ""


def _find_module_for_path(path: str) -> Optional[ModuleType]:
    """Locate the already-imported module whose ``__file__`` matches ``path``.

    Only looks at modules already present in :data:`sys.modules` — we
    never trigger a first-time import from a disk path. This keeps the
    attack surface tight: HVR can only act on code the Django process
    has already chosen to run.

    Args:
        path: File path reported by the watchdog callback.

    Returns:
        The matching module, or ``None`` if nothing in ``sys.modules``
        points at this file.
    """
    # Use realpath (not abspath) so symlinked source trees (e.g.
    # editable installs pointed at a worktree) resolve to the same
    # canonical path on both sides of the comparison.
    abs_path = os.path.realpath(path)
    for _name, mod in list(sys.modules.items()):
        mod_file = getattr(mod, "__file__", None)
        if mod_file and os.path.realpath(mod_file) == abs_path:
            return mod
    return None


def _module_has_liveview(module: ModuleType) -> bool:
    """Check whether ``module`` defines at least one LiveView subclass locally.

    "Locally" = ``attr.__module__ == module.__name__`` — we skip classes
    that were merely re-imported from elsewhere (they belong to their
    original module's reload, if any).

    Args:
        module: The module to inspect.

    Returns:
        ``True`` iff at least one locally-defined class subclasses
        :class:`djust.live_view.LiveView`.
    """
    from djust.live_view import LiveView

    for attr in module.__dict__.values():
        if (
            isinstance(attr, type)
            and issubclass(attr, LiveView)
            and attr is not LiveView
            and getattr(attr, "__module__", None) == module.__name__
        ):
            return True
    return False


def _list_event_handlers(cls: type) -> Dict[str, Callable[..., Any]]:
    """Return ``{name: handler_fn}`` for all ``@event_handler``-decorated methods.

    Reads the decorator marker :attr:`_djust_decorators` set by
    :func:`djust.decorators.event_handler`. We intentionally look at
    ``cls.__dict__`` rather than walking the MRO — inherited handlers
    from mixins aren't in scope for the compat check because mixins
    themselves aren't being swapped.
    """
    out: Dict[str, Callable[..., Any]] = {}
    for name, attr in cls.__dict__.items():
        if callable(attr) and hasattr(attr, "_djust_decorators"):
            decorators = getattr(attr, "_djust_decorators", {}) or {}
            if "event_handler" in decorators:
                out[name] = attr
    return out


def _signatures_compatible(old_cls: type, new_cls: type, name: str) -> bool:
    """Return True iff two methods' positional param lists match.

    Positional params = ``POSITIONAL_ONLY`` or ``POSITIONAL_OR_KEYWORD``.
    Matching both **count** and **names** — a rename counts as a breaking
    change because handler code in the class body is about to reference
    the new names, while the live instance's bound caller still passes
    the old ones.

    Also checks ``VAR_KEYWORD`` parity: if the old handler accepted
    ``**kwargs`` and the new one does not, reject. The live WebSocket
    dispatch path unconditionally passes extra kwargs from the event
    payload, so dropping ``**kwargs`` would raise ``TypeError`` on the
    next event dispatch.

    Catches signature-inspection errors and returns ``False`` so the
    consumer falls back to full reload rather than silently applying a
    swap against a signature we couldn't read.
    """
    try:
        old_sig = inspect.signature(getattr(old_cls, name))
        new_sig = inspect.signature(getattr(new_cls, name))
    except (TypeError, ValueError):
        return False
    old_pos = [
        p.name
        for p in old_sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    new_pos = [
        p.name
        for p in new_sig.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if old_pos != new_pos:
        return False

    # VAR_KEYWORD parity: if old accepted **kwargs, new must too (the
    # live WS dispatch passes extra kwargs from the event payload;
    # removing **kwargs would TypeError on the next event).
    old_has_var_kw = any(p.kind is p.VAR_KEYWORD for p in old_sig.parameters.values())
    new_has_var_kw = any(p.kind is p.VAR_KEYWORD for p in new_sig.parameters.values())
    if old_has_var_kw and not new_has_var_kw:
        return False

    return True


def _mro_names(cls: type) -> tuple:
    """Return a stable identity tuple of the MRO (minus ``LiveView`` / ``object``).

    Used by :func:`_is_state_compatible` to detect when a LiveView's base
    classes have changed between saves (e.g. a mixin was added or
    dropped). We skip ``cls`` itself (since we're comparing two versions
    of it), ``LiveView`` (the always-present base), and ``object`` (the
    always-present terminus) — what remains is the user-level mixin
    chain whose drift we care about.
    """
    from djust.live_view import LiveView

    return tuple(
        (b.__module__, b.__name__)
        for b in cls.__mro__
        if b is not cls and b is not LiveView and b is not object
    )


def _is_state_compatible(old_cls: type, new_cls: type) -> Tuple[bool, str]:
    """Conservative heuristic: can ``new_cls`` safely replace ``old_cls`` in
    place on a live instance?

    Returns ``(ok, reason)``. ``reason`` is the empty string on success
    or a short machine-readable code on failure (used for telemetry and
    developer-console logs).

    Rejects if ANY of these hold:
      1. ``__slots__`` layout changed — Python itself requires slot
         equality for the ``__class__`` assignment.
      2. ``new_cls`` no longer subclasses :class:`LiveView`.
      3. The MRO changed (excluding ``LiveView`` and ``object``) — a
         dropped or added mixin can silently change which handlers /
         slots the instance exposes, and our per-class handler walk
         only inspects ``cls.__dict__``, so MRO drift is not otherwise
         visible to the compat check.
      4. An old event handler is missing from the new class.
      5. A surviving handler's positional-param signature changed, or
         its ``**kwargs`` acceptance was dropped.

    Permissive about everything else (new handlers, body changes to
    ``mount`` / template paths, new public attributes) — those all
    take effect on the next render without requiring a state migration.
    """
    old_slots = tuple(getattr(old_cls, "__slots__", ()))
    new_slots = tuple(getattr(new_cls, "__slots__", ()))
    if old_slots != new_slots:
        return False, "slots_changed"

    from djust.live_view import LiveView

    if not issubclass(new_cls, LiveView):
        return False, "not_liveview"

    if _mro_names(old_cls) != _mro_names(new_cls):
        return False, "mro_changed"

    old_handlers = _list_event_handlers(old_cls)
    new_handlers = _list_event_handlers(new_cls)
    for name in old_handlers:
        if name not in new_handlers:
            return False, f"handler_removed:{name}"
        if not _signatures_compatible(old_cls, new_cls, name):
            return False, f"handler_sig_changed:{name}"
    return True, ""


def apply_class_swap(
    view_instance: Any,
    class_pairs: List[Tuple[type, type]],
    _visited: Optional[set] = None,
) -> Tuple[bool, str]:
    """Swap ``view_instance.__class__`` (and its children) to the matching new class.

    ``class_pairs`` can be either:
      * ``[(old_cls, new_cls), ...]`` — used by the watcher path where the
        pre-reload old-class references are still live locals, OR
      * ``[(new_cls, new_cls), ...]`` — used by the consumer path where
        the module has already been reloaded and only the new class
        objects are accessible via ``sys.modules``.

    In both cases we match each live instance's current class against
    the pair by **fully-qualified name + module** — that identity
    survives ``importlib.reload`` (the new class replaces the old at
    the same module attribute name) and is the only reliable way to
    find the "right" swap target on the consumer side.

    Recurses into any ``_child_views`` the instance may hold (Phase A
    sticky views), so nested composition reloads atomically. A shared
    ``_visited`` set of ``id(instance)`` values guards against cyclic
    ``_child_views`` references (A holds B and B holds A) which would
    otherwise blow the stack with a :class:`RecursionError`.

    Args:
        view_instance: Live LiveView instance to patch.
        class_pairs: Candidate pairs from :class:`ReloadResult`.
        _visited: Internal — set of ``id(instance)`` values already
            processed in this traversal. Callers should not supply.

    Returns:
        ``(True, "")`` on success or if nothing matched (the instance
        isn't of a changed class). ``(False, reason)`` if a match was
        found but :func:`_is_state_compatible` rejected it — caller
        should fall back to a full page reload.
    """
    from djust.live_view import LiveView

    if _visited is None:
        _visited = set()
    key = id(view_instance)
    if key in _visited:
        # Already processed in this traversal — cyclic _child_views.
        return True, ""
    _visited.add(key)

    current_cls = view_instance.__class__
    if not isinstance(view_instance, LiveView):
        logger.debug(
            "apply_class_swap: %s is not a LiveView; skipping",
            type(view_instance).__name__,
        )
        return True, ""  # not a LiveView — nothing to swap

    match: Optional[Tuple[type, type]] = None
    for old_cls, new_cls in class_pairs:
        if (
            current_cls.__name__ == new_cls.__name__
            and current_cls.__module__ == new_cls.__module__
        ):
            match = (current_cls, new_cls)
            break

    if match is None:
        # Instance is not of any changed class — children may still match.
        pass
    else:
        old_cls, new_cls = match
        ok, reason = _is_state_compatible(old_cls, new_cls)
        if not ok:
            return False, reason
        if old_cls is not new_cls:
            try:
                view_instance.__class__ = new_cls
            except TypeError as e:
                # Slot-layout mismatch Python couldn't catch earlier, or
                # immutable type. Fall back to full reload.
                return False, f"class_assign_failed:{e}"

    # Recurse into sticky / composed children.
    if hasattr(view_instance, "_get_all_child_views"):
        try:
            children = view_instance._get_all_child_views()
        except Exception:  # noqa: BLE001 — defensive; dev-only
            children = {}
        for child in list(children.values()):
            sub_ok, sub_reason = apply_class_swap(child, class_pairs, _visited=_visited)
            if not sub_ok:
                return False, f"child({child.__class__.__name__}):{sub_reason}"

    return True, ""


def reload_module_if_liveview(path: str) -> Optional[ReloadResult]:
    """Reload the module for ``path`` if it defines a LiveView subclass.

    Resolves ``path`` → module via :data:`sys.modules` (already-imported
    only). If the module doesn't exist in ``sys.modules`` or defines no
    LiveView subclasses locally, returns ``None`` and the caller falls
    back to the legacy template-refresh path.

    Captures old class refs, calls :func:`importlib.reload`, then builds
    a ``(old, new)`` pair for every surviving class. A class that was
    *removed* from the new module triggers a full-reload fallback
    (``None`` return) — existing instances would be orphaned and HVR
    has no safe recovery.

    ``SyntaxError`` / ``ImportError`` during reload is logged and
    swallowed — live consumers keep running the pre-save class, which
    is the correct dev UX ("typo; I'll fix it and resave").

    Each file-watcher invocation produces a fresh ``reload_id``
    (``uuid4``). A failed HVR swap is not retried with the same id;
    the next file save starts a new broadcast with a new id. Consumers
    therefore dedupe on ``reload_id`` at receipt without worrying about
    lost retries — rapid saves always produce different ids.

    Args:
        path: Absolute file path from the watchdog callback.

    Returns:
        :class:`ReloadResult` with a fresh ``reload_id`` on success,
        or ``None`` if HVR doesn't apply (not a LiveView module, not
        already imported, import error, or a class was removed).
    """
    module = _find_module_for_path(path)
    if module is None:
        return None
    if not _module_has_liveview(module):
        return None

    with _hvr_lock:
        from djust.live_view import LiveView

        old_classes: Dict[str, type] = {
            name: attr
            for name, attr in module.__dict__.items()
            if isinstance(attr, type)
            and issubclass(attr, LiveView)
            and attr is not LiveView
            and getattr(attr, "__module__", None) == module.__name__
        }
        try:
            importlib.reload(module)
        except Exception:  # noqa: BLE001 — dev-only; log + fall through
            logger.exception("HVR: importlib.reload failed for %s", path)
            return None

        new_classes: Dict[str, type] = {
            name: attr
            for name, attr in module.__dict__.items()
            if isinstance(attr, type)
            and issubclass(attr, LiveView)
            and attr is not LiveView
            and getattr(attr, "__module__", None) == module.__name__
        }

        for name in old_classes:
            if name not in new_classes:
                logger.warning(
                    "HVR: class %s removed from %s; full reload needed",
                    name,
                    module.__name__,
                )
                return None

        class_pairs = [(old_classes[name], new_classes[name]) for name in old_classes]

    return ReloadResult(
        module_name=module.__name__,
        class_pairs=class_pairs,
        reload_id=uuid.uuid4().hex,
    )


def _resolve_class_pairs(
    module_name: str, class_names: List[str]
) -> Optional[List[Tuple[type, type]]]:
    """Resolve post-reload ``(new, new)`` class pairs by name.

    Used by :class:`djust.websocket.LiveViewConsumer` on the receiving
    side of a broadcast — by the time the consumer runs, the watcher
    thread has already called :func:`importlib.reload`, so the old
    class refs are gone from ``sys.modules``. We re-resolve by
    name+module and rely on :func:`apply_class_swap` to match each
    live instance's ``__class__`` against the pair.

    Args:
        module_name: From ``ReloadResult.module_name``.
        class_names: From ``ReloadResult.class_pairs`` — list of
            ``new_cls.__name__`` strings.

    Returns:
        List of ``(new_cls, new_cls)`` tuples, or ``None`` if the
        module isn't in ``sys.modules`` (worker restart?) or any named
        class is missing (mid-edit save?).
    """
    module = sys.modules.get(module_name)
    if module is None:
        return None
    pairs: List[Tuple[type, type]] = []
    for name in class_names:
        new_cls = module.__dict__.get(name)
        if new_cls is None or not isinstance(new_cls, type):
            return None
        pairs.append((new_cls, new_cls))
    return pairs


async def broadcast_hvr_event(result: ReloadResult, file_path: str) -> None:
    """Broadcast a hotreload event with HVR metadata to all connected clients.

    Reuses the existing ``djust_hotreload`` channel group (see
    ADR note in the implementation plan §2.5). The extra ``hvr_meta``
    payload is the only thing that distinguishes an HVR burst from the
    legacy template-only path.

    Args:
        result: From :func:`reload_module_if_liveview`.
        file_path: Original file path — forwarded to the client for
            debug-panel / indicator display.
    """
    from channels.layers import get_channel_layer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    await channel_layer.group_send(
        "djust_hotreload",
        {
            "type": "hotreload",
            "file": file_path,
            "hvr_meta": {
                "module": result.module_name,
                "class_names": [new.__name__ for _, new in result.class_pairs],
                "reload_id": result.reload_id,
            },
        },
    )


__all__ = [
    "ReloadResult",
    "apply_class_swap",
    "broadcast_hvr_event",
    "reload_module_if_liveview",
]
