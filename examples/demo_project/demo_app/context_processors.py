"""
Context processors for demo_app.

Provides common data to all templates.
"""

from djust.components.layout import NavbarComponent, NavItem


def navbar(request):
    """
    Provide navbar component to all templates.

    This context processor makes the navbar available globally,
    so we don't need to pass it from every view.
    """
    # Determine which nav item is active based on current path
    path = request.path

    nav_items = [
        NavItem("Home", "/", active=(path == "/")),
        NavItem("Demos", "/demos/", active=path.startswith("/demos/")),
        NavItem("Components", "/kitchen-sink/", active=path.startswith("/kitchen-sink/")),
        NavItem("Forms", "/forms/", active=path.startswith("/forms/")),
        NavItem("Docs", "/docs/", active=path.startswith("/docs/")),
        NavItem("Hosting ↗", "https://djustlive.com", external=True),
    ]

    navbar_component = NavbarComponent(
        brand_name="",  # Empty, we'll show logo only
        brand_logo="/static/images/djust.png",
        brand_href="/",
        items=nav_items,
        fixed_top=True,
        logo_height=16,
    )

    return {
        'navbar': navbar_component,
    }
