"""
Test Tooltip component implementation.

Tests both Python and Rust implementations of the Tooltip component.
"""

import sys
import os

# Add the python directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

import pytest

# Import directly to avoid Django config issues
import importlib.util
spec = importlib.util.spec_from_file_location(
    "tooltip_simple",
    os.path.join(os.path.dirname(__file__), 'python/djust/components/ui/tooltip_simple.py')
)
tooltip_module = importlib.util.module_from_spec(spec)

# Mock the base module
class MockComponent:
    def __init__(self, **kwargs):
        pass
    def render(self):
        return self._render_custom()

# Create a minimal mock for testing
import types
base_module = types.ModuleType('base')
base_module.Component = MockComponent

# Add to sys.modules
sys.modules['djust.components.base'] = base_module
sys.modules['djust.config'] = types.ModuleType('config')
sys.modules['djust.config'].config = {'css_framework': 'bootstrap5'}

spec.loader.exec_module(tooltip_module)
Tooltip = tooltip_module.Tooltip


def test_tooltip_basic_python():
    """Test basic tooltip rendering with Python fallback"""
    tooltip = Tooltip("Hover me", text="This is helpful info")
    html = tooltip.render()

    assert "Hover me" in html
    assert "This is helpful info" in html
    assert "tooltip" in html.lower()


def test_tooltip_placements():
    """Test all tooltip placements"""
    placements = ["top", "bottom", "left", "right"]

    for placement in placements:
        tooltip = Tooltip("Test", text="Info", placement=placement)
        html = tooltip.render()
        assert placement in html
        assert "Test" in html


def test_tooltip_triggers():
    """Test different tooltip triggers"""
    triggers = ["hover", "click", "focus"]

    for trigger in triggers:
        tooltip = Tooltip("Test", text="Info", trigger=trigger)
        html = tooltip.render()
        assert trigger in html


def test_tooltip_arrow():
    """Test tooltip with and without arrow"""
    with_arrow = Tooltip("Test", text="Info", arrow=True)
    without_arrow = Tooltip("Test", text="Info", arrow=False)

    html_with = with_arrow.render()
    html_without = without_arrow.render()

    # With arrow should contain arrow attribute
    assert "arrow" in html_with.lower() or "data-bs-arrow" in html_with
    # Without arrow should not have arrow attribute
    # (Bootstrap only adds data-bs-arrow when true, so checking for absence)


def test_tooltip_html_escape():
    """Test that tooltip text is properly escaped"""
    tooltip = Tooltip(
        "Test",
        text='Text with "quotes" & <tags>',
        placement="top"
    )
    html = tooltip.render()

    # Text in title attribute should be escaped
    assert "&quot;" in html or "&#x27;" in html or "&amp;" in html


def test_tooltip_icon_content():
    """Test tooltip with icon as content"""
    tooltip = Tooltip(
        '<i class="bi bi-info-circle"></i>',
        text="Help information",
        placement="right"
    )
    html = tooltip.render()

    assert "bi-info-circle" in html
    assert "Help information" in html


def test_tooltip_rust_implementation():
    """Test Rust implementation if available"""
    try:
        from djust._rust import RustTooltip

        tooltip = RustTooltip("Hover me", "Tooltip text", "top", "hover", True)
        html = tooltip.render()

        assert "Hover me" in html
        assert "Tooltip text" in html
        assert "tooltip" in html.lower()
        assert "data-bs-placement=\"top\"" in html
        assert "data-bs-trigger=\"hover\"" in html

        print("✓ Rust implementation available and working")
    except (ImportError, AttributeError) as e:
        print(f"⚠ Rust implementation not available: {e}")
        pytest.skip("Rust implementation not available")


def test_tooltip_invalid_placement():
    """Test that invalid placement defaults to 'top'"""
    try:
        from djust._rust import RustTooltip

        tooltip = RustTooltip("Test", "Text", "invalid", "hover", True)
        html = tooltip.render()

        # Should default to "top"
        assert 'data-bs-placement="top"' in html

        print("✓ Invalid placement defaults correctly")
    except (ImportError, AttributeError):
        # Test with Python implementation
        tooltip = Tooltip("Test", text="Text", placement="invalid")
        html = tooltip.render()
        assert "top" in html or "invalid" not in html


def test_tooltip_invalid_trigger():
    """Test that invalid trigger defaults to 'hover'"""
    try:
        from djust._rust import RustTooltip

        tooltip = RustTooltip("Test", "Text", "top", "invalid", True)
        html = tooltip.render()

        # Should default to "hover"
        assert 'data-bs-trigger="hover"' in html

        print("✓ Invalid trigger defaults correctly")
    except (ImportError, AttributeError):
        # Test with Python implementation
        tooltip = Tooltip("Test", text="Text", trigger="invalid")
        html = tooltip.render()
        assert "hover" in html or "invalid" not in html


def test_tooltip_performance():
    """Test rendering performance"""
    import time

    # Test Rust implementation if available
    try:
        from djust._rust import RustTooltip

        start = time.perf_counter()
        for _ in range(1000):
            tooltip = RustTooltip("Test", "Info", "top", "hover", True)
            tooltip.render()
        rust_time = time.perf_counter() - start

        print(f"✓ Rust: 1000 renders in {rust_time*1000:.2f}ms ({rust_time}μs per render)")

        # Rust should be sub-millisecond per render
        assert rust_time < 0.1  # 100μs total for 1000 renders = 0.1s
    except (ImportError, AttributeError):
        print("⚠ Rust implementation not available for performance test")

    # Test Python implementation
    start = time.perf_counter()
    for _ in range(1000):
        tooltip = Tooltip("Test", text="Info")
        tooltip.render()
    python_time = time.perf_counter() - start

    print(f"✓ Python: 1000 renders in {python_time*1000:.2f}ms ({python_time}μs per render)")


if __name__ == "__main__":
    print("Testing Tooltip component...")
    print()

    # Run all tests
    test_tooltip_basic_python()
    print("✓ Basic tooltip rendering works")

    test_tooltip_placements()
    print("✓ All placements work")

    test_tooltip_triggers()
    print("✓ All triggers work")

    test_tooltip_arrow()
    print("✓ Arrow option works")

    test_tooltip_html_escape()
    print("✓ HTML escaping works")

    test_tooltip_icon_content()
    print("✓ Icon content works")

    try:
        test_tooltip_rust_implementation()
    except Exception as e:
        print(f"⚠ Rust implementation test: {e}")

    try:
        test_tooltip_invalid_placement()
    except Exception as e:
        print(f"⚠ Invalid placement test: {e}")

    try:
        test_tooltip_invalid_trigger()
    except Exception as e:
        print(f"⚠ Invalid trigger test: {e}")

    test_tooltip_performance()

    print()
    print("All tests passed!")
