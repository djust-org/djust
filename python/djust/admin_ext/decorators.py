"""
Decorators for djust admin_ext.
"""

from functools import wraps
from typing import Any, Callable, Optional, Sequence, Type


def register(
    *models: Type[Any], site: Optional[Any] = None
) -> Callable[[Type[Any]], Type[Any]]:
    """
    Register a model or models with the admin site.

    Can be used as a decorator:

        from djust.admin_ext import register, DjustModelAdmin

        @register(Article)
        class ArticleAdmin(DjustModelAdmin):
            list_display = ['title', 'author']

    Or with multiple models:

        @register(Article, Comment)
        class ContentAdmin(DjustModelAdmin):
            pass

    Or with a specific site:

        from djust.admin_ext import DjustAdminSite, register

        my_site = DjustAdminSite(name='my_admin')

        @register(Article, site=my_site)
        class ArticleAdmin(DjustModelAdmin):
            pass
    """
    from . import site as default_site

    def decorator(admin_class: Type[Any]) -> Type[Any]:
        admin_site = site or default_site

        if not models:
            raise ValueError("At least one model must be provided to register()")

        for model in models:
            admin_site.register(model, admin_class)

        return admin_class

    return decorator


def action(
    description: Optional[str] = None, permissions: Optional[Sequence[str]] = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for admin actions.

    Usage:
        @action(description="Publish selected articles")
        def publish_selected(self, request, queryset):
            queryset.update(status='published')

        @action(description="Archive", permissions=['can_archive'])
        def archive_selected(self, request, queryset):
            queryset.update(archived=True)
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        wrapper.short_description = description or func.__name__.replace("_", " ").title()  # type: ignore[attr-defined]
        wrapper.allowed_permissions = permissions or []  # type: ignore[attr-defined]
        return wrapper

    return decorator


def display(
    description: Optional[str] = None,
    ordering: Optional[str] = None,
    boolean: bool = False,
    empty_value: str = "-",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for custom display methods in list_display.

    Usage:
        @display(description="Full Name", ordering="first_name")
        def full_name(self, obj):
            return f"{obj.first_name} {obj.last_name}"

        @display(description="Active", boolean=True)
        def is_active(self, obj):
            return obj.status == 'active'
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = func(*args, **kwargs)
            if result is None:
                return empty_value
            return result

        wrapper.short_description = description or func.__name__.replace("_", " ").title()  # type: ignore[attr-defined]
        wrapper.admin_order_field = ordering  # type: ignore[attr-defined]
        wrapper.boolean = boolean  # type: ignore[attr-defined]
        wrapper.empty_value_display = empty_value  # type: ignore[attr-defined]
        return wrapper

    return decorator
