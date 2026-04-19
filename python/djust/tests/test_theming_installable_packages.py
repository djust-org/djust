"""Tests for Phase 5.2: Installable theme package discovery and template loading.

Tests that:
1. _load_theme_package() detects templates/ dir in a package
2. templates_dir is stored on the manifest
3. ThemePackageLoader finds templates from registered package dirs
4. get_installed_template_dirs() returns dirs from registry
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {
                    "context_processors": [],
                },
            }
        ],
    )
    django.setup()

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from djust.theming.registry import ThemeRegistry, get_registry
from djust.theming.manifest import ThemeManifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_package(tmp_path, pkg_name, has_templates=True, has_manifest=True):
    """Create a fake theme package directory structure."""
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()

    # Create __init__.py
    init_content = []
    if has_manifest:
        init_content.append(f"""
from djust.theming.manifest import ThemeManifest
from pathlib import Path

pytestmark = pytest.mark.theming

def get_theme_manifest():
    return ThemeManifest(
        name="{pkg_name.replace('_', '-')}",
        version="1.0.0",
        description="Test theme package",
        design_system="material",
        path=Path(__file__).parent,
    )
""")

    (pkg_dir / "__init__.py").write_text("\n".join(init_content))

    # Create templates directory
    if has_templates:
        tmpl_dir = pkg_dir / "templates" / "djust_theming" / "themes" / pkg_name / "components"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "button.html").write_text(
            '<button class="{{ css_prefix }}btn {{ css_prefix }}btn-{{ variant }} '
            f'{pkg_name}-custom">'
            "{% if slot_content %}{{ slot_content|safe }}{% else %}{{ text }}{% endif %}"
            "</button>"
        )

    return pkg_dir


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the singleton registry before each test."""
    ThemeRegistry._reset()
    yield
    ThemeRegistry._reset()


# ---------------------------------------------------------------------------
# 1. _load_theme_package detects templates dir
# ---------------------------------------------------------------------------


class TestLoadThemePackageDetectsTemplates:
    """_load_theme_package() finds the templates/ dir in a package."""

    def test_detects_templates_dir(self, tmp_path):
        pkg_dir = _make_fake_package(tmp_path, "test_theme_pkg")

        # Create a module that simulates the package
        mod = types.ModuleType("test_theme_pkg")
        mod.__file__ = str(pkg_dir / "__init__.py")
        mod.__path__ = [str(pkg_dir)]

        manifest = ThemeManifest(
            name="test-theme-pkg",
            version="1.0.0",
            design_system="material",
            path=pkg_dir,
        )
        mod.get_theme_manifest = lambda: manifest

        with patch.dict(sys.modules, {"test_theme_pkg": mod}):
            registry = get_registry()
            registry._load_theme_package("test_theme_pkg")

        stored_manifest = registry.get_manifest("test-theme-pkg")
        assert stored_manifest is not None
        assert stored_manifest.templates_dir is not None
        assert (stored_manifest.templates_dir / "djust_theming").is_dir()

    def test_no_templates_dir_when_missing(self, tmp_path):
        pkg_dir = _make_fake_package(tmp_path, "no_tmpl_pkg", has_templates=False)

        mod = types.ModuleType("no_tmpl_pkg")
        mod.__file__ = str(pkg_dir / "__init__.py")
        mod.__path__ = [str(pkg_dir)]

        manifest = ThemeManifest(
            name="no-tmpl-pkg",
            version="1.0.0",
            design_system="material",
            path=pkg_dir,
        )
        mod.get_theme_manifest = lambda: manifest

        with patch.dict(sys.modules, {"no_tmpl_pkg": mod}):
            registry = get_registry()
            registry._load_theme_package("no_tmpl_pkg")

        stored_manifest = registry.get_manifest("no-tmpl-pkg")
        assert stored_manifest is not None
        assert stored_manifest.templates_dir is None


# ---------------------------------------------------------------------------
# 2. templates_dir stored on manifest
# ---------------------------------------------------------------------------


class TestTemplatesDirOnManifest:
    """ThemeManifest has a templates_dir field."""

    def test_templates_dir_default_none(self):
        m = ThemeManifest(name="test", version="1.0.0")
        assert m.templates_dir is None

    def test_templates_dir_can_be_set(self, tmp_path):
        m = ThemeManifest(name="test", version="1.0.0", templates_dir=tmp_path)
        assert m.templates_dir == tmp_path


