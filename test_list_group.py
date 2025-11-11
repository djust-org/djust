"""
Test the ListGroup component with various configurations.
"""

import os
import sys
import django
from django.conf import settings

# Configure Django settings
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
        }],
    )
    django.setup()

from python.djust.components.ui import ListGroup
from python.djust.config import config


def test_basic_list_group():
    """Test basic list group with simple items"""
    print("\n=== Test 1: Basic List Group ===")
    items = [
        {'label': 'Dashboard', 'url': '/dashboard'},
        {'label': 'Profile', 'url': '/profile'},
        {'label': 'Settings', 'url': '/settings'},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'list-group' in html
    assert 'Dashboard' in html
    assert 'Profile' in html
    assert 'Settings' in html
    print("✓ Basic list group renders correctly")


def test_active_and_disabled():
    """Test list group with active and disabled items"""
    print("\n=== Test 2: Active and Disabled Items ===")
    items = [
        {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
        {'label': 'Profile', 'url': '/profile'},
        {'label': 'Settings', 'url': '/settings', 'disabled': True},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'active' in html
    assert 'disabled' in html
    assert 'aria-disabled="true"' in html
    print("✓ Active and disabled states render correctly")


def test_flush_variant():
    """Test flush variant (no borders)"""
    print("\n=== Test 3: Flush Variant ===")
    items = [
        {'label': 'Item 1', 'url': '/item1'},
        {'label': 'Item 2', 'url': '/item2'},
    ]
    list_group = ListGroup(items=items, flush=True)
    html = list_group.render()
    print(html)
    assert 'list-group-flush' in html
    print("✓ Flush variant renders correctly")


def test_numbered_list():
    """Test numbered list"""
    print("\n=== Test 4: Numbered List ===")
    items = [
        {'label': 'First step', 'url': '/step1'},
        {'label': 'Second step', 'url': '/step2'},
        {'label': 'Third step', 'url': '/step3'},
    ]
    list_group = ListGroup(items=items, numbered=True)
    html = list_group.render()
    print(html)
    assert '<ol' in html
    assert 'list-group-numbered' in html
    print("✓ Numbered list renders correctly")


def test_color_variants():
    """Test list items with color variants"""
    print("\n=== Test 5: Color Variants ===")
    items = [
        {'label': 'Success item', 'variant': 'success'},
        {'label': 'Warning item', 'variant': 'warning'},
        {'label': 'Danger item', 'variant': 'danger'},
        {'label': 'Primary item', 'variant': 'primary'},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'list-group-item-success' in html
    assert 'list-group-item-warning' in html
    assert 'list-group-item-danger' in html
    assert 'list-group-item-primary' in html
    print("✓ Color variants render correctly")


def test_with_badges():
    """Test list items with badges"""
    print("\n=== Test 6: Items with Badges ===")
    items = [
        {
            'label': 'Messages',
            'url': '/messages',
            'badge': {'text': '5', 'variant': 'primary'}
        },
        {
            'label': 'Notifications',
            'url': '/notifications',
            'badge': {'text': '12', 'variant': 'danger'}
        },
        {
            'label': 'Updates',
            'url': '/updates',
            'badge': {'text': '3', 'variant': 'success'}
        },
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'badge' in html
    assert '5' in html
    assert '12' in html
    assert '3' in html
    print("✓ Badges render correctly")


def test_empty_list():
    """Test empty list group"""
    print("\n=== Test 7: Empty List ===")
    items = []
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'list-group' in html
    print("✓ Empty list renders correctly")


def test_non_link_items():
    """Test list items without URLs (not clickable)"""
    print("\n=== Test 8: Non-link Items ===")
    items = [
        {'label': 'Static item 1'},
        {'label': 'Static item 2'},
        {'label': 'Static item 3'},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert '<li' in html
    assert 'Static item 1' in html
    print("✓ Non-link items render correctly")


def test_mixed_items():
    """Test mix of links and non-links"""
    print("\n=== Test 9: Mixed Items ===")
    items = [
        {'label': 'Link item', 'url': '/link'},
        {'label': 'Non-link item'},
        {'label': 'Active link', 'url': '/active', 'active': True},
        {'label': 'Disabled item', 'disabled': True},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert '<a href="/link"' in html
    assert '<li' in html
    print("✓ Mixed items render correctly")


def test_complex_example():
    """Test complex example with all features"""
    print("\n=== Test 10: Complex Example ===")
    items = [
        {
            'label': 'Dashboard',
            'url': '/dashboard',
            'active': True,
            'badge': {'text': 'New', 'variant': 'primary'}
        },
        {
            'label': 'Messages',
            'url': '/messages',
            'badge': {'text': '5', 'variant': 'danger'}
        },
        {
            'label': 'Important Notice',
            'variant': 'warning'
        },
        {
            'label': 'Settings',
            'url': '/settings',
            'disabled': True
        },
    ]
    list_group = ListGroup(items=items, numbered=False, flush=False)
    html = list_group.render()
    print(html)
    assert 'Dashboard' in html
    assert 'active' in html
    assert 'badge' in html
    assert 'list-group-item-warning' in html
    assert 'disabled' in html
    print("✓ Complex example renders correctly")


def test_tailwind_framework():
    """Test with Tailwind CSS framework"""
    print("\n=== Test 11: Tailwind Framework ===")
    config.set('css_framework', 'tailwind')
    items = [
        {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
        {'label': 'Profile', 'url': '/profile'},
        {'label': 'Settings', 'url': '/settings', 'disabled': True},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'divide-y' in html or 'bg-blue-600' in html  # Tailwind classes
    print("✓ Tailwind framework renders correctly")
    # Reset to default
    config.set('css_framework', 'bootstrap5')


def test_plain_framework():
    """Test with plain HTML framework"""
    print("\n=== Test 12: Plain Framework ===")
    config.set('css_framework', 'plain')
    items = [
        {'label': 'Dashboard', 'url': '/dashboard'},
        {'label': 'Profile', 'url': '/profile'},
    ]
    list_group = ListGroup(items=items)
    html = list_group.render()
    print(html)
    assert 'list-group' in html
    print("✓ Plain framework renders correctly")
    # Reset to default
    config.set('css_framework', 'bootstrap5')


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Testing ListGroup Component")
    print("="*60)

    try:
        test_basic_list_group()
        test_active_and_disabled()
        test_flush_variant()
        test_numbered_list()
        test_color_variants()
        test_with_badges()
        test_empty_list()
        test_non_link_items()
        test_mixed_items()
        test_complex_example()
        test_tailwind_framework()
        test_plain_framework()

        print("\n" + "="*60)
        print("✓ All tests passed!")
        print("="*60 + "\n")
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}\n")
        raise
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}\n")
        raise
