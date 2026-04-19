"""
Template resolution with theme-specific override support.

Provides helpers that build a fallback chain of template candidates:

    1. ``djust_theming/themes/{theme_name}/components/{component}.html``
    2. ``djust_theming/components/{component}.html``

The first template found by ``django.template.loader.select_template``
wins. If no theme-specific override exists, the default ships with the
package and is always available.
"""

from django.template.loader import select_template

from .manager import get_theme_manager


def _get_component_candidates(theme_name: str, component_name: str) -> list[str]:
    """
    Build the ordered list of template candidates for a component.

    Args:
        theme_name: Active design system theme (e.g. "material")
        component_name: Component name (e.g. "button", "card")

    Returns:
        List of template paths, theme-specific first.
    """
    return [
        f"djust_theming/themes/{theme_name}/components/{component_name}.html",
        f"djust_theming/components/{component_name}.html",
    ]


def _get_theme_template_candidates(theme_name: str, template_name: str) -> list[str]:
    """
    Build the ordered list of template candidates for a top-level theme template.

    Args:
        theme_name: Active design system theme (e.g. "material")
        template_name: Template name (e.g. "theme_switcher", "theme_head")

    Returns:
        List of template paths, theme-specific first.
    """
    return [
        f"djust_theming/themes/{theme_name}/{template_name}.html",
        f"djust_theming/{template_name}.html",
    ]


def resolve_component_template(request, component_name: str):
    """
    Resolve the template for a component, checking theme-specific override first.

    Args:
        request: Django HttpRequest (for theme state)
        component_name: e.g. "button", "card", "alert"

    Returns:
        Template object from ``select_template()``.
    """
    manager = get_theme_manager(request)
    state = manager.get_state()
    candidates = _get_component_candidates(state.theme, component_name)
    return select_template(candidates)


def _get_layout_candidates(theme_name: str, layout_name: str) -> list[str]:
    """
    Build the ordered list of template candidates for a layout.

    Args:
        theme_name: Active design system theme (e.g. "material")
        layout_name: Layout name (e.g. "base", "sidebar", "dashboard")

    Returns:
        List of template paths, theme-specific first.
    """
    return [
        f"djust_theming/themes/{theme_name}/layouts/{layout_name}.html",
        f"djust_theming/layouts/{layout_name}.html",
    ]


def resolve_layout_template(request, layout_name: str):
    """
    Resolve the template for a layout, checking theme-specific override first.

    Args:
        request: Django HttpRequest (for theme state)
        layout_name: e.g. "base", "sidebar", "dashboard"

    Returns:
        Template object from ``select_template()``.
    """
    manager = get_theme_manager(request)
    state = manager.get_state()
    candidates = _get_layout_candidates(state.theme, layout_name)
    return select_template(candidates)


def _get_page_candidates(theme_name: str, page_name: str) -> list[str]:
    """
    Build the ordered list of template candidates for a page.

    Args:
        theme_name: Active design system theme (e.g. "material")
        page_name: Page name (e.g. "login", "404", "empty_state")

    Returns:
        List of template paths, theme-specific first.
    """
    return [
        f"djust_theming/themes/{theme_name}/pages/{page_name}.html",
        f"djust_theming/pages/{page_name}.html",
    ]


def resolve_page_template(request, page_name: str):
    """
    Resolve the template for a page, checking theme-specific override first.

    Args:
        request: Django HttpRequest (for theme state)
        page_name: e.g. "login", "register", "404", "empty_state"

    Returns:
        Template object from ``select_template()``.
    """
    manager = get_theme_manager(request)
    state = manager.get_state()
    candidates = _get_page_candidates(state.theme, page_name)
    return select_template(candidates)


def resolve_theme_template(request, template_name: str):
    """
    Resolve a top-level theme template, checking theme-specific override first.

    Args:
        request: Django HttpRequest (for theme state)
        template_name: e.g. "theme_switcher"

    Returns:
        Template object from ``select_template()``.
    """
    manager = get_theme_manager(request)
    state = manager.get_state()
    candidates = _get_theme_template_candidates(state.theme, template_name)
    return select_template(candidates)
