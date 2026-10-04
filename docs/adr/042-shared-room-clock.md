# ADR-042: A shared room clock

**Status**: Proposed
**Date**: 2026-10-04
**Deciders**: Project maintainers
**Related**:
- Issue #3005: a clock for shared state, one tick per room instead of per session (the maintainer asked for this ADR before any implementation)
- #3004 (scoped push), #3095 (scoped presence broadcast), #3001 (deferred server push), #3128 (several event loops per process), #3254 / #3325 (per-connection presence), #2973 / #3324 (tenant-keyed state and presence)
- `python/djust/runtime.py` (`maybe_start_tick_task`), `python/djust/websocket.py` (`_run_tick`, `_tick_once`), `python/djust/push.py`, `python/djust/presence.py`, `python/djust/multiloop.py`, `python/djust/layers.py`
- `docs/website/guides/scaling.md` (Option A and Option B, failover) and `docs/website/guides/scaling-across-cores.md` ("More than one event loop per process")
- ADR-041: the format and release-plan convention this ADR follows

**No code in this ADR exists in any release.** Every snippet is an illustrative
sketch of a proposed shape. Nothing here changes `tick_interval` or
`handle_tick`.

---

## Release plan this ADR assumes

- **1.3 takes opt-in changes only.** Phase 1 and Phase 2 below are additive and
  change no existing behaviour, so they can land in a 1.3.x or 1.4 release; the
  maintainer picks (open question 14).
- **Multi-process ownership (Phase 4) is a separate decision.** It needs a
  state-ownership answer that this ADR does not give (open questions 9 and 10).

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
- runs `handle_tick` through `sync_to_async` on the session's thread, with the
  change-detection snapshots around it unless the handler sets `_skip_render`.

So a tick is owned by one browser tab, runs when that tab is idle, and is lost
when that tab is busy. That is the right model for "refresh my dashboard every
30 s". It is not a clock for state that several sessions share.

### What exists to build on

- **Scoped push** (`push.py`): `push_to_view(..., scope=room)` and
  `apush_to_view` reach only the sessions whose view set `push_scope` to that
  value. The group name is a digest of the view path and the scope
  (`push_scope_group_name`, `:63`). The scope is **not** tenant-aware.
- **Server-push delivery** (`websocket.py:5493`, `_defer_server_push` at `:5913`):
  a push that finds the session busy is queued, an identical queued push is
  superseded by the later one, and the queue holds at most 64 entries
  (`_MAX_DEFERRED_PUSHES`, `:53`) and drops the oldest past that. The code
  comment names "a 100 ms room clock" as the reason. Delivery is **latest
  wins, not exactly once.**
