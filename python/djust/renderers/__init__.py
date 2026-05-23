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

from .base import Renderer
from .html import HtmlRenderer

__all__ = ["Renderer", "HtmlRenderer"]
