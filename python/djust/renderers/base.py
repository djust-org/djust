"""Renderer Protocol — the contract every output backend implements.

See ADR-019 §"Three layers" §1 for the full design rationale.

This module is intentionally tiny and import-light: it defines the
Protocol and nothing else. Implementations live in sibling modules
(``html.py``, future ``native.py``) and are re-exported from
``renderers/__init__.py``.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, Tuple, runtime_checkable

__all__ = ["Renderer"]


@runtime_checkable
class Renderer(Protocol):
    """Output-format-pluggable renderer for a mounted LiveView.

    Implementations wrap an output-specific render + diff pipeline. The
    default :class:`HtmlRenderer` wraps the existing Django-template +
    Rust-parse + Rust-diff path; future implementations will produce
    widget-shaped trees for SwiftUI / Compose clients (LVN-II onward).

    The ``output_format`` attribute identifies the renderer choice at
    handshake time (PR-3 of LVN-I). ``"html"`` for the default;
    ``"swiftui"``, ``"compose"`` for native renderers (not in this PR).

    ``@runtime_checkable`` allows ``isinstance(obj, Renderer)`` to assert
    structural conformance at test time without forcing subclassing.
    """

    output_format: str

    def render_with_diff(
        self,
        request: Any = None,
        extract_liveview_root: bool = False,
        preloaded_context: Optional[dict] = None,
    ) -> Tuple[str, Optional[str], int]:
        """Render the current view state and compute the diff.

        Returns the same triple ``TemplateMixin.render_with_diff`` returns
        today: ``(html, patches_json, version)``. The ``patches_json`` is
        the Rust VDOM differ's output (msgpack/JSON envelope is added by
        the transport, NOT by the renderer).
        """
        ...
