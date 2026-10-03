"""Tenant scope for saved view state and the signed state-snapshot token.

Every place that saves or restores a view's state, in the state backend
(``_initialize_rust_view``) or in the Django session (``liveview_<path>`` and
its ``__private`` / ``_components`` / ``__sticky__`` siblings), derives its key
through this module, so tenant scoping has one definition.

- A view without a ``get_state_key_prefix()`` hook (no ``TenantMixin``) is not
  tenant-scoped: the scope is ``""`` and every key keeps its previous shape.
- A tenant view with a resolved tenant is scoped by ``tenant:<id>:``.
- A tenant view with NO resolved tenant is ``None``: nothing is read or
  written. Falling back to the unscoped key would put the view in the
  namespace that non-tenant views share.
"""

from typing import Any, Optional


def state_scope(view: Any) -> Optional[str]:
    """``""`` (not scoped), ``"tenant:<id>:"``, or ``None`` (fail closed)."""
    hook = getattr(view, "get_state_key_prefix", None)
    if not callable(hook):
        return ""
    prefix = hook()
    if not prefix:
        return None
    return f"{prefix}:"


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


def snapshot_tenant_scope(view: Any) -> Optional[str]:
    """The tenant a signed state-snapshot token is bound to.

    ``""`` for a view that is not tenant-scoped, ``"tenant:<id>"`` for a tenant
    view with a resolved tenant, ``None`` for a tenant view with none: it
    neither mints a token nor accepts one.
    """
    hook = getattr(view, "get_state_key_prefix", None)
    if not callable(hook):
        return ""
    prefix = hook()
    return prefix if prefix else None


def render_only_keys(view: Any) -> frozenset:
    """Context keys a session save must drop: they render, never persist."""
    keys = getattr(view, "_djust_render_only_context_keys", None)
    return frozenset(keys()) if callable(keys) else frozenset()
