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
        assert "whitenoise" not in content
        assert "CHANNEL_LAYERS" in content
        assert "LIVEVIEW_ALLOWED_MODULES" in content
        assert "SECRET_KEY" in content
        assert "os.environ.get" in content
        assert "django-insecure-" in content

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

    def test_settings_defaults_debug_to_false_from_env(self, tmp_cwd):
        """#637: scaffolded settings.py must fail safe — unconfigured
        deploys should default to DEBUG=False, not DEBUG=True."""
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        settings = (tmp_cwd / "mysite" / "mysite" / "settings.py").read_text()
        # The old buggy default was a hardcoded DEBUG = True.
        assert "DEBUG = True" not in settings
        # Must read DEBUG from env and default to "False" string.
        assert 'DEBUG = os.environ.get("DEBUG", "False")' in settings
        # Must interpret the env var as a boolean flag.
        assert '.lower() in ("true", "1", "yes")' in settings

    def test_settings_narrows_allowed_hosts_from_env(self, tmp_cwd):
        """#637: ALLOWED_HOSTS must default to localhost only when env
        var is unset, not the old ``["*"]`` wildcard."""
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        settings = (tmp_cwd / "mysite" / "mysite" / "settings.py").read_text()
        assert 'ALLOWED_HOSTS = ["*"]' not in settings
        assert 'os.environ.get("ALLOWED_HOSTS"' in settings
        assert '"localhost,127.0.0.1"' in settings

    def test_generates_env_example_file(self, tmp_cwd):
        """#637: scaffold must write a ``.env.example`` template that
        developers copy to ``.env`` for local development."""
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        env_example = tmp_cwd / "mysite" / ".env.example"
        assert env_example.exists()
        content = env_example.read_text()
        # Instructs copy to .env
        assert "cp .env.example .env" in content
        # Declares the three env vars settings.py reads.
        assert "DEBUG=True" in content
        assert "SECRET_KEY=" in content
        assert "ALLOWED_HOSTS=localhost,127.0.0.1" in content
        # Secret key must be a generated value, not a literal placeholder.
        for line in content.splitlines():
            if line.startswith("SECRET_KEY="):
                value = line.split("=", 1)[1]
                assert len(value) >= 20, "secret key should be a real token"
                assert "%(secret_key)s" not in value

    def test_env_example_not_in_gitignore_but_dot_env_is(self, tmp_cwd):
        """``.env.example`` is committed, ``.env`` is ignored."""
        args = types.SimpleNamespace(name="mysite")
        cmd_startproject(args)
        gitignore = (tmp_cwd / "mysite" / ".gitignore").read_text()
        lines = {line.strip() for line in gitignore.splitlines()}
        assert ".env" in lines
        assert ".env.example" not in lines


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
        assert 'dj-view="dashboard.views.DashboardView"' in content

    def test_creates_urls(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        urls = tmp_cwd / "dashboard" / "urls.py"
        assert urls.exists()
        content = urls.read_text()
        assert "DashboardView" in content

    def test_creates_apps_py(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        apps = tmp_cwd / "dashboard" / "apps.py"
        assert apps.exists()
        content = apps.read_text()
        assert "class DashboardConfig(AppConfig)" in content
        assert 'name = "dashboard"' in content

    def test_creates_models_py(self, tmp_cwd):
        args = types.SimpleNamespace(name="dashboard")
        cmd_startapp(args)
        models = tmp_cwd / "dashboard" / "models.py"
        assert models.exists()

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
