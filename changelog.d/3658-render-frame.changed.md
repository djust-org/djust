- **One `render` frame per turn, with `effects[]` (#3658).** Every turn result now
  reaches the client as one `{"type": "render", ...}` frame on the WebSocket, over
  SSE and as the HTTP answer body. Its DOM part is at most one of `patches`,
  `html`, or `view_id` + `html` (an embedded child); a frame with none of them
  acknowledges a turn that changed nothing. The side effects a handler queued
  (`push_event()`, `put_flash()`, `page_title` / `page_meta`, `set_layout()`,
  `live_patch()` / `live_redirect()`, and the auth or refusal redirect) ride in its
  `effects` list, always in the order push events, flash, page metadata, layout,
  navigation. A skip-render turn now sends its effects in the same order as any
  other turn. Effects queued by a `defer()` callback are sent in the same turn, in
  one extra effects-only frame (`source: "deferred"`) that the client applies after
  any server frame it is still holding; before, a `push_event()` from a deferred
  callback waited for the next turn. `flush_push_events()` sends a
  `source: "flush"` frame, which the client applies on arrival. The views queue
  every effect on one outbound queue (`CommandsMixin`); the public methods are
  unchanged. Custom JavaScript that read the old frames or the `_*` answer keys
  must read the `render` frame's DOM part and `effects` (hooks, `djust:*` events
  and `{% dj_flash %}` are unchanged), and tests that read frames match `"render"`
  and read `effects`. See "The wire format changed" in the 2.0 migration guide and
  the new Wire Protocol page.
