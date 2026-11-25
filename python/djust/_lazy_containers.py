"""Authored attribute-form lazy children on the page-POST fallback (#3252, #4174).

Resolution is possible only through a fresh render's server-owned registry.
The client sends a keyed address, never a class or a state payload.

Only explicit children are registered. A child is mounted through the eager
``{% live_render %}`` explicit sequence (``_mount_explicit``) and keeps exactly
its ``persist="server"`` fields, saved under its lazy address by
``save_child_states``.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template import TemplateSyntaxError
from django.utils.html import escape

from ._render_provenance import RenderedHTML
from ._tenant_state import scoped_path, session_view_key, snapshot_tenant_scope
from .auth._login_required_middleware import mark_embedded
from .auth.core import run_pre_mount_auth, enforce_object_permission
from .mixins.sticky import _child_identity

MOUNT_EVENT = "djust_lazy_mount"


logger = logging.getLogger(__name__)


def _log_unregistered(html: str, registered: int) -> None:
    if logger.isEnabledFor(logging.DEBUG) and html.lower().count("dj-lazy") > registered:
        logger.debug(
            "Lazy markup did not register: check source/final HTML context, include selectors "
            "(loop/with bindings), include depth (20), authority bytes and child permissions"
        )


@dataclass(frozen=True)
class LazyContainer:
    child_class: type
    child_tenant: str


def register_lazy_containers(parent: Any, request: Any, html: str) -> str:
    """Authorize surviving authored containers without mounting/rendering them."""
    parent.__dict__["_http_lazy_containers"] = {}
    if (
        getattr(parent, "_defer_lazy_registration", False)
        or getattr(parent, "wrapper_template", None)
        or not isinstance(html, RenderedHTML)
        or request is None
    ):
        if not getattr(parent, "_defer_lazy_registration", False):
            _log_unregistered(html, 0)
        return html
    # Authenticated-root renders track all candidates; the authored include
    # path carries any lexical-scope restriction for each individual child.
    rust_view = getattr(parent, "_rust_view", None)
    authenticated_plan = getattr(rust_view, "potential_authenticated_partial_root", lambda: False)()
    from ._rust import authored_lazy_elements
    from .templatetags.live_tags import resolve_live_view_class

    elements = authored_lazy_elements(html, list(html.spans))
    if not elements:
        _log_unregistered(html, 0)
        return html
    # A page with only deferred children may have no parent handlers or form
    # token. Its first lazy POST still needs the normal CSRF cookie/session.
    from django.middleware.csrf import get_token

    get_token(request)
    path = scoped_path(parent, request.path)
    if path is None:
        return html
    binding = request.session.get("_djust_lazy_binding")
    if not binding:
        from secrets import token_hex

        binding = token_hex(16)
        request.session["_djust_lazy_binding"] = binding
    user_id = str(getattr(getattr(request, "user", None), "pk", None))
    page_class = getattr(type(parent), "__module__", "<unknown>") + "." + type(parent).__qualname__
    inserts = []
    encoded = html.encode()
    for start, end, view_path, _trigger in elements:
        origin = html.authored_identity(start, end)
        if authenticated_plan:
            if origin is None:
                continue
            identity_path = origin[1]
            scoped_tail = identity_path.partition("/lazy-scoped/")
            nested_scope = scoped_tail[2].partition("/include:")[2] if scoped_tail[1] else ""
            if (
                "/lazy-denied/" in identity_path
                or identity_path.count("/include:") > 20
                or "/lazy-dynamic/" in nested_scope
            ):
                continue
        try:
            cls = resolve_live_view_class(view_path)
            child = cls()
            mark_embedded(child)
            child.request = request
            from .tenants.middleware import get_current_tenant, tenant_context

            with tenant_context(get_current_tenant()):
                if run_pre_mount_auth(child, request) is not None:
                    continue
                child_tenant = snapshot_tenant_scope(child)
            if child_tenant is None:
                continue
        except (TemplateSyntaxError, PermissionDenied, Http404):
            continue
        if origin is None:
            # Compatibility for direct callers supplying only authored spans.
            # HTTP renderer results always supply an authored node address.
            origin = (start, "direct")
        identity = _child_identity(
            view_path,
            {
                "page": page_class,
                "path": path,
                "session": binding,
                "user": user_id,
                "child_tenant": child_tenant,
                "authored_node": origin,
                "template": getattr(parent, "template_name", None),
            },
        )
        view_id = "lazy_" + identity
        parent.__dict__["_http_lazy_containers"][view_id] = LazyContainer(cls, child_tenant)
        # Insert reserved routing attributes first: HTML selects the first
        # duplicate, so neither interpolation nor author-provided DOM IDs can
        # shadow this registration's keyed address.
        import re

        tag = encoded[start:end].decode()
        name = re.match(r"<[a-zA-Z][^ \t\r\n\f/>]*", tag)
        if name is None:
            continue
        offset = start + len(name.group(0).encode())
        inserts.append(
            (
                offset,
                ' data-djust-lazy-id="'
                + escape(view_id)
                + '" data-djust-embedded="'
                + escape(view_id)
                + '"',
            )
        )
    # Bytes are inserted by exact offsets after the final HTML5 check, never
    # by searching for a matching class/tag or counting rendered markup.
    from ._render_provenance import join

    parts, cursor = [], 0
    for offset, markup in inserts:
        pos = html.char_offset(offset)
        parts.extend((html[cursor:pos], markup))
        cursor = pos
    parts.append(html[cursor:])
    html = join(parts)
    parent.__dict__["_http_lazy_validated_html"] = html
    _log_unregistered(html, len(parent._http_lazy_containers))
    return html


def mount_http_lazy(parent: Any, request: Any, view_id: str) -> Any:
    """Mount a registered explicit child and restore only its bound, server-held state."""
    entry = getattr(parent, "_http_lazy_containers", {}).get(view_id)
    if entry is None:
        return None
    return _mount_explicit(parent, request, view_id, entry)


def _mount_explicit(parent: Any, request: Any, view_id: str, entry: LazyContainer) -> Any:
    """Mount an explicit lazy child as ``{% live_render %}`` mounts an explicit one (#4174).

    The eager explicit sequence, with no inputs (the attribute form has none)
    and the lazy address as the slot: view auth and mount hooks before
    ``mount()``, the reuse identity compiled before it and matched after the
    object permission, and the child's ``persist="server"`` fields restored
    from the child-state scope bound to that address (session, user, tenant,
    route and slot). Its other state starts again from ``mount()``. Any
    exposure failure is a refusal.
    """
    from ._component_state import apply_declared_state
    from ._exposure import ExposureError
    from ._exposure_child_identity import child_reuse_identity
    from ._exposure_children import child_state_adapter, record_child_mount_inputs
    from ._params_form import _refuse_removed_configuration
    from ._view_hooks import call_hook_sync
    from .hooks import run_on_mount_hooks

    inputs: dict = {}
    child = entry.child_class()
    mark_embedded(child)
    child.request = request
    if run_pre_mount_auth(child, request) is not None or run_on_mount_hooks(child, request):
        raise PermissionDenied("Lazy view refused")
    if session_view_key(child, request.path) is None:
        raise PermissionDenied("Lazy view has no resolved tenant")
    try:
        record_child_mount_inputs(child, inputs)
        identity = child_reuse_identity(type(child), parent, request, view_id, inputs)
        adapter = child_state_adapter(child, parent, request, view_id, inputs, create=True)
        call_hook_sync(child.mount, request)
        _refuse_removed_configuration(child)
        restored = None
        if adapter is not None:
            try:
                restored = adapter.load()
            except ExposureError:
                restored = None  # bad schema/identity/expiry is a cache miss
            if restored is not None:
                apply_declared_state(child, restored)
        enforce_object_permission(child, request)
        if restored is not None and callable(getattr(child, "handle_restore", None)):
            call_hook_sync(child.handle_restore, request)
        if not identity.matches(
            child_reuse_identity(type(child), parent, request, view_id, inputs)
        ):
            raise ExposureError("Lazy child identity changed")
    except ExposureError:
        # Static text: the contract error's own message may name view state.
        logger.warning("Lazy view refused: an exposure contract error in the child")
        raise PermissionDenied("Lazy view refused") from None
    if snapshot_tenant_scope(child) != entry.child_tenant:
        raise PermissionDenied("Lazy view changed tenant")
    child._djust_slot_target = view_id
    child._explicit_child_reuse_identity = identity
    parent._register_child(view_id, child)
    if adapter is not None:
        adapter.save(adapter.contract.project_view(child, "server"))
        child._explicit_child_mount_binding = adapter.binding
    return child


def finalize_lazy_containers(parent: Any, html: str) -> str:
    """Drop registrations whose authority did not survive the final page edits."""
    if getattr(parent, "_defer_lazy_registration", False):
        parent.__dict__["_defer_lazy_registration"] = False
        return register_lazy_containers(parent, parent.request, html)
    registry = getattr(parent, "_http_lazy_containers", {})
    if not registry or html is getattr(parent, "_http_lazy_validated_html", None):
        return html
    if not isinstance(html, RenderedHTML):
        registry.clear()
        return html
    import re
    from ._rust import authored_lazy_elements

    retained = set()
    encoded = html.encode()
    for start, end, _view_path, _trigger in authored_lazy_elements(html, list(html.spans)):
        tag = encoded[start:end].decode()
        match = re.search(r' data-djust-lazy-id="(lazy_[a-f0-9]+)"', tag)
        if match and match.group(1) in registry:
            retained.add(match.group(1))
    for view_id in set(registry) - retained:
        del registry[view_id]
    return html
