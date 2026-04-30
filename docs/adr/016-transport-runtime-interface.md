# ADR-016: Transport Runtime Interface

**Status**: Proposed
**Date**: 2026-04-30
**Target version**: v0.9.2-1 (PR-A: minimal extraction); subsequent PRs migrate the rest
**Related**: #1237 (3 SSE bugs that motivate the refactor),
v0.9.1 retro Action #181 (two-commit shape), v0.9.1 retro Action #180
(single-implementer-per-checkout)

---

## Context

djust ships two transports today: WebSocket (`python/djust/websocket.py`,
4,912 lines) and Server-Sent Events (`python/djust/sse.py`, 815 lines).
SSE was added in v0.3.4 (PR #377) for environments that block
WebSocket. It implements its own mount + event-dispatch pipeline rather
than reusing the WebSocket consumer's logic.

The two paths have **diverged**. Issue #1237 surfaced three SSE-specific
bugs that all trace back to the same structural problem — every feature
has to be implemented twice or the SSE side falls behind:

1. URL kwargs not resolved (SSE reads `request.path`, which is the SSE
   endpoint URL, not the page URL).
2. `LiveViewSSE.sendMessage()` doesn't exist on the client (eight WS
   call sites assume it does and crash with `TypeError` over SSE).
3. `handle_params()` is never invoked over SSE (Phoenix-parity
   contract; works correctly under WS at `websocket.py:2129`).

The reporter proposed three workarounds (Referer header, ad-hoc
`sendMessage` shim, manual `handle_params` call inside the SSE mount
flow). Patching all three in place would fix the symptoms but leaves
the root structural problem — and the next feature would diverge again.

## Decision

Introduce a transport-agnostic **`ViewRuntime`** that owns the mounted
view instance and the dispatch pipeline (mount, event, url_change), and
a **`Transport`** Protocol that abstracts the wire (WS frame send vs SSE
queue push). Both transports become thin adapters whose job is wire-level
framing only:

```
                    ┌─────────────────────────────┐
                    │       ViewRuntime           │
                    │  (transport-agnostic)       │
                    │                             │
                    │  • view_instance, session_id│
                    │  • dispatch_message(data)   │
                    │  • dispatch_mount/event/    │
                    │      url_change             │
                    │  • render → patches         │
                    └────────┬────────────────────┘
                             │ self.transport.send(data)
                             ▼
                    ┌─────────────────────────────┐
                    │   Transport (Protocol)      │
                    │   async send(data: dict)    │
                    │   async send_error(...)     │
                    │   async close(code)         │
                    └────────┬────────────────────┘
                             │
                  ┌──────────┴──────────┐
                  ▼                     ▼
        WSConsumerTransport    SSESessionTransport
        (wraps send_json)      (wraps queue.put_nowait)
```

The runtime imports the already-shared utilities from
`websocket_utils.py` (`_validate_event_security`, `_call_handler`,
`_format_handler_not_found_error`, `_safe_error`) — none are duplicated.

### Client-side payoff

SSE gains a single `POST /djust/sse/<sid>/message/` endpoint that
accepts the same `{"type": "...", ...}` envelope a WebSocket frame
would carry, dispatched through the same `ViewRuntime`. With that in
place, `LiveViewSSE.sendMessage(data)` becomes one method that POSTs
the JSON; every existing `liveViewWS.sendMessage(...)` call site
(`18-navigation.js`, `02-response-handler.js`, `13-lazy-hydration.js`,
`15-uploads.js`) works transparently when SSE is active. **No
callsite-by-callsite branching.**

### Server-side payoff

The first WebSocket handler migrated to the shared runtime is
`handle_url_change` (websocket.py:3875-3922 → ~10-line shim over
`ViewRuntime.dispatch_url_change`). Subsequent PRs progressively
migrate `handle_event`, `handle_mount`, `handle_mount_batch`, etc.
Each migration is independent; nothing in this ADR forces the full
4,912-line consumer to migrate at once.

### Why NOT the Referer header for SSE URL kwargs

The reporter's workaround read `request.META.get("HTTP_REFERER")`
to discover the page URL server-side. We deliberately do not ship this:

- **Privacy.** Browsers strip Referer under
  `Referrer-Policy: no-referrer` (a common security default), or send
  only the origin (not the path).
- **Spoofability.** Referer is client-controlled. Using it to resolve
  `pk` lets a same-origin user mount a view bound to a record they
  shouldn't have access to.
- **Correctness.** Referer reflects where the page *came from*, not
  the page itself, after `pushState`-driven navigation.

The mount-frame approach (client sends `url: window.location.pathname`)
mirrors what WebSocket already does — first-party data over an
authenticated session, validated by `django.urls.resolve()`.

## Phasing

PR-A (v0.9.2-1, ~1100 LOC):

- New `python/djust/runtime.py` — `ViewRuntime`, `Transport`,
  `WSConsumerTransport`, `SSESessionTransport`, three dispatchers.
- New `POST /djust/sse/<sid>/message/` endpoint.
- New SSE client mount-frame flow (`_sendMountFrame()` in
  `03b-sse.js` posts `url: window.location.pathname` on connect).
