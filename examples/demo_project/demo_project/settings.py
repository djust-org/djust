"""
Django settings for demo_project.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-demo-key-change-in-production"

# DJUST_DEMO_DEBUG=0 runs the demo in production mode (the ADR-038 E5 matrix
# checks both error contracts). Default unchanged: DEBUG on.
DEBUG = os.environ.get("DJUST_DEMO_DEBUG", "1") != "0"

ALLOWED_HOSTS = ["*"]

# CSRF trusted origins for production
CSRF_TRUSTED_ORIGINS = [
    "https://djust.k8.trylinux.org",
    "http://localhost:8002",
    "http://127.0.0.1:8002",
    "https://djust.org",
    "https://www.djust.org",
]

# CSRF cookie settings for production HTTPS
CSRF_COOKIE_SECURE = False  # Set to True only when DEBUG=False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # Allow JavaScript to read the cookie
CSRF_USE_SESSIONS = False  # Keep CSRF token in cookie, not session

# Session cookie settings
SESSION_COOKIE_SECURE = False  # Set to True only when DEBUG=False
SESSION_COOKIE_SAMESITE = "Lax"

# Allow iframes from same origin (for embedded demos on homepage)
X_FRAME_OPTIONS = "SAMEORIGIN"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "djust",
    "djust.auth",  # account backends + page kit (ADR-039)
    "djust.theming",  # Optional extra — needed for theming tests
    "djust.admin_ext",  # Optional extra — needed for admin tests
    # Optional extra — the component gallery's LiveView routes render templates
    # from this app's templates/ directory, which Django only scans when the
    # app is installed. Without it the gallery 500s with
    # TemplateDoesNotExist: djust_components/gallery/index.html.
    "djust.components",
    # New organized apps
    "djust_shared",  # Shared components and base classes
    "djust_homepage",  # Landing page and navigation
    "djust_demos",  # Feature demonstrations
    "djust_forms",  # Forms demonstrations
    "djust_tests",  # Test views
    "djust_docs",  # Documentation views
    "djust_rentals",  # Rental property management app
    "sticky_demo",  # Sticky LiveViews / app-shell demo (#1784)
    # Old app (will be phased out)
    "demo_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Observability localhost-gate — must come early so it rejects LAN
    # requests before any downstream middleware can do work.
    "djust.observability.middleware.LocalhostOnlyObservabilityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "demo_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "demo_app.context_processors.navbar",
                # Exposes theme_head, theme_switcher and related template
                # variables (djust_theming.E001 warns when it is missing).
                "djust.theming.context_processors.theme_context",
            ],
        },
    },
]

WSGI_APPLICATION = "demo_project.wsgi.application"
ASGI_APPLICATION = "demo_project.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [
    BASE_DIR / "static",
    BASE_DIR / "demo_app" / "static",
]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Unique cookie names to avoid collisions with other Django projects on localhost
SESSION_COOKIE_NAME = "djust_demo_sessionid"
CSRF_COOKIE_NAME = "djust_demo_csrftoken"

# Channels configuration
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# LiveView WebSocket Security
# List of module prefixes allowed for WebSocket view mounting
# LiveView Configuration
LIVEVIEW_CONFIG = {
    "use_websocket": True,  # Use HTTP-only mode (disable WebSocket)
    "debug_vdom": False,  # Enable detailed VDOM patch logging
}

# Security: Whitelist allowed modules for LiveView
# Only views from these modules can be mounted via WebSocket
LIVEVIEW_ALLOWED_MODULES = [
    "demo_app.views",
    "djust_demos.views",
    "djust_shared.views",
    "djust_rentals.views",
    "djust_tests.views",
    # djust's own routed LiveViews (the admin extension at /djust-admin-demo/).
    # An explicit list replaces the default that admits them (check V015).
    "djust",
]

# djust State Backend Configuration
# Controls where LiveView session state is stored
DJUST_CONFIG = {
    # State backend: 'memory' (dev) or 'redis' (production)
    # - 'memory': Fast, simple, but no horizontal scaling (single server only)
    # - 'redis': Production-ready with horizontal scaling across multiple servers
    "STATE_BACKEND": "memory",  # Change to 'redis' for production
    # Redis connection URL (only used if STATE_BACKEND='redis')
    # Format: redis://[:password@]host[:port][/database]
    # Examples:
    #   'redis://localhost:6379/0'           # Local Redis
    #   'redis://:password@redis-host:6379/0' # With password
    #   'redis://redis.example.com:6379/1'   # Remote Redis, DB 1
    "REDIS_URL": "redis://localhost:6379/0",
    # Session TTL (Time-To-Live) in seconds
    # How long inactive sessions are kept before cleanup
    # Default: 3600 seconds (1 hour)
    "SESSION_TTL": 3600,
    # Tenant configuration (for multi-tenant demo)
    "TENANT_RESOLVER": "header",  # Demo uses query param override in view
    "TENANT_REQUIRED": False,  # Don't 404 without tenant
    "TENANT_DEFAULT": "acme",  # Default tenant for demo
}

# Production Redis Configuration Example:
# DJUST_CONFIG = {
#     'STATE_BACKEND': 'redis',
#     'REDIS_URL': 'redis://redis.example.com:6379/0',
#     'SESSION_TTL': 7200,  # 2 hours for production
# }

# DJUST_DEMO_LOG_CONSOLE=1 sends djust's logs to the console at DEBUG level, so
# the ADR-038 E5 matrix can check the server log destination in either mode.
if os.environ.get("DJUST_DEMO_LOG_CONSOLE") == "1":
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "loggers": {"djust": {"handlers": ["console"], "level": "DEBUG", "propagate": False}},
    }


# djust.auth.accounts "allauth" backend (ADR-039): wired only when the optional
# django-allauth dependency is installed (it is in the dev extras).
try:
    import allauth  # noqa: F401

    ALLAUTH_AVAILABLE = True
except ImportError:
    ALLAUTH_AVAILABLE = False

if ALLAUTH_AVAILABLE:
    INSTALLED_APPS += [
        "django.contrib.sites",
        "allauth",
        "allauth.account",
        "allauth.socialaccount",
    ]
    MIDDLEWARE += ["allauth.account.middleware.AccountMiddleware"]
    AUTHENTICATION_BACKENDS = [
        "django.contrib.auth.backends.ModelBackend",
        "allauth.account.auth_backends.AuthenticationBackend",
    ]
    SITE_ID = 1
