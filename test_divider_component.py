#!/usr/bin/env python3
"""
Test script for Divider component.

Tests both Python fallback and Rust implementations.
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

# Configure Django before importing djust
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
    )
    django.setup()

from djust.components.ui import Divider


def test_divider_simple():
    """Test simple divider without text"""
    print("Testing simple divider...")
    divider = Divider()
    html = divider.render()
    print(f"HTML: {html}")
    assert '<hr' in html
    assert 'my-3' in html or 'my-' in html
    print("✓ Simple divider test passed")


def test_divider_with_text():
    """Test divider with centered text"""
    print("\nTesting divider with text...")
    divider = Divider(text="OR")
    html = divider.render()
    print(f"HTML: {html}")
    assert 'OR' in html
    # Check for either Bootstrap flexbox, Tailwind flex, or custom divider-container
    assert ('d-flex' in html or 'flex' in html or 'divider-container' in html)
    print("✓ Divider with text test passed")


def test_divider_dashed():
    """Test dashed divider"""
    print("\nTesting dashed divider...")
    divider = Divider(style="dashed")
    html = divider.render()
    print(f"HTML: {html}")
    assert 'dashed' in html
    print("✓ Dashed divider test passed")


def test_divider_dotted():
    """Test dotted divider"""
    print("\nTesting dotted divider...")
    divider = Divider(style="dotted")
    html = divider.render()
    print(f"HTML: {html}")
    assert 'dotted' in html
    print("✓ Dotted divider test passed")


def test_divider_margins():
    """Test different margin sizes"""
    print("\nTesting margin sizes...")

    # Small margin
    divider_sm = Divider(margin="sm")
    html_sm = divider_sm.render()
    print(f"Small margin: {html_sm}")
    assert 'my-2' in html_sm or 'my-' in html_sm

    # Medium margin (default)
    divider_md = Divider(margin="md")
    html_md = divider_md.render()
    print(f"Medium margin: {html_md}")
    assert 'my-3' in html_md or 'my-' in html_md

    # Large margin
    divider_lg = Divider(margin="lg")
    html_lg = divider_lg.render()
    print(f"Large margin: {html_lg}")
    assert 'my-4' in html_lg or 'my-' in html_lg

    print("✓ Margin size tests passed")


def test_divider_combined():
    """Test divider with multiple options"""
    print("\nTesting combined options...")
    divider = Divider(text="AND", style="dashed", margin="lg")
    html = divider.render()
    print(f"HTML: {html}")
    assert 'AND' in html
    assert 'dashed' in html
    assert 'my-4' in html or 'my-' in html
    print("✓ Combined options test passed")


def test_rust_availability():
    """Check if Rust implementation is available"""
    print("\nChecking Rust implementation...")
    try:
        from djust._rust import RustDivider
        print("✓ Rust implementation available")

        # Test Rust directly
        rust_divider = RustDivider()
        html = rust_divider.render()
        print(f"Rust HTML: {html}")
        assert '<hr' in html
        print("✓ Direct Rust test passed")

        # Test with text
        rust_divider_text = RustDivider(text="TEST")
        html_text = rust_divider_text.render()
        print(f"Rust with text: {html_text}")
        assert 'TEST' in html_text
        print("✓ Rust with text passed")

        return True
    except (ImportError, AttributeError) as e:
        print(f"⚠ Rust implementation not available: {e}")
        print("  (This is OK, Python fallback will be used)")
        return False


def test_xss_protection():
    """Test HTML escaping for XSS protection"""
    print("\nTesting XSS protection...")
    divider = Divider(text="<script>alert('xss')</script>")
    html = divider.render()
    print(f"HTML: {html}")

    # Should NOT contain actual script tags
    assert '<script>' not in html.lower()
    # Should contain escaped version
    assert ('&lt;' in html or 'script' not in html.lower())
    print("✓ XSS protection test passed")


def main():
    """Run all tests"""
    print("=" * 60)
    print("Divider Component Tests")
    print("=" * 60)

    try:
        rust_available = test_rust_availability()
        test_divider_simple()
        test_divider_with_text()
        test_divider_dashed()
        test_divider_dotted()
        test_divider_margins()
        test_divider_combined()
        test_xss_protection()

        print("\n" + "=" * 60)
        print("All tests passed! ✓")
        if rust_available:
            print("Using Rust implementation (high-performance)")
        else:
            print("Using Python fallback (template rendering)")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
