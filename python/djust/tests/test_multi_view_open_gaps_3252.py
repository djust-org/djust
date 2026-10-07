"""#3252: remaining multi-view limitations after render and refusal isolation.

Per-view render/turn isolation is covered in
``test_multi_view_render_locks_3252.py``; its strict expected failures retain
synchronous WebSocket handler limitations. Refused lazy views now fail alone,
including at event time (``test_multi_view_refusal_3252.py``).

Remaining client-side limits: events sent from code with no element go to the
page view, the destination's ``dj-lazy`` containers do not hydrate after
``live_redirect``, and the page-POST fallback (a browser without WebSocket or
``EventSource``) cannot host lazy views at all
(``tests/playwright/test_multi_view_sse_http.py`` pins that it says so).
Server-side, a non-sticky ``{% live_render %}`` child's state is not preserved
between HTTP requests (``docs/website/guides/http-only-mode.md``).
"""
