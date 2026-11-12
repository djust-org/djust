"""Tests for #1624 / #4003 — theme_head and djust-components's components.css.

#1624 made `{% theme_head %}` link `static/djust_components/components.css`
whenever `djust.components` was installed. #4003 (2.0) made that link opt-in:
`LIVEVIEW_CONFIG["theme"]["component_catalog_css"] = True`, or
`{% theme_head component_catalog=True %}` on one page. The app must still be
installed for the link to appear.
"""

from unittest.mock import patch

import pytest
from django.test import override_settings

_OPT_IN = {"theme": {"component_catalog_css": True}}


def _render_theme_head(extra_context=None):
    """Render the theme_head template directly with explicit context."""
    from django.template.loader import render_to_string

    ctx = {
        "loading_class": False,
        "css_block": "<style>/*test*/</style>",
        "deferred_css_block": "",
        "component_css_block": "",
        "include_component_link": True,
        "include_components_app_link": False,
        "include_js": False,
        "direction": "ltr",
        "cookie_prefix_js": '""',
    }
    if extra_context:
        ctx.update(extra_context)
    return render_to_string("djust_theming/theme_head.html", ctx)


class TestThemeHeadComponentsAppLink1624:
    """#1624: theme_head emits djust-components/components.css link when the
    `include_components_app_link` context var is True."""

    def test_link_emitted_when_flag_true(self):
        """include_components_app_link=True → render contains the djust_components link."""
        html = _render_theme_head({"include_components_app_link": True})
        assert "djust_components/components.css" in html, (
            "Expected djust_components/components.css link in theme_head; got: %s" % html
        )

    def test_link_not_emitted_when_flag_false(self):
        """include_components_app_link=False → render does NOT contain the link."""
        html = _render_theme_head({"include_components_app_link": False})
        assert "djust_components/components.css" not in html, (
            "Expected NO djust_components link when flag is False; got: %s" % html
        )

    def test_existing_theming_link_still_emitted(self):
        """Regression: djust-theming's components.css link is still emitted."""
        html = _render_theme_head({"include_component_link": True})
        assert "djust_theming/css/components.css" in html


class TestBuildThemeHeadContextDetection1624:
    """#1624: build_theme_head_context auto-detects djust.components."""

    @override_settings(LIVEVIEW_CONFIG=_OPT_IN)
    def test_detects_djust_components_installed(self):
        """Opted in and djust.components IS in INSTALLED_APPS, flag is True."""
        from django.test import RequestFactory
        from djust.theming.templatetags.theme_tags import build_theme_head_context

        request = RequestFactory().get("/")
        # Patch is_installed to return True for djust.components specifically.
        # The test settings may or may not have djust.components installed; we
        # pin the detection logic by patching, not by relying on settings.
        with patch(
            "django.apps.apps.is_installed",
            side_effect=lambda name: name == "djust.components",
        ):
            ctx = build_theme_head_context(request)
        assert ctx.get("include_components_app_link") is True, (
            "Expected include_components_app_link=True when is_installed returns True; got: %r"
            % ctx.get("include_components_app_link")
        )

    @override_settings(LIVEVIEW_CONFIG=_OPT_IN)
    def test_returns_false_when_app_not_installed(self):
        """When djust.components is NOT installed, flag is False."""
        from django.test import RequestFactory
        from djust.theming.templatetags.theme_tags import build_theme_head_context

        request = RequestFactory().get("/")
        with patch(
            "django.apps.apps.is_installed",
            side_effect=lambda name: name != "djust.components",
        ):
            ctx = build_theme_head_context(request)
        assert ctx.get("include_components_app_link") is False

    @override_settings(LIVEVIEW_CONFIG=_OPT_IN)
    def test_defensive_when_apps_raises(self):
        """If apps.is_installed raises (e.g. unconfigured Django), flag is False."""
        from django.test import RequestFactory
        from djust.theming.templatetags.theme_tags import build_theme_head_context

        request = RequestFactory().get("/")
        with patch(
            "django.apps.apps.is_installed",
            side_effect=Exception("not ready"),
        ):
            ctx = build_theme_head_context(request)
        assert ctx.get("include_components_app_link") is False


