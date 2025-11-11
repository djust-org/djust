"""
Test suite for Radio component.
"""

import os
import sys
import django

# Setup Django before importing djust components
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')

# Add python directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

# Configure Django
from django.conf import settings
if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='test-secret-key',
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [],
            'APP_DIRS': True,
        }],
    )
    django.setup()

import pytest
from djust.components.ui.radio_simple import Radio


def test_radio_basic():
    """Test basic radio button group rendering."""
    radio = Radio(
        name="size",
        label="Select Size",
        options=[
            {'value': 's', 'label': 'Small'},
            {'value': 'm', 'label': 'Medium'},
            {'value': 'l', 'label': 'Large'},
        ],
    )

    html = radio.render()

    # Check structure
    assert '<div class="mb-3">' in html
    assert '<label class="form-label">Select Size</label>' in html

    # Check all radio buttons are present
    assert 'type="radio"' in html
    assert 'name="size"' in html
    assert 'value="s"' in html
    assert 'value="m"' in html
    assert 'value="l"' in html

    # Check labels (with whitespace tolerance)
    assert 'Small' in html
    assert 'Medium' in html
    assert 'Large' in html


def test_radio_with_selected_value():
    """Test radio button with pre-selected value."""
    radio = Radio(
        name="priority",
        options=[
            {'value': 'low', 'label': 'Low'},
            {'value': 'high', 'label': 'High'},
        ],
        value="high"
    )

    html = radio.render()

    # Check that high is checked (should appear after value="high")
    assert 'value="high" checked' in html
    # Low should not be checked
    assert 'value="low" checked' not in html


def test_radio_inline():
    """Test inline radio button layout."""
    radio = Radio(
        name="answer",
        label="Yes or No?",
        options=[
            {'value': 'yes', 'label': 'Yes'},
            {'value': 'no', 'label': 'No'},
        ],
        inline=True
    )

    html = radio.render()

    # Check inline class is present
    assert 'form-check-inline' in html


def test_radio_with_disabled_option():
    """Test radio button with disabled option."""
    radio = Radio(
        name="plan",
        options=[
            {'value': 'free', 'label': 'Free'},
            {'value': 'pro', 'label': 'Pro'},
            {'value': 'enterprise', 'label': 'Enterprise', 'disabled': True},
        ],
    )

    html = radio.render()

    # Check that enterprise option is disabled
    assert 'value="enterprise"' in html
    # Find the position of enterprise and check for disabled
    enterprise_pos = html.find('value="enterprise"')
    assert enterprise_pos > 0
    # Check that disabled appears after the enterprise value
    disabled_pos = html.find('disabled', enterprise_pos)
    # Make sure it's in the same input tag (before next >)
    next_close = html.find('>', enterprise_pos)
    assert disabled_pos > 0 and disabled_pos < next_close


def test_radio_with_help_text():
    """Test radio button with help text."""
    radio = Radio(
        name="choice",
        label="Make a choice",
        options=[
            {'value': 'a', 'label': 'Option A'},
            {'value': 'b', 'label': 'Option B'},
        ],
        help_text="Choose wisely"
    )

    html = radio.render()

    # Check help text is present
    assert '<div class="form-text">Choose wisely</div>' in html


def test_radio_no_label():
    """Test radio button without group label."""
    radio = Radio(
        name="simple",
        options=[
            {'value': '1', 'label': 'One'},
            {'value': '2', 'label': 'Two'},
        ],
    )

    html = radio.render()

    # Should not have a form-label (only form-check-label for individual radios)
    assert 'class="form-label"' not in html
    # But should still have radio buttons
    assert 'type="radio"' in html


def test_radio_unique_ids():
    """Test that each radio button gets a unique ID."""
    radio = Radio(
        name="test",
        options=[
            {'value': 'a', 'label': 'A'},
            {'value': 'b', 'label': 'B'},
            {'value': 'c', 'label': 'C'},
        ],
    )

    html = radio.render()

    # Check for unique IDs
    assert 'id="test_0"' in html
    assert 'id="test_1"' in html
    assert 'id="test_2"' in html

    # Check that labels reference correct IDs
    assert 'for="test_0"' in html
    assert 'for="test_1"' in html
    assert 'for="test_2"' in html


def test_radio_requires_options():
    """Test that Radio requires at least one option."""
    with pytest.raises(ValueError, match="at least one option"):
        Radio(name="test", options=[])


def test_radio_validates_option_structure():
    """Test that Radio validates option structure."""
    with pytest.raises(ValueError, match="must have 'value' and 'label' keys"):
        Radio(
            name="test",
            options=[
                {'value': 'a'},  # Missing 'label'
            ]
        )

    with pytest.raises(ValueError, match="must have 'value' and 'label' keys"):
        Radio(
            name="test",
            options=[
                {'label': 'A'},  # Missing 'value'
            ]
        )


def test_radio_bootstrap_classes():
    """Test that Bootstrap 5 classes are applied correctly."""
    radio = Radio(
        name="test",
        options=[
            {'value': 'a', 'label': 'A'},
        ],
    )

    html = radio.render()

    # Check Bootstrap classes
    assert 'class="mb-3"' in html
    assert 'class="form-check"' in html
    assert 'class="form-check-input"' in html
    assert 'class="form-check-label"' in html


def test_radio_context_data():
    """Test get_context_data returns correct data."""
    options = [
        {'value': 's', 'label': 'Small'},
        {'value': 'l', 'label': 'Large'},
    ]

    radio = Radio(
        name="size",
        label="Size",
        options=options,
        value="s",
        inline=True,
        help_text="Pick a size"
    )

    context = radio.get_context_data()

    assert context['name'] == 'size'
    assert context['label'] == 'Size'
    assert context['options'] == options
    assert context['value'] == 's'
    assert context['inline'] is True
    assert context['help_text'] == "Pick a size"


def test_radio_string_value_comparison():
    """Test that value comparison works with strings and numbers."""
    radio = Radio(
        name="number",
        options=[
            {'value': 1, 'label': 'One'},
            {'value': 2, 'label': 'Two'},
        ],
        value=1
    )

    html = radio.render()

    # Should match even with type coercion
    assert 'value="1" checked' in html


if __name__ == '__main__':
    # Run tests with verbose output
    pytest.main([__file__, '-v'])
