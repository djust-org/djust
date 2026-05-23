"""Renderer abstraction for djust LiveView.

Iteration I of ADR-019 (LiveView Native): introduces a pluggable
``Renderer`` Protocol so the existing server-side reactive lifecycle
can drive non-HTML targets. This PR ships the Protocol + the default
``HtmlRenderer`` only; ``NativeRenderer`` and runtime threading land
in subsequent PRs of LVN-I.

The Protocol is intentionally narrow — one method, ``render_with_diff``,
returning the same ``(html, patches_json, version)`` triple
``TemplateMixin.render_with_diff`` returns today. The Protocol exists so
future renderers (SwiftUI, Compose, terminal) can wrap an alternative
template + diff pipeline behind the same Python-side contract.

See also:
- ADR-019 §"What changes in djust core"
- ADR-016 §"ViewRuntime" (prior pluggability axis)
"""

from typing import Optional, Type

from .base import Renderer
from .html import HtmlRenderer
from .native import ComposeRenderer, NativeRenderer, SwiftUIRenderer

__all__ = [
    "Renderer",
    "HtmlRenderer",
    "NativeRenderer",
    "SwiftUIRenderer",
    "ComposeRenderer",
    "RENDERERS",
    "get_renderer_factory",
]

# Renderer registry. Keys match the ``output_format`` Protocol attribute
# and the ``?platform=`` WebSocket handshake param. Third-party renderers
# can register by appending to this dict at import time.
#
# LVN-I PR-3 added ``html``. LVN-II PR-2 (this PR) registers ``swiftui``
# and ``compose`` — the body is a scaffold today (raises NotImplementedError);
# LVN-II PR-3 ships the actual widget-tree walker. Registering them here
# so the handshake routes ?platform=swiftui to a defined error rather than
# silently falling through to HTML (which masks client-side misconfigs).
RENDERERS: dict[str, Type[Renderer]] = {
    "html": HtmlRenderer,
    "swiftui": SwiftUIRenderer,
    "compose": ComposeRenderer,
}


def get_renderer_factory(platform: Optional[str]) -> Optional[Type[Renderer]]:
    """Resolve a renderer class by ``?platform=`` value.

    Returns ``None`` for the empty / missing case so the caller can fall
    through to ViewRuntime's ``renderer_factory=None`` default
    (interpreted at the dispatch site as ``HtmlRenderer``). Returns
    ``None`` for unknown platforms too — the WS handshake should NOT
    error out on a typo; it falls back to HTML.

    The registry lookup is intentionally permissive: a bad ``?platform=``
    value never breaks a session, it just renders HTML. PR-3's gate is
    that valid values DO route correctly; PR-3 does not gate on rejecting
    invalid values (that's a follow-up if it ever matters).
    """
    if not platform:
        return None
    return RENDERERS.get(platform)
