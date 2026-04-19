"""
Django template loader for installable theme packages.

ThemePackageLoader discovers templates from pip-installed theme packages
that register a ThemeManifest with a ``templates_dir``. This allows
``{% theme_button %}`` etc. to resolve theme-specific templates from
third-party packages without requiring them to be in INSTALLED_APPS.

Usage in settings.py::

    TEMPLATES = [{
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "OPTIONS": {
            "loaders": [
                "djust.theming.loaders.ThemePackageLoader",
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
        },
    }]
"""

from pathlib import Path

from django.template.loaders.filesystem import Loader as FilesystemLoader


def get_installed_template_dirs() -> list[Path]:
    """Return template directories from all registered theme manifests.

    Queries the ThemeRegistry for manifests that have a non-None
    ``templates_dir`` and returns a list of those paths.
    """
    from .registry import get_registry

    registry = get_registry()
    dirs: list[Path] = []
    for manifest in registry.list_manifests().values():
        if manifest.templates_dir is not None:
            dirs.append(manifest.templates_dir)
    return dirs


class ThemePackageLoader(FilesystemLoader):
    """Django template loader that searches installed theme package template dirs.

    Extends the filesystem loader to dynamically include template directories
    from registered theme packages (discovered via ``ThemeRegistry``).
    """

    def get_dirs(self) -> list[Path]:
        """Return template directories from installed theme packages."""
        return get_installed_template_dirs()
