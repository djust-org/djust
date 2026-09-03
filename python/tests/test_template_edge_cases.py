"""
Edge case tests for template variable extraction.

Tests specifically for expression parsing edge cases mentioned in PR review.
"""

import pytest
from djust._rust import extract_template_variables


class TestExpressionParsingEdgeCases:
    """Test edge cases in expression parsing."""

    def test_method_call_expression_is_refused(self):
        """A method-call expression is not a valid Django ``{{ … }}`` variable.

        #2578: djust now refuses whatever Django's ``FilterExpression`` refuses.
        ``user.get_attribute("profile.name")`` is not ``[\\w.]``-tileable head
        (the ``("profile.name")`` remainder is un-tileable), so both Django and
        djust reject it. Extraction shares the render parser (#1646) and so
        surfaces the same refusal — it no longer silently extracts a partial
        ``user`` head from a template that will never render.
        """
        template = '{{ user.get_attribute("profile.name") }}'
        with pytest.raises(ValueError, match="Could not parse the remainder"):
            extract_template_variables(template)

    def test_string_literal_in_filter_argument(self):
        """Test string literals in filter arguments."""
        template = '{{ date|date:"Y.m.d" }}'
        result = extract_template_variables(template)
        # Should only extract 'date', not treat "Y.m.d" as a variable
        assert "date" in result
        assert len(result) == 1

    def test_numeric_literal_with_decimal(self):
        """Test numeric literals with decimals."""
        template = "{{ value|floatformat:3.14 }}"
        result = extract_template_variables(template)
        # Should only extract 'value'
        assert "value" in result
        # Should not have '14' or any decimal-related variables
        assert len(result) == 1

    def test_comparison_with_dots_in_string(self):
        """Test comparison operations with dots in string literals.

        KNOWN LIMITATION: String literals in if conditions with dots are currently
        parsed as variable paths. This is a false positive but harmless - extra
        variables extracted just won't be used in serialization.
        Will be fixed in Phase 2 with more sophisticated expression parsing.
        """
        template = '{% if url == "https://example.com" %}...{% endif %}'
        result = extract_template_variables(template)
        # Should extract 'url'
        assert "url" in result
        # KNOWN LIMITATION: Currently also extracts 'example' due to simplified parsing
        # This is acceptable for Phase 1 - false positives are harmless

    def test_multiple_string_literals(self):
        """Test multiple string literals in template."""
        template = """
            {{ user.email }}
            {% if status == "active.premium" %}
                {{ plan.name }}
            {% endif %}
        """
        result = extract_template_variables(template)
        assert "user" in result
        assert "email" in result["user"]
        assert "status" in result
        assert "plan" in result
        # Should not extract 'premium' or 'active' as separate variables
        assert "premium" not in result
        assert "active" not in result

    def test_attribute_access_vs_string_literal(self):
        """Test distinguishing between attribute access and string literals.

        The filter is ``default`` rather than the invented ``filter`` this read
        until #2419: an unknown name is now refused while the template is
        parsed, as Django refuses it, so a fixture naming a filter nothing
        implements is no longer a template either engine will compile. What is
        under test — a dotted path inside a quoted ARGUMENT is not a variable —
        is unchanged, and needs only a real filter that takes an argument.
        """
        template = """
            {{ obj.real.path }}
            {{ other|default:"fake.path" }}
        """
        result = extract_template_variables(template)
        assert "obj" in result
        assert "real.path" in result["obj"]
        assert "other" in result
        # Should not extract 'fake' as a variable
        assert "fake" not in result

    def test_nested_quotes_edge_case(self):
        """Test nested or escaped quotes."""
        template = r'{{ value|default:"She said \"hello.world\"" }}'
        result = extract_template_variables(template)
        # Should only extract 'value'
        assert "value" in result
        assert "hello" not in result
        assert "world" not in result

    def test_url_in_template(self):
        """Test URLs don't get parsed as nested paths."""
        template = "{{ user.homepage }} {# URL like https://foo.bar.com #}"
        result = extract_template_variables(template)
        assert "user" in result
        assert "homepage" in result["user"]
        # Comment should not create variables
        assert "foo" not in result
        assert "bar" not in result
        assert "com" not in result

    def test_boolean_operators_with_dots(self):
        """Test boolean operators don't interfere with path extraction."""
        template = "{% if user.is_active and not user.is_banned %}...{% endif %}"
        result = extract_template_variables(template)
        assert "user" in result
        paths = result["user"]
        assert "is_active" in paths
        # Note: Current parser may have limitations with 'not' operator
        # This documents the current behavior


