"""
Locale-aware formatting functions for i18n.

These functions provide locale-aware formatting for dates, times,
numbers, and currencies. They work with Django's i18n system and
support the same locales.
"""

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Try to import babel for advanced formatting (optional dependency)
try:
    import babel
    from babel import numbers, dates
    from babel.core import Locale, UnknownLocaleError
    BABEL_AVAILABLE = True
except ImportError:
    BABEL_AVAILABLE = False
    logger.debug("Babel not available, using basic formatting")


def _normalize_locale(locale: str) -> str:
    """
    Normalize locale string for babel (e.g., 'en-us' -> 'en_US').
    """
    if not locale:
        return "en_US"

    # Replace hyphen with underscore
    locale = locale.replace("-", "_")

    # Handle simple cases like 'en' -> 'en_US'
    parts = locale.split("_")
    if len(parts) == 1:
        # Map common language codes to their primary locales
        lang_to_locale = {
            "en": "en_US",
            "es": "es_ES",
            "fr": "fr_FR",
            "de": "de_DE",
            "it": "it_IT",
            "pt": "pt_BR",
            "ru": "ru_RU",
            "zh": "zh_CN",
            "ja": "ja_JP",
            "ko": "ko_KR",
            "ar": "ar_SA",
            "he": "he_IL",
            "fa": "fa_IR",
            "hi": "hi_IN",
            "nl": "nl_NL",
            "pl": "pl_PL",
            "tr": "tr_TR",
            "vi": "vi_VN",
            "th": "th_TH",
        }
        return lang_to_locale.get(locale, f"{locale}_{locale.upper()}")

    # Ensure country code is uppercase
    return f"{parts[0]}_{parts[1].upper()}"


def _get_babel_locale(locale: str) -> Optional["Locale"]:
    """Get a babel Locale object, returning None if invalid."""
    if not BABEL_AVAILABLE:
        return None

    try:
        normalized = _normalize_locale(locale)
        return Locale.parse(normalized)
    except (UnknownLocaleError, ValueError):
        try:
            # Try just the language code
            return Locale.parse(locale.split("-")[0].split("_")[0])
        except (UnknownLocaleError, ValueError):
            logger.warning(f"Unknown locale: {locale}, falling back to en_US")
            return Locale.parse("en_US")


def _parse_date(value) -> Optional[date]:
    """Parse various date formats to a date object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        # Try ISO format first
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        # Try common formats
        for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def _parse_datetime(value) -> Optional[datetime]:
    """Parse various datetime formats to a datetime object."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def _parse_time(value) -> Optional[time]:
    """Parse various time formats to a time object."""
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(f"1970-01-01T{value}").time()
        except ValueError:
            pass
    return None


# ============================================================================
# DATE/TIME FORMATTING
# ============================================================================

