"""StickyChildRegistry — per-LiveView registry of embedded child LiveViews.

This mixin is the foundation for nested LiveViews. **Phase A** (current PR)
ships the registry + event-dispatch routing only — the plumbing needed for
``{% live_render %}`` to embed a LiveView inside another LiveView. Phase B
(follow-up PR) layers sticky preservation across ``live_redirect`` on top.

Responsibilities in this phase:

* Maintain ``self._child_views`` — a ``{view_id: LiveView}`` map of children
  embedded in this view's template via ``{% live_render %}``.
* Assign unique ``view_id`` strings so the consumer's inbound event dispatch
  (``websocket.py``) can route per-view.
* Run a best-effort ``_cleanup_on_unregister`` hook on children when they
  leave the registry (WS disconnect, live_redirect unmount, etc.) so they
  can cancel pending async work / release resources.

The mixin stores NO sticky-specific state. Phase B will add
``_sticky_preserved`` and orchestrate detach/reattach; keeping Phase A
minimal makes the diff reviewable.
"""

from __future__ import annotations

import inspect
import itertools
import logging
from typing import Any, Dict, Optional

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

# Module-level counter for auto-generated view_ids. Monotonic across the
# process — the stamps are only meaningful per-connection, but uniqueness
# matters for WS event routing when two embeds of the same class live
# under the same parent.
_view_id_counter = itertools.count(1)


# ---------------------------------------------------------------------------
# ADR-018 iter 18a — sticky-child state persistence (SAVE side).
#
# A sticky child is a full ``LiveView`` embedded via ``{% live_render
# sticky=True %}`` and registered on the parent's ``StickyChildRegistry``
# keyed by its stable ``sticky_id`` class attr. Unlike LiveComponents (which
# already persist via the parent's ``get_context_data()`` walk), a sticky
# child's event-driven state was silently lost across a WebSocket reconnect.
#
# These module-level helpers implement the SAVE side: a stable session-key
# scheme (Decision 1), a both-opt-in gate (Decision 5), per-child save
# bodies for both transports (Decision 4), and a sticky-id GC ledger that
# prunes orphaned entries (Decision 3). The LOAD/restore side is iter 18b.
# ---------------------------------------------------------------------------


def sticky_child_session_key(parent_path: str, sticky_id: str) -> str:
    """Public-state session key for a sticky child. ADR-018 Decision 1.

    Namespaced by the *embedding parent's* request path so the same sticky
    child class embedded under different routes keeps distinct state.
    The private-state key is this string + ``__private`` (built by callers,
    mirroring the parent's own ``f"{save_view_key}__private"`` shape).
    """
    return f"liveview_{parent_path}__sticky__{sticky_id}"


def sticky_ids_index_key(parent_path: str) -> str:
    """GC-ledger session key listing rendered sticky_ids. ADR-018 Decision 3.

    The ledger holds the set of ``sticky_id``s rendered in the most recent
    render cycle. The save path uses it to prune ``__sticky__*`` entries for
    children no longer rendered — it does NOT drive restore (18b's
    tag-driven restore self-restores per ``{% live_render %}`` invocation).
    """
    return f"liveview_{parent_path}__sticky_ids"


def sticky_child_should_persist(child: Any, parent: Any) -> bool:
    """ADR-018 Decision 5 — both child and parent must opt into
    ``enable_state_snapshot``, and the child must have a truthy ``sticky_id``.

    Requiring the parent too keeps the reconnect-restored subtree
    tree-consistent: a child must not restore to saved state while its
    parent re-``mount()``s fresh. A child that opts in under a parent that
    does not is a misconfiguration — surfacing it (a ``djust check`` + a
    runtime warning) is iter 18c; 18a silently skips the save.
    """
    return bool(
        getattr(child, "sticky_id", None)
        and getattr(child, "enable_state_snapshot", False)
        and getattr(parent, "enable_state_snapshot", False)
    )


