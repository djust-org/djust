"""
Tests for comparison operators in {% if %} template conditions.

Tests cover all comparison operators: >, <, >=, <=, ==, !=
"""

import pytest
from djust._rust import render_template


class TestGreaterThan:
    """Tests for > (greater than) operator."""

    def test_greater_than_true(self):
        """Test > returns true when left > right."""
        template = "{% if count > 0 %}has items{% endif %}"
        context = {"count": 5}
        result = render_template(template, context)
        assert result == "has items"

    def test_greater_than_false(self):
        """Test > returns false when left <= right."""
        template = "{% if count > 10 %}many{% endif %}"
        context = {"count": 5}
        result = render_template(template, context)
        # render_template strips dj-if placeholders for standalone rendering (VDOM detail hidden from API)

        assert result == ""

    def test_greater_than_equal_values(self):
        """Test > returns false when values are equal."""
        template = "{% if count > 5 %}over{% else %}not over{% endif %}"
        context = {"count": 5}
        result = render_template(template, context)
        assert result == "not over"

    def test_greater_than_negative(self):
        """Test > works with negative numbers."""
        template = "{% if temp > -10 %}not too cold{% endif %}"
        context = {"temp": 0}
        result = render_template(template, context)
        assert result == "not too cold"


class TestLessThan:
    """Tests for < (less than) operator."""

    def test_less_than_true(self):
        """Test < returns true when left < right."""
        template = "{% if age < 18 %}minor{% endif %}"
        context = {"age": 15}
        result = render_template(template, context)
        assert result == "minor"

    def test_less_than_false(self):
        """Test < returns false when left >= right."""
        template = "{% if age < 18 %}minor{% endif %}"
        context = {"age": 21}
        result = render_template(template, context)
        # render_template strips dj-if placeholders for standalone rendering (VDOM detail hidden from API)

        assert result == ""

    def test_less_than_equal_values(self):
        """Test < returns false when values are equal."""
        template = "{% if age < 18 %}minor{% else %}adult{% endif %}"
        context = {"age": 18}
        result = render_template(template, context)
        assert result == "adult"


class TestGreaterThanOrEqual:
    """Tests for >= (greater than or equal) operator."""

    def test_gte_when_greater(self):
        """Test >= returns true when left > right."""
        template = "{% if price >= 100 %}expensive{% endif %}"
        context = {"price": 150}
        result = render_template(template, context)
        assert result == "expensive"

    def test_gte_when_equal(self):
        """Test >= returns true when left == right."""
        template = "{% if price >= 100 %}expensive{% endif %}"
        context = {"price": 100}
        result = render_template(template, context)
        assert result == "expensive"

    def test_gte_when_less(self):
        """Test >= returns false when left < right."""
        template = "{% if price >= 100 %}expensive{% endif %}"
        context = {"price": 50}
        result = render_template(template, context)
        # render_template strips dj-if placeholders for standalone rendering (VDOM detail hidden from API)

        assert result == ""


class TestLessThanOrEqual:
    """Tests for <= (less than or equal) operator."""

    def test_lte_when_less(self):
        """Test <= returns true when left < right."""
        template = "{% if score <= 50 %}failing{% endif %}"
        context = {"score": 30}
        result = render_template(template, context)
        assert result == "failing"

    def test_lte_when_equal(self):
        """Test <= returns true when left == right."""
        template = "{% if score <= 50 %}failing{% endif %}"
        context = {"score": 50}
        result = render_template(template, context)
        assert result == "failing"

    def test_lte_when_greater(self):
        """Test <= returns false when left > right."""
        template = "{% if score <= 50 %}failing{% endif %}"
        context = {"score": 80}
        result = render_template(template, context)
        # render_template strips dj-if placeholders for standalone rendering (VDOM detail hidden from API)

        assert result == ""


class TestNotEqual:
    """Tests for != (not equal) operator."""

    def test_not_equal_true(self):
        """Test != returns true when values differ."""
        template = '{% if status != "active" %}inactive{% endif %}'
        context = {"status": "pending"}
        result = render_template(template, context)
        assert result == "inactive"

    def test_not_equal_false(self):
        """Test != returns false when values are equal."""
        template = '{% if status != "active" %}inactive{% endif %}'
        context = {"status": "active"}
        result = render_template(template, context)
        # render_template strips dj-if placeholders for standalone rendering (VDOM detail hidden from API)

        assert result == ""

    def test_not_equal_numbers(self):
        """Test != works with numbers."""
        template = "{% if count != 0 %}has items{% endif %}"
        context = {"count": 5}
        result = render_template(template, context)
        assert result == "has items"


class TestEqual:
    """Tests for == (equal) operator."""

    def test_equal_strings_true(self):
        """Test == returns true for equal strings."""
        template = '{% if status == "active" %}is active{% endif %}'
        context = {"status": "active"}
        result = render_template(template, context)
        assert result == "is active"

    def test_equal_strings_false(self):
        """Test == returns false for different strings."""
        template = '{% if status == "active" %}is active{% endif %}'
        context = {"status": "pending"}
        result = render_template(template, context)
        # render_template strips dj-if placeholders for standalone rendering (VDOM detail hidden from API)

        assert result == ""

    def test_equal_numbers(self):
        """Test == works with numbers."""
        template = "{% if count == 0 %}empty{% endif %}"
        context = {"count": 0}
        result = render_template(template, context)
        assert result == "empty"


