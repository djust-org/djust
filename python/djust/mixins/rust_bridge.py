"""
RustBridgeMixin - Rust backend integration for LiveView.
"""

import hashlib
import logging
from typing import Any, List, Optional, Set
from urllib.parse import parse_qs, urlencode

from ..serialization import normalize_django_value
from ..utils import get_template_dirs

logger = logging.getLogger(__name__)

try:
    from .._rust import RustLiveView
except ImportError:
    RustLiveView = None


def _collect_safe_keys(value: Any, prefix: str = "", visited: Optional[Set[int]] = None) -> List[str]:
    """
    Recursively scan a value for SafeString instances and return dotted paths.

    Args:
        value: The value to scan (SafeString, dict, list, or any other type)
        prefix: Current path prefix (e.g., "items.0" or "data")
        visited: Set of object IDs already visited (prevents circular references)

    Returns:
        List of dotted paths to SafeString values

    Examples:
        items = [{"content": mark_safe("<b>Item</b>")}]
        → ["items.0.content"]

        data = {"content": mark_safe("<u>Text</u>")}
        → ["data.content"]
    """
    from django.utils.safestring import SafeString

    if visited is None:
        visited = set()

    safe_keys = []

    # Check if value itself is SafeString
    if isinstance(value, SafeString):
        if prefix:
            safe_keys.append(prefix)
        return safe_keys

    # Recurse into dicts
    if isinstance(value, dict):
        # Prevent circular reference loops
        value_id = id(value)
        if value_id in visited:
            return safe_keys
        visited.add(value_id)

        for key, sub_value in value.items():
            sub_prefix = f"{prefix}.{key}" if prefix else key
            safe_keys.extend(_collect_safe_keys(sub_value, sub_prefix, visited))

        visited.remove(value_id)

    # Recurse into lists/tuples
    elif isinstance(value, (list, tuple)):
        # Prevent circular reference loops
        value_id = id(value)
        if value_id in visited:
            return safe_keys
        visited.add(value_id)

        for index, sub_value in enumerate(value):
            sub_prefix = f"{prefix}.{index}" if prefix else str(index)
            safe_keys.extend(_collect_safe_keys(sub_value, sub_prefix, visited))

        visited.remove(value_id)

    return safe_keys


class RustBridgeMixin:
    """Rust integration: _initialize_rust_view, _sync_state_to_rust."""

    def _initialize_rust_view(self, request=None):
        """Initialize the Rust LiveView backend"""

        logger.debug("[LiveView] _initialize_rust_view() called, _rust_view=%s", self._rust_view)

        if self._rust_view is None:
            # Try to get from cache if we have a session
            if hasattr(self, "_websocket_session_id") and self._websocket_session_id:
                ws_path = getattr(self, "_websocket_path", "/")
                ws_query = getattr(self, "_websocket_query_string", "")

                query_hash = ""
                if ws_query:
                    params = parse_qs(ws_query)
                    sorted_query = urlencode(sorted(params.items()), doseq=True)
                    query_hash = hashlib.md5(sorted_query.encode()).hexdigest()[:8]

                view_key = f"liveview_ws_{self.__class__.__name__}_{ws_path}"
                if query_hash:
                    view_key = f"{view_key}_{query_hash}"
                session_key = self._websocket_session_id

                from ..state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}"
                logger.debug("[LiveView] Cache lookup (WebSocket): cache_key=%s", self._cache_key)

                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    # template_dirs are not serialized; restore them after cache hit
                    self._rust_view.set_template_dirs(get_template_dirs())
                    logger.debug("[LiveView] Cache HIT! Using cached RustLiveView")
                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    logger.debug("[LiveView] Cache MISS! Will create new RustLiveView")
            elif request and hasattr(request, "session"):
                view_key = f"liveview_{request.path}"
                if request.GET:
                    query_hash = hashlib.md5(request.GET.urlencode().encode()).hexdigest()[:8]
                    view_key = f"{view_key}_{query_hash}"
                session_key = request.session.session_key
                if not session_key:
                    request.session.create()
                    session_key = request.session.session_key

                from ..state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}"
                logger.debug("[LiveView] Cache lookup (HTTP): cache_key=%s", self._cache_key)

                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    # template_dirs are not serialized; restore them after cache hit
                    self._rust_view.set_template_dirs(get_template_dirs())
                    logger.debug("[LiveView] Cache HIT! Using cached RustLiveView")
                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    logger.debug("[LiveView] Cache MISS! Will create new RustLiveView")

            template_source = self.get_template()

            logger.debug("[LiveView] Creating NEW RustLiveView for cache_key=%s", self._cache_key)
            logger.debug("[LiveView] Template length: %d chars", len(template_source))
            logger.debug("[LiveView] Template preview: %s...", template_source[:200])

            template_dirs = get_template_dirs()
            self._rust_view = RustLiveView(template_source, template_dirs)

            if self._cache_key:
                from ..state_backend import get_backend

                backend = get_backend()
                backend.set(self._cache_key, self._rust_view)

    def _sync_state_to_rust(self):
        """Sync Python state to Rust backend.

        Phoenix-style change tracking: only sends values that actually changed
        since the last render. Rust's update_state() merges (extends), so
        unchanged keys are retained from the previous render.

        Two-layer detection:
          1. Instance attribute changes — snapshot-based (_changed_keys from handle_event)
          2. Computed value changes — id() reference comparison against previous render
        """
        if self._rust_view:
            from ..components.base import Component, LiveComponent
            from django import forms
            from django.utils.safestring import SafeString

            full_context = self.get_context_data()

            changed_keys = getattr(self, "_changed_keys", None)
            prev_refs = getattr(self, "_prev_context_refs", {})

            # Determine which context to send to Rust
            if prev_refs:
                # Incremental: only send values that actually changed
                context = {}
                for key, value in full_context.items():
                    if changed_keys and key in changed_keys:
                        context[key] = value  # instance attr changed (snapshot)
                    elif key not in prev_refs:
                        context[key] = value  # new key
                    elif id(value) != prev_refs.get(key):
                        context[key] = value  # different object reference
                    # else: unchanged — Rust already has it
            else:
                # First render: send everything
                context = full_context

            # Store refs for next comparison (full context, not filtered)
            self._prev_context_refs = {k: id(v) for k, v in full_context.items()}
            self._changed_keys = None  # Clear

            # Detect SafeString values before serialization loses the type info
            safe_keys = []
            rendered_context = {}
            for key, value in context.items():
                if isinstance(value, (Component, LiveComponent)):
                    rendered_html = value.render()
                    rendered_context[key] = {"render": str(rendered_html)}
                    # Component.render() returns SafeString — mark as safe
                    safe_keys.append(key)
                elif isinstance(value, forms.Form):
                    continue
                else:
                    # Recursively collect all safe keys (including nested)
                    safe_keys.extend(_collect_safe_keys(value, key))
                    rendered_context[key] = value

            json_compatible_context = normalize_django_value(rendered_context)

            self._rust_view.update_state(json_compatible_context)
            if safe_keys:
                self._rust_view.mark_safe_keys(safe_keys)

            # Mark static assigns as sent — subsequent syncs will skip them
            if getattr(self, "static_assigns", None) and not getattr(
                self, "_static_assigns_sent", False
            ):
                self._static_assigns_sent = True
