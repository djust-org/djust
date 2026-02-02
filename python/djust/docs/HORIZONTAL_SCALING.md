# Horizontal Scaling & Multi-Node Support

## Overview

djust can scale from a single development server to a multi-node production cluster. This document covers the architecture, configuration, and migration path.

## Current Limitations (Single-Node)

### In-Memory Channel Layer
By default, Django Channels uses `InMemoryChannelLayer`, which only works within a single process. WebSocket messages sent via `group_send` will not reach consumers in other processes or servers.

### In-Memory Presence Tracking
The `PresenceManager` uses Django's cache framework (default: `LocMemCache`), meaning presence data is isolated to a single process. Users on different nodes won't see each other's presence.

### State Backend (In-Memory)
The `InMemoryStateBackend` stores `RustLiveView` instances in a Python dict. This data is:
- Lost on process restart
- Not shared across processes/nodes
- Subject to memory pressure with many concurrent sessions

### Embedded LiveView Shared State
`LiveSession._shared_state` is an in-memory dict. Parent/child LiveViews must be served by the same process.

### No Process Discovery
There's no mechanism for nodes to discover each other or coordinate work (unlike Erlang/OTP's built-in distributed computing).

## How Phoenix LiveView Handles This

Phoenix LiveView benefits from the Erlang/OTP platform:

### Distributed PubSub
Phoenix.PubSub supports multiple adapters:
- **PG2 (default)**: Uses Erlang's built-in process groups. Nodes form a cluster via `libcluster` and PubSub messages are forwarded across nodes automatically.
- **Redis**: For environments where Erlang distribution isn't available.

### Process Groups & Node Discovery
- **libcluster**: Automatic node discovery via DNS, Kubernetes API, gossip protocols, etc.
- **pg (Process Groups)**: Erlang's native mechanism for grouping processes across nodes.
- Processes can be addressed by name across the cluster transparently.

### Presence (Phoenix.Presence)
- Uses CRDTs (Conflict-free Replicated Data Types) built on Phoenix.PubSub.
- Each node tracks its own presences locally.
- Presence diffs are broadcast to other nodes via PubSub.
- Conflicts are resolved automatically — no central coordinator needed.
- Heartbeat-based with automatic cleanup of dead nodes.

### Key Takeaway
Phoenix gets horizontal scaling "for free" from the BEAM VM. Django needs explicit infrastructure (Redis) to achieve similar results.

## Required Changes for Multi-Node

### 1. Redis Channel Layer

Replace the in-memory channel layer with `channels_redis`:

```python
# settings.py
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}
```

This ensures `group_send` (used for hot reload, presence broadcasts, and view updates) reaches all consumers across all nodes.

### 2. Session Affinity / Sticky Sessions

WebSocket connections are long-lived. Once established, all messages for that connection must go to the same server process. This is naturally handled because:
- The TCP connection pins the client to a specific backend server.
- The WebSocket upgrade happens on that same connection.

However, the **initial HTTP request** (which renders the page and creates the session) and the **WebSocket connection** should ideally hit the same node to avoid a state lookup miss.

**Recommended**: Use IP-hash or cookie-based sticky sessions at the load balancer level.

```nginx
upstream djust_backend {
    ip_hash;  # Sticky sessions based on client IP
    server 10.0.0.1:8000;
    server 10.0.0.2:8000;
    server 10.0.0.3:8000;
}
```

Without sticky sessions, the Redis state backend handles this transparently (the WebSocket consumer loads state from Redis regardless of which node it connects to).

### 3. Presence Tracking Across Nodes

**Current**: Uses `django.core.cache` (typically `LocMemCache` in dev).

**Required**: Switch to a shared cache backend:

```python
# settings.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://redis:6379/1",
    }
}
```

This works because `PresenceManager` already uses `django.core.cache` — no code changes needed, just configuration.

For higher-scale deployments, djust provides `djust.backends.redis.RedisPresenceBackend` which uses Redis native data structures (sorted sets with TTL) instead of serialized Python dicts, offering better performance under high concurrency.

### 4. Shared State for Embedded LiveViews

`LiveSession._shared_state` must be externalized for multi-node:

**Option A (Recommended)**: Store shared state in Redis via the state backend. The `RedisStateBackend` already handles `RustLiveView` persistence — `LiveSession` data can use the same Redis instance.

**Option B**: Use Django sessions (database-backed) for shared state. Slower but requires no additional infrastructure.

### 5. DJUST_BACKEND Setting

Configure the overall backend strategy:

```python
# settings.py
DJUST_CONFIG = {
    # State backend for LiveView sessions
    'STATE_BACKEND': 'redis',          # 'memory' (default) or 'redis'
    'REDIS_URL': 'redis://redis:6379/0',
    
    # Channel layer backend for PubSub
    # (This is separate — configured via CHANNEL_LAYERS)
    
    # Presence backend
    'PRESENCE_BACKEND': 'redis',       # 'memory' (default) or 'redis'
    'PRESENCE_REDIS_URL': 'redis://redis:6379/2',  # Can share with REDIS_URL
}
```

## Recommended Architecture

