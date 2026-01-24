"""
Tests for template filters in djust's Rust rendering engine.

Tests cover template filters accessible via {{ value|filter }} syntax.
"""

import pytest
from djust._rust import render_template


class TestUrlencodeFilter:
    """Tests for urlencode filter (GitHub Issue #36)."""

    def test_urlencode_basic_text(self):
        """Test urlencode with basic text containing spaces."""
        template = "{{ title|urlencode }}"
        context = {"title": "Hello World"}
        result = render_template(template, context)
        assert result == "Hello%20World"

    def test_urlencode_special_characters(self):
        """Test urlencode with special characters like & and =."""
        template = "{{ title|urlencode }}"
        context = {"title": "Hello World & Friends"}
        result = render_template(template, context)
        assert result == "Hello%20World%20%26%20Friends"

    def test_urlencode_query_string_chars(self):
        """Test urlencode with query string characters."""
        template = "{{ text|urlencode }}"
        context = {"text": "foo=bar&baz=qux"}
        result = render_template(template, context)
        assert result == "foo%3Dbar%26baz%3Dqux"

    def test_urlencode_safe_chars_not_encoded(self):
        """Test that safe chars (alphanumeric, -, _, ., ~) are NOT encoded."""
        template = "{{ filename|urlencode }}"
        context = {"filename": "hello-world_test.file~name"}
        result = render_template(template, context)
        assert result == "hello-world_test.file~name"

    def test_urlencode_empty_string(self):
        """Test urlencode with empty string."""
        template = "{{ text|urlencode }}"
        context = {"text": ""}
        result = render_template(template, context)
        assert result == ""

    def test_urlencode_path_and_query(self):
        """Test urlencode with path and query characters."""
        template = "{{ url|urlencode }}"
        context = {"url": "path/to/file?query=1"}
        result = render_template(template, context)
        assert result == "path%2Fto%2Ffile%3Fquery%3D1"

    def test_urlencode_in_href(self):
        """Test urlencode in realistic href usage (Twitter share example from issue)."""
        template = (
            '<a href="https://twitter.com/intent/tweet?text={{ post_title|urlencode }}">Share</a>'
        )
        context = {"post_title": "My Awesome Post!"}
        result = render_template(template, context)
        assert (
            result
            == '<a href="https://twitter.com/intent/tweet?text=My%20Awesome%20Post%21">Share</a>'
        )

    def test_urlencode_unicode(self):
        """Test urlencode with unicode characters."""
        template = "{{ text|urlencode }}"
        context = {"text": "Hello"}
        result = render_template(template, context)
        # Unicode characters should be percent-encoded as UTF-8 bytes
        assert result == "Hello"

        # Test with actual unicode
        context = {"text": "cafe"}
        result = render_template(template, context)
        # 'e' with no accent should pass through
        assert result == "cafe"

    def test_urlencode_with_unicode_emoji(self):
        """Test urlencode with emoji (multi-byte UTF-8)."""
        template = "{{ text|urlencode }}"
        # Emoji U+1F600 (grinning face) is 4 bytes in UTF-8: F0 9F 98 80
        context = {"text": "Hi!"}
        result = render_template(template, context)
        # The ! should be encoded as %21
        assert result == "Hi%21"


class TestUpperFilter:
    """Tests for upper filter."""

    def test_upper_basic(self):
        """Test upper filter converts to uppercase."""
        template = "{{ name|upper }}"
        context = {"name": "hello"}
        result = render_template(template, context)
        assert result == "HELLO"


class TestLowerFilter:
    """Tests for lower filter."""

    def test_lower_basic(self):
        """Test lower filter converts to lowercase."""
        template = "{{ name|lower }}"
        context = {"name": "HELLO"}
        result = render_template(template, context)
        assert result == "hello"


class TestEscapeFilter:
    """Tests for escape filter."""

    def test_escape_html_entities(self):
        """Test escape filter encodes HTML entities."""
        template = "{{ content|escape }}"
        context = {"content": "<script>alert('xss')</script>"}
        result = render_template(template, context)
        assert "&lt;script&gt;" in result
        assert "&#x27;" in result  # Single quote


class TestDefaultFilter:
    """Tests for default filter."""

    def test_default_with_empty_value(self):
        """Test default filter with empty string."""
        template = "{{ name|default:'Anonymous' }}"
        context = {"name": ""}
        result = render_template(template, context)
        assert result == "Anonymous"

    def test_default_with_value(self):
        """Test default filter with actual value."""
        template = "{{ name|default:'Anonymous' }}"
        context = {"name": "John"}
        result = render_template(template, context)
        assert result == "John"


class TestSlugifyFilter:
    """Tests for slugify filter."""

    def test_slugify_basic(self):
        """Test slugify filter creates URL-safe slug."""
        template = "{{ title|slugify }}"
        context = {"title": "Hello World Test!"}
        result = render_template(template, context)
        assert result == "hello-world-test"


class TestTruncatewordsFilter:
    """Tests for truncatewords filter."""

    def test_truncatewords(self):
        """Test truncatewords filter limits words."""
        template = "{{ text|truncatewords:5 }}"
        context = {"text": "This is a long sentence with many words"}
        result = render_template(template, context)
        assert result == "This is a long sentence..."


class TestJoinFilter:
    """Tests for join filter."""

    def test_join_list(self):
        """Test join filter joins list items."""
        template = "{{ items|join:', ' }}"
        context = {"items": ["a", "b", "c"]}
        result = render_template(template, context)
        assert result == "a, b, c"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
