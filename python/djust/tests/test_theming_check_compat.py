"""Tests for check-compat command and compat module (Phase 9.4)."""

from io import StringIO

import pytest

from django.core.management import call_command
from django.core.management.base import CommandError

from djust.theming.compat import CompatIssue, check_theme_compat

pytestmark = pytest.mark.theming


@pytest.fixture
def theme_dir(tmp_path):
    """Create a minimal theme directory with theme.toml and components/.

    Returns the theme directory (tmp_path/test-theme), not tmp_path itself.
    This way tmp_path acts as the themes root directory.
    """
    theme = tmp_path / "test-theme"
    theme.mkdir()
    components = theme / "components"
    components.mkdir()
    toml = theme / "theme.toml"
    toml.write_text(
        '[theme]\nname = "test-theme"\nversion = "0.1.0"\n\n'
        "[tokens]\n"
        'preset = "default"\n'
        'design_system = "material"\n'
    )
    return theme


class TestCheckThemeCompat:
    """Unit tests for check_theme_compat()."""

    def test_no_overrides_returns_empty(self, theme_dir):
        """Theme with no component overrides produces no issues."""
        issues = check_theme_compat(theme_dir)
        assert issues == []

    def test_valid_button_override_no_issues(self, theme_dir):
        """A correct button.html override produces no issues."""
        button = theme_dir / "components" / "button.html"
        button.write_text(
            '<button class="btn">\n'
            "  {{ text }}\n"
            "  {% if slot_icon %}{{ slot_icon|safe }}{% endif %}\n"
            "  {% if slot_content %}{{ slot_content|safe }}{% endif %}\n"
            "  {% if slot_loading %}{{ slot_loading|safe }}{% endif %}\n"
            "</button>\n"
        )
        issues = check_theme_compat(theme_dir)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_missing_required_element(self, theme_dir):
        """button.html without <button> tag produces an error."""
        button = theme_dir / "components" / "button.html"
        button.write_text("<div>{{ text }}</div>\n")
        issues = check_theme_compat(theme_dir)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) >= 1
        assert any("button" in e.message.lower() and "<button>" in e.message for e in errors)

    def test_missing_required_attr(self, theme_dir):
        """alert.html with <div> but no role='alert' produces an error."""
        alert = theme_dir / "components" / "alert.html"
        alert.write_text('<div class="alert">\n  {{ message }}\n</div>\n')
        issues = check_theme_compat(theme_dir)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) >= 1
        assert any('role="alert"' in e.message or "role=alert" in e.message for e in errors)

    def test_missing_required_context_var(self, theme_dir):
        """button.html without {{ text }} reference produces an error."""
        button = theme_dir / "components" / "button.html"
        button.write_text("<button>Click me</button>\n")
        issues = check_theme_compat(theme_dir)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) >= 1
        assert any("text" in e.message for e in errors)

    def test_unused_slot_info(self, theme_dir):
        """button.html that doesn't reference slot_icon produces an info issue."""
        button = theme_dir / "components" / "button.html"
        button.write_text("<button>{{ text }}</button>\n")
        issues = check_theme_compat(theme_dir)
        infos = [i for i in issues if i.severity == "info"]
        assert len(infos) >= 1
        assert any("slot_icon" in i.message for i in infos)

    def test_unknown_component_warning(self, theme_dir):
        """foobar.html in components/ produces a warning about no contract."""
        foobar = theme_dir / "components" / "foobar.html"
        foobar.write_text("<div>unknown</div>\n")
        issues = check_theme_compat(theme_dir)
        warnings = [i for i in issues if i.severity == "warning"]
        assert len(warnings) >= 1
        assert any("foobar" in w.message for w in warnings)

    def test_multiple_required_elements(self, theme_dir):
        """dropdown needs <div> + <button aria-haspopup='true'>; missing attr -> error."""
        dropdown = theme_dir / "components" / "dropdown.html"
        # Has <div> and <button> but no aria-haspopup="true"
        dropdown.write_text(
            '<div class="dropdown">\n  {{ id }}\n  {{ label }}\n  <button>Toggle</button>\n</div>\n'
        )
        issues = check_theme_compat(theme_dir)
        errors = [i for i in issues if i.severity == "error"]
        assert any("aria-haspopup" in e.message for e in errors)

    def test_compat_issue_fields(self):
        """CompatIssue has component, severity, and message fields."""
        issue = CompatIssue(component="button", severity="error", message="missing element")
        assert issue.component == "button"
        assert issue.severity == "error"
        assert issue.message == "missing element"

    def test_context_var_detected_in_if_tag(self, theme_dir):
        """Required context var used in {% if var %} is not flagged."""
        button = theme_dir / "components" / "button.html"
        button.write_text("<button>\n  {% if text %}{{ text }}{% endif %}\n</button>\n")
        issues = check_theme_compat(theme_dir)
        errors = [i for i in issues if i.severity == "error" and "text" in i.message]
        assert errors == []


class TestCheckCompatCommand:
    """Integration tests for the check-compat management command."""

    def test_command_output_pass(self, theme_dir):
        """Command prints PASS for a valid theme with no overrides."""
        out = StringIO()
        call_command(
            "djust_theme",
            "check-compat",
            compat_theme_name="test-theme",
            dir=str(theme_dir.parent),
            stdout=out,
        )
        output = out.getvalue()
        assert "PASS" in output or "pass" in output.lower()

    def test_command_output_errors(self, theme_dir):
        """Command prints ERROR for a broken override."""
        button = theme_dir / "components" / "button.html"
        button.write_text("<div>broken</div>\n")
        out = StringIO()
        call_command(
            "djust_theme",
            "check-compat",
            compat_theme_name="test-theme",
            dir=str(theme_dir.parent),
            stdout=out,
        )
        output = out.getvalue()
        assert "ERROR" in output

    def test_command_missing_theme_dir(self):
        """Command raises error for nonexistent theme."""
        with pytest.raises(CommandError):
            call_command(
                "djust_theme",
                "check-compat",
                "nonexistent-theme",
                dir="/tmp/no-such-dir",
            )

    def test_command_all_flag(self, tmp_path):
        """--all flag checks all themes in directory."""
        # Create two theme dirs
        for name in ["theme-a", "theme-b"]:
            d = tmp_path / name
            d.mkdir()
            (d / "components").mkdir()
            (d / "theme.toml").write_text(
                f'[theme]\nname = "{name}"\nversion = "0.1.0"\n\n'
                "[tokens]\n"
                'preset = "default"\n'
                'design_system = "material"\n'
            )

        out = StringIO()
        call_command(
            "djust_theme",
            "check-compat",
            dir=str(tmp_path),
            check_all=True,
            stdout=out,
        )
        output = out.getvalue()
        assert "theme-a" in output
        assert "theme-b" in output
