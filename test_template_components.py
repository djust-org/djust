#!/usr/bin/env python
"""Test template engine integration with Rust components."""

from djust._rust import render_template

def test_simple_rust_button():
    """Test rendering a simple RustButton in a template"""
    print("=" * 60)
    print("TEST: Simple RustButton in Template")
    print("=" * 60)

    template = '''
    <div class="container">
        <h1>Welcome</h1>
        <RustButton id="welcome-btn" label="Click Me!" />
    </div>
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    # Check that button was rendered
    assert 'class="btn btn-primary"' in result
    assert 'id="welcome-btn"' in result
    assert 'Click Me!' in result
    print("\n✓ Test passed!")


def test_button_with_variant():
    """Test RustButton with variant prop"""
    print("\n" + "=" * 60)
    print("TEST: RustButton with Variant")
    print("=" * 60)

    template = '<RustButton id="success-btn" label="Submit" variant="success" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'btn-success' in result
    assert 'Submit' in result
    print("\n✓ Test passed!")


def test_button_with_size():
    """Test RustButton with size prop"""
    print("\n" + "=" * 60)
    print("TEST: RustButton with Size")
    print("=" * 60)

    template = '<RustButton id="large-btn" label="Large Button" size="lg" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'btn-lg' in result
    print("\n✓ Test passed!")


def test_button_with_context_variables():
    """Test RustButton with Django template variables"""
    print("\n" + "=" * 60)
    print("TEST: RustButton with Template Variables")
    print("=" * 60)

    template = '<RustButton id="{{ btn_id }}" label="{{ btn_label }}" variant="{{ btn_variant }}" />'

    context = {
        "btn_id": "dynamic-btn",
        "btn_label": "Dynamic Label",
        "btn_variant": "danger"
    }
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nContext:", context)
    print("\nRendered HTML:")
    print(result)

    assert 'id="dynamic-btn"' in result
    assert 'Dynamic Label' in result
    assert 'btn-danger' in result
    print("\n✓ Test passed!")


def test_button_with_onclick():
    """Test RustButton with onClick handler"""
    print("\n" + "=" * 60)
    print("TEST: RustButton with onClick Handler")
    print("=" * 60)

    template = '<RustButton id="click-btn" label="Click" onClick="handleClick" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert '@click="handleClick"' in result
    print("\n✓ Test passed!")


def test_multiple_buttons():
    """Test rendering multiple RustButtons"""
    print("\n" + "=" * 60)
    print("TEST: Multiple RustButtons")
    print("=" * 60)

    template = '''
    <div class="button-group">
        <RustButton id="btn1" label="Primary" variant="primary" />
        <RustButton id="btn2" label="Success" variant="success" />
        <RustButton id="btn3" label="Danger" variant="danger" />
    </div>
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'btn-primary' in result
    assert 'btn-success' in result
    assert 'btn-danger' in result
    assert result.count('<button') == 3  # Count opening button tags
    print("\n✓ Test passed!")


def test_button_in_for_loop():
    """Test RustButton inside {% for %} loop"""
    print("\n" + "=" * 60)
    print("TEST: RustButton in For Loop")
    print("=" * 60)

    template = '''
    {% for item in items %}
        <RustButton id="{{ item.id }}" label="{{ item.label }}" variant="{{ item.variant }}" />
    {% endfor %}
    '''

    context = {
        "items": [
            {"id": "btn-1", "label": "First", "variant": "primary"},
            {"id": "btn-2", "label": "Second", "variant": "success"},
            {"id": "btn-3", "label": "Third", "variant": "info"},
        ]
    }
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nContext items:", context["items"])
    print("\nRendered HTML:")
    print(result)

    assert 'id="btn-1"' in result
    assert 'First' in result
    assert 'id="btn-2"' in result
    assert 'Second' in result
    assert 'id="btn-3"' in result
    assert 'Third' in result
    print("\n✓ Test passed!")


def test_button_with_if_condition():
    """Test RustButton with {% if %} condition"""
    print("\n" + "=" * 60)
    print("TEST: RustButton with If Condition")
    print("=" * 60)

    template = '''
    {% if show_button %}
        <RustButton id="conditional-btn" label="Visible" variant="success" />
    {% endif %}
    '''

    # Test with show_button = True
    context = {"show_button": True}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nContext (show_button=True):")
    print("\nRendered HTML:")
    print(result)

    assert 'id="conditional-btn"' in result
    assert 'Visible' in result

    # Test with show_button = False
    context = {"show_button": False}
    result = render_template(template, context)

    print("\nContext (show_button=False):")
    print("\nRendered HTML:")
    print(result)

    assert 'conditional-btn' not in result
    print("\n✓ Test passed!")


