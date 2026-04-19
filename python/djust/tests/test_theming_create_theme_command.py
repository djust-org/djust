"""
Tests for the `djust_theme create-theme` management command.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from djust.tests.theming_conftest import *  # noqa: F401,F403  — ensure Django is configured

pytestmark = pytest.mark.theming


def _call_create_theme(*args, **kwargs):
    """Helper that calls the command and captures stdout/stderr."""
    out = StringIO()
    err = StringIO()
    call_command("djust_theme", "create-theme", *args, stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Scaffold structure tests
# ---------------------------------------------------------------------------


class TestScaffoldStructure:
    """Test that create-theme generates the correct directory tree."""

    def test_creates_theme_directory(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}")

        theme_dir = themes_dir / "my-theme"
        assert theme_dir.is_dir()

    def test_creates_theme_toml(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}")

        toml_file = themes_dir / "my-theme" / "theme.toml"
        assert toml_file.is_file()
        content = toml_file.read_text()
        assert 'name = "my-theme"' in content

    def test_creates_tokens_css(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}")

        css_file = themes_dir / "my-theme" / "tokens.css"
        assert css_file.is_file()

    def test_creates_subdirectories_with_gitkeep(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}")

        theme_dir = themes_dir / "my-theme"
        expected_dirs = [
            "components",
            "layouts",
            "pages",
            "static/css",
            "static/fonts",
        ]
        for subdir in expected_dirs:
            d = theme_dir / subdir
            assert d.is_dir(), f"Missing directory: {subdir}"
            assert (d / ".gitkeep").is_file(), f"Missing .gitkeep in {subdir}"


# ---------------------------------------------------------------------------
# Theme.toml content tests
# ---------------------------------------------------------------------------


class TestTomlContent:
    """Test that generated theme.toml has correct content."""

    def test_default_preset_and_design_system(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}")

        content = (themes_dir / "my-theme" / "theme.toml").read_text()
        assert 'preset = "default"' in content
        assert 'design_system = "material"' in content

    def test_custom_preset_and_design_system(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme(
            "my-theme",
            f"--dir={themes_dir}",
            "--preset=blue",
            "--design-system=ios",
        )

        content = (themes_dir / "my-theme" / "theme.toml").read_text()
        assert 'preset = "blue"' in content
        assert 'design_system = "ios"' in content

    def test_base_theme_in_toml(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme(
            "my-theme",
            f"--dir={themes_dir}",
            "--base=default",
        )

        content = (themes_dir / "my-theme" / "theme.toml").read_text()
        assert 'base = "default"' in content


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestCreateThemeValidation:
    """Test input validation for the create-theme command."""

    def test_rejects_invalid_preset(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        with pytest.raises(CommandError, match="preset"):
            _call_create_theme(
                "my-theme",
                f"--dir={themes_dir}",
                "--preset=nonexistent",
            )

    def test_rejects_invalid_design_system(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        with pytest.raises(CommandError, match="design.system"):
            _call_create_theme(
                "my-theme",
                f"--dir={themes_dir}",
                "--design-system=nonexistent",
            )

    def test_rejects_invalid_theme_name(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        with pytest.raises(CommandError, match="name"):
            _call_create_theme("My Theme!", f"--dir={themes_dir}")

    def test_rejects_path_traversal_name(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        with pytest.raises(CommandError, match="name"):
            _call_create_theme("../etc", f"--dir={themes_dir}")

    def test_error_when_theme_already_exists(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        # Create once
        _call_create_theme("my-theme", f"--dir={themes_dir}")

        # Try again — should fail
        with pytest.raises(CommandError, match="already exists"):
            _call_create_theme("my-theme", f"--dir={themes_dir}")

    def test_force_overwrites_existing(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        # Create once
        _call_create_theme("my-theme", f"--dir={themes_dir}")

        # Force overwrite
        _call_create_theme("my-theme", f"--dir={themes_dir}", "--force")

        # Should still exist
        assert (themes_dir / "my-theme" / "theme.toml").is_file()


# ---------------------------------------------------------------------------
# tokens.css content tests
# ---------------------------------------------------------------------------


class TestTokensCss:
    """Test tokens.css template content."""

    def test_tokens_css_contains_theme_name(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}")

        css_content = (themes_dir / "my-theme" / "tokens.css").read_text()
        assert "my-theme" in css_content

    def test_tokens_css_contains_preset_info(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={themes_dir}", "--preset=blue")

        css_content = (themes_dir / "my-theme" / "tokens.css").read_text()
        assert "blue" in css_content


# ---------------------------------------------------------------------------
# Config integration tests
# ---------------------------------------------------------------------------


class TestThemesDirConfig:
    """Test themes_dir config integration."""

    def test_uses_default_themes_dir(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        # Create the default themes/ directory
        (tmp_path / "themes").mkdir()

        _call_create_theme("my-theme")

        assert (tmp_path / "themes" / "my-theme" / "theme.toml").is_file()

    def test_dir_override(self, tmp_path, settings):
        settings.BASE_DIR = tmp_path
        custom_dir = tmp_path / "custom-themes"
        custom_dir.mkdir()

        _call_create_theme("my-theme", f"--dir={custom_dir}")

        assert (custom_dir / "my-theme" / "theme.toml").is_file()
