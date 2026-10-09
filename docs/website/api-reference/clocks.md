---
title: "Clocks API"
slug: clocks
section: api-reference
order: 5
level: reference
description: "Experimental process-local RoomClock and SharedPoll API."
---

# Clocks API

Import from `djust.clocks`. **Experimental in 1.3.x for at least one minor
release**; see [API stability](../guides/api-stability.md).
Phases 1–3 are process-local; there is no distributed lease or ownership API.
The [guide](../guides/shared-room-clock.md) explains authorization, delivery,
tenants, membership and testing.

## RoomClock

`RoomClock(*, name, interval, step, ...)` is an opt-in function-of-key engine.
`name` is a nonempty ASCII slug; keys are nonempty strings of at most 512
characters. All intervals are seconds. The constructor creates no tasks or pool.

| Argument | Default | Contract |
| --- | --- | --- |
| `name`, `interval`, `step` | required | Namespace, fixed-rate seconds, callable receiving `ClockTick`. |
| `publish` | `None` | `Publish` destination; truthy step results send a doorbell. |
| `alive` | `None` | Sync/async callable of scoped key; otherwise use ensure heartbeats. |
| `idle_stop` | `5.0` | Seconds idle before stopping; liveness polling cadence is one second. |
| `trailing` | `1.0` | One final resend after last publishing beat; zero disables it. |
| `on_overrun` | `"skip"` | Skip missed slots or bounded `"catch_up"`. |
| `max_catch_up` | `3` | Maximum catch-up steps per dispatch. |
| `max_clocks` | `256` | Maximum live keys per definition, across loops. |
| `max_clocks_per_tenant` | `64` | Sub-cap for resolved tenants only; untenanted rooms use `max_clocks`. |
| `max_consecutive_errors` | `10` | Threshold for exponential retry backoff. |
| `max_backoff` | `30.0` | Maximum retry delay; success resets it. |
| `step_timeout` | `None` | Async cancellation; sync timeout reports stuck and waits. |
| `validate_key` | `None` | Optional server-side predicate of raw key; rejection raises `ValueError`. |
| `on_stop` | `None` | Sync/async `(scope, reason)` callback, skipped on cancellation. |
| `log_details` | `False` | Opt-in exception text and traceback. |
| `time_source`, `executor` | production implementations | Injection seams for deterministic tests. |

The interval floor is 0.02 seconds. A process-wide pool uses ten threads by
default, configurable by `LIVEVIEW_CONFIG["clock_workers"]` (integer >= 2).
It is separate from the session worker pool. A definition gets at most three
quarters of those threads; other eligible due calls can proceed.

| Method | Result and behavior |
| --- | --- |
| `scope(view, key)` | Canonical namespace and percent-encoded raw key, plus the resolved view's presence tenant prefix. Use this exact string for push subscriptions and state keys. |
| `ensure(view, key)` | Sync bridge for authorized WebSocket lifecycle code; returns `True` if running, `False` at capacity, when disabled in tests, or after the one-second stopping-run wait. |
| `await aensure(view, key)` | Async equivalent; refuses an unregistered/non-serving loop. A join during a requested or step-returned stop waits up to one second of real time for retirement, then starts a fresh run or returns `False` without cancelling the old worker. |
| `running(scope)` | Whether the scoped run is active, including an unfinished sync step. |
| `stop(scope, reason="requested")` | Graceful stop request, safe from a foreign thread/loop; returns whether a live run was found. |
| `pause(scope)`, `resume(scope)` | Suppress/resume steps while keeping membership checks; return whether a live run was found. |
| `set_interval(scope, seconds)` | Apply a valid interval on the next slot; return whether a live run was found. |
| `stats()` | Mapping of scopes to live stats and bounded recent stopped records. |

`room_clock` is a reserved LiveView configuration attribute, excluded from
serialized state and template context on every LiveView. On `PresenceMixin`,
set it to a `RoomClock` (or `None`) to opt into automatic tracking/restoration
binding; use another attribute name for application state.

`PresenceMixin.room_clock` binds tracking and restoration automatically. Its
raw key is the formatted presence key without the tenant prefix. Existing push
subscriptions are preserved; the clock adds one scope. The optional
`presence_key=` keyword on ensure is framework binding plumbing: application
code normally supplies `alive` or uses the mixin. It accepts only that view's
actual tenant-scoped presence key.

## ClockTick, Stop and Publish

`ClockTick` is immutable: `key` (raw), `scope` (tenant-scoped namespace), `seq`
(attempted steps within the run), `run_id`, `dt`, `skipped`, `scheduled_at`,
`started_at`, and `fence` (`None`, the deferred ownership seam). Monotonic
scheduled/start times are process-local seconds.

`Stop(reason="step stopped")` ends a run when returned by step. The reason is
stored in stats; exception and reason values are not interpolated into default
clock logs. Cancellation is recorded as `"cancelled"` and sends no trailing push.

`Publish(view_path, handler, payload=None)` sends through `apush_to_view` with
the engine's scoped key. The optional payload factory receives the raw key and
is called once per run in the shared clock worker pool, outside the registry lock. First-join ensure waits for the result, including pool queue delay; keep factories short. This initialization wait is separate from the bounded stopping-run wait. Its default is `{"key": key}`. It must return a dict
whose contents remain beat-invariant. There is no state or sequence in the
engine-generated doorbell. Returning a truthy step result publishes; a falsy
result is quiet.

## SharedPoll

`SharedPoll(*, fetch, **clock_options)` accepts all `RoomClock` options except
`step`. `fetch(ClockTick)` can be sync or async. Every successful fetch stores
its result under the scoped key and publishes. `latest(scope)` returns
`(version, result)` or `None`. Versions increase across runs while a result
remains retained. Results and reads share a lock; consumers must treat stored
objects as read-only. Retention is bounded; recently stopped results can be
removed to make room for active keys.

Only use scoped keys derived from an authorized view. Include every visibility
dimension (role, object permission, etc.) in the key. Tenant context is bound;
no configuring request/user context is inherited by the clock task.

## Test helpers

`djust.testing` exports `ManualClock`, `DeterministicClockExecutor`, and
`clocks_disabled`. Manual time supports async `advance(seconds)` and `settle()`;
the deterministic executor runs sync calls inline rather than in a thread.
The context manager `clocks_disabled()` makes sync and async ensure return
`False` before validating serving-loop or WebSocket context, allowing ordinary
`LiveViewTestClient` mounts.
