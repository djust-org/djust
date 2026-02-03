"""
Unit tests for djust i18n template tags.

Tests for language selection, RTL/LTR handling, JavaScript i18n initialization,
and locale-aware formatting filters.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, datetime


class TestDjustI18nJs:
    """Tests for djust_i18n_js template tag."""

    def test_basic_i18n_js_output(self):
        """Basic djust_i18n_js outputs script tag with i18n config."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English"), ("es", "Spanish")]

                result = djust_i18n_js({})

        assert "<script>" in result
        assert "window.djust" in result
        assert "window.djust.i18n" in result
        assert '"lang": "en"' in result
        assert '"isRtl": false' in result
        assert '"dir": "ltr"' in result

    def test_i18n_js_with_translations(self):
        """djust_i18n_js includes provided translation keys."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                with patch("djust.templatetags.djust_i18n._", side_effect=lambda x: f"translated_{x}"):
                    mock_settings.LANGUAGE_CODE = "en"
                    mock_settings.LANGUAGES = [("en", "English")]

                    result = djust_i18n_js({}, "Hello", "Goodbye")

        assert "translated_Hello" in result
        assert "translated_Goodbye" in result
        assert '"translations":' in result

    def test_i18n_js_includes_available_languages(self):
        """djust_i18n_js includes list of available languages."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Español"),
                    ("fr", "Français"),
                ]

                result = djust_i18n_js({})

        assert '"availableLanguages":' in result
        assert '"code": "en"' in result
        assert '"name": "English"' in result
        assert '"code": "es"' in result
        assert '"name": "Español"' in result

    def test_i18n_js_rtl_language(self):
        """djust_i18n_js correctly identifies RTL languages."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="ar"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "ar"
                mock_settings.LANGUAGES = [("ar", "Arabic")]

                result = djust_i18n_js({})

        assert '"isRtl": true' in result
        assert '"dir": "rtl"' in result

    def test_i18n_js_rtl_language_with_region(self):
        """djust_i18n_js handles RTL languages with region codes."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="ar-SA"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "ar-SA"
                mock_settings.LANGUAGES = [("ar-SA", "Arabic (Saudi Arabia)")]

                result = djust_i18n_js({})

        assert '"isRtl": true' in result
        assert '"dir": "rtl"' in result

    def test_i18n_js_includes_rtl_languages_list(self):
        """djust_i18n_js includes list of RTL language codes."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_i18n_js({})

        assert '"rtlLanguages":' in result
        assert '"ar"' in result
        assert '"he"' in result
        assert '"fa"' in result

    def test_i18n_js_javascript_api(self):
        """djust_i18n_js includes correct JavaScript API methods."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_i18n_js({})

        # Check JavaScript API methods are present
        assert "get lang()" in result
        assert "get isRtl()" in result
        assert "get dir()" in result
        assert "get availableLanguages()" in result
        assert "get: function(key, fallback)" in result
        assert "isRtlLanguage: function(lang)" in result
        assert "addTranslations: function(translations)" in result
        assert "_setLanguage: function(lang, dir, isRtl)" in result


class TestDjustTranslationsJson:
    """Tests for djust_translations_json template tag."""

    def test_translations_json_output(self):
        """djust_translations_json outputs valid JSON."""
        from djust.templatetags.djust_i18n import djust_translations_json

        with patch("djust.templatetags.djust_i18n._", side_effect=lambda x: f"t_{x}"):
            result = djust_translations_json({}, "Save", "Cancel")

        parsed = json.loads(result)
        assert parsed["Save"] == "t_Save"
        assert parsed["Cancel"] == "t_Cancel"

    def test_translations_json_empty(self):
        """djust_translations_json with no keys returns empty object."""
        from djust.templatetags.djust_i18n import djust_translations_json

        result = djust_translations_json({})

        parsed = json.loads(result)
        assert parsed == {}


class TestDjustLanguageSelect:
    """Tests for djust_language_select template tag."""

    def test_language_select_renders_dropdown(self):
        """djust_language_select renders a select element."""
        from djust.templatetags.djust_i18n import djust_language_select

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                ]

                result = djust_language_select({})

        assert "<select" in result
        assert "</select>" in result
        assert "<option" in result

    def test_language_select_current_language_selected(self):
        """djust_language_select marks current language as selected."""
        from djust.templatetags.djust_i18n import djust_language_select

        with patch("djust.templatetags.djust_i18n.get_language", return_value="es"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                ]

                result = djust_language_select({})

        # Spanish should be selected
        assert 'value="es" selected' in result
        # English should not be selected
        assert 'value="en" selected' not in result

    def test_language_select_includes_dj_change(self):
        """djust_language_select includes dj-change attribute by default."""
        from djust.templatetags.djust_i18n import djust_language_select

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_language_select({})

        assert 'dj-change="change_language"' in result

    def test_language_select_custom_attributes(self):
        """djust_language_select accepts custom HTML attributes."""
        from djust.templatetags.djust_i18n import djust_language_select

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_language_select({}, **{"class": "form-select", "id": "lang-picker"})

        assert 'class="form-select"' in result
        assert 'id="lang-picker"' in result

    def test_language_select_all_languages_as_options(self):
        """djust_language_select includes all configured languages."""
        from djust.templatetags.djust_i18n import djust_language_select

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Español"),
                    ("fr", "Français"),
                    ("de", "Deutsch"),
                ]

                result = djust_language_select({})

        assert 'value="en"' in result
        assert 'value="es"' in result
        assert 'value="fr"' in result
        assert 'value="de"' in result
        assert ">English<" in result
        assert ">Español<" in result
        assert ">Français<" in result
        assert ">Deutsch<" in result


