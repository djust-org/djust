# Deploying djust on Render

This guide covers deploying a djust LiveView application to [Render](https://render.com) with full WebSocket support.

## Prerequisites

- A Render account ([render.com](https://render.com))
- A djust project in a GitHub or GitLab repository
- Git repository initialized

## Project Configuration

### requirements.txt

Ensure your `requirements.txt` (or `pyproject.toml`) includes production dependencies:

```
djust>=0.3.0
Django>=4.2
channels[daphne]>=4.0.0
uvicorn[standard]>=0.30.0
whitenoise>=6.0.0
redis>=5.0.0
channels-redis>=4.1.0
psycopg2-binary>=2.9.0
dj-database-url>=2.0.0
```

### build.sh

Create a `build.sh` script in your project root:

```bash
#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
```

Make it executable:

```bash
chmod +x build.sh
```

### Production Settings

Update your `settings.py` for production:

```python
import os
import dj_database_url

# Security
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", ".onrender.com").split(",")
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS if host
]

# Database (Render provides DATABASE_URL)
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3"),
        conn_max_age=600,
    )
}

# Redis (Render provides REDIS_URL)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Django Channels — required for djust WebSocket support
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

# djust state backend
DJUST_CONFIG = {
    "STATE_BACKEND": "redis",
    "REDIS_URL": REDIS_URL,
    "SESSION_TTL": 7200,
}

# Static files (WhiteNoise)
STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # ... rest of your middleware
]
```

### ASGI Configuration

Ensure your `asgi.py` includes djust routing:

```python
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.core.asgi import get_asgi_application
from djust.routing import live_session

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            live_session(your_urlpatterns)
        )
    ),
})
```

## WebSocket Setup

Render natively supports WebSocket connections on all web services. Key points:

- Always use `wss://` for WebSocket connections (Render redirects HTTP to HTTPS, and most WebSocket clients do not follow 301 redirects)
- Render terminates TLS at the edge, so your ASGI server receives plain `ws://` connections
- No special proxy or timeout configuration is needed

## Database Setup

1. In the Render dashboard, go to **New > PostgreSQL**
2. Select a plan and create the database
3. Copy the **Internal Database URL** for use in your web service
4. Set it as the `DATABASE_URL` environment variable on your web service

## Redis Setup

1. In the Render dashboard, go to **New > Redis**
2. Select a plan and create the Redis instance
3. Copy the **Internal Redis URL**
4. Set it as the `REDIS_URL` environment variable on your web service

Both the channel layer and djust state backend will use this Redis instance.

## Step-by-Step Deployment

### 1. Create a Web Service

1. Go to the Render dashboard and click **New > Web Service**
2. Connect your GitHub/GitLab repository
3. Configure the service:
   - **Runtime**: Python
   - **Build Command**: `./build.sh`
   - **Start Command**: `uvicorn myproject.asgi:application --host 0.0.0.0 --port $PORT`

### 2. Set environment variables

In the Render dashboard, add these environment variables to your web service:

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | Generate a strong random key |
| `DJANGO_SETTINGS_MODULE` | `myproject.settings` |
| `ALLOWED_HOSTS` | `.onrender.com` |
| `DATABASE_URL` | (from your Render PostgreSQL instance) |
| `REDIS_URL` | (from your Render Redis instance) |
| `DEBUG` | `false` |
| `PYTHON_VERSION` | `3.11.6` |

### 3. Deploy

Render deploys automatically when you push to your connected branch. You can also trigger a manual deploy from the dashboard.

### 4. Run initial setup

Use the Render **Shell** tab on your web service to run one-time commands:

```bash
python manage.py createsuperuser
```

Migrations and `collectstatic` are handled by `build.sh` on every deploy.

### 5. Verify WebSocket connectivity

Open your deployed app and check the browser console. You should see the djust WebSocket connection established without errors.

## render.yaml (Infrastructure as Code)

Optionally, define your full stack in a `render.yaml` blueprint:

```yaml
services:
  - type: web
    name: myproject
    runtime: python
    buildCommand: ./build.sh
    startCommand: uvicorn myproject.asgi:application --host 0.0.0.0 --port $PORT
    envVars:
      - key: SECRET_KEY
        generateValue: true
      - key: DJANGO_SETTINGS_MODULE
        value: myproject.settings
      - key: ALLOWED_HOSTS
        value: .onrender.com
      - key: DATABASE_URL
        fromDatabase:
          name: myproject-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          name: myproject-redis
          type: redis
          property: connectionString
      - key: DEBUG
        value: "false"
      - key: PYTHON_VERSION
        value: "3.11.6"

databases:
  - name: myproject-db
    plan: starter

services:
  - type: redis
    name: myproject-redis
    plan: starter
    maxmemoryPolicy: allkeys-lru
```

## Troubleshooting

### WebSocket connection fails

- Verify your start command uses an ASGI server (uvicorn/daphne), not gunicorn with WSGI
- Ensure your client connects with `wss://` not `ws://` -- Render's HTTP-to-HTTPS redirect breaks WebSocket handshakes
- Check that `asgi.py` includes WebSocket routing

### Build fails

- Check the build logs in the Render dashboard
- Verify `build.sh` is executable (`chmod +x build.sh`)
- Ensure all dependencies are listed in `requirements.txt`

### Static files not loading

- Confirm `whitenoise` is in your middleware (after `SecurityMiddleware`)
- Verify `build.sh` runs `collectstatic`
- Check that `STATIC_ROOT` is set in settings

### Redis connection errors

- Use the **Internal** Redis URL, not the external one (lower latency, no TLS overhead)
- Verify the `redis` and `channels-redis` packages are installed
- Check that the Redis instance is in the same Render region as your web service

### Application is slow or times out

- Render free-tier services spin down after inactivity; consider a paid plan for production WebSocket apps
- Check your uvicorn worker count -- a single worker may be sufficient for small apps, but larger apps may benefit from 2-4 workers

## Further Reading

- [Render Documentation](https://render.com/docs)
- [Render WebSocket Support](https://render.com/docs/websocket)
- [djust Deployment Guide](./DEPLOYMENT.md) -- production configuration and horizontal scaling
- [Django Channels Deployment](https://channels.readthedocs.io/en/latest/deploying.html)
