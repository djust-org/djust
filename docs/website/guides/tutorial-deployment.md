---
title: "Tutorial: Ship a djust app to production"
slug: tutorial-deployment
section: guides
order: 58
level: intermediate
description: "Take a working LiveView from `make dev` to a real production deploy: ASGI server, shared sessions and channel layer, Redis VDOM cache, Nginx WebSocket proxy, and how to persist view state across workers."
---

# Tutorial: Ship a djust app to production

Going from `make dev` to a real production deploy is where djust
apps trip people up. The dev server (`uvicorn --reload`) runs a
single process with in-memory state — perfect for local hacking,
which can be too small for production traffic. A multi-process
deployment needs shared durable Django sessions and a cross-process
channel layer. A Redis state backend can share the compiled-view
cache used as a diff baseline, but it does not transfer a LiveView's
Python state to another process. Persist state that must survive a
reconnect, and use stickiness only for app-level state that lives in one
process's memory (a dict of rooms, a game clock).
Then put Nginx in front and serve the WebSocket upgrade over HTTPS.

By the end of this tutorial you'll have:

- A **production-ready ASGI app** behind multiple uvicorn workers
  with shared sessions and a channel layer, plus an explicit choice
  about state that must survive reconnects.
- An **Nginx config** that proxies HTTP and upgrades WebSocket
  connections.
- A clear answer on **sticky sessions**: why a view's state never needs
  them, and when app-level in-process state does.
- **Healthcheck endpoints** that load balancers can probe.
- The **four production checks** every team adds in week 2 and
  wishes they'd added on day one: graceful shutdown, error
  monitoring, log structure, and the security headers checklist.

| You'll learn | Documented in |
|---|---|
| Redis VDOM cache + a Redis channel layer | [Production Deployment](deployment.md) |
| Uvicorn worker count + reload behavior | [Deployment](deployment.md) |
| Nginx WebSocket proxy directives | [Deployment](deployment.md) |
| What several processes need from each other | [Scaling djust](scaling.md) |
| When sticky sessions matter | This tutorial |
| Production-readiness checklist | This tutorial |

> **Prerequisites:** A working djust app you've been running with
> `make dev` (any of the prior tutorials will do). Linux server
> access, basic systemd / nginx familiarity, a domain name with
> DNS pointing at the server, a Redis instance reachable from
> the server.

---

## Step 1 — The settings change

The development settings.py needs a handful of production swap-outs:

```python
# settings.py
import os

DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

# Never ship the key from your dev settings. A KeyError at startup is the
# point: a production process without a real key should not boot.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "myapp"),
        "USER": os.environ.get("DB_USER", "myapp"),
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ.get("DB_HOST", "localhost"),
    },
}

DJUST_CONFIG = {
    "STATE_BACKEND": "redis",
    "PRESENCE_BACKEND": "redis",
    "REDIS_URL": os.environ["REDIS_URL"],   # required, not optional
    "SESSION_TTL": 120,  # cached-view reconnect window; separate from Django auth-session expiry
}

# Cross-process messages (push_to_view, presence, broadcasts). An
# in-memory layer doesn't cross processes, so with --workers 4 a push
# from one worker never reaches clients on the other three.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            # socket_timeout above 5 s: redis-py 8 defaults it to 5 s, the
            # same as channels_redis' receive timeout, which drops idle
            # WebSockets every few seconds (#3199). 20 leaves headroom and
            # still fails a dead connection fast; don't use None. System
            # check djust.C023 warns when this is missing.
            "hosts": [{"address": os.environ["REDIS_URL"], "socket_timeout": 20}],
        },
    },
}

# Sessions every worker can read: the page GET and the WebSocket may
# land on different workers.
SESSION_ENGINE = "django.contrib.sessions.backends.db"

ALLOWED_HOSTS = ["yourapp.com", "www.yourapp.com"]

# Trust the load balancer's X-Forwarded-Proto so request.is_secure()
# returns True for HTTPS-fronted requests. Without this, every
# WebSocket upgrade tries to negotiate over plain HTTP and breaks.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

The production swap-outs:

| In dev | In prod | Why |
|---|---|---|
| `DEBUG = True` | `DEBUG = False` | Stack traces are info disclosure; templates cache; auto-reload off |
| `SECRET_KEY` in settings.py | `SECRET_KEY` from the environment | Signs sessions, CSRF and password-reset tokens; a key checked into the repository is a key an attacker has |
| `STATE_BACKEND='memory'` | `STATE_BACKEND='redis'` | Workers share the compiled-view diff cache; persist view state separately if it must survive a reconnect on another process |
| In-memory channel layer | `RedisChannelLayer` with `socket_timeout` > 5 | Pushes and presence reach clients on every worker |
| Per-process sessions (`locmem` cache, files) | `db` or `cached_db` sessions | The page load and the WebSocket may hit different workers |
| `ALLOWED_HOSTS=['*']` | Real domain list | Defense against host-header attacks |
| (no `SECURE_PROXY_SSL_HEADER`) | Set to `('HTTP_X_FORWARDED_PROTO', 'https')` | WebSocket upgrade respects HTTPS |

---

## Step 2 — Uvicorn under systemd

```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=djust app (myapp)
After=network.target redis.service postgresql.service
Wants=redis.service