class TestDjustLanguageButtons:
    """Tests for djust_language_buttons template tag."""

    def test_language_buttons_renders_buttons(self):
        """djust_language_buttons renders button elements."""
        from djust.templatetags.djust_i18n import djust_language_buttons

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                ]

                result = djust_language_buttons({})

        assert "<button" in result
        assert "</button>" in result
        assert 'type="button"' in result

    def test_language_buttons_active_class(self):
        """djust_language_buttons applies active class to current language."""
        from djust.templatetags.djust_i18n import djust_language_buttons

        with patch("djust.templatetags.djust_i18n.get_language", return_value="es"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                ]

                result = djust_language_buttons({}, active_class="btn-primary", inactive_class="btn-secondary")

        # Spanish button should have active class
        assert 'dj-value-lang="es"' in result
        # Check structure includes active/inactive classes
        assert "btn-primary" in result
        assert "btn-secondary" in result

    def test_language_buttons_aria_current(self):
        """djust_language_buttons includes aria-current for active language."""
        from djust.templatetags.djust_i18n import djust_language_buttons

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                ]

                result = djust_language_buttons({})

        assert 'aria-current="true"' in result

    def test_language_buttons_dj_click(self):
        """djust_language_buttons includes dj-click for each button."""
        from djust.templatetags.djust_i18n import djust_language_buttons

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                ]

                result = djust_language_buttons({})

        assert 'dj-click="change_language"' in result
        assert 'dj-value-lang="en"' in result
        assert 'dj-value-lang="es"' in result


