---
title: "Shared Room Clocks"
slug: shared-room-clock
section: guides
order: 13.5
level: advanced
description: "Run one process-local step or poll per tenant and room, with presence-driven lifecycle."
---

# Shared Room Clocks

`djust.clocks` is **experimental**, opt-in in 1.3.x, for at least one minor
release. A `RoomClock` runs a function once per interval for a room, regardless
of how many tabs watch it. `tick_interval` and `handle_tick` still run per
session; existing views create no clock tasks, pool or membership polls.

**These clocks are process-local.** Route a room and its in-memory state to
one process. Multiple event loops in that process are supported with
`MultiLoopInMemoryChannelLayer`. Redis transport does not elect an owner:
sessions spanning processes or `uvicorn --workers N` can double-step a room.
Distributed ownership, fencing and Redis leases are deferred (ADR-042 Phase 4).

## Quick start: presence starts and stops the clock

```python
import threading

from djust import LiveView, PresenceMixin
from djust.clocks import ClockTick, RoomClock, Publish

_snapshots = {}
_snapshot_lock = threading.Lock()


def beat(tick: ClockTick):
    with _snapshot_lock:
        _snapshots[tick.scope] = _snapshots.get(tick.scope, 0) + 1
    return True


room_clock = RoomClock(
    name="lobby",
    interval=1.0,
    step=beat,
    publish=Publish("myapp.views.LobbyView", handler="handle_refresh"),
    idle_stop=5.0,
)


class LobbyView(PresenceMixin, LiveView):
    template = "<div dj-root>{{ version }}</div>"
    presence_key = "lobby"
    room_clock = room_clock

    def mount(self, request, **kwargs):
        self.version = 0
        self.track_presence({})
        self.handle_refresh()

    def handle_refresh(self, key="", **kwargs):
        scope = room_clock.scope(self, "lobby")
        with _snapshot_lock:
            self.version = _snapshots.get(scope, 0)
```

Put this example in `myapp.views`, matching `Publish.view_path`. A real app
must authorize the room before `track_presence`, and recheck current access
when reading its snapshot. For tenant apps add `TenantMixin` before the other
mixins. The clock uses the same `tenant:<id>:` prefix as presence, and binds
that resolved tenant inside both sync and async callbacks. Never bypass
`clock.scope(view, key)` for a clock's publish subscription or state lookup.

Every connection's track and restore ensures the clock, including a second tab
of an already-present user. HTTP presence tracking is already a no-op and starts
no clock. The binding adds its scope to any existing `push_scope` subscriptions
and removes its old scope when tracked in another room; it needs one free push
scope slot. In the example its scope is `lobby:lobby`, or
`tenant:<id>:lobby:lobby` in a tenant view. Raw key punctuation is percent-encoded
in the scope (e.g. `room:same` becomes `room%3Asame`) to keep tenant IDs and
room keys from making ambiguous delimiters. The raw key is the formatted presence
key with its own tenant prefix removed. Membership uses the recorded presence
key, rather than the clock's namespaced scope.

Liveness is read at most once a second. A graceful last leave starts the idle
period using presence's existing count read. A crashed connection disappears
after the presence backend's timeout (normally 60 seconds), then a coarse poll
and `idle_stop`. A stale recorded membership can keep a clock running until that
expiry. Reconnect reads the current snapshot rather than replaying missed beats.

## Explicit lifecycle

Without the optional `room_clock` attribute, call `clock.ensure(self, key)` from
authorized WebSocket mount/event/tick code. Assign `self.push_scope` to
`clock.scope(self, key)` first. Async code calls `await clock.aensure(self, key)`.
Presence tracking also works in async handlers; its synchronous binding schedules
`aensure` on the serving loop and observes failures. The first joiner's
`handle_presence_join` runs after that ensure completes, without blocking the
loop; it may run after the async event handler returns. As with capacity refusal,
a failed ensure cannot guarantee a running clock to the callback. Successful
presence tracking still delivers the join callback if ensure raises (and logs
the failure). If the connection untracks while ensure is pending, the pending
join callback runs before its leave callback; completion cannot deliver it again.
Explicit stop-then-ensure waits at most one second (real time) for the old run to
retire before starting a new run. If it has not retired, ensure returns `False`;
retry later. The stopping run retains ownership, so its unfinished sync step
cannot overlap a replacement. Presence-bound ensures automatically register one
retry per retiring run and presence room, logged once at debug. As soon as the
old run retires, the retry re-ensures if any participants remain, even if the
joining connection has since left. No further join is required. An empty room
is not restarted. Capacity refusals and factory errors do not schedule this
retirement retry. Payload initialization does not consume the stopping-run wait
budget.
`ensure` refuses plain management-command/Celery threads and unregistered loops;
`async_to_sync` there would create a temporary loop that immediately dies.

