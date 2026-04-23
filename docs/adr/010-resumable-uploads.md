# ADR-010: Resumable Uploads Across WebSocket Disconnects

**Status**: Accepted
**Date**: 2026-04-22
**Deciders**: Project maintainers
**Related**: [ADR-007](007-package-taxonomy-and-consolidation.md),
[`python/djust/uploads/__init__.py`](../../python/djust/uploads/__init__.py),
[issue #821](https://github.com/johnrtipton/djust/issues/821)

---

## Summary

djust uploads currently die if the WebSocket drops mid-transfer. For long
mobile uploads (spotty cellular networks, background-tab throttling, OS
suspension) this is the dominant failure mode; desktop browsers on a
wired network rarely hit it. This ADR defines a resumable-upload protocol
inspired by Phoenix LiveView 1.0: server-side state (chunks received,
backend MPU / presigned / tempfile handle) persists in an injectable
state store, keyed by `upload_id`. On WS reconnect the client sends
`upload_resume` and picks up where it left off.

## Context

### Current failure mode

`UploadManager` in `python/djust/uploads/__init__.py` holds
`_entries: Dict[str, UploadEntry]` as an instance attribute on the
consumer's `view_instance`. When the WS drops:

1. `LiveViewConsumer.disconnect()` calls `view_instance._cleanup_uploads()`.
2. `UploadManager.cleanup()` aborts any in-flight `UploadWriter` via
   `writer.abort(ConnectionAbortedError("session closed"))` and deletes
   every `UploadEntry`.
3. On WS reconnect, the client sees the upload is gone and (today) gives
   up — there's no "resume from offset N" protocol.

This is fine for small desktop uploads but catastrophic for a 500 MB
mobile video over LTE: any brief network hiccup wipes minutes of
progress.

### What Phoenix LiveView 1.0 does

Phoenix stores upload state in the socket's `uploaded_entries`; on
reconnect the client re-sends a `progress` message keyed by the
persistent `entry.ref`, and the server replies with the last offset it
accepted. The resume happens inside one LV process (server memory), so
an OS restart or process crash still drops the upload — but the common
case (network blip, tab-throttle) survives.

### Failure domains we want to cover

| Failure | Current behavior | Desired behavior |
|---|---|---|
| WS drops, reconnects in <30s | Upload aborted | Resume from last chunk |
| Browser tab backgrounded on mobile | WS drops → abort | Resume on foreground |
| Server process restart | Abort (state in memory) | Resume if state in Redis |
| Client closes tab, reopens later | Abort | Prompt re-select (file ref lost) |
| Two tabs try to resume same `upload_id` | Undefined | Reject second (explicit error) |
| State store unavailable | N/A | Fall back to non-resumable path + warn |

## Decision

Ship a **`ResumableUploadWriter`** mixin path, a pluggable **state
store** interface with `InMemoryUploadState` and `RedisUploadState`
implementations, a new **`upload_resume`** WS message, a client-side
IndexedDB cache of `{upload_id, offset, file_hint}`, and an HTTP status
endpoint for state queries. Opt-in per upload slot via
`allow_upload(..., resumable=True)`.

## Wire protocol

All messages are existing JSON over the WS channel except chunks, which
stay on the current binary frame format (`[0x01][16-byte ref][4-byte
chunk_index][bytes...]`). The **`ref`** is the resumable `upload_id` —
same field, just gains a new persistence contract.

### `upload_register` (client → server, unchanged)

```json
{
  "type": "upload_register",
  "upload_name": "avatar",
  "ref": "b3e9...e2",
  "client_name": "big.mp4",
  "client_type": "video/mp4",
  "client_size": 524288000,
  "resumable": true
}
```

New optional `resumable` field. When `true` AND the slot was configured
with `resumable=True`, the server persists state keyed by `ref` into the
configured state store.

### `upload_resume` (client → server, new)

```json
{
  "type": "upload_resume",
  "ref": "b3e9...e2"
}
```

Sent on WS reconnect when the client has a retained `upload_id` + file
reference. Server replies with `upload_resumed`.

### `upload_resumed` (server → client, new)

```json
{
  "type": "upload_resumed",
  "ref": "b3e9...e2",
  "bytes_received": 67108864,
  "chunks_received": [0, 1, 2, 3, ...],
  "status": "resumed" | "not_found" | "locked"
}
```

- `status: "resumed"` — client resumes from `bytes_received` offset.
- `status: "not_found"` — state store has no entry (TTL expired, or
  never existed). Client must re-register the upload.
- `status: "locked"` — another WS session is actively uploading to this
  `ref`. Second client is rejected — we do NOT support take-over
  semantics in v1. See *Rejected alternatives*.

### `upload_chunk` (client → server, unchanged binary)

Same binary frame. Server appends to backend + updates state store on
each successful chunk.

### `upload_complete` / `upload_cancel` (unchanged)

Same binary frames. On `complete`, server finalizes backend + deletes
state store entry. On `cancel`, server aborts backend + deletes state
store entry.

### HTTP status endpoint

```
GET /djust/uploads/<upload_id>/status
```

Returns:
```json
{ "upload_id": "...", "bytes_received": N, "chunks_received": [...],
  "status": "uploading" | "complete" | "not_found" }
```

Auth: requires an active Django session cookie. The `upload_id` is a
UUID4 so prediction is infeasible (2^122 search space), and we
additionally scope entries to the session key on insert — a cross-user
status probe returns `not_found`.

## State store contract

```python
class UploadStateStore(Protocol):
    def get(self, upload_id: str) -> Optional[dict]: ...
    def set(self, upload_id: str, state: dict, ttl: int) -> None: ...
    def update(self, upload_id: str, partial: dict) -> Optional[dict]: ...
    def delete(self, upload_id: str) -> None: ...
```

`state` dict shape (JSON-serializable):
```python
{
    "upload_id": "...",
    "upload_name": "avatar",
    "client_name": "big.mp4",
    "client_type": "video/mp4",
    "client_size": 524288000,
    "bytes_received": 67108864,
    "chunks_received": [0, 1, 2, ...],
    "backend_state": {...},  # writer-specific (MPU upload id, etc.)
    "session_key": "abc123",  # for cross-session access control
    "created_at": 1745340000.0,
    "last_updated": 1745340042.3,
}
```

### Implementations

- **`InMemoryUploadState`** — process-local dict + lock. Default. Lost
  on process restart. Fine for dev and single-process deployments.
- **`RedisUploadState`** — requires `djust[redis]` extra. Stores each
  entry as a JSON blob under key `djust:upload:<upload_id>` with Redis
  native TTL. Atomic `update()` via `WATCH`/`MULTI`.

### Max state size

Hard-capped at **16 KB** per `upload_id`. A 2 GB file at 64 KB chunks
is 32,768 indices; encoded as a run-length-compressed list of ranges
(`[[0, 32767]]`) that's <20 bytes. Even 100 discontiguous ranges is
<2 KB. 16 KB is a generous safety margin; we reject state writes that
would exceed it with `UploadStateTooLarge`.

## ResumableUploadWriter

`ResumableUploadWriter(UploadWriter)` persists state BEFORE calling
`write_chunk` on the inner backend. On crash between "state persisted"
and "backend wrote", the retry re-sends the same chunk — the backend
(e.g. S3 MPU) is idempotent per (upload_id, part_number). This is the
safer direction than the reverse: a crash after "backend wrote" but
before "state persisted" just means the client re-sends a chunk we
already have — we detect the duplicate via `chunks_received` and skip.

## Failure modes

### State store unavailable (Redis down)

`ResumableUploadWriter.__init__` probes the store with a `get("__ping__")`
call. If it raises, we log a WARNING and fall back to non-resumable
behavior (same as today — upload aborts on WS drop). Better than
crashing the whole upload.

### Backend rejected a chunk

Same as today: `UploadWriter.write_chunk` raises → we call `abort()` and
mark the entry errored. **Additionally** we delete the state store
entry so a future `upload_resume` returns `not_found` instead of
resurrecting a corrupt upload.

### Orphan state

TTL handles it. Default 24 hours; configurable per-writer via
`ttl_hours=`. A periodic Celery-compatible task
(`djust.uploads.resumable.cleanup_orphan_states`) is provided as a
reference implementation for large deployments that want explicit
cleanup ahead of Redis's lazy expiry.

### Orphan MPU (S3-style)

`ResumableUploadWriter.abort()` is called by `UploadManager.cleanup()`
on session close — same as today. Additionally, a periodic scan of the
state store for entries older than `ttl_hours` that still have a
`backend_state.mpu_upload_id` can call `S3.abort_multipart_upload` to
clean up bucket-side. Provided as a reference management command:
`manage.py djust_uploads_cleanup`.

### Upload_id collision

UUID4 collision probability is negligible (2^-122). We additionally
require the `session_key` to match on resume — a stolen `upload_id` from
a different session is rejected.

## Security

1. **upload_id prediction**: UUID4 + session-scoped. An attacker who
   guesses an `upload_id` still can't resume someone else's upload.
2. **State store access control**: on `upload_resume`, server verifies
   `state["session_key"]` matches the current session. Mismatch →
   `status: "not_found"` (same response as missing entry — no
   information leak about whether the id exists).
3. **HTTP status endpoint**: requires active session + same
   session-scope check. Returns `404` on mismatch.
4. **State size cap**: 16 KB per entry. An attacker can't fill Redis by
   sending chunks with gaps to bloat `chunks_received`.
5. **Concurrent-resume DoS**: `status: "locked"` is returned instantly
   from the state store; no backend call. Rate-limited by the existing
   WS rate limiter.

## Why not tus.io?

The tus.io protocol is the obvious reference. We considered adopting it
wholesale and rejected it:

1. **Transport coupling**: tus is HTTP-first (PATCH requests with
   Upload-Offset headers). djust's upload path is WebSocket-native —
   one transport, one auth context, one disconnect handler. Bolting
   tus's HTTP semantics alongside the existing WS upload would double
   the code paths.
2. **Complexity**: full tus compliance requires implementing
   Termination, Checksum, Concatenation, and the core protocol — far
   more than we need. A minimal subset that only covers resume is
   essentially what we built, but without tus's HTTP baggage.
3. **Client library size**: the canonical tus-js-client is ~30 KB
   gzipped. Our whole client budget is ~87 KB gzipped. That's a
   non-starter.
4. **One-transport principle**: djust's Manifesto §4 ("One Stack, One
   Truth") pushes against a second RPC layer. Keeping uploads on the
   WS keeps one auth context, one retry story, one stack trace.

If a user specifically needs tus interop they can bring their own via
the pre-signed-PUT path (ADR mentions of #820) — tus can sit behind
S3-compatible storage that djust never sees.

## Rejected alternatives

### Take-over on concurrent resume

Two tabs open, both try to resume the same `upload_id`. We chose
**reject the second** for v1 because:
- User confusion: the first upload silently dying when they switch tabs
  is worse than a clear error.
- Protocol simplicity: no need for a "take-over" message + state
  invalidation dance.
- Can add opt-in take-over in v2 if real users request it.

### Persist state to the Django session

Rejected — Django sessions are scoped to one HTTP request/WS
connection's `session_key`. Two concurrent connections get two session
copies, last-writer-wins on save. State store needs atomic
multi-connection writes, which is Redis's job, not Django-session's.

### Per-chunk ACK

Current protocol has client fire-and-forget chunks. We considered
requiring server to ACK each chunk before client sends the next, for
stricter flow control. Rejected as v1 scope — increases latency,
doubles the round trips, and is unnecessary for resume (the server
side's `chunks_received` set is the source of truth on reconnect).

## Implementation summary

- `python/djust/uploads/storage.py` — state store interface +
  `InMemoryUploadState` + `RedisUploadState`
- `python/djust/uploads/resumable.py` — `ResumableUploadWriter` +
  `ResumableUploadMixin` + `ResumableUploadEntry` helpers
- `python/djust/uploads/views.py` — `UploadStatusView` (HTTP endpoint)
- `python/djust/websocket.py` — `upload_resume` message dispatch
- `python/djust/static/djust/src/15-uploads.js` — IndexedDB persistence
  + reconnect handler + `upload_resumed` processor

See the [`uploads_resumable/*`](../../python/djust/uploads/) source files
for detail.