def warn_sticky_child_optin_skip(child: Any, parent: Any) -> None:
    """ADR-018 iter 18c — runtime one-shot warning for the Decision-5
    opt-in mismatch.

    Emits a ``logger.warning`` exactly once per ``(parent-class, sticky_id)``
    when a sticky-child save is skipped because the CHILD opted into
    ``enable_state_snapshot`` but its PARENT did not — the precise
    misconfiguration :func:`sticky_child_should_persist` rejects.

    The helper re-checks the misconfiguration internally, so call sites
    (the WS save-block else-branch, the HTTP save sweep) can invoke it
    unconditionally for any non-parent child without duplicating the gate
    logic — it is a no-op unless EXACTLY this misconfiguration holds:

    * the child has a truthy ``sticky_id`` AND ``enable_state_snapshot=True``
      (a non-sticky or non-opted-in child has nothing to persist), and
    * the parent does NOT set ``enable_state_snapshot`` (a both-opted-in
      pair runs the save — no misconfig).

    Dedup is via :func:`~djust.utils.emit_one_shot_class_warning` with the
    key ``sticky_optin_<sticky_id>`` on the *parent* class — exactly "once
    per ``(parent, sticky_id)``" per ADR-018 Decision 5. Two distinct
    sticky children under one parent get two sentinels; the same child
    re-embedded under a different parent class gets a fresh sentinel.

    The static counterpart is the ``djust.V011`` system check
    (``check_sticky_child_optin``); this runtime warning is the safety net
    for misconfigurations the static template scan cannot see (dynamic
    ``{% live_render %}`` paths, templates with no statically-resolvable
    parent class).
    """
    sticky_id = getattr(child, "sticky_id", None)
    if not (sticky_id and getattr(child, "enable_state_snapshot", False)):
        return  # child not opted in / non-sticky — nothing to warn about
    if getattr(parent, "enable_state_snapshot", False):
        return  # both opted in — the save runs, no misconfiguration

    from ..utils import emit_one_shot_class_warning

    emit_one_shot_class_warning(
        type(parent),
        "sticky_optin_%s" % sticky_id,
        "Sticky child %r (sticky_id=%r) has enable_state_snapshot=True but "
        "its parent %r does not — the child's state is NOT persisted across "
        "reconnect. Set enable_state_snapshot=True on the parent too "
        "(ADR-018 Decision 5).",
        type(child).__name__,
        sticky_id,
        type(parent).__name__,
    )


def _collect_sticky_child_state(child: Any, get_context_data: dict) -> dict:
    """Filter LiveComponents out of a child's public context dict.

    Shared by the async and sync save bodies so both serialize the same
    public-state shape. ``get_context_data`` is the already-resolved context
    dict from the child's ``get_context_data()``.
    """
    # Lazy import to avoid a circular import (components -> live_view -> mixins).
    from ..components.base import LiveComponent as _LC

    return {k: v for k, v in get_context_data.items() if not isinstance(v, _LC)}


async def save_sticky_child_state(child: Any, save_session: Any, parent_path: str) -> None:
    """Persist one sticky child's public + private state to ``save_session``.

    ADR-018 Decision 1/4. Mirrors the parent save body in
    ``websocket.py`` ``_persist_state_after_event`` — private FIRST (via
    ``_get_private_state``), then public via ``get_context_data()``,
    LiveComponents filtered out, ``normalize_django_value`` applied. The
    key is :func:`sticky_child_session_key` derived from the child's
    ``sticky_id``. Does NOT call ``asave()`` — the caller batches one
    ``asave()`` after all children + the GC ledger are written. The caller
    bounds this with ``asyncio.wait_for`` and try/except (saves never break
    event handling).
    """
    from ..serialization import normalize_django_value as _normalize

    key = sticky_child_session_key(parent_path, child.sticky_id)

    # Private attrs FIRST — get_context_data() sets render-cycle internals
    # we don't want to accidentally capture.
    if hasattr(child, "_get_private_state"):
        priv = await sync_to_async(child._get_private_state)()
        if priv:
            await save_session.aset(f"{key}__private", _normalize(priv))
        else:
            try:
                await save_session.apop(f"{key}__private", None)
            except AttributeError:
                # Older Django: no apop, fall back to sync pop.
                await sync_to_async(save_session.pop)(f"{key}__private", None)

    # Public state via get_context_data() (coroutine-aware).
    gcd = child.get_context_data
    if inspect.iscoroutinefunction(gcd):
        context = await gcd()
    else:
        context = await sync_to_async(gcd)()

    state = _collect_sticky_child_state(child, context)
    await save_session.aset(key, _normalize(state))


def save_sticky_child_state_sync(child: Any, session: Any, parent_path: str) -> None:
    """Synchronous sibling of :func:`save_sticky_child_state` for the HTTP path.

    ADR-018 Decision 4 (HTTP side). The HTTP POST path is fully synchronous,
    so this uses the sync session dict API. Does NOT call ``session.save()``
    — Django saves the session at response time. Serializes the same
    public + private shape as the async variant.
    """
    from ..serialization import normalize_django_value as _normalize

    key = sticky_child_session_key(parent_path, child.sticky_id)

    if hasattr(child, "_get_private_state"):
        priv = child._get_private_state()
        if priv:
            session[f"{key}__private"] = _normalize(priv)
        else:
            session.pop(f"{key}__private", None)

    context = child.get_context_data()
    state = _collect_sticky_child_state(child, context)
    session[key] = _normalize(state)


