# Deploying djust on Railway

This guide covers deploying a djust LiveView application to [Railway](https://railway.app) with full WebSocket support.

## Prerequisites

- A Railway account ([railway.app](https://railway.app))
- The Railway CLI installed (`npm i -g @railway/cli` or `brew install railway`)
- A djust project ready for deployment
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

### Procfile

Create a `Procfile` in your project root:

```
web: uvicorn myproject.asgi:application --host 0.0.0.0 --port $PORT
```

Replace `myproject` with your Django project name.

### Production Settings

Update your `settings.py` for production:

```python
import os
import dj_database_url

# Security
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", ".railway.app").split(",")
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS if host
]

# Database (Railway provides DATABASE_URL)
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3"),
        conn_max_age=600,
    )
}

# Redis (Railway provides REDIS_URL)
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

Railway natively supports WebSocket connections. No special proxy configuration is needed. Your djust client will connect over `wss://` automatically when served over HTTPS.

Key points:

- Railway terminates TLS at the edge, so your app receives plain WebSocket connections
- No timeout configuration needed for long-lived WebSocket connections
- The `$PORT` environment variable is provided automatically

## Database Setup

1. In the Railway dashboard, click **New** and select **Database > PostgreSQL**
2. Railway automatically sets the `DATABASE_URL` environment variable
3. Link the database to your service

Run migrations after deployment:

```bash
railway run python manage.py migrate
```

## Redis Setup

1. In the Railway dashboard, click **New** and select **Database > Redis**
2. Railway automatically sets the `REDIS_URL` environment variable
3. Link Redis to your service

Both the channel layer and djust state backend will use this Redis instance.

## Step-by-Step Deployment

### 1. Initialize Railway project

```bash
railway login
railway init
```

### 2. Set environment variables

```bash
railway variables set SECRET_KEY=$(python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
railway variables set DJANGO_SETTINGS_MODULE=myproject.settings
railway variables set ALLOWED_HOSTS=.railway.app
railway variables set DEBUG=false
```

### 3. Add PostgreSQL and Redis

```bash
# Add via dashboard or CLI
railway add --plugin postgresql
railway add --plugin redis
```

### 4. Deploy

```bash
railway up
```

Or connect your GitHub repository for automatic deployments from the Railway dashboard.

### 5. Run migrations

```bash
railway run python manage.py migrate
railway run python manage.py collectstatic --noinput
railway run python manage.py createsuperuser
```

### 6. Verify WebSocket connectivity

Open your deployed app and check the browser console. You should see the djust WebSocket connection established without errors.

## Troubleshooting

### WebSocket connection fails with 404

- Verify your `asgi.py` includes WebSocket routing
- Ensure you are using an ASGI server (uvicorn/daphne), not gunicorn with WSGI
- Check that your Procfile uses `uvicorn` with the ASGI application

### Application crashes on startup

- Check logs with `railway logs`
- Verify all environment variables are set: `railway variables`
- Ensure `DJANGO_SETTINGS_MODULE` points to the correct settings file

### Static files not loading

- Confirm `whitenoise` is in your middleware (after `SecurityMiddleware`)
- Run `collectstatic`: `railway run python manage.py collectstatic --noinput`
- Verify `STATIC_ROOT` is set in settings

### Redis connection errors

- Confirm the Redis plugin is added and linked to your service
- Check that `REDIS_URL` is available: `railway variables`
- Verify the `redis` and `channels-redis` packages are installed

### Database connection errors

- Confirm PostgreSQL plugin is added and linked
- Check `DATABASE_URL` is set: `railway variables`
- Ensure `psycopg2-binary` is in your requirements

## Further Reading

- [Railway Documentation](https://docs.railway.app/)
- [djust Deployment Guide](./DEPLOYMENT.md) -- production configuration and horizontal scaling
- [Django Channels Deployment](https://channels.readthedocs.io/en/latest/deploying.html)
