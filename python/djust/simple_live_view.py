"""Server-rendered djust views with no WebSocket — ``SimpleLiveView`` (#2219).

A plain Django ``View`` that renders a template through the Rust engine and
returns HTML. No WebSocket, no VDOM, no client state: the LiveView programming
model for pages that only need the render.

Two long-standing defects are fixed here.

**It could not render at all.** ``get_context_data`` walked ``dir(self)`` and
``getattr``-ed every name, which reaches Django's ``View.as_view`` — a
``classonlymethod`` whose ``__get__`` raises on an *instance*:

    AttributeError: This method is available only on the class, not on
    instances.

So every render failed before a template was reached, and the ``except
Exception`` in ``render_template`` turned the crash into a generic message.
That was true for any subclass, always.

**Its class was named ``LiveView``.** The same name as ``djust.LiveView``,
which it is not. That is why grepping for ``SimpleLiveView`` found nothing and
the module read as unused when it was merely unfindable — it went two PRs
without anyone noticing it was a live render path (#2223). The class is now
``SimpleLiveView``; ``LiveView`` remains as a module-level alias so any existing
``from djust.simple_live_view import LiveView`` keeps working.
"""

import logging
from typing import Any, Dict, Optional, Set

from django.http import HttpRequest, HttpResponse
from django.views import View

try:
    from ._rust import render_template_with_dirs

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False

from .config import template_auto_call_enabled
from .render_env import apply_render_env
from .utils import get_template_dirs

logger = logging.getLogger(__name__)

#: Django ``View`` machinery a template has no use for, excluded so the context
#: is the view's own state rather than its plumbing.
#:
#: ``as_view`` is deliberately NOT here, though it is the attribute that caused
#: #2219. It raises on an instance, so the ``AttributeError`` guard in
#: ``get_context_data`` already excludes it — and listing it here as well made
#: the two mechanisms shadow each other: re-introducing the original bug (by
#: dropping ``as_view`` from this set) left the whole suite green, because the
#: guard silently covered for it. Two overlapping fixes read as belt-and-braces
#: and behave as one fix plus one decoration (#2129).
#:
#: These three do NOT raise. They read perfectly well and are excluded purely
#: because they are plumbing, which is a different reason and is what makes this
#: set independently reachable.
_VIEW_INTERNALS: Set[str] = {
    "http_method_names",
    "template",
    "view_is_async",
}


class SimpleLiveView(View):
    """A djust view that renders once, server-side, and returns HTML.

    Set ``template`` to a template string and assign state in ``mount``; every
    public, non-callable attribute becomes a template variable.
    """

    template: Optional[str] = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rust_view: Any = None

    def mount(self, request: HttpRequest, **kwargs: Any) -> None:
        """Override to set initial state."""

    def get_context_data(self) -> Dict[str, Any]:
        """Every public, non-callable attribute, as template context.

        Each attribute is read through ``try``/``except AttributeError``. A name
        in ``dir(self)`` is not a promise that ``getattr`` will succeed — a
        ``classonlymethod`` (which is what broke this, #2219), a property whose
        getter raises, or a descriptor that refuses instance access all look
        like ordinary attributes until read. This is a method whose entire job
        is reading attributes it does not know about, so the guard is the fix
        rather than a safety net around one.
        """
        context: Dict[str, Any] = {}
        for key in dir(self):
            if key.startswith("_") or key in _VIEW_INTERNALS:
                continue
            try:
                value = getattr(self, key)
            except AttributeError:
                continue
            if not callable(value):
                context[key] = value
        return context

    def render_template(self) -> str:
        """Render the template through the Rust engine.

        The two "cannot render" cases are reported separately. They used to
        share one message — a view with no ``template`` was told the *Rust
        backend* was unavailable, which is both false and unactionable.
        """
        if not self.template:
            return "<div>No template configured for this view</div>"
        if _RUST_AVAILABLE:
            try:
                # Per-render Django settings the Rust engine cannot read for
                # itself: the active timezone (#2209) and number format
                # (#2221). This class shares no base with ``RustBridgeMixin``,
                # so without the shared function it would render every
                # timestamp in UTC while ``LiveView`` rendered it correctly.
                apply_render_env()
                context = self.get_context_data()
                # A framework render path, so the project's auto-call setting
                # governs rather than the Rust default (#2508).
                return str(
                    render_template_with_dirs(
                        self.template,
                        context,
                        get_template_dirs(),
                        None,
                        template_auto_call_enabled(),
                    )
                )
            except Exception:
                # The exception detail goes to the LOG, never to the response
                # (CodeQL `py/stack-trace-exposure`, alert #2596; CWE-209).
                #
                # This used to render `f"<div>Template error: {e}</div>"` when
                # `DEBUG` was on. Two separate defects; CodeQL names the
                # SECOND, and the first was found only by reading the line it
                # pointed at:
                #
                # 1. The message was interpolated **unescaped**, so an
                #    exception carrying a `<` — and template errors routinely
                #    echo the offending value — injected markup straight into
                #    the page (CWE-79). `websocket.py:549` fixed exactly this
                #    shape with `escape(str(e))`, and its comment says it
                #    "mirrors the DEBUG gate in simple_live_view" — the copy
                #    was fixed and the original it was modelled on was not
                #    (#1646).
                # 2. The detail flowed into the response at all — the
                #    information-exposure rule CodeQL actually fired on.
                #    Escaping fixes (1) and would have left (2) untouched, so
                #    copying the sibling's fix verbatim would have closed the
                #    real bug and left the alert open.
                #
                # `logger.exception` is strictly better for the developer this
                # was meant to serve: a full traceback in the log rather than a
                # one-line `str(e)` in a div. Nothing is DEBUG-gated any more,
                # so dev and production return the same static string and there
                # is no mode-dependent leak to reason about.
                logger.exception("[SimpleLiveView] template render failed")
                return "<div>An error occurred rendering this view.</div>"
        return "<div>Rust backend not available</div>"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Handle GET requests."""
        self.mount(request, **kwargs)
        return HttpResponse(self.render_template())


#: Back-compat alias for the pre-#2219 name.
#:
#: Kept because the module is importable and someone may have done
#: ``from djust.simple_live_view import LiveView``. New code should use
#: ``SimpleLiveView`` — the old name shadows ``djust.LiveView``, which is a
#: different class with a different lifecycle.
LiveView = SimpleLiveView
