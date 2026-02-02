"""Tests for djust.asgi.get_application() helper."""

import os
import pytest
from unittest.mock import patch, MagicMock

# Ensure Django settings are configured before importing
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')


class TestGetApplication:
    """Tests for the get_application() helper."""

    def test_returns_protocol_type_router(self):
        from djust.asgi import get_application
        from channels.routing import ProtocolTypeRouter
        app = get_application()
        assert isinstance(app, ProtocolTypeRouter)

    def test_debug_wraps_with_static_handler(self):
        from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
        from django.conf import settings
        original = settings.DEBUG
        try:
            settings.DEBUG = True
            from djust.asgi import get_application
            app = get_application()
            http_handler = app.application_mapping.get('http')
            assert isinstance(http_handler, ASGIStaticFilesHandler)
        finally:
            settings.DEBUG = original

    def test_no_debug_no_static_handler(self):
        from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
        from django.conf import settings
        original = settings.DEBUG
        try:
            settings.DEBUG = False
            from djust.asgi import get_application
            app = get_application()
            http_handler = app.application_mapping.get('http')
            assert not isinstance(http_handler, ASGIStaticFilesHandler)
        finally:
            settings.DEBUG = original

    def test_custom_websocket_path(self):
        from djust.asgi import get_application
        # Should not raise with a custom path
        app = get_application(websocket_path='/ws/custom/')
        assert app is not None

    def test_no_auth_middleware(self):
        from djust.asgi import get_application
        from channels.auth import AuthMiddlewareStack
        app = get_application(use_auth_middleware=False)
        ws_app = app.application_mapping.get('websocket')
        # When auth is disabled, it should be a URLRouter directly
        from channels.routing import URLRouter
        assert isinstance(ws_app, URLRouter)

    def test_with_auth_middleware(self):
        from djust.asgi import get_application
        app = get_application(use_auth_middleware=True)
        ws_app = app.application_mapping.get('websocket')
        # AuthMiddlewareStack wraps as SessionMiddleware
        from channels.routing import URLRouter
        assert not isinstance(ws_app, URLRouter)


class TestStartprojectCommand:
    """Tests for the djust_startproject management command."""

    def test_creates_project_structure(self, tmp_path):
        from django.core.management import call_command
        call_command('startproject', 'mytest', directory=str(tmp_path))

        project_dir = tmp_path / 'mytest'
        assert project_dir.exists()
        assert (project_dir / 'manage.py').exists()
        assert (project_dir / 'requirements.txt').exists()
        assert (project_dir / 'mytest' / 'asgi.py').exists()
        assert (project_dir / 'mytest' / 'settings.py').exists()
        assert (project_dir / 'mytest' / 'urls.py').exists()
        assert (project_dir / 'live' / 'views.py').exists()
        assert (project_dir / 'live' / 'templates' / 'live' / 'counter.html').exists()

    def test_asgi_uses_helper(self, tmp_path):
        from django.core.management import call_command
        call_command('startproject', 'mytest', directory=str(tmp_path))
        asgi_content = (tmp_path / 'mytest' / 'mytest' / 'asgi.py').read_text()
        assert 'from djust.asgi import get_application' in asgi_content
        assert 'get_application()' in asgi_content

    def test_settings_has_djust(self, tmp_path):
        from django.core.management import call_command
        call_command('startproject', 'mytest', directory=str(tmp_path))
        settings = (tmp_path / 'mytest' / 'mytest' / 'settings.py').read_text()
        assert "'djust'" in settings
        assert "'channels'" in settings
        assert "'daphne'" in settings
        assert 'ASGI_APPLICATION' in settings
        assert 'CHANNEL_LAYERS' in settings

    def test_custom_app_name(self, tmp_path):
        from django.core.management import call_command
        call_command('startproject', 'mytest', directory=str(tmp_path), app_name='myapp')
        assert (tmp_path / 'mytest' / 'myapp' / 'views.py').exists()

    def test_duplicate_project_raises(self, tmp_path):
        from django.core.management import call_command, CommandError
        call_command('startproject', 'mytest', directory=str(tmp_path))
        with pytest.raises(CommandError):
            call_command('startproject', 'mytest', directory=str(tmp_path))

    def test_invalid_name_raises(self, tmp_path):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with pytest.raises(CommandError):
            call_command('startproject', '123-bad', directory=str(tmp_path))
