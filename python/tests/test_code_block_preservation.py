"""Test that code blocks preserve whitespace and newlines"""

from djust.live_view import LiveView


class TestCodeBlockPreservation:
    """Test that _strip_comments_and_whitespace preserves formatting in code blocks"""

    def test_preserves_newlines_in_pre_tags(self):
        """Pre tags should preserve all newlines and whitespace"""
        view = LiveView()
        html = """<div><pre><code class="language-python">def foo():
    return 42

def bar():
    return 100</code></pre></div>"""

        result = view._strip_comments_and_whitespace(html)

        # Should preserve newlines inside <pre>
        assert "\n" in result
        assert "def foo():\n    return 42" in result
        assert "def bar():\n    return 100" in result

    def test_preserves_indentation_in_code_tags(self):
        """Code tags should preserve indentation"""
        view = LiveView()
        html = """<pre><code>class Counter:
    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1</code></pre>"""

        result = view._strip_comments_and_whitespace(html)

        # Should preserve 4-space indentation
        assert "    def __init__(self):" in result
        assert "        self.count = 0" in result
        assert "    def increment(self):" in result

    def test_normalizes_whitespace_outside_code(self):
        """Should still normalize whitespace outside code blocks"""
        view = LiveView()
        html = """<div>

        Some    text    with   extra   spaces

        </div><pre><code>def foo():
    pass</code></pre>"""

        result = view._strip_comments_and_whitespace(html)

        # Should collapse whitespace outside <pre>
        assert "  \n\n        Some    text" not in result
        # But preserve it inside <pre>
        assert "def foo():\n    pass" in result

    def test_preserves_blank_lines_in_code(self):
        """Blank lines in code should be preserved"""
        view = LiveView()
        html = """<pre><code>def foo():
    x = 1

    y = 2

    return x + y</code></pre>"""

        result = view._strip_comments_and_whitespace(html)

        # Count newlines (should have at least 5)
        assert result.count("\n") >= 5

    def test_handles_nested_pre_code(self):
        """Should handle nested <pre><code> structure"""
        view = LiveView()
        html = """<div class="code-block-modern">
        <pre><code class="language-python">from djust import LiveView

class CounterView(LiveView):
    def mount(self):
        self.count = 0</code></pre>
    </div>"""

        result = view._strip_comments_and_whitespace(html)

        # Should preserve newlines and indentation
        assert "from djust import LiveView\n\nclass CounterView" in result
        assert "    def mount(self):" in result
        assert "        self.count = 0" in result
