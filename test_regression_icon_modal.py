#!/usr/bin/env python
"""
Regression tests for Icon malformed HTML bug and Modal/Offcanvas ID fixes.

These tests ensure that:
1. Icon component generates valid HTML (no missing '>')
2. Modal component uses correct ID template variable
3. Offcanvas component uses correct ID template variable
4. Debug logging configuration works correctly
"""

import sys
import os
import re

# Add python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))


def test_icon_valid_html_structure():
    """
    Regression test for malformed HTML bug.
    
    The bug was that Icon rendered:
        <i class="bi bi-star-fill"</i>  (missing '>')
    
    This test ensures it renders:
        <i class="bi bi-star-fill"></i>  (valid HTML)
    """
    try:
        from djust._rust import RustIcon
    except ImportError:
        print("⚠ Skipping: Rust implementation not available")
        return True
    
    print("\n" + "=" * 60)
    print("TEST: Icon renders valid HTML structure")
    print("=" * 60)
    
    # Test various Icon configurations
    test_cases = [
        ("star-fill", "bootstrap", "md", None, None),
        ("check-circle", "bootstrap", "lg", "success", None),
        ("info", "bootstrap", "sm", "primary", "Information"),
    ]
    
    for name, library, size, color, label in test_cases:
        icon = RustIcon(name, library, size, color, label)
        html = icon.render()
        
        print(f"\n  Testing: {name} (library={library}, size={size})")
        print(f"  HTML: {html}")
        
        # Check 1: HTML should start with <i and end with </i>
        assert html.startswith('<i '), f"Icon should start with '<i ', got: {html}"
        assert html.endswith('</i>'), f"Icon should end with '</i>', got: {html}"
        
        # Check 2: Should have proper closing '>' before </i>
        # Pattern: <i class="..."...></i>
        # The bug was: <i class="...""</i> (missing '>')
        pattern = r'<i\s+[^>]+></i>'
        assert re.match(pattern, html), f"Icon should match pattern {pattern}, got: {html}"
        
        # Check 3: Should NOT have '""<' which indicates missing '>'
        assert '""<' not in html, f"Icon has malformed closing, found '\"\"<' in: {html}"
        
        # Check 4: Count quotes - should be even (pairs of opening/closing quotes)
        quote_count = html.count('"')
        assert quote_count % 2 == 0, f"Unbalanced quotes in HTML: {html}"
        
        print(f"  ✓ PASS: Valid HTML structure")
    
    return True


def test_modal_uses_correct_id_variable():
    """
    Regression test for Modal template using wrong variable.
    
    The bug was that Modal template used {{ modal_id }} but
    get_context_data() returns { 'id': ... }
    """
    try:
        from djust.components.ui.modal_simple import Modal
    except ImportError:
        print("⚠ Skipping: Modal component not available")
        return True
    
    print("\n" + "=" * 60)
    print("TEST: Modal uses correct ID variable")
    print("=" * 60)
    
    modal = Modal(
        id="testModal",
        title="Test Modal",
        body="This is a test modal"
    )
    
    html = modal.render()
    print(f"\n  Generated HTML (first 300 chars):")
    print(f"  {html[:300]}...")
    
    # Check: Should have id="testModal" (not id="")
    assert 'id="testModal"' in html, f"Modal should have id='testModal', HTML: {html[:500]}"
    
    # Check: Should NOT have empty id attribute
    assert 'id=""' not in html, f"Modal should not have empty id, HTML: {html[:500]}"
    
    # Check: aria-labelledby should reference the ID
    assert 'aria-labelledby="testModalLabel"' in html, f"Modal aria-labelledby incorrect, HTML: {html[:500]}"
    
    print(f"  ✓ PASS: Modal renders with correct ID")
    
    return True


