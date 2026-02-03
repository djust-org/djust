"""
I18nMixin - Internationalization mixin for LiveViews.

Provides live language switching, RTL support, and integration
with Django's i18n system.
"""

import logging
from typing import Any, Dict, List, Optional, Set

from django.conf import settings
from django.utils import translation
from django.utils.translation import get_language, activate, gettext as _

from .formatting import (
    format_date,
    format_datetime,
    format_time,
    format_number,
    format_currency,
    format_percent,
    format_decimal,
)

logger = logging.getLogger(__name__)

# RTL (right-to-left) languages
RTL_LANGUAGES: Set[str] = {
    "ar",  # Arabic
    "arc",  # Aramaic
    "dv",  # Divehi
    "fa",  # Persian/Farsi
    "ha",  # Hausa
    "he",  # Hebrew
    "khw",  # Khowar
    "ks",  # Kashmiri
    "ku",  # Kurdish
    "ps",  # Pashto
    "ur",  # Urdu
    "yi",  # Yiddish
}


class I18nMixin:
    """
    Mixin that adds internationalization support to LiveViews.

    Features:
        - Auto-detect language from request (Accept-Language, session, cookie)
        - Live language switching without page reload
        - RTL (right-to-left) language detection and support
        - Locale-aware formatting (dates, numbers, currency)
        - Django translation integration ({% trans %}, {% blocktrans %})

    Attributes:
        language (str): Current language code (e.g., 'en', 'es', 'ar')
        is_rtl (bool): Whether current language is RTL
        text_direction (str): 'rtl' or 'ltr'
        available_languages (list): List of (code, name) tuples

    Example:
        class MyView(I18nMixin, LiveView):
            template_name = 'myview.html'

            def mount(self, request, **kwargs):
                # Language is auto-detected from request
                self.title = _("Welcome")

            @event_handler
            def change_language(self, lang):
                self.set_language(lang)
                self.title = _("Welcome")  # Re-translate
    """

    # Default language (can be overridden per-view)
    default_language: Optional[str] = None

    # Whether to persist language preference to session
    persist_language: bool = True

    # Session key for storing language preference
    language_session_key: str = "djust_language"

    # Cookie name for language preference (if session not available)
    language_cookie_name: str = "djust_lang"

    # Cookie max age in seconds (1 year)
    language_cookie_max_age: int = 365 * 24 * 60 * 60

    def _init_i18n(self) -> None:
        """Initialize i18n state. Called from mount or __init__."""
        self._language: Optional[str] = None
        self._pending_i18n_commands: List[Dict[str, Any]] = []

    @property
    def language(self) -> str:
        """Get the current language code."""
        if self._language:
            return self._language
        return get_language() or self._get_default_language()

    @language.setter
    def language(self, value: str) -> None:
        """Set the language (internal use)."""
        self._language = value

    @property
    def is_rtl(self) -> bool:
        """Check if current language is right-to-left."""
        lang = self.language
        # Check base language code (e.g., 'ar' from 'ar-SA')
        base_lang = lang.split("-")[0].lower()
        return base_lang in RTL_LANGUAGES

    @property
    def text_direction(self) -> str:
        """Get text direction ('rtl' or 'ltr')."""
        return "rtl" if self.is_rtl else "ltr"

    @property
    def available_languages(self) -> List[tuple]:
        """Get list of available languages as (code, name) tuples."""
        return getattr(settings, "LANGUAGES", [("en", "English")])

    def _get_default_language(self) -> str:
        """Get the default language code."""
        if self.default_language:
            return self.default_language
        return getattr(settings, "LANGUAGE_CODE", "en")

    def _detect_language(self, request) -> str:
        """
        Detect language from request.

        Priority:
        1. URL parameter (?lang=xx)
        2. Session
        3. Cookie
        4. Accept-Language header
        5. Default language
        """
        # 1. URL parameter
        lang = request.GET.get("lang") or request.GET.get("language")
        if lang and self._is_valid_language(lang):
            return lang

        # 2. Session
        if hasattr(request, "session") and self.language_session_key in request.session:
            lang = request.session[self.language_session_key]
            if self._is_valid_language(lang):
                return lang

        # 3. Cookie
        lang = request.COOKIES.get(self.language_cookie_name)
        if lang and self._is_valid_language(lang):
            return lang

        # 4. Django's language detection (Accept-Language header)
        lang = translation.get_language_from_request(request, check_path=True)
        if lang and self._is_valid_language(lang):
            return lang

        # 5. Default
        return self._get_default_language()

    def _is_valid_language(self, lang: str) -> bool:
        """Check if language code is in available languages."""
        if not lang:
            return False
        available = [code for code, name in self.available_languages]
        # Check exact match or base language (e.g., 'en' matches 'en-us')
        return lang in available or lang.split("-")[0] in available

    def set_language(
        self,
        lang: str,
        persist: Optional[bool] = None,
        update_html_lang: bool = True
    ) -> None:
        """
        Set the current language and update the UI.

        This method:
        1. Activates the new language for translations
        2. Re-renders the template with new translations
        3. Optionally updates the <html lang="..."> attribute
        4. Optionally persists preference to session/cookie

        Args:
            lang: Language code (e.g., 'en', 'es', 'fr')
            persist: Whether to persist to session. Defaults to self.persist_language
            update_html_lang: Whether to update <html lang> attribute

        Example:
            @event_handler
            def switch_to_spanish(self):
                self.set_language('es')
        """
        if not self._is_valid_language(lang):
            logger.warning(f"Invalid language code: {lang}")
            return

        old_lang = self.language
        self._language = lang

        # Activate Django translation
        activate(lang)

        # Queue command to update HTML lang attribute and direction
        if update_html_lang:
            self._pending_i18n_commands.append({
                "type": "set_language",
                "lang": lang,
                "dir": self.text_direction,
                "is_rtl": self.is_rtl,
            })

        # Persist preference
        should_persist = persist if persist is not None else self.persist_language
        if should_persist:
            self._pending_i18n_commands.append({
                "type": "persist_language",
                "lang": lang,
            })

        logger.debug(f"Language changed from {old_lang} to {lang}")

    def mount_i18n(self, request, **kwargs) -> None:
        """
        Initialize i18n on mount. Call this in your mount() method
        or let it be called automatically.

        Args:
            request: The Django request object
        """
        self._init_i18n()

        # Detect language from request
        lang = self._detect_language(request)
        self._language = lang

        # Activate Django translation
        activate(lang)

        logger.debug(f"I18n initialized with language: {lang}")

    def get_i18n_context(self) -> Dict[str, Any]:
        """
        Get i18n-related context variables for templates.

        Returns dict with:
            - language: Current language code
            - is_rtl: Boolean RTL flag
            - text_direction: 'rtl' or 'ltr'
            - available_languages: List of (code, name) tuples
        """
        return {
            "language": self.language,
            "is_rtl": self.is_rtl,
            "text_direction": self.text_direction,
            "available_languages": self.available_languages,
        }

    def _drain_i18n_commands(self) -> List[Dict[str, Any]]:
        """Drain and return pending i18n commands."""
        commands = self._pending_i18n_commands
        self._pending_i18n_commands = []
        return commands

    # ============================================================================
    # FORMATTING HELPERS (convenience wrappers around formatting module)
    # ============================================================================

    def format_date(
        self,
        value,
        format: str = "medium",
        locale: Optional[str] = None
    ) -> str:
        """
        Format a date using the current locale.

        Args:
            value: date, datetime, or ISO string
            format: 'short', 'medium', 'long', 'full', or custom format
            locale: Override locale (defaults to current language)

        Returns:
            Formatted date string
        """
        return format_date(value, format=format, locale=locale or self.language)

    def format_datetime(
        self,
        value,
        format: str = "medium",
        locale: Optional[str] = None
    ) -> str:
        """
        Format a datetime using the current locale.

        Args:
            value: datetime or ISO string
            format: 'short', 'medium', 'long', 'full', or custom format
            locale: Override locale (defaults to current language)

        Returns:
            Formatted datetime string
        """
        return format_datetime(value, format=format, locale=locale or self.language)

    def format_time(
        self,
        value,
        format: str = "medium",
        locale: Optional[str] = None
    ) -> str:
        """
        Format a time using the current locale.

        Args:
            value: time, datetime, or ISO string
            format: 'short', 'medium', 'long', 'full', or custom format
            locale: Override locale (defaults to current language)

        Returns:
            Formatted time string
        """
        return format_time(value, format=format, locale=locale or self.language)

    def format_number(
        self,
        value: float,
        locale: Optional[str] = None
    ) -> str:
        """
        Format a number using the current locale.

        Args:
            value: Number to format
            locale: Override locale (defaults to current language)

        Returns:
            Formatted number string (e.g., "1,234.56" or "1.234,56")
        """
        return format_number(value, locale=locale or self.language)

    def format_currency(
        self,
        value: float,
        currency: str = "USD",
        locale: Optional[str] = None
    ) -> str:
        """
        Format a currency value using the current locale.

        Args:
            value: Amount to format
            currency: Currency code (e.g., 'USD', 'EUR', 'GBP')
            locale: Override locale (defaults to current language)

        Returns:
            Formatted currency string (e.g., "$1,234.56" or "1.234,56 €")
        """
        return format_currency(value, currency=currency, locale=locale or self.language)

    def format_percent(
        self,
        value: float,
        locale: Optional[str] = None
    ) -> str:
        """
        Format a percentage using the current locale.

        Args:
            value: Value to format (0.5 = 50%)
            locale: Override locale (defaults to current language)

        Returns:
            Formatted percentage string (e.g., "50%" or "50 %")
        """
        return format_percent(value, locale=locale or self.language)

    def format_decimal(
        self,
        value: float,
        decimal_places: int = 2,
        locale: Optional[str] = None
    ) -> str:
        """
        Format a decimal number with specific precision.

        Args:
            value: Number to format
            decimal_places: Number of decimal places
            locale: Override locale (defaults to current language)

        Returns:
            Formatted decimal string
        """
        return format_decimal(value, decimal_places=decimal_places, locale=locale or self.language)

    # ============================================================================
    # TRANSLATION HELPERS
    # ============================================================================

    def gettext(self, message: str) -> str:
        """
        Translate a string using Django's translation system.

        This is a convenience wrapper around Django's gettext.
        In most cases, you should use _() directly in your code.

        Args:
            message: String to translate

        Returns:
            Translated string
        """
        return _(message)

    def ngettext(self, singular: str, plural: str, number: int) -> str:
        """
        Translate singular/plural forms.

        Args:
            singular: Singular form
            plural: Plural form
            number: Count for determining form

        Returns:
            Appropriate translated string
        """
        from django.utils.translation import ngettext
        return ngettext(singular, plural, number)

    def pgettext(self, context: str, message: str) -> str:
        """
        Translate a string with context disambiguation.

        Args:
            context: Translation context
            message: String to translate

        Returns:
            Translated string
        """
        from django.utils.translation import pgettext
        return pgettext(context, message)
