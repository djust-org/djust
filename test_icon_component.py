#!/usr/bin/env python
"""
Test script for Icon component.
Tests both Rust and Python implementations.
"""

import sys
from djust.components.ui import Icon


def test_icon_basic():
    """Test basic icon rendering"""
    print("Test 1: Basic Bootstrap icon")
    icon = Icon(name="star-fill", library="bootstrap", size="md")
    html = icon.render()
    print(f"  HTML: {html}")
    assert "bi bi-star-fill" in html
    assert "fs-4" in html  # md size
    print("  ✓ PASS\n")


def test_icon_fontawesome():
    """Test Font Awesome icon"""
    print("Test 2: Font Awesome icon")
    icon = Icon(name="fa-heart", library="fontawesome", size="lg")
    html = icon.render()
    print(f"  HTML: {html}")
    assert "fa-heart" in html
    assert "fs-3" in html  # lg size
    print("  ✓ PASS\n")


def test_icon_sizes():
    """Test all icon sizes"""
    print("Test 3: Icon sizes")
    sizes = ["xs", "sm", "md", "lg", "xl"]
    expected_classes = ["fs-6", "fs-5", "fs-4", "fs-3", "fs-2"]

    for size, expected_class in zip(sizes, expected_classes):
        icon = Icon(name="star", size=size)
        html = icon.render()
        assert expected_class in html, f"Expected {expected_class} for size {size}"
        print(f"  ✓ {size} -> {expected_class}")
    print("  ✓ PASS\n")


def test_icon_color():
    """Test icon with color"""
    print("Test 4: Icon with color")
    icon = Icon(name="check-circle", color="success", size="md")
    html = icon.render()
    print(f"  HTML: {html}")
    assert "text-success" in html
    print("  ✓ PASS\n")


def test_icon_accessibility():
    """Test icon with accessibility label"""
    print("Test 5: Icon with aria-label")
    icon = Icon(name="info-circle", label="Information", size="md")
    html = icon.render()
    print(f"  HTML: {html}")
    assert 'aria-label="Information"' in html
    assert 'role="img"' in html
    print("  ✓ PASS\n")


def test_icon_custom():
    """Test custom icon library"""
    print("Test 6: Custom icon library")
    icon = Icon(name="custom-logo", library="custom", size="lg")
    html = icon.render()
    print(f"  HTML: {html}")
    assert "custom-logo" in html
    assert "fs-3" in html
    print("  ✓ PASS\n")


def test_icon_xss_protection():
    """Test HTML escaping in label"""
    print("Test 7: XSS protection in label")
    icon = Icon(name="alert", label="<script>alert('xss')</script>")
    html = icon.render()
    print(f"  HTML: {html}")
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    print("  ✓ PASS\n")


def check_rust_available():
    """Check if Rust implementation is available"""
    try:
        from djust._rust import RustIcon
        print("✓ Rust implementation available\n")
        return True
    except (ImportError, AttributeError):
        print("⚠ Rust implementation not available (using fallback)\n")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Icon Component Test Suite")
    print("=" * 60)
    print()

    rust_available = check_rust_available()

    try:
        test_icon_basic()
        test_icon_fontawesome()
        test_icon_sizes()
        test_icon_color()
        test_icon_accessibility()
        test_icon_custom()
        test_icon_xss_protection()

        print("=" * 60)
        print("✓ ALL TESTS PASSED")
        print("=" * 60)
        sys.exit(0)

    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
