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

import itertools
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Module-level counter for auto-generated view_ids. Monotonic across the
# process — the stamps are only meaningful per-connection, but uniqueness
# matters for WS event routing when two embeds of the same class live
# under the same parent.
_view_id_counter = itertools.count(1)


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
            # accept a `request` attr — that's expected and fine; the child
            # just doesn't get the live request handle. Log at DEBUG so the
            # absence is observable without spamming production logs.
            try:
                child.request = new_request
            except AttributeError:
                logger.debug(
                    "sticky child %s does not accept request attribute (expected for read-only proxies)",
                    sticky_id,
                )
            survivors[sticky_id] = child
        return survivors
