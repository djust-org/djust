"""
Benchmarks for template rendering performance.

These tests measure the performance of djust's template engine
across various scenarios.
"""

import pytest

from djust._rust import render_template


class TestSimpleTemplateRendering:
    """Benchmarks for simple template rendering."""

    @pytest.mark.benchmark(group="template_simple")
    def test_variable_substitution(self, benchmark, simple_context):
        """Benchmark simple variable substitution."""
        template = "Hello {{ name }}!"
        result = benchmark(render_template, template, simple_context)
        assert result == "Hello World!"

    @pytest.mark.benchmark(group="template_simple")
    def test_multiple_variables(self, benchmark, simple_context):
        """Benchmark template with multiple variables."""
        template = "Name: {{ name }}, Count: {{ count }}, Active: {{ active }}"
        result = benchmark(render_template, template, simple_context)
        assert "World" in result
        assert "42" in result

    @pytest.mark.benchmark(group="template_simple")
    def test_missing_variable(self, benchmark):
        """Benchmark template with missing variable (graceful handling)."""
        template = "Hello {{ missing }}!"
        result = benchmark(render_template, template, {})
        assert result == "Hello !"


class TestNestedTemplateRendering:
    """Benchmarks for nested/complex template rendering."""

    @pytest.mark.benchmark(group="template_nested")
    def test_nested_variable(self, benchmark, nested_context):
        """Benchmark nested variable access."""
        template = "Theme: {{ user.profile.settings.theme }}"
        result = benchmark(render_template, template, nested_context)
        assert result == "Theme: dark"

    @pytest.mark.benchmark(group="template_nested")
    def test_multiple_nested_variables(self, benchmark, nested_context):
        """Benchmark multiple nested variable accesses."""
        template = (
            "User: {{ user.name }}, Email: {{ user.email }}, "
            "Theme: {{ user.profile.settings.theme }}"
        )
        result = benchmark(render_template, template, nested_context)
        assert "John Doe" in result
        assert "dark" in result


class TestForLoopRendering:
    """Benchmarks for for-loop template rendering."""

    @pytest.mark.benchmark(group="template_loop")
    def test_simple_for_loop(self, benchmark, large_list_context):
        """Benchmark simple for loop."""
        template = "{% for item in items %}{{ item.name }}, {% endfor %}"
        result = benchmark(render_template, template, large_list_context)
        assert "Product 0" in result
        assert "Product 99" in result

    @pytest.mark.benchmark(group="template_loop")
    def test_for_loop_with_conditions(self, benchmark, large_list_context):
        """Benchmark for loop with conditional content."""
        template = """
            {% for item in items %}
                {% if item.in_stock %}
                    <span class="in-stock">{{ item.name }}</span>
                {% else %}
                    <span class="out-of-stock">{{ item.name }}</span>
                {% endif %}
            {% endfor %}
        """
        result = benchmark(render_template, template, large_list_context)
        assert "in-stock" in result
        assert "out-of-stock" in result


class TestConditionalRendering:
    """Benchmarks for conditional template rendering."""

    @pytest.mark.benchmark(group="template_conditional")
    def test_simple_if(self, benchmark, simple_context):
        """Benchmark simple if condition."""
        template = "{% if active %}Active{% else %}Inactive{% endif %}"
        result = benchmark(render_template, template, simple_context)
        assert result == "Active"

    @pytest.mark.benchmark(group="template_conditional")
    def test_nested_conditions(self, benchmark, nested_context):
        """Benchmark nested conditions."""
        template = """
            {% if user %}
                {% if user.profile %}
                    {% if user.profile.settings.notifications %}
                        Notifications enabled
                    {% endif %}
                {% endif %}
            {% endif %}
        """
        result = benchmark(render_template, template, nested_context)
        assert "Notifications enabled" in result


class TestTemplateCompilation:
    """Benchmarks for template compilation/parsing."""

    @pytest.mark.benchmark(group="template_compile")
    def test_simple_compile_and_render(self, benchmark):
        """Benchmark simple template compilation and rendering."""
        template = "Hello {{ name }}!"
        context = {"name": "World"}

        result = benchmark(render_template, template, context)
        assert result == "Hello World!"

    @pytest.mark.benchmark(group="template_compile")
    def test_complex_compile_and_render(self, benchmark):
        """Benchmark complex template compilation and rendering."""
        complex_template = """
            <div class="dashboard">
                <h1>{{ site.name }}</h1>
                {% for item in items %}
                    <div class="item">
                        <h2>{{ item.name }}</h2>
                        <p>{{ item.description }}</p>
                        {% if item.in_stock %}
                            <span class="price">${{ item.price }}</span>
                        {% else %}
                            <span class="sold-out">Sold Out</span>
                        {% endif %}
                    </div>
                {% endfor %}
            </div>
        """
        context = {
            "site": {"name": "Test Site"},
            "items": [
                {"name": "Item 1", "description": "Desc 1", "in_stock": True, "price": "10.00"},
                {"name": "Item 2", "description": "Desc 2", "in_stock": False, "price": "20.00"},
            ],
        }

        result = benchmark(render_template, complex_template, context)
        assert "dashboard" in result


class TestRealWorldTemplates:
    """Benchmarks for real-world template scenarios."""

    @pytest.mark.benchmark(group="template_realworld")
    def test_product_listing(self, benchmark, large_list_context):
        """Benchmark real-world product listing template."""
        template = """
            <div class="products">
                {% for item in items %}
                    <div class="product-card">
                        <h3>{{ item.name }}</h3>
                        <p class="description">{{ item.description }}</p>
                        <div class="pricing">
                            {% if item.in_stock %}
                                <span class="price">${{ item.price }}</span>
                                <button class="buy-btn">Add to Cart</button>
                            {% else %}
                                <span class="out-of-stock">Out of Stock</span>
                            {% endif %}
                        </div>
                    </div>
                {% endfor %}
            </div>
        """
        result = benchmark(render_template, template, large_list_context)
        assert "product-card" in result

    @pytest.mark.benchmark(group="template_realworld")
    def test_user_profile(self, benchmark, nested_context):
        """Benchmark user profile template."""
        template = """
            <div class="profile">
                <h1>{{ user.name }}</h1>
                <p class="email">{{ user.email }}</p>
                <div class="bio">{{ user.profile.bio }}</div>
                <div class="settings">
                    <span>Theme: {{ user.profile.settings.theme }}</span>
                </div>
            </div>
        """
        result = benchmark(render_template, template, nested_context)
        assert "John Doe" in result
