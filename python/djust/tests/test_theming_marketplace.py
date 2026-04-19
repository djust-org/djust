"""Tests for the marketplace spec (Phase 9.3)."""

# Import conftest first to configure Django settings before we add ours.
import tests.conftest  # noqa: F401

import pytest
import textwrap
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from djust.theming.contracts import COMPONENT_CONTRACTS
from djust.theming.gallery.storybook import get_component_coverage
from djust.theming.manifest import ThemeManifest

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# ThemeManifest marketplace fields
# ---------------------------------------------------------------------------


class TestManifestMarketplaceFields:
    def test_parse_marketplace_section(self, tmp_path):
        """from_toml() parses [marketplace] fields."""
        toml_content = textwrap.dedent("""\
            [theme]
            name = "test-theme"
            version = "1.0.0"

            [marketplace]
            screenshots = ["screenshot1.png", "screenshot2.png"]
            tags = ["modern", "dark"]
            compatibility_range = ">=0.3.0,<1.0.0"
            preview_url = "https://example.com/preview"
        """)
        toml_file = tmp_path / "theme.toml"
        toml_file.write_text(toml_content)

        manifest = ThemeManifest.from_toml(toml_file)
        assert manifest.screenshots == ["screenshot1.png", "screenshot2.png"]
        assert manifest.tags == ["modern", "dark"]
        assert manifest.compatibility_range == ">=0.3.0,<1.0.0"
        assert manifest.preview_url == "https://example.com/preview"

    def test_marketplace_fields_default_empty(self, tmp_path):
        """Marketplace fields default to empty when section is absent."""
        toml_content = textwrap.dedent("""\
            [theme]
            name = "minimal"
            version = "1.0.0"
        """)
        toml_file = tmp_path / "theme.toml"
        toml_file.write_text(toml_content)

        manifest = ThemeManifest.from_toml(toml_file)
        assert manifest.screenshots == []
        assert manifest.tags == []
        assert manifest.compatibility_range == ""
        assert manifest.preview_url == ""

    def test_to_toml_includes_marketplace_section(self, tmp_path):
        """to_toml() serializes marketplace fields when present."""
        manifest = ThemeManifest(
            name="my-theme",
            version="1.0.0",
            screenshots=["shot.png"],
            tags=["responsive", "dark"],
            compatibility_range=">=0.3.0",
            preview_url="https://example.com",
        )
        toml_str = manifest.to_toml()
        assert "[marketplace]" in toml_str
        assert "shot.png" in toml_str
        assert "responsive" in toml_str
        assert "dark" in toml_str
        assert ">=0.3.0" in toml_str
        assert "https://example.com" in toml_str

    def test_to_toml_omits_marketplace_when_empty(self, tmp_path):
        """to_toml() omits [marketplace] section when all fields are empty."""
        manifest = ThemeManifest(
            name="plain",
            version="1.0.0",
        )
        toml_str = manifest.to_toml()
        assert "[marketplace]" not in toml_str


# ---------------------------------------------------------------------------
# Component coverage
# ---------------------------------------------------------------------------


