"""
Comprehensive tests for Rust template variable extraction.

Tests the extract_template_variables function exposed via PyO3 binding.
"""

import pytest
from djust._rust import extract_template_variables


class TestBasicExtraction:
    """Test basic variable extraction functionality."""

    def test_simple_variable(self):
        """Test extraction of simple variables."""
        result = extract_template_variables("{{ name }}")
        assert "name" in result
        assert result["name"] == []

    def test_single_level_attribute(self):
        """Test extraction of single-level attribute access."""
        result = extract_template_variables("{{ user.email }}")
        assert "user" in result
        assert result["user"] == ["email"]

    def test_nested_attributes(self):
        """Test extraction of deeply nested attributes."""
        result = extract_template_variables("{{ lease.property.owner.name }}")
        assert "lease" in result
        assert "property.owner.name" in result["lease"]

    def test_multiple_variables(self):
        """Test extraction of multiple different variables."""
        template = "{{ user.name }} {{ count }} {{ status }}"
        result = extract_template_variables(template)
        assert "user" in result
        assert "count" in result
        assert "status" in result
        assert result["user"] == ["name"]
        assert result["count"] == []
        assert result["status"] == []

    def test_multiple_paths_same_variable(self):
        """Test extraction of multiple attribute paths from same variable."""
        template = """
            {{ lease.property.name }}
            {{ lease.tenant.email }}
            {{ lease.start_date }}
        """
        result = extract_template_variables(template)
        assert "lease" in result
        paths = result["lease"]
        assert len(paths) == 3
        assert "property.name" in paths
        assert "tenant.email" in paths
        assert "start_date" in paths


class TestFilters:
    """Test variable extraction with filters."""

    def test_single_filter(self):
        """Test that filters don't interfere with path extraction."""
        result = extract_template_variables("{{ name|upper }}")
        assert "name" in result

    def test_filter_with_argument(self):
        """Test filters with arguments."""
        result = extract_template_variables('{{ date|date:"Y-m-d" }}')
        assert "date" in result

    def test_chained_filters(self):
        """Test multiple chained filters."""
        result = extract_template_variables("{{ text|lower|truncatewords:5 }}")
        assert "text" in result

    def test_nested_path_with_filter(self):
        """Test nested paths with filters."""
        result = extract_template_variables('{{ lease.end_date|date:"M d, Y" }}')
        assert "lease" in result
        assert "end_date" in result["lease"]


class TestTemplateTagsFor:
    """Test variable extraction from for loops."""

    def test_simple_for_loop(self):
        """Test extraction from simple for loop."""
        template = "{% for item in items %}{{ item.name }}{% endfor %}"
        result = extract_template_variables(template)
        assert "items" in result
        assert "item" in result
        assert "name" in result["item"]

    def test_for_with_method_call(self):
        """Test for loop with method call on iterable."""
        template = "{% for obj in queryset.all %}{{ obj }}{% endfor %}"
        result = extract_template_variables(template)
        assert "queryset" in result
        assert "all" in result["queryset"]

    def test_nested_for_loops(self):
        """Test nested for loops."""
        template = """
            {% for category in categories %}
                {% for item in category.items %}
                    {{ item.name }}
                {% endfor %}
            {% endfor %}
        """
        result = extract_template_variables(template)
        assert "categories" in result
        assert "category" in result
        assert "items" in result["category"]
        assert "item" in result
        assert "name" in result["item"]

    def test_for_with_reversed(self):
        """Test reversed for loop."""
        template = "{% for item in items reversed %}{{ item }}{% endfor %}"
        result = extract_template_variables(template)
        assert "items" in result
        assert "item" in result


