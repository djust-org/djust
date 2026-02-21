"""
Tests for SafeString handling in nested structures (issue #317).

Verifies that mark_safe() HTML is preserved when stored in:
- Top-level view attributes (regression test)
- List of dicts
- Nested dicts
- Deeply nested structures
"""
import pytest
from django.test import RequestFactory
from django.utils.safestring import mark_safe

from djust import LiveView


class SafeNestedView(LiveView):
    """Test view with SafeString in various nested structures"""

    template_name = None
    template = """
    <div dj-view>
        <div id="top">{{ top_level }}</div>
        <div id="list">
        {% for item in items %}
            <span>{{ item.content }}</span>
        {% endfor %}
        </div>
        <div id="dict">{{ data.content }}</div>
        <div id="deep">{{ deep.level1.level2.0.content }}</div>
    </div>
    """

    def mount(self, request, **kwargs):
        self.top_level = mark_safe("<b>Top</b>")
        self.items = [
            {"content": mark_safe("<b>Item 1</b>")},
            {"content": mark_safe("<i>Item 2</i>")},
        ]
        self.data = {"content": mark_safe("<u>Nested</u>")}
        self.deep = {
            "level1": {
                "level2": [{"content": mark_safe("<strong>Deep</strong>")}]
            }
        }


@pytest.fixture
def view():
    """Create a SafeNestedView instance"""
    view = SafeNestedView()
    factory = RequestFactory()
    request = factory.get("/")
    view.setup(request)
    view.mount(request)
    return view


@pytest.fixture
def rendered_html(view):
    """Render the view to HTML"""
    factory = RequestFactory()
    request = factory.get("/")
    return view.render(request)


def test_top_level_safestring_preserved(rendered_html):
    """Top-level SafeString should be preserved (regression test)"""
    assert "<b>Top</b>" in rendered_html
    assert "&lt;b&gt;Top&lt;/b&gt;" not in rendered_html


def test_list_of_dicts_safestring_preserved(rendered_html):
    """SafeString in list of dicts should be preserved"""
    assert "<b>Item 1</b>" in rendered_html
    assert "<i>Item 2</i>" in rendered_html
    assert "&lt;b&gt;Item 1&lt;/b&gt;" not in rendered_html
    assert "&lt;i&gt;Item 2&lt;/i&gt;" not in rendered_html


def test_nested_dict_safestring_preserved(rendered_html):
    """SafeString in nested dict should be preserved"""
    assert "<u>Nested</u>" in rendered_html
    assert "&lt;u&gt;Nested&lt;/u&gt;" not in rendered_html


def test_deeply_nested_safestring_preserved(rendered_html):
    """SafeString in deeply nested structure should be preserved"""
    assert "<strong>Deep</strong>" in rendered_html
    assert "&lt;strong&gt;Deep&lt;/strong&gt;" not in rendered_html


def test_collect_safe_keys_function():
    """Test the _collect_safe_keys helper function directly"""
    from djust.mixins.rust_bridge import _collect_safe_keys

    # Top-level SafeString
    result = _collect_safe_keys(mark_safe("<b>Bold</b>"), "top")
    assert result == ["top"]

    # List of dicts with SafeString
    items = [
        {"content": mark_safe("<b>Item 1</b>")},
        {"content": mark_safe("<i>Item 2</i>")},
    ]
    result = _collect_safe_keys(items, "items")
    assert "items.0.content" in result
    assert "items.1.content" in result

    # Nested dict with SafeString
    data = {"content": mark_safe("<u>Nested</u>")}
    result = _collect_safe_keys(data, "data")
    assert result == ["data.content"]

    # Deeply nested structure
    deep = {
        "level1": {
            "level2": [{"content": mark_safe("<strong>Deep</strong>")}]
        }
    }
    result = _collect_safe_keys(deep, "deep")
    assert "deep.level1.level2.0.content" in result

    # Non-SafeString values should return empty list
    result = _collect_safe_keys("regular string", "key")
    assert result == []

    result = _collect_safe_keys({"key": "value"}, "data")
    assert result == []


def test_websocket_update_preserves_safestring(view):
    """SafeString should be preserved after WebSocket state updates"""
    factory = RequestFactory()
    request = factory.get("/")

    # Initial render
    html1 = view.render(request)
    assert "<b>Item 1</b>" in html1

    # Simulate state change
    view.items = [{"content": mark_safe("<em>Updated</em>")}]

    # Re-render
    html2 = view.render(request)
    assert "<em>Updated</em>" in html2
    assert "&lt;em&gt;Updated&lt;/em&gt;" not in html2


def test_circular_reference_protection():
    """_collect_safe_keys should handle circular references without crashing"""
    from djust.mixins.rust_bridge import _collect_safe_keys

    # Create a circular reference: tree with parent references
    tree = {"name": "root", "children": []}
    child = {"name": "child", "parent": tree, "content": mark_safe("<b>Child</b>")}
    tree["children"].append(child)

    # Should not raise RecursionError
    result = _collect_safe_keys(tree, "tree")

    # Should find the SafeString in the child
    assert "tree.children.0.content" in result

    # More complex case: bidirectional graph
    node_a = {"id": "A", "neighbors": [], "label": mark_safe("<i>A</i>")}
    node_b = {"id": "B", "neighbors": [], "label": mark_safe("<u>B</u>")}
    node_a["neighbors"].append(node_b)
    node_b["neighbors"].append(node_a)

    # Should not crash
    result = _collect_safe_keys(node_a, "node_a")
    assert "node_a.label" in result
    assert "node_a.neighbors.0.label" in result

    # Self-referential case
    self_ref = {"data": mark_safe("<b>Self</b>")}
    self_ref["self"] = self_ref

    # Should not crash
    result = _collect_safe_keys(self_ref, "self_ref")
    assert "self_ref.data" in result
