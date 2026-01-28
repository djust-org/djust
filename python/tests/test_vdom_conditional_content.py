"""
Test VDOM diffing with conditional content ({% if %} blocks).

This test verifies that when conditional content is shown/hidden,
the VDOM diff correctly detects the change and generates patches.

Bug: When a boolean state toggles and shows/hides a conditional block,
the VDOM diff returns empty patches even though the HTML has changed.
"""

import pytest
import json
from djust import LiveView
from djust.decorators import event_handler
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware


# Marker to check if panel is in HTML
PANEL_MARKER = 'class="panel"'


class ConditionalContentView(LiveView):
    """Test view with conditional content that can be shown/hidden."""

    template = """<div data-djust-root>
    <div class="container">
        <button dj-click="toggle_panel">Toggle</button>
        {% if show_panel %}
        <div class="panel">
            <span>Panel Content</span>
            <button dj-click="close_panel">Close</button>
        </div>
        {% endif %}
    </div>
</div>"""

    def mount(self, request):
        self.show_panel = False

    @event_handler()
    def toggle_panel(self, **kwargs):
        """Toggle the panel visibility."""
        self.show_panel = not self.show_panel

    @event_handler()
    def close_panel(self, **kwargs):
        """Close the panel."""
        self.show_panel = False


def add_session_to_request(request):
    """Helper to add session to request."""
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


@pytest.mark.django_db
def test_conditional_content_show_generates_patches():
    """Test that showing conditional content generates patches.

    This is the core bug: toggling show_panel from False to True
    should generate patches to insert the panel div.
    """
    view = ConditionalContentView()
    factory = RequestFactory()

    # Initial request to mount the view
    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # First render - establishes VDOM baseline (panel hidden)
    html1, patches1, version1 = view.render_with_diff(request)

    print(f"\n[TEST] Initial render (panel hidden): version={version1}")
    has_panel = PANEL_MARKER in html1
    print(f"[TEST] HTML contains panel: {has_panel}")
    assert not has_panel, "Panel div should NOT be in initial HTML"

    # Toggle panel ON
    view.toggle_panel()
    html2, patches2, version2 = view.render_with_diff(request)

    print(f"[TEST] After toggle (panel shown): version={version2}")
    has_panel2 = PANEL_MARKER in html2
    print(f"[TEST] HTML contains panel: {has_panel2}")
    print(f"[TEST] patches2: {patches2}")

    assert has_panel2, "Panel div SHOULD be in HTML after toggle"

    # THIS IS THE BUG: patches should NOT be empty!
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        print(f"[TEST] Patch count: {len(patches_list)}")
        print(f"[TEST] Patch types: {[p.get('type') for p in patches_list]}")
        assert len(patches_list) > 0, "Should have at least one patch to insert the panel"
    else:
        pytest.fail("Patches should not be empty when showing conditional content")


@pytest.mark.django_db
def test_conditional_content_hide_generates_patches():
    """Test that hiding conditional content generates patches."""
    view = ConditionalContentView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # First render with panel hidden
    view.render_with_diff(request)

    # Show panel
    view.toggle_panel()
    html1, patches1, version1 = view.render_with_diff(request)
    assert PANEL_MARKER in html1, "Panel should be visible"

    # Hide panel
    view.toggle_panel()
    html2, patches2, version2 = view.render_with_diff(request)

    print(f"\n[TEST] After hiding panel: version={version2}")
    has_panel = PANEL_MARKER in html2
    print(f"[TEST] HTML contains panel: {has_panel}")
    print(f"[TEST] patches2: {patches2}")

    assert not has_panel, "Panel should NOT be in HTML after hiding"

    # Patches should not be empty
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        print(f"[TEST] Patch count: {len(patches_list)}")
        assert len(patches_list) > 0, "Should have at least one patch to remove the panel"
    else:
        pytest.fail("Patches should not be empty when hiding conditional content")


@pytest.mark.django_db
def test_conditional_content_button_count_changes():
    """Test that button count changes when panel is shown/hidden."""
    view = ConditionalContentView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # First render - should have 1 button (Toggle)
    html1, _, _ = view.render_with_diff(request)
    button_count_hidden = html1.count("<button")
    print(f"\n[TEST] Button count with panel hidden: {button_count_hidden}")
    assert button_count_hidden == 1, "Should have 1 button when panel hidden"

    # Show panel - should have 2 buttons (Toggle + Close)
    view.toggle_panel()
    html2, patches2, _ = view.render_with_diff(request)
    button_count_shown = html2.count("<button")
    print(f"[TEST] Button count with panel shown: {button_count_shown}")
    assert button_count_shown == 2, "Should have 2 buttons when panel shown"

    # Verify patches detected the new button
    if patches2:
        patches_list = json.loads(patches2)
        print(f"[TEST] Patches: {patches_list}")
        # Should have patches for inserting the panel (which contains the Close button)
        assert len(patches_list) > 0, "Should have patches for the new content"


