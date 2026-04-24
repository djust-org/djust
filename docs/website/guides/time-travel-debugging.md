---
title: "Time-Travel Debugging"
slug: time-travel-debugging
section: guides
order: 32
level: intermediate
description: "Scrub back through event history and jump to any past view state (beyond Redux DevTools)"
---

# Time-Travel Debugging

**New in v0.6.1.** Dev-only. Every `@event_handler` dispatch records a
snapshot of the view's public state *before* and *after* the handler
runs. From the browser debug panel you can scrub back through the
history and jump to any past state — the server restores the snapshot
and re-renders so the page instantly reflects the past. Think
Redux DevTools, but for Django LiveViews and with zero client-side
state store.

Gated on `DEBUG=True` **and** per-view opt-in. Zero cost in
production — when the opt-in is off, the event dispatch path runs
without instrumentation.

## Quick start

```python
from djust import LiveView
from djust.decorators import event_handler


class CounterView(LiveView):
    template = "<div>{{ count }}</div>"

    # Opt into time-travel debugging for this view.
    time_travel_enabled = True

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    @event_handler
    def reset(self, **kwargs):
        self.count = 0
```

1. Open your app in a browser with `DEBUG=True`.
2. Click a few times to fire the `increment` handler.
3. Press `Ctrl+D` (or click the debug-bar icon) to open the debug
   panel, then switch to the **Time Travel** tab.
4. Click any past event in the timeline — the server restores the
   captured `state_before` (or `state_after`), and the page re-renders
   instantly.

## How it works

Time-travel adds a per-view-instance **ring buffer** of
`EventSnapshot` entries. Each snapshot captures:

- `event_name` — the handler that fired
- `params` — the event payload
- `ref` — the client-assigned monotonic ref
- `ts` — server time
- `state_before` — snapshot of public attributes BEFORE the handler
- `state_after` — snapshot AFTER the handler
- `error` — truncated error message if the handler raised

Capture reuses the same `_capture_snapshot_state()` filter that backs
the v0.6.0 state-snapshot feature: public, JSON-serializable public
attributes only. Private (`_`-prefixed) attributes and non-JSON
objects are filtered out automatically. The buffer is bounded
(default 100 events, override via
`LIVEVIEW_CONFIG["time_travel_max_events"]`) and thread-safe.

Restoration uses `safe_setattr` from `djust.security`, so dunder keys
(`__class__`, etc.) and unsafe names are refused even if the buffer
is tampered with.

The jump flow over the WebSocket looks like this:

```
Client                                  Server
------                                  ------
time_travel_jump                   →   handle_time_travel_jump
  {index: 3, which: "before"}
                                        restore_snapshot(view, snap)
                                        render_with_diff()
                                   ←   patch / html update
                                   ←   time_travel_state
                                          {cursor: 3, which: "before",
                                           history_len: 42}
```

## Config

| Setting                     | Default | Effect                              |
|-----------------------------|---------|-------------------------------------|
| `time_travel_enabled`       | `False` | Global breadcrumb (see system check `djust.C501`). Views still opt in via the class attribute. |
| `time_travel_max_events`    | `100`   | Per-view ring-buffer cap. Validated by `djust.C502`. |

Set via `LIVEVIEW_CONFIG` in `settings.py`:

```python
LIVEVIEW_CONFIG = {
    "time_travel_enabled": True,
    "time_travel_max_events": 50,
}
```

## Limitations

- **Side effects do not replay.** Restoring state rolls back in-memory
  attributes only. Any SQL writes / external API calls fired during
  the original handler are *not* undone. This is a debugging aid,
  not a transaction system.
- **Private attributes are not recorded.** The snapshot filter skips
  `_`-prefixed names. Put debug-worthy state in public attributes.
- **Non-JSON values are silently skipped.** Store primitives /
  dicts / lists in public attributes. ORM instances should be stored
  as serialized dicts or fetched by PK inside the handler.
- **No forward replay.** Jumping backwards does not queue up
  forward replay of the captured events — you see the state, not the
  sequence. Redux DevTools' time-travel-through-action-log is on the
  v0.6.2 roadmap.
- **Dev only.** `DEBUG=False` silently disables the jump receiver at
  the consumer layer. The class attribute is still safe to leave on
  in shared codebases — production just won't allocate the buffer
  because the consumer rejects jumps before touching it.

## Comparison

|                                 | djust Time Travel      | Redux DevTools         | Phoenix LiveView debug |
|---------------------------------|------------------------|------------------------|------------------------|
| Scrub past events               | ✓                      | ✓                      | (telemetry only)       |
| Restore state + re-render       | ✓                      | ✓ (reducer replay)     | ✗                      |
| State diff before / after       | ✓                      | ✓                      | ✗                      |
| Client-side state store needed  | ✗ (server holds it)    | ✓ (entire store)       | N/A                    |
| Works with server-side rendering | ✓                     | ✗                      | ✓                      |
| Forward replay / branching      | ✗ (v0.6.2 roadmap)     | ✓                      | ✗                      |

## Security notes

- Both recording and jumping are DEBUG-gated at the WebSocket
  consumer. A production client cannot coerce the server into
  restoring state by sending `time_travel_jump` frames.
- Restoration uses `safe_setattr`, matching the v0.6.0 state-snapshot
  hardening — dunder keys and anything failing the
  `SAFE_ATTRIBUTE_PATTERN` regex are rejected.
- Snapshots are held in process memory only. There is no persistence;
  a dev-server restart clears the buffer.

> **Do NOT enable `time_travel_enabled` on views holding PII or
> secrets.** The buffer stores up to 100 full snapshots of public view
> state. If your view contains passwords, tokens, session IDs, SSNs,
> credit card numbers, or similar sensitive fields, opt out — those
> values will sit in memory in a dev-panel-readable form for the
> lifetime of the process. This mirrors the guidance for
> `enable_state_snapshot` and the v0.6.0 state-snapshot feature.

### Async / background caveats

`state_after` is captured **synchronously** at the moment the handler
returns control to the dispatcher:

- Work scheduled via `start_async()` or wrapped in `@background` runs
  in a thread **after** the handler returns. Any state it mutates will
  appear in the **next** event's snapshot (or not at all, if no further
  event fires).
- `async def` handlers are fully awaited before `state_after` is
  captured, so awaited coroutines are reflected correctly. Only
  fire-and-forget background work is deferred out of the snapshot.

If you need to time-travel past the result of background work, mutate
a public flag in the background callback and trigger a follow-up event
to capture the final state.

## Related

- [Developer Tools](developer-tools.md) — the debug panel and its tabs.
- [Hot View Replacement](hot-view-replacement.md) — state-preserving
  Python reload in dev (v0.6.1 sibling feature).
- [State Snapshot API](state-snapshot.md) — the underlying
  `_capture_snapshot_state` filter reused here.
