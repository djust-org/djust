"""Tests for #1624 — theme_head loads djust-components's components.css.

When `djust.components` is in INSTALLED_APPS, `{% theme_head %}` should
auto-link `static/djust_components/components.css` so layout rules for
`{% code_block %}`, `{% card %}`, etc. apply without manual `<link>`
additions in user templates.
"""

from unittest.mock import patch


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

    def test_detects_djust_components_installed(self):
        """When djust.components IS in INSTALLED_APPS, flag is True."""
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