class TestFloatComparisons:
    """Tests for comparison operators with floating point numbers."""

    def test_float_greater_than(self):
        """Test > works with floats."""
        template = "{% if temp > 98.6 %}fever{% endif %}"
        context = {"temp": 100.5}
        result = render_template(template, context)
        assert result == "fever"

    def test_float_less_than(self):
        """Test < works with floats."""
        template = "{% if temp < 32.0 %}freezing{% endif %}"
        context = {"temp": 20.0}
        result = render_template(template, context)
        assert result == "freezing"

    def test_float_gte(self):
        """Test >= works with floats."""
        template = "{% if price >= 9.99 %}not free{% endif %}"
        context = {"price": 9.99}
        result = render_template(template, context)
        assert result == "not free"

    def test_float_lte(self):
        """Test <= works with floats."""
        template = "{% if discount <= 0.5 %}modest{% endif %}"
        context = {"discount": 0.25}
        result = render_template(template, context)
        assert result == "modest"


class TestVariableVsVariable:
    """Tests for comparing two variables."""

    def test_var_vs_var_greater(self):
        """Test comparing two variables with >."""
        template = "{% if current > threshold %}over limit{% endif %}"
        context = {"current": 100, "threshold": 50}
        result = render_template(template, context)
        assert result == "over limit"

    def test_var_vs_var_less(self):
        """Test comparing two variables with <."""
        template = "{% if balance < minimum %}low balance{% endif %}"
        context = {"balance": 50, "minimum": 100}
        result = render_template(template, context)
        assert result == "low balance"

    def test_var_vs_var_equal(self):
        """Test comparing two variables with ==."""
        template = "{% if a == b %}equal{% endif %}"
        context = {"a": 42, "b": 42}
        result = render_template(template, context)
        assert result == "equal"


class TestElseBranch:
    """Tests for comparison operators with else branches."""

    def test_greater_than_with_else(self):
        """Test > with else branch."""
        template = "{% if count > 0 %}has items{% else %}empty{% endif %}"
        context = {"count": 0}
        result = render_template(template, context)
        assert result == "empty"

    def test_less_than_with_else(self):
        """Test < with else branch."""
        template = "{% if age < 18 %}minor{% else %}adult{% endif %}"
        context = {"age": 25}
        result = render_template(template, context)
        assert result == "adult"


class TestEdgeCases:
    """Edge case tests for comparison operators."""

    def test_zero_comparison(self):
        """Test comparison with zero."""
        template = "{% if value > 0 %}positive{% endif %}"
        context = {"value": 1}
        result = render_template(template, context)
        assert result == "positive"

    def test_negative_comparison(self):
        """Test comparison resulting in negative."""
        template = "{% if value < 0 %}negative{% endif %}"
        context = {"value": -5}
        result = render_template(template, context)
        assert result == "negative"


class TestIfInHtmlAttribute:
    """Regression tests for issue #380: {% if %} inside HTML attribute values."""

    def test_if_in_attribute_false_no_comment(self):
        """#380: {% if %} false inside attribute must not produce <!--dj-if--> in output."""
        template = '<div class="btn {% if active %}active{% endif %}"></div>'
        result = render_template(template, {"active": False})
        assert "<!--" not in result
        assert 'class="btn "' in result

    def test_if_in_attribute_true_renders_content(self):
        """#380: {% if %} true inside attribute must render the branch content."""
        template = '<div class="btn {% if active %}active{% endif %}"></div>'
        result = render_template(template, {"active": True})
        assert 'class="btn active"' in result

    def test_if_in_text_node_no_comment_via_api(self):
        """render_template API strips <!--dj-if--> for standalone use."""
        template = "<div>{% if show %}yes{% endif %}</div>"
        result = render_template(template, {"show": False})
        # Python-facing API strips placeholder comments
        assert "<!--dj-if-->" not in result
        assert result == "<div></div>"

    def test_multiple_ifs_in_attributes(self):
        """#380: Multiple {% if %} blocks inside attributes all stay clean."""
        template = (
            '<a class="{% if active %}active{% endif %} '
            '{% if disabled %}disabled{% endif %}">link</a>'
        )
        result = render_template(template, {"active": False, "disabled": False})
        assert "<!--" not in result

    def test_if_in_attribute_with_else(self):
        """#380: {% if %}...{% else %} inside attribute uses the else branch normally."""
        template = '<div class="{% if active %}on{% else %}off{% endif %}"></div>'
        result = render_template(template, {"active": False})
        assert 'class="off"' in result
        assert "<!--" not in result


class TestElifInHtmlAttribute:
    """Regression tests for issue #382: {% elif %} inside HTML attribute values."""

    def test_elif_both_false_no_comment(self):
        """#382: {% if %}...{% elif %}...{% endif %} in attribute with both false must not emit <!--dj-if-->."""
        template = '<div class="{% if a %}one{% elif b %}two{% endif %}"></div>'
        result = render_template(template, {"a": False, "b": False})
        assert "<!--" not in result
        assert 'class=""' in result

    def test_elif_branch_renders_when_true(self):
        """#382: elif branch content must render when elif condition is true."""
        template = '<div class="{% if a %}one{% elif b %}two{% endif %}"></div>'
        result = render_template(template, {"a": False, "b": True})
        assert 'class="two"' in result
        assert "<!--" not in result

    def test_if_branch_renders_when_true(self):
        """#382: if branch content still renders normally when if condition is true."""
        template = '<div class="{% if a %}one{% elif b %}two{% endif %}"></div>'
        result = render_template(template, {"a": True, "b": False})
        assert 'class="one"' in result

    def test_multiple_elif_all_false_no_comment(self):
        """#382: Multiple elif branches — all false — must not emit <!--dj-if-->."""
        template = '<div class="{% if a %}a{% elif b %}b{% elif c %}c{% endif %}"></div>'
        result = render_template(template, {"a": False, "b": False, "c": False})
        assert "<!--" not in result
        assert 'class=""' in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
