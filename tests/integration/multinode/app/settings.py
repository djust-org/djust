"""Settings for one node of the multi-process state-survival harness (#4000).

Every node of a cluster gets the same ``DJUST_MN_CONFIG`` (JSON), so they share
one session store and one channel layer, as a real deployment's processes do:

* ``session``: ``db`` (a file SQLite every node opens), ``cache_file`` (a
  file-based cache in one directory), ``cache_redis`` (Django's Redis cache),
  ``cached_db`` (the Redis cache in front of the shared database);
* ``layer``: ``redis`` (``channels_redis.core.RedisChannelLayer``) or
  ``memory`` (``channels.layers.InMemoryChannelLayer``, one per process);
* ``db``, ``cache_dir``, ``redis``: the shared locations.
"""

from __future__ import annotations

import json
import os

CONFIG = json.loads(os.environ["DJUST_MN_CONFIG"])

SECRET_KEY = "multinode-test-only-not-secret"  # noqa: S105 - a test fixture's key
DEBUG = os.environ.get("DJUST_MN_DEBUG") == "1"
ALLOWED_HOSTS = ["*"]
USE_TZ = True
ROOT_URLCONF = "tests.integration.multinode.app.urls"
ASGI_APPLICATION = "tests.integration.multinode.app.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "channels",
    "djust",
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": CONFIG["db"],
        # Two processes write one file: wait for the other's lock instead of
        # failing with "database is locked".
        "OPTIONS": {"timeout": 60},
    }
}

_session = CONFIG["session"]
if _session == "db":
    SESSION_ENGINE = "django.contrib.sessions.backends.db"
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
elif _session == "cache_file":
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": CONFIG["cache_dir"],
        }
    }
elif _session == "cache_locmem":
    # Each process has its own: the store a single-process setup works with.
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
elif _session == "file":
    SESSION_ENGINE = "django.contrib.sessions.backends.file"
    SESSION_FILE_PATH = CONFIG["cache_dir"]
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}
elif _session in ("cache_redis", "cached_db"):
    SESSION_ENGINE = (
        "django.contrib.sessions.backends.cache"
        if _session == "cache_redis"
        else "django.contrib.sessions.backends.cached_db"
    )
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CONFIG["redis"],
        }
    }
else:  # pragma: no cover - a harness typo
    raise ValueError("unknown session engine %r" % _session)

if CONFIG["layer"] == "redis":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [{"address": CONFIG["redis"], "socket_timeout": 20}]},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

DJUST_CONFIG = {
    "TENANT_RESOLVER": "header",
    "TENANT_HEADER": "X-Tenant-ID",
}
if CONFIG.get("redis"):
    DJUST_CONFIG["PRESENCE_BACKEND"] = "redis"
    DJUST_CONFIG["REDIS_URL"] = CONFIG["redis"]

LIVEVIEW_ALLOWED_MODULES = ["tests.integration.multinode.app.views"]
LIVEVIEW_CONFIG = {"hot_reload_auto_enable": False}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"stderr": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["stderr"], "level": "WARNING"},
}
