"""Authored attribute-form lazy children on the page-POST fallback (#3252).

Resolution is possible only through a fresh render's server-owned registry.
The client sends a keyed address, never a class or a state payload.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template import TemplateSyntaxError
from django.utils.html import escape

from ._exposure import uses_legacy_exposure
from ._render_provenance import RenderedHTML
from ._tenant_state import scoped_path, session_view_key, snapshot_tenant_scope
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
    state_key: str
    child_tenant: str


def register_lazy_containers(parent: Any, request: Any, html: str) -> str:
    """Authorize surviving authored containers without mounting/rendering them."""
    parent.__dict__["_http_lazy_containers"] = {}
    if (
        getattr(parent, "_defer_lazy_registration", False)
        or getattr(parent, "wrapper_template", None)
        or not isinstance(html, RenderedHTML)
        or request is None
        or not uses_legacy_exposure(parent)
    ):
        if not getattr(parent, "_defer_lazy_registration", False):
            _log_unregistered(html, 0)
        return html
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
        try:
            cls = resolve_live_view_class(view_path)
            if not uses_legacy_exposure(cls):
                continue  # Explicit views require a transport mount binding.
            child = cls()
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
        origin = html.authored_identity(start, end)
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
        state_key = "liveview_" + path + "__lazy__" + view_id
        parent.__dict__["_http_lazy_containers"][view_id] = LazyContainer(
            cls, state_key, child_tenant
        )
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
    """Mount a registered child and restore only its bound, server-held state."""
    entry = getattr(parent, "_http_lazy_containers", {}).get(view_id)
    if entry is None:
        return None
    from .hooks import run_on_mount_hooks
    from .serialization import decode_state_roundtrip
    from .security import safe_setattr

    child = entry.child_class()
    child.request = request
    if run_pre_mount_auth(child, request) is not None or run_on_mount_hooks(child, request):
        raise PermissionDenied("Lazy view refused")
    child.mount(request)
    child._snapshot_user_private_attrs()
    child._djust_slot_target = view_id
    enforce_object_permission(child, request)
    # Both the page and child must have a resolved tenant before state can be
    # retained. Never fall back to an unscoped key for a tenant-scoped child.
    if (
        session_view_key(child, request.path) is None
        or snapshot_tenant_scope(child) != entry.child_tenant
    ):
        raise PermissionDenied("Lazy view has no resolved tenant")
    saved = request.session.get(entry.state_key, {})
    for key, value in decode_state_roundtrip(saved.get("public", {})).items():
        safe_setattr(child, key, value, allow_private=False)
    child._restore_private_state(decode_state_roundtrip(saved.get("private", {})))
    if snapshot_tenant_scope(child) != entry.child_tenant:
        raise PermissionDenied("Lazy view changed tenant")
    enforce_object_permission(child, request)
    parent._register_child(view_id, child)
    return child


def save_http_lazy(parent: Any, request: Any, view_id: str, child: Any) -> None:
    """Persist only this child's legacy state under its user/session/tenant binding."""
    entry = getattr(parent, "_http_lazy_containers", {}).get(view_id)
    if entry is None:
        return
    from .components.base import is_component_collection, LiveComponent
    from .mixins.context import legacy_render_only_keys
    from .serialization import normalize_django_value
    from ._tenant_state import refresh_other_views_state

    import inspect
    from asgiref.sync import async_to_sync

    gcd = child.get_context_data
    context = async_to_sync(gcd)() if inspect.iscoroutinefunction(gcd) else gcd()
    excluded = legacy_render_only_keys(child) | {"streams"}
    public = {
        k: v
        for k, v in context.items()
        if k not in excluded and not isinstance(v, LiveComponent) and not is_component_collection(v)
    }
    request.session[entry.state_key] = normalize_django_value(
        {
            "public": public,
            "private": child._get_private_state(),
        },
        state_roundtrip=True,
    )
    refresh_other_views_state(request.session, entry.state_key)


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