- WebSocket `handle_url_change` migrated to shim over
  `ViewRuntime.dispatch_url_change` (proves the shared codepath).
- `LiveViewSSE.sendMessage(data)` + `sendEvent` delegating to it.
- Tests: ~7 in `python/tests/test_sse.py` + new `test_runtime.py`
  (~150 LoC) + new `test_sse_ws_symmetry.py` (~80 LoC) + 6 in
  `tests/js/sse-transport.test.js`.

Subsequent PRs (no fixed schedule, none blocked by PR-A):

- Migrate `handle_event` to runtime — large; touches cache config,
  async-pending, patch compression, embedded children, actors.
- Migrate `handle_mount` to runtime — largest; owns actors,
  snapshots, sticky child views.
- Migrate `handle_mount_batch`, `handle_request_html`,
  `handle_live_redirect_mount`.
- Embedded child-view dispatch over SSE.

## Future transports

The `Transport` Protocol generalises beyond WS+SSE. Adding a new
transport reduces to: implement `Transport.send`, add a wire-level
adapter that calls `runtime.dispatch_message(data)` when frames
arrive. View lifecycle, dispatch, and rendering don't change.

| Transport | When it earns its keep | Effort |
|---|---|---|
| **HTTP Long Polling** | Networks blocking both WS and SSE (rare; some legacy corporate proxies). Universal HTTP compatibility, higher per-frame overhead. | ~150 LoC: session holder + GET-with-timeout + reuse `/message/` endpoint. |
| **WebTransport (HTTP/3)** | Lower-latency over QUIC; once Firefox stable >12 months and CDN/proxy support matures. UDP often blocked on corporate networks today. | ~250 LoC: aioquic-based session + adapter. |
| **HTTP/2 Server Push** | Never. Chrome removed support in 2022; effectively dead. | n/a |
| **gRPC-Web streaming** | Never. Too heavy for a Django framework (Envoy proxy + protobuf framing). | n/a |

**Recommendation.** Ship WS + SSE in PR-A. File a tracker issue for
long-polling fallback gated on a concrete user reporting an environment
where SSE doesn't work. Add a 2026 watch-list item for WebTransport.
Don't pre-build either — the architecture pays the future-flexibility
cost regardless.

## Consequences

### Positive

- One bug-fix surface for shared lifecycle behaviour
  (`handle_params`, URL-kwarg resolution, error envelopes,
  rate-limiting). New transports inherit correctness instead of
  re-implementing it.
- Symmetry tests (`test_sse_ws_symmetry.py`) lock in identical
  behaviour across transports. Future regressions trip the test.
- WebSocket consumer can shrink from 4,912 lines toward a thin
  Channels-specific layer over many PRs.
- Adding a third transport (long-polling, WebTransport) does not
  require duplicating dispatch logic.

### Negative

- Two-class indirection (`Transport` + `ViewRuntime`) costs marginal
  cognitive load when reading the WebSocket consumer end-to-end.
  Mitigated by the migration being incremental and well-named.
- `LiveViewConsumer` retains its existing handlers in PR-A — they
  coexist with the runtime for one or more PRs. Until `handle_event`
  migrates, the runtime's `dispatch_event` is only used by SSE,
  meaning event-dispatch logic still has a brief WS-vs-runtime split
  (acknowledged tradeoff to keep PR-A small).

### Neutral

- Public API unchanged. `LiveViewSSE.sendEvent`, the `/event/`
  endpoint, the `?view=` GET-mount flow all stay (the latter two as
  back-compat aliases). Internal helpers `_sse_mount_view`,
  `_sse_handle_event` shrink to thin wrappers and are marked private.

## Implementation notes

- `dispatch_mount` early-returns if `runtime.view_instance is not
  None` to handle the legacy GET-mount + new POST mount-frame race.
- `33-sw-registration.js` monkey-patches `ws.sendMessage` for
  reconnection buffering. With SSE also exposing `sendMessage`,
  verify the SW wrapper short-circuits on `LiveViewSSE` instances;
  add a one-line guard if not.
- `dispatch_mount` does NOT support actor-based mount in PR-A
  (matching the documented SSE limitation). Emits a structured error
  envelope instead of crashing.
- Two-commit shape per Action #181: implementation + tests in commit
  one; docs (`docs/sse-transport.md` + `CHANGELOG.md`) in commit two.

## References

- Plan file: `/Users/tip/.claude/plans/sequential-hugging-brooks.md`
- Issue: https://github.com/djust-org/djust/issues/1237
- WebSocket consumer: `python/djust/websocket.py:3875-3922`
  (`handle_url_change`), `:1851-1852` (page-URL extraction), `:2129`
  (`handle_params` call)
- SSE module: `python/djust/sse.py:148-313` (`_sse_mount_view`),
  `:243` (the `request.path` mis-use), `:812-815` (URL patterns)
- Client SSE: `python/djust/static/djust/src/03b-sse.js` (full file)
- Client WS for reference: `python/djust/static/djust/src/03-websocket.js:981-1008`
  (`sendMessage`)
- Shared utilities: `python/djust/websocket_utils.py`
  (`_validate_event_security`, `_call_handler`)
