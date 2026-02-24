"""
Tests for Jinja2-style inline conditionals in {{ }} expressions.

Syntax: {{ true_expr if condition else false_expr }}
The else branch is optional and defaults to empty string.

Primary use-case: conditional CSS classes and HTML attributes without
the DOM-corrupting <!--dj-if--> comment nodes that {% if %} blocks emit.
"""

from djust._rust import render_template


class TestBasicEvaluation:
    def test_true_branch(self):
        result = render_template('{{ "active" if is_active else "" }}', {"is_active": True})
        assert result == "active"

    def test_false_branch(self):
        result = render_template('{{ "active" if is_active else "inactive" }}', {"is_active": False})
        assert result == "inactive"

    def test_no_else_true(self):
        result = render_template('{{ "visible" if show }}', {"show": True})
        assert result == "visible"

    def test_no_else_false_gives_empty(self):
        result = render_template('{{ "visible" if show }}', {"show": False})
        assert result == ""


class TestVariableBranches:
    def test_variable_in_true_branch(self):
        result = render_template('{{ name if show else "Guest" }}', {"show": True, "name": "Alice"})
        assert result == "Alice"

    def test_variable_in_false_branch(self):
        result = render_template('{{ "Alice" if show else fallback }}', {"show": False, "fallback": "nobody"})
        assert result == "nobody"


class TestComparisonConditions:
    def test_greater_than_true(self):
        result = render_template('{{ "many" if count > 0 else "none" }}', {"count": 5})
        assert result == "many"

    def test_greater_than_false(self):
        result = render_template('{{ "many" if count > 0 else "none" }}', {"count": 0})
        assert result == "none"

    def test_equality(self):
        result = render_template('{{ "dark-theme" if mode == "dark" else "light-theme" }}', {"mode": "dark"})
        assert result == "dark-theme"

    def test_inequality(self):
        result = render_template('{{ "error" if status != "ok" else "" }}', {"status": "ok"})
        assert result == ""


class TestHtmlAttributeContext:
    """The primary use-case: conditional class/attribute values."""

    def test_class_attribute_true(self):
        tmpl = '<li class="{{ "selected" if is_selected else "" }}">item</li>'
        result = render_template(tmpl, {"is_selected": True})
        assert result == '<li class="selected">item</li>'

    def test_class_attribute_false(self):
        tmpl = '<li class="{{ "selected" if is_selected else "" }}">item</li>'
        result = render_template(tmpl, {"is_selected": False})
        assert result == '<li class="">item</li>'

    def test_multiple_inline_ifs_in_template(self):
        tmpl = '<div class="{{ "active" if active else "" }} {{ "error" if error else "" }}"></div>'
        result = render_template(tmpl, {"active": True, "error": False})
        assert result == '<div class="active "></div>'

    def test_no_dj_if_comment_nodes_emitted(self):
        """Unlike {% if %}, inline if must never emit <!--dj-if--> placeholders."""
        tmpl = '<li class="{{ "selected" if is_selected else "" }}">item</li>'
        result = render_template(tmpl, {"is_selected": False})
        assert "<!--" not in result
        assert "dj-if" not in result


class TestXssAutoEscaping:
    def test_variable_output_is_escaped(self):
        result = render_template('{{ val if show else "" }}', {"show": True, "val": "<script>xss</script>"})
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_false_branch_variable_escaped(self):
        result = render_template('{{ "" if show else val }}', {"show": False, "val": "<b>bold</b>"})
        assert "<b>" not in result
        assert "&lt;b&gt;" in result


class TestEdgeCases:
    def test_undefined_variable_is_falsy(self):
        result = render_template('{{ "yes" if defined_nowhere else "no" }}', {})
        assert result == "no"

    def test_empty_string_is_falsy(self):
        result = render_template('{{ "yes" if val else "no" }}', {"val": ""})
        assert result == "no"

    def test_nonempty_string_is_truthy(self):
        result = render_template('{{ "yes" if val else "no" }}', {"val": "hello"})
        assert result == "yes"

    def test_zero_is_falsy(self):
        result = render_template('{{ "nonzero" if n else "zero" }}', {"n": 0})
        assert result == "zero"

    def test_nonzero_is_truthy(self):
        result = render_template('{{ "nonzero" if n else "zero" }}', {"n": 42})
        assert result == "nonzero"

    def test_mixed_with_regular_variable(self):
        """Inline if and regular {{ var }} can coexist in the same template."""
        tmpl = '<span class="{{ "hi" if flag else "" }}">{{ name }}</span>'
        result = render_template(tmpl, {"flag": True, "name": "Bob"})
        assert result == '<span class="hi">Bob</span>'