class TestTemplateTagsIf:
    """Test variable extraction from if conditions."""

    def test_simple_if_condition(self):
        """Test extraction from simple if condition."""
        template = "{% if user.is_active %}Active{% endif %}"
        result = extract_template_variables(template)
        assert "user" in result
        assert "is_active" in result["user"]

    def test_if_with_comparison(self):
        """Test if with comparison operators."""
        template = '{% if status == "active" %}...{% endif %}'
        result = extract_template_variables(template)
        assert "status" in result

    def test_if_elif_else(self):
        """Test if/elif/else blocks."""
        template = """
            {% if user.role == "admin" %}
                Admin
            {% elif user.role == "moderator" %}
                Moderator
            {% else %}
                User
            {% endif %}
        """
        result = extract_template_variables(template)
        assert "user" in result
        assert "role" in result["user"]

    def test_nested_if(self):
        """Test nested if conditions."""
        template = """
            {% if user.is_authenticated %}
                {% if user.profile.is_verified %}
                    Verified
                {% endif %}
            {% endif %}
        """
        result = extract_template_variables(template)
        assert "user" in result
        paths = result["user"]
        assert "is_authenticated" in paths
        assert "profile.is_verified" in paths


class TestTemplateTagsOther:
    """Test variable extraction from other template tags."""

    def test_with_tag(self):
        """Test extraction from with tag."""
        template = "{% with total=items.count %}{{ total }}{% endwith %}"
        result = extract_template_variables(template)
        assert "items" in result
        assert "count" in result["items"]
        assert "total" in result

    def test_block_tag(self):
        """Test extraction from block tag."""
        template = "{% block content %}{{ page.title }}{% endblock %}"
        result = extract_template_variables(template)
        assert "page" in result
        assert "title" in result["page"]


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_template(self):
        """Test extraction from empty template."""
        result = extract_template_variables("")
        assert result == {}

    def test_no_variables(self):
        """Test template with no variables."""
        result = extract_template_variables("<html><body>Hello World</body></html>")
        assert result == {}

    def test_whitespace_in_variables(self):
        """Test variables with whitespace."""
        result = extract_template_variables("{{  user.name  }}")
        assert "user" in result
        assert "name" in result["user"]

    def test_malformed_template_graceful_fallback(self):
        """Test that malformed templates return empty dict (graceful fallback)."""
        # Unclosed tag - parser returns empty dict rather than crashing
        result = extract_template_variables("{% if x")
        assert isinstance(result, dict)

    def test_special_characters_in_text(self):
        """Test that special characters in text don't cause issues."""
        template = '<div data-value="{{ value }}">{{ & < > }}</div>'
        result = extract_template_variables(template)
        assert "value" in result


class TestDeduplication:
    """Test deduplication of extracted paths."""

    def test_duplicate_simple_variables(self):
        """Test deduplication of repeated simple variables."""
        template = "{{ name }} {{ name }} {{ name }}"
        result = extract_template_variables(template)
        assert "name" in result
        # Should only appear once
        assert result["name"] == []

    def test_duplicate_paths(self):
        """Test deduplication of repeated attribute paths."""
        template = """
            {{ lease.property.name }}
            {{ lease.property.name }}
            {{ lease.tenant.email }}
            {{ lease.property.name }}
        """
        result = extract_template_variables(template)
        assert "lease" in result
        paths = result["lease"]
        # Should have 2 unique paths
        assert len(paths) == 2
        assert "property.name" in paths
        assert "tenant.email" in paths

    def test_paths_are_sorted(self):
        """Test that returned paths are sorted."""
        template = """
            {{ obj.zebra }}
            {{ obj.apple }}
            {{ obj.middle }}
        """
        result = extract_template_variables(template)
        paths = result["obj"]
        # Should be sorted alphabetically
        assert paths == ["apple", "middle", "zebra"]


