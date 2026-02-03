"""
Django template tags for djust i18n support.

Provides tags for exposing translations to JavaScript and managing
language switching in templates.

Usage:
    {% load djust_i18n %}

    <!-- Expose translations to JavaScript -->
    {% djust_i18n_js %}
    {% djust_i18n_js "Hello" "Goodbye" "Welcome" %}

    <!-- Language switcher -->
    {% djust_language_select %}
    {% djust_language_select class="form-select" %}

    <!-- RTL-aware container -->
    {% djust_rtl_container %}
        <div>Content that adapts to text direction</div>
    {% end_djust_rtl_container %}
"""

import json
from typing import List, Optional, Dict, Any

from django import template
from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _, get_language

register = template.Library()


# RTL languages for client-side detection
RTL_LANGUAGES = ["ar", "arc", "dv", "fa", "ha", "he", "khw", "ks", "ku", "ps", "ur", "yi"]


@register.simple_tag(takes_context=True)
def djust_i18n_js(context, *translation_keys):
    """
    Output a <script> tag that sets up window.djust.i18n with translations.

    If translation keys are provided, only those translations are included.
    Otherwise, includes basic i18n metadata (current language, RTL status).

    Args:
        *translation_keys: Optional strings to translate and include

    Returns:
        HTML script tag with i18n setup

    Example:
        {% load djust_i18n %}

        <!-- Basic setup (no translations, just metadata) -->
        {% djust_i18n_js %}

        <!-- Include specific translations -->
        {% djust_i18n_js "Hello" "Goodbye" "Save" "Cancel" %}

        JavaScript usage:
        window.djust.i18n.get('Hello')  // Returns translated string
        window.djust.i18n.lang  // Current language code
        window.djust.i18n.isRtl  // Boolean
        window.djust.i18n.dir  // 'rtl' or 'ltr'
    """
    current_lang = get_language() or settings.LANGUAGE_CODE

    # Get available languages from settings
    available_languages = getattr(settings, "LANGUAGES", [("en", "English")])

    # Build translations dict
    translations: Dict[str, str] = {}
    for key in translation_keys:
        translations[key] = _(key)

    # Determine RTL status
    base_lang = current_lang.split("-")[0].lower()
    is_rtl = base_lang in RTL_LANGUAGES

    # Build the i18n config object
    i18n_config = {
        "lang": current_lang,
        "isRtl": is_rtl,
        "dir": "rtl" if is_rtl else "ltr",
        "translations": translations,
        "availableLanguages": [
            {"code": code, "name": name}
            for code, name in available_languages
        ],
        "rtlLanguages": RTL_LANGUAGES,
    }

    # Generate the script
    script = f"""<script>
(function() {{
  window.djust = window.djust || {{}};
  window.djust.i18n = {{
    _config: {json.dumps(i18n_config, ensure_ascii=False)},

    // Get current language
    get lang() {{ return this._config.lang; }},

    // Check if current language is RTL
    get isRtl() {{ return this._config.isRtl; }},

    // Get text direction
    get dir() {{ return this._config.dir; }},

    // Get available languages
    get availableLanguages() {{ return this._config.availableLanguages; }},

    // Get a translation by key
    get: function(key, fallback) {{
      return this._config.translations[key] || fallback || key;
    }},

    // Check if a language is RTL
    isRtlLanguage: function(lang) {{
      var baseLang = (lang || '').split('-')[0].toLowerCase();
      return this._config.rtlLanguages.indexOf(baseLang) !== -1;
    }},

    // Add translations dynamically
    addTranslations: function(translations) {{
      Object.assign(this._config.translations, translations);
    }},

    // Update current language (called by server)
    _setLanguage: function(lang, dir, isRtl) {{
      this._config.lang = lang;
      this._config.dir = dir;
      this._config.isRtl = isRtl;

      // Update HTML attributes
      document.documentElement.lang = lang;
      document.documentElement.dir = dir;

      // Dispatch event for listeners
      window.dispatchEvent(new CustomEvent('djust:language-changed', {{
        detail: {{ lang: lang, dir: dir, isRtl: isRtl }}
      }}));
    }}
  }};

  // Set initial HTML attributes
  document.documentElement.lang = window.djust.i18n.lang;
  document.documentElement.dir = window.djust.i18n.dir;
}})();
</script>"""

    return mark_safe(script)


