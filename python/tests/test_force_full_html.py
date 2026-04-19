"""
Tests for _force_full_html rendering correctness (#774).

Verifies that when _force_full_html is set, ALL context values are synced
to Rust — not just the explicitly changed ones. This ensures derived context
values (computed in get_context_data from instance attrs) are fresh.
"""

import pytest

try:
    from djust import LiveView
except ImportError:
    LiveView = None

pytestmark = pytest.mark.skipif(
    LiveView is None,
    reason="djust.LiveView not available",
)


WIZARD_TEMPLATE = """
<div dj-root>
  {% if current_step == "step_a" %}
    <h2>Step A</h2>
  {% elif current_step == "step_b" %}
    <h2>Step B</h2>
  {% endif %}
  <p>Index: {{ step_index }}</p>
</div>
"""


class WizardView(LiveView):
    """Minimal wizard-style view that computes current_step from step_index."""

    template_name = None

    def get_template(self):
        return WIZARD_TEMPLATE

    def mount(self, request, **kwargs):
        self.step_index = 0
        self._steps = ["step_a", "step_b"]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["current_step"] = self._steps[self.step_index]
        ctx["step_index"] = self.step_index
        return ctx


class TestForceFullHtmlSyncsAllContext:
    """_force_full_html must bypass change tracking (#774)."""

    def _make_view(self):
        from djust import RustLiveView

        view = WizardView()
        view.template_name = "inline"
        view._full_template = WIZARD_TEMPLATE
        # Simulate mount
        view.step_index = 0
        view._steps = ["step_a", "step_b"]
        # Initialize Rust view directly (bypass request-based init)
        view._rust_view = RustLiveView(WIZARD_TEMPLATE)
        # First render — establishes baseline
        view._sync_state_to_rust()
        view._rust_view.render_with_diff()
        return view

    def test_without_force_full_html_derived_value_may_be_stale(self):
        """Demonstrate the bug: changing step_index without force_full_html
        may not sync current_step if id() comparison misses it."""
        view = self._make_view()

        # Simulate event handler changing step_index
        view.step_index = 1
        view._changed_keys = {"step_index"}

        # Sync with change tracking
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        # With the fix, this should show Step B even without _force_full_html
        # because the id() comparison should detect the new string.
        # But the test documents the pattern — derived string values
        # that happen to be interned can fool id() tracking.
        assert "Index: 1" in html

    def test_force_full_html_syncs_derived_context(self):
        """With _force_full_html, ALL context values must be sent to Rust,
        bypassing change tracking. This ensures derived values are fresh."""
        view = self._make_view()

        # Simulate event handler changing step_index and setting force flag
        view.step_index = 1
        view._changed_keys = {"step_index"}
        view._force_full_html = True

        # Sync with force_full_html — should bypass change tracking
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        # Must show Step B content (derived from step_index=1)
        assert "Step B" in html
        assert "Step A" not in html
        assert "Index: 1" in html

    def test_force_full_html_resets_baseline_for_next_cycle(self):
        """After force_full_html, the next render cycle should have
        a clean baseline for change tracking."""
        view = self._make_view()

        # Force full sync
        view.step_index = 1
        view._changed_keys = {"step_index"}
        view._force_full_html = True
        view._sync_state_to_rust()
        view._rust_view.render_with_diff()

        # Next cycle: change step_index again (normal, no force)
        view.step_index = 0
        view._changed_keys = {"step_index"}
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        # Should show Step A
        assert "Step A" in html
        assert "Index: 0" in html
