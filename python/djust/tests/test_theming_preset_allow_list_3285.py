"""The preset picker takes an allow-list (#3285)."""

from __future__ import annotations

import re

import pytest
from django.test import RequestFactory

pytestmark = pytest.mark.theming


@pytest.fixture(autouse=True)
def _fresh_warning_state():
    from djust.theming import manager
    from djust.theming.registry import get_registry

    # Other theming tests ``ThemeRegistry._reset()`` and leave an undiscovered, empty
    # registry behind; whether this file then sees the built-in presets depended
    # on which tests shared its xdist worker.
    get_registry().discover()
    manager._WARNED_SELECTABLE.clear()
    yield
    manager._WARNED_SELECTABLE.clear()


def _request(nonce=None):
    request = RequestFactory().get("/")
    request.session = {}
    if nonce is not None:
        request.csp_nonce = nonce
    return request


def _selector(request=None, **kwargs) -> str:
    from djust.theming.templatetags.theme_tags import theme_preset_selector

    return str(theme_preset_selector({"request": request or _request()}, **kwargs))


def _names(html: str) -> list[str]:
    return re.findall(r'data-theme-preset="([^"]+)"', html)


class TestPresetAllowList:
    def test_every_preset_is_listed_by_default(self):
        assert len(_names(_selector(layout="grid"))) > 20

    def test_a_comma_separated_list_limits_it_in_the_order_given(self):
        html = _selector(layout="grid", presets="legal,medical,default")
        assert _names(html) == ["legal", "medical", "default"]

    def test_a_list_and_spaces_work(self):
        assert _names(_selector(layout="grid", presets=["default", " legal "])) == [
            "default",
            "legal",
        ]

    def test_the_limit_applies_to_every_layout(self):
        for layout in ("dropdown", "grid", "list"):
            html = _selector(layout=layout, presets="legal,default")
            assert "medical" not in html and "legal" in html, layout

    def test_an_unknown_name_raises_and_names_it(self):
        with pytest.raises(ValueError, match="'legall'"):
            _selector(layout="grid", presets="legal,legall")

    def test_an_empty_string_means_no_limit(self):
        assert len(_names(_selector(layout="grid", presets=""))) > 20

    def test_the_setting_limits_it(self, settings):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["legal", "default"]}}
        assert _names(_selector(layout="grid")) == ["legal", "default"]

    def test_the_setting_accepts_a_comma_separated_string(self, settings):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": "default,legal"}}
        assert _names(_selector(layout="grid")) == ["default", "legal"]

    def test_the_tag_argument_wins_over_the_setting(self, settings):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["legal", "default"]}}
        assert _names(_selector(layout="grid", presets="medical")) == ["medical"]

    def test_an_empty_tag_argument_means_no_limit_even_with_a_setting(self, settings):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["legal"]}}
        assert len(_names(_selector(layout="grid", presets=""))) > 20

    def test_a_non_iterable_tag_argument_is_a_clear_error(self):
        with pytest.raises(ValueError, match="comma-separated string or a list"):
            _selector(layout="grid", presets=5)


class TestABadSettingNeverTakesTheSiteDown:
    """The setting is read on every request, and a preset can be removed in a
    later release, so an unusable value is logged and ignored, never raised."""

    def test_unknown_names_are_dropped_with_a_warning(self, settings, caplog):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["legal", "nope"]}}
        with caplog.at_level("WARNING", logger="djust.theming.manager"):
            assert _names(_selector(layout="grid")) == ["legal"]
        assert "'nope'" in caplog.text and "selectable_presets" in caplog.text

    def test_nothing_usable_falls_back_to_every_preset(self, settings, caplog):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["nope"]}}
        with caplog.at_level("WARNING", logger="djust.theming.manager"):
            assert len(_names(_selector(layout="grid"))) > 20
        assert "'nope'" in caplog.text

    def test_a_non_iterable_setting_falls_back_with_a_warning(self, settings, caplog):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": 5}}
        with caplog.at_level("WARNING", logger="djust.theming.manager"):
            assert len(_names(_selector(layout="grid"))) > 20
        assert "comma-separated string or a list" in caplog.text

    def test_it_warns_once_not_per_request(self, settings, caplog):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["nope"]}}
        with caplog.at_level("WARNING", logger="djust.theming.manager"):
            for _ in range(3):
                _selector(layout="grid")
        assert caplog.text.count("selectable_presets") == 1

    def test_the_context_processor_does_not_raise(self, settings):
        """context_processors.py calls get_available_presets() unguarded, on every
        request and every WebSocket event."""
        from djust.theming.context_processors import theme_context

        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["nope"]}}
        ctx = theme_context(_request())
        assert len(ctx["theme_presets"]) > 20

    def test_the_theme_mixin_mount_does_not_raise(self, settings):
        from djust.theming.mixins import ThemeMixin

        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["nope"]}}
        view = ThemeMixin()
        view.mount(_request())
        assert view.theme_head


class TestSelectablePresetsSystemCheck:
    def _run(self, settings, value):
        from djust.theming.checks import check_selectable_presets

        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": value}}
        return check_selectable_presets(app_configs=None)

    def test_a_good_setting_is_clean(self, settings):
        assert self._run(settings, ["legal", "default"]) == []
        assert self._run(settings, None) == []

    def test_an_unknown_name_is_reported_with_a_hint(self, settings):
        (warning,) = self._run(settings, ["legal", "nope"])
        assert warning.id == "djust_theming.W003"
        assert "'nope'" in warning.msg and "list-presets" in warning.hint

    def test_a_non_iterable_value_is_reported(self, settings):
        (warning,) = self._run(settings, 5)
        assert warning.id == "djust_theming.W003"
        assert "comma-separated string or a list" in warning.msg

    def test_the_active_preset_is_still_marked_active(self):
        html = _selector(layout="grid", presets="default,legal")
        assert html.count("active") >= 1
