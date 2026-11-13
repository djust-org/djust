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

- **What the view sets on `self`** is saved in the Django session after each
  event that changed it: public attributes and `state()` fields whose values
  are plain data (`None`, booleans, numbers, strings, lists, dicts). It is
  bound to the session, the user, the tenant and the route, and is put back
  after `mount()` on whichever process mounts the view next. Saving exposes
  nothing: rendering and client data stay with `state()` declarations and
  `get_context_data()`.
- `state(..., persist="server")` is the strict form: a value that is not plain
  data, or does not fit, is an error instead of being left out.
- `state(..., persist="client", client=True)` fields travel in a signed token
  the browser holds and sends back when its socket reconnects. The HTTP
  fallback does not send it.
- Not saved: names with a leading underscore, `state(..., persist=False)`,
  values that are not plain data (model instances, querysets, `Decimal`,
  dates, the request, the user), components, the render baseline, background
  work and timers. These live in the process that holds the socket; a move to
  another process runs `mount()` again. A value that is skipped is named once
  on the `djust.automatic_state` logger.
- A full page load is a fresh `mount()` and discards the route's saved state.
  State written by different code of the view class (after a deploy that
  changed it) is discarded too.
- An `exposure_policy = "legacy"` view saves the same way over the HTTP
  fallback; over a socket it keeps its state in the process only.

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
| Ordinary public attribute holding plain data | kept<br><sub>`test_ws_events_on_one_process`</sub> | kept (1, 2, 3 across A, B, A)<br><sub>`test_http_fallback_alternating_processes`</sub> | restored<br><sub>`test_ws_reconnect_to_another_process`</sub> | restored (last completed save)<br><sub>`test_kill_after_save`</sub> | Nothing: it is saved automatically, and is not rendered unless declared. |
| `_private` attribute, `state(persist=False)`, or a value that is not plain data | kept<br><sub>`test_ws_events_on_one_process`</sub> | lost: every request runs `mount()`<br><sub>`test_http_fallback_alternating_processes`</sub> | lost: `mount()` value<br><sub>`test_ws_reconnect_to_another_process`</sub> | lost: `mount()` value<br><sub>`test_kill_after_save`</sub> | Rebuild it in `mount()` or `handle_restore()`; drop the underscore if it must survive. |
| `handle_restore()` hook | n/a (no remount, so nothing to restore) | called on every request<br><sub>`test_http_fallback_alternating_processes`</sub> | called after the restore<br><sub>`test_ws_reconnect_to_another_process`</sub> | called after the restore<br><sub>`test_kill_after_save`</sub> | Use it to reload what the persisted fields point at. |
| Render baseline (what the next update sends) | patches<br><sub>`test_ws_events_on_one_process`</sub> | full HTML on every request<br><sub>`test_http_fallback_alternating_processes`</sub> | full HTML in the mount, full HTML for the first event, then patches<br><sub>`test_ws_reconnect_to_another_process`</sub> | full HTML in the mount, full HTML for the first event, then patches<br><sub>`test_kill_after_save`</sub> | Nothing: the framework re-sends the page. |
| URL kwargs and the query string (`handle_params`) | kept<br><sub>`test_url_kwargs_and_handle_params`</sub> | read from the request; persisted fields restored<br><sub>`test_url_kwargs_and_handle_params`</sub> | read from the new URL (it wins); persisted fields restored<br><sub>`test_url_kwargs_and_handle_params`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the URL is read again; it wins over a persisted field of the same name. |
| `FormMixin` input and errors, mid-validation | kept<br><sub>`test_form_mid_validation`</sub> | input and errors restored<br><sub>`test_form_mid_validation`</sub> | input and errors restored<br><sub>`test_form_mid_validation`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: input and errors are saved, except password and other sensitive fields. |
| `WizardMixin` step and answers | kept<br><sub>`test_wizard_step`</sub> | kept: the step and the answers<br><sub>`test_wizard_step`</sub> | restored: the step and the answers<br><sub>`test_wizard_step`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: the step and the answers are saved. |
| `exposure_policy = "legacy"` view attribute | kept<br><sub>`test_legacy_view`</sub> | kept (1, 2 across A, B)<br><sub>`test_legacy_view`</sub> | lost: `mount()` value<br><sub>`test_legacy_view`</sub> | n/a (same mount path as the reconnect to another process) | Nothing over the HTTP fallback. Over a socket it lives in the process: migrate the view to explicit exposure. |
| `LiveComponent` state | kept<br><sub>`test_components`</sub> | event refused (Component not found)<br><sub>`test_components`</sub> | lost: mounts fresh<br><sub>`test_components`</sub> | n/a (same mount path as the reconnect to another process) | Persist the values on the view and build the component from them. |
| Interactive component (ADR-034 `DropdownMenu`) open state | kept<br><sub>`test_components`</sub> | event refused (Component not found)<br><sub>`test_components`</sub> | lost: closed, with a new component id<br><sub>`test_components`</sub> | n/a (same mount path as the reconnect to another process) | Nothing; a reconnect closes it. No HTTP-fallback support yet (#4046). |
| `start_async()` work in flight | result delivered<br><sub>`test_async_work_in_flight`</sub> | n/a (the HTTP fallback waits for the work inside the request) | result lost; a persisted in-progress flag stays set<br><sub>`test_async_work_in_flight`</sub> | n/a (the work dies with its process; same as the reconnect) | Clear a persisted in-progress flag in `handle_restore()`; restart the work. |
| `tick_interval` / `handle_tick()` | keeps ticking<br><sub>`test_tick`</sub> | n/a (ticks run only on a connection) | restarts on the new process; the counters continue<br><sub>`test_tick`</sub> | n/a (same mount path as the reconnect to another process) | Nothing: a counter the tick changes is saved like any other state. |
| Rows added with `stream_insert()` after mount | kept<br><sub>`test_stream_rows`</sub> | n/a (not measured) | lost: the mount renders only `mount()`'s rows<br><sub>`test_stream_rows`</sub> | n/a (same mount path as the reconnect to another process) | Load the rows in `mount()` from storage. |
| An event whose save was still being written | n/a (the event's own answer waits for its save) | n/a (not measured) | n/a (see the restart column) | event lost; the previous saved state restored<br><sub>`test_kill_while_save_held`</sub> | Nothing; the user's last shown state comes back, the event is lost. |
<!-- END GENERATED survival-state -->

The HTTP fallback builds a new view for every request: `mount()`, then the
saved state, then `handle_params()` and `handle_restore()`, then the event.
What is saved carries from one request to the next, on the same process or
another.

## Sessions, sockets and messages

<!-- BEGIN GENERATED survival-sessions: python -m tests.integration.multinode.survival_table --write -->
|  | Same process | Other process | What the app author must do |
|---|---|---|---|
| Log in through one process, then use another | authenticated<br><sub>`test_login_then_other_process`</sub> | authenticated (HTTP and WebSocket)<br><sub>`test_login_then_other_process`</sub> | Use a session engine every process shares. |
| Log out through one process while a socket is open on another | n/a (Django's own logout) | next event refused, socket closed 4403<br><sub>`test_logout_while_socket_open_elsewhere`</sub> | Nothing: the open socket refuses its next event. |
| Same session, another tenant | n/a (same binding rule; measured across processes) | other tenant mounts fresh; own tenant restored<br><sub>`test_tenant_scoping_across_processes`</sub> | Nothing: persisted state is bound to the tenant. |
| Two sockets of one session on the same route | last write wins (a stale socket overwrites)<br><sub>`test_two_sockets_same_route`</sub> | last write wins (a stale socket overwrites)<br><sub>`test_two_sockets_same_route`</sub> | Expect last write wins; keep shared data in your own storage. |
| Two sockets of one session on different routes | n/a (same binding rule; measured across processes) | independent<br><sub>`test_two_sockets_other_route`</sub> | Nothing: state is per route. |
| `push_to_view()` with a Redis channel layer | n/a (measured across processes only) | delivered<br><sub>`test_push_to_view_across_processes`</sub> | Use a channel layer every process shares. |
| `push_to_view()` with an in-memory channel layer | delivered<br><sub>`test_push_with_in_memory_layer`</sub> | not delivered **(known gap)**<br><sub>`test_push_with_in_memory_layer`</sub> | Do not run more than one process with it. |
| Presence (Redis backend and channel layer) | n/a (measured across processes only) | join and leave seen by the other process<br><sub>`test_presence_across_processes`</sub> | Set `PRESENCE_BACKEND = "redis"` and a shared channel layer. |
| Presence (memory backend, in-memory channel layer) | n/a (measured across processes only) | each process counts only its own **(known gap)**<br><sub>`test_presence_with_memory_backend`</sub> | Do not run more than one process with it. |
| Persisted state read by a process whose clock is behind the writer's | n/a (one clock) | discarded: the view mounts fresh **(known gap)**<br><sub>`test_clock_skew_between_processes`</sub> | Keep the processes' clocks in sync. |
<!-- END GENERATED survival-sessions -->

Two sockets of one session on one route share one stored envelope. Each
socket's view holds its own copy, and each save writes that copy: a socket
whose copy is older than what the other saved overwrites it. This is the same
on one process and on two.

The persisted envelope records when it was written, in whole seconds of the
writing process's clock, and a reader refuses one written "in the future" by
its own clock. A process whose clock is behind the writer's therefore discards
fresh state and mounts the view from its defaults, without an error.

## Session engines

Each engine below was run with two processes. The persisted-state machinery
accepts every server-side engine, including ones that are local to a process;
nothing checks that the processes actually share it.

<!-- BEGIN GENERATED survival-engines: python -m tests.integration.multinode.survival_table --write -->
|  | HTTP fallback on another process | WebSocket reconnect to another process | What the app author must do |
|---|---|---|---|
| `db` (a database both processes use) | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Supported. |
| `file` (a directory both processes use) | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Only on storage every process shares. |
| `cache`, `FileBasedCache` in a shared directory | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Only with a cache every process shares. |
| `cache`, Django's `RedisCache` | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | A Redis with persistence. |
| `cached_db`, `RedisCache` plus the database | kept<br><sub>`test_session_engine_across_processes`</sub> | restored<br><sub>`test_session_engine_across_processes`</sub> | Supported. |
| `cache`, `LocMemCache` (one per process) | 500 error, the session cookie is deleted; the next request starts a new session **(known gap)**<br><sub>`test_session_engine_across_processes`</sub> | mount fails with an error **(known gap)**<br><sub>`test_session_engine_across_processes`</sub> | Never with more than one process. |
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
