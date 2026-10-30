# ADR-044: Turn middleware — one async chain around every LiveView turn

**Status**: Proposed
**Date**: 2026-10-21
**Citations**: paths pinned to `main` at `cc973bfbf`.
**Deciders**: Project maintainers
**Related**:
- ADR-016 (transport runtime interface), ADR-022 (ViewRuntime convergence), ADR-038 (explicit exposure; its own always-on turn authority is unchanged here)
- #1777 / #1905 (`reauth_on_event`), #3563 (`AnyOf` permission specs — reauth keeps calling `check_view_auth_lightweight`), #1446 (zero-cost-when-unused)
- `python/djust/runtime.py`, `python/djust/websocket.py`, `python/djust/sse.py`, `python/djust/mixins/request.py`, `python/djust/api/dispatch.py`, `python/djust/hooks.py`

---

## Summary (plain language first)

A djust app has no single place to run code around **everything** a LiveView
does. Django middleware sees only the first HTTP page load; `on_mount` hooks see
only mounts; `reauth_on_event` is a fixed flag with three hand-written copies.
After the page loads, every event, URL change, server push, tick, background
result and reconnect runs view code that no app-level code wraps.

That matters when an app keys security on "who is acting". An app that sets a
`current_caller` context variable for owner-scoped model managers and Postgres
row-level security needs that variable set on **every** turn, on every
transport, and inside background work the turn starts. Plain Django middleware
gives a false sense of coverage: it sets the variable for the first page load
only.

This ADR adds **turn middleware**: a `LIVEVIEW_MIDDLEWARE` setting listing
`async def mw(turn, next)` callables, shaped like Django middleware. The chain
runs around every turn kind over every transport. A middleware returns
`await next(turn)` or refuses the turn. djust's own per-turn reauth and error
boundary become built-in middleware on the same chain.

```python
# settings.py
LIVEVIEW_MIDDLEWARE = ["myapp.turns.caller_context"]

# myapp/turns.py
from djust.turns import Refuse

async def caller_context(turn, next):
    caller = await resolve_caller(turn.user, turn.session)
    if caller is None:
        return Refuse.login()
    token = current_caller.set(caller)
    try:
        return await next(turn)
    finally:
        current_caller.reset(token)
```

## Decisions

### D1. Setting and shape

- `LIVEVIEW_MIDDLEWARE`: a top-level Django setting, an ordered list of dotted
  paths. Each resolves to an `async def mw(turn, next) -> Optional[Refusal]`.
  The first entry is outermost, as in Django's `MIDDLEWARE`.
- **Async only.** A sync callable is rejected by a system check (`djust.T0xx`,
  error) and refused at chain build time. Sync work inside a middleware uses
  `sync_to_async` explicitly. Rationale: no thread hop per turn; the hot path
  stays on the event loop.
- The chain is compiled once into nested closures, cached, and rebuilt on
  `setting_changed` for `LIVEVIEW_MIDDLEWARE` / `LIVEVIEW_CONFIG`.

### D2. The turn object

`djust.turns.Turn` (slots; one per turn):

| Field | Meaning |
|---|---|
| `kind` | `"mount"`, `"reconnect"`, `"event"`, `"params"`, `"push"`, `"async"`, `"render"` |
| `source` | finer detail (table in D3) |
| `transport` | `"ws"`, `"sse"`, `"http"`, `"api"` |
| `view_class` | the LiveView class; always set when djust could resolve it |
| `view` | the instance; `None` on a mount until the view is created |
| `event` | handler name (event), task name (async), `None` otherwise |
| `params` | read-only mapping of the frame's params (empty for server turns) |
| `request` | the best `HttpRequest` djust has: the HTTP request, the SSE request, or the view's `request` after mount; `None` for a WS mount before the request is built |
| `scope` | ASGI scope (WS) or `None` |
| `user`, `session` | lazy: from `request`, else from `scope` |
| `session_id` | the djust session id when there is one |
| `state` | a dict middleware may use to pass data inward |
| `started` | `time.perf_counter()` at turn start |

`djust.turns.current_turn()` returns the running turn (a context variable, closed
when the turn ends, so background work spawned from a finished turn sees `None`
until its own turn starts).

### D3. Every turn kind, every transport

