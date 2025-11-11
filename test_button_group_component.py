"""
Test ButtonGroup component - stateless, high-performance button groups.

Run with: python test_button_group_component.py
"""

import os
import sys
import django
from django.conf import settings

# Configure Django settings before importing djust components
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
            'APP_DIRS': True,
        }],
        LIVEVIEW_CONFIG={
            'css_framework': 'bootstrap5',
        }
    )
    django.setup()

from python.djust.components.ui import ButtonGroup


def test_basic_button_group():
    """Test basic button group rendering"""
    print("Test 1: Basic Button Group")
    buttons = [
        {'label': 'Left', 'variant': 'primary'},
        {'label': 'Middle', 'variant': 'primary'},
        {'label': 'Right', 'variant': 'primary'},
    ]
    group = ButtonGroup(buttons=buttons)
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'btn-group' in html
    assert 'Left' in html
    assert 'Middle' in html
    assert 'Right' in html
    assert 'btn-primary' in html
    print("✓ Basic button group works\n")


def test_button_group_with_active():
    """Test button group with active button"""
    print("Test 2: Button Group with Active Button")
    buttons = [
        {'label': 'One', 'variant': 'primary'},
        {'label': 'Two', 'variant': 'primary', 'active': True},
        {'label': 'Three', 'variant': 'primary'},
    ]
    group = ButtonGroup(buttons=buttons)
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'active' in html
    print("✓ Active button works\n")


def test_button_group_with_disabled():
    """Test button group with disabled button"""
    print("Test 3: Button Group with Disabled Button")
    buttons = [
        {'label': 'Active', 'variant': 'primary'},
        {'label': 'Disabled', 'variant': 'primary', 'disabled': True},
    ]
    group = ButtonGroup(buttons=buttons)
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'disabled' in html
    print("✓ Disabled button works\n")


def test_button_group_small():
    """Test small button group"""
    print("Test 4: Small Button Group")
    buttons = [
        {'label': 'Small 1', 'variant': 'secondary'},
        {'label': 'Small 2', 'variant': 'secondary'},
    ]
    group = ButtonGroup(buttons=buttons, size='sm')
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'btn-group-sm' in html
    print("✓ Small button group works\n")


def test_button_group_large():
    """Test large button group"""
    print("Test 5: Large Button Group")
    buttons = [
        {'label': 'Large 1', 'variant': 'success'},
        {'label': 'Large 2', 'variant': 'success'},
    ]
    group = ButtonGroup(buttons=buttons, size='lg')
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'btn-group-lg' in html
    print("✓ Large button group works\n")


def test_button_group_vertical():
    """Test vertical button group"""
    print("Test 6: Vertical Button Group")
    buttons = [
        {'label': 'Top', 'variant': 'info'},
        {'label': 'Middle', 'variant': 'info'},
        {'label': 'Bottom', 'variant': 'info'},
    ]
    group = ButtonGroup(buttons=buttons, vertical=True)
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'btn-group-vertical' in html
    print("✓ Vertical button group works\n")


def test_button_group_toolbar():
    """Test button group toolbar mode"""
    print("Test 7: Button Group Toolbar Mode")
    buttons = [
        {'label': 'Bold', 'variant': 'outline-primary'},
        {'label': 'Italic', 'variant': 'outline-primary'},
    ]
    group = ButtonGroup(buttons=buttons, role='toolbar')
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'role="toolbar"' in html
    print("✓ Toolbar mode works\n")


def test_button_group_mixed_variants():
    """Test button group with mixed variants"""
    print("Test 8: Button Group with Mixed Variants")
    buttons = [
        {'label': 'Primary', 'variant': 'primary'},
        {'label': 'Success', 'variant': 'success'},
        {'label': 'Danger', 'variant': 'danger'},
    ]
    group = ButtonGroup(buttons=buttons)
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'btn-primary' in html
    assert 'btn-success' in html
    assert 'btn-danger' in html
    print("✓ Mixed variants work\n")


def test_button_group_complex():
    """Test complex button group with multiple states"""
    print("Test 9: Complex Button Group")
    buttons = [
        {'label': 'Active Primary', 'variant': 'primary', 'active': True},
        {'label': 'Normal Secondary', 'variant': 'secondary'},
        {'label': 'Disabled Danger', 'variant': 'danger', 'disabled': True},
    ]
    group = ButtonGroup(buttons=buttons, size='lg')
    html = group.render()
    print(f"HTML: {html}\n")
    assert 'active' in html
    assert 'disabled' in html
    assert 'btn-group-lg' in html
    print("✓ Complex button group works\n")


def test_button_group_rendering_speed():
    """Test button group rendering performance"""
    print("Test 10: Button Group Rendering Speed")
    import time

    buttons = [
        {'label': f'Button {i}', 'variant': 'primary'}
        for i in range(10)
    ]

    # Warm-up render
    group = ButtonGroup(buttons=buttons)
    group.render()

    # Time 1000 renders
    start = time.perf_counter()
    for _ in range(1000):
        group = ButtonGroup(buttons=buttons)
        html = group.render()
    end = time.perf_counter()

    avg_time_us = (end - start) * 1000000 / 1000
    print(f"Average render time: {avg_time_us:.2f} μs per render")
    print(f"Total time for 1000 renders: {(end - start) * 1000:.2f} ms\n")
    print("✓ Performance test complete\n")


def run_all_tests():
    """Run all button group tests"""
    print("=" * 60)
    print("ButtonGroup Component Tests")
    print("=" * 60)
    print()

    try:
        test_basic_button_group()
        test_button_group_with_active()
        test_button_group_with_disabled()
        test_button_group_small()
        test_button_group_large()
        test_button_group_vertical()
        test_button_group_toolbar()
        test_button_group_mixed_variants()
        test_button_group_complex()
        test_button_group_rendering_speed()

        print("=" * 60)
        print("All ButtonGroup tests passed! ✓")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        raise
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        raise


if __name__ == '__main__':
    run_all_tests()