class TestComplexExpressionEdgeCases:
    """Test complex expression combinations."""

    def test_method_call_with_string_arg_is_refused(self):
        """A method call with a string argument is not valid Django variable syntax.

        #2578: ``items.get("key.with.dots")`` leaves the un-tileable remainder
        ``("key.with.dots")`` after the ``[\\w.]`` head, so Django's
        ``FilterExpression`` refuses it and djust now matches. Extraction
        propagates the same refusal (#1646) rather than partially extracting
        ``items``.
        """
        template = '{{ items.get("key.with.dots") }}'
        with pytest.raises(ValueError, match="Could not parse the remainder"):
            extract_template_variables(template)

    def test_dictionary_key_with_dots(self):
        """Test dictionary key access with dots in key name."""
        template = "{{ data.items.first }}"
        result = extract_template_variables(template)
        assert "data" in result
        assert "items.first" in result["data"]

    def test_chained_filters_with_arguments(self):
        """Test chained filters with various arguments."""
        template = '{{ text|truncatewords:10|default:"..." }}'
        result = extract_template_variables(template)
        # Should only extract 'text', not the filter arguments
        assert "text" in result
        assert len(result) == 1

    def test_mixed_quotes_in_template(self):
        """Test mixing single and double quotes."""
        template = """
            {{ value|default:'single' }}
            {{ other|default:"double" }}
        """
        result = extract_template_variables(template)
        assert "value" in result
        assert "other" in result
        # Should not extract 'single' or 'double'
        assert "single" not in result
        assert "double" not in result

    def test_variable_name_with_underscore_vs_dot(self):
        """Test that underscores in names vs dots for paths work correctly."""
        template = """
            {{ my_var }}
            {{ my_var.some_field }}
            {{ other.nested_obj.field }}
        """
        result = extract_template_variables(template)
        assert "my_var" in result
        assert "some_field" in result["my_var"]
        assert "other" in result
        assert "nested_obj.field" in result["other"]


class TestRealWorldEdgeCases:
    """Test real-world edge cases discovered in production."""

    def test_email_address_in_template(self):
        """Test that email addresses don't get parsed as paths."""
        # Emails should be in string literals, not variable names
        template = "{{ user.email }} {# like user@example.com #}"
        result = extract_template_variables(template)
        assert "user" in result
        assert "email" in result["user"]
        # Comment should not create variables
        assert "example" not in result

    def test_version_numbers(self):
        """Test version numbers with dots."""
        template = '{% if version == "1.2.3" %}{{ app.version }}{% endif %}'
        result = extract_template_variables(template)
        assert "version" in result
        assert "app" in result
        # Should not parse "1.2.3" as variable paths
        assert "1" not in result
        assert "2" not in result
        assert "3" not in result

    def test_file_paths_in_strings(self):
        """Test file paths in string literals.

        KNOWN LIMITATION: String literals in if conditions with dots/slashes are
        currently parsed as variable paths. This is a false positive but harmless.
        Will be fixed in Phase 2 with more sophisticated expression parsing.
        """
        template = '{% if path == "/var/log/app.log" %}{{ file.name }}{% endif %}'
        result = extract_template_variables(template)
        assert "path" in result
        assert "file" in result
        # KNOWN LIMITATION: Currently also extracts path components due to simplified parsing
        # This is acceptable for Phase 1 - false positives are harmless

    def test_json_path_notation_is_refused(self):
        """A JSON-path method call is not valid Django variable syntax.

        #2578: ``data.get_path("$.items[0].name")`` leaves the un-tileable
        remainder ``("$.items[0].name")`` after the ``[\\w.]`` head, so Django's
        ``FilterExpression`` refuses it and djust now matches. Extraction
        surfaces the same refusal (#1646).
        """
        template = '{{ data.get_path("$.items[0].name") }}'
        with pytest.raises(ValueError, match="Could not parse the remainder"):
            extract_template_variables(template)

    def test_regex_pattern_in_template(self):
        """Test regex patterns don't interfere.

        ``cut`` rather than the invented ``match`` this read until #2419 — see
        ``test_attribute_access_vs_string_literal`` for why a fixture may no
        longer name a filter nothing implements. The regex still sits in the
        argument, which is the whole of what this checks.
        """
        template = '{{ text|cut:"[a-z]+\\.txt$" }}'
        result = extract_template_variables(template)
        assert "text" in result
        # Regex pattern should not create variables
        assert "txt" not in result


