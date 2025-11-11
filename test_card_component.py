#!/usr/bin/env python
"""Test Card component in templates."""

from djust._rust import render_template


def test_simple_card():
    """Test rendering a simple RustCard in a template"""
    print("=" * 60)
    print("TEST: Simple RustCard in Template")
    print("=" * 60)

    template = '''
    <div class="container">
        <RustCard body="This is the card content" />
    </div>
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    # Check that card was rendered
    assert 'class="card"' in result
    assert 'This is the card content' in result
    print("\n✓ Test passed!")


def test_card_with_header():
    """Test RustCard with header"""
    print("\n" + "=" * 60)
    print("TEST: RustCard with Header")
    print("=" * 60)

    template = '<RustCard body="Card content" header="Card Header" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'card-header' in result
    assert 'Card Header' in result
    assert 'Card content' in result
    print("\n✓ Test passed!")


def test_card_with_header_and_footer():
    """Test RustCard with header and footer"""
    print("\n" + "=" * 60)
    print("TEST: RustCard with Header and Footer")
    print("=" * 60)

    template = '''
    <RustCard
        body="Card body content"
        header="Header Text"
        footer="Footer Text"
    />
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'card-header' in result
    assert 'card-footer' in result
    assert 'Header Text' in result
    assert 'Footer Text' in result
    print("\n✓ Test passed!")


def test_card_with_variant():
    """Test RustCard with variant"""
    print("\n" + "=" * 60)
    print("TEST: RustCard with Variant")
    print("=" * 60)

    template = '<RustCard body="Success card" variant="success" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'border-success' in result
    assert 'Success card' in result
    print("\n✓ Test passed!")


def test_card_with_shadow():
    """Test RustCard with shadow"""
    print("\n" + "=" * 60)
    print("TEST: RustCard with Shadow")
    print("=" * 60)

    template = '<RustCard body="Shadowed card" shadow="true" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'shadow' in result
    print("\n✓ Test passed!")


def test_card_with_variables():
    """Test RustCard with template variables"""
    print("\n" + "=" * 60)
    print("TEST: RustCard with Template Variables")
    print("=" * 60)

    template = '''
    <RustCard
        body="{{ card_body }}"
        header="{{ card_header }}"
        variant="{{ card_variant }}"
    />
    '''

    context = {
        "card_body": "Dynamic body content",
        "card_header": "Dynamic Header",
        "card_variant": "primary"
    }
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nContext:", context)
    print("\nRendered HTML:")
    print(result)

    assert 'Dynamic body content' in result
    assert 'Dynamic Header' in result
    assert 'border-primary' in result
    print("\n✓ Test passed!")


def main():
    """Run all tests"""
    print("\n" + "🦀" * 30)
    print("RUST CARD COMPONENT TESTS".center(60))
    print("🦀" * 30)

    tests = [
        test_simple_card,
        test_card_with_header,
        test_card_with_header_and_footer,
        test_card_with_variant,
        test_card_with_shadow,
        test_card_with_variables,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"\n✗ Test failed: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Tests completed: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed == 0:
        print("\n🎉 All tests passed!")
    else:
        print(f"\n❌ {failed} test(s) failed")
        exit(1)


if __name__ == "__main__":
    main()
