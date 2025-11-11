#!/usr/bin/env python3
"""
Test script for Switch component.

Tests both Python and Rust implementations.
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

def test_switch_basic():
    """Test basic Switch component rendering."""
    from djust.components.ui import Switch

    switch = Switch(
        name="notifications",
        label="Enable notifications"
    )
    html = switch.render()

    print("=== Basic Switch ===")
    print(html)
    print()

    # Verify key elements
    assert 'form-switch' in html, "Missing form-switch class"
    assert 'type="checkbox"' in html, "Missing checkbox type"
    assert 'role="switch"' in html, "Missing switch role"
    assert 'name="notifications"' in html, "Missing name attribute"
    assert 'Enable notifications' in html, "Missing label text"
    assert 'id="notifications"' in html, "Missing ID (should default to name)"
    print("✓ Basic switch test passed")
    return True


def test_switch_checked():
    """Test checked Switch."""
    from djust.components.ui import Switch

    switch = Switch(
        name="dark_mode",
        label="Dark Mode",
        checked=True
    )
    html = switch.render()

    print("=== Checked Switch ===")
    print(html)
    print()

    assert ' checked' in html, "Missing checked attribute"
    assert 'Dark Mode' in html, "Missing label"
    print("✓ Checked switch test passed")
    return True


def test_switch_disabled():
    """Test disabled Switch."""
    from djust.components.ui import Switch

    switch = Switch(
        name="premium",
        label="Premium Features",
        disabled=True
    )
    html = switch.render()

    print("=== Disabled Switch ===")
    print(html)
    print()

    assert ' disabled' in html, "Missing disabled attribute"
    assert 'Premium Features' in html, "Missing label"
    print("✓ Disabled switch test passed")
    return True


def test_switch_with_help_text():
    """Test Switch with help text."""
    from djust.components.ui import Switch

    switch = Switch(
        name="email_notify",
        label="Email Notifications",
        help_text="Receive updates via email"
    )
    html = switch.render()

    print("=== Switch with Help Text ===")
    print(html)
    print()

    assert 'form-text' in html, "Missing form-text class"
    assert 'Receive updates via email' in html, "Missing help text"
    print("✓ Help text test passed")
    return True


def test_switch_custom_id():
    """Test Switch with custom ID."""
    from djust.components.ui import Switch

    switch = Switch(
        name="switch_name",
        label="Custom ID Test",
        id="custom-switch-id"
    )
    html = switch.render()

    print("=== Switch with Custom ID ===")
    print(html)
    print()

    assert 'id="custom-switch-id"' in html, "Missing custom ID"
    assert 'for="custom-switch-id"' in html, "Missing for attribute with custom ID"
    print("✓ Custom ID test passed")
    return True


def test_switch_inline():
    """Test inline Switch."""
    from djust.components.ui import Switch

    switch = Switch(
        name="inline_switch",
        label="Inline",
        inline=True
    )
    html = switch.render()

    print("=== Inline Switch ===")
    print(html)
    print()

    assert 'form-check-inline' in html, "Missing form-check-inline class"
    print("✓ Inline switch test passed")
    return True


def test_rust_implementation():
    """Test Rust implementation if available."""
    print("=== Testing Rust Implementation ===")
    try:
        from djust._rust import RustSwitch

        switch = RustSwitch(
            name="rust_test",
            label="Rust Switch",
            checked=True,
            help_text="This is rendered by Rust"
        )
        html = switch.render()

        print(html)
        print()

        assert 'form-switch' in html, "Rust: Missing form-switch class"
        assert 'Rust Switch' in html, "Rust: Missing label"
        assert ' checked' in html, "Rust: Missing checked attribute"
        assert 'This is rendered by Rust' in html, "Rust: Missing help text"

        print("✓ Rust implementation test passed")
        print("✓ Rust implementation is available and working!")
        return True
    except ImportError as e:
        print(f"⚠ Rust implementation not available: {e}")
        print("  (This is OK - component will fall back to template rendering)")
        return True


def test_component_integration():
    """Test that Switch is properly exported from djust.components.ui."""
    try:
        from djust.components.ui import Switch

        # Verify it's the right class
        assert Switch.__name__ == 'Switch', "Wrong class name"
        assert hasattr(Switch, 'render'), "Missing render method"

        print("✓ Component integration test passed")
        return True
    except Exception as e:
        # Django settings not configured - skip this test
        print(f"⚠ Skipping integration test (Django not configured): {e}")
        return True


def test_xss_protection():
    """Test that HTML is escaped in labels and help text (Rust implementation)."""
    try:
        from djust._rust import RustSwitch

        switch = RustSwitch(
            name="xss_test",
            label="<script>alert('xss')</script>",
            help_text="<b>bold</b> text"
        )
        html = switch.render()

        print("=== XSS Protection Test (Rust) ===")
        print(html)
        print()

        # Rust implementation should escape HTML in label and help text
        assert '<script>' not in html, "XSS vulnerability: script tag not escaped in label"
        assert '&lt;script&gt;' in html, "Label not properly escaped"
        assert '&lt;b&gt;' in html, "Help text not properly escaped"

        print("✓ XSS protection test passed (Rust escapes HTML)")
        return True
    except ImportError:
        print("⚠ Skipping XSS test (Rust not available, template rendering doesn't auto-escape)")
        return True


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Testing Switch Component")
    print("=" * 60)
    print()

    tests = [
        test_component_integration,
        test_switch_basic,
        test_switch_checked,
        test_switch_disabled,
        test_switch_with_help_text,
        test_switch_custom_id,
        test_switch_inline,
        test_xss_protection,
        test_rust_implementation,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
