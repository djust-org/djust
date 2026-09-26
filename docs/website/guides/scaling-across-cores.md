---
title: "Scaling a djust Process Across Cores"
slug: scaling-across-cores
section: guides
order: 13.5
level: advanced
description: "Use more than one CPU core per process: free-threaded Python, the worker_threads pool, scoped push, the in-process channel layer and several event loops per process, with measured numbers and the Redis multi-process alternative."
---

# Scaling a djust Process Across Cores

By default one djust process does its LiveView work on about **one CPU core**, however many cores the host has. This guide covers the two ways past that:

- **A bigger process:** free-threaded CPython plus djust's opt-in settings. One process uses several cores, and in-process state such as game rooms and caches keeps working.
- **More processes:** stock CPython, several workers, and Redis for everything shared.

They combine: you can run several free-threaded processes.

## Why one process uses one core

Three things cap a stock process:

1. **All WebSocket sessions share one sync thread.** Every `mount`, event handler, hook and render goes through asgiref's `sync_to_async` with `thread_sensitive=True`, which runs them all, for all sessions, on a single thread.
2. **The GIL.** On standard CPython only one thread runs Python at a time, so extra threads help only while they wait on I/O or run code that releases the GIL.
3. **The asyncio event loop.** Every frame for every session is decoded, dispatched and encoded on one loop thread. Once the sync work moves elsewhere, the loop becomes the next ceiling; see [More than one event loop per process](#more-than-one-event-loop-per-process).

## The recipe

```python
# settings.py
LIVEVIEW_CONFIG = {
    "worker_threads": True,  # a pinned pool, one thread per CPU (max 32)
}

CHANNEL_LAYERS = {
    "default": {"BACKEND": "djust.layers.InMemoryChannelLayer"},  # one process only
}
```

```python
# views.py: multi-room views push to one room, not to every session
class RoomView(LiveView):
    def mount(self, request, room="lobby", **kwargs):
        self.room = room
        self.push_scope = room

push_to_view("games.views.RoomView", handler="handle_refresh", scope=room)
```

```python
# asgi.py: HTTP requests share a bounded pool of threads too (#3114)
from djust.worker_pool import PooledHTTP

application = ProtocolTypeRouter({
    "http": PooledHTTP(get_asgi_application()),
    "websocket": DjustMiddlewareStack(URLRouter(websocket_urlpatterns)),
})
```

Then run on free-threaded CPython:

```bash
uv python install 3.14t
uv venv --python 3.14t && uv pip install djust uvicorn   # cp314t wheels
uvicorn myproject.asgi:application --ws websockets
```

A few of djust's dependencies have no free-threaded wheels yet: autobahn (through `channels[daphne]`) on every platform, and cbor2 on macOS x86_64. The installer builds them from source, which needs a C compiler and takes longer.

Each piece removes one ceiling. The sections below explain them.

### 1. Free-threaded CPython (3.14t)

- **What it is.** CPython 3.14's free-threaded build (`python3.14t`) has no GIL, so threads run Python in parallel.
- **djust support.** djust publishes `cp314t` wheels from 1.3. Its Rust extension declares that it does not need the GIL, so importing djust keeps the GIL off. The check is below.
- **What not to install.** Leave out `orjson`, from djust's `performance` extra: it has no free-threaded build. djust falls back to the standard library's `json`.
- **Check your other extensions too.** An extension that does not declare free-threading support makes CPython re-enable the GIL, and prints a warning when it does. Run this check in production, after your app's imports:

```python
import sys

import djust

assert not sys._is_gil_enabled(), "something re-enabled the GIL"
```

### 2. The worker pool: `LIVEVIEW_CONFIG["worker_threads"]`

- **What it does.** Each WebSocket session is pinned to one thread of a pool for its lifetime, so thread-locals and the thread's database connection stay consistent. Different sessions run at the same time.
- **Settings.** `True` means one thread per CPU; an integer sets the count. The default, `None`, keeps the single shared thread.
- **Concurrency.** A session's own events still run in order.
- **Other transports.** HTTP and SSE are unchanged unless you wrap the HTTP app in `PooledHTTP` (see [Memory under overload](#memory-under-overload)).

With the pool on, djust also moves per-frame work off the event loop:
- the pre-event snapshot of the view's state runs on the session's thread;
- a server push to a view that doesn't use ADR-038 explicit exposure runs as a single hop on the session's thread;
- for those same pushes, when the frame carries nothing but patches, the rendered patch goes into it without being re-encoded on the loop;
- a tick's change-detection snapshots run in the same hop as `handle_tick`;
- Django's stale-connection check, which Channels 4.2+ runs in its own thread hop before every WebSocket frame, runs on the session's thread before its next task instead.

Some per-event savings apply with or without the pool: the handler-permission and object-permission checks make no thread hop when there is nothing to check (no `@permission_required`, no `get_object` override), the handler's signature and type hints are worked out once per function, and `djust.layers.InMemoryChannelLayer` delivers a group send without creating a task per session.

What to know before turning it on (details in [Deployment](deployment.md#more-than-one-core-per-process-worker_threads)):

- **Shared state needs locks.** Sync handlers now run concurrently with other sessions' handlers, so module-level state they mutate needs a `threading.Lock`.
- **Database connections.** Each pool thread holds its own database connection.
- **Sessions on one thread wait on each other**, though no longer on the whole process.
- **Pool size.** Start at about the core count. On 3.14t, pools of 8–12 threads on a 12-core machine cost 2.2–2.7 MB per session. One thread per session cost 5.4 MB and gave lower tail latency.

### 3. Scoped push

`push_to_view(view, ...)` reaches every session of the view. In a view with many rooms, every room's broadcast wakes every session in every room. In one profile that meant 57,120 `server_push` calls for 3,727 real renders, and it pins the event loop long before the cores are busy.

Set `self.push_scope` and push with `scope=` so a broadcast reaches only its room; see [Scoped Push](../advanced/server-push.md#scoped-push-one-room-not-every-room).

`PresenceMixin` follows: in a view that sets `push_scope`, a join or leave wakes only the sessions that share its presence key (see [Presence](presence.md#multi-room-views-who-a-join-wakes)).

### 4. The in-process channel layer

Channels' `InMemoryChannelLayer` checks every channel for expiry on every message. A broadcast round across N sessions therefore costs O(N²) on the event loop:

| sessions | Channels' layer | `djust.layers.InMemoryChannelLayer` |
|---|---|---|
| 128 | 7.0 ms/round | 2.3 ms/round |
| 224 | 17.7 ms/round | 4.4 ms/round |
| 512 | 78.2 ms/round | 10.3 ms/round |

djust's layer is the same layer with the expiry sweep limited to once a second. Use it only when **one process** serves every WebSocket.

### 5. The GIL-releasing render

`render_with_diff` releases the GIL while the Rust engine renders and diffs. This is automatic. On standard CPython 3.12 it lets other threads run Python during a render. In a micro-benchmark, 4 threads rendered 3.1–3.4× as much as one thread, and 8 threads about 4×. On 3.14t there is no GIL to release.

## Measured numbers

The workload is the snake-arena game:
- one uvicorn process (`--ws websockets --loop asyncio`) on a 12-core Apple Silicon machine that other jobs were also using;
- real WebSocket clients in rooms of 4, each room running a bot game, with every client pressing a key every 400 ms;
- 45 s per step.

"Frames/s" is broadcast frames per client per second; the game sends about 5–7. "p95" is the event round trip. Each cell is the range over 2–4 interleaved rounds (djust 1.3, #3074).

| Clients | Stock, 3.12 | Opt-in†, 3.12 | Stock, 3.14t | Opt-in†, 3.14t | Opt-in† + `djust.layers`, 3.14t |
|---|---|---|---|---|---|
| 32 | 5.6–5.9 fps, p95 36–44 ms, 0.7 cores | 5.8–5.9 fps, p95 9–13 ms | 4.1–5.6 fps | 5.6–5.8 fps, p95 4–289 ms | – |
| 64 | **2.1–2.9 fps**, p95 0.1–2.3 s | 5.4–5.6 fps, p95 131–374 ms | 4.6–4.9 fps, 1.5 cores | 6.0–6.5 fps, p95 5–6 ms | – |
| 128 | 1.1 fps, 1.1 cores | 2.9–3.8 fps, 1.3 cores | **1.7 fps**, 1.4 cores, loop thread 0.96 | 5.2–6.8 fps, 2.2–2.4 cores | – |
| 192 | 0.5–0.7 fps | 1.8–2.0 fps | 1.3 fps | **7.1–7.3 fps, p95 180–224 ms, 4.4–4.5 cores** | 6.9 fps, loop thread 0.60 |
| 224 | – | – | – | 6.8 fps, 5.1 cores | 6.7–6.8 fps, 5.4–5.6 cores |
| 256 | – | – | – | 6.3–6.5 fps, loop thread 0.96 | **6.7–6.9 fps, 6.0–6.6 cores** |

† Opt-in: `worker_threads=True` (12 threads) plus room-scoped push; the event-loop offload comes with the pool.

What the numbers say:
- **Stock djust on 3.12 saturates at about 32 clients on one core.**
- **The opt-in settings on 3.12 double that, to about 64 clients.** The GIL still caps the process at about 1.3 cores. In the #3074 experiment, scoped push alone produced most of this gain on 3.12; per-session threads alone gave none.
- **3.14t by itself doubles stock too,** to about 64 clients. Then the event loop pins at one core.
- **3.14t with the opt-in settings serves 192–256 clients at full frame rate on 4–6.6 cores.** That is about 6–8× stock 3.12 in one process.
- **Above about 224 clients the event loop is the limit again,** at 0.93–0.97 of a core. djust's in-memory layer takes it down to 0.6–0.9.
- **Memory.** In the #3074 experiment, controlled for session count:
  - a pinned pool of 8–12 threads used 2.2–2.7 MB RSS per session on 3.14t;
  - one thread per session used 5.4 MB;
  - the shared thread used 1.6 MB, and stock 3.12 1.1 MB.

  The extra cost comes from free-threaded CPython's per-thread allocator heaps, which is why djust uses a bounded pool rather than a thread per session.

## More than one event loop per process

With the recipe above, the next ceiling is the **asyncio event loop**. It decodes, dispatches and encodes every WebSocket frame on one thread, so it saturates at about one core: snake-arena on 1.3.0rc3 in production stops scaling at about 160–192 players with the loop thread at 0.93 of a core while the pool still has room. On free-threaded Python, djust 1.3 can run **several event loops in one process** (opt-in, #3128):

```bash
djust serve myproject.asgi:application --loops 4 --ws websockets --host 0.0.0.0 --port 8000
```

```python
# settings.py: a channel layer whose queues belong to no event loop
CHANNEL_LAYERS = {
    "default": {"BACKEND": "djust.layers.MultiLoopInMemoryChannelLayer"},
}
LIVEVIEW_CONFIG = {"worker_threads": True}
```

`djust serve` runs N uvicorn servers, each with its own event loop on its own thread, all accepting on **one** listening socket. Every loop polls the socket and the kernel gives a new connection to whichever loop accepts first, so a busy loop takes fewer. A connection stays on the loop that accepted it. In-process state (rooms, caches, the channel layer) is shared by all loops, which is the reason to use loops rather than processes. The same launcher is available as `djust.multiloop.serve(app, loops=N, **uvicorn_options)`.

- **`--loops 1`, the default, is plain `uvicorn.run`.** It takes the common uvicorn options (`--host`, `--port`, `--uds`, `--fd`, `--ws`, `--http`, `--loop`, `--lifespan`, `--proxy-headers`, `--forwarded-allow-ips`, `--root-path`, `--ws-per-message-deflate`, `--backlog`, `--timeout-keep-alive`, `--timeout-graceful-shutdown`, `--limit-concurrency`, `--limit-max-requests`, `--ssl-keyfile`, `--ssl-certfile`, `--ssl-keyfile-password`, `--log-level`); `djust.multiloop.serve` takes any `uvicorn.Config` option. `--reload` and `--workers` are not offered.
- **Free-threaded only.** With the GIL on, several loops only take turns holding it and add thread switches, so `--loops 2` or more refuses to start on a GIL build, and after importing your app if an extension re-enabled the GIL. (`--allow-gil` overrides it, for tests.)
- **A loop-safe channel layer.** `djust serve` refuses Channels' `InMemoryChannelLayer`, djust's `InMemoryChannelLayer` and `channels_redis`' `RedisChannelLayer`: each keeps state that only one loop may touch (an `asyncio.Queue` per channel, a single receive lock). Use `djust.layers.MultiLoopInMemoryChannelLayer` for one process, or `channels_redis.pubsub.RedisPubSubChannelLayer`, which keeps a connection per loop. Other layers start with a warning.
- **Lifespan runs once per loop**, as it runs once per worker under `uvicorn --workers`. Whatever your app creates at startup exists once per loop.
- **Shutdown.** The main thread handles SIGINT and SIGTERM: the first asks every loop to shut down gracefully, a second forces it. If one loop stops (its lifespan startup failed, say), the others stop too, and the exit code is 3 when a loop failed to start.
- **Per-loop limits.** `--limit-concurrency` and `--limit-max-requests` count per loop, and one loop reaching `--limit-max-requests` stops the whole process (run it under a supervisor that restarts it).
- **Grow the pool with the loops.** The loops hand sync work to `worker_threads`; with 2–4 loops the pool is the next limit (see the numbers below). `djust serve` warns when `worker_threads` is off.

### `djust.layers.MultiLoopInMemoryChannelLayer`

The layer keeps each channel in a plain `deque` under one `threading.Lock`, so no queue belongs to a loop:

- `receive()` takes the oldest message, or parks a future on **its own** loop;
- `send()` checks the channel's capacity and appends under the lock, so `ChannelFull` is raised before any hand-off, then wakes one parked receiver: directly on the same loop, through `loop.call_soon_threadsafe` from another;
- `group_send()` appends to every member under one acquisition of the lock, so two group sends reach every member in the same order, and wakes each receiving loop once rather than once per member.

Messages on one channel arrive in the order they were appended, whichever loops sent them. Everything else is `djust.layers.InMemoryChannelLayer`: the same arguments, expiry, capacity and once-a-second expiry sweep. With one loop it behaves like `InMemoryChannelLayer`, so it is safe to configure before you add loops.

### What djust keeps safe across loops

| State | With several loops |
|---|---|
| A session's render lock, tick task, deferred-push queue, presence heartbeat, `start_async` / `@background` tasks | Created and used on the session's own loop. |
| `worker_threads` pool and `PooledHTTP` | Loop-agnostic: `run_in_executor` works from any loop. A pool thread serves sessions of every loop. |
| `sync_to_async` / `async_to_sync` | `async_to_sync` in a session's sync code returns to **that session's** loop. From a plain thread (a Celery task, a clock thread) it runs on a temporary loop, and the layer carries the message. |
| Presence, state, rate-limit and IP-tracker backends | Already guarded by `threading` locks for the pool. |
| Channel layers | Created once, before the loops start, so two loops never race to create two layers. |
| SSE sessions | The stream GET and a later event POST can land on different loops. The POST runs the dispatch on the session's loop, in a copy of its own context. |
| `db_notify` listener | One psycopg connection on one loop. The first loop to subscribe claims it, under a lock; other loops hop to it. |
| Hot reload | Development only; use one loop (`--reload` is not supported with `--loops`). |

### Rules for app code

A connection stays on one loop, but two sessions of the same room can be on different loops. So:

1. **Never share an asyncio object between sessions.** A module-level or class-level `asyncio.Lock`, `Event`, `Queue`, `Condition`, `Semaphore`, `Future` or `Task` belongs to the loop that first waited on it; a session on another loop that uses it fails ("is bound to a different event loop") or silently never wakes. Guard shared state with a `threading.Lock`, and talk to other sessions through `push_to_view` / `apush_to_view` or the channel layer.
2. **Room-wide background work is one task per room, not per loop.** Start it under a `threading.Lock`: while holding the lock, record when the room asked for it (`time.monotonic()`), check whether the room's task is running, and create and record the task if it isn't. Two sessions of one room can ask for it at the same moment on two loops. Decide a stop under the same lock too: when the task finds its room idle, re-check for a request recorded since, and forget the task while still holding the lock, or a session on another loop that asked in between is left relying on a task that is about to end. The task runs on the loop of the session that started it and reaches the room's sessions only by pushing. To compare times recorded on different loops, record `time.monotonic()`: `loop.time()` is `time.monotonic()` on asyncio's own loops, but uvloop keeps its own cached millisecond clock.
3. **Don't cache the running loop** in module or class state (`asyncio.get_running_loop()`, `get_event_loop()`). To act on a task or future of another loop, go through that loop: `task.get_loop().call_soon_threadsafe(task.cancel)`, or `asyncio.run_coroutine_threadsafe(coro, loop)` (`djust.multiloop.run_on_loop` wraps it).
4. **Per-loop startup state.** Anything created in a lifespan startup (an `httpx.AsyncClient`, a connection pool) exists once per loop; use it only from the loop that created it.
5. **One process only.** The in-memory layer does not cross processes. For several processes, use Redis as before; they combine: several multi-loop processes behind a load balancer, with Redis between them.

### How many loops

Measured with the snake-arena game, as above, on a 12-core Apple Silicon machine (8 performance cores) that other jobs were also using: free-threaded 3.14t, `worker_threads=8`, scoped push, rooms of 4 with a bot game each, every client pressing a key every 400 ms, 45 s per step (30 s measured), and the load generator on the same machine. One loop uses `djust.layers.InMemoryChannelLayer`, as in production; 2 and 4 loops use `MultiLoopInMemoryChannelLayer`. Each cell is the range over interleaved rounds. Steps that started with the machine's load average above 10 (1 minute) or 12 (5 minutes) were left out: 57 steps were kept and 27 left out. "Clients connected" counts the clients that loaded the page and mounted within the load generator's 20 s timeout. "Each loop thread" is the CPU time of each event loop's own thread.

<!-- ML-TABLE-START -->
| Clients | Loops | Clients connected | p95 event round trip | Frames/s per client | Process cores | Each loop thread (cores) | Load avg before (1 / 5 min) | Rounds |
|---|---|---|---|---|---|---|---|---|
| 192 | 1 | 192 | 5–18 ms | 4.46–4.56 | 3.2–3.4 | 0.38–0.42 | 5.5–7.8 / 6.7–11.9 | 4 |
| 192 | 2 | 192 | 5–20 ms | 4.50–4.67 | 3.2–3.6 | 0.23–0.30 | 5.3–7.8 / 6.5–9.4 | 4 |
| 192 | 4 | 192 | 6–78 ms | 4.47–4.53 | 3.2–4.2 | 0.11–0.25 | 6.6–7.8 / 6.5–11.5 | 3 |
| 256 | 1 | 256 | 6–169 ms | 4.43–4.56 | 4.2–4.5 | 0.51–0.59 | 4.5–7.8 / 6.0–11.8 | 5 |
| 256 | 2 | 256 | 6–527 ms | 3.60–4.58 | 4.0–4.4 | 0.30–0.37 | 4.1–8.0 / 5.9–10.8 | 5 |
| 256 | 4 | 256 | 11–12 ms | 4.55–4.63 | 4.5 | 0.15–0.25 | 4.2–4.6 / 5.7–6.3 | 2 |
| 384 | 1 | 384 | 23–4143 ms | 2.89–4.66 | 4.8–6.6 | 0.74–0.82 | 4.1–8.0 / 6.2–10.9 | 5 |
| 384 | 2 | 384 | 26–396 ms | 3.96–4.70 | 6.4–7.4 | 0.55–0.66 | 4.8–7.8 / 5.9–11.8 | 5 |
| 384 | 4 | 384 | 38–55 ms | 4.59–4.71 | 7.1–7.4 | 0.33–0.38 | 4.3–5.7 / 5.8–5.9 | 2 |
| 512 | 1 | 512 | 297–326 ms | 4.19–4.33 | 7.2–7.9 | 0.94–0.97 | 5.2–7.2 / 6.6–9.1 | 3 |
| 512 | 2 | 382–512 | 284–1792 ms | 3.03–4.26 | 4.8–9.0 | 0.52–0.82 | 5.8–7.9 / 6.1–11.1 | 5 |
| 512 | 4 | 512 | 311–369 ms | 3.93–4.13 | 8.9–9.2 | 0.44–0.53 | 5.0–6.3 / 5.8–6.2 | 2 |
| 640 | 1 | 469–476 | 229–273 ms | 4.40–4.48 | 7.4–7.5 | 0.94–0.95 | 6.3–7.5 / 6.5–8.1 | 2 |
| 640 | 2 | 591–640 | 337–380 ms | 3.46–3.80 | 9.1 | 0.86–0.87 | 6.4–7.7 / 6.8–8.0 | 2 |
| 640 | 4 | 559–616 | 340–405 ms | 3.19–3.79 | 9.0–9.2 | 0.49–0.55 | 7.5–7.7 / 7.4–7.8 | 2 |
| 768 | 1 | 641–649 | 425–453 ms | 3.53–3.57 | 6.8 | 0.95 | 6.8–7.9 / 6.7–8.3 | 2 |
| 768 | 2 | 768 | 539–708 ms | 3.15–3.19 | 8.5–8.6 | 0.82–0.83 | 7.2–7.8 / 7.0–8.1 | 2 |
| 768 | 4 | 768 | 960–4289 ms | 2.81–3.00 | 8.2–8.6 | 0.46–0.53 | 7.6 / 7.6–8.0 | 2 |
<!-- ML-TABLE-END -->

What the numbers say:

- **One loop tops out at about 512 clients on this machine.** Its thread ran at 0.94–0.97 of a core from 512 clients up. At 640 and 768 clients it could not take every connection during the ramp: 469–476 of 640 and 641–649 of 768 connected.
- **2 loops raise that ceiling.** They connected all 768 clients, with each loop at 0.82–0.87 of a core, and delivered about 6% more frames in total than one loop (768 clients × 3.2 frames/s against 645 × 3.6). At that point the process used about 9 of the machine's 12 cores and was limited by CPU, not by a loop: frames per client fell, and 640-client rounds lost up to 49 clients in the ramp.
- **Below about 384 clients the number of loops changes little.** One loop is not yet the limit there.
- **Loop work grows with the number of loops.** At 384 clients one loop used 0.74–0.82 of a core; two used 1.1–1.3 between them, and four 1.3–1.5. Each loop wakes for fewer events at a time, and a room's push reaches sessions on several loops.
- **4 loops did not beat 2** on this machine, and had a worse tail at 768 clients. The extra loop work competes with the worker pool for the same cores.
- **The same layer on one loop** (`MultiLoopInMemoryChannelLayer` with `--loops 1`) measured like `djust.layers.InMemoryChannelLayer`: 4.4–4.6 frames/s at 192–256 clients.

Guidance:

- Add loops only when **the loop thread is the limit**: it runs at about 0.9 of a core under load (production snake-arena: 0.93 at 160–192 players) while the process has cores to spare. Check the loop threads' CPU with a per-thread view: `py-spy dump` shows them as `djust-loop-N`, and so does `top -H -p <pid>` on Linux, where Python 3.14 gives threads their names.
- **Start with 2.** Go to 4 only on a host with spare cores after the pool, and measure: each loop adds loop overhead.
- **Grow `worker_threads` with the loops**, and leave a core per loop outside the pool.

## Memory under overload

On free-threaded CPython, every OS thread that allocates gets its own allocator heap, and the memory stays resident after the thread exits. What decides a process's RSS under overload is therefore **how many threads it has had at once**, as well as how many sessions it holds.

### Where the threads come from: one per HTTP request

Django's ASGI handler runs every HTTP request in its own new thread, through asgiref's `ThreadSensitiveContext`. Normally only a few requests are in flight. Under overload the event loop falls behind and requests take seconds, so hundreds are in flight at once, and that means hundreds of threads.

Measured in the snake-arena process, on 3.14t with `worker_threads=5` (#3114):

| page GETs (concurrent × rounds) | peak threads | RSS after the burst (also its peak) | live Python heap after |
|---|---|---|---|
| 256 × 1 | 259 | 81 → 1263 MB | 89 MB (RSS 1340 MB)† |
| 64 × 12 | 66 | 81 → 867 MB | – |
| 8 × 96 | 10 | 81 → 468 MB | – |
| 256 × 3 with `PooledHTTP` | 7 | 81 → 406 MB | – |

† From a separate run with `tracemalloc` on.

- **Each concurrent request thread left about 4 MB resident.** The live heap was a small fraction of that.
- **Nothing gave the memory back:** neither mimalloc's purge options nor a cyclic `gc.collect()`.

`djust.worker_pool.PooledHTTP` fixes this. It binds each HTTP request to one thread of a small pool before Django's handler runs, and because `ThreadSensitiveContext` keeps an outer choice, the handler reuses that thread:

```python
from djust.worker_pool import PooledHTTP

application = ProtocolTypeRouter({
    "http": PooledHTTP(get_asgi_application()),
    "websocket": ...,
})
```

- **Which thread.** A request goes to the thread with the fewest requests bound to it. That count is not how busy the thread is: a request whose client disconnects mid-view releases its slot while its sync code finishes, and an async streaming response keeps its slot while using no thread.
- **Pool size.** `threads=None`, the default, uses the size of the WebSocket pool (`LIVEVIEW_CONFIG["worker_threads"]`). While that setting is off, every request passes through unchanged. An integer sets the size, and `0` passes through. Wrapped apps of the same size share one pool.
- **Separate threads.** The HTTP pool's threads are named `djust-http-N` and are separate from the WebSocket sessions' threads, so a burst of page loads does not queue behind game frames.
- **What changes under a burst.** At most `threads` requests run sync code at once, and the rest wait on the event loop as coroutines, not threads. That is the thread model of a WSGI server, with the same caveats:
  - a sync view, or a sync streaming iterator, that blocks for a long time holds its thread, and the requests bound to that thread wait behind it;
  - a sync view that blocks until another request's sync code has run can wait forever if both land on one thread;
  - state kept on a thread outlives the request. Each pool thread keeps its database connection between requests (Django still runs `close_old_connections` at each request's start and end), and a `threading.local` that code sets without clearing is seen by that thread's next request.
- **What does not change.** WebSocket and lifespan scopes pass through. SSE and streaming responses keep their slot while they stream, but only their sync work runs on it.

### What else holds memory

The snake-arena process was stepped 64 → 192 → 256 clients, 60 s each, then left idle for 120 s. Settings: 3.14t, `worker_threads=5`, scoped push, `djust.layers.InMemoryChannelLayer`.

| | without `PooledHTTP` | with `PooledHTTP` |
|---|---|---|
| RSS at 64 clients | 277 MB | 252 MB |
| peak RSS (256 clients) | 2030 MB | 936 MB |
| RSS after the clients left and 120 s idle | 2030 MB | 936 MB |
| p95 event round trip at 256 clients | 6.7–7.2 s | 0.32–0.33 s |
| frames per client per second at 256 | 3.5–3.7 | 4.1–4.7 |
| p95 gap between frames at 256 | 680–707 ms | 340–353 ms |

The timings come from a shared 12-core machine: load average 5–24 for the first run and 4–7 for the second. The memory numbers are much less sensitive to that.

- **Sessions.** Expect about **2–3 MB of RSS per connected client** on 3.14t with a pinned pool (252 MB at 64 clients above, up from 116 MB idle: 2.1 MB each). That covers the view, its Rust render state and the Django session.
- **The state backend.** `InMemoryStateBackend` keeps about 270 KB per session for `SESSION_TTL` (see [Deployment](deployment.md#in-memory-development-only)).
- **RSS levels off; it does not fall.** Freed memory stays with the allocator, both CPython's mimalloc heaps and the C allocator used by the Rust engine, and is reused for the next load. Size the container for the peak.
  - On Linux, glibc also creates an arena per thread, which is one more reason to bound threads. `MALLOC_ARENA_MAX=2` caps it.
- **Disconnected sessions wait for the cyclic collector.** A consumer and its view reference each other, so they are freed by `gc`, not the moment the socket closes. Free-threaded CPython runs the collector when allocation grows, so on a server that goes idle after a burst, dead sessions can stay alive until traffic returns. That memory is reused, not leaked.
- **Queues stay bounded.** In the overloaded runs:
  - deferred server pushes stayed at 0, because pushes that arrive while a session is busy coalesce into one render and are capped at 64;
  - the in-memory channel layer held at most 53 messages per channel, against its `capacity` of 100, after which Channels drops new messages;
  - transport write buffers stayed empty;
  - the websockets inbound queue stayed at its limit of 32 per connection or less.

## The multi-process alternative: Redis

Stock CPython scales out with more processes:

```bash
uvicorn myproject.asgi:application --workers 8
```

```python
CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer",
                              "CONFIG": {"hosts": [{"address": REDIS_URL, "socket_timeout": 10}]}}}
DJUST_CONFIG = {"STATE_BACKEND": "redis", "PRESENCE_BACKEND": "redis", "REDIS_URL": REDIS_URL}
```

The channel-layer host sets `socket_timeout` above 5 s on purpose: redis-py 8 lowered its default `socket_timeout` to 5 s, the same as channels_redis' blocking read, so without a longer timeout idle WebSockets drop every few seconds (django/channels_redis#422).

This scales about linearly with workers. The #3074 estimate was roughly 40 clients per core per process for the snake game on 3.12. It costs:

- **Redis** for the channel layer, state and presence;
- **no shared in-process state.** A room object in one process is invisible to the others, so either keep all shared state in Redis or the database, or route every session of a room to the same worker, for example by hashing the room in the URL path at the load balancer;
- **scoped push still matters.** Without it, Redis carries the rooms × sessions fan-out instead of the event loop.

| | One 3.14t process | Several 3.12 processes |
|---|---|---|
| Cores used | several per process | one per process |
| In-process state (rooms, caches) | works | needs Redis or sticky routing |
| Extra infrastructure | none | Redis |
| Total capacity | bounded by one host | scales with workers and hosts |

Both approaches combine: several free-threaded processes, each using several cores, with Redis between them.
