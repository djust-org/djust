"""
Decorators for Django admin LiveView integration

Provides the @live_action decorator to convert slow admin actions
into streaming actions with progress feedback.
"""

import functools
from typing import Callable, Generator, Optional, TypeVar

F = TypeVar("F", bound=Callable)


def live_action(
    description: str = "",
    permissions: Optional[list] = None,
    show_progress: bool = True,
):
    """
    Decorator to convert an admin action into a LiveView action with progress.

    The decorated function should be a generator that yields progress updates
    using self.update_progress() or plain dicts.

    Usage:
        class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
            actions = ['export_items', 'bulk_update']

            @live_action(description="Export selected items")
            def export_items(self, request, queryset):
                total = queryset.count()
                for i, item in enumerate(queryset):
                    yield self.update_progress(i + 1, total, f"Exporting {item.name}...")
                    # Export logic here
                    item.export()

                yield self.update_progress(total, total, "Export complete!", "complete")

            @live_action(description="Bulk update prices", permissions=['change_product'])
            def bulk_update(self, request, queryset):
                total = queryset.count()
                for i, item in enumerate(queryset):
                    yield {
                        'current': i + 1,
                        'total': total,
                        'percent': int((i + 1) / total * 100),
                        'message': f"Updating {item.name}...",
                    }
                    item.update_price()
                    item.save()

    Args:
        description: Human-readable description for admin UI
        permissions: List of permission codenames required (e.g., ['change_product'])
        show_progress: Whether to show progress bar UI (default: True)

    Returns:
        Decorated function with live action metadata
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(self, request, queryset):
            # Check permissions if specified
            if permissions:
                for perm in permissions:
                    full_perm = f"{self.model._meta.app_label}.{perm}"
                    if not request.user.has_perm(full_perm):
                        yield {
                            "error": f"Permission denied: {perm}",
                            "status": "error",
                        }
                        return

            # Call the original function
            result = func(self, request, queryset)

            # If it's a generator, yield from it
            if hasattr(result, "__iter__") and hasattr(result, "__next__"):
                yield from result
            else:
                # If not a generator, wrap the result
                yield {"complete": True, "result": result}

        # Mark as live action
        wrapper._is_live_action = True
        wrapper._live_action_config = {
            "description": description,
            "permissions": permissions or [],
            "show_progress": show_progress,
        }

        # Set Django admin action attributes
        wrapper.short_description = description

        return wrapper

    # Support both @live_action and @live_action() syntax
    if callable(description):
        # Called as @live_action without parentheses
        func = description
        description = ""
        return decorator(func)

    return decorator


def async_live_action(
    description: str = "",
    permissions: Optional[list] = None,
    show_progress: bool = True,
):
    """
    Async version of @live_action for async admin actions.

    Usage:
        @async_live_action(description="Async export")
        async def async_export(self, request, queryset):
            total = queryset.count()
            async for i, item in aenumerate(queryset):
                yield self.update_progress(i + 1, total)
                await item.async_export()
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(self, request, queryset):
            # Check permissions if specified
            if permissions:
                for perm in permissions:
                    full_perm = f"{self.model._meta.app_label}.{perm}"
                    if not request.user.has_perm(full_perm):
                        yield {
                            "error": f"Permission denied: {perm}",
                            "status": "error",
                        }
                        return

            # Call the original async function
            result = func(self, request, queryset)

            # If it's an async generator, yield from it
            if hasattr(result, "__aiter__"):
                async for item in result:
                    yield item
            else:
                # If not, await and wrap
                awaited = await result
                yield {"complete": True, "result": awaited}

        # Mark as live action
        wrapper._is_live_action = True
        wrapper._is_async = True
        wrapper._live_action_config = {
            "description": description,
            "permissions": permissions or [],
            "show_progress": show_progress,
        }

        # Set Django admin action attributes
        wrapper.short_description = description

        return wrapper

    return decorator


__all__ = ["live_action", "async_live_action"]
