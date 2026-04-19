"""
Tests for ThemeManifest — TOML parsing, validation, serialization, and discovery.
"""

import textwrap
from pathlib import Path

import pytest

from unittest.mock import patch
from django.conf import settings

from djust.tests.theming_conftest import *  # noqa: F401,F403  — ensure Django is configured

from djust.theming.manifest import ThemeManifest, load_theme_manifests
from djust.theming.registry import get_registry

pytestmark = pytest.mark.theming


@pytest.fixture(autouse=True)
def ensure_registry_populated():
    """Ensure the registry has built-in presets/themes for validate() tests."""
    registry = get_registry()
    if not registry._discovered:
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            registry.discover()
    yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_TOML = textwrap.dedent("""\
    [theme]
    name = "my-theme"
    version = "0.1.0"
    description = "A custom theme"
    author = "Test Author"
    license = "MIT"

    [extends]
    base = "default"

    [tokens]
    preset = "blue"
    design_system = "material"

    [tokens.overrides]
    primary = "220 90% 56%"
    radius = "0.75"

    [static]
    css = ["static/css/custom.css"]
    fonts = ["static/fonts/inter.woff2"]
""")

MINIMAL_TOML = textwrap.dedent("""\
    [theme]
    name = "minimal"
    version = "1.0.0"
""")


def _write_toml(tmp_path: Path, content: str, filename: str = "theme.toml") -> Path:
    """Helper to write a theme.toml and return its path."""
    toml_file = tmp_path / filename
    toml_file.write_text(content)
    return toml_file


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------


