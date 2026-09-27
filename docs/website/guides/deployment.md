---
title: "Production Deployment"
slug: deployment
section: guides
order: 9
level: intermediate
description: "Deploy djust with uvicorn, Redis state backend, and Nginx load balancing"
---

# Production Deployment

This guide covers deploying djust applications to production with horizontal scaling, Redis state backend, and WebSocket-aware load balancing.

Before running more than one process or pod, read [Scaling djust](scaling.md). It lists the settings a multi-pod deployment needs, with measured numbers and failover behaviour. In particular, the Redis state backend on its own does not bring a view's state back after a reconnect to another process: that takes `enable_state_snapshot` or `state(..., persist="server")` and a session store every process shares (see [Option B](scaling.md#option-b-redis-between-processes)).

## Architecture Overview

```
                +--------------+
                | Load Balancer|
                |   (Nginx)    |
                +------+-------+
                       |
      +----------------+----------------+
      |                |                |
+-----v-----+   +-----v-----+   +-----v-----+
| Server 1  |   | Server 2  |   | Server 3  |
| (uvicorn) |   | (uvicorn) |   | (uvicorn) |
+-----+-----+   +-----+-----+   +-----+-----+
      |                |                |
      +----------------+----------------+
                       |
                 +-----v-----+
                 |   Redis   |
                 |  (State)  |
                 +-----------+
```

