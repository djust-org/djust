"""
Simple test script for NavBar component (without full Django setup).

Tests various configurations of the NavBar component.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Minimal Django config
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test_settings')

from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='test-secret-key',
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [],
            'APP_DIRS': True,
        }],
    )

import django
django.setup()

from python.djust.components.ui import NavBar
from python.djust.config import config


def test_simple_navbar():
    """Test simple navbar with basic items."""
    print("\n1. Testing simple navbar...")
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
    assert 'navbar' in html, "Missing navbar class"
    assert 'MyApp' in html, "Missing brand text"
    assert 'Home' in html, "Missing Home link"
    assert 'About' in html, "Missing About link"
    assert 'Contact' in html, "Missing Contact link"
    assert 'active' in html, "Missing active class"

    print("   ✓ Simple navbar test passed")
    print(f"   Generated HTML length: {len(html)} characters")
    return html


def test_navbar_with_logo():
    """Test navbar with brand logo."""
    print("\n2. Testing navbar with logo...")
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
    assert 'MyApp' in html, "Missing brand text"
    assert '/static/logo.png' in html, "Missing logo URL"
    assert 'dark' in html.lower(), "Missing dark variant"

    print("   ✓ Navbar with logo test passed")
    return html


def test_navbar_with_dropdown():
    """Test navbar with dropdown menu."""
    print("\n3. Testing navbar with dropdown...")
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
    assert 'Services' in html, "Missing dropdown label"
    assert 'Web Development' in html, "Missing dropdown item"
    assert 'Mobile Apps' in html, "Missing dropdown item"
    assert 'Consulting' in html, "Missing dropdown item"
    assert 'dropdown' in html, "Missing dropdown class"

    print("   ✓ Navbar with dropdown test passed")
    return html


def test_sticky_navbar():
    """Test sticky navbar."""
    print("\n4. Testing sticky navbar...")
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
    assert 'sticky' in html.lower(), "Missing sticky class"

    print("   ✓ Sticky navbar test passed")
    return html


def test_dark_navbar_fluid_container():
    """Test dark navbar with fluid container."""
    print("\n5. Testing dark navbar with fluid container...")
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
    assert 'dark' in html.lower(), "Missing dark variant"
    assert 'fluid' in html, "Missing fluid container"
    assert 'Dashboard' in html, "Missing Dashboard link"

    print("   ✓ Dark navbar with fluid container test passed")
    return html


def test_navbar_with_disabled_items():
    """Test navbar with disabled items."""
    print("\n6. Testing navbar with disabled items...")
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
    assert 'disabled' in html, "Missing disabled attribute"
    assert 'Coming Soon' in html, "Missing disabled item"

    print("   ✓ Navbar with disabled items test passed")
    return html


def test_complex_navbar():
    """Test complex navbar with all features."""
    print("\n7. Testing complex navbar with all features...")
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
    assert 'Enterprise App' in html, "Missing brand text"
    assert 'Dashboard' in html, "Missing Dashboard link"
    assert 'Products' in html, "Missing Products dropdown"
    assert 'Services' in html, "Missing Services dropdown"
    assert 'dropdown' in html, "Missing dropdown class"
    assert 'dark' in html.lower(), "Missing dark variant"
    assert 'sticky' in html.lower(), "Missing sticky class"
    assert 'fluid' in html, "Missing fluid container"

    print("   ✓ Complex navbar test passed")
    print(f"   Generated HTML length: {len(html)} characters")
    return html


def test_framework_compatibility():
    """Test framework compatibility."""
    print("\n8. Testing framework compatibility...")

    # Test Bootstrap (default)
    config._config = {'css_framework': 'bootstrap5'}
    bootstrap_navbar = NavBar(
        brand={'text': 'Bootstrap Nav', 'url': '/'},
        items=[{'label': 'Home', 'url': '/'}]
    )
    bootstrap_html = bootstrap_navbar.render()
    assert 'navbar' in bootstrap_html, "Bootstrap navbar missing"
    assert 'nav-link' in bootstrap_html, "Bootstrap nav-link class missing"
    print("   ✓ Bootstrap framework test passed")

    # Test Tailwind
    config._config = {'css_framework': 'tailwind'}
    tailwind_navbar = NavBar(
        brand={'text': 'Tailwind Nav', 'url': '/'},
        items=[{'label': 'Home', 'url': '/'}]
    )
    tailwind_html = tailwind_navbar.render()
    assert 'flex' in tailwind_html or 'bg-' in tailwind_html, "Tailwind classes missing"
    print("   ✓ Tailwind framework test passed")

    # Test Plain
    config._config = {'css_framework': 'plain'}
    plain_navbar = NavBar(
        brand={'text': 'Plain Nav', 'url': '/'},
        items=[{'label': 'Home', 'url': '/'}]
    )
    plain_html = plain_navbar.render()
    assert 'navbar' in plain_html, "Plain navbar missing"
    print("   ✓ Plain framework test passed")


def main():
    """Run all navbar tests."""
    print("\n" + "=" * 60)
    print("NavBar Component Test Suite")
    print("=" * 60)

    try:
        # Run tests
        simple_html = test_simple_navbar()
        logo_html = test_navbar_with_logo()
        dropdown_html = test_navbar_with_dropdown()
        sticky_html = test_sticky_navbar()
        dark_html = test_dark_navbar_fluid_container()
        disabled_html = test_navbar_with_disabled_items()
        complex_html = test_complex_navbar()
        test_framework_compatibility()

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)

        # Print sample outputs
        print("\n" + "=" * 60)
        print("Sample Output: Simple Navbar (Bootstrap 5)")
        print("=" * 60)
        print(simple_html[:800] + "\n..." if len(simple_html) > 800 else simple_html)

        print("\n" + "=" * 60)
        print("Sample Output: Complex Navbar (Bootstrap 5)")
        print("=" * 60)
        print(complex_html[:800] + "\n..." if len(complex_html) > 800 else complex_html)

        print("\n" + "=" * 60)
        print("✅ Test Summary")
        print("=" * 60)
        print("✓ 7 feature tests passed")
        print("✓ 3 framework compatibility tests passed")
        print("✓ Total: 10/10 tests passed")
        print("=" * 60)

        return True

    except Exception as e:
        print("\n" + "=" * 60)
        print("❌ Test failed with error:")
        print("=" * 60)
        print(f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
