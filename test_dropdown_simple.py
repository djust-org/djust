"""
Test the Dropdown component.
"""

# Configure Django settings before imports
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
        LIVEVIEW_CONFIG={
            'css_framework': 'bootstrap5',
        }
    )
    django.setup()

from python.djust.components.ui import Dropdown
from python.djust.config import config


def test_basic_dropdown():
    """Test basic dropdown rendering"""
    items = [
        {'label': 'Action', 'url': '#action'},
        {'label': 'Another action', 'url': '#another'},
        {'divider': True},
        {'label': 'Disabled', 'url': '#disabled', 'disabled': True},
    ]

    dropdown = Dropdown(label="Menu", items=items)
    html = dropdown.render()

    print("Basic Dropdown:")
    print(html)
    print("\n" + "="*80 + "\n")

    # Verify essential elements
    assert 'Menu' in html
    assert 'dropdown' in html
    assert 'Action' in html
    assert 'Another action' in html
    assert 'Disabled' in html
    assert 'dropdown-divider' in html

    return html


def test_split_dropdown():
    """Test split button dropdown"""
    items = [
        {'label': 'Edit', 'url': '#edit'},
        {'label': 'Delete', 'url': '#delete'},
    ]

    dropdown = Dropdown(
        label="Primary",
        items=items,
        variant="primary",
        split=True
    )
    html = dropdown.render()

    print("Split Dropdown:")
    print(html)
    print("\n" + "="*80 + "\n")

    # Verify split button elements
    assert 'Primary' in html
    assert 'dropdown-toggle-split' in html
    assert 'Edit' in html
    assert 'Delete' in html

    return html


def test_dropdown_variants():
    """Test different dropdown variants"""
    items = [
        {'label': 'Option 1', 'url': '#1'},
        {'label': 'Option 2', 'url': '#2'},
    ]

    variants = ['primary', 'secondary', 'success', 'danger', 'warning', 'info']

    print("Dropdown Variants:")
    for variant in variants:
        dropdown = Dropdown(
            label=f"{variant.capitalize()} Dropdown",
            items=items,
            variant=variant
        )
        html = dropdown.render()
        assert f'btn-{variant}' in html
        print(f"{variant.capitalize()}: {html[:100]}...")

    print("\n" + "="*80 + "\n")


def test_dropdown_sizes():
    """Test different dropdown sizes"""
    items = [
        {'label': 'Option 1', 'url': '#1'},
        {'label': 'Option 2', 'url': '#2'},
    ]

    print("Dropdown Sizes:")

    # Small
    small = Dropdown("Small", items=items, size="sm")
    small_html = small.render()
    assert 'btn-sm' in small_html
    print(f"Small: {small_html[:100]}...")

    # Medium (default)
    medium = Dropdown("Medium", items=items, size="md")
    medium_html = medium.render()
    print(f"Medium: {medium_html[:100]}...")

    # Large
    large = Dropdown("Large", items=items, size="lg")
    large_html = large.render()
    assert 'btn-lg' in large_html
    print(f"Large: {large_html[:100]}...")

    print("\n" + "="*80 + "\n")


def test_dropdown_directions():
    """Test different dropdown directions"""
    items = [
        {'label': 'Option 1', 'url': '#1'},
        {'label': 'Option 2', 'url': '#2'},
    ]

    print("Dropdown Directions:")

    directions = {
        'down': 'dropdown',
        'up': 'dropup',
        'start': 'dropstart',
        'end': 'dropend',
    }

    for direction, css_class in directions.items():
        dropdown = Dropdown(
            label=f"{direction.capitalize()}",
            items=items,
            direction=direction
        )
        html = dropdown.render()
        assert css_class in html
        print(f"{direction.capitalize()}: {html[:100]}...")

    print("\n" + "="*80 + "\n")


def test_tailwind_dropdown():
    """Test Tailwind rendering"""
    config.set('css_framework', 'tailwind')

    items = [
        {'label': 'Action', 'url': '#action'},
        {'label': 'Another action', 'url': '#another'},
        {'divider': True},
        {'label': 'Disabled', 'url': '#disabled', 'disabled': True},
    ]

    dropdown = Dropdown(
        label="Tailwind Menu",
        items=items,
        variant="primary"
    )
    html = dropdown.render()

    print("Tailwind Dropdown:")
    print(html)
    print("\n" + "="*80 + "\n")

    # Verify Tailwind classes
    assert 'bg-blue-600' in html
    assert 'x-data' in html
    assert 'Action' in html

    # Reset to bootstrap
    config.set('css_framework', 'bootstrap5')

    return html


def test_plain_dropdown():
    """Test plain HTML rendering"""
    config.set('css_framework', 'plain')

    items = [
        {'label': 'Action', 'url': '#action'},
        {'label': 'Another action', 'url': '#another'},
        {'divider': True},
        {'label': 'Disabled', 'url': '#disabled', 'disabled': True},
    ]

    dropdown = Dropdown(
        label="Plain Menu",
        items=items,
        variant="primary"
    )
    html = dropdown.render()

    print("Plain Dropdown:")
    print(html)
    print("\n" + "="*80 + "\n")

    # Verify plain HTML elements
    assert 'dropdown' in html
    assert 'button button-primary' in html
    assert 'Action' in html

    # Reset to bootstrap
    config.set('css_framework', 'bootstrap5')

    return html


def test_empty_items():
    """Test dropdown with no items"""
    dropdown = Dropdown(label="Empty", items=[])
    html = dropdown.render()

    print("Empty Dropdown:")
    print(html)
    print("\n" + "="*80 + "\n")

    assert 'Empty' in html
    assert 'dropdown' in html


def test_custom_id():
    """Test custom dropdown ID"""
    items = [
        {'label': 'Option 1', 'url': '#1'},
    ]

    dropdown = Dropdown(
        label="Custom ID",
        items=items,
        id="my-custom-dropdown"
    )
    html = dropdown.render()

    print("Custom ID Dropdown:")
    print(html)
    print("\n" + "="*80 + "\n")

    assert 'id="my-custom-dropdown"' in html


def run_all_tests():
    """Run all tests"""
    print("\n" + "="*80)
    print("TESTING DROPDOWN COMPONENT")
    print("="*80 + "\n")

    try:
        test_basic_dropdown()
        print("✓ Basic dropdown test passed\n")

        test_split_dropdown()
        print("✓ Split dropdown test passed\n")

        test_dropdown_variants()
        print("✓ Dropdown variants test passed\n")

        test_dropdown_sizes()
        print("✓ Dropdown sizes test passed\n")

        test_dropdown_directions()
        print("✓ Dropdown directions test passed\n")

        test_tailwind_dropdown()
        print("✓ Tailwind dropdown test passed\n")

        test_plain_dropdown()
        print("✓ Plain dropdown test passed\n")

        test_empty_items()
        print("✓ Empty items test passed\n")

        test_custom_id()
        print("✓ Custom ID test passed\n")

        print("="*80)
        print("ALL TESTS PASSED!")
        print("="*80 + "\n")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == '__main__':
    success = run_all_tests()
    exit(0 if success else 1)
