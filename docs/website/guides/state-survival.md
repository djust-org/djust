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
  `handle_restore()` then run, and what a handler set goes back over
  `handle_params()`'s defaults. So what `mount()` computed at the page load (a
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
| `mount()` | n/a (runs once, when the page's view is built) | not run again: the view is rebuilt from its saved state<br><sub>`test_http_fallback_alternating_processes`</sub> | runs on the new process, then the saved state is put back over it<br><sub>`test_ws_reconnect_to_another_process`</sub> | n/a (same mount path as the reconnect to another process) | Put what must be read again on every request in `handle_restore()` or `get_context_data()`, not only in `mount()`. |
| `handle_restore()` hook | n/a (no remount, so nothing to restore) | called on every request<br><sub>`test_http_fallback_alternating_processes`</sub> | called after the restore<br><sub>`test_ws_reconnect_to_another_process`</sub> | called after the restore<br><sub>`test_kill_after_save`</sub> | Use it to reload what the persisted fields point at. |
| Render baseline (what the next update sends) | patches<br><sub>`test_ws_events_on_one_process`</sub> | full HTML on every request<br><sub>`test_http_fallback_alternating_processes`</sub> | full HTML in the mount, full HTML for the first event, then patches<br><sub>`test_ws_reconnect_to_another_process`</sub> | full HTML in the mount, full HTML for the first event, then patches<br><sub>`test_kill_after_save`</sub> | Nothing: the framework re-sends the page. |
| URL kwargs and the query string (`handle_params`) | kept<br><sub>`test_url_kwargs_and_handle_params`</sub> | read from the request; persisted fields restored<br><sub>`test_url_kwargs_and_handle_params`</sub> | read from the new URL (it wins); persisted fields restored<br><sub>`test_url_kwargs_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the URL is read again; it wins over a persisted field of the same name. |
| A model instance `mount()` loads and keeps (`self.obj = Model.objects.get(...)`) | the instance<br><sub>`test_held_model_and_handle_params`</sub> | a dict of its fields: the view is rebuilt from saved state, `mount()` does not run<br><sub>`test_held_model_and_handle_params`</sub> | the instance `mount()` loaded on the new process<br><sub>`test_held_model_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Over the HTTP fallback read it again in `handle_restore()`: it comes back as a dict of its fields, as in 1.3. |
| A value a handler set that `handle_params()` would default | kept<br><sub>`test_held_model_and_handle_params`</sub> | kept<br><sub>`test_held_model_and_handle_params`</sub> | kept<br><sub>`test_held_model_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the saved value goes back after `handle_params()`. |
| The URL carries a value (`?tab=z`) for an undeclared attribute a handler also set | n/a (no mount, so `handle_params()` does not run again) | the handler's saved value wins<br><sub>`test_held_model_and_handle_params`</sub> | the handler's saved value wins<br><sub>`test_held_model_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Declare it `state(..., persist="server")` if the URL must win: for a declared field `handle_params()` has the last word. |
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
writing process's clock, and a reader refuses one written "in the future" by
its own clock. A process whose clock is behind the writer's therefore discards
fresh state and mounts the view from its defaults, without an error.

## Session engines

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
| `cache`, `LocMemCache` (one per process) | not kept: each process holds its own sessions, so each request starts over **(known gap)**<br><sub>`test_session_engine_across_processes`</sub> | mount fails with an error **(known gap)**<br><sub>`test_session_engine_across_processes`</sub> | Never with more than one process. |
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
