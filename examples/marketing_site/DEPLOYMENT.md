# Marketing Site Deployment Guide

This guide covers deploying the djust marketing site to production.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Security Settings](#security-settings)
- [Static Files](#static-files)
- [Cache Backend](#cache-backend)
- [State Backend](#state-backend)
- [ASGI Server](#asgi-server)
- [Deployment Platforms](#deployment-platforms)
- [Monitoring & Debugging](#monitoring--debugging)

## Prerequisites

### Required Dependencies

```bash
# Core dependencies
pip install django channels daphne djust

# Production dependencies
pip install whitenoise  # Static file serving
pip install redis django-redis  # Redis support (optional but recommended)
```

### System Requirements

- Python 3.10+
- Redis server (optional, but recommended for production)
- ASGI-capable web server (Daphne, Uvicorn, or Hypercorn)

## Environment Variables

Copy `.env.example` to `.env` and configure for your environment:

```bash
cp .env.example .env
```

### Required Settings

```bash
# Generate a secure secret key
DJANGO_SECRET_KEY=$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')

# Disable debug mode in production
DEBUG=False

# Configure allowed hosts (comma-separated)
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
```

### Optional Settings

```bash
# GitHub integration (for star count display)
GITHUB_REPO=username/djust

# Redis configuration (recommended for production)
DJUST_STATE_BACKEND=redis
REDIS_URL=redis://127.0.0.1:6379/0
CACHE_BACKEND=redis
REDIS_CACHE_URL=redis://127.0.0.1:6379/1
```

## Security Settings

### 1. Generate Secure Secret Key

**NEVER use the default secret key in production!**

```bash
# Generate a new secret key
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'

# Set it in your environment
export DJANGO_SECRET_KEY='your-generated-secret-key-here'
```

### 2. Disable Debug Mode

```bash
export DEBUG=False
```

### 3. Configure Allowed Hosts

```bash
# Single domain
export ALLOWED_HOSTS=example.com

# Multiple domains
export ALLOWED_HOSTS=example.com,www.example.com,api.example.com
```

### 4. Additional Security Headers (Recommended)

Add to `settings.py`:

```python
if not DEBUG:
    # HTTPS settings
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # HSTS settings
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # Additional security headers
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'
```

## Static Files

The marketing site uses Whitenoise for efficient static file serving.

### Collect Static Files

```bash
# Collect all static files to STATIC_ROOT
python manage.py collectstatic --noinput
```

### Static File Configuration

Already configured in `settings.py`:

```python
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
```

This configuration:
- Compresses static files (gzip)
- Adds content hashes to filenames for cache-busting
- Serves files efficiently with proper cache headers

## Cache Backend

### Development (Default)

Uses in-memory cache (no configuration needed):

```bash
# No environment variables needed
```

### Production (Redis)

Configure Redis cache for shared caching across servers:

```bash
export CACHE_BACKEND=redis
export REDIS_CACHE_URL=redis://127.0.0.1:6379/1
```

**Benefits:**
- Shared cache across multiple server instances
- Persistent cache survives server restarts
- Better performance for API calls (GitHub stars, etc.)

**Install Redis dependency:**

```bash
pip install django-redis
```

## State Backend

djust uses a state backend to store LiveView session state.

### Development (Default)

Uses in-memory state backend:

```bash
export DJUST_STATE_BACKEND=memory
```

**Limitations:**
- Single server only (no horizontal scaling)
- State lost on server restart

### Production (Redis)

Configure Redis state backend for production:

```bash
export DJUST_STATE_BACKEND=redis
export REDIS_URL=redis://127.0.0.1:6379/0
```

**Benefits:**
- Horizontal scaling across multiple servers
- Persistent state survives server restarts
- 5-10x faster serialization (native Rust MessagePack)
- 30-40% smaller payload size

**Install Redis dependency:**

```bash
pip install redis
```

## ASGI Server

The marketing site requires an ASGI server for WebSocket support.

### Daphne (Recommended)

Already included in dependencies:

```bash
# Development
daphne -b 0.0.0.0 -p 8000 marketing_project.asgi:application

# Production (with more workers)
daphne -b 0.0.0.0 -p 8000 --proxy-headers marketing_project.asgi:application
```

### Uvicorn (Alternative)

```bash
pip install uvicorn[standard]

# Development
uvicorn marketing_project.asgi:application --host 0.0.0.0 --port 8000 --reload

# Production
uvicorn marketing_project.asgi:application --host 0.0.0.0 --port 8000 --workers 4
```

### Hypercorn (Alternative)

```bash
pip install hypercorn

# Development
hypercorn marketing_project.asgi:application --bind 0.0.0.0:8000 --reload

# Production
hypercorn marketing_project.asgi:application --bind 0.0.0.0:8000 --workers 4
```

## Deployment Platforms

### Docker Deployment

**Dockerfile:**

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Collect static files
RUN python manage.py collectstatic --noinput

# Expose port
EXPOSE 8000

# Run with Daphne
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "marketing_project.asgi:application"]
```

**docker-compose.yml:**

```yaml
version: '3.8'

services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}
      - DEBUG=False
      - ALLOWED_HOSTS=${ALLOWED_HOSTS}
      - DJUST_STATE_BACKEND=redis
      - REDIS_URL=redis://redis:6379/0
      - CACHE_BACKEND=redis
      - REDIS_CACHE_URL=redis://redis:6379/1
      - GITHUB_REPO=${GITHUB_REPO}
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  redis_data:
```

### Heroku Deployment

**Procfile:**

```
web: daphne marketing_project.asgi:application --port $PORT --bind 0.0.0.0 --proxy-headers
```

**Set environment variables:**

```bash
heroku config:set DJANGO_SECRET_KEY=$(python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
heroku config:set DEBUG=False
heroku config:set ALLOWED_HOSTS=yourapp.herokuapp.com
heroku config:set DJUST_STATE_BACKEND=redis
heroku config:set CACHE_BACKEND=redis

# Add Redis addon
heroku addons:create heroku-redis:mini
```

### Railway Deployment

Railway auto-detects Django projects. Just configure environment variables:

```bash
DJANGO_SECRET_KEY=<generated-key>
DEBUG=False
ALLOWED_HOSTS=${{RAILWAY_PUBLIC_DOMAIN}}
DJUST_STATE_BACKEND=redis
REDIS_URL=${{REDIS_URL}}  # From Railway Redis service
CACHE_BACKEND=redis
REDIS_CACHE_URL=${{REDIS_URL}}
```

### DigitalOcean App Platform

**app.yaml:**

```yaml
name: djust-marketing
services:
  - name: web
    github:
      repo: username/djust-marketing
      branch: main
    build_command: |
      pip install -r requirements.txt
      python manage.py collectstatic --noinput
    run_command: daphne marketing_project.asgi:application --port 8080 --bind 0.0.0.0
    envs:
      - key: DJANGO_SECRET_KEY
        value: ${DJANGO_SECRET_KEY}
      - key: DEBUG
        value: "False"
      - key: ALLOWED_HOSTS
        value: ${APP_DOMAIN}
      - key: DJUST_STATE_BACKEND
        value: redis
      - key: CACHE_BACKEND
        value: redis
databases:
  - name: redis
    engine: REDIS
```

## Monitoring & Debugging

### Health Check Endpoint

Create a health check view:

```python
# marketing/views.py
from django.http import JsonResponse

def health_check(request):
    """Simple health check for load balancers"""
    return JsonResponse({
        'status': 'healthy',
        'version': '1.0.0',
    })
```

```python
# marketing_project/urls.py
urlpatterns = [
    path('health/', health_check),
    # ... other patterns
]
```

### Logging Configuration

Add to `settings.py`:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO' if not DEBUG else 'DEBUG',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'djust': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

### Performance Monitoring

Enable Django Debug Toolbar in development:

```bash
pip install django-debug-toolbar
```

```python
# settings.py (development only)
if DEBUG:
    INSTALLED_APPS += ['debug_toolbar']
    MIDDLEWARE += ['debug_toolbar.middleware.DebugToolbarMiddleware']
    INTERNAL_IPS = ['127.0.0.1']
```

### Error Tracking (Optional)

Consider using Sentry for production error tracking:

```bash
pip install sentry-sdk
```

```python
# settings.py
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

if not DEBUG:
    sentry_sdk.init(
        dsn=os.environ.get('SENTRY_DSN'),
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
    )
```

## Checklist

Before deploying to production:

- [ ] Generate secure `DJANGO_SECRET_KEY`
- [ ] Set `DEBUG=False`
- [ ] Configure `ALLOWED_HOSTS` with your domain(s)
- [ ] Run `python manage.py collectstatic --noinput`
- [ ] Configure Redis for state and cache backends (recommended)
- [ ] Set up HTTPS/SSL certificate
- [ ] Enable security headers (HSTS, CSP, etc.)
- [ ] Configure logging
- [ ] Set up error tracking (Sentry, etc.)
- [ ] Create health check endpoint
- [ ] Test WebSocket connectivity
- [ ] Configure backup strategy for Redis data
- [ ] Set up monitoring and alerts
- [ ] Document rollback procedure

## Troubleshooting

### WebSocket Connection Fails

**Symptom:** LiveView features don't work, browser console shows WebSocket errors

**Solutions:**
1. Verify ASGI server is running (not WSGI)
2. Check firewall allows WebSocket connections
3. If behind reverse proxy (nginx), configure WebSocket support:
   ```nginx
   location /ws/ {
       proxy_pass http://backend;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
   }
   ```

### Static Files Not Loading

**Symptom:** CSS/JS files return 404 errors

**Solutions:**
1. Run `python manage.py collectstatic --noinput`
2. Verify `STATIC_ROOT` and `STATIC_URL` in settings
3. Check Whitenoise is in `MIDDLEWARE` (before other middleware)
4. If using CDN, configure `STATIC_URL` to point to CDN

### Redis Connection Errors

**Symptom:** Application fails to start or crashes with Redis connection errors

**Solutions:**
1. Verify Redis server is running: `redis-cli ping`
2. Check `REDIS_URL` is correct
3. Verify firewall allows connection to Redis port (6379)
4. Check Redis authentication if enabled
5. Fallback to memory backend temporarily:
   ```bash
   export DJUST_STATE_BACKEND=memory
   export CACHE_BACKEND=locmem
   ```

### High Memory Usage

**Symptom:** Server runs out of memory

**Solutions:**
1. Switch from memory to Redis backends
2. Reduce `DJUST_SESSION_TTL` to expire sessions faster
3. Run cleanup task periodically:
   ```python
   from djust.live_view import cleanup_expired_sessions
   cleanup_expired_sessions(ttl=3600)
   ```
4. Increase server resources or add more server instances

## Support

For issues or questions:
- GitHub Issues: https://github.com/username/djust/issues
- Documentation: https://djust.readthedocs.io/
- Community: https://discord.gg/djust
