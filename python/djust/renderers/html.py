"""Default HTML renderer — wraps the existing Django template + Rust VDOM pipeline.

This module is the smallest possible refactor of the inline render path
in :meth:`TemplateMixin.render_with_diff`. It does NOT change ANY
behavior — it is a structural extraction so the dispatch site has a
named seam to which future ``NativeRenderer`` etc. can plug in
(LVN-II onward; ADR-019).

The renderer wraps the view instance (not just template + context)
because the existing call site does more than render: it caches a
per-view ``RustLiveView``, syncs Django context into Rust state, and
tracks ``_sync_done_this_cycle`` to avoid double-sync. Reproducing all
of that from a renderer that only knows ``(template_name, context)``
would either duplicate state or break the existing fast paths.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

__all__ = ["HtmlRenderer"]


class HtmlRenderer:
    """Default renderer — emits HTML via the existing Rust VDOM pipeline.

    Conforms to :class:`djust.renderers.Renderer` Protocol. Behavior is
    identical to the pre-refactor inline call at
    ``mixins/template.py:942``: invokes ``self.view._rust_view.render_with_diff()``
    and returns the result unchanged.
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
        return self.view._rust_view.render_with_diff()