@register.simple_tag(takes_context=True)
def djust_translations_json(context, *translation_keys):
    """
    Output just the translations as a JSON object (without script tag).

    Useful when you need to embed translations in a data attribute or
    pass them to a JavaScript framework.

    Args:
        *translation_keys: Strings to translate

    Returns:
        JSON string of translations

    Example:
        <div data-translations='{% djust_translations_json "Save" "Cancel" %}'>
    """
    translations: Dict[str, str] = {}
    for key in translation_keys:
        translations[key] = _(key)
    return mark_safe(json.dumps(translations, ensure_ascii=False))


@register.simple_tag(takes_context=True)
def djust_language_select(context, **attrs):
    """
    Render a language selector dropdown with dj-click for live switching.

    Args:
        **attrs: HTML attributes for the <select> element

    Returns:
        HTML for language selector

    Example:
        {% load djust_i18n %}
        {% djust_language_select class="form-select" id="lang-picker" %}

        <!-- The view needs an event handler: -->
        @event_handler
        def change_language(self, lang):
            self.set_language(lang)
    """
    current_lang = get_language() or settings.LANGUAGE_CODE
    available_languages = getattr(settings, "LANGUAGES", [("en", "English")])

    # Build attributes string
    attr_parts = []
    for key, value in attrs.items():
        attr_parts.append(f'{key}="{value}"')

    # Add dj-change for live updates (if not already specified)
    if "dj-change" not in attrs:
        attr_parts.append('dj-change="change_language"')

    attrs_str = " ".join(attr_parts)

    # Build options
    options = []
    for code, name in available_languages:
        selected = 'selected' if code == current_lang else ''
        options.append(f'<option value="{code}" {selected}>{name}</option>')

    options_html = "\n".join(options)

    html = f"""<select {attrs_str}>
{options_html}
</select>"""

    return mark_safe(html)


@register.simple_tag(takes_context=True)
def djust_language_buttons(context, **attrs):
    """
    Render language switcher as buttons instead of dropdown.

    Args:
        **attrs: HTML attributes applied to each button
            - class: CSS class for buttons (default: uses active/inactive distinction)
            - active_class: CSS class for active language button
            - inactive_class: CSS class for inactive language buttons

    Returns:
        HTML for language buttons

    Example:
        {% load djust_i18n %}
        {% djust_language_buttons class="btn btn-sm" active_class="btn-primary" inactive_class="btn-outline-secondary" %}
    """
    current_lang = get_language() or settings.LANGUAGE_CODE
    available_languages = getattr(settings, "LANGUAGES", [("en", "English")])

    # Get CSS classes
    base_class = attrs.pop("class", "")
    active_class = attrs.pop("active_class", "active")
    inactive_class = attrs.pop("inactive_class", "")

    # Build extra attributes
    attr_parts = []
    for key, value in attrs.items():
        attr_parts.append(f'{key}="{value}"')
    extra_attrs = " ".join(attr_parts)

    buttons = []
    for code, name in available_languages:
        is_active = code == current_lang
        css_class = f"{base_class} {active_class if is_active else inactive_class}".strip()
        aria_current = 'aria-current="true"' if is_active else ''

        button = f'''<button type="button"
            class="{css_class}"
            dj-click="change_language"
            dj-value-lang="{code}"
            {aria_current}
            {extra_attrs}>{name}</button>'''
        buttons.append(button)

    return mark_safe("\n".join(buttons))


@register.inclusion_tag("djust/i18n/language_menu.html", takes_context=True)
def djust_language_menu(context, **kwargs):
    """
    Render a dropdown menu for language selection.

    Requires template: templates/djust/i18n/language_menu.html

    Args:
        **kwargs: Extra context variables

    Example:
        {% load djust_i18n %}
        {% djust_language_menu %}
    """
    current_lang = get_language() or settings.LANGUAGE_CODE
    available_languages = getattr(settings, "LANGUAGES", [("en", "English")])

    # Find current language name
    current_lang_name = current_lang
    for code, name in available_languages:
        if code == current_lang:
            current_lang_name = name
            break

    return {
        "current_lang": current_lang,
        "current_lang_name": current_lang_name,
        "available_languages": available_languages,
        **kwargs
    }


