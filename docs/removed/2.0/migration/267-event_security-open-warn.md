---
table: settings
covers: event_security
removed: `event_security` set to `"open"` or `"warn"` in `LIVEVIEW_CONFIG` or `DJUST_CONFIG`
use: Put `@event_handler` on every method a template binds (`dj-click`, `dj-submit`, ...), then delete the key. Only decorated methods are client events, on every transport (WebSocket, SSE, the page-POST fallback, components and the test client); startup refuses the two modes (any case) with this hint and any other value except `"strict"` as unknown (#4101). `"strict"` is accepted and does nothing.
---
