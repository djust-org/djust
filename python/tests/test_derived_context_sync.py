"""
Tests for derived context value sync correctness (#774).

Verifies that container-typed context values (dicts, lists) are tracked by
value equality (not id()) so derived values are correctly detected as changed.
Prevents stale renders when a handler changes an index/key and a computed
dict/list changes as a result.
"""

import pytest

try:
    from djust import LiveView, RustLiveView
except ImportError:
    LiveView = None
    RustLiveView = None

pytestmark = pytest.mark.skipif(
    LiveView is None or RustLiveView is None,
    reason="djust.LiveView / RustLiveView not available",
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

DICT_CONTEXT_TEMPLATE = """
<div dj-root>
  <h1>{{ info.title }}</h1>
  <p>{{ info.count }}</p>
</div>
"""

LIST_CONTEXT_TEMPLATE = """
<div dj-root>
  {% for item in items %}<span>{{ item }}</span>{% endfor %}
</div>
"""


class WizardView(LiveView):
    """View that computes current_step from step_index."""

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


def _make_view(view_cls, template):
    """Create a view with Rust backend, simulate mount, and do initial render."""
    view = view_cls()
    view.template_name = "inline"
    view._full_template = template
    view.step_index = 0
    view._steps = ["step_a", "step_b"]
    view._rust_view = RustLiveView(template)
    view._sync_state_to_rust()
    view._rust_view.render_with_diff()
    return view


class TestDerivedContextAutoDetection:
    """Container-typed derived values must be synced automatically (#774).

    The developer should NOT need _force_full_html for this to work.
    """

    def test_derived_string_from_list_lookup(self):
        """Changing step_index should automatically sync current_step
        (a string looked up from a list) without _force_full_html."""
        view = _make_view(WizardView, WIZARD_TEMPLATE)

        view.step_index = 1
        view._changed_keys = {"step_index"}

        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        assert "Step B" in html
        assert "Step A" not in html
        assert "Index: 1" in html

    def test_derived_dict_auto_synced(self):
        """A dict context value computed in get_context_data must be
        re-sent to Rust even if only a source attribute changed."""

        class DictView(LiveView):
            template_name = None

            def get_template(self):
                return DICT_CONTEXT_TEMPLATE

            def mount(self, request, **kwargs):
                self.title = "Hello"
                self.count = 0

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["info"] = {"title": self.title, "count": self.count}
                return ctx

        view = DictView()
        view.template_name = "inline"
        view._full_template = DICT_CONTEXT_TEMPLATE
        view.title = "Hello"
        view.count = 0
        view._rust_view = RustLiveView(DICT_CONTEXT_TEMPLATE)
        view._sync_state_to_rust()
        view._rust_view.render_with_diff()

        # Change count, which changes the derived info dict
        view.count = 42
        view._changed_keys = {"count"}
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        assert "42" in html

    def test_derived_list_auto_synced(self):
        """A list context value computed from instance state must be
        re-sent to Rust automatically."""

        class ListView(LiveView):
            template_name = None

            def get_template(self):
                return LIST_CONTEXT_TEMPLATE

            def mount(self, request, **kwargs):
                self._data = ["a", "b", "c"]
                self.filter_len = 3

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["items"] = self._data[: self.filter_len]
                return ctx

        view = ListView()
        view.template_name = "inline"
        view._full_template = LIST_CONTEXT_TEMPLATE
        view._data = ["a", "b", "c"]
        view.filter_len = 3
        view._rust_view = RustLiveView(LIST_CONTEXT_TEMPLATE)
        view._sync_state_to_rust()
        view._rust_view.render_with_diff()

        # Change filter to show only 1 item
        view.filter_len = 1
        view._changed_keys = {"filter_len"}
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        # Rust adds dj-id attrs, so count <span dj-id=
        assert html.count("<span") == 1
        assert ">a<" in html
        # b and c should not be rendered
        assert ">b<" not in html

    def test_step_transition_roundtrip(self):
        """Full roundtrip: step 0 → step 1 → step 0 should all render correctly
        without _force_full_html."""
        view = _make_view(WizardView, WIZARD_TEMPLATE)

        # Step 0 → 1
        view.step_index = 1
        view._changed_keys = {"step_index"}
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()
        assert "Step B" in html

        # Step 1 → 0
        view.step_index = 0
        view._changed_keys = {"step_index"}
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()
        assert "Step A" in html

    def test_force_full_html_with_no_changed_keys(self):
        """When _force_full_html is True and _changed_keys is None (the websocket
        pattern), ALL context must be sent and set_changed_keys must be called
        so Rust's partial renderer re-renders affected nodes (#783)."""
        view = _make_view(WizardView, WIZARD_TEMPLATE)

        # Simulate the websocket pattern: handler sets _force_full_html,
        # websocket.py skips _changed_keys computation
        view.step_index = 1
        view._force_full_html = True
        view._changed_keys = None

        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        # Must show Step B despite no explicit _changed_keys
        assert "Step B" in html
        assert "Step A" not in html

    def test_unchanged_container_not_resent(self):
        """When a container value hasn't changed, it should NOT be
        re-sent to Rust (optimization preserved)."""
        view = _make_view(WizardView, WIZARD_TEMPLATE)

        # Change something unrelated — current_step should NOT be re-sent
        view.step_index = 0  # Same as initial value
        view._changed_keys = {"step_index"}
        view._sync_state_to_rust()
        html, _, _ = view._rust_view.render_with_diff()

        # Should still show Step A (no change)
        assert "Step A" in html
        assert "Index: 0" in html
