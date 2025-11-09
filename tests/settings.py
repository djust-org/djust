"""
Minimal Django settings for running tests.
"""

SECRET_KEY = 'test-secret-key-for-djust-tests'

DEBUG = True

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'channels',
    'djust',
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

# Templates configuration for testing
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
            ],
        },
    },
]

USE_TZ = True
