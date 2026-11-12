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

import datetime
import decimal
import enum
import itertools
import logging
import re
import uuid
from typing import Any, Callable, Dict, Optional, Union


logger = logging.getLogger(__name__)

# Module-level counter for auto-generated view_ids. Monotonic across the
# process — the stamps are only meaningful per-connection, but uniqueness
# matters for WS event routing when two embeds of the same class live
# under the same parent.
_view_id_counter = itertools.count(1)

# The shape of an auto-assigned child id: ``child_<N>`` (the process-wide
# counter), or ``child_<hash>`` / ``child_<hash>_<N>`` (named for what the child
# is, over the HTTP fallback: see ``_child_identity``).
_AUTO_VIEW_ID = re.compile(r"child_(?:[0-9]+|[0-9a-f]{12}(?:_[0-9]+)?)")


def _canonical_kwarg(value: Any, depth: int = 0) -> str:
    """A stable text for one ``{% live_render %}`` kwarg, for ``_child_identity``.

    Scalars by value, a model instance by its label and primary key, containers
    by their members, and anything else by its type alone: never an object's
    ``repr``, which may carry a memory address and so differ between requests.
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return repr(value)
    if isinstance(value, (datetime.date, datetime.time, decimal.Decimal, uuid.UUID, enum.Enum)):
        # By value, with the type: ``Decimal(1)`` and ``date(1, 1, 1)`` differ,
        # and so do two dates.
        return f"<{type(value).__qualname__}:{value}>"
    if depth < 4:
        if isinstance(value, (list, tuple)):
            return "[" + ",".join(_canonical_kwarg(v, depth + 1) for v in value) + "]"
        if isinstance(value, (set, frozenset)):
            return "{" + ",".join(sorted(_canonical_kwarg(v, depth + 1) for v in value)) + "}"
        if isinstance(value, dict):
            items = sorted(
                (_canonical_kwarg(k, depth + 1), _canonical_kwarg(v, depth + 1))
                for k, v in value.items()
            )
            return "{" + ",".join(f"{k}:{v}" for k, v in items) + "}"
        meta = getattr(value, "_meta", None)
        label = getattr(meta, "label_lower", None)
        if isinstance(label, str):
            return f"<{label}#{getattr(value, 'pk', None)!r}>"
    cls = type(value)
    return f"<{cls.__module__}.{cls.__qualname__}>"


def _child_identity(view_path: str, kwargs: Dict[str, Any]) -> str:
    """What a ``{% live_render %}`` child is: its view path and the kwargs it was given.

    Twelve hex digits of a keyed digest (HMAC with the project's ``SECRET_KEY``)
    of both. Two tags with the same view and the same arguments have one
    identity; a tag with other arguments, another. The id reaches the browser,
    so the digest is keyed: an unkeyed one would let a client confirm guesses
    about arguments it cannot see (a low-entropy primary key, a session key).
    The key is the same in every process, so the id repeats between requests.
    """
    from django.utils.crypto import salted_hmac

    text = view_path + "|" + ",".join(f"{k}={_canonical_kwarg(kwargs[k])}" for k in sorted(kwargs))
    digest: str = salted_hmac("djust.child_identity", text, algorithm="sha256").hexdigest()
    return digest[:12]


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

    def _assign_view_id(
        self,
        preferred: Optional[str] = None,
        identity: Union[str, Callable[[], str], None] = None,
    ) -> str:
        """Return a unique ``view_id`` for a new child.

        If ``preferred`` is supplied and is not already in use on this
        parent, it is honored — otherwise a monotonic ``child_N`` stamp
        is generated. The auto-generated form is the default path used
        by ``{% live_render %}`` when the caller doesn't pin a stable id.

        Over the HTTP fallback the stamp names what the child is instead
        (:meth:`_scope_child_ids_to_render`): ``identity`` (the view path and
        kwargs of the tag, ``_child_identity``) makes the id, and a repeat of
        the same child within one render gets a numeric suffix. The same
        template yields the same ids on every request (#3104), and an id never
        names a different child because the page changed in between: a list
        that gained an item in another tab leaves each row's id as it was
        (a position in the render would not).
        """
        registry = getattr(self, "_child_views", {})
        if preferred and preferred not in registry:
            return preferred
        per_render = self.__dict__.get("_render_child_ids")
        if per_render is not None:
            if identity:
                # A callable is only run here: over a socket the id is the
                # counter's and the (possibly large) arguments are not hashed.
                base = f"child_{identity() if callable(identity) else identity}"
                candidate, n = base, 1
                while candidate in registry:
                    n += 1
                    candidate = f"{base}_{n}"
                return candidate
            while True:
                candidate = f"child_{next(per_render)}"
                if candidate not in registry:
                    return candidate
        return f"child_{next(_view_id_counter)}"

    def _scope_child_ids_to_render(self) -> None:
        """Make the auto-assigned child ids of the render about to run repeat.

        Over the HTTP fallback every request builds the view afresh, so a child
        stamped with the process-wide counter (``child_17`` on the page GET,
        ``child_18`` on the POST that follows) can never be found again by the
        ``view_id`` the browser echoes. With the ids named for the child
        (``_child_identity``; a plain count from 1 only for a tag that gives
        none), the same template yields the same ids on every request, and an
        event carrying a child's ``view_id`` can be routed to that child (#3104).

        Called by the HTTP request methods immediately before each render of a
        legacy parent, never over a socket: there children live across renders
        and the process-wide counter keeps their ids unique. A child of a
        previous render of this request (an auto-numbered, non-sticky, legacy
        one: a throwaway) is dropped from the registry so the next render
        reuses its id; pinned, sticky and explicit children are left alone.
        """
        from .._exposure import uses_legacy_exposure

        self.__dict__["_render_child_ids"] = itertools.count(1)
        registry = getattr(self, "_child_views", None)
        if type(registry) is not dict:
            return
        for view_id in [
            vid
            for vid, child in registry.items()
            if _AUTO_VIEW_ID.fullmatch(vid)
            and uses_legacy_exposure(child)
            and getattr(child, "sticky", False) is not True
        ]:
            registry.pop(view_id, None)

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
        if getattr(self, "_djust_child_disposed", False) or getattr(
            child, "_djust_child_disposed", False
        ):
            raise RuntimeError("Cannot register a disposed child or parent")
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
        """Drop ``view_id`` from the registry and tear the child down.

        An explicit child is disposed with its subtree. A legacy child has its
        waiters closed and its own embedded children unregistered
        (``release_legacy_child``, #3244), then its cleanup hook runs. The hook
        (optional on the child) is wrapped so a buggy hook can't break the
        disconnect loop — we log and continue.
        """
        if not hasattr(self, "_child_views"):
            return
        child = self._child_views.pop(view_id, None)
        if child is None:
            return
        from .._exposure import uses_legacy_exposure

        if not uses_legacy_exposure(child):
            from .._child_lifecycle import dispose_child_subtree

            dispose_child_subtree(child)
            return
        from .._child_lifecycle import release_legacy_child

        # Its waiters and nested children first (#3244), then the app hook.
        release_legacy_child(child)
        cleanup = getattr(child, "_cleanup_on_unregister", None)
        if callable(cleanup):
            try:
                from .._child_lifecycle import call_teardown_hook

                call_teardown_hook(child, cleanup)
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

        This hook is called during a live_redirect transition that discards
        the sticky, or when an explicit child's mount identity changes.
        A full WS disconnect takes the normal
        :meth:`_unregister_child` -> ``_cleanup_on_unregister`` path.
        """
        cancel_all = getattr(self, "cancel_async_all", None)
        if callable(cancel_all):
            try:
                cancel_all()
            except Exception:  # noqa: BLE001 — cleanup hook must not raise
                from .._exposure import uses_legacy_exposure

                if uses_legacy_exposure(self):
                    logger.exception("sticky _on_sticky_unmount: cancel_async_all() failed")
                else:
                    logger.error("Explicit sticky async cleanup failed")
        return None

    def _on_sticky_update(self, changed: Dict[str, Any]) -> None:
        """Apply changed ``{% live_render %}`` inputs to a REUSED sticky child (#2919).

        A sticky child keeps its live instance across parent re-renders, so
        the tag's keyword arguments reach ``mount()`` only once. Override this
        to take the later ones. djust calls it on the reused child, before the
        child renders, when the tag's arguments differ from the ones last
        applied (at mount, or at the previous call)::

            class Sidebar(LiveView):
                sticky = True
                sticky_id = "sidebar"

                def mount(self, request, section="home", **kwargs):
                    self.section = section

                def _on_sticky_update(self, changed):
                    if "section" in changed:
                        self.section = changed["section"]

        ``changed`` maps each argument that is new or whose value differs to
        its NEW value; arguments that did not change are absent, and the old
        values are whatever the child holds in its own state. The child is not
        remounted and its state is not reset; the hook decides what to touch.
        Arguments dropped from the tag are not reported.

        Rules:

        * Synchronous, and called while the parent renders, so keep it cheap
          and free of I/O. An exception propagates out of the tag like one from
          ``mount()``, and the new values are not recorded as applied: the
          parent keeps failing to render until the tag passes values the hook
          accepts.
        * ``changed`` holds the raw values the template engine handed the tag
          (a non-scalar arrives as the string the template would print), so
          validate and convert them as in ``mount()``.
        * Values that compare by identity (a ``QuerySet``) look changed on
          every render; the hook must be idempotent.
        * Private by name, so no client event can name it: it is reachable
          only from the ``{% live_render %}`` tag, never over the socket.
        * Not called for a sticky preserved across ``live_redirect``: that
          child is not rendered again, the browser keeps its DOM.
        * A child that does not override it keeps today's behaviour: the new
          values are ignored and djust logs a warning naming them.
        * A child with ``exposure_policy = "explicit"`` is remounted when its
          inputs change (they are part of its reuse identity), so this is
          never reached for one.

        The default does nothing.
        """
        return None

    def _preserve_sticky_children(self, new_request: Any) -> Dict[str, Any]:
        """Stage sticky children for preservation across a live_redirect.

        Walks :attr:`_child_views`, filters to children with
        ``sticky is True``, and re-checks each child's auth against
        ``new_request`` via
        :func:`djust.auth.core.check_view_auth_lightweight` and the
        object-level :func:`djust.auth.core.enforce_object_permission`. Survivors
        are returned in a ``{sticky_id: child}`` map (keyed by each
        child's ``sticky_id`` class attr) for the consumer to stash on
        ``self._sticky_preserved``.

        Non-sticky children and auth-denied sticky children are NOT
        included; the caller is responsible for letting the normal
        unmount flow run on the old view.
        """
        # Lazy import to avoid circular: auth.core -> live_view -> mixins -> auth.
        from ..auth.core import check_view_auth_lightweight, enforce_object_permission

        def _authorized(child: Any) -> bool:
            # Authorize against the NEW request: get_object() and application
            # predicates may read self.request, and the old one is stale. A
            # child that cannot take the new request (a read-only proxy), or
            # a predicate that raises, denies this child only (fail closed).
            try:
                child.request = new_request
                if not check_view_auth_lightweight(child, new_request):
                    return False
                enforce_object_permission(child, new_request)
            except Exception:  # noqa: BLE001 — broken predicates must not permit reuse
                return False
            return True

        survivors: Dict[str, Any] = {}
        for _view_id, child in list(self._get_all_child_views().items()):
            if getattr(child, "sticky", False) is not True:
                continue
            sticky_id = getattr(child, "sticky_id", None) or _view_id
            if not _authorized(child):
                from .._child_lifecycle import discard_sticky_child
                from .._exposure import uses_legacy_exposure

                if uses_legacy_exposure(child):
                    logger.info(
                        "Sticky child %s auth denied for new request; discarding",
                        sticky_id,
                    )
                discard_sticky_child(child)
                continue
            # ``_authorized`` already set ``child.request`` to the new request
            # (a child that refuses it is denied above, #1380).
            try:
                # #2998: derive the next {% csrf_token %} from the new request.
                child._cached_csrf_token = None
            except AttributeError:
                logger.warning(
                    "sticky child %s does not accept a cached CSRF token reset",
                    sticky_id,
                )
            survivors[sticky_id] = child
        return survivors