class TestFromToml:
    """Test ThemeManifest.from_toml() parsing."""

    def test_parse_full_manifest(self, tmp_path):
        toml_path = _write_toml(tmp_path, VALID_TOML)
        manifest = ThemeManifest.from_toml(toml_path)

        assert manifest.name == "my-theme"
        assert manifest.version == "0.1.0"
        assert manifest.description == "A custom theme"
        assert manifest.author == "Test Author"
        assert manifest.license == "MIT"
        assert manifest.base == "default"
        assert manifest.preset == "blue"
        assert manifest.design_system == "material"
        assert manifest.overrides == {"primary": "220 90% 56%", "radius": "0.75"}
        assert manifest.css == ["static/css/custom.css"]
        assert manifest.fonts == ["static/fonts/inter.woff2"]
        assert manifest.path == tmp_path

    def test_parse_minimal_manifest(self, tmp_path):
        toml_path = _write_toml(tmp_path, MINIMAL_TOML)
        manifest = ThemeManifest.from_toml(toml_path)

        assert manifest.name == "minimal"
        assert manifest.version == "1.0.0"
        assert manifest.description == ""
        assert manifest.author == ""
        assert manifest.license == ""
        assert manifest.base is None
        assert manifest.preset == "default"
        assert manifest.design_system == "material"
        assert manifest.overrides == {}
        assert manifest.css == []
        assert manifest.fonts == []

    def test_parse_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ThemeManifest.from_toml(tmp_path / "nonexistent.toml")

    def test_parse_invalid_toml_raises(self, tmp_path):
        toml_path = _write_toml(tmp_path, "not valid [toml {{{")
        with pytest.raises(ValueError, match="Invalid TOML"):
            ThemeManifest.from_toml(toml_path)

    def test_parse_missing_theme_section_raises(self, tmp_path):
        toml_path = _write_toml(tmp_path, "[tokens]\npreset = 'blue'\n")
        with pytest.raises(ValueError, match="\\[theme\\]"):
            ThemeManifest.from_toml(toml_path)

    def test_parse_missing_name_raises(self, tmp_path):
        toml_path = _write_toml(tmp_path, "[theme]\nversion = '1.0.0'\n")
        with pytest.raises(ValueError, match="name"):
            ThemeManifest.from_toml(toml_path)

    def test_parse_missing_version_raises(self, tmp_path):
        toml_path = _write_toml(tmp_path, "[theme]\nname = 'foo'\n")
        with pytest.raises(ValueError, match="version"):
            ThemeManifest.from_toml(toml_path)

    def test_path_set_to_parent_directory(self, tmp_path):
        subdir = tmp_path / "mytheme"
        subdir.mkdir()
        toml_path = _write_toml(subdir, MINIMAL_TOML)
        manifest = ThemeManifest.from_toml(toml_path)
        assert manifest.path == subdir


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidate:
    """Test ThemeManifest.validate() rules."""

    def test_valid_manifest_returns_empty(self, tmp_path):
        toml_path = _write_toml(tmp_path, VALID_TOML)
        manifest = ThemeManifest.from_toml(toml_path)
        errors = manifest.validate()
        assert errors == []

    def test_invalid_name_characters(self):
        manifest = ThemeManifest(name="My Theme!", version="1.0.0")
        errors = manifest.validate()
        assert any("name" in e.lower() for e in errors)

    def test_name_with_uppercase_invalid(self):
        manifest = ThemeManifest(name="MyTheme", version="1.0.0")
        errors = manifest.validate()
        assert any("name" in e.lower() for e in errors)

    def test_name_with_underscore_invalid(self):
        manifest = ThemeManifest(name="my_theme", version="1.0.0")
        errors = manifest.validate()
        assert any("name" in e.lower() for e in errors)

    def test_valid_name_lowercase_hyphens(self):
        manifest = ThemeManifest(name="my-cool-theme", version="1.0.0")
        errors = manifest.validate()
        name_errors = [e for e in errors if "name" in e.lower()]
        assert name_errors == []

    def test_valid_name_digits(self):
        manifest = ThemeManifest(name="theme42", version="1.0.0")
        errors = manifest.validate()
        name_errors = [e for e in errors if "name" in e.lower()]
        assert name_errors == []

    def test_invalid_preset(self):
        manifest = ThemeManifest(name="test", version="1.0.0", preset="nonexistent")
        errors = manifest.validate()
        assert any("preset" in e.lower() for e in errors)

    def test_invalid_design_system(self):
        manifest = ThemeManifest(name="test", version="1.0.0", design_system="nonexistent")
        errors = manifest.validate()
        assert any("design_system" in e.lower() or "design system" in e.lower() for e in errors)

    def test_valid_preset_accepted(self):
        manifest = ThemeManifest(name="test", version="1.0.0", preset="blue")
        errors = manifest.validate()
        preset_errors = [e for e in errors if "preset" in e.lower()]
        assert preset_errors == []

    def test_valid_design_system_accepted(self):
        manifest = ThemeManifest(name="test", version="1.0.0", design_system="ios")
        errors = manifest.validate()
        ds_errors = [e for e in errors if "design" in e.lower()]
        assert ds_errors == []

    def test_empty_name_invalid(self):
        manifest = ThemeManifest(name="", version="1.0.0")
        errors = manifest.validate()
        assert any("name" in e.lower() for e in errors)

    def test_path_traversal_name_rejected(self):
        manifest = ThemeManifest(name="../etc", version="1.0.0")
        errors = manifest.validate()
        assert any("name" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------


class TestToToml:
    """Test ThemeManifest.to_toml() output."""

    def test_roundtrip_full_manifest(self, tmp_path):
        toml_path = _write_toml(tmp_path, VALID_TOML)
        original = ThemeManifest.from_toml(toml_path)
        toml_str = original.to_toml()

        # Write the generated TOML and re-parse it
        rt_path = _write_toml(tmp_path, toml_str, filename="roundtrip.toml")
        roundtrip = ThemeManifest.from_toml(rt_path)

        assert roundtrip.name == original.name
        assert roundtrip.version == original.version
        assert roundtrip.description == original.description
        assert roundtrip.preset == original.preset
        assert roundtrip.design_system == original.design_system
        assert roundtrip.overrides == original.overrides
        assert roundtrip.base == original.base

    def test_minimal_to_toml_parseable(self, tmp_path):
        manifest = ThemeManifest(name="simple", version="0.1.0")
        toml_str = manifest.to_toml()

        rt_path = _write_toml(tmp_path, toml_str, filename="simple.toml")
        roundtrip = ThemeManifest.from_toml(rt_path)

        assert roundtrip.name == "simple"
        assert roundtrip.version == "0.1.0"

    def test_to_toml_contains_theme_section(self):
        manifest = ThemeManifest(name="test", version="1.0.0")
        toml_str = manifest.to_toml()
        assert "[theme]" in toml_str

    def test_to_toml_contains_tokens_section_when_non_default(self):
        manifest = ThemeManifest(name="test", version="1.0.0", preset="blue", design_system="ios")
        toml_str = manifest.to_toml()
        assert "[tokens]" in toml_str
        assert 'preset = "blue"' in toml_str
        assert 'design_system = "ios"' in toml_str

    def test_to_toml_includes_overrides(self):
        manifest = ThemeManifest(
            name="test",
            version="1.0.0",
            overrides={"primary": "220 90% 56%"},
        )
        toml_str = manifest.to_toml()
        assert "[tokens.overrides]" in toml_str
        assert 'primary = "220 90% 56%"' in toml_str

    def test_to_toml_includes_static_section(self):
        manifest = ThemeManifest(
            name="test",
            version="1.0.0",
            css=["static/custom.css"],
            fonts=["static/font.woff2"],
        )
        toml_str = manifest.to_toml()
        assert "[static]" in toml_str

    def test_to_toml_escapes_double_quotes(self, tmp_path):
        manifest = ThemeManifest(
            name="test",
            version="1.0.0",
            description='A theme with "quotes" inside',
            author='Jane "DJ" Doe',
        )
        toml_str = manifest.to_toml()
        assert r'description = "A theme with \"quotes\" inside"' in toml_str
        assert r'author = "Jane \"DJ\" Doe"' in toml_str

        # Verify the escaped TOML is still parseable
        rt_path = _write_toml(tmp_path, toml_str, filename="escaped.toml")
        roundtrip = ThemeManifest.from_toml(rt_path)
        assert roundtrip.description == 'A theme with "quotes" inside'
        assert roundtrip.author == 'Jane "DJ" Doe'

    def test_to_toml_escapes_backslashes(self, tmp_path):
        manifest = ThemeManifest(
            name="test",
            version="1.0.0",
            description="Path: C:\\themes\\custom",
        )
        toml_str = manifest.to_toml()
        assert "C:\\\\themes\\\\custom" in toml_str

        rt_path = _write_toml(tmp_path, toml_str, filename="backslash.toml")
        roundtrip = ThemeManifest.from_toml(rt_path)
        assert roundtrip.description == "Path: C:\\themes\\custom"

    def test_to_toml_escapes_newlines_and_tabs(self, tmp_path):
        manifest = ThemeManifest(
            name="test",
            version="1.0.0",
            description="Line one\nLine two\tTabbed",
        )
        toml_str = manifest.to_toml()
        assert "\\n" in toml_str
        assert "\\t" in toml_str
        assert "\n" not in toml_str.split('description = "')[1].split('"')[0]

        rt_path = _write_toml(tmp_path, toml_str, filename="newlines.toml")
        roundtrip = ThemeManifest.from_toml(rt_path)
        assert roundtrip.description == "Line one\nLine two\tTabbed"


# ---------------------------------------------------------------------------
# Version validation tests
# ---------------------------------------------------------------------------


class TestVersionValidation:
    """Test semver validation in ThemeManifest.validate()."""

    def test_valid_semver_versions(self):
        for version in ("0.1.0", "1.0.0", "1.2.3", "10.20.30", "1.0.0-beta.1", "1.0.0+build.42"):
            manifest = ThemeManifest(name="test", version=version)
            errors = manifest.validate()
            version_errors = [e for e in errors if "version" in e.lower()]
            assert (
                version_errors == []
            ), f"Version '{version}' should be valid but got: {version_errors}"

    def test_invalid_semver_versions(self):
        for version in ("not-a-version", "1.0", "1", "v1.0.0", "1.0.0.0", "foo.bar.baz"):
            manifest = ThemeManifest(name="test", version=version)
            errors = manifest.validate()
            version_errors = [e for e in errors if "version" in e.lower()]
            assert len(version_errors) == 1, f"Version '{version}' should be invalid"


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestLoadThemeManifests:
    """Test load_theme_manifests() discovery function."""

    def test_discover_multiple_themes(self, tmp_path):
        for name in ("alpha", "beta"):
            theme_dir = tmp_path / name
            theme_dir.mkdir()
            _write_toml(
                theme_dir,
                f'[theme]\nname = "{name}"\nversion = "0.1.0"\n',
            )

        manifests = load_theme_manifests(tmp_path)
        assert len(manifests) == 2
        names = {m.name for m in manifests}
        assert names == {"alpha", "beta"}

    def test_discover_skips_dirs_without_toml(self, tmp_path):
        (tmp_path / "has-theme").mkdir()
        _write_toml(
            tmp_path / "has-theme",
            '[theme]\nname = "has-theme"\nversion = "0.1.0"\n',
        )
        (tmp_path / "no-theme").mkdir()
        # no theme.toml in no-theme/

        manifests = load_theme_manifests(tmp_path)
        assert len(manifests) == 1
        assert manifests[0].name == "has-theme"

    def test_discover_empty_dir(self, tmp_path):
        manifests = load_theme_manifests(tmp_path)
        assert manifests == []

    def test_discover_nonexistent_dir(self, tmp_path):
        manifests = load_theme_manifests(tmp_path / "nonexistent")
        assert manifests == []