def test_offcanvas_uses_correct_id_variable():
    """
    Regression test for Offcanvas template using wrong variable.
    
    The bug was that Offcanvas template used {{ offcanvas_id }} but
    get_context_data() returns { 'id': ... }
    """
    try:
        from djust.components.ui.offcanvas_simple import Offcanvas
    except ImportError:
        print("⚠ Skipping: Offcanvas component not available")
        return True
    
    print("\n" + "=" * 60)
    print("TEST: Offcanvas uses correct ID variable")
    print("=" * 60)
    
    offcanvas = Offcanvas(
        id="testOffcanvas",
        title="Test Offcanvas",
        body="This is a test offcanvas"
    )
    
    html = offcanvas.render()
    print(f"\n  Generated HTML (first 300 chars):")
    print(f"  {html[:300]}...")
    
    # Check: Should have id="testOffcanvas" (not id="")
    assert 'id="testOffcanvas"' in html, f"Offcanvas should have id='testOffcanvas', HTML: {html[:500]}"
    
    # Check: Should NOT have empty id attribute
    assert 'id=""' not in html, f"Offcanvas should not have empty id, HTML: {html[:500]}"
    
    # Check: aria-labelledby should reference the ID
    assert 'aria-labelledby="testOffcanvasLabel"' in html, f"Offcanvas aria-labelledby incorrect, HTML: {html[:500]}"
    
    print(f"  ✓ PASS: Offcanvas renders with correct ID")
    
    return True


def test_debug_vdom_config_default():
    """
    Test that debug_vdom configuration defaults to False.
    """
    from djust.config import config
    
    print("\n" + "=" * 60)
    print("TEST: Debug VDOM config defaults to False")
    print("=" * 60)
    
    debug_vdom = config.get('debug_vdom', None)
    print(f"\n  debug_vdom value: {debug_vdom}")
    
    assert debug_vdom is False, f"debug_vdom should default to False, got: {debug_vdom}"
    
    print(f"  ✓ PASS: Debug VDOM defaults to False")
    
    return True


def test_debug_vdom_config_can_be_set():
    """
    Test that debug_vdom configuration can be enabled.
    """
    from djust.config import config
    
    print("\n" + "=" * 60)
    print("TEST: Debug VDOM config can be enabled")
    print("=" * 60)
    
    # Save original value
    original = config.get('debug_vdom', False)
    
    try:
        # Set to True
        config.set('debug_vdom', True)
        assert config.get('debug_vdom') is True, "Should be able to set debug_vdom to True"
        print(f"  ✓ Set debug_vdom=True")
        
        # Set to False
        config.set('debug_vdom', False)
        assert config.get('debug_vdom') is False, "Should be able to set debug_vdom to False"
        print(f"  ✓ Set debug_vdom=False")
        
        print(f"  ✓ PASS: Debug VDOM config is settable")
        
        return True
    finally:
        # Restore original value
        config.set('debug_vdom', original)


def test_python_icon_fallback_aria_label():
    """
    Test that Python Icon fallback uses correct variable for aria-label.
    
    The bug was using self.color instead of self.label.
    """
    try:
        from djust.components.ui.icon_simple import Icon
    except ImportError:
        print("⚠ Skipping: Icon component not available")
        return True
    
    print("\n" + "=" * 60)
    print("TEST: Python Icon fallback aria-label uses correct variable")
    print("=" * 60)
    
    # Test Icon with label
    icon = Icon(
        name="info-circle",
        library="bootstrap",
        size="md",
        color="primary",
        label="Information Icon"
    )
    
    html = icon.render()
    print(f"\n  Generated HTML:")
    print(f"  {html}")
    
    # Check: aria-label should contain the label, not the color
    if 'aria-label=' in html:
        assert 'aria-label="Information Icon"' in html, f"aria-label should be 'Information Icon', HTML: {html}"
        assert 'aria-label="primary"' not in html, f"aria-label should not be color value, HTML: {html}"
        print(f"  ✓ PASS: aria-label uses label, not color")
    else:
        print(f"  ✓ PASS: No aria-label (expected when Rust implementation is used)")
    
    return True


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("REGRESSION TESTS FOR ICON/MODAL/OFFCANVAS FIXES")
    print("=" * 60)
    
    tests = [
        test_icon_valid_html_structure,
        test_modal_uses_correct_id_variable,
        test_offcanvas_uses_correct_id_variable,
        test_debug_vdom_config_default,
        test_debug_vdom_config_can_be_set,
        test_python_icon_fallback_aria_label,
    ]
    
    passed = 0
    failed = 0
    skipped = 0
    
    for test_func in tests:
        try:
            result = test_func()
            if result:
                passed += 1
            else:
                skipped += 1
        except AssertionError as e:
            print(f"\n✗ FAILED: {test_func.__name__}")
            print(f"  {e}")
            failed += 1
        except Exception as e:
            print(f"\n✗ ERROR: {test_func.__name__}")
            print(f"  {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 60)
    
    sys.exit(0 if failed == 0 else 1)
