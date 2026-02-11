"""
Tests for CLI startproject/startapp commands (#266).
"""

import types

import pytest

from djust.cli import cmd_startproject, cmd_startapp


@pytest.fixture
def tmp_cwd(tmp_path, monkeypatch):
    """Switch cwd to a temp directory for scaffold tests."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestStartProject:
    def test_creates_project_directory(self, tmp_cwd):
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        assert (tmp_cwd / "mysite").is_dir()

    def test_creates_manage_py(self, tmp_cwd):
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        manage = tmp_cwd / "mysite" / "manage.py"
        assert manage.exists()
        content = manage.read_text()
        assert "mysite.settings" in content

    def test_creates_settings_with_djust(self, tmp_cwd):
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        settings = tmp_cwd / "mysite" / "mysite" / "settings.py"
        assert settings.exists()
        content = settings.read_text()
        assert '"daphne"' in content
        assert '"djust"' in content
        assert "whitenoise" in content
        assert "CHANNEL_LAYERS" in content
        assert "LIVEVIEW_ALLOWED_MODULES" in content
        assert "SECRET_KEY" in content

    def test_creates_asgi_with_live_session(self, tmp_cwd):
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        asgi = tmp_cwd / "mysite" / "mysite" / "asgi.py"
        assert asgi.exists()
        content = asgi.read_text()
        assert "live_session" in content

    def test_creates_templates_directory(self, tmp_cwd):
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        assert (tmp_cwd / "mysite" / "templates").is_dir()

    def test_rejects_existing_directory(self, tmp_cwd):
        (tmp_cwd / "existing").mkdir()
        args = types.SimpleNamespace(name="existing")
        with pytest.raises(SystemExit):
            cmd_startproject(args)

    def test_rejects_invalid_name(self, tmp_cwd):
        args = types.SimpleNamespace(name="my-site")
        with pytest.raises(SystemExit):
            cmd_startproject(args)


class TestStartApp:
    def test_creates_app_directory(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        assert (tmp_cwd / "dashboard").is_dir()

    def test_creates_views_with_liveview(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        views = tmp_cwd / "dashboard" / "views.py"
        assert views.exists()
        content = views.read_text()
        assert "class DashboardView(LiveView)" in content
        assert "@event_handler()" in content

    def test_creates_template_with_djust_view(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        template = tmp_cwd / "dashboard" / "templates" / "dashboard" / "index.html"
        assert template.exists()
        content = template.read_text()
        assert 'data-djust-view="dashboard.views.DashboardView"' in content

    def test_creates_urls(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        urls = tmp_cwd / "dashboard" / "urls.py"
        assert urls.exists()
        content = urls.read_text()
        assert "DashboardView" in content

    def test_rejects_existing_directory(self, tmp_cwd):
        (tmp_cwd / "existing").mkdir()
        args = types.SimpleNamespace(name="existing")
        with pytest.raises(SystemExit):
            cmd_startapp(args)

    def test_underscore_name_generates_camelcase_class(self, tmp_cwd):
        args = types.SimpleNamespace(name="user_profile")
        cmd_startapp(args)
        views = tmp_cwd / "user_profile" / "views.py"
        content = views.read_text()
        assert "class UserProfileView(LiveView)" in content
