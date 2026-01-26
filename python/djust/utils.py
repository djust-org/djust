"""
Utility functions for djust.
"""

from functools import lru_cache


@lru_cache(maxsize=1)
def _get_template_dirs_cached() -> tuple[str, ...]:
    """
    Internal cached implementation.

    Reads from settings.TEMPLATES directly for compatibility with tests
    that modify settings. Django's template.engines singleton doesn't
    reflect settings changes after first access.
    """
    from django.conf import settings
    from pathlib import Path

    template_dirs = []

    # Step 1: Add DIRS from all TEMPLATES configs
    for template_config in settings.TEMPLATES:
        if "DIRS" in template_config:
            template_dirs.extend(template_config["DIRS"])

    # Step 2: Add app template directories (only for DjangoTemplates with APP_DIRS=True)
    for template_config in settings.TEMPLATES:
        if template_config["BACKEND"] == "django.template.backends.django.DjangoTemplates":
            if template_config.get("APP_DIRS", False):
                from django.apps import apps

                for app_config in apps.get_app_configs():
                    templates_dir = Path(app_config.path) / "templates"
                    if templates_dir.exists():
                        template_dirs.append(str(templates_dir))

    return tuple(str(d) for d in template_dirs)


def get_template_dirs() -> list[str]:
    """
    Get template directories from Django settings in search order.

    Returns list of template directory paths in Django's search order:
    1. DIRS from each TEMPLATES config (in order)
    2. APP_DIRS (if enabled) - searches app templates in app order

    Used internally for {% include %} tag support in Rust rendering.

    Note: Results are cached for performance. In production, template
    directories don't change at runtime so this is safe. Call
    clear_template_dirs_cache() if you need to refresh the cache.
    """
    return list(_get_template_dirs_cached())


def clear_template_dirs_cache() -> None:
    """
    Clear the template directories cache.

    Call this if you dynamically modify TEMPLATES settings and need
    the changes to be reflected in template rendering.

    Note: This is rarely needed in production since template directories
    typically don't change at runtime.
    """
    _get_template_dirs_cached.cache_clear()
