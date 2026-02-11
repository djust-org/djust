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


class TestDefaultIfNoneFilter:
    """Tests for default_if_none filter."""

    def test_default_if_none_with_none(self):
        """Test default_if_none returns fallback for None/missing values."""
        template = "{{ missing|default_if_none:'fallback' }}"
        context = {}
        result = render_template(template, context)
        assert result == "fallback"

    def test_default_if_none_with_empty_string(self):
        """Test default_if_none does NOT replace empty strings."""
        template = "{{ name|default_if_none:'fallback' }}"
        context = {"name": ""}
        result = render_template(template, context)
        assert result == ""

    def test_default_if_none_with_value(self):
        """Test default_if_none passes through actual values."""
        template = "{{ name|default_if_none:'fallback' }}"
        context = {"name": "John"}
        result = render_template(template, context)
        assert result == "John"


class TestWordcountFilter:
    """Tests for wordcount filter."""

    def test_wordcount_basic(self):
        """Test wordcount counts words."""
        template = "{{ text|wordcount }}"
        context = {"text": "one two three four"}
        result = render_template(template, context)
        assert result == "4"

    def test_wordcount_empty(self):
        """Test wordcount with empty string."""
        template = "{{ text|wordcount }}"
        context = {"text": ""}
        result = render_template(template, context)
        assert result == "0"

    def test_wordcount_extra_spaces(self):
        """Test wordcount handles extra whitespace."""
        template = "{{ text|wordcount }}"
        context = {"text": "  one   two   three  "}
        result = render_template(template, context)
        assert result == "3"


class TestWordwrapFilter:
    """Tests for wordwrap filter."""

    def test_wordwrap_basic(self):
        """Test wordwrap inserts newlines at word boundaries."""
        template = "{{ text|wordwrap:15 }}"
        context = {"text": "this is a long string that wraps"}
        result = render_template(template, context)
        assert "\n" in result

    def test_wordwrap_short_text(self):
        """Test wordwrap with text shorter than width."""
        template = "{{ text|wordwrap:50 }}"
        context = {"text": "short text"}
        result = render_template(template, context)
        assert result == "short text"


class TestStriptagsFilter:
    """Tests for striptags filter."""

    def test_striptags_basic(self):
        """Test striptags strips HTML tags."""
        template = "{{ html|striptags }}"
        context = {"html": "<b>Hello</b> <i>world</i>"}
        result = render_template(template, context)
        assert result == "Hello world"

    def test_striptags_nested(self):
        """Test striptags with nested tags."""
        template = "{{ html|striptags }}"
        context = {"html": "<div><p>Inner text</p></div>"}
        result = render_template(template, context)
        assert result == "Inner text"

    def test_striptags_no_tags(self):
        """Test striptags with plain text."""
        template = "{{ text|striptags }}"
        context = {"text": "plain text"}
        result = render_template(template, context)
        assert result == "plain text"


class TestAddslashesFilter:
    """Tests for addslashes filter."""

    def test_addslashes_basic(self):
        """Test addslashes escapes quotes and backslash (using |safe to see raw output)."""
        template = "{{ text|addslashes|safe }}"
        context = {"text": 'it\'s a "test"'}
        result = render_template(template, context)
        assert "\\'" in result
        assert '\\"' in result

    def test_addslashes_backslash(self):
        """Test addslashes escapes backslashes."""
        template = "{{ text|addslashes|safe }}"
        context = {"text": "path\\to\\file"}
        result = render_template(template, context)
        assert "\\\\" in result


class TestLjustFilter:
    """Tests for ljust filter."""

    def test_ljust_basic(self):
        """Test ljust pads with spaces on the right."""
        template = "[{{ text|ljust:10 }}]"
        context = {"text": "hi"}
        result = render_template(template, context)
        assert result == "[hi        ]"

    def test_ljust_no_pad_needed(self):
        """Test ljust when text is longer than width."""
        template = "[{{ text|ljust:3 }}]"
        context = {"text": "hello"}
        result = render_template(template, context)
        assert result == "[hello]"


class TestRjustFilter:
    """Tests for rjust filter."""

    def test_rjust_basic(self):
        """Test rjust pads with spaces on the left."""
        template = "[{{ text|rjust:10 }}]"
        context = {"text": "hi"}
        result = render_template(template, context)
        assert result == "[        hi]"


class TestCenterFilter:
    """Tests for center filter."""

    def test_center_basic(self):
        """Test center pads with spaces on both sides."""
        template = "[{{ text|center:10 }}]"
        context = {"text": "hi"}
        result = render_template(template, context)
        assert result == "[    hi    ]"


class TestMakeListFilter:
    """Tests for make_list filter."""

    def test_make_list_with_join(self):
        """Test make_list splits string into characters, verified via join."""
        template = "{{ text|make_list|join:', ' }}"
        context = {"text": "abc"}
        result = render_template(template, context)
        assert result == "a, b, c"


class TestJsonScriptFilter:
    """Tests for json_script filter."""

    def test_json_script_basic(self):
        """Test json_script wraps value in script tag."""
        template = "{{ data|json_script:'my-data' }}"
        context = {"data": "hello"}
        result = render_template(template, context)
        assert '<script id="my-data" type="application/json">' in result
        assert "</script>" in result
        assert '"hello"' in result

    def test_json_script_escapes_dangerous_chars(self):
        """Test json_script escapes < > & inside script tag."""
        template = "{{ data|json_script:'xss-test' }}"
        context = {"data": "</script><script>alert(1)"}
        result = render_template(template, context)
        # The literal </script> must NOT appear inside the JSON content
        inner = result.split('type="application/json">')[1].split("</script>")[0]
        assert "</script>" not in inner
        assert "\\u003C" in inner


