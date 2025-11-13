# Production Deployment Guide

This guide covers deploying djust LiveView applications to production with horizontal scaling using Redis state backend.

## Table of Contents

- [Quick Start](#quick-start)
- [State Backend Options](#state-backend-options)
- [Redis Setup](#redis-setup)
- [Horizontal Scaling](#horizontal-scaling)
- [Session Management](#session-management)
- [Performance Tuning](#performance-tuning)
- [Monitoring](#monitoring)
- [Deployment Checklist](#deployment-checklist)

## Quick Start

For single-server deployments, djust works out-of-the-box with the in-memory state backend. For production deployments with multiple servers, configure Redis:

```python
# settings.py
DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': 'redis://redis.example.com:6379/0',
    'SESSION_TTL': 7200,  # 2 hours
}
```

## State Backend Options

djust provides two state backends for LiveView session storage:

### InMemory Backend (Development)

**Use for:**
- Development environments
- Single-server deployments
- Testing

**Limitations:**
- No horizontal scaling (single server only)
- State lost on server restart
- Potential memory leaks without cleanup

**Configuration:**
```python
DJUST_CONFIG = {
    'STATE_BACKEND': 'memory',  # Default
    'SESSION_TTL': 3600,  # 1 hour
}
```

### Redis Backend (Production)

**Use for:**
- Production deployments
- Horizontal scaling across multiple servers
- High availability setups

**Benefits:**
- Persistent state survives server restarts
- Horizontal scaling across multiple servers
- Automatic TTL-based expiration
- Native Rust serialization (5-10x faster, 30-40% smaller)

**Requirements:**
- Redis server (6.0+)
- `redis-py` Python package

**Configuration:**
```python
DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': 'redis://redis.example.com:6379/0',
    'SESSION_TTL': 7200,  # 2 hours
}
```

## Redis Setup

### Installing Redis

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

**macOS:**
```bash
brew install redis
brew services start redis
```

**Docker:**
```bash
docker run -d -p 6379:6379 --name redis redis:7-alpine
```

### Redis Configuration

Edit `/etc/redis/redis.conf` for production:

```conf
# Bind to specific IP (not 0.0.0.0 for security)
bind 127.0.0.1 ::1

# Enable persistence
save 900 1      # Save after 15 min if 1 key changed
save 300 10     # Save after 5 min if 10 keys changed
save 60 10000   # Save after 1 min if 10000 keys changed

# Set maxmemory policy (evict least recently used keys)
maxmemory 256mb
maxmemory-policy allkeys-lru

# Enable password protection
requirepass your-strong-password-here

# Disable dangerous commands
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command CONFIG ""
```

Restart Redis after configuration changes:
```bash
sudo systemctl restart redis-server
```

### Python Redis Client

Install `redis-py`:
```bash
pip install redis
```

Update Django settings with Redis URL:
```python
import os

DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    'SESSION_TTL': int(os.environ.get('SESSION_TTL', 7200)),
}
```

Use environment variables for sensitive data:
```bash
# .env
REDIS_URL=redis://:password@redis-host:6379/0
SESSION_TTL=7200
```

## Horizontal Scaling

With Redis backend, you can run multiple djust servers sharing the same state:

```
                    ┌─────────────┐
                    │ Load Balancer│
                    │  (Nginx)    │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
    ┌─────▼─────┐    ┌────▼─────┐    ┌────▼─────┐
    │ Server 1  │    │ Server 2 │    │ Server 3 │
    │ (uvicorn) │    │ (uvicorn)│    │ (uvicorn)│
    └─────┬─────┘    └────┬─────┘    └────┬─────┘
          │               │               │
          └───────────────┼───────────────┘
                          │
                    ┌─────▼─────┐
                    │   Redis   │
                    │  (State)  │
                    └───────────┘
```

### Load Balancer Configuration

**Nginx:**
```nginx
upstream djust_backend {
    # Round-robin load balancing
    server 10.0.1.10:8000;
    server 10.0.1.11:8000;
    server 10.0.1.12:8000;

    # Sticky sessions (optional, for WebSocket)
    ip_hash;
}

server {
    listen 443 ssl http2;
    server_name example.com;

    # WebSocket support
    location /ws/ {
        proxy_pass http://djust_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeout
        proxy_read_timeout 86400;
    }

    # HTTP routes
    location / {
        proxy_pass http://djust_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Running Multiple Servers

**Server 1:**
```bash
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4
```

**Server 2:**
```bash
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4
```

**Server 3:**
```bash
uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000 --workers 4
```

All servers share the same Redis instance for state.

### Docker Compose Example

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-data:/data
    ports:
      - "6379:6379"

  web1:
    build: .
    command: uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000
    environment:
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SESSION_TTL=7200
    depends_on:
      - redis
    ports:
      - "8001:8000"

  web2:
    build: .
    command: uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000
    environment:
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SESSION_TTL=7200
    depends_on:
      - redis
    ports:
      - "8002:8000"

  web3:
    build: .
    command: uvicorn myproject.asgi:application --host 0.0.0.0 --port 8000
    environment:
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - SESSION_TTL=7200
    depends_on:
      - redis
    ports:
      - "8003:8000"

  nginx:
    image: nginx:alpine
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - web1
      - web2
      - web3

volumes:
  redis-data:
```

## Session Management

### Automatic Cleanup

Redis automatically expires sessions based on TTL. For InMemory backend, set up periodic cleanup:

**Django Management Command** (`management/commands/cleanup_sessions.py`):
```python
from django.core.management.base import BaseCommand
from djust.live_view import cleanup_expired_sessions

class Command(BaseCommand):
    help = 'Clean up expired LiveView sessions'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ttl',
            type=int,
            default=3600,
            help='Session TTL in seconds (default: 3600)',
        )

    def handle(self, *args, **options):
        ttl = options['ttl']
        cleaned = cleanup_expired_sessions(ttl=ttl)
        self.stdout.write(
            self.style.SUCCESS(f'Cleaned {cleaned} expired sessions')
        )
```

**Cron Job** (run every hour):
```bash
0 * * * * cd /path/to/project && python manage.py cleanup_sessions
```

**Celery Periodic Task**:
```python
from celery import shared_task
from djust.live_view import cleanup_expired_sessions

@shared_task
def cleanup_liveview_sessions():
    cleaned = cleanup_expired_sessions(ttl=3600)
    return f'Cleaned {cleaned} expired sessions'

# In celery beat schedule
from celery.schedules import crontab

app.conf.beat_schedule = {
    'cleanup-sessions': {
        'task': 'myapp.tasks.cleanup_liveview_sessions',
        'schedule': crontab(minute=0),  # Every hour
    },
}
```

### Session Statistics

Monitor session statistics:

```python
from djust.live_view import get_session_stats

stats = get_session_stats()
print(f"Backend: {stats['backend']}")
print(f"Total sessions: {stats['total_sessions']}")
print(f"Oldest session: {stats.get('oldest_session_age', 0):.1f}s")
print(f"Average age: {stats.get('average_age', 0):.1f}s")

# Redis-specific stats
if stats['backend'] == 'redis':
    print(f"Redis memory: {stats.get('redis_memory', 'N/A')}")
```

**Monitoring Endpoint**:
```python
from django.http import JsonResponse
from djust.live_view import get_session_stats

def session_stats_view(request):
    """Endpoint for monitoring tools (Prometheus, Datadog, etc.)"""
    return JsonResponse(get_session_stats())
```

## Performance Tuning

### Redis Optimization

**Memory Management:**
```conf
# Set maxmemory based on available RAM
maxmemory 2gb

# Use LRU eviction for session cache
maxmemory-policy allkeys-lru

# Disable persistence for pure cache (faster, but loses data on restart)
# save ""
# appendonly no
```

**Connection Pooling:**
```python
# settings.py
import redis.connection

DJUST_CONFIG = {
    'STATE_BACKEND': 'redis',
    'REDIS_URL': 'redis://redis-host:6379/0',
    'SESSION_TTL': 7200,
}

# Configure connection pool
from djust.state_backend import RedisStateBackend
backend = RedisStateBackend(
    redis_url=DJUST_CONFIG['REDIS_URL'],
    default_ttl=DJUST_CONFIG['SESSION_TTL'],
)
# Connection pool managed automatically by redis-py
```

### Session TTL Tuning

Balance between memory usage and user experience:

| Use Case | Recommended TTL | Reasoning |
|----------|----------------|-----------|
| E-commerce | 1-2 hours | Shopping cart persistence |
| Admin dashboards | 4-8 hours | Long work sessions |
| Public content | 30-60 minutes | Lower memory usage |
| Real-time apps | 15-30 minutes | Fast state updates |

### ASGI Server Configuration

**Uvicorn (recommended):**
```bash
# Production configuration
uvicorn myproject.asgi:application \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --loop uvloop \
    --ws websockets \
    --log-level warning \
    --access-log
```

**Gunicorn + Uvicorn workers:**
```bash
gunicorn myproject.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --log-level warning \
    --access-logfile -
```

Worker count formula: `workers = (2 × CPU cores) + 1`

## Monitoring

### Health Checks

```python
from django.http import JsonResponse
from djust.state_backend import get_backend

def health_check(request):
    """Health check endpoint for load balancers"""
    backend = get_backend()

    # Test backend connectivity
    try:
        stats = backend.get_stats()
        return JsonResponse({
            'status': 'healthy',
            'backend': stats['backend'],
            'sessions': stats['total_sessions'],
        })
    except Exception as e:
        return JsonResponse({
            'status': 'unhealthy',
            'error': str(e)
        }, status=503)
```

### Prometheus Metrics

```python
from prometheus_client import Counter, Gauge, Histogram
from djust.live_view import get_session_stats

# Define metrics
session_count = Gauge('liveview_sessions_total', 'Total LiveView sessions')
session_age = Histogram('liveview_session_age_seconds', 'Session age distribution')

def update_metrics():
    """Update Prometheus metrics (call periodically)"""
    stats = get_session_stats()
    session_count.set(stats['total_sessions'])
    if 'average_age' in stats:
        session_age.observe(stats['average_age'])
```

### Logging

```python
# settings.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': '/var/log/djust/sessions.log',
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,
        },
    },
    'loggers': {
        'djust.state_backend': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

## Deployment Checklist

### Pre-Deployment

- [ ] Install Redis server (6.0+)
- [ ] Install `redis-py` Python package
- [ ] Configure `DJUST_CONFIG` with Redis URL
- [ ] Set appropriate `SESSION_TTL` for use case
- [ ] Configure Redis persistence (RDB/AOF)
- [ ] Set Redis `maxmemory` and eviction policy
- [ ] Enable Redis password protection
- [ ] Test Redis connectivity from application servers

### Application Configuration

- [ ] Set `STATE_BACKEND='redis'` in production settings
- [ ] Use environment variables for sensitive data
- [ ] Configure session cleanup (cron/celery)
- [ ] Add health check endpoint
- [ ] Configure logging for state backend
- [ ] Test serialization/deserialization
- [ ] Verify VDOM state preservation

### Infrastructure

- [ ] Set up load balancer (Nginx/HAProxy)
- [ ] Configure WebSocket support in load balancer
- [ ] Enable sticky sessions (if using WebSocket)
- [ ] Deploy multiple application servers
- [ ] Configure Redis high availability (Sentinel/Cluster)
- [ ] Set up monitoring (Prometheus/Datadog)
- [ ] Configure alerting for Redis downtime
- [ ] Test failover scenarios

### Security

- [ ] Enable Redis password authentication
- [ ] Bind Redis to private network only
- [ ] Disable dangerous Redis commands
- [ ] Use TLS for Redis connections (if remote)
- [ ] Rotate Redis passwords regularly
- [ ] Configure firewall rules for Redis port
- [ ] Enable Django CSRF protection
- [ ] Use HTTPS for all LiveView connections

### Performance Testing

- [ ] Load test with multiple concurrent users
- [ ] Verify session state sharing across servers
- [ ] Monitor Redis memory usage under load
- [ ] Test session cleanup performance
- [ ] Benchmark serialization performance
- [ ] Verify WebSocket connection stability
- [ ] Test reconnection after server failure

### Post-Deployment

- [ ] Monitor session statistics
- [ ] Check Redis memory usage
- [ ] Review cleanup task logs
- [ ] Test horizontal scaling
- [ ] Verify state persistence after restart
- [ ] Monitor WebSocket connection health
- [ ] Review performance metrics

## Troubleshooting

### Redis Connection Errors

**Error:** `redis.exceptions.ConnectionError: Error connecting to Redis`

**Solutions:**
1. Verify Redis server is running: `redis-cli ping`
2. Check Redis URL in settings
3. Verify network connectivity
4. Check firewall rules
5. Verify Redis password (if configured)

### Session Not Found

**Error:** Session state not found after server restart

**Solutions:**
1. Verify `STATE_BACKEND='redis'` in settings
2. Check Redis persistence is enabled
3. Verify Redis data directory has write permissions
4. Check Redis `save` configuration

### Memory Issues

**Error:** Redis running out of memory

**Solutions:**
1. Increase Redis `maxmemory` limit
2. Enable `maxmemory-policy allkeys-lru`
3. Reduce `SESSION_TTL` value
4. Run cleanup tasks more frequently
5. Monitor session count and age

### Serialization Errors

**Error:** `MessagePack deserialization error`

**Solutions:**
1. Verify djust version matches across all servers
2. Check template source hasn't changed
3. Clear Redis cache and restart
4. Verify Rust extension is compiled correctly

## Additional Resources

- [Redis Documentation](https://redis.io/docs/)
- [django-redis](https://github.com/jazzband/django-redis) - Alternative Django Redis backend
- [Redis Sentinel](https://redis.io/topics/sentinel) - High availability
- [Redis Cluster](https://redis.io/topics/cluster-tutorial) - Horizontal partitioning
- [Uvicorn Documentation](https://www.uvicorn.org/)
- [Nginx WebSocket Proxy](https://nginx.org/en/docs/http/websocket.html)

## Support

For issues or questions:
- GitHub Issues: https://github.com/yourusername/djust/issues
- Documentation: https://djust.readthedocs.io/