class TestRustEngineGapFixes:
    """Regression tests for Rust template engine gaps fixed in DJU-3."""

    def test_model_instance_attributes_accessible(self):
        """Arbitrary Python object attributes are accessible via dotted path lookup.

        Previously, objects that weren't dict/list/primitives fell back to str()
        and attribute access returned empty string. Now __dict__ is extracted.
        """
        from djust._rust import render_template

        class FakeModel:
            def __init__(self):
                self.title = "Hello World"
                self.slug = "hello-world"
                self._state = object()  # Private - should be excluded

        model = FakeModel()
        result = render_template("{{ post.title }} ({{ post.slug }})", {"post": model})
        assert result == "Hello World (hello-world)"

    def test_private_attrs_excluded_from_model_serialization(self):
        """Private attributes (starting with _) are excluded from object serialization."""
        from djust._rust import render_template

        class FakeModel:
            def __init__(self):
                self.public_field = "visible"
                self._private = "hidden"

        model = FakeModel()
        # Since #2418 the template does not COMPILE at all: Django's
        # `Variable.__init__` refuses a name carrying `._` while the template
        # is being compiled, and djust now matches it. That is strictly
        # stronger than "renders empty" — the value has no path to the page.
        with pytest.raises(RuntimeError, match="may not begin with underscores"):
            render_template("{{ obj.public_field }}|{{ obj._private }}", {"obj": model})
        # The public half still renders, so the refusal is about the NAME and
        # not about the object.
        assert render_template("{{ obj.public_field }}", {"obj": model}) == "visible"

    def test_quote_escaping_in_attribute_values(self):
        """Double quotes in template variables are escaped to &quot; in output.

        This prevents breaking out of HTML attribute values.
        """
        from djust._rust import render_template

        result = render_template(
            '<div data-value="{{ text }}">test</div>',
            {"text": 'say "hello"'},
        )
        assert "&quot;hello&quot;" in result
        # The attribute should not be broken by unescaped quotes
        assert 'data-value="say &quot;hello&quot;"' in result

    def test_unsupported_custom_tag_raises_runtime_error(self):
        """Unsupported template tags raise RuntimeError instead of outputting comments.

        This allows Python callers to catch the error and fall back to Django's
        template engine, which handles custom tag libraries.
        """
        from djust._rust import render_template

        with pytest.raises(RuntimeError, match="Unsupported template tag.*unknown_custom_tag"):
            render_template("{% unknown_custom_tag arg1 arg2 %}", {})

    def test_supported_tags_still_work(self):
        """Basic built-in tags still render correctly after the gap fixes."""
        from djust._rust import render_template

        result = render_template(
            "{% if show %}{{ name }}{% endif %}",
            {"show": True, "name": "World"},
        )
        assert result == "World"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
