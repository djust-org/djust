---
title: "Turn Middleware"
slug: turn-middleware
section: guides
order: 12.95
level: advanced
description: "LIVEVIEW_MIDDLEWARE: one async chain around every LiveView turn — mount, events, URL changes, pushes, ticks, background results and reconnects — on every transport"
---

# Turn Middleware

Django middleware runs once, for the first HTTP page load. After that a LiveView
keeps running view code over the socket: events, URL changes, server pushes,
ticks, background results, reconnects. **Turn middleware** wraps all of it.

`LIVEVIEW_MIDDLEWARE` lists `async def mw(turn, next)` callables. Every turn, on
every transport (WebSocket, SSE, the HTTP fallback and the auto-exposed HTTP API),
runs them in order, like Django's `MIDDLEWARE`. The design is
[ADR-044](https://github.com/djust-org/djust/blob/main/docs/adr/044-turn-middleware.md).

Use it for anything that must hold for **every** turn: who is acting (for
owner-scoped managers or Postgres row-level security), tracing spans, audit
context, per-caller rate limits.

> Plain Django middleware gives a false sense of coverage: it sets a context
> variable for the page load only. Every later event, push and background result
> runs without it.

## Quick start

```python
# settings.py
LIVEVIEW_MIDDLEWARE = ["myapp.turns.caller_context"]
```

```python
# myapp/turns.py
import contextvars

from djust.turns import Refuse

current_caller = contextvars.ContextVar("current_caller", default=None)


async def caller_context(turn, next):
    caller = await resolve_caller(turn.user, turn.session)  # your code; cache it
    if caller is None:
        return Refuse.login()
    token = current_caller.set(caller)
    try:
        return await next(turn)
    finally:
        current_caller.reset(token)
```

A middleware returns `await next(turn)` to run the rest of the turn, or a
refusal to stop it. Middleware must be `async def`; a sync entry fails system
check [C026](error-codes.md#c026-invalid-liveview_middleware).

## The turn object

| Attribute | Meaning |
|---|---|
| `turn.kind` | `"mount"`, `"reconnect"`, `"event"`, `"params"`, `"push"`, `"async"` or `"render"` |
| `turn.source` | finer detail, see the table below |
| `turn.transport` | `"ws"`, `"sse"`, `"http"` or `"api"` |
| `turn.view_class` | the LiveView class |
| `turn.view` | the view instance; `None` during a mount until djust creates it |
| `turn.event` | the handler name (`event`) or task name (`async`) |
| `turn.params` | read-only mapping of the frame's params |
| `turn.request` | the HTTP or SSE request, when the transport has one |
| `turn.scope` | the ASGI scope (WebSocket) |
| `turn.user`, `turn.session` | from the request, else from the scope |
| `turn.state` | a dict to pass data to inner middleware |
| `turn.started` | `time.perf_counter()` when the turn started |

`djust.turns.current_turn()` returns the running turn from anywhere inside it.

## Every turn kind

| `kind` | `source` | When |
|---|---|---|
| `mount` | `page`, `slot`, `batch`, `navigation`, `lazy` | a view is mounted: the page GET, a socket mount, a lazy view beside the page, `mount_batch`, a `live_redirect` |
| `reconnect` | as for mount | a mount after the socket reconnected (a client hint; treat it exactly like `mount`) |
| `event` | `client`, `deferred`, `api`, `server_function` | a client event, a deferred `dj_activity` replay, an HTTP API or `@server_function` call |
| `params` | `url_change` | `handle_params` after a `live_patch` or back/forward |
| `push` | `server_push`, `db_notify`, `tick`, `presence`, `client_push` | the server delivers data or runs a hook without a client event |
| `async` | `start`, `result`, `catch_up` | `start_async` / `@background` work starts, and its result is handled |
| `render` | `recovery` | the client asks for full HTML after a failed patch |

Server push, ticks, `db_notify` and presence exist on the WebSocket only (SSE
joins no channel groups); background work runs on WebSocket and SSE; HTTP and
the API have page mounts and events.

## Order

```
error_boundary   (built-in, always)
  reauth         (built-in, when LIVEVIEW_CONFIG["reauth_on_event"] is on)
    LIVEVIEW_MIDDLEWARE[0] ... [n]
      the turn: login_required / permission_required, on_mount hooks,
      object permissions, mount() / handler / hook, render, send
```

- **Built-ins are outermost and cannot be removed.** `LIVEVIEW_MIDDLEWARE`
  lists only your middleware, so setting it never drops `reauth_on_event`.
- **Your middleware wraps djust's view-level auth**, as Django middleware wraps a
  view and its decorators. It also sees anonymous users and turns djust is about
  to refuse.
- There is **no per-view opt-out**. A middleware that should skip some views
  checks `turn.view_class` itself.

## Refusing a turn

Return a refusal instead of calling `next`, or raise
`django.core.exceptions.PermissionDenied` (same as `Refuse.deny()`). The rest
of the chain and the turn do not run.

| Refusal | Socket (WebSocket / SSE) | HTTP page GET | HTTP POST / API |
|---|---|---|---|
| `Refuse.deny(message=None)` | `permission_denied` error frame (with the event `ref`); a refused mount closes 4403 | the project's 403 handler | 403 JSON |
| `Refuse.login(next_url=None)` | navigate to `LOGIN_URL`, close 4403 | redirect to `LOGIN_URL?next=...` | 403 `{"redirect": ...}` (API: 401) |
| `Refuse.redirect(url)` | navigate frame | redirect | 403 `{"redirect": ...}` |
| `Refuse.flash(message, level="error")` | flash frame; the socket stays open | 403 | 403 JSON |
| `Refuse.rate_limited()` | `rate_limit_exceeded` frame | 429 | 429 |

Redirect targets must stay on your site; anything else falls back to `/`. A
refused server turn (`push`, `async`) is dropped: nothing is sent unless the
refusal navigates or flashes. Pass `close=True` / `close=False` to override
whether a socket turn closes the connection.

## Context reaches background work

A context variable set before `await next(turn)` is visible to the handler, to
sync code djust runs in a thread, and to every task the turn starts:
`start_async`, `@background`, `assign_async`, the tick loop a mount starts.

```python
@event_handler()
def generate(self, **kwargs):
    self.start_async(self._generate)       # current_caller is still set in there

def _generate(self):
    return Report.objects.for_caller().build()   # owner-scoped, no caller argument
```

A task a view starts with `self.create_task()` inherits the context the same
way; it renders nothing, so it is not a turn of its own (a push it sends runs
the chain on the receiving socket).

Background work also runs the chain itself: once before the callback starts
(`async`/`start`) and again before its result is handled and rendered
(`async`/`result`), so a caller who signed out in between is refused.

## A transaction per sync step: `sync_turn_wrapper`

A middleware wraps the whole turn, which is async: the turn runs the view's
sync code in separate hops (the handler, `mount`, `handle_params`, a
`start_async` callback and its `handle_async_result`, `handle_tick`, the
render's `get_context_data()`, where the JIT evaluates the view's QuerySets,
the authorization checks, the `on_mount` hooks, `connected()` and, off the event
loop, a released view's teardown hooks) with awaits between them. With
`worker_threads` a pool thread runs other sessions' hops in those gaps, and
Django's database connection belongs to the thread. So a `transaction.atomic()`
opened in a middleware would span awaits and take in other sessions' queries.

`LIVEVIEW_CONFIG["sync_turn_wrapper"]` is the place for thread-bound state such
as a transaction. It names a callable `wrapper(view, step, turn)` returning a
context manager; djust enters it **on the thread that runs the step**, around
the step, so what it opens closes inside one sync call:

```python
# settings.py
LIVEVIEW_CONFIG = {"sync_turn_wrapper": "myapp.db.caller_scope"}

# myapp/db.py
from contextlib import contextmanager, nullcontext
from django.db import connection, transaction

def caller_scope(view, step, turn):
    if turn is not None and turn.transport == "http":
        return nullcontext()        # the request's own transaction holds
    user = turn.user if turn is not None else getattr(view.request, "user", None)
    return _as_caller(getattr(user, "pk", None))

@contextmanager
def _as_caller(user_id):
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id or "")])
        yield
```

- `step` is one of:

  | `step` | Runs |
  |---|---|
  | `"event"` | an event handler; also a time-travel replay of one |
  | `"mount"`, `"handle_params"` | `mount()`, `handle_params()`. A view that sets `params_form` enters `"handle_params"` twice for a URL: once to validate it (the form's validators and querysets run there), once for `handle_params()` or `handle_invalid_params()` |
  | `"hook"` | another sync view hook: a push handler (in the stock turn and in the worker-pool one-hop turn), `handle_info`, `handle_cursor_move()`, `update_presence_heartbeat()`, and the `get_presence_key()` / `untrack_presence()` reads |
  | `"async"` | a `start_async` / `@background` callback **and** the `handle_async_result` call that receives its outcome (two separate entries) |
  | `"tick"` | `handle_tick()` |
  | `"render"` | every call djust makes to `get_context_data()`, on every transport |
  | `"auth"` | an authorization decision that runs your code: `check_permissions()`, Django's access mixins and replayed `dispatch` decorators (`test_func`), `@permission_required` on a handler, and the object-permission check (`get_object()` + `has_object_permission()`) |
  | `"lifecycle"` | the `on_mount` hooks (the whole chain is one entry), `connected()` and `disconnected()`, the snapshot overrides (`_should_restore_snapshot()`, `_restore_snapshot()`, `_capture_snapshot_state()`, including the per-event signed-snapshot refresh), the restore of private state (`_restore_private_state()`, which re-fetches private model references; a guarded step, so a wrapper that swallows a failure cannot leave a half-restored view), and a released view's teardown hooks **only when they run on a worker thread**; on the event loop they run with no step (see [Not wrapped](#not-wrapped)) |
- `view` is the view or component whose code runs; `turn` is
  `djust.turns.current_turn()`, or `None` when the step runs outside a turn (a
  `start_async` callback runs between its start and result turns).
- A step that runs inside another step on the same thread (a handler that calls
  `get_context_data()`) does not enter the wrapper again.
- **Accept every step name.** The list of steps grows (`"auth"` and
  `"lifecycle"` are new with #3637). A wrapper that ends in
  `else: raise ValueError(step)` fails every mount and event after an upgrade. Let
  a name you do not know pass through unchanged (example below).
- **An `"auth"` step fails closed, when it is the outermost step.** If the wrapper
  raises (on entry or on exit, for example a failed commit) the check is refused,
  and the object-permission cache (`view._object`) is only written after the step
  exits cleanly. If the wrapper's `__exit__` swallows an exception that the check
  or an `on_mount` hook raised, djust raises `PermissionDenied` instead of
  treating the missing verdict as "allowed". The same guard covers the
  `on_mount` chain, the `_restore_snapshot` override and `_restore_private_state`,
  because their outcome is a verdict too (a swallowed failure in the last would
  otherwise leave a silently half-restored view). The guard belongs to the step djust enters: a check that runs
  *inside* another step on the same thread (an `"auth"` check called from a
  `mount()` that is itself being wrapped) does not enter the wrapper again, so a
  wrapper that swallows exceptions on the **outer** `"mount"` / `"event"` step is
  not guarded here. Do not suppress exceptions in a wrapper.
- **Entries per request.** A request enters the wrapper several times on every
  transport. These sequences were measured with a counting wrapper against a view
  that overrides `handle_params()` and `get_object()` (the test
  `test_the_guides_entries_per_request_*` pins them, so this list cannot drift):

  | Request | Entries, in order |
  |---|---|
  | HTTP page GET | `"auth"` (view-level), `"lifecycle"` (the `on_mount` chain, only when the view has one), `"mount"`, `"handle_params"`, `"auth"` (object check, when `get_object()` is overridden), `"render"`, `"render"` (the second render builds the diff baseline) |
  | HTTP POST event, no saved state | `"auth"`, `"mount"`, `"handle_params"`, `"auth"`, `"event"`, `"render"`, `"render"` |
  | HTTP POST event, saved state restored | `"auth"`, `"lifecycle"` (the private-state restore), `"auth"`, `"hook"` (`handle_restore()`), `"event"`, `"render"`, `"render"` |

  Each is one entry on the thread that runs it. Keep the wrapper cheap.
- `disconnected()` runs when a connection ends, outside any turn: the wrapper
  gets `turn=None`. It runs the hook with the tenant bound and a failing wrapper
  is logged, never raised, like a failing hook.
- The wrapper is entered on **every transport** for the `"auth"` steps and
  `on_mount` hooks (the HTTP page GET and API included), as it already is for
  `"render"`; a wrapper that opens a transaction for `turn.transport == "http"`
  should return `nullcontext()` as in the example above.
- An `async def` handler or hook runs on the event loop and is not wrapped; its
  own `sync_to_async` calls are yours to scope. A `sync_safe` view renders on
  the loop, so its wrapper runs there too.
- `LiveViewTestClient` enters the same steps for **sync** code, so a test of a
  handler that reads rows runs inside the same scope as the socket:
  `send_event()` (`"event"`), `send_push()` and `trigger_info()` (`"hook"`), and
  the callbacks and `handle_async_result` calls that `render_async()` drains
  (`"async"`); `LiveComponentTestClient.send_event()` is an `"event"` step too.
  An `async def` hook is run to completion unwrapped, as on the socket (inside a
  running event loop, on a helper thread). The one gap: `send_event()` with an
  `async def` handler still only calls it and drops the coroutine, so that test
  does not run the handler at all. The client does not tick, so `handle_tick` is
  called by your own test.
- A `handle_async_result` (or, on the default tick, `handle_tick`) written as
  `async def` is refused with `TypeError: sync_to_async can only be applied to
  sync functions`, with or without a wrapper: both are called off the loop.
- One entry per hop, not per task: a `start_async` callback and the
  `handle_async_result` that receives its outcome are two entries. If the
  wrapper's own exit raises after `handle_async_result` succeeded, the failure
  is reported as the task's error and `handle_async_result` is called a second
  time with `error=`. Keep the wrapper's exit from raising (let the
  transaction's commit failure surface in the handler instead), or make the hook
  safe to run twice.
- Unset (the default), nothing is wrapped and nothing is paid.

A wrapper that accepts every step name, today's and any added later:

```python
from contextlib import nullcontext

KNOWN = {"event", "mount", "handle_params", "hook", "async", "tick", "render", "auth", "lifecycle"}

def caller_scope(view, step, turn):
    if step not in KNOWN:
        return nullcontext()      # a step added by a later djust
    return open_scope(view, turn)  # your scope, as before
```

### Not wrapped

Everything above is the sync code djust runs on a worker thread through
`sync_to_async`. What still runs without the wrapper, and why:

- **An `async def` hook** of any kind (`mount`, a handler, `check_permissions`,
  `handle_tick`, a push hook, `handle_cursor_move`, `get_presence_key`): it runs
  on the event loop, so a thread-bound scope cannot be open in it. Its own
  `sync_to_async` calls are yours to scope. djust refuses an `async def` where
  only a sync hook is allowed (`TypeError`), with or without a wrapper.
- **Tenant resolution** (`TenantMixin` `_ensure_tenant`, run beside the auth
  check): it decides *which tenant* the turn is for, the input of the scope, so
  it cannot run inside it.
- **Teardown hooks on the event loop.** `_on_sticky_unmount()`,
  `_cleanup_on_unregister()` and `_cleanup_uploads()` run when a view or child is
  released. Which thread runs them depends on the transport:

  | Release | Thread | Step |
  |---|---|---|
  | WebSocket disconnect, a second `mount` frame, the teardown of a `live_redirect` | the event loop | none |
  | SSE: the stream's own close, a rate-limit disconnect | the event loop | none |
  | SSE navigation, the release of a view mounted beside the page, and `response.close()` of a stream that never started | a worker thread | `"lifecycle"` |
  | WebSocket `live_redirect` discarding a sticky child whose auth now fails | a worker thread | `"lifecycle"` |

  On a worker thread each hook is its own `"lifecycle"` entry (`view` is the object
  whose hook runs, `turn` is `None`, as for `disconnected()`). A wrapper that raises
  on entry does not skip the hook: the failure is logged and the hook runs without
  the scope; a wrapper that raises on exit after the hook ran is logged, not raised,
  because the view is already released. On the event loop the hooks are not wrapped:
  a wrapper that opens a transaction would itself raise there, and a hook that
  queries on the loop fails with or without a wrapper. Do queries in
  `disconnected()` instead, which always runs on a worker thread as a
  `"lifecycle"` step.
- **Hooks that run inside a hop that is already a step.** These were run, with a
  recording wrapper, and the step that was open when the hook body ran is the one
  listed (`test_sync_turn_inside_audit_3646.py`):
  `handle_component_event()`, `LiveComponent.update()` and a component's
  `_handle_event()` run under the `"event"` step of the handler that reaches them;
  `LiveComponent.mount()` under the `"mount"` step of the view that creates the
  component; `get_render_only_context()` under the `"render"` step. The
  `DataTableMixin.handle_*` overrides run under `"event"`, and the form hooks
  `get_form_class()`, `get_form_kwargs()` and `_create_form()` under `"mount"` and
  `"event"`. `serialize_for_template()` is an app helper the framework never
  calls, so it runs wherever your code calls it. `get_http_status()` runs in the
  HTTP response phase. The *other* component and tag hooks are not here: see
  [template-time hooks](#not-wrapped-template-time-form-and-tag-hooks).
- **State restores that assign with `setattr`.** The time-travel jump
  (`restore_snapshot`, `restore_component_snapshot`) and a component restore
  (`_restore_component_state`) assign saved fields with `safe_setattr` / `setattr`,
  so a property setter you wrote would run there without the scope. They call no
  hook by name and read no rows. (`_restore_private_state` is different: it
  re-fetches private model references, so it is a `"lifecycle"` step.)
- **Identity resolution before the scope exists.** `establish_mount_session` and
  `fresh_socket_request` call Django's `get_user()`, which runs your authentication
  backend's `get_user()` to find the caller; the scope is built from that caller,
  so it cannot be open yet.
- **Preserving sticky children across a `live_redirect`**
  (`_preserve_sticky_children`) runs on a worker thread and calls two things that
  are covered, not unwrapped: the auth re-check goes through the `"auth"` leaves,
  and discarding a child whose auth now fails runs its teardown hooks as
  `"lifecycle"` steps (see above).
- **The render plumbing around `get_context_data()`** (template build, Rust state
  sync, HTML post-processing). Only the `get_context_data()` calls inside it are
  `"render"` steps.
- **`get_template()` inside the render** (while the template is built for a view
  render) runs in the framework's render plumbing with no step open; an HTTP GET
  shows none. Only the two direct calls (the actor mount, a hot reload) are
  `"render"` steps.
- **A task you start yourself** with `create_task()`: it inherits the context
  variables of the turn but is not a sync step. Handlers run inside an `"event"`
  step, so what they call directly is covered; a task they start is not.

### Not wrapped: template-time form and tag hooks

The `"render"` step covers the call to `get_context_data()`. The template engine
runs **after** it returns, and nothing around the template engine is a step. Any
hook that a template tag, a filter or a component's `{{ component }}` calls therefore
runs with **no step open and no wrapper scope**, on every transport. Checked by
running each one with a recording wrapper (`test_sync_turn_inside_audit_3646.py`):

| Hook | Called by | Step open |
|---|---|---|
| `FormMixin.as_live()` / `as_live_field()` | `{% live_form %}` / `{% live_field %}` | none |
| `get_field_value()` (including `AdminFormMixin`) | the `field_value` filter | none (it also runs under `"render"` when an admin view's own `get_context_data()` calls it) |
| `_ensure_form_state()` | the same tags and filters | none (under `"event"` when `submit_form()` calls it) |
| form adapters' `render_field()` and `_merge_widget_attrs()` | `as_live_field()` | none |
| `Component.get_context_data()` and `_render_custom()` | `{{ component }}` | none |
| `_on_sticky_update()` | `{% live_render ... sticky=True %}` while the parent renders | none |

**A row read in one of these runs without the wrapper scope.** The clearest case
is a `ModelChoiceField`: `{% live_field view "group" %}` evaluates the field's
queryset while it renders the `<select>`, and a test shows that read happens with no
step open. Under row-level security that read has no per-turn scope, whatever your
wrapper does for `get_context_data()`. Read the rows in `mount()`, a handler or
`get_context_data()` (all steps) and hand the template the result, rather than
letting a tag or a field evaluate a queryset.

The snapshot-restore overrides are covered: `_should_restore_snapshot()`,
`_restore_snapshot()` and `_capture_snapshot_state()` are `"lifecycle"` steps (the
per-event refresh of the signed snapshot as well as the mount), and
`handle_restore()` is a `"hook"` step. A `{% live_render lazy=True %}` child's
`mount()` and `get_context_data()` are `"mount"` and `"render"` steps when its slot
is filled. `PresenceMixin` entering its broadcast from
async code (`track_presence()` under a running loop) is a `"hook"` step as well.

## Hot path

Turn middleware runs on **every** event, tick and push. Keep it cheap:

- Cache anything that does I/O (a backend session check, a permission lookup).
  A ticking view runs the chain every tick.
- A server turn may run while the session's render lock is held; a slow
  middleware delays that session's other turns.
- The chain itself is cheap: about 2 µs per turn with no `LIVEVIEW_MIDDLEWARE`
  (the built-ins plus timing), and about 0.5 µs more per pass-through
  middleware (measured on an Apple-silicon laptop, Python 3.12). HTTP and API
  requests skip the chain entirely when `LIVEVIEW_MIDDLEWARE` is empty; with it
  set, they pay one event-loop hop per request.

Per-turn timings (the whole chain plus the turn) are recorded alongside handler
timings. In development, read them from
`/_djust/observability/turn_timings/` (`?kind=push` filters), or use
`turn.started` for your own spans.

## Built-in behaviour on the chain

- **`reauth_on_event`** runs as the `reauth` built-in for legacy-exposure views
  with `login_required` / `permission_required`: client turns re-check every
  turn, server turns at most every `reauth_server_turn_interval` seconds.
  `url_change` and background results are now re-checked too.
- **Errors**: `PermissionDenied` anywhere in a turn becomes a deny; any other
  exception escaping a socket client turn is answered with the value-free error
  envelope. Server turns keep their own error handling.
- **Rate limiting stays at transport ingress**: every WebSocket and SSE frame is
  charged when it arrives, before it can queue behind a busy view.
  `Refuse.rate_limited()` lets your middleware add per-caller limits.

## Testing your middleware

`djust.turns.run_turn` runs a turn without a transport:

```python
from djust.turns import Turn, run_turn

async def test_anonymous_callers_are_sent_to_login():
    async def body(turn):
        raise AssertionError("must not run")

    refusal = await run_turn(Turn("event", "ws", view=MyView()), body)
    assert refusal.kind == "login"
```

## See also

- [on_mount hooks](on-mount-hooks.md): mount-only logic per view
- [Authentication](authentication.md): `login_required`, `reauth_on_event`
- [ADR-044](https://github.com/djust-org/djust/blob/main/docs/adr/044-turn-middleware.md)
