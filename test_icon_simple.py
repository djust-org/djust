#!/usr/bin/env python
"""
Standalone test for Icon component (no Django imports).
"""

import sys
import os

# Add python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

# Check if Rust implementation is available
try:
    from djust._rust import RustIcon
    print("✓ Rust implementation available")
    rust_available = True
except (ImportError, AttributeError) as e:
    print(f"⚠ Rust implementation not available: {e}")
    rust_available = False

if rust_available:
    print("\n" + "=" * 60)
    print("Testing RustIcon directly")
    print("=" * 60)

    # Test 1: Basic Bootstrap icon
    print("\nTest 1: Basic Bootstrap icon")
    icon = RustIcon("star-fill", "bootstrap", "md", None, None)
    html = icon.render()
    print(f"  HTML: {html}")
    assert "bi bi-star-fill" in html
    assert "fs-4" in html
    print("  ✓ PASS")

    # Test 2: Font Awesome
    print("\nTest 2: Font Awesome icon")
    icon = RustIcon("fa-heart", "fontawesome", "lg", None, None)
    html = icon.render()
    print(f"  HTML: {html}")
    assert "fa-heart" in html
    assert "fs-3" in html
    print("  ✓ PASS")

    # Test 3: Sizes
    print("\nTest 3: Icon sizes")
    sizes = [("xs", "fs-6"), ("sm", "fs-5"), ("md", "fs-4"), ("lg", "fs-3"), ("xl", "fs-2")]
    for size, expected_class in sizes:
        icon = RustIcon("star", "bootstrap", size, None, None)
        html = icon.render()
        assert expected_class in html
        print(f"  ✓ {size} -> {expected_class}")

    # Test 4: Color
    print("\nTest 4: Icon with color")
    icon = RustIcon("check-circle", "bootstrap", "md", "success", None)
    html = icon.render()
    print(f"  HTML: {html}")
    assert "text-success" in html
    print("  ✓ PASS")

    # Test 5: Accessibility
    print("\nTest 5: Icon with aria-label")
    icon = RustIcon("info-circle", "bootstrap", "md", None, "Information")
    html = icon.render()
    print(f"  HTML: {html}")
    assert 'aria-label="Information"' in html
    assert 'role="img"' in html
    print("  ✓ PASS")

    # Test 6: Custom library
    print("\nTest 6: Custom icon library")
    icon = RustIcon("custom-logo", "custom", "lg", None, None)
    html = icon.render()
    print(f"  HTML: {html}")
    assert "custom-logo" in html
    print("  ✓ PASS")

    # Test 7: XSS protection
    print("\nTest 7: XSS protection in label")
    icon = RustIcon("alert", "bootstrap", "md", None, "<script>alert('xss')</script>")
    html = icon.render()
    print(f"  HTML: {html}")
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    print("  ✓ PASS")

    print("\n" + "=" * 60)
    print("✓ ALL TESTS PASSED")
    print("=" * 60)
else:
    print("\nCannot run tests without Rust implementation")
    sys.exit(1)