def test_simple_rust_input():
    """Test rendering a simple RustInput in a template"""
    print("\n" + "=" * 60)
    print("TEST: Simple RustInput in Template")
    print("=" * 60)

    template = '''
    <div class="form-group">
        <RustInput id="email-input" placeholder="Enter email" />
    </div>
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    # Check that input was rendered
    assert 'class="form-control"' in result
    assert 'id="email-input"' in result
    assert 'placeholder="Enter email"' in result
    assert 'type="text"' in result
    print("\n✓ Test passed!")


def test_input_with_type():
    """Test RustInput with different types"""
    print("\n" + "=" * 60)
    print("TEST: RustInput with Type")
    print("=" * 60)

    template = '<RustInput id="email" inputType="email" placeholder="user@example.com" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'type="email"' in result
    assert 'placeholder="user@example.com"' in result
    print("\n✓ Test passed!")


def test_input_with_size():
    """Test RustInput with size prop"""
    print("\n" + "=" * 60)
    print("TEST: RustInput with Size")
    print("=" * 60)

    template = '<RustInput id="large-input" size="lg" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'form-control-lg' in result
    print("\n✓ Test passed!")


def test_input_with_context_variables():
    """Test RustInput with template variables"""
    print("\n" + "=" * 60)
    print("TEST: RustInput with Template Variables")
    print("=" * 60)

    template = '<RustInput id="{{ input_id }}" placeholder="{{ placeholder }}" value="{{ value }}" />'

    context = {
        "input_id": "username",
        "placeholder": "Enter username",
        "value": "john_doe"
    }
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nContext:", context)
    print("\nRendered HTML:")
    print(result)

    assert 'id="username"' in result
    assert 'placeholder="Enter username"' in result
    assert 'value="john_doe"' in result
    print("\n✓ Test passed!")


def test_simple_rust_text():
    """Test rendering a simple RustText in a template"""
    print("\n" + "=" * 60)
    print("TEST: Simple RustText in Template")
    print("=" * 60)

    template = '''
    <div class="container">
        <RustText content="Hello, World!" />
    </div>
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    # Check that text was rendered
    assert '<span' in result
    assert 'Hello, World!' in result
    print("\n✓ Test passed!")


def test_text_as_heading():
    """Test RustText as heading"""
    print("\n" + "=" * 60)
    print("TEST: RustText as Heading")
    print("=" * 60)

    template = '<RustText content="Page Title" element="h1" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert '<h1' in result
    assert 'Page Title' in result
    print("\n✓ Test passed!")


def test_text_as_label():
    """Test RustText as label for input"""
    print("\n" + "=" * 60)
    print("TEST: RustText as Label")
    print("=" * 60)

    template = '''
    <RustText content="Email Address" element="label" forInput="email-input" />
    <RustInput id="email-input" inputType="email" />
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert '<label' in result
    assert 'for="email-input"' in result
    assert 'Email Address' in result
    print("\n✓ Test passed!")


def test_text_with_color():
    """Test RustText with color"""
    print("\n" + "=" * 60)
    print("TEST: RustText with Color")
    print("=" * 60)

    template = '<RustText content="Success message" color="success" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'text-success' in result
    assert 'Success message' in result
    print("\n✓ Test passed!")


def test_text_with_weight():
    """Test RustText with font weight"""
    print("\n" + "=" * 60)
    print("TEST: RustText with Font Weight")
    print("=" * 60)

    template = '<RustText content="Bold text" weight="bold" />'

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    assert 'fw-bold' in result
    assert 'Bold text' in result
    print("\n✓ Test passed!")


def test_form_with_inputs_and_labels():
    """Test complete form with RustText labels and RustInput fields"""
    print("\n" + "=" * 60)
    print("TEST: Complete Form")
    print("=" * 60)

    template = '''
    <form>
        <div class="mb-3">
            <RustText content="Email" element="label" forInput="email" />
            <RustInput id="email" inputType="email" placeholder="user@example.com" />
        </div>
        <div class="mb-3">
            <RustText content="Password" element="label" forInput="password" />
            <RustInput id="password" inputType="password" placeholder="Enter password" />
        </div>
        <RustButton id="submit-btn" label="Submit" variant="primary" />
    </form>
    '''

    context = {}
    result = render_template(template, context)

    print("\nTemplate:")
    print(template)
    print("\nRendered HTML:")
    print(result)

    # Check labels
    assert result.count('<label') == 2
    assert 'for="email"' in result
    assert 'for="password"' in result

    # Check inputs
    assert 'type="email"' in result
    assert 'type="password"' in result

    # Check button
    assert 'btn-primary' in result
    print("\n✓ Test passed!")


def main():
    """Run all tests"""
    print("\n" + "🦀" * 30)
    print("TEMPLATE ENGINE + RUST COMPONENTS TESTS".center(60))
    print("🦀" * 30)

    tests = [
        test_simple_rust_button,
        test_button_with_variant,
        test_button_with_size,
        test_button_with_context_variables,
        test_button_with_onclick,
        test_multiple_buttons,
        test_button_in_for_loop,
        test_button_with_if_condition,
        test_simple_rust_input,
        test_input_with_type,
        test_input_with_size,
        test_input_with_context_variables,
        test_simple_rust_text,
        test_text_as_heading,
        test_text_as_label,
        test_text_with_color,
        test_text_with_weight,
        test_form_with_inputs_and_labels,
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
