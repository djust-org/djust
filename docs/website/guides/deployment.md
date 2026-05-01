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

### In-Memory (Development Only)

```python
DJUST_CONFIG = {
    'STATE_BACKEND': 'memory',
    'SESSION_TTL': 3600,
}
```

Single server only. State lost on restart.

### Redis (Production)

```python
import os

DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    'SESSION_TTL': 7200,  # 2 hours
}
```

Requires Redis 6.0+ and `redis-py`:

```bash
pip install redis
```

## Channel Layer (for cross-process push)

`DJUST_STATE_BACKEND` and Django Channels' `CHANNEL_LAYERS` are **two separate concerns** that both happen to use Redis. Don't conflate them:

| Concern | Setting | What it does |
|---|---|---|
| **State backend** | `DJUST_STATE_BACKEND` (or `DJUST_CONFIG['STATE_BACKEND']`) | Stores per-view state (assigns) so a reconnecting client gets its view back. Single-process traffic. |
| **Channel layer** | `CHANNEL_LAYERS` (Django Channels) | Routes cross-process messages — required when ANY view uses `push_to_view()`, presence, cursor tracking, or DB-change notifications. |

**Required configuration** any time your app has multiple ASGI processes or uses cross-process push:

```python
# settings/prod.py
import os

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [os.environ["REDIS_CHANNEL_URL"]]},
    },
}
```

```bash
pip install channels_redis
```

For small deployments, point `REDIS_URL` and `REDIS_CHANNEL_URL` at the same Redis instance — it serves both concerns fine. **Separate the instances** when:

- ElastiCache memory utilization climbs past 70% (the channel layer holds short-lived messages; the state backend holds longer-lived per-view state — eviction policies want to differ).
- You want to scale the channel layer independently (e.g., to a Redis cluster) without touching the state backend.
- You're auditing for blast radius and want a Redis outage to fail one concern at a time.

The `InMemoryChannelLayer` is **development-only** — it doesn't cross processes, so multi-worker / multi-server `push_to_view` silently no-ops.

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

# Memory management
maxmemory 256mb
maxmemory-policy allkeys-lru

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
    --loop uvloop \
    --ws websockets \
    --log-level warning \
    --access-log
```

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

Note that disabling compression in djust's config is **advisory** — the actual wire-level compression is negotiated by the ASGI server. To enforce the no-compression decision at the server level, pass the appropriate flag to Uvicorn / Daphne (e.g. Uvicorn's `--ws-per-message-deflate=false` when using `websockets`). See the [deployment runbook](#production-checklist) for the full set of ASGI-server flags.

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
      - SESSION_TTL=7200
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
    "cleanup-expired-sessions": {
        "task": "myapp.tasks.cleanup_expired_sessions",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
    },
    "sync-third-party-data": {
        "task": "myapp.tasks.sync_third_party",
        "schedule": crontab(minute=15, hour=2),  # Daily 2:15am
    },
}
```

## Session Management

### Session TTL Guidelines

| Use Case         | Recommended TTL |
| ---------------- | --------------- |
| E-commerce       | 1-2 hours       |
| Admin dashboards | 4-8 hours       |
| Public content   | 30-60 minutes   |
| Real-time apps   | 15-30 minutes   |

### Cleanup for In-Memory Backend

Redis handles expiration automatically. For the memory backend, set up periodic cleanup:

```python
# management/commands/cleanup_sessions.py
from django.core.management.base import BaseCommand
from djust.live_view import cleanup_expired_sessions

class Command(BaseCommand):
    help = 'Clean up expired LiveView sessions'

    def handle(self, *args, **options):
        cleaned = cleanup_expired_sessions(ttl=3600)
        self.stdout.write(f'Cleaned {cleaned} expired sessions')
```

```bash
# Cron: run every hour
0 * * * * cd /path/to/project && python manage.py cleanup_sessions
```

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
4. If the task dies and the client reconnects, ALB picks a healthy task and the cookie now hashes to the new one — state survives via the Redis state backend.

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

- [ ] Session cleanup configured (cron or Celery)
- [ ] Health check endpoint added
- [ ] Logging configured for `djust.state_backend`
- [ ] CSRF protection enabled
- [ ] `SESSION_TTL` tuned for your use case

### Verification

- [ ] Load test with concurrent WebSocket connections
- [ ] Verify state sharing across multiple servers
- [ ] Test reconnection after server restart
- [ ] Monitor Redis memory under load
- [ ] Confirm browser back/forward works after navigation

## Sizing and Scaling Tiers

Three tiers indexed to concurrent active users. The numbers are heuristics for typical mixed-workload djust apps; CPU-heavy apps (large VDOM diffs, complex template rendering) and I/O-heavy apps (lots of `push_to_view`, presence, third-party APIs) shift the curves. Use these as a starting point and adjust based on your own load testing.

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

- **djust state in Redis** is the canonical multi-server pattern (`DJUST_STATE_BACKEND = redis://...`). View state survives container replacement; reconnects within `SESSION_TTL` get the user's view back.
- **`channels_redis` is required (not optional)** when any view uses `push_to_view`, presence, cursor tracking, or any cross-process feature. Don't try to make it work with `InMemoryChannelLayer` in production.
- **`sync_to_async` for ORM** in event handlers is built into the `@event_handler` decorator — no manual wrapping needed in your view code.
- **`transaction.on_commit()` for Celery enqueue** is the right pattern; never `.delay(...)` directly from a view.
- **WebSocket Origin check (`check_origin`)** is on by default since v0.4.1 (CSWSH protection). Don't disable.
- **HSTS preload, secure cookies, CSP nonce** — all covered by djust's recommended `settings/prod.py`. Disabling for "convenience" opens real attack surface.
- **`@event_handler` decorator** validates handler names + applies rate limits. Don't bypass with raw consumer methods.
- **VDOM diffing in Rust** — there's nothing to "tune". The diff algorithm is O(N log N) keyed-LIS reconciliation; it's already as fast as you can reasonably get without compromising correctness.

If you find yourself building infrastructure to work around any of these (e.g., a custom cross-process push mechanism that bypasses `channels_redis`), step back and confirm the canonical path doesn't already do what you need.

## Troubleshooting

**Redis connection errors**: Verify Redis is running (`redis-cli ping`), check URL and password, confirm firewall rules.

**Session not found after restart**: Ensure `STATE_BACKEND='redis'` and Redis persistence is enabled (`save` directives in `redis.conf`).

**Memory issues**: Increase `maxmemory`, enable `allkeys-lru` eviction, reduce `SESSION_TTL`, run cleanup more frequently.

**Serialization errors**: Ensure djust version matches across all servers, clear Redis cache, and verify Rust extension is compiled for the correct Python version.