@pytest.mark.django_db
def test_conditional_content_with_whitespace():
    """Test that conditional content works even with whitespace between elements.

    This mimics the djust_chat template structure where there's whitespace
    between the button and the {% if %} block.
    """

    class WhitespaceView(LiveView):
        """Test view with whitespace around conditional content."""

        # Note the blank line between </button> and {% if %}
        template = """<div data-djust-root>
    <div class="model-config">
        <button dj-click="toggle">Toggle</button>

        {% if show_panel %}
        <div class="panel">
            <span>Panel Content</span>
            <button dj-click="close">Close</button>
        </div>
        {% endif %}
    </div>
</div>"""

        def mount(self, request):
            self.show_panel = False

        @event_handler()
        def toggle(self, **kwargs):
            self.show_panel = not self.show_panel

    view = WhitespaceView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # First render - panel hidden
    html1, patches1, version1 = view.render_with_diff(request)
    button_count1 = html1.count("<button")
    print(f"\n[TEST] Initial render: {button_count1} buttons")
    assert button_count1 == 1, "Should have 1 button when panel hidden"

    # Toggle panel ON
    view.toggle()
    html2, patches2, version2 = view.render_with_diff(request)
    button_count2 = html2.count("<button")
    print(f"[TEST] After toggle: {button_count2} buttons")
    print(f"[TEST] patches2: {patches2}")

    assert button_count2 == 2, "Should have 2 buttons when panel shown"

    # Patches should include the new panel
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        print(f"[TEST] Patch count: {len(patches_list)}")
        print(f"[TEST] Patch types: {[p.get('type') for p in patches_list]}")
        assert len(patches_list) > 0, "Should have patches for the new panel"
    else:
        pytest.fail("Patches should not be empty")


@pytest.mark.django_db
def test_conditional_content_with_conditional_class():
    """Test conditional content + conditional class (like djust_chat model-config).

    This mimics the actual djust_chat structure where:
    1. The toggle button has {% if show_panel %}btn--active{% endif %} class
    2. The panel is a sibling conditional block

    Both the button attrs AND the sibling children change simultaneously.
    """

    class ConditionalClassView(LiveView):
        """Test view with conditional class on button + conditional sibling."""

        # Matches djust_chat's model-config structure more closely
        template = """<div data-djust-root>
    <div class="model-config">
        <button
            class="btn btn--icon {% if show_panel %}btn--active{% endif %}"
            dj-click="toggle"
            title="Settings"
        >Settings</button>

        {% if show_panel %}
        <div class="panel">
            <div class="panel__header">
                <span>Settings</span>
                <button class="btn btn--small" dj-click="reset">Reset</button>
            </div>
            <div class="panel__option">
                <label>Temperature</label>
                <input type="range" min="0" max="2" step="0.1" value="{{ temperature }}" dj-input="set_temp">
            </div>
        </div>
        {% endif %}
    </div>
</div>"""

        def mount(self, request):
            self.show_panel = False
            self.temperature = 0.7

        @event_handler()
        def toggle(self, **kwargs):
            self.show_panel = not self.show_panel

    view = ConditionalClassView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # First render - panel hidden
    html1, patches1, version1 = view.render_with_diff(request)
    button_count1 = html1.count("<button")
    has_active = "btn--active" in html1
    print(f"\n[TEST] Initial render: {button_count1} buttons, btn--active={has_active}")
    assert button_count1 == 1, "Should have 1 button when panel hidden"
    assert not has_active, "Should NOT have btn--active when panel hidden"

    # Toggle panel ON
    view.toggle()
    html2, patches2, version2 = view.render_with_diff(request)
    button_count2 = html2.count("<button")
    has_active2 = "btn--active" in html2
    print(f"[TEST] After toggle: {button_count2} buttons, btn--active={has_active2}")
    print(f"[TEST] patches2: {patches2}")

    assert button_count2 == 2, "Should have 2 buttons when panel shown"
    assert has_active2, "Should have btn--active when panel shown"

    # Patches should include BOTH:
    # 1. SetAttr for the btn--active class change on toggle button
    # 2. InsertChild for the panel div
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        print(f"[TEST] Patch count: {len(patches_list)}")
        patch_types = [p.get("type") for p in patches_list]
        print(f"[TEST] Patch types: {patch_types}")

        # We expect at least 2 patches:
        # - SetAttr for class change on button
        # - InsertChild for the panel
        assert len(patches_list) >= 2, f"Should have at least 2 patches, got {len(patches_list)}"
        assert "SetAttr" in patch_types, "Should have SetAttr for class change"
        assert "InsertChild" in patch_types, "Should have InsertChild for panel"
    else:
        pytest.fail("Patches should not be empty")


