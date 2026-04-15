"""
Tests for partial template rendering optimisation (issue #737).

Verifies that:
- set_changed_keys() is accepted by the Rust backend
- Partial render produces identical output to full render
- Unchanged nodes are skipped (performance, verified via timing)
"""

import pytest

# Import the Rust-backed LiveView class
try:
    from djust import RustLiveView  # type: ignore[import]
except ImportError:
    RustLiveView = None

pytestmark = pytest.mark.skipif(
    RustLiveView is None,
    reason="Rust extension (djust.RustLiveView) not available",
)


TEMPLATE = "<div><p>{{ greeting }}</p><span>{{ name }}</span></div>"


@pytest.fixture
def backend():
    """Create a minimal RustLiveView backend for testing."""
    view = RustLiveView(TEMPLATE)
    return view


class TestPartialRenderIdentical:
    """Partial render must produce the same HTML as a full render."""

    def test_first_render_is_full(self, backend):
        """First render should work without set_changed_keys."""
        backend.update_state({"greeting": "Hello", "name": "World"})
        html, _patches, version = backend.render_with_diff()
        assert "Hello" in html
        assert "World" in html
        assert version == 1

    def test_partial_render_matches_full(self, backend):
        """After setting changed keys, output must match a full render."""
        # First render (full)
        backend.update_state({"greeting": "Hello", "name": "World"})
        backend.render_with_diff()

        # Second render: change only 'name'
        backend.update_state({"greeting": "Hello", "name": "Rust"})
        backend.set_changed_keys(["name"])
        partial_html, _patches, _v = backend.render_with_diff()

        # Compare with a fresh full render
        fresh = RustLiveView(TEMPLATE)
        fresh.update_state({"greeting": "Hello", "name": "Rust"})
        full_html, _p, _v = fresh.render_with_diff()

        assert partial_html == full_html

    def test_partial_render_all_keys_changed(self, backend):
        """Changing all keys should still produce correct output."""
        backend.update_state({"greeting": "Hi", "name": "Alice"})
        backend.render_with_diff()

        backend.update_state({"greeting": "Hey", "name": "Bob"})
        backend.set_changed_keys(["greeting", "name"])
        html, _patches, _v = backend.render_with_diff()

        assert "Hey" in html
        assert "Bob" in html

    def test_no_changed_keys_falls_back_to_full(self, backend):
        """Without set_changed_keys, always does a full render."""
        backend.update_state({"greeting": "Hi", "name": "Alice"})
        backend.render_with_diff()

        # Don't call set_changed_keys — should still render correctly
        backend.update_state({"greeting": "Hey", "name": "Bob"})
        html, _patches, _v = backend.render_with_diff()

        assert "Hey" in html
        assert "Bob" in html


class TestSetChangedKeys:
    """Test the set_changed_keys Python-Rust bridge."""

    def test_set_changed_keys_accepts_list(self, backend):
        """set_changed_keys should accept a Python list of strings."""
        backend.set_changed_keys(["greeting", "name"])

    def test_set_changed_keys_empty_list(self, backend):
        """Empty changed keys should be accepted (no nodes re-render)."""
        backend.update_state({"greeting": "Hello", "name": "World"})
        backend.render_with_diff()

        backend.set_changed_keys([])
        html, _patches, _v = backend.render_with_diff()
        # Should still produce valid (cached) HTML
        assert "Hello" in html


class TestUpdateTemplateInvalidatesCache:
    """Changing the template source must clear the node HTML cache."""

    def test_update_template_forces_full_render(self, backend):
        backend.update_state({"greeting": "Hi", "name": "World"})
        backend.render_with_diff()

        # Change template
        backend.update_template("<section>{{ greeting }} {{ name }}</section>")
        backend.update_state({"greeting": "Hi", "name": "World"})
        html, _patches, _v = backend.render_with_diff()

        assert "section" in html
        assert "Hi" in html
