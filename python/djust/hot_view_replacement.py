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
import importlib.util
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
            LiveView subclass the reload rebuilt: those defined in the module
            and those of the dependent modules reloaded with it.
        reload_id: UUID-hex string that consumers use to dedupe
            broadcasts within a rapid-save burst.
        modules: ``{module_name: [class names]}`` for every module reloaded,
            the saved one first.
    """

    module_name: str
    class_pairs: List[Tuple[type, type]] = field(default_factory=list)
    reload_id: str = ""
    modules: Dict[str, List[str]] = field(default_factory=dict)


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
    """Return ``{name: handler_fn}`` for the ``@event_handler`` methods ``cls`` declares.

    Uses the discovery dispatch uses (ADR-037 D1), limited to ``cls`` itself:
    inherited handlers from mixins aren't in scope for the compat check
    because mixins themselves aren't being swapped.
    """
    from ._parameter_metadata import declared_handlers

    return {
        handler.name: handler.function
        for handler in declared_handlers(cls, lambda klass: klass is not cls)
    }


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
            # ADR-038: an in-place swap never runs the construction guard. A
            # policy change would keep state built under one exposure contract
            # in a view governed by another, so it reloads instead.
            if getattr(old_cls, "exposure_policy", None) != getattr(
                new_cls, "exposure_policy", None
            ):
                return False, "exposure_policy_changed"
            try:
                view_instance.__class__ = new_cls
            except TypeError as e:
                # Slot-layout mismatch Python couldn't catch earlier, or
                # immutable type. Fall back to full reload.
                return False, f"class_assign_failed:{e}"
            try:
                view_instance._validate_exposure_configuration()
            except Exception:  # noqa: BLE001 — the new class's configuration is invalid
                view_instance.__class__ = old_cls
                return False, "exposure_configuration_invalid"

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


def _local_liveviews(module: ModuleType) -> Dict[str, type]:
    from djust.live_view import LiveView

    return {
        name: attr
        for name, attr in module.__dict__.items()
        if isinstance(attr, type)
        and issubclass(attr, LiveView)
        and attr is not LiveView
        and getattr(attr, "__module__", None) == module.__name__
    }


def _current_liveview_classes() -> List[type]:
    """Every LiveView subclass that is still its module's attribute.

    ``__subclasses__()`` also returns the classes earlier reloads replaced
    (alive while something references them); only the class a module holds
    now is current.
    """
    from djust.live_view import LiveView

    found: List[type] = []
    seen: set = set()
    stack = list(LiveView.__subclasses__())
    while stack:
        cls = stack.pop()
        if id(cls) in seen:
            continue
        seen.add(id(cls))
        stack.extend(cls.__subclasses__())
        module = sys.modules.get(getattr(cls, "__module__", ""))
        if module is not None and module.__dict__.get(cls.__name__) is cls:
            found.append(cls)
    return found


def _dependent_modules(module: ModuleType) -> List[ModuleType]:
    """Modules to reload after ``module``, bases before subclasses.

    A module is dependent when it defines a current LiveView whose MRO holds
    a class defined in ``module``: a mixin or base class the save changed, or
    a view of ``module`` subclassed elsewhere. The MRO is complete, so a
    subclass of a subclass is found directly. Only modules with a source file
    count; ``djust`` itself and ``__main__`` never reload.
    """
    owned = {
        id(attr)
        for attr in module.__dict__.values()
        if isinstance(attr, type) and getattr(attr, "__module__", None) == module.__name__
    }
    if not owned:
        return []
    candidates: Dict[str, List[type]] = {}
    for cls in _current_liveview_classes():
        name = cls.__module__
        if name == module.__name__ or name == "__main__" or name.split(".")[0] == "djust":
            continue
        if any(id(base) in owned for base in cls.__mro__):
            candidates.setdefault(name, []).append(cls)
    modules = {
        name: sys.modules[name]
        for name in candidates
        if getattr(sys.modules.get(name), "__file__", None)
    }
    # Reload a module only after every candidate module its classes inherit
    # from (Kahn's order; a cycle keeps the remaining modules in name order).
    needs = {
        name: {
            base.__module__
            for cls in candidates[name]
            for base in cls.__mro__[1:]
            if base.__module__ in modules and base.__module__ != name
        }
        for name in modules
    }
    order: List[str] = []
    while needs:
        ready = sorted(name for name, deps in needs.items() if not deps) or sorted(needs)[:1]
        for name in ready:
            order.append(name)
            needs.pop(name)
        for deps in needs.values():
            deps.difference_update(ready)
    return [modules[name] for name in order]


def _drop_cached_bytecode(module: ModuleType) -> None:
    """Remove ``module``'s ``.pyc`` so the reload compiles the saved source.

    A ``.pyc`` stays valid while the source's whole-second mtime and size are
    unchanged, so a second save within the same second that keeps the size
    (``v1`` -> ``v2``) would otherwise reload the previous save's bytecode.
    """
    source = getattr(module, "__file__", None)
    if not source or not source.endswith(".py"):
        return
    try:
        cached = importlib.util.cache_from_source(source)
    except (NotImplementedError, ValueError):
        return
    try:
        os.remove(cached)
    except FileNotFoundError:
        return
    except OSError:
        logger.debug("HVR: could not remove %s; the reload may reuse it", cached)


def reload_module_if_liveview(path: str) -> Optional[ReloadResult]:
    """Reload the module for ``path``, and the views built on its classes.

    Resolves ``path`` → module via :data:`sys.modules` (already-imported
    only). The module is reloaded when it defines a LiveView subclass or a
    class (a mixin, a base view) that a LiveView in another module inherits
    from. Each such dependent module is then reloaded too, bases before
    subclasses, so its classes are rebuilt on the new code: otherwise they
    would keep inheriting from the class objects they were created with. A
    module with neither returns ``None`` and the caller falls back to the
    legacy template-refresh path.

    Captures old class refs, calls :func:`importlib.reload`, then builds
    a ``(old, new)`` pair for every surviving class. A class that was
    *removed* from a reloaded module triggers a full-reload fallback
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
        or ``None`` if HVR doesn't apply (no LiveView depends on the module,
        not already imported, import error, or a class was removed).
    """
    module = _find_module_for_path(path)
    if module is None:
        return None

    with _hvr_lock:
        dependents = _dependent_modules(module)
        if not dependents and not _module_has_liveview(module):
            return None

        class_pairs: List[Tuple[type, type]] = []
        modules: Dict[str, List[str]] = {}
        for target in [module, *dependents]:
            old_classes = _local_liveviews(target)
            _drop_cached_bytecode(target)
            try:
                importlib.reload(target)
            except Exception:  # noqa: BLE001 — dev-only; log + fall through
                logger.exception("HVR: importlib.reload failed for %s", target.__name__)
                return None
            new_classes = _local_liveviews(target)
            for name in old_classes:
                if name not in new_classes:
                    logger.warning(
                        "HVR: class %s removed from %s; full reload needed",
                        name,
                        target.__name__,
                    )
                    return None
            class_pairs.extend((old_classes[name], new_classes[name]) for name in old_classes)
            modules[target.__name__] = list(old_classes)

        # The reloaded classes are new objects: let the live auth check
        # re-read what the URLconf routes wrap them in.
        from djust.auth.core import invalidate_url_auth_cache

        invalidate_url_auth_cache()
        _repoint_http_entries(class_pairs)

    return ReloadResult(
        module_name=module.__name__,
        class_pairs=class_pairs,
        reload_id=uuid.uuid4().hex,
        modules=modules,
    )


#: How deep :func:`_repoint_callable` follows closures and ``__wrapped__``
#: from a URLconf callback (decorators around ``as_view()`` nest a few deep).
_REPOINT_MAX_DEPTH = 16


def _repoint_callable(func: Any, replacements: Dict[int, type], seen: set, depth: int = 0) -> int:
    """Point ``func`` (and what it closes over) at the reloaded classes.

    ``as_view()`` returns a function closing over ``cls``; decorators applied
    to it close over that function. Each closure cell or ``view_class``
    holding a replaced class is rewritten in place, so the callable already in
    the URLconf builds the new class. Returns how many references changed.
    """
    if depth > _REPOINT_MAX_DEPTH or id(func) in seen or not callable(func):
        return 0
    seen.add(id(func))
    changed = 0
    view_class = getattr(func, "view_class", None)
    if isinstance(view_class, type) and id(view_class) in replacements:
        try:
            func.view_class = replacements[id(view_class)]
            changed += 1
        except (AttributeError, TypeError):
            # A read-only attribute (a builtin or C callable): the closure
            # walk below still repoints what the callable builds.
            logger.debug("HVR: view_class on %r is read-only", func)
    for cell in getattr(func, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:  # an empty cell
            continue
        if isinstance(value, type) and id(value) in replacements:
            cell.cell_contents = replacements[id(value)]
            changed += 1
        elif callable(value) and not isinstance(value, type):
            changed += _repoint_callable(value, replacements, seen, depth + 1)
    wrapped = getattr(func, "__wrapped__", None)
    if wrapped is not None:
        changed += _repoint_callable(wrapped, replacements, seen, depth + 1)
    return changed


def _iter_url_callbacks(patterns: Any, depth: int = 0) -> Any:
    if depth > _REPOINT_MAX_DEPTH:
        return
    for pattern in patterns:
        nested = getattr(pattern, "url_patterns", None)
        if nested is not None:
            yield from _iter_url_callbacks(nested, depth + 1)
            continue
        callback = getattr(pattern, "callback", None)
        if callback is not None:
            yield callback


def _repoint_http_entries(class_pairs: List[Tuple[type, type]]) -> int:
    """Make the URLconf's ``as_view()`` callables build the reloaded classes.

    The live instances are swapped by :func:`apply_class_swap`; a new page
    load goes through the callable Django resolved at import, which kept the
    pre-reload class. A class whose ``exposure_policy`` changed is left as it
    is (its HTTP entry was wrapped for the old policy): a restart picks it up.
    Covers ``settings.ROOT_URLCONF``; a per-request ``request.urlconf`` is not
    walked. Returns how many references changed.
    """
    replacements: Dict[int, type] = {}
    for old_cls, new_cls in class_pairs:
        if old_cls is new_cls:
            continue
        if getattr(old_cls, "exposure_policy", None) != getattr(new_cls, "exposure_policy", None):
            logger.warning(
                "HVR: %s changed exposure_policy; restart the server to serve it over HTTP",
                new_cls.__qualname__,
            )
            continue
        replacements[id(old_cls)] = new_cls
    if not replacements:
        return 0
    try:
        from django.urls import get_resolver

        patterns = get_resolver().url_patterns
    except Exception:  # noqa: BLE001 — dev-only; a broken URLconf has its own error
        logger.warning("HVR: could not read the URLconf; new page loads keep the old class")
        return 0
    seen: set = set()
    changed = 0
    for callback in _iter_url_callbacks(patterns):
        changed += _repoint_callable(callback, replacements, seen)
    return changed


#: Per-class facts worked out from a view's template source, dropped when a
#: template changes: the hash that keys the saved renderer, and the
#: ``dj-update`` containers that accumulate rows (which also read the files the
#: template includes, so ANY template edit can change them).
_TEMPLATE_DERIVED_CLASS_CACHES = ("_djust_template_hash_slot", "_djust_accumulating_ids")


def note_template_change(path: str) -> int:
    """Forget what every LiveView class derived from its template, after a file change.

    The state backend keys a view's saved Rust renderer by the hash of its
    template (``_initialize_rust_view``), and that hash is cached on the class
    for its lifetime (``_get_cached_template_hash_slot``). A ``.py`` reload
    makes a new class; a template-only edit does not, so the same session kept
    getting the renderer compiled from the old template. Dropping the cached
    hash makes the next render hash the edited source and miss that entry.

    The same goes for the ``dj-update`` containers found by reading the template
    (``_accumulating_containers``, #3647): cached per class, they kept the ids of
    the template as it was until a restart. They are dropped here too, for every
    class, because an edit of an included file changes them without touching the
    view's own template.

    Dev-only (the file watcher); returns how many classes were reset.
    """
    from djust.live_view import LiveView

    reset = 0
    stack = [LiveView]
    seen: set = set()
    while stack:
        cls = stack.pop()
        if id(cls) in seen:
            continue
        seen.add(id(cls))
        dropped = False
        for name in _TEMPLATE_DERIVED_CLASS_CACHES:
            if name in cls.__dict__:
                try:
                    delattr(cls, name)
                    dropped = True
                except (AttributeError, TypeError):
                    # An immutable class keeps its cache; its next page load may
                    # still use what was derived from the old template until a
                    # restart.
                    logger.debug("HVR: could not reset %s of %s", name, cls.__qualname__)
        reset += dropped
        stack.extend(cls.__subclasses__())
    logger.debug("HVR: %s changed; reset the template caches of %d view classes", path, reset)
    return reset


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


def resolve_reload_pairs(hvr_meta: Dict[str, Any]) -> Optional[List[Tuple[type, type]]]:
    """Post-reload class pairs for every module a broadcast names.

    ``hvr_meta["modules"]`` (``{module: [class names]}``) covers the saved
    module and the dependent modules reloaded with it; a broadcast without it
    names one ``module`` and its ``class_names``. ``None`` when any module or
    class is missing (see :func:`_resolve_class_pairs`).
    """
    modules = hvr_meta.get("modules") or {
        hvr_meta.get("module", ""): hvr_meta.get("class_names", []) or []
    }
    pairs: List[Tuple[type, type]] = []
    for module_name, class_names in modules.items():
        resolved = _resolve_class_pairs(module_name, list(class_names or []))
        if resolved is None:
            return None
        pairs.extend(resolved)
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
                "class_names": [
                    new.__name__
                    for _, new in result.class_pairs
                    if new.__module__ == result.module_name
                ],
                "modules": result.modules,
                "reload_id": result.reload_id,
            },
        },
    )


__all__ = [
    "ReloadResult",
    "apply_class_swap",
    "broadcast_hvr_event",
    "note_template_change",
    "resolve_reload_pairs",
    "reload_module_if_liveview",
]
