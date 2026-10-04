# ADR-042: A shared room clock

**Status**: Proposed
**Date**: 2026-10-04
**Deciders**: Project maintainers
**Related**:
- Issue #3005: a clock for shared state, one tick per room instead of per session (the maintainer asked for this ADR before any implementation)
- #3004 (scoped push), #3095 (scoped presence broadcast), #3001 (deferred server push), #3128 (several event loops per process), #3254 / #3325 (per-connection presence), #2973 / #3324 (tenant-keyed state and presence)
- `python/djust/runtime.py` (`maybe_start_tick_task`), `python/djust/websocket.py` (`_run_tick`, `_tick_once`, `_defer_server_push`), `python/djust/push.py`, `python/djust/presence.py`, `python/djust/multiloop.py`, `python/djust/layers.py`
- `docs/website/guides/scaling.md` (its "Option A" routes rooms to one process, its "Option B" shares state through Redis) and `docs/website/guides/scaling-across-cores.md` ("More than one event loop per process")
- ADR-041: the format and release-plan convention this ADR follows

**No code in this ADR exists in any release.** Every snippet is an illustrative
sketch of a proposed shape. Nothing here changes `tick_interval` or
`handle_tick`. The design alternatives below are called **Shapes** so they are
not confused with the scaling guide's Option A and Option B.

---

## Release plan this ADR assumes

- **1.3 takes opt-in changes only.** Phases 1 and 2 are additive and change no
  existing behaviour, so they can land in a 1.3.x or 1.4 release; the maintainer
  picks (open question 14).
- **Multi-process ownership (Phase 4) is a separate decision.** It needs a
  state-ownership answer this ADR does not give (open questions 9 and 10).

## Context

### What djust gives a view today

`tick_interval` (milliseconds, class attribute, `python/djust/live_view.py:653`)
plus an overridden `handle_tick()` (`:1634`) makes the **session's** WebSocket
consumer run a loop. `maybe_start_tick_task` (`python/djust/runtime.py:649`)
reads `tick_interval` from the view **class** at mount, requires that
`handle_tick` is overridden, and starts `consumer._run_tick`
(`python/djust/websocket.py:6218`). The interval is therefore per session and
fixed for the lifetime of the mount. Each iteration, `_tick_once`
(`websocket.py:6269`):

- returns without ticking when a user event is in flight or the session's
  render lock is held longer than 0.1 s (a **skip**, not a catch-up);
- runs `handle_tick` through `sync_to_async` on the session's thread. The
  assign snapshot **before** the handler always runs (`:6344`, `:6365`); only the
  one after it is skipped when the handler sets `_skip_render`.

So a tick is owned by one browser tab, runs when that tab is idle, and is lost
when that tab is busy. That is the right model for "refresh my dashboard every
30 s". It is not a clock for state that several sessions share.

### What exists to build on

- **Scoped push** (`push.py`): `push_to_view(..., scope=room)` and
  `apush_to_view` reach only the sessions whose view set `push_scope` to that
  value. A session joins the scope's group from its own `push_scope`
  (`push.py:166`), so the publisher's scope and the view's `push_scope` must be
  the same string or nothing is delivered.
- **Server-push delivery** (`websocket.py:5493`, `_defer_server_push` at `:5913`):
  a push that finds the session busy is queued. Only an **identical** queued
  event is superseded by the later one (`queued[1] == event`, `:5934`, and the
  event includes its payload, `push.py:324-341`); distinct events stay queued in
  order, at most 64 (`_MAX_DEFERRED_PUSHES`, `:53`), dropping the oldest past
  that. The drain runs the handler once per queued event, in order, then renders
  once (`_run_server_push_turn`, `:5571`). The test
  `test_identical_pushes_coalesce_and_distinct_ones_keep_order`
  (`python/djust/tests/test_server_push_deferred_3001.py:174`) pins this.
  Delivery is therefore **ordered and bounded, and coalesced only for identical
  events**, not "latest wins" in general.
- **Self-broadcast filter** (`websocket.py:5534-5535`): a push whose
  `sender_channel` equals the receiving session's own token is dropped. The
  token is the `push.origin_channel` context variable, set only on the event
  spine (`runtime.py:1833`), and a task created from inside an event handler
  copies it.