[Service]
Type=simple
User=myapp
Group=myapp
WorkingDirectory=/opt/myapp
Environment="DJANGO_SETTINGS_MODULE=myproject.settings"
# Secrets live in a root-owned file only root can read, not in this
# unit: unit files are world-readable and `systemctl show` prints every
# Environment= line to any local user.
EnvironmentFile=/etc/myapp/env
ExecStart=/opt/myapp/.venv/bin/uvicorn \
    myproject.asgi:application \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --proxy-headers \
    --forwarded-allow-ips="127.0.0.1" \
    --timeout-graceful-shutdown 30
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Put the secrets in `/etc/myapp/env`, one `KEY=value` per line:

```ini
# /etc/myapp/env  (owner root, mode 0600)
DJANGO_SECRET_KEY=<50+ random characters>
DB_PASSWORD=<database password>
REDIS_URL=redis://localhost:6379/0
DEBUG=False
```

```bash
sudo install -d -m 0755 /etc/myapp
sudo touch /etc/myapp/env
sudo chown root:root /etc/myapp/env
sudo chmod 0600 /etc/myapp/env
# Generate a key:
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

systemd reads `EnvironmentFile=` as root before it drops to
`User=myapp`, so the file can stay root-only. After editing it, run
`sudo systemctl restart myapp`.

Three production-relevant flags:

- **`--workers 4`** — one process per CPU core is the standard
  starting point. Each worker has its own memory; they share
  the configured session store and channel layer. The Redis state
  backend shares the compiled-view diff cache, not live view state.
  Persist state that must survive a reconnect to another worker. More workers = more concurrent connections
  but also more memory.
- **`--proxy-headers --forwarded-allow-ips="127.0.0.1"`** — trust
  `X-Forwarded-For` / `X-Real-IP` headers from the local Nginx,
  so `request.META["REMOTE_ADDR"]` is the real client IP, not
  `127.0.0.1`.
- **`--timeout-graceful-shutdown 30`** — when systemd sends
  SIGTERM (deploy, reboot), uvicorn stops accepting new
  connections but lets in-flight ones finish for up to 30 s.
  Without this, a deploy mid-WebSocket disconnects every user.

---

## Step 3 — Nginx in front

```nginx
# /etc/nginx/sites-enabled/myapp.conf
upstream myapp {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name yourapp.com www.yourapp.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourapp.com www.yourapp.com;

    ssl_certificate     /etc/letsencrypt/live/yourapp.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourapp.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;

    # Static files served by Nginx, not Django (faster, no Python in path)
    location /static/ {
        alias /opt/myapp/staticfiles/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Healthcheck (no Django; load-balancer probes hit this)
    location = /nginx-health {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }

    # Everything else — HTTP and WebSocket — goes to uvicorn
    location / {
        proxy_pass http://myapp;
        proxy_http_version 1.1;

        # WebSocket upgrade headers — the line everyone forgets
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Real client IP + scheme (paired with SECURE_PROXY_SSL_HEADER)
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Long-lived WebSocket connections need a long read timeout
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }
}
```

Three lines that get forgotten the most:

- **`proxy_set_header Upgrade $http_upgrade;`** + **`proxy_set_header Connection "upgrade";`** — required for WebSocket. Without them, the upgrade negotiation fails and every reactive feature falls back to HTTP polling (or doesn't work at all).
- **`proxy_set_header X-Forwarded-Proto $scheme;`** — paired with Django's `SECURE_PROXY_SSL_HEADER`. Without this, the Django app thinks every request is HTTP even though Nginx is serving HTTPS, and `request.is_secure()` returns False.
- **`proxy_read_timeout 3600;`** — Nginx defaults to 60 s. A WebSocket sitting idle for 65 s gets killed. Bump to 1 hour (or whatever matches your session timeout).

---

## Step 4 — Sticky sessions: when, when not

WebSocket connections are sticky by definition — once a client
opens a WS to worker N, all subsequent frames go to worker N.
That's not the issue. The issue is the **next page load** in the
same browser session.

If sticky sessions are OFF (default for most load balancers), the
page load and the WebSocket, or a reconnect after a network blip,
can land on a different worker. Shared sessions and the channel
layer let the request authenticate and communicate across workers;
they do not preserve arbitrary view state by themselves. Rebuild
state from durable app data, or opt into the persistence described
below. Stickiness does not preserve a view's own state: a reconnect
runs `mount()` again even on the same worker. It only helps app-level
state your code keeps in one process's memory, such as a dict of rooms.

One thing the Redis state backend does **not** do is carry a view's
state to another worker. A default LiveView that reconnects to a
different process runs `mount()` again, and anything it held in
memory is lost (typed-in form fields are re-sent by the client, so
those survive). To keep view state across processes, persist it in
the session:

- legacy views: set `enable_state_snapshot = True` on the view (or inherit from `djust.PersistentLiveView`, which sets it);
- [explicit exposure](../state/explicit-exposure.md) views: declare
  the fields with `state(..., persist="server")`.

So: **a view's state never needs sticky sessions**; persist it or
rebuild it in `mount()`. Use stickiness only when your app keeps
shared state in one process's memory (for example rooms hashed to a
process, as in Scaling djust's sharded option). See
[Scaling djust](scaling.md#more-than-one-process-or-pod) for the
measured multi-process setup.

For Nginx specifically, stickiness needs one upstream entry per
process, so run four single-worker uvicorn services on ports
8000-8003 instead of one `--workers 4` service (the kernel, not
Nginx, spreads connections across `--workers`):

```nginx
upstream myapp {
    ip_hash;             # ← sticky by client IP
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
    keepalive 32;
}
```

Or for Cloudflare / AWS ALB, configure session affinity via the
load balancer dashboard.

---

## Step 5 — The four checks every team eventually adds

### Healthcheck endpoint that touches the DB and Redis

<!-- Imports redis, a deployment dependency the snippet checker
     does not install. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/views.py
import logging

import redis
from django.conf import settings
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthcheck(request):
    """Probed by the load balancer + uptime monitor."""
    checks = {"db": False, "redis": False}
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            checks["db"] = True
    except Exception:
        logger.warning("healthcheck: database unreachable", exc_info=True)
    try:
        r = redis.Redis.from_url(settings.DJUST_CONFIG["REDIS_URL"])
        r.ping()
        checks["redis"] = True
    except Exception:
        logger.warning("healthcheck: redis unreachable", exc_info=True)
    status = 200 if all(checks.values()) else 503
    return JsonResponse(checks, status=status)
```

Wire at `/health/`. Configure the load balancer to mark a worker
unhealthy on 5 consecutive 503s — DB connection storms or Redis
flakes get the bad worker out of rotation before it tanks user
requests.

### Sentry (or equivalent error monitor)

```python
# settings.py
import sentry_sdk

if not DEBUG and (dsn := os.environ.get("SENTRY_DSN")):
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.1,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        send_default_pii=False,  # don't auto-send user data
    )
```

Catches the 500s your error pages render and the unhandled
exceptions in event handlers. `send_default_pii=False` is already
Sentry's default; it is spelled out so nobody flips it casually. Set
to `True`, the SDK attaches user details (id, username, email), IP
addresses, cookies and request bodies to events. Leave it off
unless your privacy policy explicitly allows it.

### Structured logging

```python
# settings.py
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",  # python-json-logger 3.x
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
```

Pipe stdout into the host's log aggregator (CloudWatch, Datadog,
Loki, etc.). Json log lines are queryable; multi-line text
tracebacks are not. Worth the 5 minutes of setup.