# ---------------------------------------------------------------------------
# 3. ThemePackageLoader
# ---------------------------------------------------------------------------


class TestThemePackageLoader:
    """ThemePackageLoader finds templates from registered package template dirs."""

    def test_loader_finds_template_from_package(self, tmp_path):
        from djust.theming.loaders import ThemePackageLoader

        # Set up a package with templates
        pkg_dir = _make_fake_package(tmp_path, "loader_test_pkg")
        tmpl_dir = pkg_dir / "templates"

        # Register a manifest with templates_dir
        registry = get_registry()
        manifest = ThemeManifest(
            name="loader-test-pkg",
            version="1.0.0",
            templates_dir=tmpl_dir,
        )
        registry.register_manifest("loader-test-pkg", manifest)

        # Create the loader
        engine = MagicMock()
        loader = ThemePackageLoader(engine)

        # Get template dirs
        dirs = loader.get_dirs()
        assert tmpl_dir in dirs

    def test_loader_returns_empty_when_no_packages(self):
        from djust.theming.loaders import ThemePackageLoader

        get_registry()
        engine = MagicMock()
        loader = ThemePackageLoader(engine)

        dirs = loader.get_dirs()
        assert len(dirs) == 0

    def test_loader_skips_manifests_without_templates_dir(self):
        from djust.theming.loaders import ThemePackageLoader

        registry = get_registry()
        manifest = ThemeManifest(
            name="no-templates",
            version="1.0.0",
        )
        registry.register_manifest("no-templates", manifest)

        engine = MagicMock()
        loader = ThemePackageLoader(engine)

        dirs = loader.get_dirs()
        assert len(dirs) == 0


# ---------------------------------------------------------------------------
# 4. get_installed_template_dirs
# ---------------------------------------------------------------------------


class TestGetInstalledTemplateDirs:
    """get_installed_template_dirs() returns dirs from all registered manifests."""

    def test_returns_dirs_from_manifests(self, tmp_path):
        from djust.theming.loaders import get_installed_template_dirs

        dir1 = tmp_path / "pkg1" / "templates"
        dir1.mkdir(parents=True)
        dir2 = tmp_path / "pkg2" / "templates"
        dir2.mkdir(parents=True)

        registry = get_registry()
        registry.register_manifest(
            "pkg1",
            ThemeManifest(
                name="pkg1",
                version="1.0.0",
                templates_dir=dir1,
            ),
        )
        registry.register_manifest(
            "pkg2",
            ThemeManifest(
                name="pkg2",
                version="1.0.0",
                templates_dir=dir2,
            ),
        )

        dirs = get_installed_template_dirs()
        assert dir1 in dirs
        assert dir2 in dirs

    def test_skips_none_templates_dir(self):
        from djust.theming.loaders import get_installed_template_dirs

        registry = get_registry()
        registry.register_manifest(
            "no-dir",
            ThemeManifest(
                name="no-dir",
                version="1.0.0",
            ),
        )

        dirs = get_installed_template_dirs()
        assert len(dirs) == 0

    def test_empty_when_no_manifests(self):
        from djust.theming.loaders import get_installed_template_dirs

        dirs = get_installed_template_dirs()
        assert dirs == []


# ---------------------------------------------------------------------------
# 5. End-to-end: mock package templates resolved
# ---------------------------------------------------------------------------


class TestEndToEndPackageTemplateResolution:
    """A mock installed package's templates can be found via get_installed_template_dirs."""

    def test_package_templates_in_dir_list(self, tmp_path):
        from djust.theming.loaders import get_installed_template_dirs

        pkg_dir = _make_fake_package(tmp_path, "e2e_pkg")
        tmpl_dir = pkg_dir / "templates"

        registry = get_registry()
        manifest = ThemeManifest(
            name="e2e-pkg",
            version="1.0.0",
            templates_dir=tmpl_dir,
        )
        registry.register_manifest("e2e-pkg", manifest)

        dirs = get_installed_template_dirs()
        assert tmpl_dir in dirs

        # Verify the template file is reachable
        btn_path = tmpl_dir / "djust_theming" / "themes" / "e2e_pkg" / "components" / "button.html"
        assert btn_path.is_file()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
