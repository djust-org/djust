"""HTML rendering through Django templates and the Rust VDOM pipeline.

TemplateMixin prepares the view's context and native backend. Authenticated
explicit root renders prepare an owned diff; render-scoped reconciliation
validates and commits it. Ordinary renders use the native differ directly.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

__all__ = ["HtmlRenderer"]


class HtmlRenderer:
    """HTML renderer with ownership-aware preparation for authenticated roots.

    Conforms to :class:`djust.renderers.Renderer`; returns HTML, patches and
    version while the outer render scope commits authenticated child ownership.
    """

    output_format: str = "html"

    def __init__(self, view: Any) -> None:
        """Bind the renderer to a LiveView instance.

        The caller (currently ``TemplateMixin.render_with_diff``) is
        responsible for ``_initialize_rust_view`` and state sync — the
        renderer assumes ``view._rust_view`` is ready.
        """
        self.view = view

    def render_with_diff(
        self,
        request: Any = None,
        extract_liveview_root: bool = False,
        preloaded_context: Optional[dict] = None,
    ) -> Tuple[str, Optional[str], int]:
        """Run the Rust VDOM differ and return ``(html, patches_json, version)``.

        The ``request``, ``extract_liveview_root``, and ``preloaded_context``
        parameters are on the Protocol surface but not consumed here; the
        wrapping ``TemplateMixin.render_with_diff`` handles them around
        this call. They are accepted so the Protocol shape is consistent
        across renderers (LVN-II's ``NativeRenderer`` will need
        ``request`` for per-platform variant resolution).
        """
        from .._child_rendering import owned_diff_active, prepare_owned_child_diff

        if getattr(
            self.view._rust_view, "potential_authenticated_partial_root", lambda: False
        )() and owned_diff_active(self.view):
            return prepare_owned_child_diff(self.view)
        return self.view._rust_view.render_with_diff()