### Development (Single Node)

```
Browser → Daphne (single process) → Django + Channels (InMemory)
```

### Production (Multi-Node)

```
                    ┌─────────────┐
                    │   nginx     │
                    │ (ip_hash)   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴───┐ ┌─────┴─────┐
        │  Daphne   │ │Daphne │ │  Daphne   │
        │  Worker 1 │ │  W 2  │ │  Worker 3 │
        └─────┬─────┘ └───┬───┘ └─────┬─────┘
              │            │            │
              └────────────┼────────────┘
                           │
                    ┌──────┴──────┐
                    │    Redis    │
                    │ (channels,  │
                    │  state,     │
                    │  presence,  │
                    │  cache)     │
                    └─────────────┘
```

### Component Responsibilities

| Component | Role |
|-----------|------|
| **nginx** | TLS termination, static files, sticky sessions, WebSocket upgrade |
| **Daphne workers** | ASGI server running Django + Channels consumers |
| **Redis** | Channel layer (PubSub), state backend, presence, Django cache |

### nginx Configuration

```nginx
upstream djust_backend {
    ip_hash;
    server 10.0.0.1:8000;
    server 10.0.0.2:8000;
}

server {
    listen 443 ssl;
    server_name example.com;

    ssl_certificate /etc/ssl/cert.pem;
    ssl_certificate_key /etc/ssl/key.pem;

    # Static files
    location /static/ {
        alias /var/www/static/;
        expires 30d;
    }

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
        proxy_read_timeout 86400;  # 24h for long-lived WebSockets
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

### Daphne Worker Configuration

```bash
# Run multiple workers (one per CPU core)
daphne -b 0.0.0.0 -p 8000 --proxy-headers myproject.asgi:application

# Or with uvicorn (alternative ASGI server)
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4
```

### Docker Compose Example

```yaml
version: "3.8"

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  web:
    build: .
    command: daphne -b 0.0.0.0 -p 8000 --proxy-headers myproject.asgi:application
    environment:
      - DJANGO_SETTINGS_MODULE=myproject.settings
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis
    deploy:
      replicas: 3

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./static:/var/www/static
    depends_on:
      - web

volumes:
  redis_data:
```

## Configuration Examples

### Minimal Production Setup

```python
# settings.py

# Redis for everything
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': REDIS_URL,
    'SESSION_TTL': 7200,  # 2 hours
    'COMPRESSION_ENABLED': True,
}
```

### Development Setup (No Redis Required)

```python
# settings.py

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

# Default DJUST_CONFIG uses memory backend — no configuration needed
```

## Migration Guide: Single-Node → Multi-Node

### Step 1: Install Dependencies

```bash
pip install channels-redis redis
# Optional but recommended:
pip install zstandard  # For state compression
```

### Step 2: Set Up Redis

```bash
# Docker
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Or install natively
# macOS: brew install redis
# Ubuntu: sudo apt install redis-server
```

### Step 3: Update Django Settings

```python
# settings.py
import os

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# 1. Channel layer → Redis
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

# 2. Cache → Redis (for presence)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# 3. State backend → Redis
DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': REDIS_URL,
    'SESSION_TTL': 3600,
}
```

### Step 4: Validate Configuration

```bash
python manage.py djust_check --verbose
```

This will verify:
- ✅ Channel layer is configured for multi-node
- ✅ State backend matches channel layer configuration
- ✅ Redis is reachable
- ⚠️ Warnings for any mismatched configurations

### Step 5: Deploy Behind a Load Balancer

See the nginx configuration above. Key points:
- Enable `ip_hash` for sticky sessions
- Set `proxy_read_timeout` high for WebSockets
- Proxy WebSocket upgrade headers

### Step 6: Monitor

```bash
# Check session stats
python manage.py shell -c "
from djust.state_backends import get_backend
backend = get_backend()
print(backend.health_check())
print(backend.get_stats())
"
```

## Performance Considerations

| Metric | In-Memory | Redis |
|--------|-----------|-------|
| State read latency | ~0.01ms | ~0.5ms |
| State write latency | ~0.01ms | ~1ms |
| Max concurrent sessions (per node) | ~10,000 | ~100,000+ |
| Horizontal scaling | ❌ No | ✅ Yes |
| Survives restart | ❌ No | ✅ Yes |
| Memory overhead | Low | Moderate (network + serialization) |

### Optimization Tips

1. **Enable compression** for state > 10KB (`COMPRESSION_ENABLED: True`)
2. **Use `temporary_assigns`** to reduce state size for large collections
3. **Use streams** instead of storing full collections in state
4. **Tune Redis** — increase `maxmemory`, use `allkeys-lru` eviction policy
5. **Monitor state sizes** via `get_memory_stats()` and set appropriate `STATE_SIZE_WARNING_KB`

## Future Improvements

- **Redis Cluster / Sentinel** support for Redis high availability
- **CRDT-based presence** (inspired by Phoenix.Presence) for eventually-consistent presence without central coordination
- **WebSocket connection migration** — allow connections to move between nodes during rolling deploys
- **Distributed rate limiting** — share rate limit state across nodes via Redis