class TestForceEscapeFilter:
    """Tests for force_escape filter."""

    def test_force_escape_html(self):
        """Test force_escape escapes HTML entities."""
        template = "{{ html|force_escape }}"
        context = {"html": "<b>hello</b>"}
        result = render_template(template, context)
        assert result == "&lt;b&gt;hello&lt;/b&gt;"

    def test_force_escape_quotes(self):
        """Test force_escape escapes quotes."""
        template = "{{ text|force_escape }}"
        context = {"text": 'say "hello"'}
        result = render_template(template, context)
        assert "&quot;" in result


class TestEscapejsFilter:
    """Tests for escapejs filter."""

    def test_escapejs_quotes_and_backslash(self):
        """Test escapejs escapes quotes, backslash, and special chars."""
        template = "{{ text|escapejs }}"
        context = {"text": 'it\'s a "test"\\done'}
        result = render_template(template, context)
        assert "\\u0027" in result  # single quote
        assert "\\u0022" in result  # double quote
        assert "\\u005C" in result  # backslash

    def test_escapejs_newlines(self):
        """Test escapejs escapes newlines and tabs."""
        template = "{{ text|escapejs }}"
        context = {"text": "line1\nline2\ttab"}
        result = render_template(template, context)
        assert "\\u000A" in result
        assert "\\u0009" in result


class TestUrlizeFilter:
    """Tests for urlize filter."""

    def test_urlize_url(self):
        """Test urlize wraps URLs in anchor tags."""
        template = "{{ text|urlize|safe }}"
        context = {"text": "Visit https://example.com today"}
        result = render_template(template, context)
        assert '<a href="https://example.com"' in result
        assert 'rel="nofollow"' in result

    def test_urlize_email(self):
        """Test urlize wraps emails in mailto links."""
        template = "{{ text|urlize|safe }}"
        context = {"text": "Email user@example.com for help"}
        result = render_template(template, context)
        assert '<a href="mailto:user@example.com">' in result


class TestTruncateHtmlFilters:
    """Tests for truncatechars_html and truncatewords_html filters."""

    def test_truncatechars_html_preserves_tags(self):
        """Test truncatechars_html counts only visible chars and closes tags."""
        template = "{{ html|truncatechars_html:11|safe }}"
        context = {"html": "<p>Hello <b>world</b> this is long</p>"}
        result = render_template(template, context)
        assert "..." in result
        assert "<p>" in result

    def test_truncatewords_html_preserves_tags(self):
        """Test truncatewords_html counts only words outside tags."""
        template = "{{ html|truncatewords_html:3|safe }}"
        context = {"html": "<p>one two <b>three four</b> five</p>"}
        result = render_template(template, context)
        assert "one" in result
        assert "two" in result
        assert "three" in result
        assert "..." in result


class TestLinenumbersFilter:
    """Tests for linenumbers filter."""

    def test_linenumbers_basic(self):
        """Test linenumbers prepends line numbers."""
        template = "{{ text|linenumbers }}"
        context = {"text": "first\nsecond\nthird"}
        result = render_template(template, context)
        assert "1. first" in result
        assert "2. second" in result
        assert "3. third" in result


class TestPhone2numericFilter:
    """Tests for phone2numeric filter."""

    def test_phone2numeric_basic(self):
        """Test phone2numeric converts letters to digits."""
        template = "{{ phone|phone2numeric }}"
        context = {"phone": "1-800-COLLECT"}
        result = render_template(template, context)
        assert result == "1-800-2655328"


class TestGetDigitFilter:
    """Tests for get_digit filter."""

    def test_get_digit_rightmost(self):
        """Test get_digit returns Nth digit from right."""
        template = "{{ num|get_digit:1 }}"
        context = {"num": "12345"}
        result = render_template(template, context)
        assert result == "5"

    def test_get_digit_third(self):
        """Test get_digit returns 3rd digit from right."""
        template = "{{ num|get_digit:3 }}"
        context = {"num": "12345"}
        result = render_template(template, context)
        assert result == "3"


class TestIriencodeFilter:
    """Tests for iriencode filter."""

    def test_iriencode_preserves_non_ascii(self):
        """Test iriencode preserves non-ASCII characters."""
        template = "{{ text|iriencode }}"
        context = {"text": "café"}
        result = render_template(template, context)
        assert "café" in result

    def test_iriencode_encodes_spaces(self):
        """Test iriencode encodes spaces."""
        template = "{{ text|iriencode }}"
        context = {"text": "hello world"}
        result = render_template(template, context)
        assert "%20" in result


class TestPprintFilter:
    """Tests for pprint filter."""

    def test_pprint_string(self):
        """Test pprint of a string value."""
        template = "{{ text|pprint }}"
        context = {"text": "hello"}
        result = render_template(template, context)
        assert "hello" in result


class TestUnorderedListFilter:
    """Tests for unordered_list filter."""

    def test_unordered_list_flat(self):
        """Test unordered_list with flat list."""
        template = "{{ items|unordered_list|safe }}"
        context = {"items": ["one", "two", "three"]}
        result = render_template(template, context)
        assert "<li>one</li>" in result
        assert "<li>two</li>" in result
        assert "<li>three</li>" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