def format_date(
    value,
    format: str = "medium",
    locale: str = "en"
) -> str:
    """
    Format a date using locale-specific formatting.

    Args:
        value: date, datetime, or ISO date string
        format: One of 'short', 'medium', 'long', 'full', or a custom pattern
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted date string

    Examples:
        >>> format_date(date(2024, 1, 15), 'short', 'en')
        '1/15/24'
        >>> format_date(date(2024, 1, 15), 'medium', 'de')
        '15.01.2024'
        >>> format_date(date(2024, 1, 15), 'long', 'es')
        '15 de enero de 2024'
    """
    parsed = _parse_date(value)
    if parsed is None:
        return str(value) if value else ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return dates.format_date(parsed, format=format, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel date formatting failed: {e}")

    # Fallback to basic formatting
    format_map = {
        "short": "%m/%d/%y",
        "medium": "%b %d, %Y",
        "long": "%B %d, %Y",
        "full": "%A, %B %d, %Y",
    }
    fmt = format_map.get(format, format)
    return parsed.strftime(fmt)


def format_datetime(
    value,
    format: str = "medium",
    locale: str = "en"
) -> str:
    """
    Format a datetime using locale-specific formatting.

    Args:
        value: datetime or ISO datetime string
        format: One of 'short', 'medium', 'long', 'full', or a custom pattern
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted datetime string

    Examples:
        >>> format_datetime(datetime(2024, 1, 15, 14, 30), 'short', 'en')
        '1/15/24, 2:30 PM'
        >>> format_datetime(datetime(2024, 1, 15, 14, 30), 'medium', 'de')
        '15.01.2024, 14:30:00'
    """
    parsed = _parse_datetime(value)
    if parsed is None:
        return str(value) if value else ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return dates.format_datetime(parsed, format=format, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel datetime formatting failed: {e}")

    # Fallback to basic formatting
    format_map = {
        "short": "%m/%d/%y %I:%M %p",
        "medium": "%b %d, %Y %I:%M:%S %p",
        "long": "%B %d, %Y %I:%M:%S %p %Z",
        "full": "%A, %B %d, %Y %I:%M:%S %p %Z",
    }
    fmt = format_map.get(format, format)
    return parsed.strftime(fmt)


def format_time(
    value,
    format: str = "medium",
    locale: str = "en"
) -> str:
    """
    Format a time using locale-specific formatting.

    Args:
        value: time, datetime, or time string
        format: One of 'short', 'medium', 'long', 'full', or a custom pattern
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted time string

    Examples:
        >>> format_time(time(14, 30), 'short', 'en')
        '2:30 PM'
        >>> format_time(time(14, 30), 'medium', 'de')
        '14:30:00'
    """
    parsed = _parse_time(value)
    if parsed is None:
        return str(value) if value else ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return dates.format_time(parsed, format=format, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel time formatting failed: {e}")

    # Fallback to basic formatting
    format_map = {
        "short": "%I:%M %p",
        "medium": "%I:%M:%S %p",
        "long": "%I:%M:%S %p %Z",
        "full": "%I:%M:%S %p %Z",
    }
    fmt = format_map.get(format, format)
    return parsed.strftime(fmt)


# ============================================================================
# NUMBER FORMATTING
# ============================================================================

def format_number(
    value: Union[int, float, Decimal],
    locale: str = "en"
) -> str:
    """
    Format a number using locale-specific formatting.

    Args:
        value: Number to format
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted number string

    Examples:
        >>> format_number(1234567.89, 'en')
        '1,234,567.89'
        >>> format_number(1234567.89, 'de')
        '1.234.567,89'
        >>> format_number(1234567.89, 'fr')
        '1 234 567,89'
    """
    if value is None:
        return ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return numbers.format_decimal(value, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel number formatting failed: {e}")

    # Fallback to basic formatting
    return f"{value:,.2f}" if isinstance(value, float) else f"{value:,}"


def format_decimal(
    value: Union[int, float, Decimal],
    decimal_places: int = 2,
    locale: str = "en"
) -> str:
    """
    Format a decimal number with specific precision.

    Args:
        value: Number to format
        decimal_places: Number of decimal places
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted decimal string

    Examples:
        >>> format_decimal(1234.5, 2, 'en')
        '1,234.50'
        >>> format_decimal(1234.567, 1, 'de')
        '1.234,6'
    """
    if value is None:
        return ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            format_str = f"#,##0.{'0' * decimal_places}"
            return numbers.format_decimal(value, format=format_str, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel decimal formatting failed: {e}")

    # Fallback to basic formatting
    return f"{value:,.{decimal_places}f}"


def format_currency(
    value: Union[int, float, Decimal],
    currency: str = "USD",
    locale: str = "en"
) -> str:
    """
    Format a currency value using locale-specific formatting.

    Args:
        value: Amount to format
        currency: Currency code (e.g., 'USD', 'EUR', 'GBP')
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted currency string

    Examples:
        >>> format_currency(1234.56, 'USD', 'en')
        '$1,234.56'
        >>> format_currency(1234.56, 'EUR', 'de')
        '1.234,56 €'
        >>> format_currency(1234.56, 'GBP', 'en-gb')
        '£1,234.56'
    """
    if value is None:
        return ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return numbers.format_currency(value, currency, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel currency formatting failed: {e}")

    # Fallback to basic formatting
    currency_symbols = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
        "KRW": "₩",
        "INR": "₹",
        "RUB": "₽",
        "BRL": "R$",
        "MXN": "$",
        "CAD": "C$",
        "AUD": "A$",
    }
    symbol = currency_symbols.get(currency, currency)
    formatted = f"{value:,.2f}"
    return f"{symbol}{formatted}"


def format_percent(
    value: Union[int, float, Decimal],
    locale: str = "en"
) -> str:
    """
    Format a percentage using locale-specific formatting.

    Args:
        value: Value to format (0.5 = 50%)
        locale: Locale code (e.g., 'en', 'es', 'de')

    Returns:
        Formatted percentage string

    Examples:
        >>> format_percent(0.5, 'en')
        '50%'
        >>> format_percent(0.1234, 'de')
        '12,34 %'
    """
    if value is None:
        return ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return numbers.format_percent(value, locale=babel_locale)
        except Exception as e:
            logger.warning(f"Babel percent formatting failed: {e}")

    # Fallback to basic formatting
    return f"{value * 100:.0f}%"


# ============================================================================
# RELATIVE TIME FORMATTING
# ============================================================================

def format_timedelta(
    value,
    granularity: str = "second",
    threshold: float = 0.85,
    locale: str = "en"
) -> str:
    """
    Format a timedelta in a human-readable relative format.

    Args:
        value: timedelta object
        granularity: Smallest unit to display ('year', 'month', 'day', 'hour', 'minute', 'second')
        threshold: Factor for when to use the next larger unit
        locale: Locale code

    Returns:
        Human-readable time difference (e.g., "3 days ago", "in 2 hours")

    Examples:
        >>> from datetime import timedelta
        >>> format_timedelta(timedelta(days=5), locale='en')
        '5 days'
        >>> format_timedelta(timedelta(hours=3), locale='es')
        '3 horas'
    """
    if value is None:
        return ""

    if BABEL_AVAILABLE:
        babel_locale = _get_babel_locale(locale)
        try:
            return dates.format_timedelta(
                value,
                granularity=granularity,
                threshold=threshold,
                locale=babel_locale
            )
        except Exception as e:
            logger.warning(f"Babel timedelta formatting failed: {e}")

    # Fallback to basic formatting
    from datetime import timedelta
    if not isinstance(value, timedelta):
        return str(value)

    total_seconds = int(value.total_seconds())
    if abs(total_seconds) < 60:
        return f"{total_seconds} seconds"
    elif abs(total_seconds) < 3600:
        minutes = total_seconds // 60
        return f"{minutes} minute{'s' if abs(minutes) != 1 else ''}"
    elif abs(total_seconds) < 86400:
        hours = total_seconds // 3600
        return f"{hours} hour{'s' if abs(hours) != 1 else ''}"
    else:
        days = total_seconds // 86400
        return f"{days} day{'s' if abs(days) != 1 else ''}"
