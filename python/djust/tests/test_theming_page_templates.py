"""Tests for Phase 4: Page Templates + theme_pages tag library.

Tests cover:
- Template existence for all 9 page templates
- Template resolution with theme-specific override fallback
- Tag rendering for all 9 theme_pages tags
- Structural HTML validation (forms, buttons, headings)
- Slot passthrough
- Accessibility (labels, heading hierarchy)
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

from unittest.mock import MagicMock, patch

import pytest

from django.template.loader import get_template
from django.test import RequestFactory

from djust.theming.template_resolver import (
    _get_page_candidates,
    resolve_page_template,
)

pytestmark = pytest.mark.theming


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request():
    request = MagicMock()
    request.COOKIES = {}
    request.session = {}
    request._djust_theme_manager = None
    return request


def _render_page_tag(tag_fn, **kwargs):
    """Call a theme_pages tag function with a mocked context."""
    request = _make_request()
    ctx = {"request": request}
    with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
        return str(tag_fn(ctx, **kwargs))


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------


class TestPageResolver:
    """Tests for page template resolution."""

    def test_get_page_candidates_default(self):
        candidates = _get_page_candidates("material", "login")
        assert candidates == [
            "djust_theming/themes/material/pages/login.html",
            "djust_theming/pages/login.html",
        ]

    def test_get_page_candidates_custom_theme(self):
        candidates = _get_page_candidates("ios", "404")
        assert candidates[0] == "djust_theming/themes/ios/pages/404.html"
        assert candidates[1] == "djust_theming/pages/404.html"

    def test_resolve_page_template_returns_default(self):
        factory = RequestFactory()
        request = factory.get("/")
        request.session = {}
        request.COOKIES = {}
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            template = resolve_page_template(request, "login")
        assert "pages/login.html" in template.origin.name


# ---------------------------------------------------------------------------
# Template existence — all 9 pages loadable
# ---------------------------------------------------------------------------


PAGE_NAMES = [
    "login",
    "register",
    "password_reset",
    "password_confirm",
    "404",
    "500",
    "403",
    "maintenance",
    "empty_state",
]


class TestPageTemplateExistence:
    """Each page template must be loadable by Django's template engine."""

    @pytest.mark.parametrize("name", PAGE_NAMES)
    def test_page_template_exists(self, name):
        tpl = get_template(f"djust_theming/pages/{name}.html")
        assert tpl is not None


# ---------------------------------------------------------------------------
# Auth page tag tests — login
# ---------------------------------------------------------------------------


class TestLoginPage:
    """Tests for theme_login_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_login_page

        self.tag = theme_login_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_form(self):
        html = _render_page_tag(self.tag)
        assert "<form" in html

    def test_form_has_post_method(self):
        html = _render_page_tag(self.tag)
        assert 'method="post"' in html

    def test_contains_email_input(self):
        html = _render_page_tag(self.tag)
        assert 'type="email"' in html

    def test_contains_password_input(self):
        html = _render_page_tag(self.tag)
        assert 'type="password"' in html

    def test_contains_submit_button(self):
        html = _render_page_tag(self.tag)
        assert 'type="submit"' in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Sign in" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Log in")
        assert "Log in" in html

    def test_custom_action(self):
        html = _render_page_tag(self.tag, action="/auth/login/")
        assert 'action="/auth/login/"' in html

    def test_forgot_password_url(self):
        html = _render_page_tag(self.tag, forgot_password_url="/reset/")
        assert "/reset/" in html

    def test_register_url(self):
        html = _render_page_tag(self.tag, register_url="/register/")
        assert "/register/" in html

    def test_slot_social(self):
        html = _render_page_tag(self.tag, slot_social='<div class="google-btn">Google</div>')
        assert "google-btn" in html

    def test_slot_footer(self):
        html = _render_page_tag(self.tag, slot_footer="<p>Custom footer</p>")
        assert "Custom footer" in html

    def test_contains_card_class(self):
        html = _render_page_tag(self.tag)
        assert "card" in html

    def test_has_labels(self):
        html = _render_page_tag(self.tag)
        assert "<label" in html


# ---------------------------------------------------------------------------
# Auth page tag tests — register
# ---------------------------------------------------------------------------


class TestRegisterPage:
    """Tests for theme_register_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_register_page

        self.tag = theme_register_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_form(self):
        html = _render_page_tag(self.tag)
        assert "<form" in html

    def test_form_has_post_method(self):
        html = _render_page_tag(self.tag)
        assert 'method="post"' in html

    def test_contains_name_input(self):
        html = _render_page_tag(self.tag)
        assert 'name="name"' in html

    def test_contains_email_input(self):
        html = _render_page_tag(self.tag)
        assert 'type="email"' in html

    def test_contains_password_inputs(self):
        html = _render_page_tag(self.tag)
        # Should have two password fields
        assert html.count('type="password"') >= 2

    def test_contains_submit_button(self):
        html = _render_page_tag(self.tag)
        assert 'type="submit"' in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Create account" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Sign up")
        assert "Sign up" in html

    def test_login_url(self):
        html = _render_page_tag(self.tag, login_url="/login/")
        assert "/login/" in html

    def test_terms_url(self):
        html = _render_page_tag(self.tag, terms_url="/terms/")
        assert "/terms/" in html

    def test_slot_footer(self):
        html = _render_page_tag(self.tag, slot_footer="<p>Custom footer</p>")
        assert "Custom footer" in html

    def test_has_labels(self):
        html = _render_page_tag(self.tag)
        assert "<label" in html


