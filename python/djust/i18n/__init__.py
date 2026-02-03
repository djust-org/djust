"""
djust internationalization (i18n) support.

Provides seamless integration with Django's i18n system for LiveViews,
including live language switching without page reload, RTL support,
and locale-aware formatting.

Usage:
    from djust.i18n import I18nMixin

    class MyView(I18nMixin, LiveView):
        def mount(self, request, **kwargs):
            # self.language is auto-detected from request
            self.greeting = _("Hello")

        @event_handler
        def change_language(self, lang):
            self.set_language(lang)  # Updates UI without reload
"""

from .mixin import I18nMixin
from .formatting import (
    format_date,
    format_datetime,
    format_time,
    format_number,
    format_currency,
    format_percent,
    format_decimal,
)

__all__ = [
    "I18nMixin",
    "format_date",
    "format_datetime",
    "format_time",
    "format_number",
    "format_currency",
    "format_percent",
    "format_decimal",
]
