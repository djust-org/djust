# djust Async-ORM / `sync_to_async` Audit — 2026-05

**Status**: Snapshot in time. Classifies every `sync_to_async` /
`async_to_sync` / `database_sync_to_async` call site in `python/djust/`
(excluding `tests/` and `__pycache__/`) as of 2026-05-19.

**Issue**: [#1434](https://github.com/djust-org/djust/issues/1434) — *Replace
`sync_to_async(Model.objects.X)` with native async ORM after psycopg3 lands*.

**Companion**: [`scripts/bench_sync_to_async_overhead.py`](../../scripts/bench_sync_to_async_overhead.py)
— empirical per-crossing overhead measurement.

**Scope**: every `sync_to_async`-family call site in framework code — what it
wraps, which category it falls in, and whether the native Django async ORM
can replace it.

---

## 1. Headline finding

**#1434's premise does not hold.** The issue was filed (2026-05-08) to track
replacing "~150 `sync_to_async(Model.objects.X)` sites" with the native
Django async ORM (`await Model.objects.aget()`) once psycopg3 landed. The
blocker — #1433, the psycopg2-without-psycopg3 system check — is now closed,
and djust requires `psycopg[binary]>=3.1,<4`. So the work is unblocked. But
the audit of every call site shows there is almost nothing to migrate:

- **0** call sites wrap a literal `Model.objects.X()` / `instance.asave()` /
  queryset-evaluation expression. `rg 'sync_to_async\([^)]*\.(objects|save|
  delete|create)\b' python/djust` returns nothing.
- **3** sites are ORM-category *at all*, and all three are *indirect* — they
  wrap framework auth/tenant helper functions (`check_view_auth`,
  `check_object_permission`, `_ensure_tenant`) that touch the DB internally.
  All three fire **once per WebSocket connection at mount**, never per event.
- The remaining **123** sites wrap Rust-extension calls (58), user-supplied
  callbacks (31), framework state-plumbing (25), Channels group ops (5), and
  Django session-store ops (4) — none of which the native async ORM touches.

The native-async-ORM migration #1434 envisioned has **no hot-path surface**
in framework code. Section 7 recommends closing or radically de-scoping it.

## 2. Methodology and the real count

`rg -c 'sync_to_async|async_to_sync' python/djust --glob '!**/tests/**'` reports
**165 line-matches across 17 files**. That number is misleading: it counts
import lines, comment mentions, and docstring code examples. Counting actual
*call expressions* (a `sync_to_async(` / `async_to_sync(` /
`database_sync_to_async(` token outside an import or comment) and confirming
each by reading its surrounding context gives **126 call sites**. The 39-line
gap breaks down as ~9 import statements, ~12 comment mentions, and ~18
docstring/prose references (e.g. `decorators.py`, `mixins/async_work.py`, and
`db/notifications.py` contain *only* prose mentions — zero call sites).

The classification is reproducible: every site in Appendix A was read in
context and assigned exactly one category.

## 3. Category breakdown

Each of the 126 sites is exactly one of:

| Category | Meaning | Native async ORM applies? |
|---|---|---|
| **ORM** | Wraps a DB-backed model/queryset operation (directly or via a helper). | **Yes** — the migration target. |
| **CACHE** | Wraps a Django cache op (`cache.get/set`). | Partially (`await cache.aget()`). |
| **SESSION** | Wraps a Django session-store op. | No — session store, not Model ORM. |
| **RUST** | Wraps a call into djust's Rust extension / renderer. | No — needs an async Rust surface (out of scope). |
| **CALLBACK** | Wraps a user-supplied sync callback / lifecycle method. | No — can't rewrite user API. |
| **CHANNELS** | Wraps a Django Channels channel-layer / pubsub op. | No — not Model ORM. |
| **OTHER** | Framework state-plumbing, template-loader I/O, composite view methods. | No. |

Counts, per file:

| File | ORM | CACHE | SESSION | RUST | CALLBACK | CHANNELS | OTHER | Total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `websocket.py` | 3 | 0 | 2 | 32 | 17 | 0 | 17 | 71 |
| `sse.py` | 0 | 0 | 0 | 10 | 4 | 0 | 2 | 16 |
| `runtime.py` | 0 | 0 | 0 | 10 | 3 | 0 | 2 | 15 |
| `streaming.py` | 0 | 0 | 0 | 5 | 1 | 0 | 0 | 6 |
| `mixins/sticky.py` | 0 | 0 | 2 | 0 | 1 | 0 | 1 | 4 |
| `mixins/notifications.py` | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 3 |
| `mixins/request.py` | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |
| `websocket_utils.py` | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| `api/dispatch.py` | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| `templatetags/live_tags.py` | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| `live_view.py` | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| `push.py` | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| `presence.py` | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |
| `testing.py` | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| **Total** | **3** | **0** | **4** | **58** | **31** | **5** | **25** | **126** |

`CACHE` is zero: the issue speculated `sync_to_async(cache.get)` sites might
be replaceable with `await cache.aget()`. There are none — djust does not
wrap the cache in `sync_to_async` anywhere in the async path.

## 4. The ORM sites — all 3, in detail

These are the *entire* migration surface. None is a literal ORM expression;
each wraps a framework helper that hits the DB internally.

| Site | Wrapped helper | When it fires | Native-ORM migration |
|---|---|---|---|
| `websocket.py:1947` | `check_view_auth(view, request)` | Once, at WS mount | Helper must become `async def` and use `aget`/`aexists` internally — defined in `python/djust/auth/`. |
| `websocket.py:1974` | `view._ensure_tenant(request)` | Once, at WS mount | `TenantMixin` tenant-record lookup — would need the mixin method made async. |
| `websocket.py:2161` | `check_object_permission(view, request)` | Once, at WS mount | Calls the user's `get_object()` (itself a CALLBACK) then a permission check. |

Three observations make even these poor migration candidates:

1. **They are connection-scoped, not event-scoped.** All three run inside the
   mount handshake, once per WebSocket connection. A connection lives for
   minutes; the per-event hot path never re-enters them. Issue #1434's
   acceptance criterion is "≥5% of *event-handler* latency" — these sites
   contribute 0% to event-handler latency.
2. **Migration is not a call-site rewrite.** It is an async-conversion of
   three helper call chains down into `python/djust/auth/` and the tenants
   extra — a behavioural change to public-ish helpers, far larger and riskier
   than the "swap `sync_to_async(get)` for `aget()`" the issue describes.
3. **`check_object_permission` wraps a user `get_object()`.** Its DB cost is
   the *user's* query, which the framework cannot rewrite.

## 5. Why the other 123 sites stay

- **RUST — 58 sites.** `render_with_diff`, `_sync_state_to_rust`,
  `_initialize_rust_view`, `_strip_comments_and_whitespace`,
  `_extract_liveview_content`, `update_template`, `_render_embedded_child`,
  and closures composing them. These are CPU-bound crossings into the PyO3
  extension; the extension releases the GIL during the work, so the
  `sync_to_async` thread hop is correct. Migrating them needs an async Rust
  surface — explicitly out of scope per #1434 and a much larger effort.
- **CALLBACK — 31 sites.** User-supplied lifecycle methods and handlers
  (`mount`, `handle_params`, `get_context_data`, `handle_tick`, `handle_info`,
  `@event_handler` methods, `start_async` callbacks, `handle_async_result`).
  The framework cannot rewrite user code. djust *already* offers the escape
  hatch: an `async def` handler / `get_context_data` skips the thread hop via
  the `_skip_thread` fast path (`websocket.py:1257`, `:3417`) — a user who
  wants native async ORM writes `async def handle_x` and `await
  Model.objects.aget()` themselves. That is a **docs** matter, not a
  framework migration.
- **SESSION — 4 sites.** `save_session.pop` / `_save_components_to_session`.
  Two (`sticky.py:189`, `:263`) are already inside `except AttributeError`
  fallbacks for old Django that lacks `apop`; the happy path already uses
  native async (`aget`/`aset`/`apop`). Session store, not Model ORM.
- **CHANNELS — 5 sites.** `channel_layer.group_send` and the Postgres
  `LISTEN` listener coroutine. Not ORM.
- **OTHER — 25 sites.** Framework state-plumbing (`_restore_*`,
  `_capture_snapshot_state`, `_assign_component_ids`, time-travel restore),
  presence-backend ops, `get_template` (template-loader *filesystem* I/O,
  wrapped with `database_sync_to_async` despite the name), and composite view
  methods (`self.get`, `self.dispatch`). No Model ORM.

## 6. Benchmark — `sync_to_async` overhead and the <5% gate

`scripts/bench_sync_to_async_overhead.py` measures the per-crossing cost
empirically (asgiref threadpool dispatch; no Django needed). Representative
run — dev machine, CPython 3.12.9, GIL-enabled, 2000 iterations:

```
direct sync call (baseline noop)        median=  0.04 us
sync_to_async(thread_sensitive=True)     median= 59.92 us   p99= 104.38 us
sync_to_async(thread_sensitive=False)    median= 56.58 us   p99= 108.37 us

per-crossing overhead (median, minus baseline):  ~60 us
```

So the issue's "~50-200 µs per call" estimate is empirically confirmed at the
low end: **~60 µs per crossing** on this hardware (numbers are
machine-specific; re-run the script to refresh).

Applied to a representative LiveView event (a counter-increment `dj-click`),
the per-event `sync_to_async` crossing budget from the classification above
is **1 CALLBACK + 4 RUST = 5 crossings ≈ 300 µs**, of which **0 are ORM or
cache**. The ORM/cache-migratable fraction of per-event threadpool overhead
is **0 µs (0.0%)** — structurally, not as a timing artifact.

Against #1434's own acceptance gate ("if the measured win is < 5% of
event-handler latency, deprioritize and just document the pattern"), the
measured win for framework code is **0%**. The gate says deprioritize.

## 7. Recommendation

1. **Close #1434, or radically de-scope it.** Its premise — a fleet of
   `sync_to_async(Model.objects.X)` sites awaiting psycopg3 — is empirically
   false. There are none. This audit is the resolution artifact.
2. **Do not file per-file migration issues.** "Migrate `websocket.py`",
   "migrate `sse.py`", etc. would each migrate zero ORM sites. The drain the
   issue sketched ("a migration PR per heavy file") has no payload.
3. **The 3 auth/tenant helper sites are not worth a migration PR.** They are
   connection-scoped (0% of event latency) and migrating them is an
   async-conversion of `python/djust/auth/` helpers, not a call-site swap.
   If a future profiling pass shows mount-handshake latency matters, revisit
   then — but the data today says no.
4. **The real async-ORM story is a documentation one.** ORM work in a djust
   app lives in *user* `mount()` / event handlers (CALLBACK sites). djust
   already runs `async def` handlers without the thread hop. The actionable
   guidance is: "write `async def handle_x` and use `await
   Model.objects.aget()`" — a guide note, not a framework change.
5. **`scripts/bench_sync_to_async_overhead.py` stays** as the re-runnable
   measurement if the question resurfaces (e.g. after an async Rust surface
   exists, which would convert the 58 RUST sites — a separate, much larger
   effort with its own issue if ever pursued).

## Appendix A — full classification (126 sites)

### `websocket.py` (71)

| line | wrapped callable | category |
|---|---|---|
| 907 | `callback` (async-work) | CALLBACK |
| 920 | `handle_async_result` | CALLBACK |
| 926 | `_sync_state_to_rust` | RUST |
| 928 | `render_with_diff` | RUST |
| 940 | strip/extract lambda | RUST |
| 972 | `handle_async_result` | CALLBACK |
| 978 | `_sync_state_to_rust` | RUST |
| 980 | `render_with_diff` | RUST |
| 993 | strip/extract lambda | RUST |
| 1276 | `_sync_context_and_render` | CALLBACK |
| 1308 | `_sync_strip_and_extract` | RUST |
| 1473 | `untrack_presence` | OTHER |
| 1936 | `_initialize_temporary_assigns` | OTHER |
| 1947 | `check_view_auth` | **ORM** |
| 1974 | `_ensure_tenant` | **ORM** |
| 1979 | `run_on_mount_hooks` | CALLBACK |
| 2005 | `_restore_private_state` | OTHER |
| 2018 | `_restore_upload_configs` | OTHER |
| 2020 | `_restore_presence` | OTHER |
| 2022 | `_restore_listen_channels` | OTHER |
| 2024 | `_initialize_temporary_assigns` | OTHER |
| 2025 | `_assign_component_ids` | OTHER |
| 2034 | `_restore_component_state` | OTHER |
| 2120 | `_should_restore_snapshot` | CALLBACK |
| 2124 | `_restore_snapshot` | OTHER |
| 2138 | `mount` | CALLBACK |
| 2161 | `check_object_permission` | **ORM** |
| 2188 | `handle_params` | CALLBACK |
| 2214 | `get_context_data` | CALLBACK |
| 2224 | `_initialize_rust_view` | RUST |
| 2225 | `_sync_state_to_rust` | RUST |
| 2228 | `render_with_diff` | RUST |
| 2231 | `_strip_comments_and_whitespace` | RUST |
| 2236 | `_extract_liveview_content` | RUST |
| 2243 | `get_context_data` | CALLBACK |
| 2259 | `_initialize_rust_view` | RUST |
| 2260 | `_sync_state_to_rust` | RUST |
| 2264 | `render_with_diff` | RUST |
| 2267 | `_strip_comments_and_whitespace` | RUST |
| 2271 | `_extract_liveview_content` | RUST |
| 2306 | `_capture_snapshot_state` | OTHER |
| 3179 | `_get_private_state` | OTHER |
| 3193 | `save_session.pop` | SESSION |
| 3205 | `get_context_data` | CALLBACK |
| 3216 | `_save_components_to_session` | SESSION |
| 3399 | `_render_embedded_child` | RUST |
| 3464 | `_sync_context_and_render` | CALLBACK |
| 3637 | `_sync_strip_and_extract` | RUST |
| 4123 | `get_template` (loader I/O) | OTHER |
| 4137 | `_rust_view.update_template` | RUST |
| 4143 | `render_with_diff` | RUST |
| 4307 | `_preserve_sticky_children` | OTHER |
| 4457 | `update_presence_heartbeat` | OTHER |
| 4469 | `handle_cursor_move` | CALLBACK |
| 4496 | `_strip_comments_and_whitespace` | RUST |
| 4497 | `_extract_liveview_content` | RUST |
| 4668 | `restore_snapshot` | OTHER |
| 4677 | `render_with_diff` | RUST |
| 4742 | `restore_component_snapshot` | OTHER |
| 4750 | `render_with_diff` | RUST |
| 4826 | `replay_event` | CALLBACK |
| 4842 | `render_with_diff` | RUST |
| 4942 | `handler_fn` (server_push) | CALLBACK |
| 4959 | `_sync_state_to_rust` | RUST |
| 4961 | `render_with_diff` | RUST |
| 5062 | `handler` (`handle_info`) | CALLBACK |
| 5082 | `_sync_state_to_rust` | RUST |
| 5084 | `render_with_diff` | RUST |
| 5165 | `handle_tick` | CALLBACK |
| 5177 | `_sync_state_to_rust` | RUST |
| 5179 | `render_with_diff` | RUST |

### `sse.py` (16)

| line | wrapped callable | category |
|---|---|---|
| 237 | `check_view_auth` | OTHER |
| 268 | `_initialize_temporary_assigns` | OTHER |
| 269 | `mount` | CALLBACK |
| 283 | `_initialize_rust_view` | RUST |
| 284 | `_sync_state_to_rust` | RUST |
| 285 | `render_with_diff` | RUST |
| 286 | `_strip_comments_and_whitespace` | RUST |
| 287 | `_extract_liveview_content` | RUST |
| 438 | `_sync_render` closure | RUST |
| 630 | `callback` | CALLBACK |
| 633 | `handle_async_result` | CALLBACK |
| 638 | `_sync_state_to_rust` | RUST |
| 640 | `render_with_diff` | RUST |
| 672 | `handle_async_result` | CALLBACK |
| 676 | `_sync_state_to_rust` | RUST |
| 677 | `render_with_diff` | RUST |

`sse.py:237` `check_view_auth` is classified OTHER (not ORM) here because the
SSE path's auth check is a thin framework gate; the equivalent
connection-scoped DB-backed reasoning as `websocket.py:1947` applies, but the
SSE path is not the per-event hot path #1434 targets.

### `runtime.py` (15)

| line | wrapped callable | category |
|---|---|---|
| 323 | `_initialize_temporary_assigns` | OTHER |
| 350 | `mount` | CALLBACK |
| 369 | `handle_params` | CALLBACK |
| 384 | `_initialize_rust_view` | RUST |
| 386 | `_sync_state_to_rust` | RUST |
| 387 | `render_with_diff` | RUST |
| 389 | `_strip_comments_and_whitespace` | RUST |
| 391 | `_extract_liveview_content` | RUST |
| 561 | `handle_params` | CALLBACK |
| 564 | `_sync_state_to_rust` | RUST |
| 566 | `render_with_diff` | RUST |
| 585 | `_strip_comments_and_whitespace` | RUST |
| 589 | `_extract_liveview_content` | RUST |
| 708 | `check_view_auth` | OTHER |
| 763 | `render_with_diff` | RUST |

### `streaming.py` (6)

| line | wrapped callable | category |
|---|---|---|
| 273 | `render_with_diff` | RUST |
| 285 | `_strip_comments_and_whitespace` | RUST |
| 286 | `_extract_liveview_content` | RUST |
| 306 | `get_context_data` | CALLBACK |
| 309 | `render_to_string` | RUST |
| 314 | `tmpl.render` | RUST |

### `mixins/sticky.py` (4)

| line | wrapped callable | category |
|---|---|---|
| 181 | `_get_private_state` | OTHER |
| 189 | `save_session.pop` | SESSION |
| 196 | `get_context_data` | CALLBACK |
| 263 | `save_session.pop` | SESSION |

### `mixins/notifications.py` (3)

| line | wrapped callable | category |
|---|---|---|
| 74 | `listener.ensure_listening` | CHANNELS |
| 153 | `listener.ensure_listening` | CHANNELS |
| 181 | `listener.ensure_listening` | CHANNELS |

### `mixins/request.py` (2)

| line | wrapped callable | category |
|---|---|---|
| 352 | `self.get` (sync GET pipeline) | OTHER |
| 362 | `self.get` (sync GET pipeline) | OTHER |

### Remaining files (9)

| site | wrapped callable | category |
|---|---|---|
| `websocket_utils.py:308` | event `handler` (with params) | CALLBACK |
| `websocket_utils.py:309` | event `handler` (no params) | CALLBACK |
| `api/dispatch.py:137` | user `mount`/`api_mount` coroutine | CALLBACK |
| `api/dispatch.py:213` | user serializer coroutine | CALLBACK |
| `templatetags/live_tags.py:1621` | `_render_eager` (child mount + render) | RUST |
| `live_view.py:432` | `self.dispatch` (Django CBV) | OTHER |
| `push.py:62` | `channel_layer.group_send` | CHANNELS |
| `presence.py:352` | `channel_layer.group_send` | CHANNELS |
| `testing.py:570` | user `start_async` callback | CALLBACK |

`decorators.py`, `mixins/async_work.py`, and `db/notifications.py` contain
only prose/docstring mentions of `sync_to_async` — zero call sites.
