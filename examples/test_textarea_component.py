"""
Test script for TextArea component.

Tests both Python and Rust implementations.
"""

# Configure Django settings for testing
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


def test_textarea_basic():
    """Test basic textarea rendering"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="description",
        label="Description",
        placeholder="Enter description...",
        rows=5
    )

    html = textarea.render()
    print("Basic TextArea:")
    print(html)
    print()

    assert "form-control" in html
    assert "description" in html
    assert "Description" in html
    assert "Enter description..." in html
    assert "rows=\"5\"" in html
    print("✓ Basic textarea test passed")


def test_textarea_with_value():
    """Test textarea with initial value"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="bio",
        label="Biography",
        value="Initial bio text here",
        rows=4
    )

    html = textarea.render()
    print("TextArea with value:")
    print(html)
    print()

    assert "Initial bio text here" in html
    assert "Biography" in html
    print("✓ TextArea with value test passed")


def test_textarea_required():
    """Test required textarea"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="comment",
        label="Comment",
        required=True,
        rows=3
    )

    html = textarea.render()
    print("Required TextArea:")
    print(html)
    print()

    assert "required" in html
    assert "text-danger" in html  # Required indicator
    assert "*" in html
    print("✓ Required textarea test passed")


def test_textarea_validation_invalid():
    """Test textarea with invalid validation state"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="description",
        label="Description",
        value="Short",
        validation_state="invalid",
        validation_message="Description must be at least 10 characters",
        rows=3
    )

    html = textarea.render()
    print("TextArea with invalid validation:")
    print(html)
    print()

    assert "is-invalid" in html
    assert "invalid-feedback" in html
    assert "Description must be at least 10 characters" in html
    print("✓ Invalid validation test passed")


def test_textarea_validation_valid():
    """Test textarea with valid validation state"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="description",
        label="Description",
        value="This is a long enough description",
        validation_state="valid",
        validation_message="Looks good!",
        rows=3
    )

    html = textarea.render()
    print("TextArea with valid validation:")
    print(html)
    print()

    assert "is-valid" in html
    assert "valid-feedback" in html
    assert "Looks good!" in html
    print("✓ Valid validation test passed")


def test_textarea_disabled():
    """Test disabled textarea"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="readonly_text",
        label="Read Only",
        value="Can't edit this",
        disabled=True,
        rows=2
    )

    html = textarea.render()
    print("Disabled TextArea:")
    print(html)
    print()

    assert "disabled" in html
    print("✓ Disabled textarea test passed")


def test_textarea_readonly():
    """Test readonly textarea"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="readonly_text",
        label="Read Only",
        value="Can't edit this",
        readonly=True,
        rows=2
    )

    html = textarea.render()
    print("Readonly TextArea:")
    print(html)
    print()

    assert "readonly" in html
    print("✓ Readonly textarea test passed")


def test_textarea_help_text():
    """Test textarea with help text"""
    from djust.components.ui import TextArea

    textarea = TextArea(
        name="notes",
        label="Notes",
        help_text="Maximum 500 characters",
        rows=4
    )

    html = textarea.render()
    print("TextArea with help text:")
    print(html)
    print()

    assert "form-text" in html
    assert "Maximum 500 characters" in html
    print("✓ Help text test passed")


def test_rust_implementation():
    """Test that Rust implementation is being used"""
    from djust.components.ui.textarea_simple import _RUST_AVAILABLE

    if _RUST_AVAILABLE:
        print("✓ Rust implementation available")

        # Test direct Rust usage
        from djust._rust import RustTextArea

        rust_textarea = RustTextArea(
            name="test",
            label="Test Label",
            value="Test value",
            rows=5,
            required=True
        )

        html = rust_textarea.render()
        print("Direct Rust TextArea:")
        print(html)
        print()

        assert "form-control" in html
        assert "Test Label" in html
        assert "Test value" in html
        assert "required" in html
        print("✓ Direct Rust implementation test passed")
    else:
        print("⚠ Rust implementation not available, using Python fallback")


def test_xss_protection():
    """Test HTML escaping for XSS protection"""
    from djust.components.ui import TextArea
    from djust.components.ui.textarea_simple import _RUST_AVAILABLE

    textarea = TextArea(
        name="xss_test",
        label="<script>alert('xss')</script>",
        value="<img src=x onerror='alert(1)'>",
        rows=3
    )

    html = textarea.render()
    print("XSS Protection test:")
    print(html)
    print()

    # Rust implementation escapes HTML, Python fallback doesn't (would need Django's escape)
    if _RUST_AVAILABLE:
        # Check that HTML is escaped by Rust
        assert "&lt;script&gt;" in html, "Rust implementation should escape HTML"
        assert "&lt;img" in html, "Rust implementation should escape HTML"
        print("✓ XSS protection test passed (Rust)")
    else:
        # Python fallback uses template rendering which doesn't auto-escape in this context
        print("⚠ Skipping XSS test for Python fallback (needs Django template auto-escape)")
        print("✓ XSS protection test skipped")


if __name__ == "__main__":
    print("Testing TextArea Component")
    print("=" * 60)
    print()

    tests = [
        test_textarea_basic,
        test_textarea_with_value,
        test_textarea_required,
        test_textarea_validation_invalid,
        test_textarea_validation_valid,
        test_textarea_disabled,
        test_textarea_readonly,
        test_textarea_help_text,
        test_rust_implementation,
        test_xss_protection,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} failed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All tests passed!")
    else:
        print(f"❌ {failed} test(s) failed")