A **turn** is one unit of work in which djust runs view code (mount, handler,
hook, render) or delivers view data to a client. The chain wraps:

| `kind` | `source` | WebSocket | SSE | HTTP / API |
|---|---|---|---|---|
| `mount` | `page`, `slot`, `batch`, `navigation`, `lazy` | `ViewRuntime.dispatch_mount` (all callers: page, slot, `mount_batch`, `live_redirect_mount`) | stream GET, slot mount, `_navigate` | `RequestMixin.get` (page), POST `djust_lazy_mount` (lazy) |
| `reconnect` | same as mount | mount frame with the client's reconnect hint | stream GET reusing an existing session id | — |
| `event` | `client`, `deferred` (`dj_activity` replay), `api`, `server_function` | `ViewRuntime.dispatch_event` (root, component, sticky child, actor) and the deferred-activity drain | same | `RequestMixin.post` (`client`), `api.dispatch_api` (`api`), `dispatch_server_function` (`server_function`) |
| `params` | `url_change` | `ViewRuntime.dispatch_url_change` | same | — |
| `push` | `server_push`, `db_notify`, `tick`, `presence`, `client_push` | consumer turn functions | — (SSE joins no groups) | — |
| `async` | `start`, `result`, `catch_up` | the whole background task (callback included), and nested inside it, `handle_async_result` + render under the render lock; the explicit-exposure catch-up render | same (runtime path; child views too) | — |
| `render` | `recovery` | `request_html` | — | — |

`reconnect` comes from a client hint (WS) or an existing session id (SSE). It is
informational: mount and reconnect run the same chain, so no security decision
may depend on telling them apart.

Where the chain sits on each path:

- Runtime client turns: at the top of `dispatch_mount` / `dispatch_event` /
  `dispatch_url_change` (every caller, including the `mount_batch`,
  `live_redirect_mount` and SSE stream paths that bypass `dispatch_message`).
- WebSocket server turns: around the part of each turn that holds the render
  lock (`_run_server_push_turn`, the locked part of `db_notify` and the tick).
  Presence and `push_event` delivery, the async start and the recovery render
  run the chain without the lock.
- HTTP: around `RequestMixin.get` / `aget` / `post`; the API after its own
  authentication and CSRF, so middleware sees the API-authenticated user.

A view refused on reauth in a turn that does not hold the render lock stops
being served at once (socket closed, references dropped); its release
(disconnect hooks, background work) waits for the render lock, as it always ran
under it.

**Not turns** (no view code runs and no view data is sent): ping, cursor and
presence heartbeats, upload register/chunk/resume frames (their completion
handlers run inside the event turn that consumes them), hot reload (DEBUG only),
`forward_replay` (DEBUG only time travel).

### D4. Ordering

```
[transport ingress: origin, frame size, per-connection rate limit]
  └─ error_boundary            (built-in, always)
      └─ reauth                (built-in, when reauth_on_event is on)
          └─ LIVEVIEW_MIDDLEWARE[0] … [n]
              └─ the turn: check_view_auth, on_mount, permission_required,
                 object permissions, mount()/handler/hook, render, send
```

- Built-ins are **fixed and outermost**. `LIVEVIEW_MIDDLEWARE` lists only app
  middleware, so setting it cannot drop a security built-in.
- App middleware **wraps djust's view-level auth**, as Django middleware wraps a
  view and its decorators. It sees refused turns and anonymous users and may
  refuse first. A middleware that only wants authorized turns checks
  `turn.user` itself.
- Explicit-exposure (ADR-038) authority checks stay inside the turn, unchanged.
- **No per-view opt-out.** Every turn runs the whole chain. A middleware that
  wants to skip some views decides from `turn.view_class` itself.

### D5. Refusal

A middleware refuses by returning a `Refusal` instead of calling `next`, or by
raising `django.core.exceptions.PermissionDenied` (mapped to `Refuse.deny()` by
the error boundary). The handler and the rest of the chain do not run.

