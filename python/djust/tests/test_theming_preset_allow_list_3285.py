"""The preset picker takes an allow-list (#3285)."""

from __future__ import annotations

import re

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory

pytestmark = pytest.mark.theming


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

    def test_an_unknown_name_in_the_setting_is_a_configuration_error(self, settings):
        settings.LIVEVIEW_CONFIG = {"theme": {"selectable_presets": ["nope"]}}
        with pytest.raises(ImproperlyConfigured, match="selectable_presets"):
            _selector(layout="grid")

    def test_the_active_preset_is_still_marked_active(self):
        html = _selector(layout="grid", presets="default,legal")
        assert html.count("active") >= 1