def _rendered_sticky_ids(parent: Any) -> list:
    """Return the sorted list of sticky_ids currently registered on ``parent``.

    Sticky children are registered on ``_child_views`` keyed by their
    ``sticky_id`` (``live_tags.py`` pins ``preferred_view_id = sticky_id``),
    so the registry keys for entries whose child has a truthy ``sticky_id``
    are exactly the rendered sticky_ids. Sorted for deterministic tests +
    JSON-stable ledger writes.
    """
    children = parent._get_all_child_views() if hasattr(parent, "_get_all_child_views") else {}
    return sorted(
        view_id for view_id, child in children.items() if getattr(child, "sticky_id", None)
    )


async def write_sticky_index_and_prune(parent: Any, save_session: Any, parent_path: str) -> None:
    """Write the sticky-id GC ledger and prune orphaned ``__sticky__`` entries.

    ADR-018 Decision 3. The ledger ``liveview_<path>__sticky_ids`` holds the
    set of sticky_ids currently registered on ``parent``. Any session key
    ``liveview_<path>__sticky__<id>`` (+ ``__private``) whose ``<id>`` is in
    the OLD ledger but not the NEW set is popped (child no longer rendered).
    Does NOT call ``asave()`` — the caller batches one ``asave()``.
    """
    new_ids = _rendered_sticky_ids(parent)
    old_ids = await save_session.aget(sticky_ids_index_key(parent_path), [])

    new_set = set(new_ids)
    for old_id in old_ids:
        if old_id in new_set:
            continue
        orphan_key = sticky_child_session_key(parent_path, old_id)
        for stale_key in (orphan_key, f"{orphan_key}__private"):
            try:
                await save_session.apop(stale_key, None)
            except AttributeError:
                # Older Django: no apop, fall back to sync pop.
                await sync_to_async(save_session.pop)(stale_key, None)

    await save_session.aset(sticky_ids_index_key(parent_path), new_ids)


def write_sticky_index_and_prune_sync(parent: Any, session: Any, parent_path: str) -> None:
    """Synchronous sibling of :func:`write_sticky_index_and_prune` (HTTP path)."""
    new_ids = _rendered_sticky_ids(parent)
    old_ids = session.get(sticky_ids_index_key(parent_path), [])

    new_set = set(new_ids)
    for old_id in old_ids:
        if old_id in new_set:
            continue
        orphan_key = sticky_child_session_key(parent_path, old_id)
        session.pop(orphan_key, None)
        session.pop(f"{orphan_key}__private", None)

    session[sticky_ids_index_key(parent_path)] = new_ids


# ---------------------------------------------------------------------------
# ADR-018 iter 18b — sticky-child state persistence (LOAD/restore side).
#
# The SAVE side (18a, above) writes a sticky child's state under the stable
# ``liveview_<path>__sticky__<sticky_id>`` key on every child event. The LOAD
# side restores it: when ``{% live_render sticky=True %}`` constructs a sticky
# child during the parent's template render, the tag — BEFORE calling the
# child's ``mount()`` — invokes :func:`restore_sticky_child_state`. If saved
# state exists and the both-opt-in gate holds (Decision 5), the saved public +
# private state is applied to the child IN LIEU OF ``mount()`` state-init, and
# the ``_restore_*`` side-effect replay runs (Decision 6) — exactly mirroring
# the parent's own skip-``mount()``-on-saved-state path at
# ``websocket.py`` ``handle_mount`` (the ``saved_state`` restore region).
# ---------------------------------------------------------------------------


