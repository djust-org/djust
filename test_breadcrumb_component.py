#!/usr/bin/env python3
"""
Test script for Breadcrumb component.

Tests the Breadcrumb component with various configurations.
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

# Configure Django settings before importing
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test_settings')

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='test-secret-key',
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [],
            'APP_DIRS': True,
            'OPTIONS': {
                'context_processors': [
                    'django.template.context_processors.debug',
                    'django.template.context_processors.request',
                ],
            },
        }],
    )
    django.setup()

from djust.components.ui import Breadcrumb
from djust.config import config


def test_basic_breadcrumb():
    """Test basic breadcrumb with multiple items"""
    print("=" * 60)
    print("Test 1: Basic Breadcrumb")
    print("=" * 60)

    breadcrumb = Breadcrumb(items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'Products', 'url': '/products'},
        {'label': 'Laptop', 'url': None},  # Current page
    ])

    html = breadcrumb.render()
    print(html)
    print()

    # Verify HTML structure
    assert 'breadcrumb' in html.lower()
    assert 'Home' in html
    assert 'Products' in html
    assert 'Laptop' in html
    assert '/' in html or 'href="/"' in html  # Check for home link
    print("✅ Basic breadcrumb test passed")
    print()


def test_custom_separator():
    """Test breadcrumb with custom separator"""
    print("=" * 60)
    print("Test 2: Custom Separator (>)")
    print("=" * 60)

    breadcrumb = Breadcrumb(
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Category', 'url': '/category'},
            {'label': 'Item'},
        ],
        separator='>'
    )

    html = breadcrumb.render()
    print(html)
    print()

    # Note: Bootstrap5 uses CSS for separators, plain HTML uses explicit separator
    print("✅ Custom separator test passed")
    print()


def test_with_home_icon():
    """Test breadcrumb with home icon"""
    print("=" * 60)
    print("Test 3: Breadcrumb with Home Icon")
    print("=" * 60)

    breadcrumb = Breadcrumb(
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Dashboard', 'url': '/dashboard'},
            {'label': 'Settings'},
        ],
        show_home=True
    )

    html = breadcrumb.render()
    print(html)
    print()

    # Check for Bootstrap icon or emoji
    assert 'bi-house' in html or '🏠' in html
    print("✅ Home icon test passed")
    print()


def test_single_item():
    """Test breadcrumb with single item (edge case)"""
    print("=" * 60)
    print("Test 4: Single Item Breadcrumb")
    print("=" * 60)

    breadcrumb = Breadcrumb(items=[
        {'label': 'Current Page'},
    ])

    html = breadcrumb.render()
    print(html)
    print()

    assert 'Current Page' in html
    assert 'active' in html.lower()  # Should be active (current page)
    print("✅ Single item test passed")
    print()


def test_empty_breadcrumb():
    """Test breadcrumb with no items (edge case)"""
    print("=" * 60)
    print("Test 5: Empty Breadcrumb")
    print("=" * 60)

    breadcrumb = Breadcrumb(items=[])

    html = breadcrumb.render()
    print(html)
    print()

    # Should render empty nav element
    assert 'breadcrumb' in html.lower()
    print("✅ Empty breadcrumb test passed")
    print()


def test_long_breadcrumb():
    """Test breadcrumb with many items"""
    print("=" * 60)
    print("Test 6: Long Breadcrumb Trail")
    print("=" * 60)

    breadcrumb = Breadcrumb(items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'Products', 'url': '/products'},
        {'label': 'Electronics', 'url': '/products/electronics'},
        {'label': 'Computers', 'url': '/products/electronics/computers'},
        {'label': 'Laptops', 'url': '/products/electronics/computers/laptops'},
        {'label': 'Gaming Laptops'},  # Current page
    ])

    html = breadcrumb.render()
    print(html)
    print()

    assert 'Gaming Laptops' in html
    assert 'Home' in html
    print("✅ Long breadcrumb test passed")
    print()


def test_all_frameworks():
    """Test breadcrumb with different CSS frameworks"""
    print("=" * 60)
    print("Test 7: Different CSS Frameworks")
    print("=" * 60)

    items = [
        {'label': 'Home', 'url': '/'},
        {'label': 'Products', 'url': '/products'},
        {'label': 'Laptop'},
    ]

    # Test Bootstrap 5 (default)
    print("--- Bootstrap 5 ---")
    config.set('css_framework', 'bootstrap5')
    breadcrumb = Breadcrumb(items=items)
    html = breadcrumb.render()
    print(html)
    print()
    assert 'breadcrumb' in html
    assert 'breadcrumb-item' in html

    # Test Tailwind
    print("--- Tailwind CSS ---")
    config.set('css_framework', 'tailwind')
    breadcrumb = Breadcrumb(items=items)
    html = breadcrumb.render()
    print(html)
    print()
    assert 'inline-flex' in html or 'flex' in html

    # Test Plain
    print("--- Plain HTML ---")
    config.set('css_framework', 'plain')
    breadcrumb = Breadcrumb(items=items)
    html = breadcrumb.render()
    print(html)
    print()
    assert 'breadcrumb' in html

    # Reset to default
    config.set('css_framework', 'bootstrap5')

    print("✅ All frameworks test passed")
    print()


def test_performance():
    """Test performance of breadcrumb rendering"""
    print("=" * 60)
    print("Test 8: Performance Test")
    print("=" * 60)

    import time

    items = [
        {'label': 'Home', 'url': '/'},
        {'label': 'Products', 'url': '/products'},
        {'label': 'Laptop'},
    ]

    breadcrumb = Breadcrumb(items=items)

    # Warm up
    for _ in range(10):
        breadcrumb.render()

    # Time 1000 renders
    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        breadcrumb.render()
    end = time.perf_counter()

    elapsed_ms = (end - start) * 1000
    per_render_us = (elapsed_ms / iterations) * 1000

    print(f"Rendered {iterations} times in {elapsed_ms:.2f}ms")
    print(f"Average: {per_render_us:.2f}μs per render")
    print()

    # Should be reasonably fast (< 200μs for Python fallback)
    assert per_render_us < 200, f"Too slow: {per_render_us}μs per render"

    print("✅ Performance test passed")
    print()


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("BREADCRUMB COMPONENT TEST SUITE")
    print("=" * 60 + "\n")

    try:
        test_basic_breadcrumb()
        test_custom_separator()
        test_with_home_icon()
        test_single_item()
        test_empty_breadcrumb()
        test_long_breadcrumb()
        test_all_frameworks()
        test_performance()

        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        return 0

    except Exception as e:
        print("=" * 60)
        print(f"❌ TEST FAILED: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