| Refusal | WebSocket / SSE client turn | Server turn (push, async, render) | HTTP GET | HTTP POST / API |
|---|---|---|---|---|
| `Refuse.deny(message=None)` | `permission_denied` error frame (slot / batch mounts: `view_refused`); mount also closes 4403 | dropped; nothing sent | 403 | 403 `{"error", "code": "permission_denied"}` |
| `Refuse.login(next_url=None)` | navigate to `LOGIN_URL?next=…` + close 4403 (slot / batch: `view_refused`) | navigate + close 4403 | redirect | 403 `{"redirect": url}` |
| `Refuse.redirect(url)` | navigate frame (`url` must pass `url_has_allowed_host_and_scheme`) | navigate | redirect | 403 `{"redirect": url}` |
| `Refuse.flash(message, level="error")` | flash frame; the socket stays open | flash frame | 403 with message | 403 `{"error": message}` |
| `Refuse.rate_limited()` | `rate_limit_exceeded` frame | dropped | 429 | 429 `{"code": "rate_limited"}` |

`Refusal.close` (default per row above) can be set explicitly. A refused mount
leaves no mounted view.

### D6. Context propagation

The chain runs in the turn's own task and context. A context variable a
middleware sets before `await next(turn)` is visible to the handler, to sync
code djust runs through `sync_to_async` (asgiref copies the context), and to
every task the turn starts: `start_async`, `@background`, `assign_async`,
the tick task started by a mount. Each `async` turn then runs the chain again,
so a middleware re-checks before the background callback starts and again
before its result is applied and rendered.

djust never runs a turn in an empty `contextvars.Context`; the worker-pool
thread pin (`worker_pool.bind_session`) is itself a context variable.

### D7. Built-in middleware

- **`error_boundary`** (always, outermost). `PermissionDenied` from anywhere in
  the chain or turn becomes `Refuse.deny()`. Any other exception escaping an
  `event` or `params` turn becomes the value-free `handle_exception` envelope
  (with the event `ref`), as `ViewRuntime.dispatch_message`'s catch did, now
  also for direct callers (`handle_event`, the view lanes). Mount turns re-raise
  to their entry point's established handling (the SSE stream's protected 500,
  `mount_batch`'s `failed[]`, `dispatch_message`'s envelope). When diagnostics are
  allowed for a legacy view it re-raises to the transport's own handler, as
  before. Exceptions escaping server turns (and deferred replays) propagate to
  each turn's existing handler, so a failing `handle_async_result` still reaches
  its error arm. HTTP re-raises to Django. The finer, existing handlers inside
  the turn (a handler exception answers with an error frame and keeps the
  socket) are unchanged.
- **`reauth`** (when `LIVEVIEW_CONFIG["reauth_on_event"]` is on). Replaces the
  three copies. For a legacy view with `login_required` / `permission_required`:
  client turns (`event`, `params`, `render`) re-check every turn; server turns
  (`push`, `async`) re-check at most every `reauth_server_turn_interval`
  seconds per connection; mounts and HTTP are skipped (they authenticate fresh).
  It calls `check_view_auth_lightweight`, so `AnyOf` specs are honoured. This
  closes two gaps: legacy `url_change` and the runtime's `async` turns had no
  re-check.

**Rate limiting stays at transport ingress**, not on the chain. The
per-connection bucket must charge a frame when it arrives: a frame queued behind
a busy view (up to 64) and then refused would otherwise never be charged, so a
flooding client would never be disconnected; one `mount_batch` frame is many
mount turns (the default burst is 20); and non-turn frames (uploads, pings) must
be charged too. SSE gains the ingress check it lacks today (root events,
`url_change` and page mounts over SSE were not charged). The per-handler
`@rate_limit` stays in `_validate_event_security`, because it needs the resolved
handler. `Refuse.rate_limited()` lets app middleware add per-caller limits that
answer on the same wire form. SSE's frame check moves to the top of
`SSESession.dispatch`, so every POSTed frame is charged once (slot
mount/unmount and navigation were charged in their own branches before; root
events, `url_change` and page mounts were not).

### D8. Cost (hot path)

- No app middleware: each socket turn builds one `Turn`, runs the two
  built-ins and records its timing — about 2 µs per turn; each pass-through
  middleware adds about 0.5 µs (Apple-silicon laptop, Python 3.12). HTTP and
  API skip the chain entirely in this case (the built-ins do nothing there).
- With app middleware, HTTP runs the chain through `async_to_sync` (one loop hop
  per request); the inner turn returns to the request thread.
