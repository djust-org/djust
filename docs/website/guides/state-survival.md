---
title: "What Survives a Reconnect, a Restart and a Second Process"
slug: state-survival
section: guides
order: 13.45
level: advanced
description: "A tested table of what a djust 2.0 view keeps when the next request or the reconnecting socket lands on another process, when a process is killed, and what the app author has to do for each kind of state."
---

# What survives a reconnect, a restart and a second process

A djust app can run as several processes on several servers, and any request
or reconnect may land on a process that has never seen the user before. A load
balancer without session affinity does exactly that. This page says, for each
kind of state, what the next process sees.

Every outcome below is measured by a test that starts **two real server
processes** (uvicorn) sharing one session store and one channel layer, and
sends each request or socket to the process it chooses. The test that proves a
cell is named under it, in `tests/integration/multinode/test_state_survival.py`.
A test that sees something else fails, and so does a table that says something
its test does not check; the tables below are generated from the tests'
metadata (`tests/integration/multinode/survival_table.py`).

Cells marked **(known gap)** describe what happens today where an app author
could reasonably expect otherwise. They are pinned as they are, so a fix shows
up as a failing test and a table update.

## The rule

What a view keeps across processes is exactly what went into the shared
session:

- **The view's state** is saved in the Django session after the page load and
  after each event that changed it, as in 1.x: its public attributes, the
  `_private` attributes `mount()` created, its `state()` fields and the state
  of the components it holds, each value with its Python type. It is bound to
  the session, the user, the tenant and the route. Saving exposes nothing:
  rendering and client data stay with `state()` declarations and
  `get_context_data()`.
- **One saved state per tab.** Each page load is given a tab id, carried in the
  page; its socket and its fallback requests send it back. Two tabs on one
  route do not share state, a reload starts a new tab from `mount()`, and a
  duplicated browser tab shares its original's state until its next page load.
  A request with no tab id, or a malformed one, saves and restores nothing.
- **How tab states are bounded.** At every save the session drops expired
  states, keeps at most 8 tab states per view and route and no more of them
  than fit together in that view's size limit (512 KB by default, so reloading
  a large page does not multiply it), and keeps all saved view states of the
  session together under 1 MB; the oldest go first and the state just written
  is always kept. A tab whose state was evicted starts from `mount()` again,
  with a WARNING on the `djust.automatic_state` logger.
- **Over the HTTP fallback** a later request rebuilds the view from its saved
  state and does not run `mount()` again; `handle_params()` and
  `handle_restore()` then run.
- **The URL and the saved state.** The state is saved with the query string
  the view last read. While a request or reconnect comes with that same query
  (compared as parsed parameters, so order and encoding do not matter; a
  repeated key such as `?a=1&a=2` counts by its last value, the one reading the
  socket and HTTP share), the saved state wins over what `handle_params()`
  assigns: a tab a handler set survives the hook's default. When the query changed, `handle_params()` has
  the last word for everything it assigns (`?page=3` shows page 3, and a value
  it defaults is defaulted), and the rest of the saved state is kept. A
  handler's `live_patch(params=...)` moves the saved URL with it. A declared
  `persist="server"` field follows the same rule as the rest of the state. So what `mount()` computed at the page load (a
  permission flag, a list of rows) continues as saved until the next page
  load, and a model instance `mount()` loaded is a dict of its fields there, as
  in 1.3: read it again in `handle_restore()`.
- **Over a socket**, a mount or reconnect runs `mount()` and then puts back
  only what a handler (or a later step such as a background result or a tick)
  changed since `mount()`, and removes again what a handler deleted. Everything `mount()` computes is fresh on every
  socket mount: a permission flag follows the user, and a model instance stays
  an instance.
- The two paths differ only for values `mount()` computes and no handler
  changes: fresh on a socket mount, as saved on the fallback. Put authorization
  in the framework's gates, which run on every request on both, and read data
  that must be current in `handle_restore()` or `get_context_data()`.
- A value that cannot be saved (a queryset, the request's user, an arbitrary
  object, or a callable such as a class, a `functools.partial` or an object
  with `__call__`) is skipped, never turned into a string; a WARNING names it once, and
  the next fallback request runs `mount()` again to re-create it before the
  saved state is put back.
