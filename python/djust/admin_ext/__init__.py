"""
djust.admin_ext: A modern, reactive Django admin interface powered by djust.

This package provides a drop-in replacement for Django's built-in admin
with real-time updates, plugin architecture, and a modern UX.

Folded into djust core from the standalone djust-admin package.
"""

# Import adapters module to register admin_tailwind adapter
from . import adapters  # noqa: F401
from .decorators import action, display, register
from .options import DjustModelAdmin
from .plugins import AdminPage, AdminPlugin, AdminWidget, NavItem
from .sites import DjustAdminSite

# Default admin site instance
site = DjustAdminSite()


def autodiscover():
    """
    Auto-discover djust_admin.py modules in all installed apps.

    Similar to django.contrib.admin.autodiscover() but looks for
    djust_admin.py instead of admin.py to avoid conflicts.
    """
    from django.utils.module_loading import autodiscover_modules

    autodiscover_modules("djust_admin", register_to=site)


default_app_config = "djust.admin_ext.apps.DjustAdminConfig"


__all__ = [
    "DjustAdminSite",
    "DjustModelAdmin",
    "AdminPlugin",
    "AdminPage",
    "AdminWidget",
    "NavItem",
    "register",
    "action",
    "display",
    "site",
    "autodiscover",
]
