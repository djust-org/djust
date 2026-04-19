"""
Tests for ThemeRegistry — singleton, registration, discovery, thread safety.
"""

import threading
from unittest.mock import patch, MagicMock

import pytest

from djust.tests.theming_conftest import *  # noqa: F401,F403  — ensure Django is configured

from djust.theming.registry import ThemeRegistry, get_registry

pytestmark = pytest.mark.theming


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the singleton before and after each test."""
    ThemeRegistry._reset()
    yield
    ThemeRegistry._reset()


# ---------------------------------------------------------------------------
# Singleton behaviour
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_singleton_returns_same_instance(self):
        a = ThemeRegistry()
        b = ThemeRegistry()
        assert a is b

    def test_get_registry_returns_singleton(self):
        reg = get_registry()
        assert reg is ThemeRegistry()

    def test_reset_clears_singleton(self):
        a = ThemeRegistry()
        ThemeRegistry._reset()
        b = ThemeRegistry()
        assert a is not b

    def test_reset_clears_data(self):
        reg = get_registry()
        reg.register_preset("x", "value")
        ThemeRegistry._reset()
        reg2 = get_registry()
        assert reg2.get_preset("x") is None


# ---------------------------------------------------------------------------
# Registration API
# ---------------------------------------------------------------------------


class TestRegisterPreset:
    def test_register_and_get_preset(self):
        reg = get_registry()
        reg.register_preset("custom", {"name": "custom"})
        assert reg.get_preset("custom") == {"name": "custom"}

    def test_overwrite_preset(self):
        reg = get_registry()
        reg.register_preset("custom", "v1")
        reg.register_preset("custom", "v2")
        assert reg.get_preset("custom") == "v2"

    def test_get_preset_default(self):
        reg = get_registry()
        assert reg.get_preset("nonexistent") is None

    def test_get_preset_custom_default(self):
        reg = get_registry()
        sentinel = object()
        assert reg.get_preset("nonexistent", sentinel) is sentinel

    def test_has_preset_true(self):
        reg = get_registry()
        reg.register_preset("exists", True)
        assert reg.has_preset("exists") is True

    def test_has_preset_false(self):
        reg = get_registry()
        assert reg.has_preset("nope") is False


class TestRegisterTheme:
    def test_register_and_get_theme(self):
        reg = get_registry()
        reg.register_theme("custom-ds", {"type": "design_system"})
        assert reg.get_theme("custom-ds") == {"type": "design_system"}

    def test_overwrite_theme(self):
        reg = get_registry()
        reg.register_theme("ds", "v1")
        reg.register_theme("ds", "v2")
        assert reg.get_theme("ds") == "v2"

    def test_get_theme_default(self):
        reg = get_registry()
        assert reg.get_theme("nonexistent") is None

    def test_has_theme_true(self):
        reg = get_registry()
        reg.register_theme("exists", True)
        assert reg.has_theme("exists") is True

    def test_has_theme_false(self):
        reg = get_registry()
        assert reg.has_theme("nope") is False


class TestRegisterManifest:
    def test_register_and_get_manifest(self):
        reg = get_registry()
        manifest = MagicMock()
        reg.register_manifest("m1", manifest)
        assert reg.get_manifest("m1") is manifest

    def test_get_manifest_missing(self):
        reg = get_registry()
        assert reg.get_manifest("missing") is None


# ---------------------------------------------------------------------------
# List API
# ---------------------------------------------------------------------------


class TestListAPIs:
    def test_list_presets_returns_copy(self):
        reg = get_registry()
        reg.register_preset("a", 1)
        reg.register_preset("b", 2)
        result = reg.list_presets()
        assert result == {"a": 1, "b": 2}
        # Modifying returned dict does not affect registry
        result["c"] = 3
        assert not reg.has_preset("c")

    def test_list_themes_returns_copy(self):
        reg = get_registry()
        reg.register_theme("x", 10)
        result = reg.list_themes()
        assert result == {"x": 10}
        result["y"] = 20
        assert not reg.has_theme("y")

    def test_list_manifests_returns_copy(self):
        reg = get_registry()
        reg.register_manifest("m", "data")
        result = reg.list_manifests()
        assert result == {"m": "data"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_discover_populates_builtins(self):
        reg = get_registry()
        reg.discover()
        # All 19 built-in presets
        assert len(reg.list_presets()) >= 19
        assert reg.has_preset("default")
        assert reg.has_preset("blue")
        # All 11 built-in design systems
        assert len(reg.list_themes()) >= 11
        assert reg.has_theme("material")
        assert reg.has_theme("ios")

    def test_discover_is_idempotent(self):
        reg = get_registry()
        reg.discover()
        count1 = len(reg.list_presets())
        reg.discover()
        count2 = len(reg.list_presets())
        assert count1 == count2

    def test_discover_themes_dir(self, tmp_path, settings):
        """Manifests from a themes_dir are loaded into the registry."""
        settings.BASE_DIR = tmp_path

        # Create a theme.toml in a themes subdirectory
        themes_dir = tmp_path / "themes"
        theme_dir = themes_dir / "my-custom"
        theme_dir.mkdir(parents=True)
        (theme_dir / "theme.toml").write_text('[theme]\nname = "my-custom"\nversion = "0.1.0"\n')

        reg = get_registry()
        reg.discover()
        assert reg.get_manifest("my-custom") is not None

    def test_discover_djust_themes_setting(self, settings):
        """Packages listed in DJUST_THEMES are imported and their presets/themes loaded."""
        fake_module = MagicMock()
        fake_module.PRESETS = {"ext-preset": "preset-data"}
        fake_module.DESIGN_SYSTEMS = {"ext-ds": "ds-data"}
        del fake_module.get_theme_manifest  # no manifest

        settings.DJUST_THEMES = ["fake_theme_package"]

        reg = get_registry()
        with patch("importlib.import_module", return_value=fake_module):
            reg.discover()

        assert reg.has_preset("ext-preset")
        assert reg.has_theme("ext-ds")

    def test_discover_bad_theme_package_logged(self, settings):
        """A failing theme package import is logged but doesn't crash discover()."""
        settings.DJUST_THEMES = ["nonexistent_package"]

        reg = get_registry()
        with patch("importlib.import_module", side_effect=ImportError("nope")):
            reg.discover()  # should not raise

        # Built-ins should still be loaded
        assert reg.has_preset("default")

    def test_discover_no_base_dir(self, settings):
        """If BASE_DIR is not set, themes_dir discovery is skipped gracefully."""
        if hasattr(settings, "BASE_DIR"):
            delattr(settings, "BASE_DIR")
        reg = get_registry()
        reg.discover()
        # Should still have builtins
        assert reg.has_preset("default")


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_register_and_get(self):
        """Concurrent register/get calls don't raise."""
        reg = get_registry()
        errors = []

        def writer(n):
            try:
                for i in range(100):
                    reg.register_preset(f"t{n}-{i}", i)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    reg.list_presets()
                    reg.has_preset("default")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []
