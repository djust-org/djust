"""Tenant scope for saved view state (#2973).

Every place that saves or restores a view's state, in the state backend
(``_initialize_rust_view``) or in the Django session (``liveview_<path>`` and
its ``__private`` / ``_components`` / ``__sticky__`` siblings), derives its key
through this module, so tenant scoping has one definition.

- A view without a ``get_state_key_prefix()`` hook (no ``TenantMixin``) is not
  tenant-scoped: the scope is ``""`` and every key keeps its pre-#2973 shape.
- A view mounted beside the page view (#3252) adds ``slot:<target>:``.
- A tenant view with a resolved tenant is scoped by ``tenant:<id>:``.
- A tenant view with NO resolved tenant is ``None``: nothing is read or
  written. Falling back to the unscoped key would put the view in the
  namespace that non-tenant views share.
"""

from typing import Any, Optional
from urllib.parse import quote


def state_scope(view: Any) -> Optional[str]:
    """``""`` (not scoped), ``"tenant:<id>:"``, ``"slot:<target>:"``, both, or ``None`` (fail closed).

    A view mounted beside the page view (a slot, #3252) has a scope of its own:
    the page view and the views beside it share one URL and one session, so
    without it they would read and write one another's saved state.
    """
    slot = getattr(view, "_djust_slot_target", None)
    slot_scope = f"slot:{quote(slot, safe='')}:" if isinstance(slot, str) and slot else ""
    hook = getattr(view, "get_state_key_prefix", None)
    if not callable(hook):
        return slot_scope
    prefix = hook()
    if not prefix:
        return None
    return f"{prefix}:{slot_scope}"


def scoped_path(view: Any, path: str) -> Optional[str]:
    """``path`` with the tenant scope in front, or ``None`` to skip saved state."""
    scope = state_scope(view)
    if scope is None:
        return None
    return f"{scope}{path}"


def session_view_key(view: Any, path: str) -> Optional[str]:
    """The ``request.session`` key for ``view``'s saved state at ``path``.

    ``liveview_<path>`` for a view that is not tenant-scoped,
    ``liveview_tenant:<id>:<path>`` for a tenant view, ``None`` to skip.
    The ``__private``, ``_components`` and ``__sticky__`` keys are built from
    this value (or from :func:`scoped_path`), so they are scoped too.
    """
    scoped = scoped_path(view, path)
    return None if scoped is None else f"liveview_{scoped}"


def refresh_other_views_state(session: Any, own_key: str) -> None:
    """Take the state other views saved from the stored session, before this one saves.

    Every view builds its own copy of the Django session when it mounts, and a
    save writes the whole copy. The page view and the views mounted beside it
    (#3252) share one session, so a save from one view would overwrite what
    another saved since this copy was loaded. Right before a view writes, the
    ``liveview_*`` entries that are not its own are read again from the store,
    so that write carries the other views' latest state instead of the state
    this copy was loaded with. Anything else in the session is left as it is:
    application values a handler wrote into this copy are saved with it.

    Best effort: a store that cannot be read keeps the save as it was.
    """
    key = getattr(session, "session_key", None)
    if not key:
        return
    try:
        # A second store object reads it: ``load()`` on a session whose row is
        # gone (or whose cache blipped) resets that object's key, which would
        # turn this save into the creation of a new session (#3247).
        stored = type(session)(key).load()
        cache = session._session
    except Exception:  # noqa: BLE001 - the save goes ahead with this copy
        return
    for key in [k for k in cache if k.startswith("liveview_") and not k.startswith(own_key)]:
        del cache[key]
    for key, value in stored.items():
        if key.startswith("liveview_") and not key.startswith(own_key):
            cache[key] = value