@pytest.mark.django_db
def test_conditional_content_with_svg_icon():
    """Test conditional content with SVG icon inside button (like djust_chat).

    The djust_chat model-config button has an SVG icon inside it.
    This tests if complex SVG content inside the button causes diffing issues.
    """

    class SvgIconView(LiveView):
        """Test view with SVG icon inside toggle button."""

        template = """<div data-djust-root>
    <div class="model-config">
        <button
            class="btn btn--icon {% if show_panel %}btn--active{% endif %}"
            dj-click="toggle"
            title="Settings"
        >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="3"/>
                <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06"/>
            </svg>
        </button>

        {% if show_panel %}
        <div class="panel">
            <div class="panel__header">
                <span>Settings</span>
                <button class="btn btn--small" dj-click="reset">Reset</button>
            </div>
            <div class="panel__option">
                <label>Temperature</label>
                <input type="range" min="0" max="2" step="0.1" value="{{ temperature }}" dj-input="set_temp">
            </div>
        </div>
        {% endif %}
    </div>
</div>"""

        def mount(self, request):
            self.show_panel = False
            self.temperature = 0.7

        @event_handler()
        def toggle(self, **kwargs):
            self.show_panel = not self.show_panel

    view = SvgIconView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # First render - panel hidden
    html1, patches1, version1 = view.render_with_diff(request)
    button_count1 = html1.count("<button")
    has_svg = "<svg" in html1
    print(f"\n[TEST] Initial render: {button_count1} buttons, has_svg={has_svg}")
    assert button_count1 == 1, "Should have 1 button when panel hidden"
    assert has_svg, "Should have SVG icon"

    # Toggle panel ON
    view.toggle()
    html2, patches2, version2 = view.render_with_diff(request)
    button_count2 = html2.count("<button")
    print(f"[TEST] After toggle: {button_count2} buttons")
    print(f"[TEST] patches2: {patches2[:500] if patches2 else 'None'}...")

    assert button_count2 == 2, "Should have 2 buttons when panel shown"

    # Verify patches
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        patch_types = [p.get("type") for p in patches_list]
        print(f"[TEST] Patch count: {len(patches_list)}")
        print(f"[TEST] Patch types: {patch_types}")
        assert "InsertChild" in patch_types, "Should have InsertChild for panel"
    else:
        pytest.fail("Patches should not be empty")


@pytest.mark.django_db
def test_multiple_sibling_conditionals():
    """Test multiple sibling conditional blocks (like djust_chat header).

    djust_chat has multiple sibling conditional blocks in the header:
    - prompt-selector with {% if show_prompt_selector %}
    - model-config with {% if show_model_config %}
    - export-menu with {% if show_export_menu %}

    This tests that toggling one doesn't interfere with others.
    """

    class MultipleSiblingConditionalsView(LiveView):
        """Test view with multiple sibling conditional blocks."""

        template = """<div data-djust-root>
    <main class="chat-main">
        <header class="chat-header">
            <div class="header__controls">
                <!-- First conditional: prompt selector -->
                <div class="prompt-selector">
                    <button class="btn" dj-click="toggle_prompt">Prompts</button>
                    {% if show_prompt %}
                    <div class="prompt-dropdown">
                        <span>Prompt Options</span>
                        <button dj-click="select_prompt">Select</button>
                    </div>
                    {% endif %}
                </div>

                <!-- Second conditional: model config -->
                <div class="model-config">
                    <button
                        class="btn {% if show_model_config %}btn--active{% endif %}"
                        dj-click="toggle_model_config"
                    >Settings</button>
                    {% if show_model_config %}
                    <div class="model-config__panel">
                        <span>Model Settings</span>
                        <button dj-click="reset">Reset</button>
                        <input type="range" min="0" max="2" step="0.1" value="{{ temperature }}">
                    </div>
                    {% endif %}
                </div>

                <!-- Third conditional: export menu -->
                <div class="export-menu">
                    <button class="btn" dj-click="toggle_export">Export</button>
                    {% if show_export %}
                    <div class="export-dropdown">
                        <a href="#">Export JSON</a>
                        <a href="#">Export Markdown</a>
                    </div>
                    {% endif %}
                </div>
            </div>
        </header>
    </main>
</div>"""

        def mount(self, request):
            self.show_prompt = False
            self.show_model_config = False
            self.show_export = False
            self.temperature = 0.7

        @event_handler()
        def toggle_prompt(self, **kwargs):
            self.show_prompt = not self.show_prompt
            self.show_model_config = False
            self.show_export = False

        @event_handler()
        def toggle_model_config(self, **kwargs):
            self.show_model_config = not self.show_model_config
            self.show_prompt = False
            self.show_export = False

        @event_handler()
        def toggle_export(self, **kwargs):
            self.show_export = not self.show_export
            self.show_prompt = False
            self.show_model_config = False

    view = MultipleSiblingConditionalsView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # Initial render - all panels hidden
    html1, patches1, version1 = view.render_with_diff(request)
    button_count1 = html1.count("<button")
    print(f"\n[TEST] Initial: {button_count1} buttons")
    assert button_count1 == 3, "Should have 3 buttons initially (one per section)"

    # Toggle model config ON
    view.toggle_model_config()
    html2, patches2, version2 = view.render_with_diff(request)
    button_count2 = html2.count("<button")
    has_panel = "model-config__panel" in html2
    print(f"[TEST] After toggle_model_config: {button_count2} buttons, has_panel={has_panel}")
    print(f"[TEST] patches2: {patches2[:300] if patches2 else 'None'}...")

    assert button_count2 == 4, "Should have 4 buttons (added Reset button)"
    assert has_panel, "Should have model-config__panel"

    # Verify patches
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        patch_types = [p.get("type") for p in patches_list]
        print(f"[TEST] Patch count: {len(patches_list)}")
        print(f"[TEST] Patch types: {patch_types}")
        assert "InsertChild" in patch_types, "Should have InsertChild for panel"
    else:
        pytest.fail("Patches should not be empty when showing model config")


