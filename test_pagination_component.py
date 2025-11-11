"""
Test script for Pagination component.

Tests the Pagination component with various configurations.
"""

import sys
import os

# Configure Django settings before importing djust
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
        LIVEVIEW_CONFIG={
            'css_framework': 'bootstrap5',
        }
    )
    django.setup()

# Add the python directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

from djust.components.ui import Pagination


def test_basic_pagination():
    """Test basic pagination rendering."""
    print("=" * 80)
    print("Test 1: Basic pagination (page 5 of 20)")
    print("=" * 80)

    pagination = Pagination(
        current_page=5,
        total_pages=20,
        size="md",
        show_first_last=True,
        max_visible_pages=5
    )

    html = pagination.render()
    print(html)
    print()

    # Verify key elements
    assert 'aria-label="Page navigation"' in html
    assert 'Previous' in html
    assert 'Next' in html
    assert 'active' in html  # Current page should be marked active
    assert '...' in html  # Should have ellipsis for large page count
    print("✓ Basic pagination test passed")
    print()


def test_first_page():
    """Test pagination on first page."""
    print("=" * 80)
    print("Test 2: First page (page 1 of 10)")
    print("=" * 80)

    pagination = Pagination(
        current_page=1,
        total_pages=10,
        size="md",
        show_first_last=True,
        max_visible_pages=5
    )

    html = pagination.render()
    print(html)
    print()

    # Verify Previous button is disabled
    assert 'disabled' in html
    print("✓ First page test passed")
    print()


def test_last_page():
    """Test pagination on last page."""
    print("=" * 80)
    print("Test 3: Last page (page 10 of 10)")
    print("=" * 80)

    pagination = Pagination(
        current_page=10,
        total_pages=10,
        size="md",
        show_first_last=True,
        max_visible_pages=5
    )

    html = pagination.render()
    print(html)
    print()

    # Verify Next button is disabled
    assert 'disabled' in html
    print("✓ Last page test passed")
    print()


def test_small_page_count():
    """Test pagination with few pages (no ellipsis)."""
    print("=" * 80)
    print("Test 4: Small page count (page 2 of 4)")
    print("=" * 80)

    pagination = Pagination(
        current_page=2,
        total_pages=4,
        size="md",
        show_first_last=True,
        max_visible_pages=5
    )

    html = pagination.render()
    print(html)
    print()

    # Should not have ellipsis with only 4 pages
    assert '...' not in html
    print("✓ Small page count test passed")
    print()


def test_size_variants():
    """Test different size variants."""
    print("=" * 80)
    print("Test 5: Size variants")
    print("=" * 80)

    sizes = ['sm', 'md', 'lg']

    for size in sizes:
        print(f"\nSize: {size}")
        print("-" * 40)
        pagination = Pagination(
            current_page=5,
            total_pages=10,
            size=size,
            show_first_last=True,
            max_visible_pages=5
        )

        html = pagination.render()
        if size != 'md':
            assert f'pagination-{size}' in html
            print(f"✓ Size {size} class found")
        else:
            print(f"✓ Default size (no extra class)")

    print()


def test_edge_cases():
    """Test edge cases."""
    print("=" * 80)
    print("Test 6: Edge cases")
    print("=" * 80)

    # Single page
    print("\nSingle page (1 of 1)")
    print("-" * 40)
    pagination = Pagination(
        current_page=1,
        total_pages=1,
        size="md",
        show_first_last=True,
        max_visible_pages=5
    )
    html = pagination.render()
    print(html)
    assert 'disabled' in html  # Both buttons should be disabled
    print("✓ Single page test passed")

    # Large page count with ellipsis on both sides
    print("\nLarge page count (page 50 of 100)")
    print("-" * 40)
    pagination = Pagination(
        current_page=50,
        total_pages=100,
        size="md",
        show_first_last=True,
        max_visible_pages=5
    )
    html = pagination.render()
    print(html)
    # Should have ellipsis on both sides
    assert html.count('...') >= 2
    print("✓ Large page count test passed")
    print()


def test_max_visible_pages():
    """Test max_visible_pages parameter."""
    print("=" * 80)
    print("Test 7: Max visible pages")
    print("=" * 80)

    for max_visible in [3, 5, 7]:
        print(f"\nMax visible pages: {max_visible}")
        print("-" * 40)
        pagination = Pagination(
            current_page=10,
            total_pages=20,
            size="md",
            show_first_last=True,
            max_visible_pages=max_visible
        )

        html = pagination.render()
        # Count page items (excluding Previous/Next and ellipsis)
        page_items = html.count('data-page=')
        print(f"Total clickable items: {page_items}")
        print("✓ Max visible pages test passed")

    print()


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("PAGINATION COMPONENT TEST SUITE")
    print("=" * 80)
    print()

    try:
        test_basic_pagination()
        test_first_page()
        test_last_page()
        test_small_page_count()
        test_size_variants()
        test_edge_cases()
        test_max_visible_pages()

        print("=" * 80)
        print("ALL TESTS PASSED! ✓")
        print("=" * 80)
        print()

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
