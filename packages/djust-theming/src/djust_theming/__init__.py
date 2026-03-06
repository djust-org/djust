"""
djust-theming: CSS theming utilities and base stylesheets for djust applications.

Provides dark/light mode support, design tokens, and component styles
via CSS variables and a lightweight JS theme switcher.

Add to INSTALLED_APPS to make static files available::

    INSTALLED_APPS = [
        ...
        "djust_theming",
    ]

Then in your base template::

    {% load static %}
    <link rel="stylesheet" href="{% static 'djust_theming/css/theme.css' %}">
    <link rel="stylesheet" href="{% static 'djust_theming/css/components.css' %}">
    <link rel="stylesheet" href="{% static 'djust_theming/css/utilities.css' %}">
    <script src="{% static 'djust_theming/js/theme-switcher.js' %}"></script>
"""

default_app_config = "djust_theming.apps.DjustThemingConfig"
