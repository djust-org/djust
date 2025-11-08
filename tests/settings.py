"""
Minimal Django settings for running tests.
"""

SECRET_KEY = 'test-secret-key-for-django-rust-live-tests'

DEBUG = True

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'channels',
    'django_rust_live',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Required for sessions
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
]

# Channels configuration
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer'
    }
}

USE_TZ = True