def restore_sticky_child_state(child: Any, parent: Any, session: Any, parent_path: str) -> bool:
    """Restore a sticky child's saved state in lieu of its ``mount()``.

    ADR-018 Decisions 2 + 6. Reads the 18a SAVE-side keys
    (:func:`sticky_child_session_key` + its ``__private`` sibling) and applies
    the saved public + private state to a freshly-constructed (but not yet
    ``mount()``-ed) sticky ``child``. Returns ``True`` when state was restored
    — the caller then SKIPS ``mount()`` — and ``False`` otherwise (the caller
    runs ``mount()`` fresh).

    Mirrors the parent's own restore path (``websocket.py`` ``handle_mount``,
    the ``saved_state``/``safe_setattr``/``_restore_private_state`` region):

    1. Decision 5 both-opt-in gate (:func:`sticky_child_should_persist`) — a
       child only restores when BOTH it and ``parent`` opt into
       ``enable_state_snapshot``, keeping the reconnect-restored subtree
       tree-consistent.
    2. ``session.get`` the public state under the stable key; absent → fresh
       ``mount()``.
    3. ``safe_setattr`` each public attr (``allow_private=False``).
    4. ``_restore_private_state`` for the ``__private`` sibling key.
    5. ``_restore_*`` side-effect replay (Decision 6), each ``hasattr``-guarded:
       ``_restore_upload_configs`` / ``_restore_presence`` /
       ``_restore_listen_channels``.
    6. ``_initialize_temporary_assigns`` (idempotent).

    Synchronous: the ``{% live_render %}`` eager branch is fully synchronous on
    both transports (WS render runs under ``sync_to_async``, HTTP render is
    sync), so the sync session dict API is correct in both contexts. Returns
    ``False`` (the caller mounts fresh) when ``session`` is ``None``, the gate
    fails, or no saved state exists.

    This helper assumes a **well-formed** saved entry (a dict, as written by
    the 18a save path). A corrupt non-dict session value will raise
    (e.g. ``AttributeError`` from ``.items()``); the ``{% live_render %}`` tag
    wraps the call in ``try/except`` and falls through to a fresh ``mount()``
    — the tag wrapper, not this helper, is the render-safety boundary.
    """
    if session is None:
        return False
    if not sticky_child_should_persist(child, parent):
        return False

    key = sticky_child_session_key(parent_path, child.sticky_id)
    saved = session.get(key)
    if not saved:
        return False

    # Lazy import to avoid a circular import (security -> live_view -> mixins).
    from ..security import safe_setattr

    for attr, value in saved.items():
        safe_setattr(child, attr, value, allow_private=False)

    private = session.get(f"{key}__private")
    if private and hasattr(child, "_restore_private_state"):
        child._restore_private_state(private)

    # Decision 6 — replay process-wide side effects mount() would have
    # re-issued. Each method is provided by a mixin the child may or may
    # not compose, so each is hasattr-guarded.
    if hasattr(child, "_restore_upload_configs"):
        child._restore_upload_configs()
    if hasattr(child, "_restore_presence"):
        child._restore_presence()
    if hasattr(child, "_restore_listen_channels"):
        child._restore_listen_channels()

    # Mirrors the parent restore path's post-restore ordering; idempotent
    # via the ``_temporary_assigns_initialized`` guard.
    if hasattr(child, "_initialize_temporary_assigns"):
        child._initialize_temporary_assigns()

    return True


