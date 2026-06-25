"""
Plugin system for djust admin_ext.

Provides extension points for packages to hook into the admin interface:
- AdminPlugin: Groups pages, widgets, and nav items for a package
- AdminPage: Custom LiveView page inside admin chrome
- AdminWidget: Dashboard card on admin index
- NavItem: Sidebar navigation entry

Example usage (in your_package/djust_admin.py):

    from djust.admin_ext import site
    from djust.admin_ext.plugins import AdminPlugin, AdminPage, AdminWidget

    class MyWidget(AdminWidget):
        widget_id = "my_stats"
        label = "My Stats"
        template_name = "my_package/admin/widgets/stats.html"

        def get_context(self, request):
            return {"count": 42}

    class MyPlugin(AdminPlugin):
        name = "my_package"
        verbose_name = "My Package"

        def get_widgets(self):
            return [MyWidget()]

    site.register_plugin(MyPlugin)
"""

from typing import Any, Dict, List, Optional, Type

from django.http import HttpRequest


class NavItem:
    """Sidebar navigation entry."""

    def __init__(
        self,
        label: str,
        url_name: str,
        icon: Optional[str] = None,
        order: int = 0,
        section: Optional[str] = None,
        permission: Optional[str] = None,
    ) -> None:
        self.label = label
        self.url_name = url_name
        self.icon = icon
        self.order = order
        self.section = section
        self.permission = permission

    def has_permission(self, request: HttpRequest) -> bool:
        """Check if the user has permission to see this nav item."""
        if self.permission is None:
            return True
        return bool(request.user.has_perm(self.permission))

    def __repr__(self) -> str:
        return f"NavItem(label={self.label!r}, url_name={self.url_name!r})"


class AdminPage:
    """
    Custom LiveView page within admin chrome.

    Auto-generates a NavItem unless show_in_nav=False.
    """

    def __init__(
        self,
        url_path: str,
        url_name: str,
        view_class: Type[Any],
        label: Optional[str] = None,
        icon: Optional[str] = None,
        nav_section: Optional[str] = None,
        nav_order: int = 0,
        permission: Optional[str] = None,
        show_in_nav: bool = True,
    ) -> None:
        self.url_path = url_path.strip("/")
        self.url_name = url_name
        self.view_class = view_class
        self.label = label or url_name.replace("_", " ").title()
        self.icon = icon
        self.nav_section = nav_section
        self.nav_order = nav_order
        self.permission = permission
        self.show_in_nav = show_in_nav

    def get_nav_item(self) -> Optional[NavItem]:
        """Generate a NavItem for this page."""
        if not self.show_in_nav:
            return None
        return NavItem(
            label=self.label,
            url_name=self.url_name,
            icon=self.icon,
            order=self.nav_order,
            section=self.nav_section,
            permission=self.permission,
        )

    def __repr__(self) -> str:
        return f"AdminPage(url_path={self.url_path!r}, url_name={self.url_name!r})"


class AdminWidget:
    """
    Dashboard widget on admin index.

    Subclass and override get_context() to provide data for your template.
    """

    widget_id: Optional[str] = None
    label = ""
    template_name: Optional[str] = None
    order = 0
    size = "md"  # "sm", "md", or "lg"
    permission: Optional[str] = None

    def get_context(self, request: HttpRequest) -> Dict[str, Any]:
        """Return context dict for the widget template. Override in subclasses."""
        return {}

    def has_permission(self, request: HttpRequest) -> bool:
        """Check if the user has permission to see this widget."""
        if self.permission is None:
            return True
        return bool(request.user.has_perm(self.permission))

    def render(self, request: HttpRequest) -> str:
        """Render the widget to HTML using Django's template engine."""
        from django.template.loader import render_to_string

        if not self.template_name:
            return ""
        context = self.get_context(request)
        context["widget"] = self
        return str(render_to_string(self.template_name, context, request=request))

    def __repr__(self) -> str:
        return f"AdminWidget(widget_id={self.widget_id!r}, label={self.label!r})"


class AdminPlugin:
    """
    Base class for admin extensions. One per package.

    Subclass and implement get_pages(), get_widgets() to hook into admin.
    Register via site.register_plugin(MyPlugin).
    """

    name: Optional[str] = None  # Unique identifier (required)
    verbose_name: Optional[str] = None  # Human-readable name

    def get_pages(self) -> List[AdminPage]:
        """Return list of AdminPage instances. Override in subclasses."""
        return []

    def get_widgets(self) -> List[AdminWidget]:
        """Return list of AdminWidget instances. Override in subclasses."""
        return []

    def get_nav_items(self) -> List[NavItem]:
        """
        Return list of NavItem instances for the sidebar.

        By default, auto-generates NavItems from pages that have show_in_nav=True.
        Override for custom nav items.
        """
        items: List[NavItem] = []
        for page in self.get_pages():
            nav_item = page.get_nav_item()
            if nav_item is not None:
                items.append(nav_item)
        return items

    def ready(self) -> None:
        """Called when the plugin is registered. Override for setup logic."""
        pass

    def __repr__(self) -> str:
        return f"AdminPlugin(name={self.name!r})"
