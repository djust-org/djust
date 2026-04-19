"""
Decorators for djust admin_ext.
"""

from functools import wraps


def register(*models, site=None):
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

    def decorator(admin_class):
        admin_site = site or default_site

        if not models:
            raise ValueError("At least one model must be provided to register()")

        for model in models:
            admin_site.register(model, admin_class)

        return admin_class

    return decorator


def action(description=None, permissions=None):
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

    def decorator(func):
        func.short_description = description or func.__name__.replace("_", " ").title()
        func.allowed_permissions = permissions or []

        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        return wrapper

    return decorator


def display(description=None, ordering=None, boolean=False, empty_value="-"):
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

    def decorator(func):
        func.short_description = description or func.__name__.replace("_", " ").title()
        func.admin_order_field = ordering
        func.boolean = boolean
        func.empty_value_display = empty_value

        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if result is None:
                return empty_value
            return result

        return wrapper

    return decorator
