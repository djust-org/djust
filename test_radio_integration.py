"""
Integration test for Radio component with LiveView.

This demonstrates how the Radio component integrates with djust's LiveView system.
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

from djust.components.ui import Radio


class MockLiveView:
    """Mock LiveView for testing Radio component integration."""

    def __init__(self):
        self.size = 'm'
        self.priority = 'medium'
        self.theme = 'auto'

    def setup_components(self):
        """Setup Radio components."""
        # Size selector (inline)
        self.size_radio = Radio(
            name="size",
            label="Size",
            options=[
                {'value': 's', 'label': 'Small'},
                {'value': 'm', 'label': 'Medium'},
                {'value': 'l', 'label': 'Large'},
            ],
            value=self.size,
            inline=True
        )

        # Priority selector (stacked)
        self.priority_radio = Radio(
            name="priority",
            label="Priority",
            options=[
                {'value': 'low', 'label': 'Low'},
                {'value': 'medium', 'label': 'Medium'},
                {'value': 'high', 'label': 'High'},
            ],
            value=self.priority,
        )

        # Theme selector (inline with help text)
        self.theme_radio = Radio(
            name="theme",
            label="Theme",
            options=[
                {'value': 'light', 'label': 'Light'},
                {'value': 'dark', 'label': 'Dark'},
                {'value': 'auto', 'label': 'Auto'},
            ],
            value=self.theme,
            inline=True,
            help_text="Choose your preferred color theme"
        )

    def handle_size_change(self, value: str):
        """Handle size selection change."""
        self.size = value
        # Re-create component with new value
        self.size_radio = Radio(
            name="size",
            label="Size",
            options=[
                {'value': 's', 'label': 'Small'},
                {'value': 'm', 'label': 'Medium'},
                {'value': 'l', 'label': 'Large'},
            ],
            value=self.size,
            inline=True
        )

    def handle_priority_change(self, value: str):
        """Handle priority selection change."""
        self.priority = value
        # Re-create component with new value
        self.priority_radio = Radio(
            name="priority",
            label="Priority",
            options=[
                {'value': 'low', 'label': 'Low'},
                {'value': 'medium', 'label': 'Medium'},
                {'value': 'high', 'label': 'High'},
            ],
            value=self.priority,
        )

    def render_all(self):
        """Render all components."""
        return {
            'size_html': self.size_radio.render(),
            'priority_html': self.priority_radio.render(),
            'theme_html': self.theme_radio.render(),
        }


def test_liveview_integration():
    """Test Radio component integration with LiveView pattern."""
    # Create mock view
    view = MockLiveView()
    view.setup_components()

    # Test initial render
    rendered = view.render_all()

    # Check that all components rendered
    assert 'name="size"' in rendered['size_html']
    assert 'name="priority"' in rendered['priority_html']
    assert 'name="theme"' in rendered['theme_html']

    # Check initial values
    assert 'value="m" checked' in rendered['size_html']
    assert 'value="medium" checked' in rendered['priority_html']
    assert 'value="auto" checked' in rendered['theme_html']

    print("✓ Initial render successful")

    # Simulate size change event
    view.handle_size_change('l')
    rendered = view.render_all()

    # Check updated value
    assert 'value="l" checked' in rendered['size_html']
    assert 'value="m" checked' not in rendered['size_html']

    print("✓ Size change event handled successfully")

    # Simulate priority change event
    view.handle_priority_change('high')
    rendered = view.render_all()

    # Check updated value
    assert 'value="high" checked' in rendered['priority_html']
    assert 'value="medium" checked' not in rendered['priority_html']

    print("✓ Priority change event handled successfully")

    # Check that inline styles are applied correctly
    assert 'form-check-inline' in rendered['size_html']
    assert 'form-check-inline' not in rendered['priority_html']  # stacked layout
    assert 'form-check-inline' in rendered['theme_html']

    print("✓ Layout styles applied correctly")

    # Check help text
    assert 'Choose your preferred color theme' in rendered['theme_html']

    print("✓ Help text rendered correctly")


def test_multiple_radio_groups():
    """Test multiple independent Radio groups."""
    radio1 = Radio(
        name="question1",
        label="Question 1",
        options=[
            {'value': 'yes', 'label': 'Yes'},
            {'value': 'no', 'label': 'No'},
        ],
        value="yes"
    )

    radio2 = Radio(
        name="question2",
        label="Question 2",
        options=[
            {'value': 'yes', 'label': 'Yes'},
            {'value': 'no', 'label': 'No'},
        ],
        value="no"
    )

    html1 = radio1.render()
    html2 = radio2.render()

    # Check that each group has unique IDs
    assert 'id="question1_0"' in html1
    assert 'id="question1_1"' in html1
    assert 'id="question2_0"' in html2
    assert 'id="question2_1"' in html2

    # Check that selections are independent
    assert 'value="yes" checked' in html1
    assert 'value="no" checked' in html2

    print("✓ Multiple radio groups work independently")


def test_form_submission_pattern():
    """Test Radio component in a form submission pattern."""
    # Create a form with multiple radio groups
    size_radio = Radio(
        name="size",
        options=[
            {'value': 's', 'label': 'S'},
            {'value': 'm', 'label': 'M'},
            {'value': 'l', 'label': 'L'},
        ],
        value="m"
    )

    color_radio = Radio(
        name="color",
        options=[
            {'value': 'red', 'label': 'Red'},
            {'value': 'blue', 'label': 'Blue'},
            {'value': 'green', 'label': 'Green'},
        ],
        value="blue"
    )

    # Simulate form HTML
    form_html = f"""
    <form>
        {size_radio.render()}
        {color_radio.render()}
        <button type="submit">Submit</button>
    </form>
    """

    # Check that both components are in the form
    assert 'name="size"' in form_html
    assert 'name="color"' in form_html
    assert 'value="m" checked' in form_html
    assert 'value="blue" checked' in form_html

    print("✓ Form submission pattern works correctly")


def demo_integration():
    """Demonstrate full integration."""
    print("\n" + "=" * 80)
    print("  Radio Component - LiveView Integration Demo")
    print("=" * 80)

    view = MockLiveView()
    view.setup_components()

    print("\n[Initial State]")
    print(f"Size: {view.size}")
    print(f"Priority: {view.priority}")
    print(f"Theme: {view.theme}")

    print("\n[Size Radio HTML]")
    print(view.size_radio.render())

    print("\n[Priority Radio HTML]")
    print(view.priority_radio.render())

    print("\n[Theme Radio HTML]")
    print(view.theme_radio.render())

    # Simulate user interaction
    print("\n" + "=" * 80)
    print("  Simulating User Interactions")
    print("=" * 80)

    print("\n[User changes size to 'Large']")
    view.handle_size_change('l')
    print(f"New size: {view.size}")

    print("\n[User changes priority to 'High']")
    view.handle_priority_change('high')
    print(f"New priority: {view.priority}")

    print("\n[Updated State]")
    print(f"Size: {view.size}")
    print(f"Priority: {view.priority}")
    print(f"Theme: {view.theme}")

    print("\n" + "=" * 80)
    print("  Integration Demo Complete")
    print("=" * 80 + "\n")


if __name__ == '__main__':
    # Run tests
    print("\n" + "=" * 80)
    print("  Running Integration Tests")
    print("=" * 80 + "\n")

    test_liveview_integration()
    test_multiple_radio_groups()
    test_form_submission_pattern()

    print("\n" + "=" * 80)
    print("  All Integration Tests Passed!")
    print("=" * 80 + "\n")

    # Run demo
    demo_integration()
