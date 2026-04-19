"""
Tests for the `djust_theme create-package` management command.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from djust.tests.theming_conftest import *  # noqa: F401,F403  -- ensure Django is configured

pytestmark = pytest.mark.theming


def _call_create_package(*args, **kwargs):
    """Helper that calls the command and captures stdout/stderr."""
    out = StringIO()
    err = StringIO()
    call_command("djust_theme", "create-package", *args, stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


def _pkg_root(base, name):
    """Return the expected package root directory path."""
    return base / f"djust-theme-{name}"


def _py_pkg(base, name):
    """Return the expected Python package directory path."""
    pkg_name = name.replace("-", "_")
    return _pkg_root(base, name) / f"djust_theme_{pkg_name}"


# ---------------------------------------------------------------------------
# Scaffold structure tests
# ---------------------------------------------------------------------------


class TestPackageStructure:
    """Test that create-package generates the correct directory tree."""

    def test_creates_package_root_directory(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        assert _pkg_root(tmp_path, "my-pkg").is_dir()

    def test_creates_pyproject_toml(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        assert (_pkg_root(tmp_path, "my-pkg") / "pyproject.toml").is_file()

    def test_creates_readme(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        assert (_pkg_root(tmp_path, "my-pkg") / "README.md").is_file()

    def test_creates_license(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        assert (_pkg_root(tmp_path, "my-pkg") / "LICENSE").is_file()

    def test_creates_python_package_dir_with_init(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        py_dir = _py_pkg(tmp_path, "my-pkg")
        assert py_dir.is_dir()
        assert (py_dir / "__init__.py").is_file()

    def test_creates_theme_toml(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        assert (_py_pkg(tmp_path, "my-pkg") / "theme.toml").is_file()

    def test_creates_tokens_css(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        assert (_py_pkg(tmp_path, "my-pkg") / "tokens.css").is_file()

    def test_creates_template_dirs_with_gitkeep(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        components_dir = (
            _py_pkg(tmp_path, "my-pkg")
            / "templates"
            / "djust_theming"
            / "themes"
            / "my-pkg"
            / "components"
        )
        assert components_dir.is_dir()
        assert (components_dir / ".gitkeep").is_file()

    def test_creates_static_dirs_with_gitkeep(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        py_pkg = _py_pkg(tmp_path, "my-pkg")
        for subdir in ["css", "fonts"]:
            d = py_pkg / "static" / "djust_theme_my_pkg" / subdir
            assert d.is_dir(), f"Missing static dir: {subdir}"
            assert (d / ".gitkeep").is_file(), f"Missing .gitkeep in static/{subdir}"


# ---------------------------------------------------------------------------
# pyproject.toml content tests
# ---------------------------------------------------------------------------


class TestPyprojectContent:
    """Test that generated pyproject.toml has correct metadata."""

    def test_pyproject_has_package_name(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_pkg_root(tmp_path, "my-pkg") / "pyproject.toml").read_text()
        assert 'name = "djust-theme-my-pkg"' in content

    def test_pyproject_has_author(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}", "--author=Jane Doe")

        content = (_pkg_root(tmp_path, "my-pkg") / "pyproject.toml").read_text()
        assert "Jane Doe" in content

    def test_pyproject_has_djust_theming_dependency(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_pkg_root(tmp_path, "my-pkg") / "pyproject.toml").read_text()
        assert "djust-theming" in content

    def test_pyproject_has_correct_python_package_name(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_pkg_root(tmp_path, "my-pkg") / "pyproject.toml").read_text()
        assert "djust_theme_my_pkg" in content


# ---------------------------------------------------------------------------
# theme.toml content tests
# ---------------------------------------------------------------------------


class TestThemeTomlContent:
    """Test that the generated theme.toml has the correct fields."""

    def test_theme_toml_has_correct_name(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_py_pkg(tmp_path, "my-pkg") / "theme.toml").read_text()
        assert 'name = "my-pkg"' in content

    def test_theme_toml_has_preset(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}", "--preset=blue")

        content = (_py_pkg(tmp_path, "my-pkg") / "theme.toml").read_text()
        assert 'preset = "blue"' in content

    def test_theme_toml_has_design_system(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}", "--design-system=ios")

        content = (_py_pkg(tmp_path, "my-pkg") / "theme.toml").read_text()
        assert 'design_system = "ios"' in content


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestCreatePackageValidation:
    """Test input validation for the create-package command."""

    def test_rejects_invalid_name(self, tmp_path):
        with pytest.raises(CommandError, match="name"):
            _call_create_package("My Package!", f"--dir={tmp_path}")

    def test_rejects_path_traversal(self, tmp_path):
        with pytest.raises(CommandError, match="name"):
            _call_create_package("../etc", f"--dir={tmp_path}")

    def test_rejects_invalid_preset(self, tmp_path):
        with pytest.raises(CommandError, match="preset"):
            _call_create_package("my-pkg", f"--dir={tmp_path}", "--preset=nonexistent")

    def test_rejects_invalid_design_system(self, tmp_path):
        with pytest.raises(CommandError, match="design.system"):
            _call_create_package("my-pkg", f"--dir={tmp_path}", "--design-system=nonexistent")

    def test_error_when_package_exists(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        with pytest.raises(CommandError, match="already exists"):
            _call_create_package("my-pkg", f"--dir={tmp_path}")

    def test_force_overwrites_existing(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")
        _call_create_package("my-pkg", f"--dir={tmp_path}", "--force")

        assert (_pkg_root(tmp_path, "my-pkg") / "pyproject.toml").is_file()


# ---------------------------------------------------------------------------
# Output directory tests
# ---------------------------------------------------------------------------


class TestOutputDir:
    """Test output directory behavior."""

    def test_creates_in_cwd_by_default(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        _call_create_package("my-pkg")

        assert _pkg_root(tmp_path, "my-pkg").is_dir()

    def test_dir_override(self, tmp_path):
        output_dir = tmp_path / "custom-output"
        output_dir.mkdir()

        _call_create_package("my-pkg", f"--dir={output_dir}")

        assert _pkg_root(output_dir, "my-pkg").is_dir()


# ---------------------------------------------------------------------------
# README content tests
# ---------------------------------------------------------------------------


class TestReadmeContent:
    """Test that the generated README has useful instructions."""

    def test_readme_has_install_instructions(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_pkg_root(tmp_path, "my-pkg") / "README.md").read_text()
        assert "pip install" in content

    def test_readme_has_installed_apps_instructions(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_pkg_root(tmp_path, "my-pkg") / "README.md").read_text()
        assert "INSTALLED_APPS" in content

    def test_readme_has_package_name(self, tmp_path):
        _call_create_package("my-pkg", f"--dir={tmp_path}")

        content = (_pkg_root(tmp_path, "my-pkg") / "README.md").read_text()
        assert "djust-theme-my-pkg" in content
