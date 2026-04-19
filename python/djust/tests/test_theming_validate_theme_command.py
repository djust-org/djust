"""
Tests for the `djust_theme validate-theme` management command.
"""

import textwrap
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from djust.tests.theming_conftest import *  # noqa: F401,F403  — ensure Django is configured

from djust.theming.registry import ThemeRegistry, get_registry

pytestmark = pytest.mark.theming


@pytest.fixture(autouse=True)
def _ensure_registry_populated():
    """Ensure registry is populated for every test."""
    ThemeRegistry._reset()
    get_registry().discover()
    yield
    ThemeRegistry._reset()


def _call_validate(*args, **kwargs):
    """Helper that calls validate-theme and captures output."""
    out = StringIO()
    err = StringIO()
    call_command("djust_theme", "validate-theme", *args, stdout=out, stderr=err, **kwargs)
    return out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Valid theme tests
# ---------------------------------------------------------------------------


class TestValidateValidTheme:
    def test_validate_valid_theme(self, tmp_path, settings):
        """A well-formed theme passes all checks."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "good-theme"
        theme_dir.mkdir(parents=True)

        toml_content = textwrap.dedent("""\
            [theme]
            name = "good-theme"
            version = "1.0.0"

            [tokens]
            preset = "default"
            design_system = "material"
        """)
        (theme_dir / "theme.toml").write_text(toml_content)

        out, _ = _call_validate("good-theme", f"--dir={themes_dir}")
        assert "PASS" in out or "pass" in out.lower() or "valid" in out.lower()

    def test_validate_valid_token_override(self, tmp_path, settings):
        """Override keys matching ThemeTokens fields pass."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "override-theme"
        theme_dir.mkdir(parents=True)

        toml_content = textwrap.dedent("""\
            [theme]
            name = "override-theme"
            version = "1.0.0"

            [tokens]
            preset = "default"
            design_system = "material"

            [tokens.overrides]
            primary = "220 90% 56%"
            background = "0 0% 100%"
        """)
        (theme_dir / "theme.toml").write_text(toml_content)

        out, _ = _call_validate("override-theme", f"--dir={themes_dir}")
        # Should not mention invalid override
        assert "unknown override" not in out.lower()


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestValidateErrors:
    def test_validate_missing_toml(self, tmp_path, settings):
        """Theme directory without theme.toml reports error."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "no-toml"
        theme_dir.mkdir(parents=True)

        with pytest.raises(CommandError, match="theme.toml"):
            _call_validate("no-toml", f"--dir={themes_dir}")

    def test_validate_invalid_toml(self, tmp_path, settings):
        """Malformed TOML reports parse error."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "bad-toml"
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.toml").write_text("not valid [toml {{{")

        with pytest.raises(CommandError, match="(?i)toml|parse"):
            _call_validate("bad-toml", f"--dir={themes_dir}")

    def test_validate_missing_name(self, tmp_path, settings):
        """TOML without [theme].name reports error."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "no-name"
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.toml").write_text("[theme]\nversion = '1.0.0'\n")

        with pytest.raises(CommandError, match="name"):
            _call_validate("no-name", f"--dir={themes_dir}")

    def test_validate_bad_preset(self, tmp_path, settings):
        """Preset not in registry reports error."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "bad-preset"
        theme_dir.mkdir(parents=True)

        toml_content = textwrap.dedent("""\
            [theme]
            name = "bad-preset"
            version = "1.0.0"

            [tokens]
            preset = "nonexistent-preset"
            design_system = "material"
        """)
        (theme_dir / "theme.toml").write_text(toml_content)

        out, _ = _call_validate("bad-preset", f"--dir={themes_dir}")
        assert "preset" in out.lower()

    def test_validate_bad_design_system(self, tmp_path, settings):
        """Design system not in registry reports error."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "bad-ds"
        theme_dir.mkdir(parents=True)

        toml_content = textwrap.dedent("""\
            [theme]
            name = "bad-ds"
            version = "1.0.0"

            [tokens]
            preset = "default"
            design_system = "nonexistent-ds"
        """)
        (theme_dir / "theme.toml").write_text(toml_content)

        out, _ = _call_validate("bad-ds", f"--dir={themes_dir}")
        assert "design_system" in out.lower() or "design system" in out.lower()

    def test_validate_missing_static_file(self, tmp_path, settings):
        """Referenced CSS file that doesn't exist reports warning."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "missing-css"
        theme_dir.mkdir(parents=True)

        toml_content = textwrap.dedent("""\
            [theme]
            name = "missing-css"
            version = "1.0.0"

            [tokens]
            preset = "default"
            design_system = "material"

            [static]
            css = ["static/css/missing.css"]
        """)
        (theme_dir / "theme.toml").write_text(toml_content)

        out, _ = _call_validate("missing-css", f"--dir={themes_dir}")
        assert "missing" in out.lower() or "not found" in out.lower()

    def test_validate_invalid_token_override(self, tmp_path, settings):
        """Override key not in ThemeTokens reports warning."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "bad-override"
        theme_dir.mkdir(parents=True)

        toml_content = textwrap.dedent("""\
            [theme]
            name = "bad-override"
            version = "1.0.0"

            [tokens]
            preset = "default"
            design_system = "material"

            [tokens.overrides]
            nonexistent_token = "220 90% 56%"
        """)
        (theme_dir / "theme.toml").write_text(toml_content)

        out, _ = _call_validate("bad-override", f"--dir={themes_dir}")
        assert "unknown override" in out.lower() or "nonexistent_token" in out.lower()


# ---------------------------------------------------------------------------
# --all flag
# ---------------------------------------------------------------------------


class TestValidateAll:
    def test_validate_all_flag(self, tmp_path, settings):
        """--all validates all themes in the themes directory."""
        settings.BASE_DIR = tmp_path
        themes_dir = tmp_path / "themes"

        for name in ("theme-a", "theme-b"):
            d = themes_dir / name
            d.mkdir(parents=True)
            (d / "theme.toml").write_text(
                f'[theme]\nname = "{name}"\nversion = "0.1.0"\n'
                f'\n[tokens]\npreset = "default"\ndesign_system = "material"\n'
            )

        out, _ = _call_validate("--all", f"--dir={themes_dir}")
        assert "theme-a" in out
        assert "theme-b" in out