Supply `alive(scope)` for membership, or heartbeat `ensure` more frequently
than `idle_stop`, with margin (a five-second heartbeat needs around twelve
seconds, rather than the default five). A false, `None`, or failing `alive`
starts the idle timer; a new ensure protects the run from a racing idle check.
A pending presence retry temporarily retains its authorized view binding.
Running steps retain no view or request user; they look state up by `tick.scope`.

Return a truthy value to publish, a falsy value to remain quiet, or
`Stop("room reaped")` to end a run. Sync steps and liveness callbacks use a
separate shared worker pool with database connection cleanup; async callbacks
run on the owner loop. One key never overlaps itself. A sync `step_timeout`
marks the run stuck and keeps waiting, because a thread cannot be cancelled.
An async timeout cancels the step and continues with the error policy.

## Timing, delivery and diagnostics

The fixed-rate schedule uses monotonic time. `on_overrun="skip"` drops missed
slots, supplies their count in `tick.skipped`, and uses
`dt = interval * (skipped + 1)`. `"catch_up"` runs at most `max_catch_up` steps
(default three), each with `dt = interval`, dropping the rest. `seq` counts
attempted steps within `run_id`; restart creates a new run ID. `fence` is always
`None`. Seeds and authoritative room state belong to the application.

The doorbell uses a payload built once from the raw key (default `{"key": key}`).
A custom `Publish.payload` factory runs in the shared clock worker pool, once
per run and outside the registry lock. First-join ensure waits for its result,
including any queue delay from busy clock workers. Keep factories short; this
initialization wait is separate from the one-second stopping-run wait.
It carries no sequence or run ID. Existing push delivery is best effort,
ordered, bounded, and coalesces identical queued events. A busy session gets
one queued doorbell for that key, then reads the latest committed snapshot.
Publish follows the step's completed writes; commit database work before
returning. `trailing=1.0` resends once after the last publishing beat. `Stop`
and idle/graceful stop flush a pending resend immediately; cancellation does not.

Step errors are value-free in logs by default, including in Django DEBUG.
`log_details=True` opts into exception text and traceback. After ten consecutive
errors, retries back off exponentially up to thirty seconds; a successful step
resets the breaker. Breaker-suppressed slots are intentional pauses: they do not
count as missed beats or contribute to the next step's `dt`. A failing clock
does not terminate automatically.
`clock.stats()` reports steps, skipped beats, overruns, last/max duration, run
age, sequence, run ID, stuck status, consecutive errors and stop reason. Recent
stopped records are bounded by `max_clocks`; expose these only to authorized
operators, because scope keys and application stop reasons can be sensitive.

Defaults cap each clock definition at 256 live keys and 64 per resolved tenant.
Untenanted rooms use the global 256-key limit. Clocks refuse
rather than queue excess keys, and reject intervals below 0.02 seconds. A lazy
process-wide ten-thread pool is separate from session `worker_threads`.
`LIVEVIEW_CONFIG["clock_workers"]` changes its size (integer at least two,
chosen before its first use). Each clock definition can occupy at most three
quarters of the pool, with due-time ordering among eligible pending calls.
A hung sync step retains its thread and scope until it returns. Allow a graceful
shutdown grace period longer than your sync callbacks: loop shutdown waits for
in-flight sync work, and a callback that never returns can block interpreter exit.

## Variable rate, pause and shared polling

Pass the scoped key to `set_interval(scope, seconds)`, `pause(scope)`,
`resume(scope)`, `stop(scope)`, and `running(scope)`. Rate changes affect the
next slot without restarting. Pause suppresses steps while membership and idle
checks continue; resume does not replay paused time.

`SharedPoll` takes `fetch` instead of `step`, stores the latest `(version,
result)` under a lock, and publishes a doorbell after each successful fetch.
View handlers call `poll.latest(poll.scope(self, key))`. Late joiners also read
that result during mount. Result retention is bounded by `max_clocks`; callers
handle `None` before the first successful fetch or after eviction of an idle key.
Include roles or object permissions in the raw key whenever they affect
visibility. The framework adds the tenant; it does not authorize a caller's
lookup. Per-user results are unsuitable for a shared poll. Prefer `db_notify`
when a table change can supply the doorbell directly.

## Deterministic tests

`djust.testing.ManualClock` supplies `now`, `sleep`, `advance(seconds)` and
`settle()`. Pass it as `time_source`, with `DeterministicClockExecutor` as
`executor`, to run due beats without wall-clock waits. That executor runs sync
callbacks inline **only in tests**. `advance` jumps time, so use interval-sized
advances for normal beats and a large jump to exercise late-beat handling.
The helpers require a serving-loop registration supplied by a real WebSocket
consumer; engine-only tests can use the internal loop registry explicitly.

Wrap synchronous `LiveViewTestClient` mounts in `clocks_disabled()` to make
ensure a no-op without needing a serving loop. This switch is context-local.

See the [clock API reference](../api-reference/clocks.md),
[server push](../advanced/server-push.md), and
[scaling across cores](scaling-across-cores.md).