### The security headers minimum

```python
# settings.py
# HSTS is sticky: browsers remember it for SECURE_HSTS_SECONDS, and
# lowering the value later does not reach browsers that already cached
# it. Start small (e.g. 3600) and raise it once HTTPS works everywhere.
SECURE_HSTS_SECONDS = 31536000          # 1 year
# Only if EVERY subdomain serves HTTPS: this covers them all.
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Preloading ships your domain in browsers' built-in lists, and removal
# takes months. Enable it only after the two settings above have run
# cleanly, then submit the domain at hstspreload.org.
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True              # enforce HTTPS at app level too
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
```

Run `python manage.py check --deploy` after applying — Django's
deploy checks catch any remaining gaps and tell you the exact
setting to add.

---

## What just happened, end to end

```
   Client                   Nginx :443                   Uvicorn :8000          Redis
      │                          │                              │                   │
      │ GET /dashboard/          │                              │                   │
      │ ──────────────────────► proxy_pass ───────────────────► HTTP handler        │
      │                          │                              │ mount()           │
      │                          │                              │ render template   │
      │ ◄ HTTP response ─────────│ ◄────────────────────────────│                   │
      │                          │                              │                   │
      │ WebSocket upgrade /ws/   │                              │                   │
      │ ───── Connection: ──────► proxy_pass + Upgrade ───────► consumer            │
      │       Upgrade            │ headers preserved            │ accepts WS        │
      │                          │                              │                   │
      │ click event              │                              │                   │
      │ ───────────────────────► proxy_pass (existing WS) ────► event_handler       │
      │                          │                              │ self.x = ...      │
      │                          │                              │ sends diff        │
      │ ◄ patch ─────────────────│ ◄────────────────────────────│                   │
```