- **Presence** (`presence.py`, `backends/`): per-connection records aggregated
  per user (#3325), a 60 s timeout (`presence.py:89`), `handle_presence_join` /
  `handle_presence_leave` on a user's first and last connection, and the tenant
  prefix applied to a presence key by `tenant_scoped_presence_key` (`:93`).
- **Several event loops** (`multiloop.py`, #3128): sessions of one room can be
  on different loops, so room-wide background work must be one task per room
  started under a `threading.Lock`, must not cache the running loop, and
  reaches sessions only by pushing (`docs/website/guides/scaling-across-cores.md`,
  rules 2 and 3).
- **Worker threads** (`worker_pool.py`): `LIVEVIEW_CONFIG["worker_threads"]`
  pins each session to a thread. A clock step is not a session and must not
  borrow a session's thread.
- **Cross-process transport**: a Redis channel layer and a Redis presence
  backend. There is **no lease, lock or leader-election primitive** anywhere in
  `python/djust` (searched for `lease`, `nx=True`, `redlock`, `fencing`,
  `leader`).
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
- Now: `snake_arena/clock.py` (202 lines), one asyncio task per room,
  started from the WebSocket branch of `SnakeGameView.mount`
  (`views.py:284-291`, never from the HTTP prerender) and pushing with
  `apush_to_view(..., scope=room)` (`clock.py:147`). It hand-builds:
  - drift-free scheduling against the loop clock, and **skip, don't burst** when
    more than one interval behind (`clock.py:96-103`);
  - steps in a shared thread pool of 10 (`clock.py:47`), because
    `sync_to_async` without an executor made up to 20 threads per loop;
  - start-on-first-member with a race guard (`_REQUESTED`, `clock.py:128`) and
    idle stop after 5 s with a locked re-check (`_stop_if_idle`, `:163`),
    because two sessions of one room can ask on two loops at once (#3128);
  - a **backstop**: every session's 5 s tick calls `ensure_clock`
    (`views.py:632-638`), so whatever stops a clock, a player still present
    restarts it;
  - error isolation (one failing beat is logged and the clock continues,
    `:114`), and a final-frame **resend** one second after a match ends because
    a dropped push would leave the last frame stale (`:137-142`; djust queues
    such pushes since #3001, so the resend is now a backstop);
  - a presence roster read **every beat** (`clock.py:68`, `:84`), because `prune`
    drops queued players who are missing from it. With the Redis presence
    backend that is a sorted-set read ten times a second per room.
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
  (`views.py:_publish_if_changed` docstring): the clock removes N *steps*, not N
  *renders*. Renders are a separate cost (`docs/djust-improvements.md`,
  section 2).

**Emberdeep** (`dungeon-arena/emberdeep/emberdeep/`, a co-op dungeon).

- The world clock is a `threading.Thread` per run that does `time.sleep(TICK_MS)`
  and then ticks (`world.py:483-485`, `TICK_MS = 200` at `:19`). The period is
  the interval **plus** the step time, so it drifts; there is no skip policy.
- The comment above it records why it left the elected host: "A client-elected
  host is fragile: if the host's socket flaps ... tick_count freezes and the
  whole dungeon deadlocks" (`world.py:459-465`).
- To push from a foreign thread it stores the loop in a module global,
  `_MAIN_LOOP` (`views.py:31-56`). Under `djust serve --loops N` that is one
  loop of several, and the pattern is the one rule 3 of the multi-loop guide
  forbids ("don't cache the running loop").
- Its push is unscoped: `push_to_view(VIEW_PATH, handler="refresh_run",
  payload={"channel": ...})` (`views.py:45-51`, `:103`, `:330`) wakes every session of
  the view in every channel.
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
deployment status every 3 s and deployment logs every 1.5 s
(`djustlive/dashboard/views.py:257`, `:429`, `:606`, `:849`), operations pages
every 10 s (`djustlive/ops/views.py:139`, `:375`, `:512`, `:610`). N viewers of
one deployment run N identical queries. These are not games: no shared mutable
state, no ordering, a dropped beat is harmless, and the poll should stop when
nobody is looking. They want "do this once per interval for everyone watching
key K" and nothing more.

### What the apps need from a framework clock

| Need | Snake Arena | Emberdeep | Encounter | djustlive polls |
|---|---|---|---|---|
| Rate | fixed 0.1 s | fixed 0.2 s | fixed 0.05 s | 1.5 to 10 s, ideally changes (1.5 s while a deploy runs) |
| Start | first WS member | first member (`get_run`) | first session | first viewer |
| Stop | 5 s after last member | 60 s after last human | room TTL 600 s | last viewer |
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
3. **Honest delivery.** The channel layer is best effort and server push is
   latest-wins (`websocket.py:5913`). The design must not promise exactly-once
   delivery to sessions.
4. **Correct under `--loops N`.** One task per key across loops, no cached loop.
5. **Tenant and room isolation.** A key must not cross tenants; a client must
   not be able to start clocks at will.
6. **No new infrastructure by default.** The single-process case (which is
   Snake, Emberdeep and Encounter) works with the in-memory layer and no Redis.
7. **Additive.** Per-session `tick_interval` and `handle_tick` keep their
   meaning; no existing app changes unless it opts in.
8. **Testable without sleeping.** The scheduler must run against an injected
   clock (the repo already treats wall-clock waits as flaky: #2124).
9. **Do not pretend multi-process is solved.** A lease says who steps. It does
   not say where the room's state lives.

## Options

Four options were asked for. Each is described by what runs, then compared.

### Option A: framework-owned task per room, delivered to ONE leader view

The framework starts a task per room key and, each beat, delivers
`handle_room_tick` to one chosen member session (the leader) through the
channel layer. Across pods the leader is chosen by a Redis lease or by sticky
routing.

- A "leader view" is a session. When its tab reloads, is rate-limited, or is
  busy, beats stop or are skipped (`_tick_once` skips a busy session). Emberdeep
  left the elected host for that reason (`world.py:459-465`). The framework
  would be re-implementing host election, with the same takeover problem,
  inside the runtime.
- The step would run on a session's thread under that session's render lock,
  coupling the room's rate to one viewer.
- It would also deliver a step command through a channel that coalesces and
  drops (`websocket.py:5913`).

### Option B: `RoomClock`, a helper the app owns, with an explicit lifecycle

A library class (`djust.clocks.RoomClock`) that is the engine Snake wrote by
hand: a registry of one supervised task per key, `ensure(key)` to start it
idempotently, idle stop, drift-free scheduling with a declared late-beat policy,
steps in a worker thread, error isolation, a doorbell publish through scoped
push. The step is a **function of the key**, not a method of a view. The app
calls `ensure` from `mount` (WebSocket branch). A later phase binds the
lifecycle to presence.

- Maps one-to-one onto working code (Snake `clock.py`) and onto the multi-loop
  rules already documented.
- Explicit: the app decides what a room is, who may start one and what the step
  does.
- Does nothing about processes: the registry is per process.

### Option C: `tick_scope = "room"` on the view, group-broadcast tick with dedupe

The view declares `tick_scope = "room"`. Every member keeps a tick task, the
tick is broadcast to the room group, and a dedupe (an in-process lock, or a
Redis `SET NX` per beat across pods) lets the first receiver run
`handle_tick`.

- Timers do not go away: N sessions still run N timers and N wakeups per beat,
  so the dominant cost in Snake's "before" figures stays.
- A dedupe per beat across pods is one Redis operation per beat per room (10 a
  second at 10 Hz) on the hot path.
- Whoever wins a beat is a session, with Option A's problems; a busy winner
  skips the beat for the whole room.
- It is the smallest API change, and it is declarative, which is its appeal.

### Option D: leave it to app code, document it, add a lease helper

Document the Snake pattern (the multi-loop guide already does, in prose), and
ship only a small `djust.leases` helper for apps that want cross-process
ownership.

- No clock API to stabilise.
- Every app keeps its own scheduler, and the three in this workspace show how
  that goes: three designs, one with drift and a captured loop, one that never
  left the per-session tick.
- A lease helper without a state-ownership model invites split brain (below).

### Comparison

| | A: leader view | B: `RoomClock` | C: `tick_scope="room"` | D: docs + lease |
|---|---|---|---|---|
| Steps per beat | 1 | 1 | 1 (N timers wake) | app's |
| Owner of the clock | a session | the registry | a session | the app |
| Leader/host dies mid-game | beats stop until re-election; same bug class as Snake's old host takeover | no leader; the process owns it (process death = Phase 4) | as A | app's |
| Split brain | possible with Redis lease, needs fencing | impossible in one process; Phase 4 needs lease plus fencing | dedupe window can double-step | app's |
| Network partition (Redis unreachable) | leader can't prove leadership | single process unaffected; Phase 4 fails stopped (open question 10) | dedupe fails open or closed, app can't tell | app's |
| Tick delivery | at-most-once to one session, coalesced | step runs once per slot or is skipped and counted; doorbell is latest-wins | at-least-once with dedupe, coalesced | app's |
| Latency/jitter | one channel hop plus a session's render lock | schedule on the loop clock; jitter is loop lag | channel hop plus lock | app's |
| Works with `--loops N` | needs cross-loop leader | one task, started under a lock | per-session, any loop | app's |
| Tenant/DoS surface | key from the view | `ensure` is server code only, caps, tenant-prefixed key | key from the view | app's |
| API surface | new view hook + election rules | one module, one class | one view attribute + dedupe contract | docs + helper |
| Effort | high | medium | low-medium | low |
| Verdict | **reject** | **recommend** | reject as the engine; revisit as sugar | reject as the whole answer |

## Decision (PROPOSED: the maintainer decides)

### 1. Build Option B, and keep its step a function of a key

Add `djust.clocks` with a `RoomClock` registry. The clock is process-owned, not
session-owned. It is the generalisation of Snake's `clock.py`, so the first
user is the app that exposed the need.

Illustrative sketch, **not an available API**:

```python
# ILLUSTRATIVE: proposed shape, not available in any release.
from djust.clocks import RoomClock, ClockTick, Publish

def snake_beat(tick: ClockTick) -> bool:
    """One beat for one room. Runs in a worker thread and never overlaps
    itself for the same key. Truthy return means "publish a doorbell"."""
    game = peek_game(tick.room)
    before = game.version
    game.tick()
    return game.version != before

snake_clock = RoomClock(
    name="snake",                    # namespaces the keys of this clock
    interval=0.1,                    # seconds
    step=snake_beat,
    on_overrun="skip",               # "skip" | "catch_up" (bounded by max_catch_up)
    idle_stop=5.0,                   # seconds without ensure() or members
    publish=Publish("snake_arena.views.SnakeGameView", handler="handle_refresh_room"),
    max_clocks=256,                  # per process
)

# SnakeGameView.mount, WebSocket branch only, after the seat is authorised:
snake_clock.ensure(room)             # idempotent; starts the clock if needed

# ClockTick: room, seq, run_id, dt, skipped, scheduled_at, started_at.
# snake_clock.running(room), snake_clock.stop(room), snake_clock.stats()
```

Contract of the engine:

- **One task per key per process**, created under a `threading.Lock` together
  with the request timestamp, with the stop decided under the same lock. This is
  Snake's `_ensure` / `_stop_if_idle` pair, written once and tested once.
- **Fixed-rate schedule against the loop clock** (`next_at += interval`), so
  step time does not accumulate into the period.
- **Late-beat policy is declared, not implied.** `"skip"`: if the clock is more
  than one interval behind, drop the missed beats, count them in
  `tick.skipped`, and resume (Snake). `"catch_up"`: run up to `max_catch_up`
  steps with the same `dt` (Encounter's cap of three), drop the rest. Never an
  unbounded burst.
- **`tick.dt`** is the monotonic seconds since the previous step began, so a
  step that needs elapsed time (a countdown, a match timer) does not assume
  every callback was exactly one interval apart.
- **`tick.seq` and `tick.run_id`.** `seq` counts steps within a run; `run_id`
  changes when the clock restarts. A consumer can tell "next beat" from
  "the clock restarted". The framework supplies the sequence and the monotonic
  time and nothing else toward determinism: random seeds stay the app's.
- **Steps run in a shared worker pool**, one pool per process (not per loop), so
  a slow step cannot block the event loop and thread count does not multiply
  with `--loops N`. Size and executor choice are open question 7.
- **One step at a time per key.** A step that outlives its interval is not
  overlapped; the next beat follows the overrun policy.
- **Errors are logged and the clock continues.** Logging uses the
  log-exposure-safe helper, rate-limited per key. A step that exceeds a
  `step_timeout` is reported; whether it is cancelled is open question 8.
- **Doorbell publish.** A truthy step result makes the clock
  `apush_to_view(path, handler=..., payload={"room", "seq", "run_id"}, scope=<tenant-scoped key>)`.
  The handler re-reads the app's snapshot. The framework does not carry room
  state in the push, because pushes are coalesced and dropped
  (`websocket.py:5913`). A reconnecting or late-joining session already gets the
  current state from `mount`.
- **Server-only start.** `ensure` is a server-side call. There is no event
  handler, decorator or client parameter that starts a clock.

### 2. Phase 2: bind the lifecycle to presence, then optional declarative sugar

Once `RoomClock` exists, tie it to presence (#3325) so an app stops writing the
backstop:

```python
# ILLUSTRATIVE: proposed shape, not available in any release.
class SnakeGameView(PresenceMixin, LiveView):
    presence_key = "snake:{room}"
    room_clock = snake_clock        # starts on the first presence join for the key,
                                    # stops idle_stop seconds after the last leave
```

The binding reads the presence **count**, not the roster, at a coarse cadence
(proposed once a second, not every beat), and restarts on the next join. A step
that needs the roster, as Snake's `prune` does, reads it itself. This is sugar
over Option B, which is the form of Option C that survives: declarative, with the
clock still process-owned rather than session-owned.

### 3. Phase 3: variable interval, pause, shared poll

- `clock.set_interval(key, seconds)` for clocks that change rate (djustlive's
  1.5 s while a deployment runs, slower afterwards) and `pause(key)` /
  `resume(key)` that keep membership and liveness running while the step is not
  called (Snake pauses inside the game and keeps the clock running to reap and
  to keep the room alive; whether that moves into the framework is open
  question 12).
- A `shared_poll` convenience: one query per interval per key, fanned out as a
  doorbell, for the djustlive-style views.

### 4. Phase 4 (separate decision): cross-process ownership

Not recommended for the first release. Sketch of what it would need, so the
trade-off is visible:

- **Lease.** A Redis lease per key (`SET key token NX PX ttl`), renewed every
  `ttl/3` by compare-and-extend, released on graceful stop by compare-and-delete.
  A fencing counter (`INCR`) is attached to each acquisition.
- **Fail stopped.** An owner stops stepping when it cannot renew within
  `ttl - margin` by its own monotonic clock, so a paused or partitioned owner
  stops before another owner can start. It cannot make a side effect it already
  started safe: effects (database writes) must carry the fencing token and
  reject a stale one.
- **Failover time.** A crash is detected after `ttl` plus a takeover poll of
  `ttl/3`. With `ttl = 2 s` that is at most about 2.7 s; a graceful release is
  taken over within one poll (about 0.7 s). That sits inside the 1 to 3 s
  reconnect window measured for pod loss (1.3 to 1.5 s graceful, 1.5 to 2.9 s
  crash; `docs/website/guides/scaling.md`, "Failover and deploys"), which is the
  target in the acceptance criteria.
- **A lease decides who steps, not where the room lives.** If room state is in
  the owner's memory (Snake, Emberdeep, Encounter), members on other pods
  cannot read it and their commands cannot reach it: that needs a room-actor
  protocol (commands forwarded to the owner, frames back) which this ADR does
  not design. If room state is in Redis or the database, any pod can step, at the
  price of a state read-modify-write per beat. Today's supported answer for live
  in-process objects is Option A of the scaling guide, routing each room to one
  process, and it stays that. Open questions 9 and 10.
- **Single-process fallback.** With the in-memory channel layer (or no lease
  backend configured) `ensure` takes the in-process lock and nothing else. The
  API is the same.

### What this does not change

- `tick_interval` and `handle_tick` keep their per-session meaning, and
  `maybe_start_tick_task` is untouched. A view may use both: Snake keeps a 5 s
  session tick for its presence heartbeat.
- No deprecation of per-session ticks is proposed. A later system check may
  suggest `RoomClock` for a view that sets `push_scope` or `presence_key` and a
  `tick_interval` below one second (open question 13).

## Cross-cutting design

### Security

- **Who may start a clock.** Only server code, after the room is authorised, and
  never from the HTTP prerender. Snake gates on the WebSocket branch
  (`views.py:284`), and rooms are reachable by an unauthenticated
  `GET /r/<anything>/` (`game.py` `reap_idle_rooms` docstring), so the clock
  must not be a free side effect of a GET. `ensure` also refuses a key that
  fails the key validation the app declares (a slug rule), as `slugify_room`
  does.
- **DoS by many rooms.** A per-process `max_clocks` cap with a defined behaviour
  at the cap (refuse and log, never queue), a floor on `interval`, and an idle
  stop. A hostile client can create at most as many clocks as the app lets it
  create rooms, and the framework adds the cap as a second line. Defaults are
  open question 8. The 10-WebSockets-per-IP limit already bounds one client.
- **Tenant isolation.** The clock key is the tenant-prefixed key, using the
  same helper rule as presence (`tenant:<id>:...`, `tenant_scoped_presence_key`,
  #3324), applied inside `ensure` so an app cannot forget it. **Finding:**
  `push_scope_group_name` (`push.py:63`) digests the view path and the scope
  only, with no tenant. A tenant view that sets `push_scope = room` shares a
  push group with another tenant's room of the same name. The clock's publish
  must use the tenant-scoped key as its scope; whether the framework should
  prefix tenant on `push_scope` for every tenant view is a separate decision
  (open question 11).
- **Who may receive.** Joining a scope is the session assigning
  `self.push_scope`; nothing checks that the session may be in that room. A
  clock does not change that, and its doorbell carries no room state, so the
  worst a wrong member sees is a wake-up. The state a wrong member could read
  is whatever the app's `mount` shows, as today.
- **Rate limits.** A clock is server-driven and bypasses the per-connection
  event rate limit by design; the doorbell is coalesced per session
  (`websocket.py:5913`), so a fast clock cannot grow a slow session's queue past
  64 entries.
- **Logging.** Step failures go through the log-exposure-safe helper, never a
  raw exception with room state.

### Operations

- **Redis is not required** for Phases 1 to 3. They run on the in-memory
  layer, and on `MultiLoopInMemoryChannelLayer` with `--loops N`.
- **One process only** in Phases 1 to 3. This matches the guidance in
  `scaling.md` that live in-process state is Option A or a single larger
  process; the clock does not widen that.
- **Several pods without Phase 4.** A clock per pod would step the same room
  twice if a room's sessions span pods, and the room state would diverge. The
  documentation must say that a deployment with more than one pod and
  in-memory rooms needs room routing (Option A) first. A startup check can
  warn when `RoomClock` is used with a Redis channel layer and no declared
  ownership model (open question 9).
- **Deploys.** A restart stops every clock. The next `ensure` (a reconnecting
  member's mount, or the presence join) starts it. `run_id` changes, so a
  consumer sees the restart.

### Observability

- Logger `djust.clocks`: INFO on start and stop with the reason (Snake logs
  "room clock started for ..." and "stopped (reason)").
- `RoomClock.stats()` per key: steps, skipped beats, overruns, last step
  duration, max step duration, run age, `seq`, `run_id`. Surfacing it in the
  existing observability views and in `djust_audit` is open question 13.
- A WARNING, rate limited, when the lateness of beats exceeds an interval for
  several beats in a row (an overloaded loop or pool).

### API surface and compatibility

- One new module, `djust.clocks`: `RoomClock`, `ClockTick`, `Publish`, and a
  testing helper in `djust.testing`. Phase 2 adds one optional view attribute.
- No change to `LiveView`, `tick_interval`, the runtime or the consumer in
  Phase 1.
- `djust.clocks` would be documented in `server-push.md` ("Periodic Tick") and in
  the multi-loop guide, replacing the prose recipe in rule 2 with a pointer.

## Consequences

**Positive**
- Steps per room per second are independent of the number of members. For
  Snake's measured load runs (rooms of 4, 192 to 256 clients, 48 to 64 rooms
  at 10 Hz), by arithmetic: 480 to 640 steps a second where the host-election
  design ran 1,920 to 2,560 ticks a second.
- The three race guards in Snake's `clock.py`, the locked start and stop, the
  thread pool and the skip policy live in one tested module. Snake's clock
  module shrinks to its `step` function and a registration.
- Emberdeep loses its captured loop and its unscoped push; Encounter can drop
  its per-session 20 Hz tick.
- A lobby or a deployment-status page can poll once per interval for all
  viewers.

**Negative / risks**
- A new public API to keep stable. The step signature and `ClockTick` fields
  are the contract.
- The registry is per process. Anyone who reads "room clock" as "works across
  pods" will be wrong; the documentation and a check have to say so.
- A shared worker pool can be saturated by slow steps; a room's beats then
  slip. The overrun metrics exist for this. Snake's own load run saw the clock
  pool reach its cap of 10 at 384 clients and could not rule out beats queueing
  there (`docs/deploy-k8s.md`, rc4 analysis).
- Doorbell plus snapshot pushes the snapshot discipline onto the app. The
  framework does not provide a room-state store.
- It removes N steps, not N renders. An app that wants N renders to cost less
  needs the shared-fragment work (Snake `djust-improvements.md`, section 2).

**Neutral**
- Per-session `tick_interval` stays, with its cost, for apps that do not opt in.

## Alternatives rejected

1. **Option A** (leader view): rebuilds host election in the runtime. See above.
2. **Option C as the engine** (`tick_scope = "room"`): keeps N timers and needs
   a per-beat dedupe; its declarative form survives as Phase 2 sugar.
3. **Option D alone**: no shared tested implementation; keeps three divergent
   clocks. A lease helper is kept as Phase 4, after the state-ownership
   decision.
4. **Making `tick_interval` mutable per session or per room.** It does not fix
   ownership, and the interval is read from the class at mount by design
   (`runtime.py:663`).
5. **Carrying room state in the doorbell.** Pushes are coalesced and dropped
   under load; state must come from a snapshot.
6. **Exactly-once step delivery across processes in the first release.**
   Needs a lease, fencing and a state-ownership model; none exist.

## Migration and compatibility

- Phase 1 is additive; nothing existing imports or changes.
- **Snake Arena** (the proof): `clock.py`'s scheduling, registry, lock, executor
  and stop logic are replaced by a `RoomClock` instance; `step()`, `_persist`
  and the roster read stay in the app. Its `tests/test_clock.py` cases (one
  task per room, pushes on change, stops when idle or reaped, survives a
  failing step, resends the final frame) become the engine's tests; the final
  frame resend stays in the app unless the maintainer wants it in the framework
  (open question 3). This is done in the snake-arena repository, not here.
- **Emberdeep** and **Encounter** migrate by replacing the thread or the
  accumulator with a `RoomClock`, with `on_overrun="catch_up"` for Encounter.
- **djustlive** polling views stay on `tick_interval` until Phase 3's shared
  poll; changing them is a separate decision per view.
- Existing `tick_interval` views do not change behaviour. A view may call
  `ensure` from `mount` and also keep its tick.

## Phased plan and acceptance criteria

### Phase 1: the engine, the smallest useful slice

Ship `djust.clocks.RoomClock` for one process (any number of loops), with the
injected time source, the testing helper, docs, and Snake migrated in its own
repo. No presence binding, no view attribute, no Redis.

Acceptance criteria (each is a test, and each is gate-off verified):

1. **One step per slot, not per session.** With N = 1, 4 and 50 sessions pushing
   to one room for 5 s of fake time at 0.1 s, the step ran 50 times (plus or
   minus one), independent of N. A per-session design fails this test.
2. **Single flight.** Two `ensure` calls racing from two threads and from two
   event loops produce exactly one task (port of Snake's `test_multiloop`).
   A step slower than its interval never overlaps itself.
3. **Drift.** Over 60 s of fake time at 0.1 s with a 20 ms step, the step count
   is 600 (plus or minus one) and the scheduled time of step k is
   `start + k * interval`. A sleep-after-work loop (Emberdeep's) gives about 500
   and fails.
4. **Late-beat policy.** `skip`: a 350 ms stall at 100 ms runs one step and
   reports `skipped = 3`. `catch_up` with `max_catch_up = 3`: the same stall runs
   three steps and reports the rest skipped. No burst beyond the cap.
5. **Lifecycle.** The clock stops within `idle_stop` plus one interval of the last
   request, and restarts on the next `ensure` with a new `run_id`. After 1,000
   create-and-idle cycles the number of tasks and threads returns to its
   baseline. An `ensure` that lands during a stop produces one running clock,
   never zero (the race Snake's `_REQUESTED` guards).
6. **Errors.** A raising step is logged once per rate-limit window and the next
   beat runs. The step runs on a worker thread (asserted by thread identity),
   and a blocking step does not stall the event loop (loop lag under a 200 ms
   step stays under 20 ms).
7. **Isolation.** Two rooms with two sessions each: each session's handler is
   called only for its room's beats. Two tenants using the same room name get
   two clocks and no cross-delivery. `max_clocks` refuses the next key and logs
   once.
8. **Snake Arena.** With the engine in place, `clock.py` contains no lock, no
   task registry, no executor and no schedule arithmetic; its existing tests pass
   against the engine; a room with 10 tabs runs the step 10 times a second, as
   today, and the lobby (if moved to `shared_poll` in Phase 3) runs one presence
   scan per second for any number of viewers instead of one per viewer.
9. **Overhead.** Per-beat engine overhead (scheduling, lock, dispatch, stats)
   is measured against a no-op step and reported in the PR. The target (open
   question 8) is under 0.2 ms per beat at 10 Hz and under 1 % of a core at 256
   active clocks. This is a target to be measured, not a result.

### Phase 2: presence binding

Acceptance: a room's clock starts on the first presence join and stops
`idle_stop` after the last leave without any `ensure` call from the app;
two tabs of one user keep it running until both close (per-connection
presence, #3325); the binding performs at most one presence-count read per
second per clock (asserted with a counting backend); Snake's session tick no
longer calls `ensure_clock`.

### Phase 3: variable interval, pause, shared poll

Acceptance: `set_interval` takes effect on the next slot without a restart;
a paused clock makes no step calls but keeps liveness; N viewers of one key run
one poll per interval (asserted with a counting query).

### Phase 4: cross-process ownership (only after open questions 9 and 10)

Acceptance, on the scaling lab's setup (2 to 4 pods, Redis, no sticky routing):

- **Failover.** After a pod crash, the first step by the new owner starts within
  3 s at p95 over 10 runs; after a graceful delete, within 1.5 s. Both are within
  the measured client reconnect windows.
- **No double stepping.** In a run where the owner is paused longer than the
  lease TTL (`SIGSTOP`) and then resumed, the resumed owner makes no step and
  the effect it attempted is rejected by the fencing token.
- **Fail stopped.** With Redis unreachable for longer than the TTL, no pod steps
  a leased key, and stepping resumes after Redis returns.
- **State.** The behaviour of a room whose state was in the failed owner's
  memory is whatever open question 10 decides, and is tested.

## Test plan

- **Fake clock.** The engine takes a time source and a sleep function. The
  testing helper provides a manual clock whose `advance(seconds)` runs due
  steps deterministically; scheduling tests never sleep (#2124, #1795).
- **Unit.** Scheduling arithmetic, both overrun policies, `dt` / `seq` /
  `run_id`, idle stop, stop-versus-ensure interleavings (a stress test that
  explores orderings), key validation, cap, tenant prefixing.
- **Concurrency.** Threads and two real event loops against one registry, in the
  style of `test_multiloop_sessions_3128.py` and `test_multiloop_serve_3128.py`.
- **Integration.** `WebsocketCommunicator` mounts: rooms and sessions as in
  criterion 7, a real scoped push, a late joiner reading a snapshot, a busy
  session coalescing a fast doorbell to the latest.
- **Gate-off.** Each criterion's test is run once with its fix removed
  (per-session ticking, sleep-after-work, no lock, unscoped push) and must fail.
- **Phase 4.** Lease acquire, renew, expiry, release and fencing against a real
  Redis (skipped when absent), a simulated pause by withholding renewals, and
  the lab failover timings (not in CI).
- **Pins, not promises.** Source-text pins for the one-task-per-key rule and the
  server-only `ensure` use the real code path, not a re-implemented condition.

## Open questions

Each is for the maintainer. None is decided here.

1. **Interface shape.** A function-of-a-key engine with an optional declarative
   view attribute (proposed), the declarative attribute alone, or the engine
   alone. Names: `djust.clocks`, `RoomClock`, `Publish`, `ClockTick`.
2. **Stability tier.** Is `djust.clocks` public from the first release, or
   marked experimental in `api-stability.md` for one minor?
3. **Delivery contract.** Is "step runs once per slot or is skipped and counted;
   the doorbell is best effort and latest-wins; state comes from the app's
   snapshot" the guarantee to document? Should the framework also provide the
   final-frame resend (Snake) or leave it to the app?
4. **Default late-beat policy.** `skip` or bounded `catch_up`, and the default
   `max_catch_up`.
5. **Determinism.** Is `seq`, `run_id` and monotonic `dt` all the framework
   offers, or should it offer a per-run seed so replays and tests are
   reproducible? (Snake uses `random`; Emberdeep derives pseudo-random values from
   the tick count.)
6. **Liveness source.** `ensure` heartbeats from sessions (Snake's backstop),
   presence binding (Phase 2), or both; the presence-count cadence; the default
   `idle_stop` (5 s in Snake, about 60 s in Emberdeep).
7. **Worker threads.** A framework pool (size, and how it relates to
   `LIVEVIEW_CONFIG["worker_threads"]`), an app-supplied executor, or both;
   behaviour on GIL and free-threaded builds.
8. **Limits and defaults.** Default `max_clocks`, the minimum `interval`,
   `step_timeout` and whether a timed-out step is cancelled or only reported;
   the overhead target in criterion 9.
9. **Multi-process scope.** Is Phase 4 in scope at all? If yes, which ownership
   model is supported: rooms routed to one pod (scaling guide, Option A, which
   has a routing gap today), state externalised to Redis or the database (a
   per-beat round trip), or a room-actor protocol. Should `RoomClock` warn or
   refuse when used with several pods and in-memory rooms?
10. **Owner loss and Redis loss.** On owner death, does the room resume from a
    snapshot or restart empty? When Redis is unreachable, do clocks stop (fail
    stopped, proposed) or continue on the last owner?
11. **Push-scope tenancy.** `push_scope_group_name` has no tenant. Should the
    framework prefix the tenant for every tenant view's `push_scope`, as #3324
    did for presence keys and saved state? (A separate issue if yes; not filed
    by this ADR.)
12. **Pause.** Does pausing live in the framework clock (membership and liveness
    continue, no step) or stay in app state (Snake's flag, with the clock still
    ticking to reap rooms)?
13. **Observability and nudges.** Where `stats()` surfaces (observability views,
    `djust_audit`, the debug panel), and whether a system check should suggest a
    clock to a view that sets `push_scope` or `presence_key` with a sub-second
    `tick_interval`.
14. **Release train.** Land Phases 1 and 2 in 1.3.x (opt-in, additive) or wait
    for the next minor.
15. **Runtime-variable per-session interval.** `tick_interval` is fixed per
    class at mount. This ADR leaves that unchanged; say so explicitly or reopen
    it.