class TestCatalogStylesheetIsOptIn4003:
    """#4003: installed is no longer enough; the project or the page opts in."""

    @staticmethod
    def _ctx(**kwargs):
        from django.test import RequestFactory
        from djust.theming.templatetags.theme_tags import build_theme_head_context

        with patch(
            "django.apps.apps.is_installed",
            side_effect=lambda name: name == "djust.components",
        ):
            return build_theme_head_context(RequestFactory().get("/"), **kwargs)

    @override_settings(LIVEVIEW_CONFIG={})
    def test_installed_but_not_opted_in_is_not_linked(self):
        assert self._ctx()["include_components_app_link"] is False

    @override_settings(LIVEVIEW_CONFIG={"theme": {"component_catalog_css": False}})
    def test_stated_false_is_not_linked(self):
        assert self._ctx()["include_components_app_link"] is False

    @override_settings(LIVEVIEW_CONFIG={})
    def test_the_page_can_opt_in(self):
        assert self._ctx(component_catalog=True)["include_components_app_link"] is True

    @override_settings(LIVEVIEW_CONFIG=_OPT_IN)
    def test_the_page_can_opt_out_of_the_project_setting(self):
        assert self._ctx(component_catalog=False)["include_components_app_link"] is False

    @pytest.mark.parametrize(
        "config, source, linked",
        [
            ({}, "{% load theme_tags %}{% theme_head %}", False),
            (_OPT_IN, "{% load theme_tags %}{% theme_head %}", True),
            ({}, "{% load theme_tags %}{% theme_head component_catalog=True %}", True),
        ],
    )
    def test_through_the_tag(self, config, source, linked):
        """The real tag, through a Django template, with djust.components installed."""
        from django.apps import apps
        from django.template import engines
        from django.test import RequestFactory

        if not apps.is_installed("djust.components"):
            pytest.skip("djust.components is not installed in the test settings")
        engine = next(e for e in engines.all() if e.__class__.__name__ == "DjangoTemplates")
        with override_settings(LIVEVIEW_CONFIG=config):
            html = engine.from_string(source).render({}, request=RequestFactory().get("/"))
        assert ("djust_components/components.css" in html) is linked, html
        # The theme pack's own component CSS is linked either way.
        assert "djust_theming/css/components.css" in html


class TestW004:
    """djust_theming.W004: djust.components installed without the setting stated."""

    @staticmethod
    def _run(installed: bool):
        from djust.theming.checks import check_component_catalog_css

        with patch(
            "django.apps.apps.is_installed",
            side_effect=lambda name: installed and name == "djust.components",
        ):
            return check_component_catalog_css(None)

    @override_settings(LIVEVIEW_CONFIG={})
    def test_warns_when_installed_and_unstated(self):
        msgs = self._run(installed=True)
        assert [m.id for m in msgs] == ["djust_theming.W004"]
        assert "component_catalog_css" in msgs[0].hint

    @pytest.mark.parametrize("value", [True, False])
    def test_stating_either_value_silences_it(self, value):
        with override_settings(LIVEVIEW_CONFIG={"theme": {"component_catalog_css": value}}):
            assert self._run(installed=True) == []

    @override_settings(LIVEVIEW_CONFIG={})
    def test_silent_when_the_app_is_not_installed(self):
        assert self._run(installed=False) == []


def _djust_engine():
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


def _django_engine():
    from django.template import engines

    return engines["django"]


@pytest.mark.parametrize("engine", [_django_engine, _djust_engine], ids=["django", "rust"])
@pytest.mark.parametrize(
    "config, arg, linked",
    [
        ({}, "", False),
        ({"theme": {"component_catalog_css": True}}, "", True),
        ({"theme": {"component_catalog_css": False}}, "", False),
        # Only True opts in: a truthy string is not an opt-in.
        ({"theme": {"component_catalog_css": "yes"}}, "", False),
        ({}, " component_catalog=True", True),
        ({"theme": {"component_catalog_css": True}}, " component_catalog=False", False),
    ],
)
def test_both_engines_agree_4003(engine, config, arg, linked):
    """The Rust engine reaches theme_head through theming/rust_handlers.py; the
    opt-in, the page override and the True-only rule hold there too."""
    from django.apps import apps
    from django.test import RequestFactory

    from djust.theming.context_processors import clear_theme_context_cache

    if not apps.is_installed("djust.components"):
        pytest.skip("djust.components is not installed in the test settings")
    clear_theme_context_cache()
    with override_settings(LIVEVIEW_CONFIG=config):
        html = (
            engine()
            .from_string("{% load theme_tags %}{% theme_head" + arg + " %}")
            .render({}, request=RequestFactory().get("/"))
        )
    assert ("djust_components/components.css" in html) is linked


@pytest.mark.parametrize(
    "config, linked",
    [
        ({}, False),
        ({"theme": {"component_catalog_css": True}}, True),
        ({"theme": {"component_catalog_css": False}}, False),
    ],
)
def test_the_context_variable_follows_the_project_setting_4003(config, linked):
    """``{{ theme_head }}`` from the context processor takes no arguments, so it
    follows LIVEVIEW_CONFIG["theme"]["component_catalog_css"]."""
    from django.apps import apps
    from django.test import RequestFactory

    from djust.theming.context_processors import clear_theme_context_cache, theme_context

    if not apps.is_installed("djust.components"):
        pytest.skip("djust.components is not installed in the test settings")
    clear_theme_context_cache()
    with override_settings(LIVEVIEW_CONFIG=config):
        html = str(theme_context(RequestFactory().get("/"))["theme_head"])
    clear_theme_context_cache()
    assert ("djust_components/components.css" in html) is linked
