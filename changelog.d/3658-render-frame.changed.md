- **One `render` frame per turn, with `effects[]`, and a self-reload for outdated tabs
  (#3658).** Every turn result now reaches the client as one
  `{"type": "render", ...}` frame on the WebSocket, over SSE and as the HTTP answer
  body. Its DOM part is at most one of `patches`, `html`, or `view_id` + `html` (an
  embedded child); a frame with none of them acknowledges a turn that changed
  nothing. The side effects a handler queued (`push_event()`, `put_flash()`,
  `page_title` / `page_meta`, `set_layout()`, `live_patch()` / `live_redirect()`,
  and the auth or refusal redirect) ride in its `effects` list, always in the order
  push events, flash, page metadata, layout, navigation. The `patch`,
  `html_update`, `noop`, `embedded_update`, `push_event`, `flash`,
  `page_metadata`, `layout`, `navigation` and `navigate` frames, and the HTTP
  answer's `_flash` / `_page_metadata` / `_push_events` / `_layout` /
  `_navigation` keys, are gone. A skip-render turn now sends its effects in the
  same order as any other turn. Effects queued by a `defer()` callback are sent in
  the same turn, in one extra effects-only frame (`source: "deferred"`); before,
  a `push_event()` from a deferred callback waited for the next turn. The views
  queue every effect on one outbound queue (`CommandsMixin`); the public methods
  are unchanged. The wire protocol has a version (`2`): the client sends it with
  every mount and POST, and a tab running an older cached bundle reloads itself
  once (WebSocket close code 4409, an SSE `reload` frame, or HTTP 409
  `protocol_mismatch`). The client bundle URLs carry `?v=<djust version>` so the
  reload fetches the new bundle. Tests that read frames: match `"render"` and read
  `effects` instead of the old frame types.