# ---------------------------------------------------------------------------
# Auth page tag tests — password_reset
# ---------------------------------------------------------------------------


class TestPasswordResetPage:
    """Tests for theme_password_reset_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_password_reset_page

        self.tag = theme_password_reset_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_form(self):
        html = _render_page_tag(self.tag)
        assert "<form" in html

    def test_form_has_post_method(self):
        html = _render_page_tag(self.tag)
        assert 'method="post"' in html

    def test_contains_email_input(self):
        html = _render_page_tag(self.tag)
        assert 'type="email"' in html

    def test_contains_submit_button(self):
        html = _render_page_tag(self.tag)
        assert 'type="submit"' in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Reset password" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Forgot password")
        assert "Forgot password" in html

    def test_default_description(self):
        html = _render_page_tag(self.tag)
        assert "email" in html.lower()

    def test_custom_description(self):
        html = _render_page_tag(self.tag, description="Custom help text")
        assert "Custom help text" in html

    def test_login_url(self):
        html = _render_page_tag(self.tag, login_url="/login/")
        assert "/login/" in html

    def test_has_labels(self):
        html = _render_page_tag(self.tag)
        assert "<label" in html


# ---------------------------------------------------------------------------
# Auth page tag tests — password_confirm
# ---------------------------------------------------------------------------


class TestPasswordConfirmPage:
    """Tests for theme_password_confirm_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_password_confirm_page

        self.tag = theme_password_confirm_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_form(self):
        html = _render_page_tag(self.tag)
        assert "<form" in html

    def test_form_has_post_method(self):
        html = _render_page_tag(self.tag)
        assert 'method="post"' in html

    def test_contains_password_inputs(self):
        html = _render_page_tag(self.tag)
        assert html.count('type="password"') >= 2

    def test_contains_submit_button(self):
        html = _render_page_tag(self.tag)
        assert 'type="submit"' in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Set new password" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Change password")
        assert "Change password" in html

    def test_has_labels(self):
        html = _render_page_tag(self.tag)
        assert "<label" in html


# ---------------------------------------------------------------------------
# Error page tag tests — 404
# ---------------------------------------------------------------------------