- `state(..., persist="server")` is the strict form (an unsaveable value is an
  error) and is kept per route, shared by its tabs;
  `state(..., persist="client", client=True)` fields travel in a signed token
  the browser holds; `state(..., persist=False)` is never saved.
- Not saved: the render baseline, background work, timers, and the open state
  of ADR-034 interactive components. These live in the process that holds the
  socket.
- Past 512 KB of saved state per view the event's change is refused whole;
  state is never saved in part.

So a multi-process deployment needs a session engine every process shares and
a channel layer every process shares. See
[Scaling djust](scaling.md#option-b-redis-between-processes) for the settings.

## Kinds of state

<!-- BEGIN GENERATED survival-state: python -m tests.integration.multinode.survival_table --write -->
|  | Same process, next event | HTTP fallback request on another process | WebSocket reconnect to another process | Process killed (SIGKILL), reconnect to another | What the app author must do |
|---|---|---|---|---|---|
| `state(..., persist="server")` field | kept<br><sub>`test_ws_events_on_one_process`</sub> | kept (1, 2, 3 across A, B, A)<br><sub>`test_http_fallback_alternating_processes`</sub> | restored<br><sub>`test_ws_reconnect_to_another_process`</sub> | restored (last completed save)<br><sub>`test_kill_after_save`</sub> | Declare it `persist="server"`; use a session engine every process shares. |
| `state(..., persist="client", client=True)` field | kept<br><sub>`test_ws_events_on_one_process`</sub> | lost: every request starts from the default<br><sub>`test_http_fallback_alternating_processes`</sub> | restored from the browser's signed token<br><sub>`test_ws_reconnect_to_another_process`</sub> | restored from the browser's signed token<br><sub>`test_kill_after_save`</sub> | Nothing more over a WebSocket. Over the HTTP fallback it is not kept: use `persist="server"` for a value that must be. |
| `state()` without `persist` | kept<br><sub>`test_ws_events_on_one_process`</sub> | kept (1, 2, 3 across A, B, A)<br><sub>`test_http_fallback_alternating_processes`</sub> | restored<br><sub>`test_ws_reconnect_to_another_process`</sub> | restored (last completed save)<br><sub>`test_kill_after_save`</sub> | Nothing: it is saved automatically. Use a session engine every process shares. |
| Ordinary attribute set in `mount()` or a handler, public or `_private` | kept<br><sub>`test_ws_events_on_one_process`</sub> | kept (1, 2, 3 across A, B, A)<br><sub>`test_http_fallback_alternating_processes`</sub> | restored<br><sub>`test_ws_reconnect_to_another_process`</sub> | restored (last completed save)<br><sub>`test_kill_after_save`</sub> | Nothing: it is saved automatically, and is not rendered unless declared. |
| `state(..., persist=False)` field | kept<br><sub>`test_ws_events_on_one_process`</sub> | lost: every request starts from the default<br><sub>`test_http_fallback_alternating_processes`</sub> | lost: the default<br><sub>`test_ws_reconnect_to_another_process`</sub> | lost: the default<br><sub>`test_kill_after_save`</sub> | Nothing: you asked for it not to be kept. |
| `state()` without `persist`, or an ordinary attribute, on a view with saving switched off (`automatic_state = False`, or `DJUST_AUTOMATIC_STATE = False` for the project) | kept<br><sub>`test_saving_switched_off`</sub> | not kept: every request runs `mount()` again (a `persist="server"` field is kept)<br><sub>`test_saving_switched_off`</sub> | not kept: starts from `mount()` (a `persist="server"` field is restored)<br><sub>`test_saving_switched_off`</sub> | n/a (same mount path as the reconnect to another process) | Nothing is saved, by your choice: `mount()` builds the view again. Declare a field `persist="server"` to keep it anyway. |
| `state()` without `persist`, or an ordinary attribute, with the sealed state store (`DJUST_STATE_STORE = "djust.state_store.SealedSessionViewStateStore"`) | kept<br><sub>`test_state_store`</sub> | kept (1, 2, 3 across A, B, A); the session holds no view content<br><sub>`test_state_store`</sub> | restored; the session holds no view content<br><sub>`test_state_store`</sub> | n/a (same mount path as the reconnect to another process) | Give every process the same `DJUST_STATE_STORE_KEYS`. A field declared `persist="server"` is not sealed: it stays in the session as before. |
| `state()` without `persist`, or an ordinary attribute, with a state store outside the session (`DJUST_STATE_STORE` naming your own `ViewStateStore`) | kept<br><sub>`test_state_store`</sub> | kept (1, 2, 3 across A, B, A); the session holds no view content<br><sub>`test_state_store`</sub> | restored; the session holds no view content<br><sub>`test_state_store`</sub> | n/a (same mount path as the reconnect to another process) | Use a store every process reaches, as the session engine must be. |
| `mount()` | n/a (runs once, when the page's view is built) | not run again: the view is rebuilt from its saved state<br><sub>`test_http_fallback_alternating_processes`</sub> | runs on the new process, then the saved state is put back over it<br><sub>`test_ws_reconnect_to_another_process`</sub> | n/a (same mount path as the reconnect to another process) | Put what must be read again on every request in `handle_restore()` or `get_context_data()`, not only in `mount()`. |
| `handle_restore()` hook | n/a (no remount, so nothing to restore) | called on every request<br><sub>`test_http_fallback_alternating_processes`</sub> | called after the restore<br><sub>`test_ws_reconnect_to_another_process`</sub> | called after the restore<br><sub>`test_kill_after_save`</sub> | Use it to reload what the persisted fields point at. |
| Render baseline (what the next update sends) | patches<br><sub>`test_ws_events_on_one_process`</sub> | full HTML on every request<br><sub>`test_http_fallback_alternating_processes`</sub> | full HTML in the mount, full HTML for the first event, then patches<br><sub>`test_ws_reconnect_to_another_process`</sub> | full HTML in the mount, full HTML for the first event, then patches<br><sub>`test_kill_after_save`</sub> | Nothing: the framework re-sends the page. |
| URL kwargs and the query string (`handle_params`) | kept<br><sub>`test_url_kwargs_and_handle_params`</sub> | read from the request; persisted fields restored<br><sub>`test_url_kwargs_and_handle_params`</sub> | read from the new URL (it wins); persisted fields restored<br><sub>`test_url_kwargs_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the URL is read again; it wins over a persisted field of the same name. |
| A model instance `mount()` loads and keeps (`self.obj = Model.objects.get(...)`) | the instance<br><sub>`test_held_model_and_handle_params`</sub> | a dict of its fields: the view is rebuilt from saved state, `mount()` does not run<br><sub>`test_held_model_and_handle_params`</sub> | the instance `mount()` loaded on the new process<br><sub>`test_held_model_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Over the HTTP fallback read it again in `handle_restore()`: it comes back as a dict of its fields, as in 1.3. |
| A value a handler set that `handle_params()` would default | kept<br><sub>`test_held_model_and_handle_params`</sub> | kept<br><sub>`test_held_model_and_handle_params`</sub> | kept<br><sub>`test_held_model_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the saved value goes back after `handle_params()`. |
| The URL carries a value (`?tab=z`) for an undeclared attribute a handler also set | n/a (no mount, so `handle_params()` does not run again) | the URL wins when it changed; the same URL keeps the handler's value<br><sub>`test_held_model_and_handle_params`</sub> | the URL wins when it changed; the same URL keeps the handler's value<br><sub>`test_held_model_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: `handle_params()` has the last word when the URL changed since the state was saved; while it is the same URL, the handler's value is kept. |
| `FormMixin` input and errors, mid-validation | kept<br><sub>`test_form_mid_validation`</sub> | input and errors restored<br><sub>`test_form_mid_validation`</sub> | input and errors restored<br><sub>`test_form_mid_validation`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: input and errors are saved, except password and other sensitive fields. |
| `WizardMixin` step and answers | kept<br><sub>`test_wizard_step`</sub> | kept: the step and the answers<br><sub>`test_wizard_step`</sub> | restored: the step and the answers<br><sub>`test_wizard_step`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the step and the answers are saved. |
| `exposure_policy = "legacy"` view attribute | kept<br><sub>`test_legacy_view`</sub> | lost: every request runs `mount()`<br><sub>`test_legacy_view`</sub> | lost: `mount()` value<br><sub>`test_legacy_view`</sub> | n/a (same mount path as the reconnect to another process) | Migrate the view to explicit exposure: a legacy view's state is not saved. |
| `LiveComponent` state | kept<br><sub>`test_components`</sub> | kept: the event reaches the component and its state carries<br><sub>`test_components`</sub> | restored<br><sub>`test_components`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: its state is saved with the view's. |
| Interactive component (ADR-034 `DropdownMenu`) open state | kept<br><sub>`test_components`</sub> | event refused (Component not found)<br><sub>`test_components`</sub> | lost: closed, with a new component id<br><sub>`test_components`</sub> | n/a (same mount path as the reconnect to another process) | Nothing; a reconnect closes it. No HTTP-fallback support yet (#4046). |
| `start_async()` work in flight | result delivered<br><sub>`test_async_work_in_flight`</sub> | n/a (the HTTP fallback waits for the work inside the request) | result lost; a persisted in-progress flag stays set<br><sub>`test_async_work_in_flight`</sub> | n/a (the work dies with its process; same as the reconnect) | Clear a persisted in-progress flag in `handle_restore()`; restart the work. |
| `tick_interval` / `handle_tick()` | keeps ticking<br><sub>`test_tick`</sub> | n/a (ticks run only on a connection) | restarts on the new process; the counters continue<br><sub>`test_tick`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: a counter the tick changes is saved like any other state. |
| Rows added with `stream_insert()` after mount | kept<br><sub>`test_stream_rows`</sub> | n/a (not measured) | lost: the mount renders only `mount()`'s rows<br><sub>`test_stream_rows`</sub> | n/a (same mount path as the reconnect to another process) | Load the rows in `mount()` from storage. |
| An event whose save was still being written | n/a (the event's own answer waits for its save) | n/a (not measured) | n/a (see the restart column) | event lost; the previous saved state restored<br><sub>`test_kill_while_save_held`</sub> | Nothing; the user's last shown state comes back, the event is lost. |
<!-- END GENERATED survival-state -->

The HTTP fallback builds a new view for every request from the saved state
(running `mount()` only when nothing is saved or something could not be
saved), then `handle_params()` and `handle_restore()`, then the event.

## Sessions, sockets and messages

<!-- BEGIN GENERATED survival-sessions: python -m tests.integration.multinode.survival_table --write -->
|  | Same process | Other process | What the app author must do |
|---|---|---|---|
| Log in through one process, then use another | authenticated<br><sub>`test_login_then_other_process`</sub> | authenticated (HTTP and WebSocket)<br><sub>`test_login_then_other_process`</sub> | Use a session engine every process shares. |
| Log out through one process while a socket is open on another | n/a (Django's own logout) | next event refused, socket closed 4403<br><sub>`test_logout_while_socket_open_elsewhere`</sub> | Nothing: the open socket refuses its next event. |
| Same session, another tenant | n/a (same binding rule; measured across processes) | other tenant mounts fresh; own tenant restored<br><sub>`test_tenant_scoping_across_processes`</sub> | Nothing: persisted state is bound to the tenant. |
| Two tabs of one session on the same route | independent; a `persist="server"` field is still shared (last write wins)<br><sub>`test_two_sockets_same_route`</sub> | independent; a `persist="server"` field is still shared (last write wins)<br><sub>`test_two_sockets_same_route`</sub> | Nothing for the view's own state: each page load is its own tab. A `persist="server"` field is per route: keep shared data in your own storage. |
| Two sockets of one session on different routes | n/a (same binding rule; measured across processes) | independent<br><sub>`test_two_sockets_other_route`</sub> | Nothing: state is per route. |
| `push_to_view()` with a Redis channel layer | n/a (measured across processes only) | delivered<br><sub>`test_push_to_view_across_processes`</sub> | Use a channel layer every process shares. |
| `push_to_view()` with an in-memory channel layer | delivered<br><sub>`test_push_with_in_memory_layer`</sub> | not delivered **(known gap)**<br><sub>`test_push_with_in_memory_layer`</sub> | Do not run more than one process with it. |
| Presence (Redis backend and channel layer) | n/a (measured across processes only) | join and leave seen by the other process<br><sub>`test_presence_across_processes`</sub> | Set `PRESENCE_BACKEND = "redis"` and a shared channel layer. |
| Presence (memory backend, in-memory channel layer) | n/a (measured across processes only) | each process counts only its own **(known gap)**<br><sub>`test_presence_with_memory_backend`</sub> | Do not run more than one process with it. |
| Persisted state read by a process whose clock is behind the writer's | n/a (one clock) | discarded: the view mounts fresh **(known gap)**<br><sub>`test_clock_skew_between_processes`</sub> | Keep the processes' clocks in sync. |
<!-- END GENERATED survival-sessions -->

Two tabs of one session on one route each have their own saved state, keyed
by the tab id their page load was given. A field declared `persist="server"`
is the exception: it has one envelope per route, and a tab whose copy is older
than what the other saved overwrites it.

The persisted envelope records when it was written, in whole seconds of the
writing process's clock. A reader accepts one written up to 30 seconds "in the
future" by its own clock (`CLOCK_SKEW_TOLERANCE` in `djust._exposure_sessions`),
so small differences between servers' clocks do not lose state. A process whose
clock is further behind the writer's discards the state, mounts the view from
its defaults and logs a warning naming the skew; keep the clocks in sync.

With `DEBUG = False`, system checks [`djust.C032`](error-codes.md#c032-in-memory-channel-layer)
(an in-memory channel layer) and [`djust.C033`](error-codes.md#c033-presence-counted-per-process)
(memory presence used by a view) warn about the two rows above that cover only
one process.

## Keeping saved state out of the plain session

The rows above for the sealed state store and for a state store outside the
session show the same survival with `DJUST_STATE_STORE` set: the session then
holds an index entry with no values, and the state itself is sealed, or held
by your own store. See [The state store](../state/state-store.md).

## Switching saving off

Saving is on by default. Two switches turn it off without changing what a view
exposes:

```python
# settings.py: nothing of any view is saved between requests
DJUST_AUTOMATIC_STATE = False
```

```python
class ReportView(LiveView):
    automatic_state = False  # this view saves nothing, whatever the project says
```

A view may also say `automatic_state = True` in a project that set
`DJUST_AUTOMATIC_STATE = False`: the view's own value wins in both directions,
and a subclass takes its base class's value unless it states its own. The
switch is coarse on purpose: it covers the whole view, so no attribute has to
be found and excluded one at a time.

With saving off, nothing of the view is written to the session on any
transport: not its public or `_private` attributes, not its plain `state()`
fields, not its components' state, not the record of what a handler changed,
and no empty entry at the page load. What follows:

- over the page-POST fallback every request runs `mount()` again, then
  `handle_params()` and `handle_restore()`, then the event;
- a socket reconnect starts from `mount()`; while the socket is open the view
  object lives on and keeps everything, as always;
- no size limit applies and nothing is reported as unsaveable;
- state saved before the switch was turned off is not read. The first request
  or reconnect of a tab removes that tab's saved state from the session; what
  other tabs left stays until its session expires, or until
  `djust clear --all` (on the `db` and `cached_db` engines).

The switch covers every road by which a view's content reaches the session:

- the page's own state (above);
- a **lazy legacy child** (`dj-lazy` under a legacy page), whose state the
  fallback kept in the session: it is saved only while saving is on for the
  page AND for the child. A child cannot switch saving on under a page that
  switched it off;
- an **embedded child** (`{% live_render %}`, sticky or not) never has its own
  state saved automatically, whatever its `automatic_state` says. Its declared
  `persist="server"` fields are kept, as the page's are;
- form input and validation errors (`FormMixin`) are part of the page's state:
  with saving off they are in the answer and not in the session.

What djust still writes with saving off: fields declared `persist="server"`
and their index (`_djust_explicit_...`), and one random token that binds lazy
children to the session (`_djust_lazy_binding`). Neither holds view content
you did not declare.

**A field declared `state(..., persist="server")` is still kept.** The
developer asked for exactly that field, by name, so the switch does not
override it. To have no view content in the session at all, set the switch and
declare no field `persist="server"`.

Only `True` and `False` are accepted (`None` on a view means the project's
value). Any other value is read as off, so a mistake never saves state nobody
asked for, and system check `djust.C035` reports it. The setting is read on
each request and the view's attribute from its class, never from the instance:
`self.automatic_state = False` in `mount()` does nothing, and a WARNING on the
`djust.automatic_state` logger says so once per view class.
`DJUST_AUTOMATIC_STATE` is a top-level Django setting. Inside `LIVEVIEW_CONFIG`
or `DJUST_CONFIG` (as `automatic_state` or `DJUST_AUTOMATIC_STATE`) it is
refused at startup instead of being ignored.

## Every page load creates a session

A LiveView page GET saves the view's state, so it creates a session when the
visitor has none and the response sets the session cookie, as in 1.x. Two things follow:

- **Caching.** A response that sets a session cookie carries `Vary: Cookie`
  and must not be stored by a shared cache or CDN. Cache the static assets;
  serve a page that has to be cached for everyone as a plain Django view.
- **Cookie consent.** Every visitor to a LiveView page gets the session cookie
  on the first load, before any interaction. It holds only the session id and
  exists to make the page work; whether that needs consent or a notice where
  you operate is for you to decide.

## Session engines

Saved state needs one of Django's server-side engines (`db`, `cached_db`,
`cache`, `file`) or an engine whose `SessionStore` subclasses one of theirs (a
project's own db store with another expiry or an extra column). With the
signed-cookies engine, or a store built on none of them, nothing is saved and
system check `djust.C034` says so at startup; a field declared
`persist="server"` is refused there (`djust.C030`).

Each engine below was run with two processes. The persisted-state machinery
accepts every server-side engine, including ones that are local to a process.
With `DEBUG = False`, system check [`djust.C031`](error-codes.md#c031-session-store-local-to-one-process)
warns about a `cache` or `cached_db` engine over `LocMemCache`; whether a `file`
engine's directory is shared cannot be checked.

<!-- BEGIN GENERATED survival-engines: python -m tests.integration.multinode.survival_table --write -->
|  | HTTP fallback on another process | WebSocket reconnect to another process | What the app author must do |
|---|---|---|---|
| `db` (a database both processes use) | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Supported. |
| `file` (a directory both processes use) | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Only on storage every process shares. |
| `cache`, `FileBasedCache` in a shared directory | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Only with a cache every process shares. |
| `cache`, Django's `RedisCache` | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | A Redis with persistence. |
| `cached_db`, `RedisCache` plus the database | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Supported. |
| `cache`, `LocMemCache` (one per process) | not kept: each process holds its own sessions, so each request starts over **(known gap)**<br><sub>`test_session_engine_across_processes`</sub> | the view mounts fresh: this process does not hold the session (#4102) **(known gap)**<br><sub>`test_session_engine_across_processes`</sub> | Never with more than one process. |
<!-- END GENERATED survival-engines -->

## Session reads and writes per event

Counted, not timed, on the database engine: Django's session `load()` and
`save()` calls, and the SQL statements on `django_session`, for one event on a
view with persisted state (after its first event).

<!-- BEGIN GENERATED survival-cost: python -m tests.integration.multinode.survival_table --write -->
|  | Session loads | Session saves | `SELECT`s on `django_session` | `UPDATE`s on `django_session` |
|---|---|---|---|---|
| WebSocket event on a mounted view | 1<br><sub>`test_session_cost_per_event`</sub> | 1<br><sub>`test_session_cost_per_event`</sub> | 2<br><sub>`test_session_cost_per_event`</sub> | 1<br><sub>`test_session_cost_per_event`</sub> |
| HTTP fallback event (page POST) | 1<br><sub>`test_session_cost_per_event`</sub> | 2<br><sub>`test_session_cost_per_event`</sub> | 1<br><sub>`test_session_cost_per_event`</sub> | 2<br><sub>`test_session_cost_per_event`</sub> |
<!-- END GENERATED survival-cost -->

## Not covered here

These are not measured by the tests above, so the tables make no claim about
them: uploads in progress (the [Uploads](uploads.md) guide says the client
registers them again after a reconnect), embedded and sticky child views (see
[Sticky-Child State Persistence](sticky-child-persistence.md)), the SSE
transport, and Redis presence under failover (see
[Scaling djust](scaling.md#several-pods-option-b-measured)).

## Running the tests

```bash
.venv/bin/python -m pytest tests/integration/multinode/ -n 2
```

They start their own processes and, when `redis-server` and `channels_redis`
are installed, their own Redis on a Unix socket; without those the Redis rows
are skipped. They are in the slow tier: CI runs them on pushes to `main` and
nightly, not on every pull request.
