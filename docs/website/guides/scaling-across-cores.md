---
title: "Scaling a djust Process Across Cores"
slug: scaling-across-cores
section: guides
order: 13.5
level: advanced
description: "Use more than one CPU core per process: free-threaded Python, the worker_threads pool, scoped push and the in-process channel layer, with measured numbers and the Redis multi-process alternative."
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
3. **The asyncio event loop.** Every frame for every session is decoded, dispatched and encoded on one loop thread. Once the sync work moves elsewhere, the loop becomes the next ceiling.

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

Then run on free-threaded CPython:

```bash
uv python install 3.14t
uv venv --python 3.14t && uv pip install djust uvicorn   # cp314t wheels
uvicorn myproject.asgi:application --ws websockets
```

Some of djust's dependencies (autobahn, through `channels[daphne]`) have no free-threaded wheels yet on every platform. They build from source during install, so the install takes longer; it does not change anything else.

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
- **Other transports.** HTTP and SSE are unchanged.

With the pool on, djust also moves per-frame work off the event loop:
- the pre-event snapshot of the view's state runs on the session's thread;
- a server push to a view that doesn't use ADR-038 explicit exposure runs as a single hop on the session's thread;
- when a push frame carries nothing but patches, the rendered patch goes into it without being re-encoded on the loop.

What to know before turning it on (details in [Deployment](deployment.md#more-than-one-core-per-process-worker_threads)):

- **Shared state needs locks.** Sync handlers now run concurrently with other sessions' handlers, so module-level state they mutate needs a `threading.Lock`.
- **Database connections.** Each pool thread holds its own database connection.
- **Sessions on one thread wait on each other**, though no longer on the whole process.
- **Pool size.** Start at about the core count. On 3.14t, pools of 8–12 threads on a 12-core machine cost 2.2–2.7 MB per session. One thread per session cost 5.4 MB and gave lower tail latency.

### 3. Scoped push

`push_to_view(view, ...)` reaches every session of the view. In a view with many rooms, every room's broadcast wakes every session in every room. In one profile that meant 57,120 `server_push` calls for 3,727 real renders, and it pins the event loop long before the cores are busy.

Set `self.push_scope` and push with `scope=` so a broadcast reaches only its room; see [Scoped Push](../advanced/server-push.md#scoped-push-one-room-not-every-room).

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

## The multi-process alternative: Redis

Stock CPython scales out with more processes:

```bash
uvicorn myproject.asgi:application --workers 8
```

```python
CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer",
                              "CONFIG": {"hosts": [REDIS_URL]}}}
DJUST_CONFIG = {"STATE_BACKEND": "redis", "PRESENCE_BACKEND": "redis", "REDIS_URL": REDIS_URL}
```

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