- **Presence** (`presence.py`, `backends/`): per-connection records aggregated
  per user (#3325), a 60 s timeout (`presence.py:89`), `handle_presence_join` /
  `handle_presence_leave` on a user's first and last connection, and the tenant
  prefix applied to a presence key by `tenant_scoped_presence_key` (`:93`),
  which needs the view. When state restoration skips `mount()`, presence is
  re-registered by `_restore_presence` (`:577`), not by a mount.
- **Several event loops** (`multiloop.py`, #3128): sessions of one room can be
  on different loops, so room-wide background work must be one task per room
  started under a `threading.Lock`, must not cache the running loop, and
  reaches sessions only by pushing (`scaling-across-cores.md`, rules 2 and 3).
  `MultiLoopInMemoryChannelLayer` (`layers.py:155`) accepts sends from any loop or
  thread; the plain in-memory layer does not.
- **Worker threads** (`worker_pool.py`): `LIVEVIEW_CONFIG["worker_threads"]`
  pins each session to a thread. A clock step is not a session and must not
  borrow a session's thread.
- **Cross-process transport**: a Redis channel layer and a Redis presence
  backend. There is **no lease, leader or fencing primitive** in `python/djust`
  outside tests. The nearest code is not one: `nx=True` appears at
  `state_backends/redis.py:135` (registering an observation counter) and
  `bug_capture_store.py:385` (an id-collision guard). The nearest **prior art**
  is `RedisStateBackend._claim_observation` (`state_backends/redis.py:139`), a
  Lua compare-and-set with a monotonic sequence.
- **`db_notify`** (`db/notifications.py`): one LISTEN task per process, and
  notifications missed while its connection drops are lost (module docstring).
  It is a doorbell for database changes, not a timer.

### What the apps in this workspace do instead

Three shared-room apps, three different home-made clocks. All three exist
because the per-session tick cannot be one.

**Snake Arena** (`snake-arena/snake_arena/`, on k3s at snake.djust.org).

- Before: every tab ticked at 10 Hz and the tabs elected a host. "A room with
  10 open tabs ran 100 session ticks a second to do the work of 10"
  (`docs/room-clock.md:11`). Measured before the tick path honoured
  `_skip_render` (#2822): a non-host session cost 7.5 ms/s of change detection
  and a room of 8 sessions 60 ms/s (`docs/review-2026-09-14.md`, F1).
- Now: `snake_arena/clock.py` (202 lines), one asyncio task per room, started
  from the WebSocket branch of `SnakeGameView.mount` (`views.py:283-291`, never
  from the HTTP prerender) and pushing with `apush_to_view(..., scope=room)`
  (`clock.py:147`). It hand-builds:
  - drift-free scheduling against the loop clock, and **skip, don't burst** when
    more than one interval behind (`clock.py:96-103`);
  - steps in a shared thread pool of 10 (`clock.py:47`), because
    `sync_to_async` without an executor made up to 20 threads per loop;
  - start-on-first-member with a race guard (`_REQUESTED`, `clock.py:128`) and
    idle stop after 5 s with a locked re-check (`_stop_if_idle`, `:163`),
    because two sessions of one room can ask on two loops at once (#3128);
  - a **stop signal from the game**: the clock exits when the room is reaped
    (`clock.py:105-108`) and when the roster has been empty for 5 s (`:132`);
  - a **backstop**: every session's 5 s tick calls `ensure_clock`
    (`views.py:632-638`). It matters because `mount` does not run again when
    a failed-over session's state is restored;
  - error isolation (one failing beat is logged and the clock continues, `:114`),
    and a final-frame **resend** one second after a match ends because a dropped
    push would leave the last frame stale (`:137-142`);
  - a presence roster read **every beat** (`clock.py:68`, `:84`), because `prune`
    drops queued players who are missing from it. With the Redis presence
    backend that is a sorted-set read ten times a second per room.
  - A payload of `{"room": room}` that is the same on every beat (`:149`), so a
    busy session's queued doorbells supersede each other.
- The lobby has the same shape one level up: `LobbyView` ticks at 1 Hz per
  session and each tick lists presence for every room (`views.py:790-830`).
  N viewers do N identical scans a second.
- Everything is in one process by design: "The room clock, sessions, presence
  and the channel layer are all in-process ... Two pods would split rooms, so
  never scale this Deployment up" (`docs/deploy-k8s.md`).
- The app's own proposal asks for the same primitive and lists what a
  multi-process version needs: "renewable leases, fencing tokens,
  authoritative sequence numbers, and atomic state writes ... A Redis channel
  layer alone does not solve simulation ownership"
  (`docs/djust-improvements.md`, section 1).
- Cost after the change (`docs/room-clock.md:11-13`): 10 steps a second per
  room plus one heartbeat tick per tab every 5 s, instead of 10 ticks a second
  per tab. Every changed beat still renders every session of the room
  (`_publish_if_changed` in `views.py`): the clock removes wasted wakeups, not
  renders. Renders are a separate cost (`docs/djust-improvements.md`, section 2).
- Its test file names the behaviours an engine must support
  (`tests/test_clock.py`): one clock per room, stops when empty, stops when
  reaped, survives a failing step, pushes a finished match again, an unreadable
  roster still lets the clock stop, a join during the stop check keeps the clock,
  two loops starting one room at once start one clock (`:302`).

**Emberdeep** (`dungeon-arena/emberdeep/emberdeep/`, a co-op dungeon).

- The world clock is a `threading.Thread` per run that does `time.sleep(TICK_MS)`
  and then ticks (`world.py:485`, `TICK_MS = 200` at `:19`). The period is the
  interval **plus** the step time, so it drifts; there is no skip policy.
- The comment above it records why it left the elected host: "A client-elected
  host is fragile: if the host's socket flaps ... tick_count freezes and the
  whole dungeon deadlocks" (`world.py:463-468`).
- To push from a foreign thread it stores the loop in a module global,
  `_MAIN_LOOP`, because a group send from a foreign thread does not wake the
  in-memory layer's queues (`views.py:31-56`). Under `djust serve --loops N`
  that is one loop of several, and the pattern is the one rule 3 of the
  multi-loop guide forbids ("don't cache the running loop").
- Its push is unscoped: `push_to_view(VIEW_PATH, handler="refresh_run",
  payload={"channel": ...})` (`views.py:45-51`, `:103`, `:330`) wakes every session
  of the view in every channel.
- It also runs `tick_interval = 200` per session only to heartbeat presence and
  prune seats (`views.py:86`, `:318-333`).

**Encounter** (`dungeon-crawler/glasshouse/example/encounter_view.py`).

- It never left the per-session tick: `tick_interval = 50` (`:19`), and each
  session's `handle_tick` takes the room lock and calls `session.advance()`
  (`:71-76`). Correctness comes from a **wall-clock accumulator**: `advance`
  computes elapsed time since the room's `last_tick`, runs up to three fixed
  50 ms steps (a 0.15 s cap) and advances `last_tick` by the steps run
  (`encounter_rooms.py:43-52`). Two tabs do not double-step the room because
  the second finds nothing elapsed.
- Cost: every session wakes 20 times a second, fingerprints its assigns and
  re-renders in `_load`, to find out that another session already stepped.
- Its stepping is **bounded catch-up**, the opposite of Snake's skip. Both are
  legitimate; a framework clock has to offer both.

**djustlive** and the examples use the tick as a poll rather than a clock:
deployment status every 3 s (`djustlive/dashboard/views.py:257`, project page
`:606`), deployment logs every 1.5 s (`:429`), the apps list every 5 s (`:849`),
operations pages every 10 s (`djustlive/ops/views.py:139`, `:375`, `:512`,
`:610`). N viewers of one deployment run N identical queries. These are not
games: no shared mutable state, no ordering, a dropped beat is harmless, and the
poll should stop when nobody is looking. Some already have a doorbell
alternative: the ops views subscribe to Postgres LISTEN through `db_notify` when
`DJUSTLIVE_ENABLE_LIVE_LISTEN` is on and poll otherwise (`ops/views.py:38-50`).
A poll is still the right tool where there is nothing to LISTEN to (time-based
recomputation such as heartbeat age, an external API).

### What the apps need from a framework clock

| Need | Snake Arena | Emberdeep | Encounter | djustlive polls |
|---|---|---|---|---|
| Rate | fixed 0.1 s | fixed 0.2 s | fixed 0.05 s | 1.5 to 10 s, ideally changes (1.5 s while a deploy runs) |
| Start | first WS member | first member (`get_run`) | first session | first viewer |
| Stop | 5 s after last member, or game says stop | 60 s after last human | room TTL 600 s | last viewer |
| On a late beat | **skip** | none (drifts) | **bounded catch-up** (3 steps) | skip |
| Step runs in | worker thread | its own thread | session thread | session thread |
| Frame to members | doorbell, members re-read | doorbell | each session re-reads | each session re-queries |
| Late joiner / reconnect | mount reads the live game | mount reads the run | mount reads the room | mount queries |
| Determinism | none (`random` in game logic) | tick-derived pseudo-random, "no RNG in clock" | none | not needed |
| Per-room pause | game flag, clock keeps running | none | none | none |

Two lessons carry across all rows. **The clock must outlive any session**: three
apps abandoned (or never reached) "a session owns the clock" because a tab that
reloads, is rate-limited or is busy stops the room. And **the clock publishes a
doorbell, not state**: every app has members re-read an authoritative snapshot,
which is also what makes a dropped or coalesced frame harmless.

### Problem statement

There is no framework primitive for "run this once per interval for everyone
in room K, start it when the first member arrives, stop it when the last
leaves". Each app re-derives scheduling, single-flight start, idle stop, thread
use, error isolation and push scoping, and each re-derivation has had its own
race or drift. Snake's version is correct today, and still carries three
separate race guards.

## Decision drivers

1. **One clock per key, not per session.** Steps per room per second must not
   grow with the number of members.
2. **The clock outlives sessions.** No session-owned leadership.
3. **Honest delivery.** The channel layer is best effort, and server push is
   ordered, bounded and coalesced only for identical events
   (`websocket.py:5934`). The design must not promise exactly-once delivery to
   sessions, and must publish something that coalesces.
4. **Correct under `--loops N`.** One task per key across loops, no cached loop.
5. **Tenant and room isolation.** A key must not cross tenants; a client must
   not be able to start clocks at will.
6. **No new infrastructure by default.** The single-process case (which is
   Snake, Emberdeep and Encounter) works with the in-memory layer and no Redis.
7. **Additive.** Per-session `tick_interval` and `handle_tick` keep their
   meaning; no existing app changes unless it opts in.
8. **Testable without sleeping.** The scheduler runs against an injected clock
   (the repo already treats wall-clock waits as flaky: #2124).
9. **Do not pretend multi-process is solved.** A lease says who steps. It does
   not say where the room's state lives.

## Design shapes

Four shapes were asked for. Each is described by what runs, then compared. The
issue's own spellings, `@room_clock(interval=0.1, key="snake:{room}")` and
`djust.clocks.run_every(group, interval, fn)`, are covered: `run_every` is the
function form of Shape 2, and the decorator is its declarative form (Phase 2).

### Shape 1: framework-owned task per room, delivered to ONE leader view

The framework starts a task per room key and, each beat, delivers
`handle_room_tick` to one chosen member session (the leader) through the
channel layer. Across pods the leader is chosen by a Redis lease or by sticky
routing.

- A "leader view" is a session. When its tab reloads, is rate-limited, or is
  busy, beats stop or are skipped (`_tick_once` skips a busy session). Emberdeep
  left the elected host for that reason (`world.py:463-468`). The framework
  would be re-implementing host election, with the same takeover problem,
  inside the runtime.
- The step would run on a session's thread under that session's render lock,
  coupling the room's rate to one viewer.
- It would deliver a step command through a channel that queues and drops
  (`websocket.py:5913`).

### Shape 2: `RoomClock`, a helper the app owns, with an explicit lifecycle

A library class (`djust.clocks.RoomClock`) that is the engine Snake wrote by
hand: a registry of one supervised task per key, `ensure(view, key)` to start it
idempotently, a stop condition, drift-free scheduling with a declared late-beat
policy, steps in a worker pool, error isolation, a doorbell publish through
scoped push. The step is a **function of the key**, not a method of a view. The
app calls `ensure` from `mount` (WebSocket branch) and from a backstop. A later
phase binds start and stop to presence.

- Maps one-to-one onto working code (Snake `clock.py`) and onto the multi-loop
  rules already documented.
- Explicit: the app decides what a room is, who may start one and what the step
  does.
- Does nothing about processes: the registry is per process.

### Shape 3: `tick_scope = "room"` on the view, group-broadcast tick with dedupe

The view declares `tick_scope = "room"`. Every member keeps a tick task, the
tick is broadcast to the room group, and a dedupe (an in-process lock, or a
Redis `SET NX` per beat across pods) lets the first receiver run
`handle_tick`.

- Timers do not go away: N sessions still run N timers and N wakeups per beat,
  so the dominant cost in Snake's "before" figures stays.
- A dedupe per beat across pods is one Redis operation per beat per room (10 a
  second at 10 Hz) on the hot path.
- Whoever wins a beat is a session, with Shape 1's problems; a busy winner skips
  the beat for the whole room.
- It is the smallest API change, and it is declarative, which is its appeal.

### Shape 4: leave it to app code, document it, add a lease helper

Document the Snake pattern (the multi-loop guide already does, in prose), and
ship only a small `djust.leases` helper for apps that want cross-process
ownership.

- No clock API to stabilise.
- Every app keeps its own scheduler, and the three in this workspace show how
  that goes: three designs, one with drift and a captured loop, one that never
  left the per-session tick.
- A lease helper without a state-ownership model invites split brain (below).

### Comparison

| | 1: leader view | 2: `RoomClock` | 3: `tick_scope="room"` | 4: docs + lease |
|---|---|---|---|---|
| Steps per beat | 1 | 1 | 1 (N timers wake) | app's |
| Owner of the clock | a session | the registry | a session | the app |
| Leader/host dies mid-game | beats stop until re-election; same bug class as Snake's old host takeover | no leader; the process owns it (process death = Phase 4) | as 1 | app's |
| Split brain | possible with Redis lease, needs fencing | impossible in one process; Phase 4 needs lease plus fencing | dedupe window can double-step | app's |
| Network partition (Redis unreachable) | leader can't prove leadership | single process unaffected; Phase 4 fails stopped (open question 10) | dedupe fails open or closed, app can't tell | app's |
| Tick delivery | at-most-once to one session, queued | step runs once per slot or is skipped and counted; doorbell is ordered, bounded, identical-coalesced | at-least-once with dedupe, queued | app's |
| Latency/jitter | one channel hop plus a session's render lock | schedule on the owning loop's clock; jitter is that loop's lag | channel hop plus lock | app's |
| Works with `--loops N` | needs cross-loop leader | one task, started under a lock | per-session, any loop | app's |
| Tenant/DoS surface | key from the view | `ensure` is server code only, caps, tenant-scoped key | key from the view | app's |
| API surface | new view hook + election rules | one module, one class | one view attribute + dedupe contract | docs + helper |
| Effort | high | medium | low-medium | low |
| Verdict | **reject** | **recommend** | reject as the engine; revisit as sugar | reject as the whole answer |

## Decision (PROPOSED: the maintainer decides)

### 1. Build Shape 2, and keep its step a function of a key

Add `djust.clocks` with a `RoomClock` registry. The clock is process-owned, not
session-owned. It is the generalisation of Snake's `clock.py`, so the first user
is the app that exposed the need.

Illustrative sketch, **not an available API**:

```python
# ILLUSTRATIVE: proposed shape, not available in any release.
from djust.clocks import RoomClock, ClockTick, Publish, Stop

def snake_beat(tick: ClockTick):
    """One beat for one room. Runs in a worker thread and never overlaps itself
    for the same key. Returns True to publish a doorbell, a falsy value to stay
    quiet, or Stop(reason) to end the clock."""
    game = peek_game(tick.scope)     # state is looked up by the tenant-scoped key
    if game is None:
        return Stop("room reaped")
    before = game.version
    game.tick()
    return game.version != before

snake_clock = RoomClock(
    name="snake",                    # namespaces this clock's keys
    interval=0.1,                    # seconds
    step=snake_beat,                 # sync (worker pool) or async def (on the loop)
    on_overrun="skip",               # "skip" | "catch_up" (bounded by max_catch_up)
    alive=lambda scope: bool(roster(scope)),  # gets the scope; polled about once a second,
                                              # in the worker pool, not per beat
    idle_stop=5.0,                   # seconds not alive, and not ensured, before stopping
    trailing=1.0,                    # one more doorbell this long after the last change
    publish=Publish("snake_arena.views.SnakeGameView", handler="handle_refresh_room"),
    max_clocks=256,                  # per process, for this clock
    max_clocks_per_tenant=64,
)

# SnakeGameView.mount, WebSocket branch only, after the seat is authorised.
# The scope is the tenant-scoped key; the view, the app's room registry and the
# clock must all use the same one.
scope = snake_clock.scope(self, room)
self.push_scope = scope
game = get_game(scope)
snake_clock.ensure(self, room)       # idempotent; starts the clock if needed

# Async code uses `await snake_clock.aensure(self, room)`.
# ClockTick: key, scope, seq, run_id, dt, skipped, fence, scheduled_at, started_at.
# snake_clock.running(scope), snake_clock.stop(scope), snake_clock.stats()
```

Contract of the engine:

**Ownership and loop.**

- **One task per scope per process**, created under a `threading.Lock` together
  with the request timestamp, with the stop decided under the same lock. This is
  Snake's `_ensure` / `_stop_if_idle` pair, written once and tested once.
- **The task lives on one loop, and the contract says which.** In Phase 1 it is
  the loop of the session that first called `ensure`/`aensure` (Snake today,
  rule 2 of the multi-loop guide). Consequences: sync `ensure` is valid only
  where `async_to_sync` returns to a serving loop, that is under a session's
  `sync_to_async` (a mount, an event handler, `handle_tick`); async code uses
  `aensure`. From a plain thread (a Celery task, a management command, a test)
  `async_to_sync` would run a temporary loop and a task created there dies when
  it closes, so the engine **refuses** (raises) when it is not on a serving loop.
  **A serving loop is one the engine can recognise**: inside `async_to_sync` from
  a plain thread, `get_running_loop()` returns the temporary loop, so it cannot
  tell. Phase 1 therefore adds a small registry of serving loops (each consumer
  registers the loop it runs on at connect, held weakly; an entry counts only
  while its loop is running and not closed). The alternative, asgiref's private
  thread-local for the main loop, is rejected as private API (open question 17).
  Other loops and threads see the running task in the registry and do nothing.
  `stop` from another thread or loop goes to the owner with
  `call_soon_threadsafe` (rule 3). A registered task whose owner loop is stopped
  or closed is **not** `done()`, so the registry treats a task as dead when
  `task.done()` **or** `task.get_loop().is_closed()` (or the loop is no longer
  running), and the next `ensure` restarts the clock on the caller's loop. That
  also keeps per-test loops from leaving stale entries behind.
  Jitter is the owning loop's lag, which is the loop that serves sessions; see
  the dedicated-loop alternative below.
- **Clean context, created in a way no task factory can undo.** The task is
  created with `contextvars.Context().run(loop.create_task, coro)`. The
  `create_task(..., context=)` keyword (Python 3.11, the project's floor) is not
  enough: it depends on the loop's task factory honouring it, and a factory that
  ignores the keyword silently gives the task the caller's context while one that
  does not accept it raises `TypeError`; djust already knows custom factories
  exist (`runtime.py`, the `_djust_tick_view` comment in `maybe_start_tick_task`).
  Running `create_task` inside a fresh context works on every version and
  factory. Without a clean context, a clock started from an event handler
  inherits `push.origin_channel` and every doorbell is dropped as that session's
  own self-broadcast (`websocket.py:5534`).
- **What the fresh context drops, and what the engine puts back.** It carries no
  request, user, tenant or diagnostics state, so:
  - **Tenant.** `ensure(view, key)` captures the view's resolved tenant and the
    engine sets it as the current tenant (`get_current_tenant()`,
    `tenants/middleware.py:28`) inside the fresh context, for the step and for
    `alive`. A clock is one tenant for its whole life, because the tenant is part
    of the scope. A view with no tenant sets nothing. Without this, tenant-aware
    ORM code in a step sees no tenant. Whether the engine sets it, or steps must
    filter by `tick.scope` explicitly, is open question 15.
  - **Failure logging.** The diagnostics gate defaults to "details allowed" in a
    fresh context (`_exposure_diagnostics.py:20`), and a function-of-key step has
    no owning view to restrict it. The engine does not rely on the default:
    **step and `alive` failures are logged value-free**, with the exception type
    and the clock's own message only, no exception text and no traceback, inside
    a diagnostics scope the engine opens and restricts itself. A clock built with
    `log_details=True` opts in to the full exception for apps that want it
    (open question 16).
  - The step looks state up by `tick.scope`, not by anything ambient.
- **Server-only start, from any lifecycle point.** `ensure` is a server-side
  call. No event parameter, decorator or client field starts a clock. Calling it
  from an event handler is allowed, because the context is clean and the
  authorisation is the app's.

**Stopping.**

- **`alive(key)`** is the engine's membership source. It is polled at a coarse
  cadence (proposed 1 s), not per beat, and an exception or `None` counts as
  not alive (Snake: an unreadable roster lets the clock stop, and never prunes).
  The clock stops when `alive` has been false for `idle_stop` **and** no
  `ensure` arrived since the first false reading (Snake's `_REQUESTED` guard,
  built in).
- **Without `alive`**, liveness is `ensure()` heartbeats alone: the clock stops
  after `idle_stop` with no `ensure`. `idle_stop` must then exceed the app's
  heartbeat period with margin (Snake's 5 s session tick needs about 12 s, not
  5 s); the documentation says so.
- **A step can stop the clock**: `Stop(reason)` (Snake: room reaped, match over).
  The reason goes to the log and `stats()`; an `on_stop(key, reason)` callback is
  proposed.

**Scheduling.**

- **Fixed-rate schedule on the owning loop's clock** (`next_at += interval`), so
  step time does not accumulate into the period.
- **Late-beat policy is declared, not implied.** `"skip"`: if the clock is more
  than one interval behind, drop the missed beats, count them in
  `tick.skipped`, and resume (Snake). `"catch_up"`: run up to `max_catch_up`
  steps back to back, drop the rest (Encounter's cap of three). Never an
  unbounded burst.
- **`tick.dt`** is the room time this step covers, in seconds: `interval` for an
  on-time step and for each catch-up step, and `interval * (skipped + 1)` after a
  skip, so a countdown or a match timer stays accurate. The framework supplies
  `seq` (steps within a run), `run_id` (changes when the clock restarts) and
  `dt`, and nothing else toward determinism: random seeds stay the app's.
- **A timer wheel is an implementation choice, not a contract.** One scheduler
  task per process waking at the next due time across all clocks does fewer
  wakeups than a task per key. The engine measures both (acceptance B3) and
  picks; nothing above depends on it.

**Steps.**

- **Sync steps run in a shared worker pool**, one pool per process (not per
  loop), so a slow step cannot block the loop and thread count does not multiply
  with `--loops N`. **Async steps** (`async def`) run on the owning loop, for the
  async ORM and async clients, and can be cancelled on timeout. `alive` is a sync
  callable that reads presence or a database, so it **also runs in the pool**,
  never on the loop (an `async def alive` runs on the loop). Pool calls are
  wrapped in `close_old_connections()` before and after, as djust's own worker
  path does (`websocket.py:5729-5731`), so a long-lived pool thread does not hold
  a stale database connection.
- **One step at a time per key.** A step that outlives its interval is not
  overlapped; the next beat follows the late-beat policy. A sync step in a thread
  **cannot be cancelled**, so `step_timeout` for a sync step means only "report
  and keep waiting": the clock does not start the next step until the thread
  returns, and the clock is marked stuck in `stats()`. A hung sync step holds a
  pool thread; that is why the pool, not the loop, absorbs it.
- **Errors are logged and the clock continues, with a circuit breaker.** A step
  that fails on `max_consecutive_errors` beats in a row (proposed 10) moves to
  exponential backoff capped at `max_backoff` (proposed 30 s) instead of failing
  at full rate, logged once per window with the log-exposure-safe helper;
  one success resets it. Whether it ever stops is open question 8.

**Publishing.**

- **The doorbell payload is beat-invariant.** The clock publishes the same event
  every beat for a key: the handler name and a payload built once from the key
  (Snake's `{"room": room}`), with **no `seq` and no `run_id`**. Identical queued
  events supersede each other (`websocket.py:5934`), so a busy session holds at
  most one queued doorbell per key and runs the handler once per drain turn. A
  payload that changed every beat would queue up to 64 and run the handler up to
  64 times in one turn. `seq` and `run_id` stay in `ClockTick` and in the app's
  own snapshot; a view that wants them reads `snake_clock.stats()` or its
  snapshot's version.
- **Publish happens after the step returns**, so the step's writes must be
  visible by then (committed to the database, or done under the lock the
  snapshot read takes: Snake's `_load` reads under `game.lock`).
- **A trailing doorbell** (`trailing`, proposed 1 s) is sent once, that long
  after the last beat that published, so a doorbell lost at the end of activity
  heals without periodic noise. It generalises Snake's final-frame resend. On
  `Stop(reason)` and on an idle stop it is **flushed at once**, if any beat
  published since the last doorbell, so members re-read the final state without
  waiting; a cancellation (shutdown) does not flush (open question 6).
- The publish scope is `clock.scope(view, key)`, the same string the view sets
  as its `push_scope`.

**Tenant scoping (requirement).** Clock keys and publish scopes are
tenant-scoped through the same helpers as presence keys (#2973, #3324). The exact
wiring is in the sketch: `clock.scope(view, key)` applies the tenant helper to
the key, `ensure(view, key)` uses the scope as the registry key, and the view
assigns the same string to `push_scope`. The per-tenant sub-cap and the isolation
tests below follow from this. Tenant scoping of clock keys and publish scopes
must be specified and tested before Phase 1 is accepted.

**Alternative loop ownership: a dedicated clock thread and loop.** The engine
owns one thread running its own loop, and every clock runs there. Valid from any
thread, so a Celery task or a test can call `ensure`; jitter no longer depends on
the busiest serving loop (Snake's loop thread ran at 0.92 to 0.96 of a core above
192 clients on one loop, `docs/deploy-k8s.md`); it removes the need for
Emberdeep's captured loop. Costs: one more thread; the publish crosses loops, so
it needs a loop-safe layer (`MultiLoopInMemoryChannelLayer` or Redis) or an
explicit hop to a serving loop, which the plain in-memory layer does not accept
from a foreign loop (the reason for `_MAIN_LOOP`, `views.py:31-36`); async steps
would run on a loop that is not the app's. Recommended for Phase 1 is the session
loop, because it needs nothing new; the engine keeps the loop behind one internal
seam so this can be added. Open question 1.

**Alternative coalescing: change the framework.** If the owner wants a doorbell
that carries `seq`, the deferred queue could coalesce by `(group, handler)` for
events flagged as coalescible. That changes `_defer_server_push`'s equality rule
(pinned by `test_identical_pushes_coalesce_and_distinct_ones_keep_order`) and so
would be an explicit, opt-in change with its own tests. Not proposed; open
question 3.

### 2. Phase 2: bind start and stop to presence, then optional declarative sugar

Once `RoomClock` exists, tie it to presence (#3325) so an app stops writing the
backstop. Phase 1 stays free of `PresenceMixin` changes because the binding
touches its track, restore and join paths (#3254), which is separate risk, and
because the engine is useful alone (Snake migrates in Phase 1 with its own
`alive` and backstop).

```python
# ILLUSTRATIVE: proposed shape, not available in any release.
class SnakeGameView(PresenceMixin, LiveView):
    presence_key = "snake:{room}"
    room_clock = snake_clock        # ensure on EVERY connection's track or restore;
                                    # stops idle_stop after the presence count reaches 0
```

- **Start keys off every connection**, in `track_presence` and in
  `_restore_presence` (`presence.py:577`), not only `handle_presence_join`. A
  restored session skips `mount()`, and `handle_presence_join` fires only for a
  user's first connection (#3325): after a restart against a Redis presence
  backend the old record still stands until its 60 s timeout, so a returning tab
  produces no join. `ensure` is idempotent, so calling it on every connection is
  safe.
- **Only stop polls the count** (`presence_count`, once a second, not per beat).
  A step that needs the roster, as Snake's `prune` does, reads it itself.
- **Stop latency for a crashed tab is bounded by the presence timeout** (60 s,
  `presence.py:89`), plus `idle_stop`, not by `idle_stop` alone. A graceful leave
  stops within `idle_stop`.
- This is the declarative form of the issue's `@room_clock` spelling, with the
  clock still process-owned rather than session-owned (the part of Shape 3 that
  survives). The headline lifecycle of #3005, "starts on first join, stops when
  empty", lands here and not in Phase 1 for the reason above.

### 3. Phase 3: variable interval, pause, shared poll

- `clock.set_interval(key, seconds)` for clocks that change rate (djustlive's
  1.5 s while a deployment runs, slower afterwards) and `pause(key)` /
  `resume(key)` that keep membership and liveness running while the step is not
  called (open question 12).
- **`SharedPoll`**: one fetch per interval per key for every viewer. Unlike a
  doorbell, viewers need the result, so it needs a **result store**: the engine
  keeps the latest result and a version in process memory (`poll.latest(key)`),
  and the doorbell tells viewers to read it. Rules: the fetch runs without a
  request user, so the **key must carry whatever decides visibility** (the
  tenant is added by the framework; a role or an object permission is the app's
  to put in the key, and per-user data is not a shared poll); the result is read
  under the same lock the fetch writes it with; a trailing doorbell applies as
  above. A `db_notify` doorbell is the better choice where the source is a table
  (djustlive's ops views); `SharedPoll` is for sources with nothing to LISTEN to.
  It covers djustlive's poll views, and Snake's lobby once its per-room scan runs
  once per second per process.

### 4. Phase 4 (separate decision): cross-process ownership

Not recommended for the first release. Sketch of what it would need:

- **Lease and fence, atomic.** Acquire is one Lua script:
  `SET lease owner NX PX ttl`, and only on success `INCR fence` and return the
  token. A separate `SET` then `INCR` is not atomic. Renew is a compare-owner
  `PEXPIRE` script, release a compare-owner delete. Prior art in the repo:
  `_claim_observation` (`state_backends/redis.py:139`).
- **The fencing token is in `ClockTick.fence`** (`None` in a single process) and
  must be checked by whatever stores the effect (a database write rejects a stale
  token). Without a store that checks it, the token is decoration, and the
  acceptance test below needs one. Who provides the check (a helper for a
  compare-and-set column, or the app) is open question 9.
- **Redis failover can lose both the lease and the counter** (replica promotion
  with asynchronous replication), so a Redis fence is not monotonic across a
  failover. A durable monotonic source (a database sequence) is the alternative.
  Other lease sources: a Postgres advisory lock (a dedicated connection; not
  through a transaction-pooling pgbouncer, the caveat `db_notify` already
  documents), or Kubernetes Lease objects (needs RBAC and an API client, k3s
  deployments only). Multi-Redis lock schemes are rejected as too much for the
  guarantee.
- **Fail stopped, with a wide margin.** An owner stops stepping when it cannot
  renew within `ttl - margin` by its own monotonic clock, so a paused or
  partitioned owner stops before another can start. Renewal every `ttl/3` on a
  2 s TTL flaps on a Redis latency spike (about 0.67 s between attempts), so the
  TTL is a deployment setting with a wider default (proposed 6 s TTL, 2 s renew
  interval, margin of one renew interval).
- **Failover time is derived, not promised.** A crash is noticed after `ttl` plus
  a takeover poll, so with the proposed values at most about 8 s, and a graceful
  release within one poll (about 2 s). A shorter TTL gives a faster takeover and
  more flapping; the number is the owner's choice (open question 10). The 1 to
  3 s client reconnect figure in the scaling guide is how fast clients come back,
  and says nothing about a room's state; it is not a target for the clock.
- **A lease decides who steps, not where the room lives.** If room state is in
  the owner's memory (Snake, Emberdeep, Encounter), members on other pods cannot
  read it and their commands cannot reach it: that needs a room-actor protocol
  (commands forwarded to the owner, frames back) which this ADR does not design.
  If room state is in Redis or the database, any pod can step, at the price of a
  state read-modify-write per beat. Today's supported answer for live in-process
  objects is the scaling guide's Option A, routing each room to one process, and
  it stays that.
- **Single-process fallback.** With no lease backend configured, `ensure` takes
  the in-process lock and nothing else. The API is the same.

### What this does not change

- `tick_interval` and `handle_tick` keep their per-session meaning, the interval
  stays fixed per class at mount, and `maybe_start_tick_task` is untouched. A
  view may use both: Snake keeps a 5 s session tick for its presence heartbeat.
- No deprecation of per-session ticks is proposed. A later system check may
  suggest `RoomClock` for a view that sets `push_scope` or `presence_key` and a
  `tick_interval` below one second (open question 13).

## Cross-cutting design

### Security

- **Who may start a clock.** Only server code, after the room is authorised, and
  never from the HTTP prerender. Snake gates on the WebSocket branch
  (`views.py:283`), and rooms are reachable by an unauthenticated
  `GET /r/<anything>/` (`game.py` `reap_idle_rooms` docstring), so the clock
  must not be a free side effect of a GET. `ensure` also refuses a key that
  fails the key validation the app declares (a slug rule), as `slugify_room` does.
- **DoS by many rooms.** Caps at two levels, each refusing (log once, never
  queue) at the limit: `max_clocks` per process for a clock, and
  `max_clocks_per_tenant` per tenant scope prefix, so one tenant cannot exhaust
  the process cap for the others. The pool is fair by construction: at most one
  in-flight step per key, so one slow room occupies one thread; due keys are
  dispatched in due-time order; and one clock may hold at most three quarters of
  the pool, so another clock's steps still find a thread. The interval has a
  floor. Proposed defaults: `max_clocks` 256, `max_clocks_per_tenant` 64,
  interval floor 0.02 s, pool 10 threads (Snake's measured pool, which reached
  its cap at 384 clients without a demonstrated problem, `docs/deploy-k8s.md`;
  the size is a setting). A step that never returns holds its thread until it does; the
  per-clock cap and the stuck report bound the damage but cannot free it. Open
  question 8 asks the owner to approve these.
- **Tenant isolation.** See "Tenant scoping (requirement)" above: scoped keys,
  scoped publish, a per-tenant cap, and a test with two tenants sharing a room
  name.
- **Who may receive.** A clock does not authorise membership: the app authorises
  a session for a room before its view assigns `push_scope`. The doorbell carries
  no room state, so a member learns only that something changed; the state it
  then reads is whatever the app's snapshot returns for that member. Tenant
  scoping of clock keys and publish scopes must be specified and tested.
- **Rate.** A clock is server-driven and bypasses the per-connection event rate
  limit by design. Because the doorbell is beat-invariant, a fast clock cannot
  grow a slow session's queue beyond one entry per key.
- **Logging.** Step failures go through the log-exposure-safe helper, never a
  raw exception with room state.

### Operations

- **Redis is not required** for Phases 1 to 3. They run on the in-memory layer
  and, with `--loops N`, on `MultiLoopInMemoryChannelLayer`.
- **One process only** in Phases 1 to 3, as in the scaling guide: live in-process
  state is Option A or a single larger process, and the clock does not widen
  that. This includes `uvicorn --workers N` with an in-memory layer, where rooms
  and clocks silently split per worker; documented as unsupported.
- **Several pods without Phase 4.** A clock per pod would step the same room
  twice if a room's sessions span pods, and the room state would diverge. A
  startup check can warn when a `RoomClock` runs with a Redis channel layer and
  no declared ownership model; it cannot see `--workers N` (open question 9).
- **Deploys.** A restart stops every clock. The next `ensure` starts it, and
  because a state-restored session skips `mount`, that needs a backstop
  (Snake's 5 s tick in Phase 1; Phase 2's per-connection start). `run_id` changes,
  so a consumer sees the restart.

### Observability

- Logger `djust.clocks`: INFO on start and stop with the reason (Snake logs
  "room clock started for ..." and "stopped (reason)").
- `RoomClock.stats()` per key: steps, skipped beats, overruns, last and max step
  duration, run age, `seq`, `run_id`, stuck, consecutive errors, stop reason.
  Surfacing it in the existing observability views and in `djust_audit` is open
  question 13.
- A WARNING, rate limited, when beats are late by more than an interval several
  times in a row (an overloaded loop or pool).

### API surface and compatibility

- One new module, `djust.clocks`: `RoomClock`, `ClockTick`, `Publish`, `Stop`, and
  a testing helper in `djust.testing`. Phase 2 adds one optional view attribute;
  Phase 3 adds `SharedPoll`.
- No change to `LiveView`, `tick_interval`, the runtime or the consumer in
  Phase 1.
- `djust.clocks` would be documented in `docs/website/advanced/server-push.md`
  ("Periodic Tick") and in the multi-loop guide, replacing the prose recipe in
  rule 2 with a pointer.

## Consequences

**Positive**
- Work per room per second is independent of the number of members. For Snake's
  measured load runs (rooms of 4, 192 to 256 clients, 48 to 64 rooms at 10 Hz),
  by arithmetic: 480 to 640 clock beats a second where the host-election design
  woke 1,920 to 2,560 session ticks a second, each running a handler that mostly
  returned early. The saving is wasted wakeups; renders on change are unchanged.
- The three race guards in Snake's `clock.py`, the locked start and stop, the
  thread pool, the stop conditions and the skip policy live in one tested module.
  Snake's clock module shrinks to its `step`, its `alive` and a registration.
- Emberdeep loses its captured loop and its unscoped push; Encounter can drop its
  per-session 20 Hz tick.
- A lobby or a deployment-status page can poll once per interval for all
  viewers (Phase 3).

**Negative / risks**
- A new public API to keep stable. The step signature and `ClockTick` fields are
  the contract.
- The registry is per process. Anyone who reads "room clock" as "works across
  pods" will be wrong; the documentation and a check have to say so.
- A shared worker pool can be saturated by slow steps; a room's beats then slip.
  The overrun metrics exist for this. Snake's own load run saw the clock pool
  reach its cap of 10 at 384 clients and could not rule out beats queueing there
  (`docs/deploy-k8s.md`, rc4 analysis).
- Doorbell plus snapshot pushes the snapshot discipline onto the app. A snapshot
  that is not in memory (a database or Redis read) is read by every member on
  every doorbell: N reads per beat. The framework does not provide a room-state
  store; caching one snapshot per `(room, version)` is the app's job, and is the
  shared-fragment direction in Snake's `djust-improvements.md`, section 2.
- The step does the work once, but every changed beat still renders every
  session. An app that wants the render cost down needs that same work.
- The sync step timeout cannot reclaim a thread.

**Neutral**
- Per-session `tick_interval` stays, with its cost, for apps that do not opt in.

## Alternatives rejected

1. **Shape 1** (leader view): rebuilds host election in the runtime.
2. **Shape 3 as the engine** (`tick_scope = "room"`): keeps N timers and needs a
   per-beat dedupe; its declarative form survives as Phase 2 sugar.
3. **Shape 4 alone**: no shared tested implementation; keeps three divergent
   clocks. A lease helper is kept as Phase 4, after the state-ownership decision.
4. **Making `tick_interval` mutable per session or per room.** It does not fix
   ownership, and the interval is read from the class at mount by design
   (`runtime.py:663`).
5. **Carrying room state in the doorbell.** Pushes are queued and can be dropped
   under load; state must come from a snapshot.
6. **A beat-varying doorbell payload.** It defeats the identical-event coalescing
   (`websocket.py:5934`), so a busy session runs the handler once per queued beat.
7. **Exactly-once step delivery across processes in the first release.** Needs a
   lease, fencing and a state-ownership model; none exist.

## Migration and compatibility

- Phase 1 is additive; nothing existing imports or changes.
- **Snake Arena** (the proof, done in its own repository). Each of its
  `test_clock.py` behaviours maps to the engine:

  | Snake test behaviour | Engine feature |
  |---|---|
  | one clock per room; join during the stop check; ensure between the idle check and the stop; two loops starting one room | locked start/stop with the request guard (engine tests) |
  | stops when the room is empty; an unreadable roster still lets the clock stop | `alive` and `idle_stop` (not alive and no newer `ensure`) |
  | stops when the room is reaped | `Stop("room reaped")` |
  | one failing step does not stop the clock | error isolation and breaker |
  | a finished match is pushed again | `trailing` |
  | beats run on the shared pool | the engine's pool |
  | the roster is read fresh every beat | stays an app test of `step` (the roster read is the app's) |
  | `ensure_clock` is a no-op when autostart is off | stays an app switch around `ensure` |

  The 5 s session tick keeps calling `ensure` as the backstop until Phase 2.
- **Emberdeep** and **Encounter** migrate by replacing the thread or the
  accumulator with a `RoomClock`, with `on_overrun="catch_up"` for Encounter.
- **djustlive** polling views stay on `tick_interval` until Phase 3's `SharedPoll`
  (or `db_notify` where a table is the source); changing them is a separate
  decision per view.
- Existing `tick_interval` views do not change behaviour. A view may call
  `ensure` from `mount` and also keep its tick.

## Phased plan and acceptance criteria

Criteria in group A are CI tests on the injected clock, with no wall-clock
waits, and each is gate-off verified. Group B are benchmarks reported in the pull
request, not gates: wall-clock measurements are the flake class of #2124.

### Phase 1: the engine, the smallest useful slice

Ship `djust.clocks.RoomClock` for one process (any number of loops), with the
injected time source, the testing helper, docs, and Snake migrated in its own
repo. No presence binding, no view attribute, no `SharedPoll`, no Redis.

**A. Deterministic (CI)**

1. **One step per slot, not per session.** With N = 1, 4 and 50 sessions of one
   room and 5 s of fake time at 0.1 s, the step ran 50 times (plus or minus
   one), independent of N. A per-session design fails this test.
2. **Single flight.** Two `ensure` calls racing from two threads and from two
   event loops produce exactly one task (the engine port of Snake's
   `test_two_loops_starting_one_room_at_once_start_one_clock`). A step slower than
   its interval never overlaps itself.
3. **Schedule.** Over 60 s of fake time at 0.1 s with a step that takes 20 ms of
   fake time, the step count is 600 (plus or minus one) and the scheduled time of
   step k is `start + k * interval`. A sleep-after-work loop (Emberdeep's) gives
   about 500 and fails.
4. **Late-beat policy.** After a 0.55 s stall at 0.1 s with beats due at 0.1
   through 0.5: `skip` runs one step with `skipped = 4` and `dt = 0.5`;
   `catch_up` with `max_catch_up = 3` runs three steps with `dt = 0.1` each and
   reports `skipped = 2`. No burst beyond the cap, and the schedule resumes on
   the grid.
5. **Lifecycle.** The clock stops when `alive` is false for `idle_stop` and no
   `ensure` came after the first false reading, and when a step returns `Stop`;
   an unreadable `alive` counts as false. With heartbeats only, it stops after
   `idle_stop` without an `ensure`. It restarts on the next `ensure` with a new
   `run_id`. An `ensure` that lands during a stop produces one running clock,
   never zero. After 1,000 create-and-idle cycles the number of tasks and
   registry entries returns to its baseline.
6. **Errors.** A raising step is logged once per rate-limit window, the next beat
   runs, and after the error threshold the beats back off and a success resets
   them. A sync step runs on a worker thread (asserted by thread identity with an
   event, not a sleep), and a stuck sync step is reported and not overlapped.
7. **Delivery.** The doorbell for a key is the same event on every beat, so a
   session held busy for 50 beats has one queued entry and runs the handler once
   when it drains (against the real deferred queue); a clock started from inside
   an event handler delivers its doorbells to that handler's session (clean
   context); the trailing doorbell arrives once after the last change.
8. **Isolation and caps.** Two rooms with two sessions each: each session's
   handler runs only for its room's beats. Two tenants using the same room name
   get two clocks and no cross-delivery. `max_clocks` and `max_clocks_per_tenant`
   refuse the next key and log once; one tenant at its cap does not block another.
   `ensure` from a plain thread raises instead of starting a clock that dies.
9. **Snake Arena.** With the engine in place, `clock.py` contains no lock, task
   registry, executor or schedule arithmetic, and the behaviours in the migration
   table above pass against the engine. This does not depend on `SharedPoll`.
10. **Context and tenant.** With a task factory that ignores `context=` and one
    that rejects it, a clock started from inside an event handler still has a
    clean context (no `origin_channel`, so its doorbells reach the starter); a
    step and an `alive` call see the tenant that `ensure` captured and no other;
    two tenants with the same room name never see each other's state through
    `tick.scope`.
11. **Logging.** A step that raises with room state in its message leaves only the
    exception type and the clock's message in the log records (no message text,
    no traceback) by default, and the full exception with `log_details=True`.
12. **Serving loop and tests.** `ensure` from a plain thread raises; a registry
    entry whose loop was closed is treated as dead and the next `ensure` restarts
    the clock; with the testing no-op helper active, `ensure` does nothing and a
    `LiveViewTestClient` mount works.
13. **Pool calls.** Steps and `alive` run with `close_old_connections()` around
    them; `alive` never runs on the loop; `Stop` and an idle stop flush the
    trailing doorbell once, and a cancellation does not.

**B. Benchmarks (reported, non-gating)**

1. **Per-beat overhead.** Microseconds per beat of the engine (scheduling, lock,
   dispatch to the pool, stats) against a no-op step, and against Snake's current
   `clock.py` under the same harness. The target is no worse than Snake's loop.
   Absolute budgets are not set here: at 256 clocks and 10 Hz there are 2,560
   beats a second, so even 100 microseconds a beat is a quarter of a core, and the
   number is what decides whether the engine uses a timer wheel.
2. **Loop lag.** Event-loop lag under a 200 ms sync step stays small (reported;
   it is wall-clock, so it is not a gate).
3. **Timer wheel against a task per key** at 256 clocks: wakeups and CPU per
   second.

### Phase 2: presence binding

Acceptance (deterministic): a room's clock starts on a connection's track and on
a restored connection's `_restore_presence` without any `ensure` call from the
app, including for a user whose first connection is old (no join event fires);
two tabs of one user keep it running until both close (#3325); the binding does
at most one presence-count read per second per clock (asserted with a counting
backend); after a graceful leave of the last member the clock stops within
`idle_stop`, and after a crashed tab it stops within the presence timeout plus
`idle_stop`; Snake's session tick no longer calls `ensure`.

### Phase 3: variable interval, pause, shared poll

Acceptance: `set_interval` takes effect on the next slot without a restart; a
paused clock makes no step calls but keeps liveness; N viewers of one key run
one fetch per interval (asserted with a counting query) and read the same
result version; the result key is tenant-scoped and a viewer of another scope
never reads it.

### Phase 4: cross-process ownership (only after open questions 9 and 10)

Acceptance, on the scaling lab's setup (2 to 4 pods, Redis, no sticky routing),
reported as lab results and not CI gates:

- **Failover.** After a pod crash, the first step by the new owner starts within
  `ttl + takeover poll + 1 s` at p95 over 10 runs, whatever TTL the owner picks;
  after a graceful delete, within one takeover poll plus 1 s.
- **No double stepping.** In a run where the owner is paused longer than the TTL
  (`SIGSTOP`) and then resumed, the resumed owner makes no step, and an effect it
  attempted is rejected by the fencing check of a store that implements it.
- **Fail stopped.** With Redis unreachable for longer than the TTL, no pod steps a
  leased key, and stepping resumes after Redis returns.
- **State.** The behaviour of a room whose state was in the failed owner's memory
  is whatever open question 10 decides, and is tested.

Deterministic CI tests for Phase 4 cover the Lua scripts (acquire, renew,
release, token monotonicity) against a real Redis when one is available.

## Test plan

- **Injected time.** The engine takes one `now()` abstraction that covers both
  the loop's sleep scheduling and the `time.monotonic()` stamps (Snake uses both:
  `loop.time()` for the schedule, `time.monotonic()` for the request guard,
  `clock.py:92`, `:109`). The testing helper provides a manual clock whose
  `advance(seconds)` runs due beats deterministically, and a **deterministic
  executor** that runs a sync step inline at the beat (with a flag to run on a
  real thread for the one test that asserts thread identity). Scheduling tests
  never sleep (#2124, #1795).
- **Unit.** Scheduling arithmetic, both late-beat policies, `dt` / `seq` /
  `run_id`, `alive`, `Stop`, trailing doorbell, breaker, key validation, caps,
  tenant scoping.
- **Concurrency.** Threads and two real event loops against one registry, in the
  style of `test_multiloop_sessions_3128.py` and `test_multiloop_serve_3128.py`,
  plus a stress test that explores stop-versus-ensure interleavings.
- **Integration.** `WebsocketCommunicator` mounts: rooms and sessions as in
  criterion 8, a real scoped push, a late joiner reading a snapshot, a busy
  session draining the real deferred queue, a clock started from an event
  handler.
- **Gate-off.** Each criterion's test is run once with its fix removed
  (per-session ticking, sleep-after-work, no lock, varying payload, inherited
  context, `create_task(context=)` instead of `Context().run`, ambient tenant,
  details-allowed logging, unscoped push) and must fail.
- **Testing helpers.** `djust.testing` provides the manual clock, the
  deterministic executor, and a no-op switch for `ensure` (the generalisation of
  Snake's `AUTOSTART`), so `LiveViewTestClient` mounts, which run outside a
  serving loop, do not start clocks or raise.
- **Phase 4.** Lease scripts against a real Redis (skipped when absent), a
  simulated pause by withholding renewals, and the lab timings (not in CI).
- **Pins, not promises.** Source-text pins for the one-task-per-key rule and the
  server-only `ensure` use the real code path, not a re-implemented condition.

## Open questions

Each is for the maintainer. None is decided here.

1. **Loop ownership.** The session loop (proposed for Phase 1, `ensure` valid
   only on a serving loop) or a dedicated clock thread and loop (valid from any
   thread, isolates jitter, needs a loop-safe layer or a hop). Also whether
   `ensure` from a non-serving context should raise (proposed) or start a
   dedicated loop lazily.
2. **Interface and stability.** A function-of-a-key engine with a later
   declarative attribute (proposed), the attribute alone, or the engine alone;
   the names `djust.clocks`, `RoomClock`, `Publish`, `Stop`, `ClockTick`; and
   whether `djust.clocks` is public from the first release or marked experimental
   in `api-stability.md` for one minor.
3. **Delivery contract.** Is "step runs once per slot or is skipped and counted;
   the doorbell is beat-invariant, ordered, bounded and coalesced when identical;
   state comes from the app's snapshot; a trailing doorbell heals a lost last
   one" the guarantee to document? Or should the framework change the deferred
   queue to coalesce by `(group, handler)` for flagged events, so a doorbell can
   carry `seq`?
4. **Default late-beat policy.** `skip` or bounded `catch_up`, and the default
   `max_catch_up`.
5. **Determinism.** Is `seq`, `run_id` and `dt` all the framework offers, or
   should it offer a per-run seed so replays and tests are reproducible?
6. **Liveness defaults.** The `alive` cadence (proposed 1 s), the default
   `idle_stop`, and whether an unreadable `alive` stops the clock (proposed, as
   Snake does), and whether `Stop` and an idle stop flush the trailing doorbell
   at once (proposed) or wait out its delay.
7. **Async steps and the pool.** Whether `async def` steps are in Phase 1, and
   the pool's relationship to `LIVEVIEW_CONFIG["worker_threads"]` (a separate
   framework pool is proposed) and to free-threaded and GIL builds.
8. **Limits and breaker policy.** Approval of the proposed defaults (256 clocks,
   64 per tenant, 0.02 s floor, 10 pool threads, 10 errors then backoff to 30 s),
   whether a clock that fails forever ever stops (after how long), and whether a
   stuck sync step should stop the clock.
9. **Multi-process scope.** Is Phase 4 in scope at all? If yes, which ownership
   model is supported: rooms routed to one pod (scaling guide, Option A, which
   has a routing gap today), state externalised to Redis or the database (a
   per-beat round trip), or a room-actor protocol; who enforces the fencing token
   (a framework helper or the app); and whether `RoomClock` should warn or refuse
   with several pods and in-memory rooms.
10. **Owner loss, Redis loss and timing.** On owner death, does the room resume
    from a snapshot or restart empty? When Redis is unreachable, do clocks stop
    (fail stopped, proposed) or continue on the last owner? Which lease source
    (Redis, Postgres advisory lock, Kubernetes Lease) and which TTL, given the
    trade between takeover time and flapping.
11. **Tenant scoping of clock keys and publish scopes** must be specified and
    tested (requirement above). Open: the exact helper wiring in the API (sketched
    with `clock.scope(view, key)`), and whether `clock.scope` is the only way to
    name a scope that a clock publishes to.
12. **Pause.** Does pausing live in the framework clock (membership and liveness
    continue, no step) or stay in app state (Snake's flag, with the clock still
    ticking to reap rooms)?
13. **Observability and nudges.** Where `stats()` surfaces (observability views,
    `djust_audit`, the debug panel), and whether a system check should suggest a
    clock to a view that sets `push_scope` or `presence_key` with a sub-second
    `tick_interval`.
14. **Release train.** Land Phases 1 and 2 in 1.3.x (opt-in, additive) or wait
    for the next minor.
15. **Tenant context for steps.** The engine sets the captured tenant as the
    current tenant inside the clock's context (proposed), or steps must filter by
    `tick.scope` explicitly and see no ambient tenant.
16. **Logging for ownerless steps.** Value-free by default, with a
    `log_details=True` opt-in per clock (proposed); whether the opt-in should
    exist at all.
17. **Serving-loop detection.** A djust registry of loops that consumers register
    (proposed), asgiref's private main-loop state, or accepting any running loop
    and documenting the risk.
