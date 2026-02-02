"""
djust startproject — scaffold a new djust project with correct configuration.

Usage:
    python -m django djust_startproject myproject
    # or if djust is in INSTALLED_APPS:
    python manage.py djust_startproject myproject
"""

import os
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


# --- Templates ---

ASGI_TEMPLATE = '''"""ASGI config for {project_name}."""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{project_name}.settings')

from djust.asgi import get_application
application = get_application()
'''

SETTINGS_TEMPLATE = '''"""Django settings for {project_name}."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = '{secret_key}'

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'djust',
    '{app_name}',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = '{project_name}.urls'

TEMPLATES = [
    {{
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {{
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        }},
    }},
]

ASGI_APPLICATION = '{project_name}.asgi.application'

CHANNEL_LAYERS = {{
    'default': {{
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }},
}}

DATABASES = {{
    'default': {{
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }},
}}

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
'''

URLS_TEMPLATE = '''"""URL configuration for {project_name}."""
from django.contrib import admin
from django.urls import path
from djust.routing import live_session
from {app_name}.views import CounterView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', live_session(CounterView), name='home'),
]
'''

MANAGE_TEMPLATE = '''#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{project_name}.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed?"
        ) from exc
    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
'''

APP_VIEWS_TEMPLATE = '''"""Sample djust LiveView."""
from djust import LiveView, event_handler


class CounterView(LiveView):
    """A simple counter demonstrating djust LiveView."""
    template_name = '{app_name}/counter.html'

    def mount(self, **kwargs):
        self.count = 0

    @event_handler
    def increment(self):
        self.count += 1

    @event_handler
    def decrement(self):
        self.count -= 1
'''

APP_TEMPLATE = '''<div>
  <h1>djust Counter</h1>
  <p>Count: {{{{ count }}}}</p>
  <button dj-click="increment">+</button>
  <button dj-click="decrement">-</button>
</div>
'''

APP_INIT_TEMPLATE = '''default_app_config = '{app_name}.apps.{app_class}Config'
'''

APP_APPS_TEMPLATE = '''from django.apps import AppConfig


class {app_class}Config(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = '{app_name}'
'''

REQUIREMENTS_TEMPLATE = '''djust
channels
daphne
'''

PROJECT_INIT_TEMPLATE = ''


class Command(BaseCommand):
    help = 'Create a new djust project with correct ASGI, channels, and LiveView configuration.'

    def add_arguments(self, parser):
        parser.add_argument('name', help='Name of the project to create')
        parser.add_argument(
            '--directory',
            default='.',
            help='Directory to create the project in (default: current directory)',
        )
        parser.add_argument(
            '--app-name',
            default=None,
            help='Name of the sample app (default: "live")',
        )

    def handle(self, *args, **options):
        project_name = options['name']
        base_dir = Path(options['directory']).resolve()
        app_name = options['app_name'] or 'live'
        app_class = app_name.capitalize()

        # Validate project name
        if not project_name.isidentifier():
            raise CommandError(f"'{project_name}' is not a valid Python identifier.")

        project_dir = base_dir / project_name
        if project_dir.exists():
            raise CommandError(f"Directory '{project_dir}' already exists.")

        # Generate a secret key
        from django.utils.crypto import get_random_string
        secret_key = get_random_string(50, 'abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*(-_=+)')

        ctx = {
            'project_name': project_name,
            'app_name': app_name,
            'app_class': app_class,
            'secret_key': secret_key,
        }

        # Create directory structure
        dirs = [
            project_dir,
            project_dir / project_name,
            project_dir / app_name,
            project_dir / app_name / 'templates' / app_name,
            project_dir / 'static',
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # Write files
        files = {
            project_dir / 'manage.py': MANAGE_TEMPLATE.format(**ctx),
            project_dir / 'requirements.txt': REQUIREMENTS_TEMPLATE,
            project_dir / project_name / '__init__.py': PROJECT_INIT_TEMPLATE,
            project_dir / project_name / 'asgi.py': ASGI_TEMPLATE.format(**ctx),
            project_dir / project_name / 'settings.py': SETTINGS_TEMPLATE.format(**ctx),
            project_dir / project_name / 'urls.py': URLS_TEMPLATE.format(**ctx),
            project_dir / app_name / '__init__.py': APP_INIT_TEMPLATE.format(**ctx),
            project_dir / app_name / 'apps.py': APP_APPS_TEMPLATE.format(**ctx),
            project_dir / app_name / 'views.py': APP_VIEWS_TEMPLATE.format(**ctx),
            project_dir / app_name / 'templates' / app_name / 'counter.html': APP_TEMPLATE,
        }

        for filepath, content in files.items():
            filepath.write_text(content.lstrip('\n'))

        # Make manage.py executable
        (project_dir / 'manage.py').chmod(0o755)

        self.stdout.write(self.style.SUCCESS(f'Created djust project "{project_name}" in {project_dir}'))
        self.stdout.write(f'\nNext steps:')
        self.stdout.write(f'  cd {project_name}')
        self.stdout.write(f'  pip install -r requirements.txt')
        self.stdout.write(f'  python manage.py migrate')
        self.stdout.write(f'  daphne {project_name}.asgi:application')