Single client, multiple workers under the upstream — the WS is
sticky to whichever worker accepted the upgrade, but the next
HTTP page-load from the same client can land on any worker,
because sessions, the channel layer and the state backend are
shared. View state itself follows the client only if you opted in
as described in Step 4.

---

## Where to go next

- **Zero-downtime deploys:** add `Restart=on-failure` + a
  systemd `KillMode=mixed` so SIGTERM goes to all workers; pair
  with `--timeout-graceful-shutdown 30` so in-flight WebSockets
  drain. Then `systemctl restart myapp` becomes safe mid-traffic.
- **Containerized deploy:** the same ASGI command works inside a
  Docker container (see [Production Deployment](deployment.md)).
  To deploy to the managed djustlive.com platform instead, djust
  ships a CLI (see [djust-deploy CLI](djust-deploy.md)).
- **Multi-region:** if you scale to >1 region, you'll need a
  region-local Redis per region. Cross-region WebSocket session
  resumption isn't supported out of the box — design your URL
  scheme so each region gets its own subdomain (\`us.example.com\`,
  \`eu.example.com\`).
- **Read replicas:** Django has built-in support for a read
  replica via `DATABASES['replica']`. Routing reads from
  LiveView mount() to the replica is a useful optimization
  once the primary DB is hot.
- **CDN for static:** Whitenoise is fine for one server. At
  scale, point a CDN (CloudFront, Cloudflare, etc.) at
  `/static/`. The same Nginx `location /static/` block above
  becomes a `proxy_pass` to your CDN origin instead.

The four production checks (healthcheck, Sentry, structured
logs, security headers) are not optional — they're what
separates "the app stayed up" from "the app went down at 3 AM
and nobody knew until customers tweeted." Add them before the
launch, not after the first incident.
