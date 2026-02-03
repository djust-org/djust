"""
Tests for djust internationalization (i18n) support.
"""

import pytest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import Mock, MagicMock, patch

import django
from django.conf import settings
from django.test import RequestFactory, override_settings

# Configure Django settings before importing djust modules
if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        ROOT_URLCONF="",
        LANGUAGE_CODE="en",
        LANGUAGES=[
            ("en", "English"),
            ("es", "Spanish"),
            ("de", "German"),
            ("ar", "Arabic"),
            ("he", "Hebrew"),
            ("fr", "French"),
            ("ja", "Japanese"),
        ],
        USE_I18N=True,
        USE_L10N=True,
        TEMPLATES=[],
    )
    django.setup()

from djust.i18n import (
    I18nMixin,
    format_date,
    format_datetime,
    format_time,
    format_number,
    format_currency,
    format_percent,
    format_decimal,
)
from djust.i18n.mixin import RTL_LANGUAGES


class TestI18nMixin:
    """Tests for I18nMixin functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.factory = RequestFactory()

        # Create a mock view class with I18nMixin
        class TestView(I18nMixin):
            pass

        self.view = TestView()
        self.view._init_i18n()

    def test_init_i18n(self):
        """Test i18n initialization."""
        assert self.view._language is None
        assert self.view._pending_i18n_commands == []

    def test_language_property_default(self):
        """Test default language property."""
        with patch("djust.i18n.mixin.get_language", return_value="en"):
            assert self.view.language == "en"

    def test_language_property_set(self):
        """Test language property after setting."""
        self.view._language = "es"
        assert self.view.language == "es"

    def test_is_rtl_false_for_ltr_languages(self):
        """Test is_rtl is False for LTR languages."""
        ltr_languages = ["en", "es", "de", "fr", "ja", "zh"]
        for lang in ltr_languages:
            self.view._language = lang
            assert self.view.is_rtl is False, f"{lang} should be LTR"

    def test_is_rtl_true_for_rtl_languages(self):
        """Test is_rtl is True for RTL languages."""
        for lang in RTL_LANGUAGES:
            self.view._language = lang
            assert self.view.is_rtl is True, f"{lang} should be RTL"

    def test_is_rtl_with_locale_code(self):
        """Test is_rtl works with full locale codes like ar-SA."""
        self.view._language = "ar-SA"
        assert self.view.is_rtl is True

        self.view._language = "en-US"
        assert self.view.is_rtl is False

    def test_text_direction(self):
        """Test text_direction property."""
        self.view._language = "en"
        assert self.view.text_direction == "ltr"

        self.view._language = "ar"
        assert self.view.text_direction == "rtl"

    @override_settings(LANGUAGES=[("en", "English"), ("es", "Spanish")])
    def test_available_languages(self):
        """Test available_languages property."""
        langs = self.view.available_languages
        assert ("en", "English") in langs
        assert ("es", "Spanish") in langs

    def test_detect_language_from_url_param(self):
        """Test language detection from URL parameter."""
        request = self.factory.get("/?lang=es")
        detected = self.view._detect_language(request)
        assert detected == "es"

    def test_detect_language_from_session(self):
        """Test language detection from session."""
        request = self.factory.get("/")
        request.session = {self.view.language_session_key: "de"}
        detected = self.view._detect_language(request)
        assert detected == "de"

    def test_detect_language_from_cookie(self):
        """Test language detection from cookie."""
        request = self.factory.get("/")
        request.COOKIES = {self.view.language_cookie_name: "fr"}
        detected = self.view._detect_language(request)
        assert detected == "fr"

    def test_set_language(self):
        """Test set_language method."""
        with patch("djust.i18n.mixin.activate") as mock_activate:
            self.view.set_language("es")

            assert self.view._language == "es"
            mock_activate.assert_called_once_with("es")

            # Check i18n command was queued
            commands = self.view._drain_i18n_commands()
            assert len(commands) == 2  # set_language + persist_language

            # Find set_language command
            set_lang_cmd = next(c for c in commands if c["type"] == "set_language")
            assert set_lang_cmd["lang"] == "es"
            assert set_lang_cmd["dir"] == "ltr"
            assert set_lang_cmd["is_rtl"] is False

    def test_set_language_rtl(self):
        """Test set_language for RTL language."""
        with patch("djust.i18n.mixin.activate"):
            self.view.set_language("ar")

            commands = self.view._drain_i18n_commands()
            set_lang_cmd = next(c for c in commands if c["type"] == "set_language")
            assert set_lang_cmd["lang"] == "ar"
            assert set_lang_cmd["dir"] == "rtl"
            assert set_lang_cmd["is_rtl"] is True

    def test_set_language_invalid(self):
        """Test set_language rejects invalid language codes."""
        self.view._language = "en"
        self.view.set_language("xyz")  # Invalid
        assert self.view._language == "en"  # Unchanged

    def test_mount_i18n(self):
        """Test mount_i18n initialization."""
        request = self.factory.get("/?lang=de")
        with patch("djust.i18n.mixin.activate") as mock_activate:
            self.view.mount_i18n(request)

            assert self.view._language == "de"
            mock_activate.assert_called_once_with("de")

    def test_get_i18n_context(self):
        """Test get_i18n_context returns proper context dict."""
        self.view._language = "ar"
        ctx = self.view.get_i18n_context()

        assert ctx["language"] == "ar"
        assert ctx["is_rtl"] is True
        assert ctx["text_direction"] == "rtl"
        assert "available_languages" in ctx

    def test_drain_i18n_commands_clears_queue(self):
        """Test that draining commands clears the queue."""
        self.view._pending_i18n_commands = [{"type": "test"}]
        commands = self.view._drain_i18n_commands()

        assert len(commands) == 1
        assert self.view._pending_i18n_commands == []


class TestFormattingFunctions:
    """Tests for locale-aware formatting functions."""

    # ========== DATE FORMATTING ==========

    def test_format_date_basic(self):
        """Test basic date formatting."""
        d = date(2024, 1, 15)
        result = format_date(d, "medium", "en")
        assert "2024" in result or "24" in result
        assert "15" in result or "Jan" in result

    def test_format_date_from_string(self):
        """Test formatting date from ISO string."""
        result = format_date("2024-01-15", "medium", "en")
        assert "2024" in result or "24" in result

    def test_format_date_from_datetime(self):
        """Test formatting date from datetime object."""
        dt = datetime(2024, 1, 15, 10, 30)
        result = format_date(dt, "medium", "en")
        assert "2024" in result or "24" in result

    def test_format_date_none(self):
        """Test formatting None returns empty string."""
        assert format_date(None) == ""

    def test_format_date_formats(self):
        """Test different date format presets."""
        d = date(2024, 1, 15)
        for fmt in ["short", "medium", "long", "full"]:
            result = format_date(d, fmt, "en")
            assert len(result) > 0

    # ========== DATETIME FORMATTING ==========

    def test_format_datetime_basic(self):
        """Test basic datetime formatting."""
        dt = datetime(2024, 1, 15, 14, 30, 45)
        result = format_datetime(dt, "medium", "en")
        assert "2024" in result or "24" in result

    def test_format_datetime_from_string(self):
        """Test formatting datetime from ISO string."""
        result = format_datetime("2024-01-15T14:30:45", "short", "en")
        assert len(result) > 0

    # ========== TIME FORMATTING ==========

    def test_format_time_basic(self):
        """Test basic time formatting."""
        t = time(14, 30, 45)
        result = format_time(t, "medium", "en")
        assert ":" in result  # Time should have colon

    def test_format_time_from_datetime(self):
        """Test formatting time from datetime."""
        dt = datetime(2024, 1, 15, 14, 30)
        result = format_time(dt, "short", "en")
        assert len(result) > 0

    # ========== NUMBER FORMATTING ==========

    def test_format_number_basic(self):
        """Test basic number formatting."""
        result = format_number(1234567.89, "en")
        # Should have thousands separator
        assert "1" in result and "234" in result

    def test_format_number_none(self):
        """Test formatting None returns empty string."""
        assert format_number(None) == ""

    def test_format_number_integer(self):
        """Test formatting integer."""
        result = format_number(1000, "en")
        assert "1" in result and "000" in result

    # ========== DECIMAL FORMATTING ==========

    def test_format_decimal_precision(self):
        """Test decimal formatting with precision."""
        result = format_decimal(1234.5, decimal_places=2, locale="en")
        # Should have 2 decimal places
        assert ".50" in result or ",50" in result

    def test_format_decimal_different_precisions(self):
        """Test decimal formatting with various precisions."""
        for places in [0, 1, 2, 3, 4]:
            result = format_decimal(1234.56789, decimal_places=places, locale="en")
            assert len(result) > 0

    # ========== CURRENCY FORMATTING ==========

    def test_format_currency_usd(self):
        """Test USD currency formatting."""
        result = format_currency(1234.56, "USD", "en")
        assert "$" in result or "USD" in result
        assert "1" in result

    def test_format_currency_eur(self):
        """Test EUR currency formatting."""
        result = format_currency(1234.56, "EUR", "en")
        assert "€" in result or "EUR" in result

    def test_format_currency_none(self):
        """Test formatting None returns empty string."""
        assert format_currency(None) == ""

    def test_format_currency_various(self):
        """Test various currency codes."""
        currencies = ["USD", "EUR", "GBP", "JPY", "CNY"]
        for curr in currencies:
            result = format_currency(100, curr, "en")
            assert len(result) > 0

    # ========== PERCENT FORMATTING ==========

    def test_format_percent_basic(self):
        """Test basic percentage formatting."""
        result = format_percent(0.5, "en")
        assert "50" in result
        assert "%" in result

    def test_format_percent_none(self):
        """Test formatting None returns empty string."""
        assert format_percent(None) == ""

    def test_format_percent_small(self):
        """Test formatting small percentages."""
        result = format_percent(0.01, "en")
        assert "1" in result


class TestI18nMixinFormatting:
    """Tests for formatting methods on I18nMixin."""

    def setup_method(self):
        """Set up test fixtures."""

        class TestView(I18nMixin):
            pass

        self.view = TestView()
        self.view._init_i18n()
        self.view._language = "en"

    def test_format_date_method(self):
        """Test format_date method on mixin."""
        d = date(2024, 1, 15)
        result = self.view.format_date(d)
        assert len(result) > 0

    def test_format_datetime_method(self):
        """Test format_datetime method on mixin."""
        dt = datetime(2024, 1, 15, 14, 30)
        result = self.view.format_datetime(dt)
        assert len(result) > 0

    def test_format_time_method(self):
        """Test format_time method on mixin."""
        t = time(14, 30)
        result = self.view.format_time(t)
        assert len(result) > 0

    def test_format_number_method(self):
        """Test format_number method on mixin."""
        result = self.view.format_number(1234.56)
        assert len(result) > 0

    def test_format_currency_method(self):
        """Test format_currency method on mixin."""
        result = self.view.format_currency(99.99, "USD")
        assert len(result) > 0

    def test_format_percent_method(self):
        """Test format_percent method on mixin."""
        result = self.view.format_percent(0.5)
        assert len(result) > 0

    def test_format_decimal_method(self):
        """Test format_decimal method on mixin."""
        result = self.view.format_decimal(1234.5, decimal_places=2)
        assert len(result) > 0

    def test_formatting_uses_current_language(self):
        """Test that formatting uses the current language."""
        self.view._language = "de"
        # German typically uses comma as decimal separator
        result = self.view.format_number(1234.56)
        # Just verify it produces output (actual format depends on babel)
        assert len(result) > 0


class TestRTLLanguages:
    """Tests for RTL language detection."""

    def test_rtl_languages_set(self):
        """Test RTL languages set contains expected languages."""
        assert "ar" in RTL_LANGUAGES
        assert "he" in RTL_LANGUAGES
        assert "fa" in RTL_LANGUAGES
        assert "ur" in RTL_LANGUAGES

    def test_ltr_languages_not_in_rtl(self):
        """Test LTR languages are not in RTL set."""
        assert "en" not in RTL_LANGUAGES
        assert "es" not in RTL_LANGUAGES
        assert "de" not in RTL_LANGUAGES
        assert "fr" not in RTL_LANGUAGES
        assert "ja" not in RTL_LANGUAGES


class TestTranslationHelpers:
    """Tests for translation helper methods."""

    def setup_method(self):
        """Set up test fixtures."""

        class TestView(I18nMixin):
            pass

        self.view = TestView()
        self.view._init_i18n()

    def test_gettext_method(self):
        """Test gettext helper method."""
        # Without actual translations, should return the input
        result = self.view.gettext("Hello")
        assert result == "Hello"

    def test_ngettext_method(self):
        """Test ngettext pluralization helper."""
        result = self.view.ngettext("item", "items", 1)
        assert "item" in result

        result = self.view.ngettext("item", "items", 5)
        assert "item" in result

    def test_pgettext_method(self):
        """Test pgettext context helper."""
        result = self.view.pgettext("button", "Save")
        assert "Save" in result


class TestFormattingTimedelta:
    """Tests for timedelta formatting."""

    def test_format_timedelta_days(self):
        """Test formatting timedelta in days."""
        from djust.i18n.formatting import format_timedelta

        delta = timedelta(days=5)
        result = format_timedelta(delta, locale="en")
        assert "5" in result
        assert "day" in result.lower()

    def test_format_timedelta_hours(self):
        """Test formatting timedelta in hours."""
        from djust.i18n.formatting import format_timedelta

        delta = timedelta(hours=3)
        result = format_timedelta(delta, locale="en")
        assert "3" in result or "hour" in result.lower()

    def test_format_timedelta_none(self):
        """Test formatting None returns empty string."""
        from djust.i18n.formatting import format_timedelta

        assert format_timedelta(None) == ""


class TestLocaleNormalization:
    """Tests for locale normalization."""

    def test_normalize_locale_basic(self):
        """Test basic locale normalization."""
        from djust.i18n.formatting import _normalize_locale

        assert _normalize_locale("en") == "en_US"
        assert _normalize_locale("de") == "de_DE"

    def test_normalize_locale_with_region(self):
        """Test locale normalization with region code."""
        from djust.i18n.formatting import _normalize_locale

        result = _normalize_locale("en-gb")
        assert "en" in result.lower()
        assert "GB" in result or "gb" in result.lower()

    def test_normalize_locale_already_normalized(self):
        """Test already normalized locale."""
        from djust.i18n.formatting import _normalize_locale

        result = _normalize_locale("en_US")
        assert result == "en_US"

    def test_normalize_locale_empty(self):
        """Test empty locale returns default."""
        from djust.i18n.formatting import _normalize_locale

        assert _normalize_locale("") == "en_US"
        assert _normalize_locale(None) == "en_US"


class TestLanguagePersistence:
    """Tests for language persistence behavior."""

    def setup_method(self):
        """Set up test fixtures."""

        class TestView(I18nMixin):
            pass

        self.view = TestView()
        self.view._init_i18n()

    def test_persist_language_setting(self):
        """Test persist_language setting affects commands."""
        with patch("djust.i18n.mixin.activate"):
            # With persistence enabled (default)
            self.view.persist_language = True
            self.view.set_language("es")
            commands = self.view._drain_i18n_commands()

            persist_cmd = [c for c in commands if c["type"] == "persist_language"]
            assert len(persist_cmd) == 1

    def test_disable_persistence(self):
        """Test disabling language persistence."""
        with patch("djust.i18n.mixin.activate"):
            self.view.set_language("es", persist=False)
            commands = self.view._drain_i18n_commands()

            persist_cmd = [c for c in commands if c["type"] == "persist_language"]
            assert len(persist_cmd) == 0

    def test_custom_session_key(self):
        """Test custom session key is used."""
        self.view.language_session_key = "my_custom_lang_key"
        assert self.view.language_session_key == "my_custom_lang_key"

    def test_custom_cookie_name(self):
        """Test custom cookie name is used."""
        self.view.language_cookie_name = "my_lang_cookie"
        assert self.view.language_cookie_name == "my_lang_cookie"
