"""
Tests for template attribute standardization between Component and LiveComponent.

These tests verify:
1. 'template' attribute works correctly for both Component and LiveComponent
2. Error handling when template attribute is not defined
3. Template rendering with various content types
"""

import pytest
from djust import LiveComponent, Component


class TestTemplateAttributeStandardization:
    """Test 'template' attribute for LiveComponent."""

    def test_livecomponent_with_template_attribute(self):
        """Test LiveComponent with 'template' attribute works correctly."""

        class SimpleComponent(LiveComponent):
            template = "<div>Hello {{ name }}</div>"

            def mount(self, name="World"):
                self.name = name

        # Should not raise any errors
        component = SimpleComponent()

        # Should render correctly
        html = component.render()
        assert "Hello World" in html
        assert "data-component-id" in html

    def test_error_when_template_not_defined(self):
        """Test that error is raised when template is not defined."""

        class NoTemplateComponent(LiveComponent):
            def mount(self):
                pass

        component = NoTemplateComponent()

        # Should raise ValueError when rendering
        with pytest.raises(ValueError) as exc_info:
            component.render()

        assert "must define 'template' attribute" in str(exc_info.value)
        assert NoTemplateComponent.__name__ in str(exc_info.value)


class TestComponentBaseClass:
    """Test that Component base class uses 'template' consistently."""

    def test_component_uses_template_attribute(self):
        """Test that stateless Component uses 'template' attribute."""

        class SimpleComponent(Component):
            template = "<div>Simple {{ label }}</div>"

            def __init__(self, label="Button"):
                super().__init__()
                self.label = label

        component = SimpleComponent()
        html = component.render()

        assert "Simple Button" in html


class TestTemplateWithVariousContent:
    """Test template attribute with various template content."""

    def test_template_with_complex_django_syntax(self):
        """Test template attribute works with complex Django template syntax."""

        class ComplexComponent(LiveComponent):
            template = """
                <div>
                    {% if show_items %}
                        {% for item in items %}
                        <div class="item">
                            <span>{{ item.name|upper }}</span>
                            {% if item.active %}
                            <span class="badge">Active</span>
                            {% endif %}
                        </div>
                        {% endfor %}
                    {% else %}
                        <p>No items</p>
                    {% endif %}
                </div>
            """

            def mount(self, items=None):
                self.items = items or []
                self.show_items = len(self.items) > 0

        component = ComplexComponent(items=[{"name": "test", "active": True}])
        html = component.render()

        assert "TEST" in html  # |upper filter works
        assert "Active" in html

    def test_template_with_multiline_strings(self):
        """Test template attribute works with multiline strings."""

        class MultilineComponent(LiveComponent):
            template = """
                <div class="container">
                    <h1>{{ title }}</h1>
                    <p>{{ description }}</p>
                </div>
            """

            def mount(self, title="Test", description="Description"):
                self.title = title
                self.description = description

        component = MultilineComponent()
        html = component.render()

        assert "Test" in html
        assert "Description" in html
        assert "container" in html
