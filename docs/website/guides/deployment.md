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

```bash
gunicorn myproject.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --log-level warning
```

Worker count formula: `(2 x CPU cores) + 1`

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

## Session Management

### Session TTL Guidelines

| Use Case | Recommended TTL |
|----------|----------------|
| E-commerce | 1-2 hours |
| Admin dashboards | 4-8 hours |
| Public content | 30-60 minutes |
| Real-time apps | 15-30 minutes |

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

## Troubleshooting

**Redis connection errors**: Verify Redis is running (`redis-cli ping`), check URL and password, confirm firewall rules.

**Session not found after restart**: Ensure `STATE_BACKEND='redis'` and Redis persistence is enabled (`save` directives in `redis.conf`).

**Memory issues**: Increase `maxmemory`, enable `allkeys-lru` eviction, reduce `SESSION_TTL`, run cleanup more frequently.

**Serialization errors**: Ensure djust version matches across all servers, clear Redis cache, and verify Rust extension is compiled for the correct Python version.