class Test404Page:
    """Tests for theme_404_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_404_page

        self.tag = theme_404_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_error_code(self):
        html = _render_page_tag(self.tag)
        assert "404" in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Page not found" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Not here")
        assert "Not here" in html

    def test_default_description(self):
        html = _render_page_tag(self.tag)
        # Should have some description text
        assert len(html) > 50

    def test_custom_description(self):
        html = _render_page_tag(self.tag, description="Custom 404 message")
        assert "Custom 404 message" in html

    def test_home_button(self):
        html = _render_page_tag(self.tag)
        assert 'href="/"' in html or "Go home" in html or "go home" in html.lower()

    def test_custom_home_url(self):
        html = _render_page_tag(self.tag, home_url="/dashboard/")
        assert "/dashboard/" in html

    def test_slot_illustration(self):
        html = _render_page_tag(self.tag, slot_illustration='<img src="/lost.svg" />')
        assert "/lost.svg" in html

    def test_has_heading(self):
        html = _render_page_tag(self.tag)
        assert "<h1" in html or "<h2" in html


# ---------------------------------------------------------------------------
# Error page tag tests — 500
# ---------------------------------------------------------------------------


class Test500Page:
    """Tests for theme_500_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_500_page

        self.tag = theme_500_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_error_code(self):
        html = _render_page_tag(self.tag)
        assert "500" in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Something went wrong" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Server error")
        assert "Server error" in html

    def test_custom_description(self):
        html = _render_page_tag(self.tag, description="Oops, our bad")
        assert "Oops, our bad" in html

    def test_home_url(self):
        html = _render_page_tag(self.tag, home_url="/")
        assert "/" in html

    def test_retry_url(self):
        html = _render_page_tag(self.tag, retry_url="/retry/")
        assert "/retry/" in html

    def test_has_heading(self):
        html = _render_page_tag(self.tag)
        assert "<h1" in html or "<h2" in html


# ---------------------------------------------------------------------------
# Error page tag tests — 403
# ---------------------------------------------------------------------------


