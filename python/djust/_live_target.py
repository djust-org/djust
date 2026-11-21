"""Which live transport a view's server-initiated sends go to (#4232, #4256).

One rule, shared by streaming (``StreamingMixin``) and push events
(``PushEventMixin.flush_push_events`` / ``StreamingMixin.push_state``):
the WebSocket consumer when one is attached; else the SSE session's runtime,
but only while that session still serves this view (a view replaced by
``live_redirect`` keeps ``_sse_session`` and must not write into the new
page's queue). The HTTP fallback has neither.
"""

from __future__ import annotations

from typing import Any, Optional


def sse_runtime_for(view: Any) -> Optional[Any]:
    """The SSE session's runtime while that session still serves ``view``; else ``None``.

    Returns ``None`` when a WebSocket consumer is attached (WS wins), when the
    view has no SSE session, or when the session now serves another view.
    """
    if view.__dict__.get("_ws_consumer") is not None:
        return None
    session = view.__dict__.get("_sse_session")
    if session is None or getattr(session, "view_instance", None) is not view:
        return None
    return getattr(session, "runtime", None)
