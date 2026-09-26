---
title: "Scaling djust"
slug: scaling
section: guides
order: 13.4
level: advanced
description: "Decide how to scale a djust app: cut the cost per frame, use more cores in one process, bound memory, or run several processes or pods with Redis. Covers the settings multi-pod deployments need, failover and rolling deploys, measured capacity and a troubleshooting checklist."
---

# Scaling djust

By default one djust process does its LiveView work on about **one CPU core**. There are four ways past that, and they combine:

1. **Do less work per frame.** Often the cheapest gain.
2. **Use more cores in one process.** Free-threaded CPython plus djust's opt-in settings. In-process state such as game rooms keeps working.
3. **Bound memory**, so a process that holds more clients stays up.
4. **Run more processes or pods.** Redis carries messages, presence and sessions between them.

This guide is organised by decision. The in-depth reference for one process is [Scaling a djust Process Across Cores](scaling-across-cores.md); this guide links into it rather than repeating it.

Every number below comes from a specific app on specific hardware, and each one is given with its conditions. Treat them as the shape to expect, not as a promise for your app. Measure your own before sizing anything (see [Measuring your own](#measuring-your-own)).

## Start from the symptom

| Symptom | Usual cause | Go to |
|---|---|---|
| One core at 100 %, the others idle | the shared sync thread, the GIL or the event loop | [One process across cores](#one-process-across-cores) |
| High CPU per frame even with few clients | too much rendered or diffed per frame | [Cost per frame](#before-adding-cores-cost-per-frame) |
| High latency, low CPU | distance to users, database or other I/O waits | [Cost per frame](#latency-that-is-not-cpu) |
| RSS climbs and never falls | state TTL, per-thread heaps, garbage collection, the allocator | [Memory](#memory) |
| You need more than one host, or high availability | more processes or pods | [More than one process or pod](#more-than-one-process-or-pod), [Failover and deploys](#failover-and-deploys) |

## Before adding cores: cost per frame

### Measure CPU per delivered frame

The number that predicts capacity is **server CPU time per frame delivered to a client**. To measure it:

- read the process's CPU time (`ps -o cputime= -p <pid>`, or the cgroup's `cpu.stat` in a container) at the start and end of a fixed window of steady load;
- divide by the frames your clients received in that window;
- compare variants A/B with a fresh server process per run, interleave the runs, and do at least three of each. On a shared machine, record the load average with every run.

Be careful with profilers:

- **Deterministic profilers** (cProfile, yappi) over-attribute time to code made of many tiny calls. In one investigation yappi put most of the WebSocket dispatch time in state snapshots and fingerprints, while a stack sampler put about 5 % on fingerprinting.
- **py-spy** needs root on macOS and has no `--native` mode there. Without it, threads waiting for the GIL are sampled in the frame they wait in, so waiting shows up as work. One py-spy run put about 14 % on WebSocket compression and 7 % on a per-event import; A/B timing of both found no measurable CPU change. On Linux, `py-spy dump` and `top -H -p <pid>` show per-thread CPU.
- **On free-threaded 3.14t,** walking another thread's frames through `sys._current_frames()` can crash the interpreter. Sample the main thread with a `SIGALRM` handler instead.

### Render less

- **Large repetitive grids** (game boards, heat maps): render one data attribute that carries the state, and draw it on the client with a [`dj-hook`](hooks.md) on a `<canvas>`. In a multiplayer game this took the server's CPU per frame from 8.16 ms to 3.45–3.59 ms, and a steady frame became one attribute patch of about 140 bytes. Conditions: a shared dev machine (12-core Apple Silicon), CPython 3.12, djust 1.2.1, 16 clients, three interleaved runs per variant. The work moves to the browser: the client now paints the board.
- **Collapsed UI** (closed panels, hidden tabs): don't render its contents until it opens.

### The loop render cache

On djust 1.3 you don't need to tune it. A view whose `{% for %}` render cache hits less than 20 % of the time over 8 renders turns the cache off for that view instance (#3167). On a high-churn loop the cache had cost about 20 % at p50 and 32 % at p95 of render time in 1.2.1 (#3071). `LIVEVIEW_CONFIG["loop_render_cache_enabled"]` still turns it off globally.

### Latency that is not CPU

If p95 latency is high while the process is far from a full core, the time is going elsewhere:

- **Distance.** Measure TCP connect time from where your users are. One app moved from a host with a best TCP connect of 115 ms (typically 160–270 ms) to one at 40 ms (typically 100–125 ms) from the same client.
- **Database and I/O.** A slow query in a handler holds up every session that shares its thread. See [Database Connection Pooling](deployment.md#database-connection-pooling) and the worker pool below.

## One process across cores

### Standard CPython (3.12, 3.13)

Expect about one core per process, whatever the host has. What helps:

- **Scoped push** helps most. Set `self.push_scope` and call `push_to_view(..., scope=room)` so a room's broadcast wakes only that room. Without it, one profile found 57,120 server pushes for 3,727 real renders. See [Scoped Push](../advanced/server-push.md#scoped-push-one-room-not-every-room).
- **The GIL-releasing render** is automatic. It gave about 15 % more frames on 3.12.
- **The worker pool** (`LIVEVIEW_CONFIG["worker_threads"]`) helps when handlers wait on I/O. For a CPU-bound app on 3.12 it measured no gain.
- **`djust.layers.InMemoryChannelLayer`**, for one process only. It avoids Channels' per-message expiry sweep.

When one process is not enough, use free-threaded Python (next section) or [more processes](#more-than-one-process-or-pod).

### Free-threaded CPython (3.14t)

The full recipe, with each setting explained, is in [The recipe](scaling-across-cores.md#the-recipe). In short: `LIVEVIEW_CONFIG["worker_threads"]`, scoped push, `djust.layers.InMemoryChannelLayer` and `djust.worker_pool.PooledHTTP`. Points that matter in production:

- **Keep the GIL off.** An extension without free-threading support re-enables it and only prints a warning. Check at build time, by importing everything your server loads (including lazily imported URLconfs) with `python -W error::RuntimeWarning`. At startup, refuse to run if `sys._is_gil_enabled()` is true.
- **Dependencies.** Leave out `orjson` (no free-threaded build; djust falls back to `json`). Some packages have no `cp314t` wheel and build from source, such as autobahn (the multiplayer game's image builds it with `AUTOBAHN_USE_NVX=0`) and cbor2 on macOS x86_64.
- **Size the pool explicitly in containers.** `worker_threads = True` uses the CPUs the process may run on, which in a container with a CPU *limit* is usually the node's core count. Set an integer: the CPU limit minus about one core per event loop. The multiplayer game uses 5 threads under a 6-CPU limit with one loop, and 6 under 8 CPUs with two loops.
- **App code must be thread-safe.** Module-level state that handlers change needs a `threading.Lock`; each pool thread holds its own database connection; sessions pinned to the same thread wait on each other.
- **Wrap HTTP in `PooledHTTP`**, sized on its own (`PooledHTTP(app, threads=3)` in the game). Without it, every concurrent page load gets its own thread, and on 3.14t each thread's heap stays resident. See [Memory under overload](scaling-across-cores.md#memory-under-overload).
- **Answer probes on the event loop**, ahead of the HTTP pool, so a burst of page loads cannot fail them. Give the probe a timeout of at least 5 s; an example follows this list.
- **Serve static files off the thread pools**, from a CDN or an async static handler on the event loop. In the game's container test (12,000 requests, 3.14t), serving through Django's handler grew the process from 6 to 19 threads and 136 MB, and WhiteNoise's middleware to 263 MB; an async handler on the loop stayed flat at 111 MB.

A probe answered on the event loop, ahead of Django and the HTTP pool:

```python
# asgi.py: serve myproject.asgi:app, which wraps the ProtocolTypeRouter `application`
async def app(scope, receive, send):
    if scope["type"] == "http" and scope["path"] == "/healthz":
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})
        return
    await application(scope, receive, send)
```

### Several event loops

Once the pool spreads the sync work, the asyncio event loop becomes the limit at about one core. `djust serve --loops N` runs several loops in one free-threaded process. Add loops only when the loop thread runs at about 0.9 of a core while the process has cores to spare, start with 2, and leave a core per loop outside the pool. The rules for app code (no asyncio objects shared between sessions, one background task per room) are in [More than one event loop per process](scaling-across-cores.md#more-than-one-event-loop-per-process), and the measurements are in [How many loops](scaling-across-cores.md#how-many-loops).

A stuck loop stops accepting connections while the others still answer probes. If you run several loops, have each loop update a heartbeat and make liveness fail when one goes stale.

### Verify

- Per-thread CPU: `py-spy dump`, or `top -H -p <pid>` on Linux. djust names its threads `djust-loop-N`, `djust-worker-N` and `djust-http-N`.
- A loop thread near 1.0 core is the loop limit; pool threads near 1.0 each with the pod below its limit means grow `worker_threads`.
- `assert not sys._is_gil_enabled()` in production.

## Memory

| Setting | Default | Recommendation |
|---|---|---|
| `DJUST_CONFIG["SESSION_TTL"]` | 3600 s | The reconnect window you need. The multiplayer game uses 120 s. The in-memory state backend holds about *new sessions per second × `SESSION_TTL`* entries, at about 270 KB each in that game. |
| `djust.worker_pool.PooledHTTP` | off | On for 3.14t. In one run on a shared dev machine (3.14t, `worker_threads=5`) it cut peak RSS at 256 clients from 2,030 MB to 936 MB. |
| Idle-time `gc.collect()` and `malloc_trim(0)` | not run | On 3.14t, a housekeeping thread that collects only when the process is idle (or every 5 minutes at most), then calls glibc's `malloc_trim(0)` through `ctypes`. Log the pause: a forced collection took 40–190 ms even when it found nothing. |
| `MALLOC_ARENA_MAX`, mimalloc purge options | unset | Made no measurable difference in the game's tests. |

Since 1.2.2 and 1.3 the in-memory backend applies `SESSION_TTL` by itself (#3080); no cleanup job is needed.

What the game measured, 3.14t in a Linux container, 5 × 192 clients then 3 minutes idle: RSS after idle was 651–687 MB as first deployed, 491 MB with `SESSION_TTL` at 120 s alone, and 272–363 MB with all of the above. On the cluster the same change took RSS after five churn cycles from 620 MB to 366 MB.

Planning figures, each from one setup:

- about **1 MB RSS per connected client** on the cluster: 3.14t, 2 loops, 6 pool threads, djust 1.3.0rc4;
- about **0.4 MB per session** on CPython 3.12, one process per pod, with 125–250 MB per pod at its capacity;
- **2.2–2.7 MB per session** on 3.14t with a pool of 8–12 threads on a shared dev machine (one thread per session: 5.4 MB).

**Size the container for the peak.** RSS levels off after load; it does not fall back, because the allocator keeps freed pages for reuse. Watch the state backend's entry count and the live heap, not RSS alone. On 3.14t:

- every thread gets its own allocator heap, so bound your threads;
- state released from another thread waits for a garbage collection on the owning thread;
- a new thread inherits its starter's context variables.

## More than one process or pod

As soon as there is more than one process, **in-process state is not shared**. A dict of rooms, a game clock or a cache exists once per process, and a client that reconnects may land on another process. There are two ways to deal with it:

- **Option B: share everything through Redis and the database.** Any process can serve any request. Measured below.
- **Option A: route each room to one process.** Rooms stay in memory. Not built into djust; described below as a design.

### Option B: Redis between processes

Every process gets the same settings:

```python
# settings.py (every process or pod)
import os

REDIS_URL = os.environ["REDIS_URL"]

# Sessions every process can read, and that survive a Redis restart.
SESSION_ENGINE = "django.contrib.sessions.backends.db"  # or cached_db with a shared cache

DJUST_CONFIG = {
    "STATE_BACKEND": "redis",
    "PRESENCE_BACKEND": "redis",
    "REDIS_URL": REDIS_URL,
}
```

Plus a Redis channel layer, `channels_redis.core.RedisChannelLayer`, as in [Channel Layer](deployment.md#channel-layer-for-cross-process-push). If you run several event loops per process (`djust serve --loops`), use `channels_redis.pubsub.RedisPubSubChannelLayer` instead: `djust serve` refuses the core layer with more than one loop. That combination was not part of the measurements below.

**Sticky routing is not needed.** In the measured setup, a plain Kubernetes Service with no session affinity sent each page GET, WebSocket and reconnect to any pod, and all of them worked.

#### What each of these settings is for

Each item below caused a failure in a multi-pod test when it was missing.

1. **Shared, durable sessions.** The page GET and the WebSocket can land on different processes, so sessions kept per process (a `locmem` cache, files on each pod's disk) share nothing. Use the database (`db`), `cached_db` with a cache every process shares, or a `cache` session on a Redis that persists. With sessions in a Redis without persistence, a Redis restart logs everyone out; in the test, every user of an explicit-exposure view was stuck until they reloaded the page (#3201).
2. **`socket_timeout` above 5 s on the channel layer with redis-py 8.** redis-py 8 made its socket timeout default to 5 s, which equals channels_redis' receive timeout. A process whose channel receives nothing for 5 s drops a WebSocket: 38 unexpected disconnects in 90 s on 2 pods with idle per-user clients. Give each channel-layer host a `socket_timeout` greater than 5, for example 10. See [#3199](https://github.com/djust-org/djust/issues/3199) for the configuration and the system check.
3. **Opt in to state that survives a reconnect.** `STATE_BACKEND = "redis"` does not bring a view's state back on another process. It caches the compiled view as the diff baseline for the next mount. A default LiveView that reconnects to another process runs `mount()` again, and whatever it held is lost. In the test, a counter went back to 0 on every reconnect of a default view. To keep state across processes, persist it through the Django session:
   - legacy views: set `enable_state_snapshot = True` on the view;
   - [explicit exposure](../state/explicit-exposure.md) views: declare the fields with `state(..., persist="server")`.

   Both restored every counter exactly in the test (198 of 198 reconnects). Both need the shared session store from item 1. Typed-in form fields survive either way, because client.js re-sends fields that differ from the server's values after a reconnect (see [Form Recovery](reconnection.md#form-recovery)). Under explicit exposure, validation messages are not persisted: they come back when the user next edits the field.
4. **One process of headroom (N+1).** WebSockets are long-lived, and nothing moves them after a failover. See [Connections don't rebalance](#connections-dont-rebalance).
5. **Room for rolling updates.** With `maxUnavailable: 0` a rolling update needs a free slot for the surge pod. On a node at its pod limit the rollout stalls. Keep a slot free, or use `maxUnavailable: 1` with the N+1 headroom from item 4.

#### In-process state under Option B

Anything that lives in one process's memory is invisible to the others: room objects, game clocks, module-level dicts, `functools.lru_cache` contents that must agree. Keep shared state in Redis or the database, and reach other sessions only through the channel layer. The benchmark app kept each chat room's history in a Redis list and broadcast with `push_to_view(..., scope=room)`. If your shared state is a live object that can't move to Redis, look at Option A.

#### Measured capacity

Conditions for every number in this section:

- djust 1.3.0rc4, **CPython 3.12**, one uvicorn process per pod, `worker_threads` unset;
- each pod had a **2-CPU limit**;
- `channels_redis.core.RedisChannelLayer` with `socket_timeout=10`, Redis state and presence backends, sessions in Redis;
- one Kubernetes node (16 cores) for all pods and Redis, so cross-pod traffic never crossed a real network;
- load from inside the cluster, real djust WebSocket clients, 60 s steps;
- capacity = the most clients with p95 at or below 150 ms and no errors, from two rounds hours apart (rounds differed by 10–20 %).

| Workload | 1 pod | 2 pods | 4 pods |
|---|---|---|---|
| Per-user events: 1 event per client per second (counter and form validation) | 252–300 clients | 504–600 | 1,002–1,100 |
| Chat rooms of 10, each client sending every 3 s, broadcast across pods | 30–40 clients | 80–84 | 200–204 |

- **Each 3.12 pod stops at about one core** (0.9–1.0 used under a 2-CPU limit, throttled 1 % or less). The process, not the quota, is the limit.
- **Chat grows faster than the pod count.** One process renders a room's recipients one after another; more pods render them in parallel.
- **Cross-pod delivery was not slower than same-pod.** At 4 pods and 200 chat clients: cross-pod p95 95 ms, same-pod p95 119 ms. On a real network, add the hop.
- **Redis was nearly idle for per-user events.** 1,100 clients on 4 pods cost Redis 0.006 cores and 3 operations per second: the state backend is written at mount, not per event. Moving from in-memory to Redis made no measurable difference to one pod's per-user capacity (252–300 either way).
- **Redis halved one process's chat capacity:** 30–40 clients against 80–84 with the in-memory layer, 5.1 ms of CPU per delivery against 3.6 ms. Over half of Redis's commands were presence reads from the online-list render. Those numbers predate [#3203](https://github.com/djust-org/djust/issues/3203), which cut a presence list to two Redis commands on the 1.3 line; they have not been re-measured since.
- **Connect bursts are expensive.** Ramping 1,100 clients in 10 s on 4 pods (27 connects per second per pod) took 4.6 s at p50 and 7.1 s at p95 to load the page and mount.
- **Explicit exposure is currently much lower:** 76 clients on 1 pod and 300 on 4. Its per-event session save has a 150 ms budget that includes queueing, so under load it answers with "State unavailable. Please reload the page." long before the CPU runs out ([#3200](https://github.com/djust-org/djust/issues/3200)).
- **Free-threaded pods:** one run with 3.14t, `worker_threads=2`, one loop and a 2-CPU pod handled 600 per-user clients (1.6 cores) and 100 chat clients, at 590 MB RSS. That is one round only.

### Option A: route each room to one process (not built)

This is a design, not a djust feature, and it has not been measured.

- **Shape:** N single-process djust servers, each set up as in [One process across cores](#one-process-across-cores) with in-memory rooms, channel layer and presence. The load balancer hashes the room key to a server, so every session of a room lands on the same process.
- **The routing gap:** djust's WebSocket connects to `/ws/live/`, which does not carry the room. The page has to route the socket some other way, such as a per-server host name or a cookie or header the load balancer hashes on. djust does not provide this today.
- **What stays local:** rooms, clocks, presence within a room, scoped push.
- **What still needs a shared store:** anything across rooms, such as a lobby listing every room, a leaderboard, a global online count, or a push from one room to another.
- **Failure mode:** a lost process takes its rooms' live state with it. The clients reconnect to whichever process the hash now picks, and the rooms start empty. Every deploy restarts rooms unless the app hands them off, which djust does not do.
- **Capacity:** roughly N times one process, if rooms spread evenly. That is an estimate; a hot room does not split.

### Choosing between A and B

| | Option B (measured) | Option A (not built) |
|---|---|---|
| Infrastructure | Redis, shared session store | a load balancer that hashes rooms, plus a store for anything cross-room |
| In-process state | not shared; move it to Redis or the database | works, per room |
| Pod loss | clients reconnect to any pod in 1–3 s; persisted state restored | the rooms of that process lose their live state |
| Deploys | rolling, about 1.5 reconnects per client | every room restarts empty unless handed off |
| Broadcast-heavy rooms | about half the in-memory rate per process (before #3203) | in-memory rate |
| Redis outage | chat and presence recover by themselves in about 5 s | no Redis in the room path |

Per-user apps (forms, dashboards, CRUD) fit Option B well. Apps whose shared state is a live in-process object, such as a game room, fit Option A or a single larger process better.

## Failover and deploys

### One process

With one replica and in-process state, a restart loses every room and every session's server state. Plan for it:

- the multiplayer game runs one replica with the Kubernetes `Recreate` strategy: a deploy stops the old pod before starting the new one, so there is a short gap, but never two pods holding different rooms;
- probes answer on the event loop, with a per-loop heartbeat when you run several loops, and timeouts of at least 5 s;
- an out-of-memory kill is a restart too, which is why the [memory settings](#memory) matter. Without them, one overloaded 3.14t pod grew to 1.3 GB and was restarted.

### Several pods (Option B, measured)

Conditions: the setup from [Measured capacity](#measured-capacity), 4 pods, 200 clients (100 per-user, 100 in 20 chat rooms, about 40 % of capacity), and the failure 40 s into the run.

| Event | What clients saw |
|---|---|
| Pod deleted gracefully (close code 1012) | Every client of that pod reconnected on its first attempt, within 1.3–1.5 s. The gap is mostly client.js's own reconnect delay; the other pods accepted at once. |
| Pod crashed (close code 1006) | Reconnected within 1.5–2.9 s. The dead pod stays in the Service until its readiness probe fails, so some reconnects needed a second attempt. |
| Chat during a single pod loss | 0 messages lost and 0 duplicated in 10 runs, about 16,500 expected deliveries each. Messages sent during the gap are in the room history the reconnect renders. |
| Presence | The online count was exact in 99.4–99.9 % of deliveries, with brief over-counts of 1–3. |
| State after reconnect | Restored exactly with `enable_state_snapshot` or `persist="server"`; reset for default views. |
| Real browser (Chromium, djust's `client.min.js`) | No page reloads in 12 kills. Typed form input, including an unsent draft, survived in every view. |
| Latency | p95 rose to 235–424 ms for about 2 s while persisted views remounted, then settled at 63–84 ms against 41–65 ms before. |
| Rolling restart (`maxSurge: 1`, `maxUnavailable: 0`) | 30 s for 4 pods. Each client moved about 1.5 times on average, because some reconnected to pods that were replaced next. 0.03–0.07 % of chat deliveries lost; no page reloads. |
| Redis restart (no persistence) | Chat and presence recovered by themselves in 5–6 s; the room history kept in Redis was gone. Users whose sessions were in that Redis were logged out (see item 1 above). |

#### Connections don't rebalance

A WebSocket stays on the pod it connected to. After a pod delete or crash, the replacement pod ended every run with **0 sockets** while 3 pods carried the load (for example 76, 67, 61 and 0). After a rolling restart, pods held 21–85 sockets each, and p95 settled at about 125 ms against 55–65 ms before the roll.

So a cluster that has just failed over runs on N−1 pods until clients churn. Keep one pod of headroom (N+1) at your expected peak. djust has no server-side rebalance yet.

#### Explicit exposure under failover

Until [#3200](https://github.com/djust-org/djust/issues/3200) and [#3201](https://github.com/djust-org/djust/issues/3201) are fixed:

- a rolling restart produced 80–93 "State unavailable. Please reload the page." errors among 100 explicit-view users, in a burst at each step of the roll, because the surviving pods were busy with the reconnect wave;
- after a Redis restart that lost their sessions, explicit-view pages could not mount again until the user reloaded.

Keep sessions in the database, and test a rolling restart under load before relying on explicit exposure in a multi-pod deployment.

#### Probes

**Readiness should not check Redis.** If every pod reports not-ready during a Redis blip, the Service has no endpoints, and a 5-second Redis restart becomes a full outage. In the test the pods stayed ready through the Redis restart and recovered by themselves. Check Redis in monitoring and alerts instead.

## Capacity reference

These are two apps' numbers, not a promise. The multiplayer game is a snake game in rooms of 4: about 5 broadcast frames per client per second, and each client presses a key every 400 ms. For the game, "holds up to" is the last step where clients still got their full frame rate, and "saturated at" is the first step where they did not. The benchmark app is the per-user and chat workload from [Option B](#measured-capacity); for it, "holds up to" is the last step with p95 at or below 150 ms.

| Setup | Where | Holds up to | Saturated at | Cores used |
|---|---|---|---|---|
| Game, 3.12, stock, board rendered as DOM | shared dev machine (12-core Apple Silicon) | about 24 clients | – | 1.0 |
| Game, 3.12, stock, canvas board | shared dev machine | about 32 clients | 64 | up to 1.15 |
| Game, 3.12, pool + scoped push | shared dev machine | about 64 clients (p95 131–374 ms) | 128 | up to 1.33 |
| Game, 3.14t, stock | shared dev machine | about 64 clients | 128 | up to 1.51 |
| Game, 3.14t, pool + scoped push + `djust.layers` | shared dev machine | 192–256 clients (p95 180–304 ms) | – | 4.3–6.6 |
| Game, 3.14t, 1 loop, djust 1.3.0rc2 | Kubernetes pod, 6-CPU limit | 128 clients | 160 | 4–4.8 |
| Game, 3.14t, 1 loop, `PooledHTTP`, djust 1.3.0rc3 | pod, 6-CPU limit | 160 clients | 192 | 4.8–5.7 |
| Game, 3.14t, 2 loops, djust 1.3.0rc4 | pod, 8-CPU limit | 192 clients | 256 | 5.5–6.9 |
| Benchmark app, 3.12, per-user events, Option B | 1 / 2 / 4 pods, 2-CPU limit each | 252–300 / 504–600 / 1,002–1,100 clients | – | about 1 per pod |
| Benchmark app, 3.12, chat rooms of 10, Option B | 1 / 2 / 4 pods, 2-CPU limit each | 30–40 / 80–84 / 200–204 clients | – | about 1 per pod |
| Benchmark app, 3.14t, `worker_threads=2`, Redis (one round) | 1 pod, 2-CPU limit | 600 per-user or 100 chat clients | – | 1.6 |

- The dev-machine rows ran while other jobs used the same machine, so their spread is wide.
- The cluster node's cores were roughly half as fast as the dev machine's (an estimate from comparing runs, not a benchmark), and the loop-bound limit fell about in proportion.
- The game's pod rows are one process per pod. Room sharding (Option A) would multiply them by the number of processes only if rooms spread evenly; that has not been measured.

## Measuring your own

The numbers above came from load generators that behave like browsers. To measure your app:

- **Use real djust WebSocket clients:** load the page to get the session and CSRF cookies, open `/ws/live/` with the same `Origin` and cookies, and send the mount and your events. Measure the round trip from an event to its reply, and for broadcasts the time from sender to each recipient.
- **Run the load inside the cluster**, so the numbers measure your pods and not the internet.
- **Mind the per-IP limit.** djust allows 10 WebSockets per client IP by default (`LIVEVIEW_CONFIG["rate_limit"]["max_connections_per_ip"]`) and refuses more with close code 4429. From inside the cluster, give each simulated client its own `X-Forwarded-For` and set `DJUST_TRUSTED_PROXY_COUNT` to match; never trust that header from the internet.
- **Step the load** (for example 60 s per step after a 10 s ramp) and stop at the first step over your latency target or with any error.
- **Record CPU per thread** as well as per process: the loop thread tells you when to add loops, and cgroup throttling (`nr_throttled` in `cpu.stat`) tells you the pod limit is the constraint.
- **Watch the load generator** too. Its own CPU, the host's load average and dropped result logs all distort numbers.

## Troubleshooting checklist

- [ ] **The process sits at about 1.0 core with idle cores.** Is the GIL on (`sys._is_gil_enabled()`)? Is `worker_threads` set? Is some work still on the single shared thread?
- [ ] **The loop thread is at about 0.9 core.** Is every broadcast scoped, including presence? Are you using `djust.layers.InMemoryChannelLayer`? Then try `djust serve --loops 2`.
- [ ] **Pool threads are busy but the pod is under its limit.** Raise `worker_threads`, and look for a slow handler blocking the sessions on its thread.
- [ ] **Nothing is CPU-bound, yet p95 explodes.** Check per-thread pool CPU, lock contention and garbage-collection pauses in the logs.
- [ ] **CPU is throttled** (`nr_throttled` grows). The pool plus the loops exceed the CPU limit: leave a core per loop.
- [ ] **RSS grows with every load run.** Check `SESSION_TTL`, `PooledHTTP`, idle-time collection, and the thread count over time.
- [ ] **500 errors on the first concurrent page loads on 3.14t.** Use djust 1.3.0rc4 or later (#3151).
- [ ] **"is bound to a different event loop".** An asyncio object is shared between sessions under `--loops`.
- [ ] **Probes fail during page-load bursts.** Answer `/healthz` on the event loop, not through `PooledHTTP`.
- [ ] **Several pods: a view's state resets after a reconnect.** Set `enable_state_snapshot` or `persist="server"`, and use a shared session store.
- [ ] **Several pods: idle clients drop every few seconds.** Set `socket_timeout` above 5 s on the channel layer (redis-py 8, #3199).
- [ ] **Several pods: one pod is idle after a failover.** Connections don't rebalance; size for N+1.
- [ ] **A rolling update never finishes.** The surge pod can't be scheduled: free a pod slot or use `maxUnavailable: 1`.
- [ ] **Load test numbers look too good or too bad.** Check for 4429 refusals (the per-IP limit), the load generator's CPU, the host's load average, and lost result logs.