class TestDjustRtlHelpers:
    """Tests for RTL/LTR helper tags."""

    def test_djust_rtl_class_ltr_language(self):
        """djust_rtl_class returns LTR class for LTR languages."""
        from djust.templatetags.djust_i18n import djust_rtl_class

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"

                result = djust_rtl_class({}, "text-right", "text-left")

        assert result == "text-left"

    def test_djust_rtl_class_rtl_language(self):
        """djust_rtl_class returns RTL class for RTL languages."""
        from djust.templatetags.djust_i18n import djust_rtl_class

        with patch("djust.templatetags.djust_i18n.get_language", return_value="ar"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "ar"

                result = djust_rtl_class({}, "text-right", "text-left")

        assert result == "text-right"

    def test_djust_rtl_class_hebrew(self):
        """djust_rtl_class correctly identifies Hebrew as RTL."""
        from djust.templatetags.djust_i18n import djust_rtl_class

        with patch("djust.templatetags.djust_i18n.get_language", return_value="he"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "he"

                result = djust_rtl_class({}, "rtl-class", "ltr-class")

        assert result == "rtl-class"

    def test_djust_text_dir_ltr(self):
        """djust_text_dir returns 'ltr' for LTR languages."""
        from djust.templatetags.djust_i18n import djust_text_dir

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"

                result = djust_text_dir()

        assert result == "ltr"

    def test_djust_text_dir_rtl(self):
        """djust_text_dir returns 'rtl' for RTL languages."""
        from djust.templatetags.djust_i18n import djust_text_dir

        with patch("djust.templatetags.djust_i18n.get_language", return_value="fa"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "fa"

                result = djust_text_dir()

        assert result == "rtl"

    def test_djust_is_rtl_false(self):
        """djust_is_rtl returns False for LTR languages."""
        from djust.templatetags.djust_i18n import djust_is_rtl

        with patch("djust.templatetags.djust_i18n.get_language", return_value="es"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "es"

                result = djust_is_rtl()

        assert result is False

    def test_djust_is_rtl_true(self):
        """djust_is_rtl returns True for RTL languages."""
        from djust.templatetags.djust_i18n import djust_is_rtl

        with patch("djust.templatetags.djust_i18n.get_language", return_value="ur"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "ur"

                result = djust_is_rtl()

        assert result is True


class TestRtlLanguagesList:
    """Tests for RTL language detection."""

    def test_all_rtl_languages_detected(self):
        """All defined RTL languages are correctly detected."""
        from djust.templatetags.djust_i18n import RTL_LANGUAGES, djust_is_rtl

        rtl_langs = ["ar", "arc", "dv", "fa", "ha", "he", "khw", "ks", "ku", "ps", "ur", "yi"]

        for lang in rtl_langs:
            with patch("djust.templatetags.djust_i18n.get_language", return_value=lang):
                with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                    mock_settings.LANGUAGE_CODE = lang
                    result = djust_is_rtl()
                    assert result is True, f"{lang} should be detected as RTL"

    def test_ltr_languages_not_rtl(self):
        """Common LTR languages are not detected as RTL."""
        from djust.templatetags.djust_i18n import djust_is_rtl

        ltr_langs = ["en", "es", "fr", "de", "it", "pt", "ru", "zh", "ja", "ko"]

        for lang in ltr_langs:
            with patch("djust.templatetags.djust_i18n.get_language", return_value=lang):
                with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                    mock_settings.LANGUAGE_CODE = lang
                    result = djust_is_rtl()
                    assert result is False, f"{lang} should be detected as LTR"


class TestLanguageFallback:
    """Tests for language fallback behavior."""

    def test_fallback_to_settings_language_code(self):
        """Falls back to settings.LANGUAGE_CODE when get_language returns None."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value=None):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "fr"
                mock_settings.LANGUAGES = [("fr", "French")]

                result = djust_i18n_js({})

        assert '"lang": "fr"' in result

    def test_fallback_languages_list(self):
        """Falls back to default languages if LANGUAGES not set."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                # Simulate LANGUAGES not being set
                del mock_settings.LANGUAGES

                # Should use getattr default
                result = djust_i18n_js({})

        assert "<script>" in result


class TestFormattingFilters:
    """Tests for i18n formatting filters."""

    def test_format_number_i18n_filter(self):
        """format_number_i18n filter formats numbers."""
        from djust.templatetags.djust_i18n import format_number_i18n
        from djust.i18n.formatting import BABEL_AVAILABLE

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            result = format_number_i18n(1234567.89)

        # Result should contain formatted number
        assert "1" in result
        assert "234" in result or "," in result  # Has thousands separator

    def test_format_currency_i18n_filter(self):
        """format_currency_i18n filter formats currency."""
        from djust.templatetags.djust_i18n import format_currency_i18n

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            result = format_currency_i18n(99.99, "USD")

        assert "$" in result or "USD" in result
        assert "99" in result

    def test_format_currency_with_locale(self):
        """format_currency_i18n filter accepts locale override."""
        from djust.templatetags.djust_i18n import format_currency_i18n

        result = format_currency_i18n(1234.56, "EUR:de")

        # Should contain the amount (may have different thousand separators)
        assert "1,234" in result or "1.234" in result or "1234" in result

    def test_format_date_i18n_filter(self):
        """format_date_i18n filter formats dates."""
        from djust.templatetags.djust_i18n import format_date_i18n

        test_date = date(2024, 1, 15)

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            result = format_date_i18n(test_date, "medium")

        # Should contain date components
        assert "2024" in result or "24" in result
        assert "15" in result or "Jan" in result

    def test_format_datetime_i18n_filter(self):
        """format_datetime_i18n filter formats datetimes."""
        from djust.templatetags.djust_i18n import format_datetime_i18n

        test_dt = datetime(2024, 1, 15, 14, 30)

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            result = format_datetime_i18n(test_dt, "short")

        # Should contain date and time components
        assert "15" in result or "1" in result
        assert "30" in result or "2:30" in result

    def test_format_percent_i18n_filter(self):
        """format_percent_i18n filter formats percentages."""
        from djust.templatetags.djust_i18n import format_percent_i18n

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            result = format_percent_i18n(0.5)

        assert "50" in result
        assert "%" in result


class TestLanguageMenuContext:
    """Tests for djust_language_menu inclusion tag context."""

    def test_language_menu_context(self):
        """djust_language_menu returns correct context dict."""
        from djust.templatetags.djust_i18n import djust_language_menu

        with patch("djust.templatetags.djust_i18n.get_language", return_value="es"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [
                    ("en", "English"),
                    ("es", "Spanish"),
                    ("fr", "French"),
                ]

                result = djust_language_menu({})

        assert result["current_lang"] == "es"
        assert result["current_lang_name"] == "Spanish"
        assert ("en", "English") in result["available_languages"]
        assert ("es", "Spanish") in result["available_languages"]
        assert ("fr", "French") in result["available_languages"]

    def test_language_menu_extra_kwargs(self):
        """djust_language_menu passes through extra kwargs."""
        from djust.templatetags.djust_i18n import djust_language_menu

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_language_menu({}, custom_param="value")

        assert result["custom_param"] == "value"


class TestI18nJsEventDispatching:
    """Tests for JavaScript event dispatching in i18n."""

    def test_i18n_js_dispatches_language_change_event(self):
        """djust_i18n_js includes language change event dispatching."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_i18n_js({})

        assert "djust:language-changed" in result
        assert "CustomEvent" in result
        assert "dispatchEvent" in result

    def test_i18n_js_updates_html_attributes(self):
        """djust_i18n_js includes code to update HTML lang/dir attributes."""
        from djust.templatetags.djust_i18n import djust_i18n_js

        with patch("djust.templatetags.djust_i18n.get_language", return_value="en"):
            with patch("djust.templatetags.djust_i18n.settings") as mock_settings:
                mock_settings.LANGUAGE_CODE = "en"
                mock_settings.LANGUAGES = [("en", "English")]

                result = djust_i18n_js({})

        assert "document.documentElement.lang" in result
        assert "document.documentElement.dir" in result
