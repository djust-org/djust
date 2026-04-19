"""
Theme-aware Django form renderer.

Provides ``ThemeFormRenderer`` which overrides Django's default widget
templates to inject djust-theming CSS classes.  Configure in settings::

    FORM_RENDERER = "djust.theming.forms.ThemeFormRenderer"

All ``{{ form }}`` and ``{{ form.as_div }}`` calls will then render
widgets with themed classes automatically.
"""

from pathlib import Path

from django.forms.renderers import DjangoTemplates as DjangoFormRenderer
from django.template.backends.django import DjangoTemplates as DjangoBackend
from django.utils.functional import cached_property


class ThemeFormRenderer(DjangoFormRenderer):
    """
    Form renderer that applies djust-theming CSS classes to all widgets.

    Loads themed widget templates from ``djust_theming/form_templates/``
    which take priority over Django's built-in templates. The themed
    templates add CSS classes like ``theme-input``, ``theme-textarea``,
    ``theme-select``, etc., respecting the ``css_prefix`` setting.

    Usage in settings.py::

        FORM_RENDERER = "djust.theming.forms.ThemeFormRenderer"
    """

    @cached_property
    def engine(self):
        import django.forms

        theme_templates_dir = (
            Path(__file__).parent / "templates" / "djust_theming" / "form_templates"
        )
        django_forms_templates_dir = Path(django.forms.__file__).parent / "templates"

        return DjangoBackend(
            {
                "APP_DIRS": True,
                "DIRS": [
                    str(theme_templates_dir),
                    str(django_forms_templates_dir),
                ],
                "NAME": "djust_themed_forms",
                "OPTIONS": {},
            }
        )
