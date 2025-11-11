"""
Test script for NavBar component.

Tests various configurations of the NavBar component.
"""

# Configure Django settings before importing djust components
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'examples.demo_project.demo_project.settings')

import django
django.setup()

from python.djust.components.ui import NavBar


def test_simple_navbar():
    """Test simple navbar with basic items."""
    navbar = NavBar(
        brand={'text': 'MyApp', 'url': '/'},
        items=[
            {'label': 'Home', 'url': '/', 'active': True},
            {'label': 'About', 'url': '/about'},
            {'label': 'Contact', 'url': '/contact'},
        ]
    )

    html = navbar.render()

    # Verify key elements
    assert 'navbar' in html
    assert 'MyApp' in html
    assert 'Home' in html
    assert 'About' in html
    assert 'Contact' in html
    assert 'active' in html  # Active class for Home

    print("✓ Simple navbar test passed")
    return html


def test_navbar_with_logo():
    """Test navbar with brand logo."""
    navbar = NavBar(
        brand={'text': 'MyApp', 'url': '/', 'logo': '/static/logo.png'},
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Products', 'url': '/products'},
        ],
        variant="dark"
    )

    html = navbar.render()

    # Verify key elements
    assert 'MyApp' in html
    assert '/static/logo.png' in html
    assert 'dark' in html.lower()

    print("✓ Navbar with logo test passed")
    return html


def test_navbar_with_dropdown():
    """Test navbar with dropdown menu."""
    navbar = NavBar(
        brand={'text': 'MyApp', 'url': '/'},
        items=[
            {'label': 'Home', 'url': '/', 'active': True},
            {
                'label': 'Services',
                'dropdown': [
                    {'label': 'Web Development', 'url': '/services/web'},
                    {'label': 'Mobile Apps', 'url': '/services/mobile'},
                    {'divider': True},
                    {'label': 'Consulting', 'url': '/services/consulting'},
                ]
            },
            {'label': 'About', 'url': '/about'},
        ]
    )

    html = navbar.render()

    # Verify key elements
    assert 'Services' in html
    assert 'Web Development' in html
    assert 'Mobile Apps' in html
    assert 'Consulting' in html
    assert 'dropdown' in html
    assert 'divider' in html

    print("✓ Navbar with dropdown test passed")
    return html


def test_sticky_navbar():
    """Test sticky navbar."""
    navbar = NavBar(
        brand={'text': 'MyApp', 'url': '/'},
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Features', 'url': '/features'},
        ],
        sticky="top"
    )

    html = navbar.render()

    # Verify sticky class
    assert 'sticky' in html.lower()

    print("✓ Sticky navbar test passed")
    return html


def test_dark_navbar_fluid_container():
    """Test dark navbar with fluid container."""
    navbar = NavBar(
        brand={'text': 'MyApp', 'url': '/'},
        items=[
            {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
            {'label': 'Settings', 'url': '/settings'},
        ],
        variant="dark",
        container="fluid"
    )

    html = navbar.render()

    # Verify key elements
    assert 'dark' in html.lower()
    assert 'fluid' in html
    assert 'Dashboard' in html

    print("✓ Dark navbar with fluid container test passed")
    return html


def test_navbar_with_disabled_items():
    """Test navbar with disabled items."""
    navbar = NavBar(
        brand={'text': 'MyApp', 'url': '/'},
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Coming Soon', 'url': '#', 'disabled': True},
            {
                'label': 'Services',
                'dropdown': [
                    {'label': 'Available Service', 'url': '/services/web'},
                    {'label': 'Coming Soon', 'url': '#', 'disabled': True},
                ]
            },
        ]
    )

    html = navbar.render()

    # Verify disabled items
    assert 'disabled' in html
    assert 'Coming Soon' in html

    print("✓ Navbar with disabled items test passed")
    return html


def test_complex_navbar():
    """Test complex navbar with all features."""
    navbar = NavBar(
        brand={'text': 'Enterprise App', 'url': '/', 'logo': '/static/logo.svg'},
        items=[
            {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
            {
                'label': 'Products',
                'dropdown': [
                    {'label': 'All Products', 'url': '/products'},
                    {'label': 'New Arrivals', 'url': '/products/new'},
                    {'divider': True},
                    {'label': 'Categories', 'url': '/categories'},
                ]
            },
            {
                'label': 'Services',
                'dropdown': [
                    {'label': 'Consulting', 'url': '/services/consulting'},
                    {'label': 'Development', 'url': '/services/dev'},
                    {'label': 'Support', 'url': '/services/support'},
                ]
            },
            {'label': 'About', 'url': '/about'},
            {'label': 'Contact', 'url': '/contact'},
        ],
        variant="dark",
        sticky="top",
        container="fluid",
        expand="lg"
    )

    html = navbar.render()

    # Verify all elements are present
    assert 'Enterprise App' in html
    assert 'Dashboard' in html
    assert 'Products' in html
    assert 'Services' in html
    assert 'dropdown' in html
    assert 'dark' in html.lower()
    assert 'sticky' in html.lower()
    assert 'fluid' in html

    print("✓ Complex navbar test passed")
    return html


def main():
    """Run all navbar tests."""
    print("Testing NavBar Component")
    print("=" * 50)

    # Run tests
    simple_html = test_simple_navbar()
    logo_html = test_navbar_with_logo()
    dropdown_html = test_navbar_with_dropdown()
    sticky_html = test_sticky_navbar()
    dark_html = test_dark_navbar_fluid_container()
    disabled_html = test_navbar_with_disabled_items()
    complex_html = test_complex_navbar()

    print("\n" + "=" * 50)
    print("All tests passed! ✓")
    print("=" * 50)

    # Print sample outputs
    print("\nSample Output (Simple Navbar):")
    print("-" * 50)
    print(simple_html[:500] + "..." if len(simple_html) > 500 else simple_html)

    print("\n\nSample Output (Complex Navbar):")
    print("-" * 50)
    print(complex_html[:500] + "..." if len(complex_html) > 500 else complex_html)

    # Test framework compatibility
    print("\n\n" + "=" * 50)
    print("Testing Framework Compatibility")
    print("=" * 50)

    # Test Bootstrap (default)
    from python.djust.config import config
    config._config = {'css_framework': 'bootstrap5'}
    bootstrap_navbar = NavBar(
        brand={'text': 'Bootstrap Nav', 'url': '/'},
        items=[{'label': 'Home', 'url': '/'}]
    )
    bootstrap_html = bootstrap_navbar.render()
    assert 'navbar' in bootstrap_html
    assert 'nav-link' in bootstrap_html
    print("✓ Bootstrap framework test passed")

    # Test Tailwind
    config._config = {'css_framework': 'tailwind'}
    tailwind_navbar = NavBar(
        brand={'text': 'Tailwind Nav', 'url': '/'},
        items=[{'label': 'Home', 'url': '/'}]
    )
    tailwind_html = tailwind_navbar.render()
    assert 'flex' in tailwind_html or 'bg-' in tailwind_html
    print("✓ Tailwind framework test passed")

    # Test Plain
    config._config = {'css_framework': 'plain'}
    plain_navbar = NavBar(
        brand={'text': 'Plain Nav', 'url': '/'},
        items=[{'label': 'Home', 'url': '/'}]
    )
    plain_html = plain_navbar.render()
    assert 'navbar' in plain_html
    print("✓ Plain framework test passed")

    print("\n" + "=" * 50)
    print("Framework compatibility tests passed! ✓")
    print("=" * 50)


if __name__ == '__main__':
    main()