class StickyChildRegistry:
    """Per-LiveView registry of embedded child LiveViews.

    Composed into :class:`~djust.live_view.LiveView` via the MRO list in
    ``live_view.py``. Consumers must call :meth:`_init_sticky` from
    ``__init__`` so the ``_child_views`` dict exists before any template
    tag tries to register into it.
    """

    # Populated by ``_init_sticky``; declared here for type checkers.
    _child_views: Dict[str, Any]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_sticky(self) -> None:
        """Initialise child-view storage. Called from ``LiveView.__init__``."""
        self._child_views = {}

    # ------------------------------------------------------------------
    # view_id assignment
    # ------------------------------------------------------------------

    def _assign_view_id(self, preferred: Optional[str] = None) -> str:
        """Return a unique ``view_id`` for a new child.

        If ``preferred`` is supplied and is not already in use on this
        parent, it is honored — otherwise a monotonic ``child_N`` stamp
        is generated. The auto-generated form is the default path used
        by ``{% live_render %}`` when the caller doesn't pin a stable id.
        """
        if preferred and preferred not in getattr(self, "_child_views", {}):
            return preferred
        return f"child_{next(_view_id_counter)}"

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _register_child(self, view_id: str, child: Any) -> None:
        """Register ``child`` under ``view_id`` on this parent.

        Wires the reverse parent/view_id pointers on the child so it can
        route events upward and identify itself in VDOM patches.

        Raises ``ValueError`` if ``view_id`` is already registered —
        template authors must use distinct ids within one parent.
        """
        if not hasattr(self, "_child_views"):
            self._init_sticky()
        if view_id in self._child_views:
            raise ValueError(
                "child view_id=%r already registered on %s" % (view_id, type(self).__name__)
            )
        self._child_views[view_id] = child
        # Bookkeep parent + id on the child so it can route events up /
        # send patches down. Phase B will also need these for preservation.
        child._parent_view = self
        child._view_id = view_id

    # ------------------------------------------------------------------
    # De-registration
    # ------------------------------------------------------------------

    def _unregister_child(self, view_id: str) -> None:
        """Drop ``view_id`` from the registry and run the child's cleanup hook.

        The cleanup hook (optional on the child) is wrapped so a buggy
        hook can't break the disconnect loop — we log and continue.
        """
        if not hasattr(self, "_child_views"):
            return
        child = self._child_views.pop(view_id, None)
        if child is None:
            return
        cleanup = getattr(child, "_cleanup_on_unregister", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:  # noqa: BLE001 — defensive: never break disconnect
                logger.exception("child view %s _cleanup_on_unregister raised", view_id)

    # ------------------------------------------------------------------
    # Queries (used by the consumer's event-dispatch path)
    # ------------------------------------------------------------------

    def _get_all_child_views(self) -> Dict[str, Any]:
        """Return the full ``{view_id: child}`` map (read-only view).

        Returns an empty dict if the mixin was never initialized — e.g.
        a custom subclass that overrides ``__init__`` without calling
        super(). This is the defensive shape the WS consumer has relied
        on via ``hasattr`` guards.
        """
        return getattr(self, "_child_views", {})

    def _get_child_view(self, view_id: str) -> Optional[Any]:
        """Return a specific child by ``view_id``, or None."""
        return self._get_all_child_views().get(view_id)

    # ------------------------------------------------------------------
    # Sticky preservation (Phase B)
    # ------------------------------------------------------------------

    def _on_sticky_unmount(self) -> None:
        """Called when a sticky child is discarded during a navigation
        (new layout has no matching ``[dj-sticky-slot]``, or an auth
        re-check failed).

        The default implementation cancels any in-flight background
        tasks via :meth:`AsyncWorkMixin.cancel_async_all` (if the view
        mixes it in — most LiveViews do, via the base class). Without
        this, a sticky with running ``start_async`` tasks that gets
        unmounted would leak the tasks: they'd keep executing against
        a detached view whose ``request`` is stale and whose consumer
        reference may already be gone.

        Subclasses that override this to release other resources
        (audio streams, open files, etc.) should call
        ``super()._on_sticky_unmount()`` to preserve task cleanup.

        This hook is ONLY called during a live_redirect transition that
        discards the sticky — a full WS disconnect takes the normal
        :meth:`_unregister_child` -> ``_cleanup_on_unregister`` path.
        """
        cancel_all = getattr(self, "cancel_async_all", None)
        if callable(cancel_all):
            try:
                cancel_all()
            except Exception:  # noqa: BLE001 — cleanup hook must not raise
                logger.exception("sticky _on_sticky_unmount: cancel_async_all() failed")
        return None

    def _preserve_sticky_children(self, new_request: Any) -> Dict[str, Any]:
        """Stage sticky children for preservation across a live_redirect.

        Walks :attr:`_child_views`, filters to children with
        ``sticky is True``, and re-checks each child's auth against
        ``new_request`` via
        :func:`djust.auth.core.check_view_auth_lightweight`. Survivors
        are returned in a ``{sticky_id: child}`` map (keyed by each
        child's ``sticky_id`` class attr) for the consumer to stash on
        ``self._sticky_preserved``.

        Non-sticky children and auth-denied sticky children are NOT
        included; the caller is responsible for letting the normal
        unmount flow run on the old view.
        """
        # Lazy import to avoid circular: auth.core -> live_view -> mixins -> auth.
        from ..auth.core import check_view_auth_lightweight

        survivors: Dict[str, Any] = {}
        for _view_id, child in self._get_all_child_views().items():
            if getattr(child, "sticky", False) is not True:
                continue
            sticky_id = getattr(child, "sticky_id", None) or _view_id
            if not check_view_auth_lightweight(child, new_request):
                logger.info(
                    "Sticky child %s auth denied for new request; discarding",
                    sticky_id,
                )
                hook = getattr(child, "_on_sticky_unmount", None)
                if callable(hook):
                    try:
                        hook()
                    except Exception:  # noqa: BLE001 — defensive
                        logger.exception("sticky child %s _on_sticky_unmount raised", sticky_id)
                continue
            # Update request back-reference so handlers see the new request.
            # Some child types (slot descriptors, read-only proxies) can't
            # accept a `request` attr. When this happens, downstream
            # per-event object-permission checks for this child WILL fail
            # closed (websocket_utils.py:234, #1380) — log at WARNING so
            # the gap is observable at its source rather than silently at
            # the denial site.
            try:
                child.request = new_request
            except AttributeError:
                logger.warning(
                    "sticky child %s does not accept request attribute "
                    "(read-only proxy?); per-event object-permission "
                    "checks will fail closed for this child until "
                    "request is stamped",
                    sticky_id,
                )
            survivors[sticky_id] = child
        return survivors
