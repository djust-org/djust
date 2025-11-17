"""
Django settings for demo_project.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-demo-key-change-in-production'

DEBUG = True

ALLOWED_HOSTS = ['*']

# CSRF trusted origins for production
CSRF_TRUSTED_ORIGINS = [
    'https://djust.k8.trylinux.org',
    'http://localhost:8002',
    'http://127.0.0.1:8002',
    'https://djust.org',
    'https://www.djust.org',
]

# CSRF cookie settings for production HTTPS
CSRF_COOKIE_SECURE = False  # Set to True only when DEBUG=False
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript to read the cookie
CSRF_USE_SESSIONS = False  # Keep CSRF token in cookie, not session

# Session cookie settings
SESSION_COOKIE_SECURE = False  # Set to True only when DEBUG=False
SESSION_COOKIE_SAMESITE = 'Lax'

# Allow iframes from same origin (for embedded demos on homepage)
X_FRAME_OPTIONS = 'SAMEORIGIN'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'djust',
    # New organized apps
    'djust_shared',      # Shared components and base classes
    'djust_homepage',    # Landing page and navigation
    'djust_demos',       # Feature demonstrations
    'djust_forms',       # Forms demonstrations
    'djust_tests',       # Test views
    'djust_docs',        # Documentation views
    'djust_rentals',     # Rental property management app
    # Old app (will be phased out)
    'demo_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'demo_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'demo_app.context_processors.navbar',
            ],
        },
    },
]

WSGI_APPLICATION = 'demo_project.wsgi.application'
ASGI_APPLICATION = 'demo_project.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
    BASE_DIR / 'demo_app' / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise configuration
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Channels configuration
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer'
    }
}

# LiveView WebSocket Security
# List of module prefixes allowed for WebSocket view mounting
# LiveView Configuration
LIVEVIEW_CONFIG = {
    'use_websocket': True,  # Use HTTP-only mode (disable WebSocket)
    'debug_vdom': False,  # Enable detailed VDOM patch logging
}

# Security: Whitelist allowed modules for LiveView
# Only views from these modules can be mounted via WebSocket
LIVEVIEW_ALLOWED_MODULES = [
    'demo_app.views',
    'djust_demos.views',
    'djust_shared.views',
    'djust_rentals.views',
]

# djust State Backend Configuration
# Controls where LiveView session state is stored
DJUST_CONFIG = {
    # State backend: 'memory' (dev) or 'redis' (production)
    # - 'memory': Fast, simple, but no horizontal scaling (single server only)
    # - 'redis': Production-ready with horizontal scaling across multiple servers
    'STATE_BACKEND': 'memory',  # Change to 'redis' for production

    # Redis connection URL (only used if STATE_BACKEND='redis')
    # Format: redis://[:password@]host[:port][/database]
    # Examples:
    #   'redis://localhost:6379/0'           # Local Redis
    #   'redis://:password@redis-host:6379/0' # With password
    #   'redis://redis.example.com:6379/1'   # Remote Redis, DB 1
    'REDIS_URL': 'redis://localhost:6379/0',

    # Session TTL (Time-To-Live) in seconds
    # How long inactive sessions are kept before cleanup
    # Default: 3600 seconds (1 hour)
    'SESSION_TTL': 3600,
}

# Production Redis Configuration Example:
# DJUST_CONFIG = {
#     'STATE_BACKEND': 'redis',
#     'REDIS_URL': 'redis://redis.example.com:6379/0',
#     'SESSION_TTL': 7200,  # 2 hours for production
# }