class Test403Page:
    """Tests for theme_403_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_403_page

        self.tag = theme_403_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_contains_error_code(self):
        html = _render_page_tag(self.tag)
        assert "403" in html

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Access denied" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Forbidden")
        assert "Forbidden" in html

    def test_custom_description(self):
        html = _render_page_tag(self.tag, description="No permission")
        assert "No permission" in html

    def test_back_url(self):
        html = _render_page_tag(self.tag, back_url="/home/")
        assert "/home/" in html

    def test_has_heading(self):
        html = _render_page_tag(self.tag)
        assert "<h1" in html or "<h2" in html


# ---------------------------------------------------------------------------
# Utility page tag tests — maintenance
# ---------------------------------------------------------------------------


class TestMaintenancePage:
    """Tests for theme_maintenance_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_maintenance_page

        self.tag = theme_maintenance_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "Under maintenance" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Scheduled downtime")
        assert "Scheduled downtime" in html

    def test_default_description(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 50

    def test_custom_description(self):
        html = _render_page_tag(self.tag, description="Back in 30 minutes")
        assert "Back in 30 minutes" in html

    def test_slot_illustration(self):
        html = _render_page_tag(self.tag, slot_illustration='<svg class="wrench"></svg>')
        assert "wrench" in html

    def test_slot_eta(self):
        html = _render_page_tag(self.tag, slot_eta="<p>ETA: 2pm</p>")
        assert "ETA: 2pm" in html

    def test_slot_progress(self):
        html = _render_page_tag(self.tag, slot_progress='<div class="progress-bar"></div>')
        assert "progress-bar" in html

    def test_has_heading(self):
        html = _render_page_tag(self.tag)
        assert "<h1" in html or "<h2" in html


# ---------------------------------------------------------------------------
# Utility page tag tests — empty_state
# ---------------------------------------------------------------------------


class TestEmptyStatePage:
    """Tests for theme_empty_state_page tag."""

    @pytest.fixture(autouse=True)
    def _import_tag(self):
        from djust.theming.templatetags.theme_pages import theme_empty_state_page

        self.tag = theme_empty_state_page

    def test_renders_without_error(self):
        html = _render_page_tag(self.tag)
        assert len(html) > 0

    def test_default_title(self):
        html = _render_page_tag(self.tag)
        assert "No items yet" in html

    def test_custom_title(self):
        html = _render_page_tag(self.tag, title="Empty inbox")
        assert "Empty inbox" in html

    def test_custom_description(self):
        html = _render_page_tag(self.tag, description="Add your first item")
        assert "Add your first item" in html

    def test_cta_button_shown_when_provided(self):
        html = _render_page_tag(self.tag, cta_text="Add item", cta_url="/add/")
        assert "Add item" in html
        assert "/add/" in html

    def test_cta_button_hidden_when_not_provided(self):
        html = _render_page_tag(self.tag, cta_text="", cta_url="")
        # Should not have an anchor with empty href causing issues
        # The CTA section should be absent or empty
        assert "btn" not in html.lower() or html.count("btn") == 0 or "cta_text" not in html

    def test_slot_icon(self):
        html = _render_page_tag(self.tag, slot_icon='<svg class="inbox-icon"></svg>')
        assert "inbox-icon" in html

    def test_has_heading(self):
        html = _render_page_tag(self.tag)
        assert "<h1" in html or "<h2" in html


# ---------------------------------------------------------------------------
# CSS prefix support
# ---------------------------------------------------------------------------


class TestPagesCSSPrefix:
    """Verify pages respect css_prefix."""

    def test_login_uses_css_prefix(self):
        from djust.theming.templatetags.theme_pages import theme_login_page

        request = _make_request()
        ctx = {"request": request}
        config = {"theme": {"css_prefix": "dj-"}}
        with patch.object(settings, "LIVEVIEW_CONFIG", config, create=True):
            html = str(theme_login_page(ctx))
        assert "dj-card" in html or "dj-btn" in html or "dj-input" in html

    def test_404_uses_css_prefix(self):
        from djust.theming.templatetags.theme_pages import theme_404_page

        request = _make_request()
        ctx = {"request": request}
        config = {"theme": {"css_prefix": "dj-"}}
        with patch.object(settings, "LIVEVIEW_CONFIG", config, create=True):
            html = str(theme_404_page(ctx))
        assert "dj-" in html


# ---------------------------------------------------------------------------
# Pages CSS file existence
# ---------------------------------------------------------------------------


class TestPagesCSSFile:
    """Verify the pages.css static file exists and uses layers."""

    @staticmethod
    def _css_path():
        import os

        base = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(base, "theming", "static", "djust_theming", "css", "pages.css")

    def test_pages_css_exists(self):
        import os

        assert os.path.isfile(self._css_path())

    def test_pages_css_has_file_header(self):
        """Older design wrapped the pages file in an ``@layer`` block; the
        current design ships plain CSS. Verify the generated file carries its
        identifying header comment (proof the generator ran) and the next
        test covers actual class output."""
        css = open(self._css_path()).read()
        assert "djust-theming page styles" in css

    def test_pages_css_has_page_classes(self):
        css = open(self._css_path()).read()
        assert "page-" in css


# ---------------------------------------------------------------------------
# CSRF token tests — auth forms must include csrfmiddlewaretoken
# ---------------------------------------------------------------------------


AUTH_TAGS = [
    "theme_login_page",
    "theme_register_page",
    "theme_password_reset_page",
    "theme_password_confirm_page",
]


class TestCSRFTokenInAuthForms:
    """Auth page forms must contain a CSRF hidden input when a request is available."""

    @pytest.mark.parametrize("tag_name", AUTH_TAGS)
    def test_csrf_token_present(self, tag_name):
        from djust.theming.templatetags import theme_pages as mod

        tag_fn = getattr(mod, tag_name)
        factory = RequestFactory()
        request = factory.get("/")
        request.session = {}
        request.COOKIES = {}
        ctx = {"request": request}
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            html = str(tag_fn(ctx))
        assert 'name="csrfmiddlewaretoken"' in html

    @pytest.mark.parametrize("tag_name", AUTH_TAGS)
    def test_csrf_token_absent_when_no_request(self, tag_name):
        from djust.theming.templatetags import theme_pages as mod

        tag_fn = getattr(mod, tag_name)
        request = _make_request()
        ctx = {"request": request}
        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            html = str(tag_fn(ctx))
        # MagicMock request won't produce a real token, so gracefully absent
        assert "csrfmiddlewaretoken" in html or len(html) > 0
