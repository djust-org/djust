"""
Test Tooltip Rust implementation directly.

Simple test that verifies the RustTooltip component works.
"""

import time


def test_rust_tooltip():
    """Test RustTooltip basic functionality"""
    try:
        from djust._rust import RustTooltip

        print("Testing RustTooltip implementation...")
        print()

        # Test basic rendering
        tooltip = RustTooltip("Hover me", "Tooltip text", "top", "hover", True)
        html = tooltip.render()

        print("✓ Basic rendering:")
        print(f"  {html}")
        print()

        assert "Hover me" in html, "Content not found"
        assert "Tooltip text" in html, "Tooltip text not found"
        assert "tooltip" in html.lower(), "Tooltip class not found"
        assert "data-bs-placement=\"top\"" in html, "Placement not found"
        assert "data-bs-trigger=\"hover\"" in html, "Trigger not found"
        assert "data-bs-arrow=\"true\"" in html, "Arrow attribute not found"

        print("✓ All assertions passed")
        print()

        # Test all placements
        print("Testing all placements...")
        placements = ["top", "bottom", "left", "right"]
        for placement in placements:
            tooltip = RustTooltip("Test", "Text", placement, "hover", True)
            html = tooltip.render()
            assert f'data-bs-placement="{placement}"' in html
            print(f"  ✓ {placement}")
        print()

        # Test all triggers
        print("Testing all triggers...")
        triggers = ["hover", "click", "focus"]
        for trigger in triggers:
            tooltip = RustTooltip("Test", "Text", "top", trigger, True)
            html = tooltip.render()
            assert f'data-bs-trigger="{trigger}"' in html
            print(f"  ✓ {trigger}")
        print()

        # Test arrow option
        print("Testing arrow option...")
        with_arrow = RustTooltip("Test", "Text", "top", "hover", True)
        without_arrow = RustTooltip("Test", "Text", "top", "hover", False)

        html_with = with_arrow.render()
        html_without = without_arrow.render()

        assert "data-bs-arrow=\"true\"" in html_with
        assert "data-bs-arrow" not in html_without
        print("  ✓ With arrow")
        print("  ✓ Without arrow")
        print()

        # Test HTML escaping
        print("Testing HTML escaping...")
        tooltip = RustTooltip(
            "<script>alert('xss')</script>",
            "Text with \"quotes\" & <tags>",
            "top",
            "hover",
            True
        )
        html = tooltip.render()

        # Content should not be escaped (allows HTML like icons)
        assert "<script>" in html
        print("  ✓ Content not escaped (allows HTML)")

        # But title attribute should be escaped
        assert "&quot;" in html
        assert "&amp;" in html
        assert "&lt;" in html
        assert "&gt;" in html
        print("  ✓ Title attribute properly escaped")
        print()

        # Test invalid placement (should default to "top")
        print("Testing invalid placement...")
        tooltip = RustTooltip("Test", "Text", "invalid", "hover", True)
        html = tooltip.render()
        assert 'data-bs-placement="top"' in html
        print("  ✓ Defaults to 'top'")
        print()

        # Test invalid trigger (should default to "hover")
        print("Testing invalid trigger...")
        tooltip = RustTooltip("Test", "Text", "top", "invalid", True)
        html = tooltip.render()
        assert 'data-bs-trigger="hover"' in html
        print("  ✓ Defaults to 'hover'")
        print()

        # Test __repr__
        print("Testing __repr__...")
        tooltip = RustTooltip("Hover me", "Tooltip text", "top", "hover", True)
        repr_str = repr(tooltip)
        print(f"  {repr_str}")
        assert "RustTooltip" in repr_str
        assert "Hover me" in repr_str
        assert "Tooltip text" in repr_str
        print("  ✓ __repr__ works")
        print()

        # Performance test
        print("Testing performance...")
        iterations = 10000

        start = time.perf_counter()
        for _ in range(iterations):
            tooltip = RustTooltip("Test", "Info", "top", "hover", True)
            tooltip.render()
        elapsed = time.perf_counter() - start

        per_render = (elapsed / iterations) * 1_000_000  # Convert to microseconds
        print(f"  {iterations} renders in {elapsed*1000:.2f}ms")
        print(f"  {per_render:.2f}μs per render")

        # Should be very fast (sub-10μs per render)
        if per_render < 10:
            print("  ✓ Performance excellent (<10μs per render)")
        elif per_render < 100:
            print("  ✓ Performance good (<100μs per render)")
        else:
            print(f"  ⚠ Performance slower than expected ({per_render:.2f}μs)")
        print()

        print("=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        return True

    except (ImportError, AttributeError) as e:
        print(f"❌ Rust implementation not available: {e}")
        print()
        print("Make sure you've run 'make dev-build' to compile the Rust extensions.")
        return False
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_rust_tooltip()
    exit(0 if success else 1)
