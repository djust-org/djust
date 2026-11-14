"""Inventory of a project's LiveViews and their declared authorization.

The public form of what ``manage.py djust_audit`` reads (#4129). Use it in a
test that every view declares access control, or to build a report::

    from djust.introspection import live_views, view_auth_info

    def test_every_view_declares_access():
        for view in live_views(app_label="crm"):
            assert view_auth_info(view), view.__qualname__

Everything here reads classes only: nothing is instantiated or mounted, and
the URL configuration is not consulted. Views are found by subclass walk, so
a view's module must have been imported: loading the URL configuration
(``django.urls.get_resolver().url_patterns``) imports the routed views.
"""

from __future__ import annotations

from typing import Any, List, Optional

from djust.management._introspect import app_label_for_class, is_user_class, walk_subclasses

__all__ = [
    "app_label_for_class",
    "is_user_class",
    "live_components",
    "live_views",
    "view_auth_info",
    "walk_subclasses",
]


def _project_classes(base: type, app_label: Optional[str]) -> List[type]:
    found = [
        cls
        for cls in walk_subclasses(base)
        if is_user_class(cls) and (app_label is None or app_label_for_class(cls) == app_label)
    ]
    found.sort(key=lambda cls: (cls.__module__, cls.__qualname__))
    return found


def live_views(app_label: Optional[str] = None) -> List[type]:
    """Every ``LiveView`` subclass the project defines, sorted by dotted path.

    Framework classes (those in ``djust`` modules) are left out, as
    ``djust_audit`` leaves them out; abstract intermediate bases the project
    defines are included.

    Args:
        app_label: Only the classes whose module path starts with this
            package (``"crm"`` for ``crm.views.DealView``).

    Returns:
        The classes, sorted by ``(module, qualname)``.
    """
    from djust.live_view import LiveView

    return _project_classes(LiveView, app_label)


def live_components(app_label: Optional[str] = None) -> List[type]:
    """Every ``LiveComponent`` subclass the project defines, as :func:`live_views`."""
    from djust.components.base import LiveComponent

    return _project_classes(LiveComponent, app_label)


def view_auth_info(view_class: type) -> dict[str, Any]:
    """The authorization a LiveView (or LiveComponent) class declares.

    The same reading ``djust_audit`` reports. Keys, each present only when it
    applies:

    * ``login_required``: ``True`` when the class sets ``login_required``.
    * ``permission_required``: the permissions that are all required.
    * ``permission_required_any``: one list per ``any_of(...)`` group, of
      which one permission is required.
    * ``custom_check``: ``True`` when a project class overrides
      ``check_permissions``.
    * ``dispatch_mixin``: ``True`` when nothing above applies but a mixin
      looks like it authorizes in ``dispatch()`` (a ``LoginRequired...`` /
      ``PermissionRequired...`` mixin, or a ``dispatch`` override).

    An empty dict means the class declares no authorization of its own.
    Authorization added from outside the class (a ``live_session`` hook, a
    URL decorator, middleware) is not seen.
    """
    from djust.management.commands.djust_audit import _extract_auth_info

    info: dict[str, Any] = _extract_auth_info(view_class)
    return info
