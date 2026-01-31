"""
RustBridgeMixin - Rust backend integration for LiveView.
"""

import hashlib
import json
import logging
import sys
from urllib.parse import parse_qs, urlencode

from ..serialization import DjangoJSONEncoder
from ..utils import get_template_dirs

logger = logging.getLogger(__name__)

try:
    from .._rust import RustLiveView
except ImportError:
    RustLiveView = None


class RustBridgeMixin:
    """Rust integration: _initialize_rust_view, _sync_state_to_rust."""

    def _initialize_rust_view(self, request=None):
        """Initialize the Rust LiveView backend"""

        print(
            f"[LiveView] _initialize_rust_view() called, _rust_view={self._rust_view}",
            file=sys.stderr,
        )

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
                print(
                    f"[LiveView] Cache lookup (WebSocket): cache_key={self._cache_key}",
                    file=sys.stderr,
                )

                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    # template_dirs are not serialized; restore them after cache hit
                    self._rust_view.set_template_dirs(get_template_dirs())
                    print("[LiveView] Cache HIT! Using cached RustLiveView", file=sys.stderr)
                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    print("[LiveView] Cache MISS! Will create new RustLiveView", file=sys.stderr)
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
                print(
                    f"[LiveView] Cache lookup (HTTP): cache_key={self._cache_key}", file=sys.stderr
                )

                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    # template_dirs are not serialized; restore them after cache hit
                    self._rust_view.set_template_dirs(get_template_dirs())
                    print("[LiveView] Cache HIT! Using cached RustLiveView", file=sys.stderr)
                    backend.set(self._cache_key, cached_view)
                    return
                else:
                    print("[LiveView] Cache MISS! Will create new RustLiveView", file=sys.stderr)

            template_source = self.get_template()

            print(
                f"[LiveView] Creating NEW RustLiveView for cache_key={self._cache_key}",
                file=sys.stderr,
            )
            print(f"[LiveView] Template length: {len(template_source)} chars", file=sys.stderr)
            print(f"[LiveView] Template preview: {template_source[:200]}...", file=sys.stderr)

            template_dirs = get_template_dirs()
            self._rust_view = RustLiveView(template_source, template_dirs)

            if self._cache_key:
                from ..state_backend import get_backend

                backend = get_backend()
                backend.set(self._cache_key, self._rust_view)

    def _sync_state_to_rust(self):
        """Sync Python state to Rust backend"""
        if self._rust_view:
            from ..components.base import Component, LiveComponent
            from django import forms

            context = self.get_context_data()

            rendered_context = {}
            for key, value in context.items():
                if isinstance(value, (Component, LiveComponent)):
                    rendered_html = str(value.render())
                    rendered_context[key] = {"render": rendered_html}
                elif isinstance(value, forms.Form):
                    continue
                else:
                    rendered_context[key] = value

            json_str = json.dumps(rendered_context, cls=DjangoJSONEncoder)
            json_compatible_context = json.loads(json_str)

            self._rust_view.update_state(json_compatible_context)
