"""NativeRenderer scaffold — emits widget-shaped VNodes for native clients.

LVN-II PR-2 of [ADR-019](../../../docs/adr/019-liveview-native.md). This
module is the **scaffold** that establishes the structural seam between
the renderer pipeline and the widget-shaped output path. The full
Django-template walker that translates ``<Stack>`` / ``<Text>`` / etc.
template syntax into widget VNodes is deferred to LVN-II PR-3 (template
loader) and LVN-II PR-4 (reference variant + stub-client integration
test).

Current behavior: ``NativeRenderer`` is registered in the ``RENDERERS``
dict so the LiveViewConsumer handshake (LVN-I PR-3) successfully routes
``?platform=swiftui`` / ``?platform=compose`` requests through this
renderer; today the render path raises ``NotImplementedError`` with a
clear pointer to the tracking issue, so a native client connecting
prematurely sees a defined error rather than silent HTML fallback.

The ``output_format`` attribute carries the platform key the handshake
selected (``"swiftui"`` or ``"compose"``) so a future template-walker
can pick a per-platform variant. Both platforms share the widget
vocabulary; differences live in style / event-attr handling on the
client side.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

__all__ = ["NativeRenderer", "SwiftUIRenderer", "ComposeRenderer"]


class NativeRenderer:
    """Scaffold for the native-widget renderer (LVN-II PR-2).

    Conforms to :class:`djust.renderers.Renderer` Protocol but raises
    ``NotImplementedError`` from ``render_with_diff`` — the widget-tree
    walker lands in LVN-II PR-3. Subclassed by :class:`SwiftUIRenderer`
    and :class:`ComposeRenderer` to fix the ``output_format`` per
    platform; both classes are registered in
    ``djust.renderers.RENDERERS``.

    The scaffold exists so:
    - LVN-I PR-3's handshake successfully routes native ``?platform=``
      values (the alternative — keeping native out of the registry —
      means the handshake silently falls back to HTML, which masks
      misconfigurations).
    - LVN-II PR-3 / PR-4 have a defined class to plug into rather than
      birthing one mid-PR.
    - LVN-III / LVN-IV client developers have a known server-side
      target to write integration tests against, even if the body is
      a clear NotImplementedError today.
    """

    output_format: str = "native"

    def __init__(self, view: Any) -> None:
        self.view = view

    def resolve_template(self, base: str) -> str:
        """Pick the platform variant of ``base`` for this renderer.

        Wraps :func:`template_resolver.resolve_variant` with this
        renderer's ``output_format`` so callers don't have to plumb
        the format separately.
        """
        from .template_resolver import resolve_variant

        return resolve_variant(base, self.output_format)

    def render_with_diff(
        self,
        request: Any = None,
        extract_liveview_root: bool = False,
        preloaded_context: Optional[dict] = None,
    ) -> Tuple[str, Optional[str], int]:
        """Emit a widget-VNode-shaped diff.

        Today: raises ``NotImplementedError`` — the widget-tree walker is
        the remaining LVN-II work (the renderer scaffold + vocabulary +
        template resolver are in place; gluing them together via a real
        Rust-side widget VDOM differ is the substantial piece).

        The error includes the resolved template variant so a client
        developer who hits it knows EXACTLY which template the resolver
        picked — invaluable for debugging "why is my native variant not
        loading?" before the walker is implemented.

        See: https://github.com/djust-org/djust/issues/1578
        """
        base_template = getattr(self.view, "template_name", "<unknown>")
        resolved = (
            self.resolve_template(base_template) if base_template != "<unknown>" else "<unknown>"
        )
        raise NotImplementedError(
            f"NativeRenderer.render_with_diff (output_format={self.output_format!r}) "
            f"is awaiting the widget-tree walker. The platform variant resolver "
            f"picked '{resolved}' (from base '{base_template}'). See "
            f"djust-org/djust#1578 for the remaining implementation. To unblock "
            f"client development today, run the WS without ?platform= and the "
            f"handshake falls through to HtmlRenderer."
        )


class SwiftUIRenderer(NativeRenderer):
    """``?platform=swiftui`` — used by ``djust-org/djust-native-ios``."""

    output_format: str = "swiftui"


class ComposeRenderer(NativeRenderer):
    """``?platform=compose`` — used by ``djust-org/djust-native-android``."""

    output_format: str = "compose"