@register.simple_tag(takes_context=True)
def djust_rtl_class(context, rtl_class: str = "rtl", ltr_class: str = "ltr"):
    """
    Output appropriate CSS class based on text direction.

    Args:
        rtl_class: Class to output for RTL languages
        ltr_class: Class to output for LTR languages

    Returns:
        CSS class string

    Example:
        <div class="container {% djust_rtl_class 'text-right' 'text-left' %}">
    """
    current_lang = get_language() or settings.LANGUAGE_CODE
    base_lang = current_lang.split("-")[0].lower()
    is_rtl = base_lang in RTL_LANGUAGES
    return rtl_class if is_rtl else ltr_class


@register.simple_tag
def djust_text_dir():
    """
    Output current text direction ('rtl' or 'ltr').

    Example:
        <div dir="{% djust_text_dir %}">Content</div>
    """
    current_lang = get_language() or settings.LANGUAGE_CODE
    base_lang = current_lang.split("-")[0].lower()
    return "rtl" if base_lang in RTL_LANGUAGES else "ltr"


@register.simple_tag
def djust_is_rtl():
    """
    Return True if current language is RTL, False otherwise.

    Example:
        {% if djust_is_rtl %}
            <link rel="stylesheet" href="{% static 'css/rtl.css' %}">
        {% endif %}
    """
    current_lang = get_language() or settings.LANGUAGE_CODE
    base_lang = current_lang.split("-")[0].lower()
    return base_lang in RTL_LANGUAGES


@register.filter
def format_number_i18n(value, locale=None):
    """
    Filter to format a number with locale-aware formatting.

    Args:
        value: Number to format
        locale: Optional locale override

    Example:
        {{ total|format_number_i18n }}
        {{ price|format_number_i18n:"de" }}
    """
    from djust.i18n.formatting import format_number
    lang = locale or get_language() or "en"
    return format_number(value, locale=lang)


@register.filter
def format_currency_i18n(value, currency_and_locale=None):
    """
    Filter to format currency with locale-aware formatting.

    Args:
        value: Amount to format
        currency_and_locale: "CURRENCY" or "CURRENCY:locale"

    Example:
        {{ price|format_currency_i18n:"USD" }}
        {{ price|format_currency_i18n:"EUR:de" }}
    """
    from djust.i18n.formatting import format_currency

    currency = "USD"
    locale = get_language() or "en"

    if currency_and_locale:
        parts = currency_and_locale.split(":")
        currency = parts[0]
        if len(parts) > 1:
            locale = parts[1]

    return format_currency(value, currency=currency, locale=locale)


@register.filter
def format_date_i18n(value, format_and_locale=None):
    """
    Filter to format a date with locale-aware formatting.

    Args:
        value: Date to format
        format_and_locale: "format" or "format:locale"

    Example:
        {{ event_date|format_date_i18n:"medium" }}
        {{ event_date|format_date_i18n:"long:de" }}
    """
    from djust.i18n.formatting import format_date

    format_str = "medium"
    locale = get_language() or "en"

    if format_and_locale:
        parts = format_and_locale.split(":")
        format_str = parts[0]
        if len(parts) > 1:
            locale = parts[1]

    return format_date(value, format=format_str, locale=locale)


@register.filter
def format_datetime_i18n(value, format_and_locale=None):
    """
    Filter to format a datetime with locale-aware formatting.

    Args:
        value: Datetime to format
        format_and_locale: "format" or "format:locale"

    Example:
        {{ created_at|format_datetime_i18n:"short" }}
        {{ updated_at|format_datetime_i18n:"long:fr" }}
    """
    from djust.i18n.formatting import format_datetime

    format_str = "medium"
    locale = get_language() or "en"

    if format_and_locale:
        parts = format_and_locale.split(":")
        format_str = parts[0]
        if len(parts) > 1:
            locale = parts[1]

    return format_datetime(value, format=format_str, locale=locale)


@register.filter
def format_percent_i18n(value, locale=None):
    """
    Filter to format a percentage with locale-aware formatting.

    Args:
        value: Decimal value (0.5 = 50%)
        locale: Optional locale override

    Example:
        {{ completion|format_percent_i18n }}
        {{ rate|format_percent_i18n:"de" }}
    """
    from djust.i18n.formatting import format_percent
    lang = locale or get_language() or "en"
    return format_percent(value, locale=lang)