- Middleware runs on every tick and push. Anything slow (a backend session
  check) must cache; server turns may hold the session's render lock.

### D9. Timing

`run_turn` records `kind`, `source`, `transport`, view class and duration via
`djust.observability.timings.record_turn_timing`, in the same rolling window as
handler timings and, like them, always (an uncontended lock and a bounded deque
append). The DEBUG-gated `/_djust/observability/turn_timings/` endpoint serves
percentiles. A middleware can read `turn.started` for its own spans.

### D10. Async `check_permissions` (addendum, 2026-10-21)

A view may declare `async def check_permissions(self, request)`. It is **not**
turn middleware and adds nothing to the chain: it stays where the sync check
runs, so the D4 order is unchanged.

```
[ingress] → error_boundary → reauth (awaits the check, when on) → app middleware
    → the turn: login_required → permission_required → check_permissions
      (awaited on the loop) → Django access mixins → on_mount → mount()/handler
```

- **Mount** (WebSocket, SSE): `dispatch_mount` awaits it through
  `djust.auth.arun_pre_mount_auth`, inside the turn (app middleware context is
  set), before `mount()`.
- **Reauth** (`reauth_on_event`, WebSocket and SSE): the `reauth` built-in
  awaits it through `acheck_view_auth_lightweight`. The built-in is outside app
  middleware, so app middleware context is not set there.
- **Explicit exposure** (ADR-038) event and server-turn authorization: awaited
  through `aauthorize_event`.
- **Synchronous paths** (HTTP GET, page-POST fallback, API, `{% live_render %}`
  children, sticky re-authorization, explicit child events): `check_view_auth`
  awaits the coroutine from the request or worker thread (`async_to_sync`); on
  the event loop's own thread it raises instead of blocking the loop.

The steps around the check (login, `permission_required`, Django's access
mixins, HTTP-only access control) still run in a worker thread, since they may
use the ORM. A sync check runs exactly as before: one `sync_to_async` call of
the whole sequence.

A refusal is today's: `False` or `PermissionDenied` is a 403 / `permission_denied`
/ close 4403 for a signed-in user and the login redirect for an anonymous one;
any other exception fails closed. Before this, an `async def check_permissions`
was called and never awaited, and since a coroutine is truthy it allowed every
request (fail open).

Why it matters: asgiref runs every thread-sensitive `sync_to_async` call on one
shared thread. A sync check that waits on I/O (a backend session read bridged
with `async_to_sync`) holds that thread for every connection's handlers and
renders while it waits.

## Consequences

- One mechanism for per-turn cross-cutting code; reauth has one implementation.
- Security coverage gains: reauth on legacy `url_change`, on runtime async
  results and on `client_push` delivery; protected error envelopes on mount paths
  that bypassed `dispatch_message`; SSE frames are rate-limited at ingress.
- New public API: `djust.turns` (`Turn`, `Refusal`, `Refuse`, `current_turn`),
  `LIVEVIEW_MIDDLEWARE`, one system check.

## Open follow-ups

- **Uploads.** Upload register / chunk / resume frames are not turns, but an
  app-supplied `UploadWriter` runs on chunk frames, outside any turn, and
  `upload_register` has never been re-checked by `reauth_on_event`. Covering
  them needs a decision on the kind set (a new `upload` kind, or
  `event`/`upload`) and on reauth cost per chunk (a 10 MB file is ~157 frames;
  the server-turn throttle would fit).
- **Sticky children on `live_redirect`.** `_preserve_sticky_children`
  re-authorizes preserved sticky children (`check_view_auth_lightweight`,
  which can run an app's `check_permissions`) before the navigation's `mount`
  turn starts, so that check runs outside the chain.

## Alternatives considered

- **Built-ins listed in the default `LIVEVIEW_MIDDLEWARE`** (Django style,
  reorderable). Rejected: `LIVEVIEW_MIDDLEWARE = ["app.mw"]` would silently drop
  reauth; a system check would catch it only if run.
- **App middleware after view auth.** Rejected: a middleware could not refuse
  before djust's checks or observe refused turns.
- **Sync middleware support.** Rejected for the hot-path thread hop.
- **Per-view opt-out.** Rejected: an exemption is a hole in owner scoping.
- **Rate limiting on the chain.** Rejected (D7).
