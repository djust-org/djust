"""
Tests for HTML comment and whitespace stripping functionality.

These tests verify that the _strip_comments_and_whitespace() method
correctly prepares templates for VDOM baseline establishment.
"""

import pytest
from djust import LiveView


class TestLiveView(LiveView):
    """Test LiveView class"""

    template = "<div>Test</div>"

    def mount(self, request):
        pass


class TestCommentStripping:
    """Test HTML comment and whitespace stripping"""

    def test_strips_html_comments(self):
        """Test that HTML comments are removed"""
        view = TestLiveView()
        html = "<div><!-- Comment -->Content<!-- Another --></div>"
        result = view._strip_comments_and_whitespace(html)

        assert "<!--" not in result
        assert "-->" not in result
        assert "Comment" not in result
        assert "Content" in result

    def test_strips_multiline_comments(self):
        """Test that multiline HTML comments are removed"""
        view = TestLiveView()
        html = """<div>
            <!-- This is a
                 multiline comment -->
            <span>Content</span>
        </div>"""
        result = view._strip_comments_and_whitespace(html)

        assert "<!--" not in result
        assert "multiline comment" not in result
        assert "<span>Content</span>" in result

    def test_normalizes_whitespace(self):
        """Test that whitespace is normalized"""
        view = TestLiveView()
        html = "<div>  \n\n  Multiple    spaces   \t\t tabs  </div>"
        result = view._strip_comments_and_whitespace(html)

        # Multiple whitespace should be collapsed to single space
        assert "  " not in result
        assert "\n\n" not in result
        assert "\t\t" not in result
        assert "Multiple spaces tabs" in result

    def test_removes_whitespace_between_tags(self):
        """Test that whitespace between tags is removed"""
        view = TestLiveView()
        html = "<div>   <span>   Content   </span>   </div>"
        result = view._strip_comments_and_whitespace(html)

        # Whitespace between tags should be completely removed
        assert "><" in result
        assert "> <" not in result  # Even single spaces removed between tags

    def test_preserves_content(self):
        """Test that actual content is preserved"""
        view = TestLiveView()
        html = """<nav class="navbar">
            <!-- Navigation Component -->
            <div class="container">
                <a href="#">Link</a>
            </div>
        </nav>"""
        result = view._strip_comments_and_whitespace(html)

        # Structure preserved
        assert '<nav class="navbar">' in result
        assert '<div class="container">' in result
        assert '<a href="#">Link</a>' in result
        # Comments removed
        assert "<!--" not in result
        assert "Navigation Component" not in result

    def test_handles_empty_html(self):
        """Test handling of empty HTML"""
        view = TestLiveView()
        result = view._strip_comments_and_whitespace("")
        assert result == ""

    def test_handles_comments_with_special_chars(self):
        """Test comments with special characters"""
        view = TestLiveView()
        html = "<div><!-- Comment with <, >, & special chars --><span>Content</span></div>"
        result = view._strip_comments_and_whitespace(html)

        assert "<!--" not in result
        assert "<span>Content</span>" in result


class TestVDOMBaselineAlignment:
    """Test that VDOM baseline matches client DOM"""

    def test_template_stripped_before_baseline(self):
        """Test that template stripping preserves structure while removing comments"""
        view = TestLiveView()
        html = """<div dj-root>
            <!-- Component Comment -->
            <nav>Content</nav>
        </div>"""

        stripped = view._strip_comments_and_whitespace(html)

        # Comments should be stripped
        assert "<!--" not in stripped
        assert "Component Comment" not in stripped
        # Content preserved (whitespace collapsed)
        assert "<nav>Content</nav>" in stripped
        assert "dj-root" in stripped

    def test_template_stripping_idempotent(self):
        """Test that stripping is idempotent (multiple calls produce same result)"""
        view = TestLiveView()
        html = "<div><!-- Comment --><span>Content</span></div>"

        result1 = view._strip_comments_and_whitespace(html)
        result2 = view._strip_comments_and_whitespace(result1)

        assert result1 == result2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