@pytest.mark.django_db
def test_deeply_nested_conditional():
    """Test conditional content in deeply nested structure.

    This mimics djust_chat's actual nesting:
    div[data-djust-root] > main > header > div.controls > div.model-config > {% if %}
    """

    class DeeplyNestedView(LiveView):
        """Test view with deeply nested conditional."""

        template = """<div data-djust-root>
    <main class="chat-main">
        <header class="chat-header">
            <div class="chat-header__controls">
                <select class="select">
                    <option value="a">Option A</option>
                    <option value="b">Option B</option>
                </select>
                <div class="model-config">
                    <button
                        class="btn btn--icon {% if show_panel %}btn--active{% endif %}"
                        dj-click="toggle"
                        title="Settings"
                    >
                        <svg width="18" height="18" viewBox="0 0 24 24">
                            <circle cx="12" cy="12" r="3"/>
                        </svg>
                    </button>

                    {% if show_panel %}
                    <div class="model-config__panel">
                        <div class="panel-header">
                            <span>Settings</span>
                            <button class="btn btn--small" dj-click="reset">Reset</button>
                        </div>
                        <div class="panel-option">
                            <label>Temperature: {{ temperature }}</label>
                            <input type="range" min="0" max="2" value="{{ temperature }}">
                        </div>
                    </div>
                    {% endif %}
                </div>
                <div class="export-menu">
                    <button class="btn" dj-click="export">Export</button>
                </div>
            </div>
        </header>
        <div class="chat-messages">
            <div class="message">Hello world</div>
        </div>
    </main>
</div>"""

        def mount(self, request):
            self.show_panel = False
            self.temperature = 0.7

        @event_handler()
        def toggle(self, **kwargs):
            self.show_panel = not self.show_panel

    view = DeeplyNestedView()
    factory = RequestFactory()

    request = factory.get("/test/")
    request = add_session_to_request(request)
    view.dispatch(request)

    # Initial render
    html1, patches1, version1 = view.render_with_diff(request)
    button_count1 = html1.count("<button")
    print(f"\n[TEST] Initial: {button_count1} buttons")
    assert button_count1 == 2, "Should have 2 buttons initially"

    # Toggle panel ON
    view.toggle()
    html2, patches2, version2 = view.render_with_diff(request)
    button_count2 = html2.count("<button")
    has_panel = "model-config__panel" in html2
    has_active = "btn--active" in html2
    print(
        f"[TEST] After toggle: {button_count2} buttons, has_panel={has_panel}, btn--active={has_active}"
    )
    print(f"[TEST] patches2: {patches2[:400] if patches2 else 'None'}...")

    assert button_count2 == 3, "Should have 3 buttons (added Reset)"
    assert has_panel, "Should have model-config__panel"
    assert has_active, "Toggle button should have btn--active class"

    # Verify patches
    assert patches2 is not None, "Patches should not be None"
    if patches2:
        patches_list = json.loads(patches2)
        patch_types = [p.get("type") for p in patches_list]
        print(f"[TEST] Patch count: {len(patches_list)}")
        print(f"[TEST] Patch types: {patch_types}")

        # Should have SetAttr for class change AND InsertChild for panel
        assert "SetAttr" in patch_types, "Should have SetAttr for btn--active"
        assert "InsertChild" in patch_types, "Should have InsertChild for panel"
    else:
        pytest.fail("Patches should not be empty")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
