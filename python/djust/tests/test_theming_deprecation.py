"""Tests for deprecation warnings on themes.py legacy API."""

import pytest

pytestmark = pytest.mark.theming


class TestDeprecatedThemesDict:
    """Verify _DeprecatedThemesDict emits DeprecationWarning on all access paths."""

    def test_getitem_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            THEMES["material"]

    def test_contains_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            "material" in THEMES

    def test_get_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            THEMES.get("material")

    def test_items_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            THEMES.items()

    def test_keys_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            THEMES.keys()

    def test_values_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            THEMES.values()

    def test_iter_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            list(THEMES)

    def test_len_warns(self):
        from djust.theming.themes import THEMES

        with pytest.warns(DeprecationWarning, match="THEMES"):
            len(THEMES)

    def test_get_theme_warns(self):
        from djust.theming.themes import get_theme

        with pytest.warns(DeprecationWarning, match="get_theme"):
            get_theme("material")

    def test_list_themes_warns(self):
        from djust.theming.themes import list_themes

        with pytest.warns(DeprecationWarning, match="list_themes"):
            list_themes()