class TestComponentCoverage:
    def test_no_overrides_all_inherited(self, tmp_path):
        """Theme with no component overrides shows 0% coverage."""
        theme_dir = tmp_path / "my-theme"
        theme_dir.mkdir()
        (theme_dir / "theme.toml").write_text(
            textwrap.dedent("""\
            [theme]
            name = "my-theme"
            version = "1.0.0"
        """)
        )

        result = get_component_coverage("my-theme", tmp_path)
        assert result["overridden"] == []
        assert len(result["inherited"]) == len(COMPONENT_CONTRACTS)
        assert result["coverage_pct"] == 0.0

    def test_some_overrides(self, tmp_path):
        """Theme with some component overrides shows correct coverage."""
        theme_dir = tmp_path / "my-theme"
        comp_dir = theme_dir / "components"
        comp_dir.mkdir(parents=True)
        (theme_dir / "theme.toml").write_text(
            textwrap.dedent("""\
            [theme]
            name = "my-theme"
            version = "1.0.0"
        """)
        )
        # Create overrides for button and card
        (comp_dir / "button.html").write_text("<button>custom</button>")
        (comp_dir / "card.html").write_text("<div>custom card</div>")

        result = get_component_coverage("my-theme", tmp_path)
        assert sorted(result["overridden"]) == ["button", "card"]
        assert "button" not in result["inherited"]
        assert "card" not in result["inherited"]
        total = len(COMPONENT_CONTRACTS)
        expected_pct = round(2 / total * 100, 1)
        assert result["coverage_pct"] == expected_pct

    def test_all_overridden(self, tmp_path):
        """Theme overriding all components shows 100% coverage."""
        theme_dir = tmp_path / "my-theme"
        comp_dir = theme_dir / "components"
        comp_dir.mkdir(parents=True)
        (theme_dir / "theme.toml").write_text(
            textwrap.dedent("""\
            [theme]
            name = "my-theme"
            version = "1.0.0"
        """)
        )
        for name in COMPONENT_CONTRACTS:
            (comp_dir / f"{name}.html").write_text(f"<div>{name}</div>")

        result = get_component_coverage("my-theme", tmp_path)
        assert len(result["overridden"]) == len(COMPONENT_CONTRACTS)
        assert result["inherited"] == []
        assert result["coverage_pct"] == 100.0

    def test_missing_theme_raises(self, tmp_path):
        """Raises FileNotFoundError for a non-existent theme directory."""
        with pytest.raises(FileNotFoundError):
            get_component_coverage("does-not-exist", tmp_path)


# ---------------------------------------------------------------------------
# marketplace-info CLI command
# ---------------------------------------------------------------------------


class TestMarketplaceInfoCommand:
    def _create_theme(self, tmp_path, name, overrides=None, marketplace_toml=""):
        theme_dir = tmp_path / name
        comp_dir = theme_dir / "components"
        comp_dir.mkdir(parents=True)
        toml = textwrap.dedent(f"""\
            [theme]
            name = "{name}"
            version = "1.0.0"
            {marketplace_toml}
        """)
        (theme_dir / "theme.toml").write_text(toml)
        for comp_name in overrides or []:
            (comp_dir / f"{comp_name}.html").write_text(f"<div>{comp_name}</div>")

    def test_basic_output(self, tmp_path):
        """marketplace-info shows theme name, coverage, and component lists."""
        self._create_theme(tmp_path, "test-theme", overrides=["button", "card"])
        out = StringIO()
        call_command(
            "djust_theme",
            "marketplace-info",
            "test-theme",
            dir=str(tmp_path),
            stdout=out,
        )
        output = out.getvalue()
        assert "test-theme" in output
        assert "button" in output
        assert "card" in output
        assert "Coverage" in output or "coverage" in output

    def test_shows_inherited_components(self, tmp_path):
        """marketplace-info lists inherited (non-overridden) components."""
        self._create_theme(tmp_path, "test-theme", overrides=["button"])
        out = StringIO()
        call_command(
            "djust_theme",
            "marketplace-info",
            "test-theme",
            dir=str(tmp_path),
            stdout=out,
        )
        output = out.getvalue()
        # "card" is not overridden, so it should appear in inherited
        assert "card" in output

    def test_missing_theme_error(self, tmp_path):
        """marketplace-info raises CommandError for non-existent theme."""
        out = StringIO()
        with pytest.raises(CommandError, match="Theme directory not found"):
            call_command(
                "djust_theme",
                "marketplace-info",
                "nonexistent",
                dir=str(tmp_path),
                stdout=out,
            )

    def test_marketplace_section_in_output(self, tmp_path):
        """marketplace-info shows marketplace metadata when present in theme.toml."""
        marketplace = textwrap.dedent("""\

            [marketplace]
            tags = ["modern", "dark"]
            compatibility_range = ">=0.3.0"
            preview_url = "https://example.com"
        """)
        self._create_theme(
            tmp_path,
            "fancy-theme",
            overrides=["button"],
            marketplace_toml=marketplace,
        )
        out = StringIO()
        call_command(
            "djust_theme",
            "marketplace-info",
            "fancy-theme",
            dir=str(tmp_path),
            stdout=out,
        )
        output = out.getvalue()
        assert "modern" in output or "dark" in output
        assert ">=0.3.0" in output or "compatibility" in output.lower()