The diagrammed setup is **Tier 1** (pilot / soft-launch, ≤50 concurrent active users). For larger deployments — auto-scaling, dedicated channel-layer Redis, RDS Proxy, CloudFront — see [Sizing and Scaling Tiers](#sizing-and-scaling-tiers) below.

## State Backend Configuration

### In-Memory (one process)

```python
DJUST_CONFIG = {
    'STATE_BACKEND': 'memory',
    'SESSION_TTL': 3600,
}
```

One process only, and the cache is lost on restart. That makes it the right
choice for development and also for production when a single process serves
every WebSocket, such as one free-threaded process using several cores (see
[Scaling djust](scaling.md#one-process-across-cores)). Use Redis once there is
more than one web process. (The channel layer is a separate question: pushes
from Celery workers or management commands need a Redis channel layer even
with one web process; see [Channel Layer](#channel-layer-for-cross-process-push).)

**Memory and `SESSION_TTL`.** The in-memory backend keeps one entry per
(session, page): the page's compiled `RustLiveView` with its last render, which
the WebSocket mount reuses instead of compiling and rendering from scratch.
In a snake-arena load test this was about 270 KB of live heap per session.
An entry is written when the page is served and when the WebSocket mounts, not
on events. It expires once it has not been written for `SESSION_TTL` seconds
(default 3600). Reading an expired entry is a miss, so the mount builds a fresh
one. Writes sweep expired entries at most once every `min(SESSION_TTL, 60)`
seconds. `SESSION_TTL = 0` means never expire. So the backend holds roughly *new
(session, page) visits per second × `SESSION_TTL`* entries. At 5 new visits a
second with the default hour, that is 18,000 entries (about 5 GB at 270 KB each). Lower
`SESSION_TTL` to the reconnect window you actually need, or use Redis.

Before 1.2.2 and 1.3 the TTL was applied only by `djust clear` and
`cleanup_expired_sessions()`, so entries stayed until the process exited
(#3080).

Expect process RSS to level off, not fall, when sessions expire. The allocator
keeps freed pages and reuses them for new sessions. Measure growth with the
entry count (`get_backend().get_stats()["total_sessions"]`) or the allocator's
in-use figure, not with RSS alone.

### Redis (Production)

```python
import os

DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    'SESSION_TTL': 120,  # your reconnect window; see Choosing SESSION_TTL
}
```

Requires Redis 6.0+ and `redis-py`:

```bash
pip install redis
```

### Deploy-time state invalidation

Each session's `RustLiveView` is cached in Redis (default TTL 1 hour) and used as the diff baseline on WebSocket reconnect. When you deploy a new release that changes a template's structure, attributes, or whitespace, a reconnecting client's cached pre-deploy view will not match the new render — the resulting patches target text-node positions that no longer exist, the patch fails, recovery HTML may be unavailable on a fresh consumer, and the user is forced through a `window.location.reload()`.

**Since v0.9.4 this is handled automatically.** Per PR [#1367](https://github.com/johnrtipton/djust/pull/1367), the Redis cache key includes an 8-hex template-source hash:

```
djust:<session>_liveview_<request_path>[_<query_hash>]_t<template_8hex>
```

(`djust:` is the default `REDIS_KEY_PREFIX`; `<request_path>` is the page's URL path.) Edit any byte of a primary template, the per-template hash flips, the cache key flips, the next reconnect misses the cache, and a fresh `RustLiveView` is constructed cleanly with no stale baseline.

Operators no longer need to manually rotate the prefix on every deploy. Earlier releases (pre-v0.9.4) of djust required a pattern like:

```python
# settings.py — pre-v0.9.4 only; NO LONGER NEEDED
import os
_BUILD_ID = os.environ.get("BUILD_ID", "dev")[:12]  # truncated git SHA

DJUST_CONFIG = {
    "STATE_BACKEND": "redis",
    "REDIS_URL": "redis://...",
    "REDIS_KEY_PREFIX": f"djust:{_BUILD_ID}:",  # obsolete since v0.9.4
}
```

**Multi-template caveat.** The cache key uses the **primary** template's hash. If you use `{% include %}` / `{% extends %}` patterns and edit only a sub-template, the primary's source bytes don't change → the hash doesn't flip → the old key keeps hitting until the existing entries TTL out (default 1 hour). For an immediate invalidation in that edge case, run `djust clear --all` after the deploy. Stale entries from before the deploy still expire within `SESSION_TTL` (default 3600 s) regardless.

### Recovery HTML semantics

When a VDOM patch fails on the client, djust falls back to the server-rendered "recovery HTML" snapshot to repair the DOM without forcing a page reload. Two important properties to be aware of in production deployments:

1. **Recovery HTML is per-consumer and one-shot.** After it's used once, the consumer clears `self._recovery_html = None`. Any subsequent patch failure within the same WebSocket lifetime falls through to the forced-reload error ("Recovery HTML unavailable — the server may have restarted. A page reload will fix this.").

2. **Fresh consumers start with no recovery state at all.** A fresh consumer is spawned on every WebSocket reconnect — including the natural ones (rolling deploys, autoscaling events, network blips). The new consumer has no recovery HTML staged until the next successful render produces one. Any patch failure in the brief window before the first render lands skips the recovery branch entirely and goes straight to the forced reload.

**Multi-task deployments amplify the user-visible impact.** With auto-scaling and rolling-deploy patterns, the proportion of WebSocket frames hitting freshly-spawned consumers is non-trivial. Any patch-failure trigger that exists at all (stale state, structural shift, race) gets channeled into the forced-reload path more often than the per-failure rate would suggest.

**Cross-reference.** v0.9.4-1's keyed conditional VDOM diff (#1358 / PR [#1365](https://github.com/johnrtipton/djust/pull/1365)) is the architectural escape hatch: it eliminates the most common patch-failure trigger by making `{% if %}` structural changes diff cleanly via `dj-if` boundary markers, so the recovery path is exercised much less often in normal operation.

## Channel Layer (for cross-process push)

`DJUST_STATE_BACKEND` and Django Channels' `CHANNEL_LAYERS` are **two separate concerns** that both happen to use Redis. Don't conflate them:

| Concern | Setting | What it does |
|---|---|---|
| **State backend** | `DJUST_STATE_BACKEND` (or `DJUST_CONFIG['STATE_BACKEND']`) | Caches each session's compiled view (`RustLiveView`) as the diff baseline for its next WebSocket mount. It does not restore a view's state (its assigns) on another process: that needs `enable_state_snapshot` or `state(..., persist="server")` plus a session store every process shares (see [Option B](scaling.md#option-b-redis-between-processes)). |
| **Channel layer** | `CHANNEL_LAYERS` (Django Channels) | Carries messages between sessions: `push_to_view()`, presence, cursor tracking and DB-change notifications. Messages between processes need a Redis layer. |

**Required configuration** any time more than one process serves WebSockets, **or** any code calls [`push_to_view` / `apush_to_view`](../advanced/server-push.md) from another process (Celery workers, management commands, cron jobs):

```python
# settings/prod.py
import os

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{"address": os.environ["REDIS_CHANNEL_URL"], "socket_timeout": 20}],
        },
    },
}
```

```bash
pip install channels_redis
```

**Set `socket_timeout` on every host, longer than 5 s.** The core layer waits
for messages with a blocking `BZPOPMIN` of 5 s (channels_redis'
`brpop_timeout`), and redis-py 8 lowered its default `socket_timeout` from
`None` to 5 s. The socket read then times out at the moment the pop is still
legitimately waiting, and any WebSocket whose channel has nothing to deliver
for 5 s is dropped (django/channels_redis#422). The traceback names redis
(`redis.exceptions.TimeoutError: Timeout reading from ...`), not your channel
layer settings.

- **Use 20.** It clears the 5 s pop with room for network and event-loop
  delays, and a production deployment held an idle WebSocket for 120 s with it
  on redis-py 8.1 (#3210). Any value above 5 works; higher values only detect a dead connection more slowly. The multi-pod
  measurements in [Scaling djust](scaling.md#measured-capacity) ran with 10.
- **Don't use `None`.** It stops the drops too, but a half-open TCP connection
  then hangs on read until TCP keepalive notices (300 s in that deployment).
  With 20 a dead connection fails within 20 s.
- A `?socket_timeout=` in the URL overrides the dict's key, as in redis-py.
  System check `djust.C023` warns when a host keeps redis-py 8's default.

For small deployments, point `REDIS_URL` and `REDIS_CHANNEL_URL` at the same Redis instance — it serves both concerns fine. **Separate the instances** when:

- ElastiCache memory utilization climbs past 70% (the channel layer holds short-lived messages; the state backend holds longer-lived per-view state — eviction policies want to differ).
- You want to scale the channel layer independently (e.g., to a Redis cluster) without touching the state backend.
- You're auditing for blast radius and want a Redis outage to fail one concern at a time.

An in-memory channel layer doesn't cross processes, so a `push_to_view` reaches only the sessions in the process that sent it, without an error. A push from a Celery task, a management command or a cron job therefore reaches nobody. Use an in-memory layer only when **one process** serves every WebSocket **and** nothing pushes from another process: in development, or in production with a single web process and no out-of-process pushes (see [Push from Celery Tasks](../advanced/server-push.md#push-from-celery-tasks)). With free-threaded Python and `worker_threads`, that one process can use several cores (see [More than one core per process](#more-than-one-core-per-process-worker_threads)).

For that single-process case, prefer djust's in-memory layer over Channels' own:

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "djust.layers.InMemoryChannelLayer",
        # "CONFIG": {"clean_interval": 1.0},  # seconds between expiry sweeps; 0 = every message
    },
}
```

It behaves like `channels.layers.InMemoryChannelLayer`, with one difference.
- **Channels' layer** sweeps every channel and group for expired entries on *every* `receive()` and `group_send()`. A broadcast round across N sessions therefore costs O(N²) on the event loop: 17.7 ms per round at 224 sessions in rooms of 4.
- **djust's layer** sweeps at most once per `clean_interval`, which is 4.4 ms per round at 224 sessions.
- The trade-off is timing: an expired message or group membership is removed up to `clean_interval` seconds later.
  - Until then, an expired message may still be delivered to a consumer that finally reads its queue.
  - A queue full of expired messages keeps refusing new ones for that long. `group_send` skips a full channel, as it always has.
  - Messages expire after 60 s by default, so this only affects consumers that have not read for a minute.

With several event loops in one process (`djust serve --loops N` with N > 1), the layer must not belong to one loop. Use `djust.layers.MultiLoopInMemoryChannelLayer` for one process. Of channels_redis' two layers, `djust serve` accepts only `channels_redis.pubsub.RedisPubSubChannelLayer`, which keeps a connection per loop. It refuses `channels_redis.core.RedisChannelLayer` (and subclasses), as it refuses both `InMemoryChannelLayer`s. See [More than one event loop per process](scaling-across-cores.md#more-than-one-event-loop-per-process).

## Redis Setup

### Installation

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install redis-server
sudo systemctl enable redis-server && sudo systemctl start redis-server

# macOS
brew install redis && brew services start redis

# Docker
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

### Production Configuration

Edit `/etc/redis/redis.conf`:

```conf
bind 127.0.0.1 ::1
requirepass your-strong-password-here

# Persistence
save 900 1
save 300 10
save 60 10000

# Memory management. On a Redis that also carries the channel layer or
# presence, evicting keys silently breaks broadcasts and presence: use
# noeviction and size maxmemory, or give the state backend its own instance.
maxmemory 256mb
maxmemory-policy noeviction

# Disable dangerous commands
rename-command FLUSHDB ""
rename-command FLUSHALL ""
```

## ASGI Server

### Uvicorn (Recommended)

```bash
uvicorn myproject.asgi:application \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --ws websockets \
    --log-level warning \
    --access-log
```

**`--workers 4` is four processes.** Each has its own memory, so everything in [More than one process or pod](scaling.md#more-than-one-process-or-pod) applies: a Redis channel layer (with the `socket_timeout` above), Redis state and presence backends, a session store every process shares, `enable_state_snapshot` or `persist="server"` for state that must survive a reconnect, and no in-process shared state such as a dict of rooms. The settings are listed in [Option B](scaling.md#option-b-redis-between-processes). To stay in one process instead, run one worker; on free-threaded Python it can use several cores ([One process across cores](scaling.md#one-process-across-cores)).

**The event loop is uvloop by default.** uvicorn's default `--loop auto` uses uvloop whenever it is installed, and `uvicorn[standard]`, which djust installs, installs it. So do gunicorn's `UvicornWorker` and `djust serve` (its `--loop` also defaults to `auto`). Leaving the flag out does not avoid uvloop. It is a trade-off, measured once (#3095):

- in one round at 256 clients, uvloop used about 13% less event-loop CPU (0.45 against 0.52 cores);
- in one local ramp on macOS at 384 clients, it lost 201 of 384 WebSocket connections while they connected. The macOS accept backlog is the likely cause, but it has not been investigated.

To use asyncio's loop instead, pass `--loop asyncio` to `uvicorn` or to `djust serve`. For gunicorn, use a worker class whose `CONFIG_KWARGS` sets `"loop": "asyncio"` (see below). Measure with your own connection bursts before choosing.

#### Quantified Daphne → Uvicorn benchmark

Concrete numbers from a 1 vCPU / 2 GB Fargate task running the a representative downstream-consumer app (mixed workload — health endpoint plus a DB-touching home page), identical traffic, single-task per ASGI server:

| Endpoint | Daphne | gunicorn -k uvicorn.workers.UvicornWorker -w 2 |
|---|---|---|
| `/health/` rps @ c=20 | 18.8 | **120.3** (6.4×) |
| `/health/` p99 @ c=20 | 1815 ms | **218 ms** (8.3× faster) |
| `/` (home) rps @ c=10 | 11.3 | **18.5** (1.6×) |

**Disclaimer.** Numbers are from a 1 vCPU / 2 GB Fargate task with the a representative downstream-consumer app. Per-app variance is expected; absolute numbers will differ for your workload, but the relative improvement (~6× rps headroom on health-check endpoints) has been consistent across multiple deployments. The home-page row is a useful indicator that DB-touching pages also gain headroom — just not as dramatically, because the bottleneck shifts from ASGI server overhead to ORM/DB latency.

### Gunicorn + Uvicorn Workers

For containerized production deployments (Fargate, GKE, Cloud Run, Fly.io machines, etc.), Gunicorn supervising Uvicorn workers gives you process-based supervision (worker recycling, graceful reloads, memory-based restarts) on top of Uvicorn's async event loop:

```bash
gunicorn -k uvicorn.workers.UvicornWorker \
    -w 2 \
    -b 0.0.0.0:8000 \
    --timeout 120 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    myproject.asgi:application
```

**Each worker is a separate process.** With `-w 2` or more, everything in [More than one process or pod](scaling.md#more-than-one-process-or-pod) applies, as for `uvicorn --workers` above.

`UvicornWorker` runs uvloop when it is installed (see the uvloop note above). To run asyncio's loop, point `-k` at a subclass:

<!-- doc-snippet-check: skip -->
```python
# myproject/workers.py
from uvicorn.workers import UvicornWorker


class AsyncioUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {"loop": "asyncio", "http": "auto"}
```

and run `gunicorn -k myproject.workers.AsyncioUvicornWorker ...`.

Flag rationale:

- **`-w 2`** — for ASGI workers, the right rule is **`cpu_count`**, not `(2*cpu)+1` (that's for synchronous Gunicorn workers). Each Uvicorn worker is itself async-concurrent; oversubscribing thrashes the event loop. On a 1 vCPU container, `-w 2` gives you 2× headroom on a single CPU at the cost of context-switching — usually still a win for mixed workloads where one worker can be busy with `sync_to_async` while the other handles WS frames.
- **`--timeout 120`** — Gunicorn's default 30s is too short for AI/OCR/upload mutations. Pick a timeout long enough that legitimate slow handlers don't get killed but short enough that runaway tasks get cleaned up. Raise to 300s if you have multi-minute synchronous handlers (consider Celery instead, though).
- **`--keep-alive 5`** — keep slightly less than your LB's idle timeout (AWS ALB defaults to 60s, Cloudflare to 100s). The mismatch direction matters: app-side timeout must be SHORTER than LB-side, otherwise the LB closes a connection the app still thinks is open.
- **`--access-logfile -` and `--error-logfile -`** — log to stdout/stderr so the container collector picks them up. No `logrotate`, no log volume, no permission issues.

When to use Uvicorn standalone (the previous section) vs Gunicorn+Uvicorn:

- **Uvicorn standalone**: single-process dev or single-container with N processes via Uvicorn's own `--workers` flag.
- **Gunicorn+Uvicorn**: production. Worker recycling (`--max-requests`), graceful restarts on SIGHUP, memory-based restarts (`--max-requests-jitter`) all live at the Gunicorn layer. Most cloud-platform health checks expect a Gunicorn-supervised process tree.

Worker count formula:
- For pure-async workloads (`-k uvicorn.workers.UvicornWorker`): `cpu_count` (e.g., `-w 2` on 1 vCPU, `-w 4` on 2 vCPU).
- For sync workloads (`-k sync` — not used by djust): `(2 x cpu_count) + 1`.

### More than one core per process: `worker_threads`

By default every WebSocket session's sync work (`mount`, event handlers, hooks, renders) runs on **one thread shared by all sessions** in the process, so one process does LiveView work on about one core, and one session's slow handler delays every other session's. `LIVEVIEW_CONFIG["worker_threads"]` (djust 1.3, opt-in) gives the WebSocket path a pool of threads instead, with each session pinned to one thread for its lifetime:

```python
LIVEVIEW_CONFIG = {
    "worker_threads": True,  # one thread per available CPU, max 32; or an int
}
```

Before turning it on: module-level state that handlers change needs a `threading.Lock`; each pool thread opens its own database connection (see [Database Connection Pooling](#database-connection-pooling)); and on standard CPython (3.12, 3.13) the GIL still caps Python work at about one core, so the multi-core gain needs free-threaded CPython 3.14t. An invalid value is reported by the system check `djust.C021`.

The pool, what it moves off the event loop, `djust.worker_pool.PooledHTTP` for HTTP requests, and measured numbers are in [Scaling a djust Process Across Cores](scaling-across-cores.md#the-recipe). Where it fits among the other ways to scale is in [Scaling djust](scaling.md#one-process-across-cores).

### More than one event loop per process: `djust serve --loops`

Once the pool spreads the sync work, the asyncio event loop is the next ceiling, at about one core. On free-threaded Python, `djust serve myproject.asgi:application --loops N` runs N event loops in one process on one listening socket (djust 1.3, opt-in; `--loops 1` is plain `uvicorn.run`). It needs a loop-safe channel layer (see [Channel Layer](#channel-layer-for-cross-process-push)) and changes what app code may share between sessions: see [More than one event loop per process](scaling-across-cores.md#more-than-one-event-loop-per-process).

### WebSocket per-message compression (permessage-deflate)

VDOM patches are highly compressible — typical gzip ratios of **60-80% reduction in wire size** for repetitive HTML fragments and JSON patch structures. Both Uvicorn (with the `websockets` library) and Daphne support the `permessage-deflate` WebSocket extension out of the box and negotiate it with any modern browser client.

**No code change needed in your djust app** — the compression is transparent. To verify it's active:

```python
# Confirm the djust config reflects compression state
from djust.config import config
config.get("websocket_compression")  # True by default
```

**Memory tradeoff.** Each active connection holds a zlib compression context, roughly **~64 KB per connection**. For typical deployments (<10k concurrent WebSockets per worker) this is fine — the bandwidth savings dwarf the RSS cost. High-connection-density deployments (100k+ per worker) may want to disable compression:

```python
# settings.py
DJUST_WS_COMPRESSION = False  # disable permessage-deflate negotiation
```

Note that disabling compression in djust's config is **advisory** — the actual wire-level compression is negotiated by the ASGI server. To enforce the no-compression decision at the server level, pass the appropriate flag to Uvicorn / Daphne (e.g. Uvicorn's `--ws-per-message-deflate=false` when using `websockets`). See the [Deployment Checklist](#deployment-checklist) for the full set of ASGI-server flags.

**Do not combine with a compressing CDN.** Cloudflare, AWS CloudFront, and similar CDNs will double-compress and burn CPU on both sides. Either turn off compression at the CDN for the `/ws/` path, or disable it in djust.

## Nginx Configuration

```nginx
upstream djust_backend {
    server 10.0.1.10:8000;
    server 10.0.1.11:8000;
    server 10.0.1.12:8000;
    ip_hash;  # Sticky sessions for WebSocket
}

server {
    listen 443 ssl http2;
    server_name example.com;

    # WebSocket
    location /ws/ {
        proxy_pass http://djust_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }

    # HTTP
    location / {
        proxy_pass http://djust_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Serving the app under a path prefix

When the app lives under a prefix (`https://example.com/app/`, set with
`FORCE_SCRIPT_NAME = "/app"` or passed by the server as `SCRIPT_NAME` /
the ASGI `root_path`), the client has to open its socket under the same
prefix. Put `{% djust_client_config %}` in the base template's `<head>`: it
emits `<meta name="djust-ws-path" content="/app/ws/live/">` from the request's
script prefix, and the client connects there. Without the tag the client
connects to `/ws/live/` at the host root, which is some other app (or
nothing) on a shared host.

The server must then answer the WebSocket at `/app/ws/live/`. Pick one:

- **The proxy strips the prefix** (`location /app/ { proxy_pass http://djust_backend/; }`,
  with the trailing slash). The server sees `/ws/live/`, so the usual
  `path("ws/live/", LiveViewConsumer.as_asgi())` route matches unchanged.
  Keep `FORCE_SCRIPT_NAME = "/app"` so Django's URLs and the emitted
  paths carry the prefix.
- **The server knows its root path** (`uvicorn --root-path /app`, and the proxy
  passes `/app/...` through). Channels' `URLRouter` removes `root_path` from
  the path before matching, so the `ws/live/` route again matches unchanged.
- **Neither**: route the prefixed path yourself,
  `path("app/ws/live/", LiveViewConsumer.as_asgi())`.

Whichever you choose, the nginx `location` for the WebSocket upgrade must
cover the prefixed path (`location /app/ws/`), not only `/ws/`.

To emit a different path, set `DJUST_WS_PATH` in settings; it is used
verbatim in place of the script prefix plus `ws/live/`.

**Upgrade note (#3186).** Before this change the client always connected to
the host-root `/ws/live/`, so an existing prefixed deployment may route only
that path (for example `location /ws/`) and not `/app/ws/live/`. After
upgrading, the client connects to `/app/ws/live/` first. If that first
handshake fails before the socket has ever opened, the client tries
`/ws/live/` once and logs a `console.warn` naming the URL that failed. The
root path is adopted for the rest of the page only if that socket opens; if it
fails too, the normal reconnect backoff goes back to `/app/ws/live/`. So a
deployment that routes only `/ws/live/` keeps its WebSocket, at the cost of
one failed handshake per page load. To remove that cost, either route
`<prefix>/ws/live/` to djust as above, or pin the old path with
`DJUST_WS_PATH = "/ws/live/"` (system check C025 reports a value that is not a
path starting with a single `/`).

A correctly routed deployment also makes this one attempt whenever its first
handshake fails for an ordinary reason, such as a restart or a rollout. On a
shared host, where something else answers at `/ws/live/`, that attempt can
open against the other app, and the page then stays there. Setting
`DJUST_WS_PATH` to your own prefixed path does not avoid the attempt; routing
nothing else at the host-root `/ws/live/` does. A per-site shim that rewrote
the socket URL to add the prefix (djust-docs' `ws-prefix.js`, for example) is
now redundant; it is harmless, because it rewrites only an exact `/ws/live/`.

## Database Connection Pooling

djust apps using Postgres need to manage DB connections carefully — every web process and every Celery worker opens its own connections, and the count multiplies fast. Three layers, in order of effort:

### Layer 1: Django connection reuse (`CONN_MAX_AGE`)

Free, built into Django:

```python
# settings/prod.py
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": ...,
        # Reuse the connection for 60s instead of opening a fresh one per request.
        "CONN_MAX_AGE": 60,
        # Django 4.1+: probe the connection on checkout; reconnect if dead.
        "CONN_HEALTH_CHECKS": True,
    },
}
```

Sufficient for small deployments (< 10 web processes total). Each process keeps a small pool of warm connections; the request-rate amortizes the connect cost.

### Layer 2: PgBouncer (cloud-agnostic external pooler)

When `CONN_MAX_AGE` isn't enough — typically when you have many small containers, or you start hitting Postgres's `max_connections` ceiling — run PgBouncer in front of Postgres:

```ini
# /etc/pgbouncer/pgbouncer.ini
[databases]
* = host=postgres-primary.internal port=5432

[pgbouncer]
listen_port = 6432
listen_addr = 0.0.0.0
auth_type = md5
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
```

App-side: point `DATABASE_URL` at the PgBouncer port (`6432`) instead of Postgres (`5432`).

**Caveat — transaction-pooling mode breaks LISTEN/NOTIFY**: PgBouncer transaction mode multiplexes connections across clients between transactions, so `LISTEN` registrations don't survive. djust's database-change notifications feature uses `LISTEN/NOTIFY`; if you use that feature, either run PgBouncer in `pool_mode = session` (less efficient) or run a dedicated direct-connection path for the listener. Same caveat applies to other Postgres features that rely on session-scoped state (advisory locks across statements, cursors, prepared statements).

### Layer 3: Cloud-managed pooler (AWS RDS Proxy / GCP Cloud SQL Proxy)

Cloud-specific managed alternative to PgBouncer. AWS RDS Proxy example:

```hcl
# Terraform
resource "aws_db_proxy" "main" {
  name                   = "djust-app-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.rds_proxy.arn
  vpc_subnet_ids         = var.private_subnet_ids
  require_tls            = true

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "REQUIRED"
    secret_arn  = aws_secretsmanager_secret.db.arn
  }
}
```

Tuning:
- **`max_connections_percent = 75`** — leave 25% of your DB's `max_connections` for ad-hoc admin connections, migrations, and emergency direct access.
- **`idle_client_timeout`** — match your Django `SESSION_COOKIE_AGE`. When a logged-out user's session expires, their app-side connection cleans up in sync.
- **`require_tls = true`** — the proxy terminates TLS to the proxy itself; the proxy-to-DB hop runs in the VPC.

When using RDS Proxy, **set `CONN_MAX_AGE = 0` in Django** — let the proxy pool. Double-pooling (Django pool + RDS Proxy pool) defeats the proxy's purpose and can cause stale-connection errors.

### When to escalate

- **Tier 1**: `CONN_MAX_AGE = 60`. Sufficient up to ~30 active Aurora connections.
- **Tier 2**: PgBouncer or RDS Proxy. Required when `max_connections` ceiling is in sight or when your container count keeps growing.
- **Tier 3**: same poolers, sized up; add Aurora read replicas for read/write split (separate concern; covered in [Sizing and Scaling Tiers](#sizing-and-scaling-tiers)).

## Docker Compose

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-data:/data

  web:
    build: .
    command: uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000
    environment:
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SESSION_TTL=120  # djust doesn't read this itself; see the note below
    depends_on:
      - redis
    deploy:
      replicas: 3

  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - web

volumes:
  redis-data:
```

djust reads the session TTL only from `DJUST_CONFIG["SESSION_TTL"]`, not from
the environment. To use the `SESSION_TTL` variable above, wire it in
`settings.py`: `'SESSION_TTL': int(os.environ.get('SESSION_TTL', 3600))`.

## Celery Integration

Most production djust apps use Celery for background work — long-running mutations, scheduled jobs, OCR/AI calls, email sending, scheduled DB cleanup. djust doesn't require Celery, but if you have it, deploy it correctly.

### Why Celery for djust apps

- **Long-running mutations**: handlers that need to call slow external APIs (Textract, S3, third-party AI). Better to enqueue and notify the user when done than block the WebSocket event handler.
- **Side-effect isolation**: emails, audit logs, third-party webhooks. Failure of these shouldn't fail the user's request.
- **Scheduled jobs**: deadline checks, periodic syncs, cleanup tasks via `celery beat`.

### Enqueue from views: `transaction.on_commit()`

Always enqueue Celery tasks via `transaction.on_commit()`, never directly:

```python
from django.db import transaction
from .tasks import process_document

@event_handler
def upload_document(self, file):
    doc = Document.objects.create(file=file)
    transaction.on_commit(lambda: process_document.delay(doc.id))
```

Why: a direct `.delay(doc.id)` enqueue can fire BEFORE the transaction commits. The Celery worker then picks up the task, queries for `Document.objects.get(pk=doc.id)`, and gets `DoesNotExist` — race between web-side write and worker-side read.

### Broker choice: Redis vs SQS

| Broker | Use case | Pros | Cons |
|---|---|---|---|
| **Redis** | Self-hosted; small/medium deployments; same Redis already in use | Low ops, fast, in-process at scale | Single point of failure unless clustered; message durability depends on persistence config |
| **AWS SQS** | AWS-native deployments | Managed, durable, scales horizontally for free, IAM-authed | AWS-specific, slightly higher latency, requires `kombu[sqs]` |

Redis broker config:

```python
# settings/prod.py
CELERY_BROKER_URL = os.environ["CELERY_BROKER_URL"]  # redis://:pw@host:6379/1
CELERY_RESULT_BACKEND = os.environ["CELERY_RESULT_BACKEND"]  # redis://:pw@host:6379/2
```

SQS broker config:

```python
# settings/prod.py
if CELERY_BROKER_URL.startswith("sqs://"):
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        "region": os.environ.get("AWS_REGION", "us-east-1"),
        "predefined_queues": {
            "celery": {"url": os.environ["CELERY_SQS_QUEUE_URL"]},
            "default": {"url": os.environ["CELERY_SQS_QUEUE_URL"]},
        },
    }
```

```bash
pip install 'celery[redis]'   # for Redis broker
pip install 'kombu[sqs]'      # for SQS broker (alongside celery)
```

### Pool choice: prefork vs gevent

| Pool | Use case | Concurrency rule |
|---|---|---|
| **`prefork`** (default) | CPU-bound tasks, lots of RAM per task, can't run untrusted code in shared process | `--concurrency=N` ≈ `vCPU * 1` (Django bootstrap costs RAM per process) |
| **`gevent`** | I/O-bound tasks: HTTP calls, S3, SES, Textract, third-party APIs | `--concurrency=N` ≈ `20-50` per vCPU for purely I/O |

For djust apps doing lots of network I/O (file uploads, AI/OCR, email), **gevent is dramatically more efficient**: 20+ in-flight tasks on a single vCPU vs 1 with prefork.

```bash
celery -A myproject worker -l info \
    -Q celery,default \
    --concurrency=20 \
    --pool=gevent \
    --without-gossip --without-mingle
```

```bash
pip install gevent
```

**gevent monkey-patch gotcha**: gevent patches `socket`, `ssl`, `time`, etc. at import time — and the patches MUST be applied **before** `import django.*`. The `--pool=gevent` flag handles this for the standard worker entrypoint, but if you have a custom startup script that imports Django modules before invoking Celery, you need to call `gevent.monkey.patch_all()` first explicitly. Symptom of this bug: occasional `RecursionError` or `ssl: WRONG_VERSION_NUMBER` from gevent-patched code calling pre-patched primitives.

### Beat scheduler is a singleton — never scale it

`celery -A myproject beat` is the scheduler that fires periodic tasks. **Run exactly one instance, ever.** Two beat instances = duplicate task firing = duplicate side effects (double emails, double DB writes, double API calls).

```bash
celery -A myproject beat -l info --schedule=/tmp/celerybeat-schedule
```

If you're running on Fargate / ECS / Kubernetes, lock `desired_count = 1` (or `replicas: 1`) in your infrastructure-as-code AND add a lifecycle guard / comment so a future "let's scale this up" change doesn't accidentally double-schedule. Some teams use `django-celery-beat`'s database scheduler with a leader-election lock; that's overkill for most apps — just don't scale.

### Worker auto-scaling: scale on queue depth, not CPU

I/O-bound workers using gevent have low CPU even when work is piling up — auto-scaling on CPU sees a flat 5% and never scales out. **Scale on queue depth instead**:

- AWS SQS: `ApproximateNumberOfMessagesVisible > 50` → scale-out target.
- Redis broker: `LLEN celery` (queue list length) > 50 → scale-out target.
- RabbitMQ: queue `messages_ready` count.

CPU-bound prefork workers can scale on CPU as usual.

### Beat schedule example

```python
# settings/prod.py
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "clear-django-sessions": {
        "task": "myapp.tasks.clear_django_sessions",  # below
        "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
    },
    "sync-third-party-data": {
        "task": "myapp.tasks.sync_third_party",
        "schedule": crontab(minute=15, hour=2),  # Daily 2:15am
    },
}
```

<!-- doc-snippet-check: skip -->
```python
# myapp/tasks.py
from celery import shared_task
from django.core.management import call_command


@shared_task
def clear_django_sessions():
    call_command("clearsessions")
```

## Session Management

### Choosing `SESSION_TTL`

`DJUST_CONFIG["SESSION_TTL"]` (default 3600 s) is how long the state backend keeps a session's cached view after it was last written. Set it to your **reconnect window**: how long after a disconnect a client should still find its cached view. Longer only costs memory. The multiplayer game in [Scaling djust](scaling.md#memory) uses 120 s.

Size for it: the backend keeps one entry per session **and** LiveView page, so it holds about *new (session, page) visits per second × `SESSION_TTL`* entries: sessions × the LiveView pages each visits within the TTL. That is about 270 KB each in-process in one load test (see [In-Memory](#in-memory-one-process)). At 5 new page visits a second, 120 s is 600 entries (about 160 MB) and the default hour is 18,000 (about 5 GB). Redis expires keys with the same TTL.

No cleanup job is needed. The in-memory backend has applied `SESSION_TTL` by itself since 1.2.2 and 1.3 (#3080), and Redis expires keys on its own. Django's own sessions are separate: with the `db` or `cached_db` session engine, run Django's `clearsessions` command periodically (see the [beat schedule example](#beat-schedule-example)).

## Health Check Endpoint

```python
from django.http import JsonResponse
from djust.state_backend import get_backend

def health_check(request):
    try:
        stats = get_backend().get_stats()
        return JsonResponse({
            'status': 'healthy',
            'backend': stats['backend'],
            'sessions': stats['total_sessions'],
        })
    except Exception as e:
        return JsonResponse({'status': 'unhealthy', 'error': str(e)}, status=503)
```

## Deploying Behind an L7 Load Balancer (AWS ALB, Cloudflare, Fly.io)

When djust runs behind a trusted L7 load balancer that terminates TLS and health-checks
task IPs directly (AWS ALB, Cloudflare, Fly.io, GCP External HTTP(S) LB, etc.), the task's
private IP rotates on every redeploy or autoscale event. Enumerating them in
`ALLOWED_HOSTS` is not feasible, and setting `ALLOWED_HOSTS = ['*']` normally trips
the `djust.A010` system check.

djust provides an explicit opt-in for this topology: set **both** `SECURE_PROXY_SSL_HEADER`
and a new `DJUST_TRUSTED_PROXIES` list/tuple, and `djust.A010` / `djust.A011` will recognize
that the trusted proxy has already performed Host validation at the edge.

```python
# settings.py — only when you really are behind a trusted L7 proxy

ALLOWED_HOSTS = ['*']  # task IPs rotate; edge proxy performs Host validation

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Identify the trusted proxy terminating requests. Any non-empty list/tuple
# opts in — contents are informational and can be used by your middleware.
DJUST_TRUSTED_PROXIES = ['aws-alb']  # or ['cloudflare'], ['fly-edge'], etc.
```

**Security warning.** Only use this escape hatch if you are actually behind a trusted L7
proxy that performs Host validation at the edge. On a raw VM / bare-metal with no proxy in
front, `ALLOWED_HOSTS = ['*']` opens you up to Host-header attacks — don't paper over the
system check with this setting.

If only one of the two settings is present, `djust.A010` / `djust.A011` still fire — both are
required so a single typo can't accidentally disable the check.

### WebSocket stickiness on AWS ALB

ALB stickiness keeps a client's WebSocket connection pinned to the same task across reconnects. The literal solution is `app_cookie` stickiness with an application-set cookie, but the simpler shortcut is to use Django's existing `sessionid` cookie:

```hcl
# Terraform — ALB target group
stickiness {
  type            = "app_cookie"
  cookie_name     = "sessionid"
  cookie_duration = 86400  # 24h
  enabled         = true
}
```

How it works:
1. User loads any page → Django's `SessionMiddleware` sets `sessionid` on the response.
2. Browser opens WebSocket → upgrade request includes the `sessionid` cookie.
3. ALB routes the upgrade by `sessionid` hash → pinned to the same task.
4. If the task dies and the client reconnects, ALB picks a healthy task and the cookie now hashes to the new one. The view's state comes back only if it is persisted (`enable_state_snapshot` or `state(..., persist="server")` with a shared session store); otherwise the view mounts again. See [Option B](scaling.md#option-b-redis-between-processes), which also found stickiness unnecessary in its measured setup.

Caveat: pages that open a WebSocket WITHOUT a prior session-creating request won't have the cookie set yet → the first WS connect goes to whichever target ALB picks. After that, subsequent reconnects have `sessionid` and pin correctly. This is fine when `SessionMiddleware` is in `MIDDLEWARE` (default for any Django app); if you've removed it, you'll want to set a fresh cookie on the WS upgrade response yourself.

For higher-stakes setups (financial, healthcare), a dedicated NLB in front of Daphne/Uvicorn for the WS path with source-IP stickiness gives stronger guarantees — see [Sizing and Scaling Tiers](#sizing-and-scaling-tiers) Tier 3.

## Static and Media Files

For containerized deployments, do NOT serve `/static/` and `/media/` from the ASGI server in production. Each request consumes worker capacity that should be reserved for live-view traffic.

### Cloud-agnostic options

| Option | Use case |
|---|---|
| **WhiteNoise** | Small deployments, single container, no CDN budget |
| **Nginx** in front | Self-hosted bare-metal / VM |
| **S3 + CloudFront** | AWS deployments |
| **GCS + Cloud CDN** | GCP |
| **Cloudflare R2 + Cloudflare CDN** | Multi-cloud / cost-sensitive |

### Cloud storage configuration (S3 example)

```python
# settings/prod.py
AWS_STORAGE_BUCKET_NAME = os.environ["AWS_STORAGE_BUCKET_NAME"]

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "region_name": os.environ.get("AWS_REGION", "us-east-1"),
            "default_acl": "private",       # User uploads — never public
            "file_overwrite": False,
            "querystring_auth": True,        # Presigned URLs for media
            "querystring_expire": 3600,
        },
    },
    "staticfiles": {
        "BACKEND": "storages.backends.s3boto3.S3StaticStorage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME + "-static",
            "region_name": os.environ.get("AWS_REGION", "us-east-1"),
            # Static files are public, long-cached
        },
    },
}

STATIC_URL = f"https://{os.environ['CDN_DOMAIN']}/static/"
```

```bash
pip install 'django-storages[s3]'
```

### Offloading static-file serving from ASGI

When a CDN serves `/static/` directly, your ASGI server doesn't need to. Set:

```bash
# Environment variable
ASGI_SERVE_STATIC=False
```

And in `asgi.py`, gate the static-files handler on this:

```python
import os
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from channels.routing import ProtocolTypeRouter, URLRouter

django_asgi_app = get_asgi_application()

if os.environ.get("ASGI_SERVE_STATIC", "true").lower() in ("true", "1", "yes"):
    http_app = ASGIStaticFilesHandler(django_asgi_app)
else:
    http_app = django_asgi_app  # CDN serves static; ASGI handles only Django views

application = ProtocolTypeRouter({
    "http": http_app,
    "websocket": ...,
})
```

### CloudFront cache behavior split

Configure two cache behaviors on your CloudFront distribution:
- **`/static/*`** — long TTL (1 year), versioned via `ManifestStaticFilesStorage` hashes; static assets never change once deployed.
- **`/media/*`** — `Cache-Control: no-cache`, presigned-URL passthrough; media is per-user and presigned URLs already enforce expiry.

Don't combine `/static/` and `/media/` under one cache behavior — the long-TTL bucket would cache user-private media URLs.

## Deployment Checklist

### Production checklist (one-page recipe)

The 8-line copy-pasteable recipe. Each line links to the relevant subsection of this guide for rationale and config:

```
☐ ASGI server: gunicorn -k uvicorn.workers.UvicornWorker -w <cpu_count> (see rationale)
☐ Channel layer (several processes): channels_redis.core.RedisChannelLayer, host socket_timeout 20
☐ State backend: DJUST_CONFIG["STATE_BACKEND"] = "redis"
☐ State key invalidation: auto-derived from template hash (no env var needed since v0.9.4)
☐ ALB sticky sessions (optional): app_cookie on "sessionid"
☐ CONN_MAX_AGE = 60, CONN_HEALTH_CHECKS = True
☐ App Auto Scaling registered; aws_ecs_service has lifecycle ignore_changes = [desired_count]
☐ Celery: --pool=gevent --concurrency=N for I/O work; queue-depth autoscaling
```

Where each line is covered:

- **ASGI server** → [Gunicorn + Uvicorn Workers](#gunicorn--uvicorn-workers).
- **Channel layer** → [Channel Layer (for cross-process push)](#channel-layer-for-cross-process-push).
- **State backend** → [Redis (Production)](#redis-production).
- **State key invalidation** → [Deploy-time state invalidation](#deploy-time-state-invalidation).
- **ALB sticky sessions** → [WebSocket stickiness on AWS ALB](#websocket-stickiness-on-aws-alb). Optional: the measured multi-pod setup in [Scaling djust](scaling.md#option-b-redis-between-processes) needed no session affinity.
- **`CONN_MAX_AGE` / `CONN_HEALTH_CHECKS`** → [Layer 1: Django connection reuse (`CONN_MAX_AGE`)](#layer-1-django-connection-reuse-conn_max_age).
- **Auto Scaling + `lifecycle ignore_changes`** → [Sizing and Scaling Tiers](#sizing-and-scaling-tiers) (Tier 2).
- **Celery `gevent` + queue-depth autoscaling** → [Pool choice: prefork vs gevent](#pool-choice-prefork-vs-gevent) and [Worker auto-scaling: scale on queue depth, not CPU](#worker-auto-scaling-scale-on-queue-depth-not-cpu).

### Infrastructure

- [ ] Redis 6.0+ installed with password protection
- [ ] Redis bound to private network only
- [ ] `STATE_BACKEND='redis'` in production settings
- [ ] Sensitive config in environment variables
- [ ] ASGI server (uvicorn) with multiple workers
- [ ] Nginx with WebSocket proxy and sticky sessions
- [ ] HTTPS enabled for all connections
- [ ] `proxy_read_timeout 86400` set for WebSocket routes

### Application

- [ ] Django's `clearsessions` scheduled if you use `db` or `cached_db` sessions (djust's state backends expire entries themselves)
- [ ] Health check endpoint added
- [ ] Logging configured for `djust.state_backends`
- [ ] CSRF protection enabled
- [ ] `SESSION_TTL` set to your reconnect window ([Choosing `SESSION_TTL`](#choosing-session_ttl))

### Verification

- [ ] Load test with concurrent WebSocket connections
- [ ] Verify state sharing across multiple servers
- [ ] Test reconnection after server restart
- [ ] Monitor Redis memory under load
- [ ] Confirm browser back/forward works after navigation

## Sizing and Scaling Tiers

Three tiers indexed to concurrent active users. For measured per-pod capacity (clients per pod, CPU per frame, memory per client) and the settings several pods need, see [Scaling djust](scaling.md): its [Capacity reference](scaling.md#capacity-reference) and [Measured capacity](scaling.md#measured-capacity). The numbers below are heuristics for typical mixed-workload djust apps; CPU-heavy apps (large VDOM diffs, complex template rendering) and I/O-heavy apps (lots of `push_to_view`, presence, third-party APIs) shift the curves. Use these as a starting point and adjust based on your own load testing.

**Don't pre-build for higher tiers** — usage data should justify each escalation. Premature scaling adds cost and operational surface area without improving the user experience.

### Tier 1: Pilot / soft-launch (≤50 concurrent active users)

| Component | Sizing |
|---|---|
| Web | 2 instances, 1 vCPU / 2 GB each, `gunicorn -w 2` |
| Worker (if Celery) | 1 instance, 1 vCPU / 2 GB, `--concurrency=20 --pool=gevent` for I/O |
| Beat (if Celery) | 1 instance (singleton), 0.25 vCPU / 0.5 GB |
| Redis | 1 instance for state + channel layer (e.g., `cache.t3.micro` on AWS) |
| Database | Standard managed Postgres, no proxy, `CONN_MAX_AGE=60` |
| Static/media | WhiteNoise OR Nginx OR small CDN |

Two web instances buy you HA + zero-downtime deploys. Single Redis instance is fine.

### Tier 2: Production launch (50-500 concurrent active users)

| Component | Sizing |
|---|---|
| Web | Auto-scale 2-6 instances on CPU 50% target; cooldown 60s scale-out / 300s scale-in |
| Worker | Auto-scale 1-4 on **queue depth** (NOT CPU — I/O-bound) |
| Beat | Stays at 1 |
| Redis | Either upsize the single instance (e.g., `cache.t3.small`) OR separate state vs channel-layer instances |
| Database | Add PgBouncer or RDS Proxy; set `CONN_MAX_AGE=0` if pooling externally |
| Static/media | CDN required (CloudFront / Cloud CDN / Cloudflare); set `ASGI_SERVE_STATIC=False` |

Auto-scaling triggers (AWS CloudWatch examples; equivalent metrics exist on every cloud):

- Web `CPUUtilization > 50%` for 60s → scale out by 1
- Web `CPUUtilization < 30%` for 300s → scale in by 1
- Worker `ApproximateNumberOfMessagesVisible > 50` for 60s → scale out by 1
- Worker `ApproximateNumberOfMessagesVisible < 5` for 300s → scale in by 1

### Tier 3: High-scale (>500 concurrent active users)

| Component | Sizing |
|---|---|
| WebSocket transport | Dedicated NLB in front of ASGI for WS-only path; ALB stays for HTTP |
| Redis | ElastiCache cluster mode (3+ shards) with separate state vs channel-layer instances |
| Database | Aurora read replica(s) + read/write split via `DATABASE_ROUTERS` |
| Caching | Application-level view cache for hot pages (`django.core.cache` Redis-backed) |
| Search | Move full-text search out of Postgres into Elasticsearch / OpenSearch |

Tier 3 is genuinely different — the architecture changes, not just the numbers.

### When to escalate

| From | Trigger |
|---|---|
| Tier 1 → 2 | Web CPU > 60% sustained for an hour, OR Aurora active connections > 30, OR ALB target response p95 > 2s, OR memory > 80% |
| Tier 2 → 3 | Web tasks pegged at max for >2 hours, OR Aurora ACUs sustained > 8, OR ElastiCache memory > 70%, OR ALB request rate > 5k req/s |

Set CloudWatch alarms (or equivalents) for each trigger. Don't escalate on a hunch — wait for data.

## What's Already Production-Ready in djust

Anti-recommendation list. Things you should NOT re-evaluate on every deployment — they're canonical patterns built into the framework:

- **djust state in Redis** is the canonical multi-server pattern (`DJUST_CONFIG["STATE_BACKEND"] = "redis"`). It caches each session's compiled view as the diff baseline for its next mount, on any process. It does not bring a view's state back after a reconnect to another process: that takes `enable_state_snapshot` or `state(..., persist="server")` and a shared session store ([Option B](scaling.md#option-b-redis-between-processes)).
- **`channels_redis` is required for more than one process** when any view uses `push_to_view`, presence, cursor tracking, or any other cross-process feature, and whenever a Celery worker, management command or cron job pushes to views. An in-memory layer reaches only the sessions of its own process. With exactly one process and no out-of-process pushes, `djust.layers.InMemoryChannelLayer` is the right production choice (`djust.layers.MultiLoopInMemoryChannelLayer` under `djust serve --loops`).
- **`sync_to_async` for ORM** in event handlers is handled by djust's event dispatcher: sync handlers run via `sync_to_async`, so no manual wrapping is needed in your view code.
- **`transaction.on_commit()` for Celery enqueue** is the right pattern; never `.delay(...)` directly from a view.
- **WebSocket Origin check (`check_origin`)** is on by default since v0.4.1 (CSWSH protection). Don't disable.
- **HSTS preload, secure cookies, CSP nonce** — all covered by djust's recommended `settings/prod.py`. Disabling for "convenience" opens real attack surface.
- **`@event_handler` decorator** validates handler names + applies rate limits. Don't bypass with raw consumer methods.
- **VDOM diffing in Rust** — there's nothing to "tune". The diff algorithm is O(N log N) keyed-LIS reconciliation; it's already as fast as you can reasonably get without compromising correctness.

If you find yourself building infrastructure to work around any of these (e.g., a custom cross-process push mechanism that bypasses `channels_redis`), step back and confirm the canonical path doesn't already do what you need.

## Troubleshooting

**Redis connection errors**: Verify Redis is running (`redis-cli ping`), check URL and password, confirm firewall rules.

**Session not found after restart**: Ensure `STATE_BACKEND='redis'` and Redis persistence is enabled (`save` directives in `redis.conf`).

**Memory issues**: In Redis, increase `maxmemory`. Don't enable `allkeys-lru` on a Redis that also holds the channel layer or presence: evicting their keys silently breaks broadcasts and presence. Use `noeviction` there, or move the state backend to its own instance, where eviction only costs a cache miss. In the djust process, lower `SESSION_TTL` to your reconnect window, and see [Memory](scaling.md#memory) and the [troubleshooting checklist](scaling.md#troubleshooting-checklist) in Scaling djust (RSS that levels off rather than falls, `PooledHTTP`, idle-time collection).

**Serialization errors**: Ensure djust version matches across all servers, clear Redis cache, and verify Rust extension is compiled for the correct Python version.