class TestRealWorldTemplates:
    """Test with real-world template patterns."""

    def test_django_admin_style_template(self):
        """Test extraction from Django admin-style template."""
        template = """
            <table>
                {% for obj in object_list %}
                <tr>
                    <td>{{ obj.id }}</td>
                    <td>{{ obj.get_status_display }}</td>
                    <td>{{ obj.created_at|date:"Y-m-d" }}</td>
                    <td>{{ obj.user.username }}</td>
                </tr>
                {% endfor %}
            </table>
        """
        result = extract_template_variables(template)
        assert "object_list" in result
        assert "obj" in result
        obj_paths = result["obj"]
        assert "id" in obj_paths
        assert "get_status_display" in obj_paths
        assert "created_at" in obj_paths
        assert "user.username" in obj_paths

    def test_rental_dashboard_template(self):
        """Test extraction from rental dashboard template."""
        template = """
            {% for lease in expiring_soon %}
            <div class="alert">
                <strong>{{ lease.property.name }}</strong>
                <p>Tenant: {{ lease.tenant.user.get_full_name }}</p>
                <p>Email: {{ lease.tenant.user.email }}</p>
                <p>Expires: {{ lease.end_date|date:"M d, Y" }}</p>
                {% if lease.property.maintenance_requests.count > 0 %}
                    <span class="badge">{{ lease.property.maintenance_requests.count }} pending</span>
                {% endif %}
            </div>
            {% endfor %}
        """
        result = extract_template_variables(template)

        assert "expiring_soon" in result
        assert "lease" in result

        lease_paths = result["lease"]
        assert "property.name" in lease_paths
        assert "tenant.user.get_full_name" in lease_paths
        assert "tenant.user.email" in lease_paths
        assert "end_date" in lease_paths
        assert "property.maintenance_requests.count" in lease_paths

    def test_nested_components_template(self):
        """Test extraction from template with nested components."""
        template = """
            <div>
                {% for category in categories %}
                    <h2>{{ category.name }}</h2>
                    {% for product in category.products.active %}
                        <div>
                            <h3>{{ product.name }}</h3>
                            <p>{{ product.description|truncatewords:20 }}</p>
                            <span>${{ product.price }}</span>
                            {% if product.reviews.exists %}
                                Rating: {{ product.reviews.average_rating }}
                            {% endif %}
                        </div>
                    {% endfor %}
                {% endfor %}
            </div>
        """
        result = extract_template_variables(template)

        assert "categories" in result
        assert "category" in result
        assert "product" in result

        category_paths = result["category"]
        assert "name" in category_paths
        assert "products.active" in category_paths

        product_paths = result["product"]
        assert "name" in product_paths
        assert "description" in product_paths
        assert "price" in product_paths
        assert "reviews.exists" in product_paths
        assert "reviews.average_rating" in product_paths


class TestPerformance:
    """Test performance characteristics."""

    def test_large_template(self):
        """Test extraction from large template completes quickly."""
        import time

        # Generate a large template
        template_parts = []
        for i in range(100):
            template_parts.append(f"""
                {{% for obj{i} in list{i} %}}
                    {{{{ obj{i}.field1 }}}}
                    {{{{ obj{i}.field2.nested }}}}
                    {{{{ obj{i}.field3.deeply.nested.value }}}}
                {{% endfor %}}
            """)
        template = "\n".join(template_parts)

        start = time.time()
        result = extract_template_variables(template)
        elapsed = time.time() - start

        # Should complete in less than 100ms
        assert elapsed < 0.1, f"Extraction took {elapsed:.3f}s, expected < 0.1s"

        # Verify we got results
        assert len(result) > 0


class TestComplexExpressions:
    """Test extraction from complex template expressions."""

    def test_method_calls_with_arguments(self):
        """Test method calls with arguments."""
        template = "{{ items.filter(active=True).count }}"
        result = extract_template_variables(template)
        # Should extract the base variable and path
        assert "items" in result

    def test_list_indexing(self):
        """Test list indexing in templates."""
        template = "{{ items.0.name }}"
        result = extract_template_variables(template)
        assert "items" in result

    def test_dictionary_access(self):
        """Test dictionary key access."""
        template = "{{ data.items.first }}"
        result = extract_template_variables(template)
        assert "data" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
