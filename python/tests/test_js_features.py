"""
Tests for client-side JS features — dj-confirm, dj-loading, dj-debounce,
dj-throttle, dj-target, dj-transition, and dj-change blur behavior.

These test that:
1. Server-side views correctly render HTML with the right dj-* attributes
2. Event handlers receive the correct parameters
3. The HTML structure matches what the JS runtime expects

The actual JS execution is tested via browser tests; here we verify the
server-side rendering and handler behavior that enables these features.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
            }
        ],
        SECRET_KEY="test-secret-key",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

import pytest
from djust.testing import LiveViewTestClient, MockUploadFile


# ============================================================================
# Mock Views for Testing
# ============================================================================


class ConfirmDeleteView:
    """View that uses dj-confirm for confirmation dialogs."""

    template_name = "confirm_delete.html"

    def __init__(self):
        self.items = []
        self.deleted_item = None
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.items = kwargs.get("items", ["Item 1", "Item 2", "Item 3"])
        self.deleted_item = None
        self._update_html()

    def delete_item(self, item_id: int = 0):
        """Handler that should only be called after user confirms."""
        if 0 <= item_id < len(self.items):
            self.deleted_item = self.items.pop(item_id)
        self._update_html()

    def delete_all(self):
        """Delete all items - requires confirmation."""
        self.items = []
        self.deleted_item = "all"
        self._update_html()

    def _update_html(self):
        items_html = ""
        for i, item in enumerate(self.items):
            items_html += f'''
                <li class="item" id="item-{i}">
                    {item}
                    <button dj-click="delete_item" data-item-id="{i}"
                            dj-confirm="Are you sure you want to delete {item}?">
                        Delete
                    </button>
                </li>
            '''
        self._html = f'''
            <div id="item-list">
                <ul>{items_html}</ul>
                <button dj-click="delete_all"
                        dj-confirm="Delete ALL items? This cannot be undone!">
                    Delete All
                </button>
            </div>
        '''

    def get_context_data(self):
        return {"items": self.items, "deleted_item": self.deleted_item}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class LoadingStatesView:
    """View that demonstrates dj-loading.* attributes."""

    template_name = "loading_states.html"

    def __init__(self):
        self.result = None
        self.loading_message = ""
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.result = None
        self.loading_message = "Ready"
        self._update_html()

    def slow_action(self):
        """Simulates a slow action that would show loading states."""
        self.result = "Action completed!"
        self.loading_message = "Done"
        self._update_html()

    def fetch_data(self):
        """Another action with loading states."""
        self.result = "Data fetched"
        self._update_html()

    def _update_html(self):
        self._html = f'''
            <div id="loading-demo">
                <!-- Button with dj-loading.disable -->
                <button id="action-btn"
                        dj-click="slow_action"
                        dj-loading.disable>
                    Do Slow Action
                </button>

                <!-- Button with dj-loading.class -->
                <button id="fetch-btn"
                        dj-click="fetch_data"
                        dj-loading.class="btn-loading">
                    Fetch Data
                </button>

                <!-- Loading spinner (hidden by default, shown during loading) -->
                <div id="spinner"
                     style="display: none;"
                     dj-loading.show>
                    Loading...
                </div>

                <!-- Content that hides during loading -->
                <div id="content"
                     dj-loading.hide>
                    {self.result or 'No result yet'}
                </div>

                <!-- Loading indicator with specific display value -->
                <div id="flex-spinner"
                     style="display: none;"
                     dj-loading.show="flex">
                    <span>Spinner</span>
                </div>

                <p id="message">{self.loading_message}</p>
            </div>
        '''

    def get_context_data(self):
        return {"result": self.result, "loading_message": self.loading_message}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class DebounceThrottleView:
    """View for testing dj-debounce and dj-throttle behaviors."""

    template_name = "debounce_throttle.html"

    def __init__(self):
        self.search_query = ""
        self.search_count = 0
        self.slider_value = 50
        self.slider_update_count = 0
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.search_count = 0
        self.slider_value = 50
        self.slider_update_count = 0
        self._update_html()

    def search(self, value: str = ""):
        """Handler called after debounce period."""
        self.search_query = value
        self.search_count += 1
        self._update_html()

    def update_slider(self, value: int = 50):
        """Handler called on throttled slider input."""
        self.slider_value = value
        self.slider_update_count += 1
        self._update_html()

    def _update_html(self):
        self._html = f'''
            <div id="debounce-demo">
                <!-- Search input with debounce -->
                <input type="text"
                       id="search-input"
                       name="search"
                       dj-input="search"
                       data-debounce="300"
                       placeholder="Search..."
                       value="{self.search_query}">

                <p>Search count: <span id="search-count">{self.search_count}</span></p>
                <p>Last query: <span id="last-query">{self.search_query}</span></p>

                <!-- Slider with throttle -->
                <input type="range"
                       id="slider"
                       name="slider"
                       min="0"
                       max="100"
                       dj-input="update_slider"
                       data-throttle="100"
                       value="{self.slider_value}">

                <p>Slider value: <span id="slider-value">{self.slider_value}</span></p>
                <p>Update count: <span id="update-count">{self.slider_update_count}</span></p>
            </div>
        '''

    def get_context_data(self):
        return {
            "search_query": self.search_query,
            "search_count": self.search_count,
            "slider_value": self.slider_value,
            "slider_update_count": self.slider_update_count,
        }

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class TargetedUpdateView:
    """View demonstrating dj-target for scoped DOM updates."""

    template_name = "targeted_update.html"

    def __init__(self):
        self.sidebar_count = 0
        self.main_count = 0
        self.footer_count = 0
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.sidebar_count = 0
        self.main_count = 0
        self.footer_count = 0
        self._update_html()

    def update_sidebar(self):
        """Update only the sidebar section."""
        self.sidebar_count += 1
        self._update_html()

    def update_main(self):
        """Update only the main content."""
        self.main_count += 1
        self._update_html()

    def update_footer(self):
        """Update only the footer."""
        self.footer_count += 1
        self._update_html()

    def _update_html(self):
        self._html = f'''
            <div id="app">
                <aside id="sidebar">
                    <p>Sidebar updates: {self.sidebar_count}</p>
                    <button dj-click="update_sidebar" dj-target="#sidebar">
                        Update Sidebar Only
                    </button>
                </aside>

                <main id="main-content">
                    <p>Main updates: {self.main_count}</p>
                    <button dj-click="update_main" dj-target="#main-content">
                        Update Main Only
                    </button>
                </main>

                <footer id="footer">
                    <p>Footer updates: {self.footer_count}</p>
                    <button dj-click="update_footer" dj-target="#footer">
                        Update Footer Only
                    </button>
                </footer>
            </div>
        '''

    def get_context_data(self):
        return {
            "sidebar_count": self.sidebar_count,
            "main_count": self.main_count,
            "footer_count": self.footer_count,
        }

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class TransitionView:
    """View demonstrating dj-transition for CSS animations."""

    template_name = "transitions.html"

    def __init__(self):
        self.items = []
        self.show_modal = False
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.items = kwargs.get("items", [])
        self.show_modal = False
        self._update_html()

    def add_item(self, text: str = "New Item"):
        """Add item with enter transition."""
        self.items.append(text)
        self._update_html()

    def remove_item(self, index: int = 0):
        """Remove item with leave transition."""
        if 0 <= index < len(self.items):
            self.items.pop(index)
        self._update_html()

    def toggle_modal(self):
        """Toggle modal with fade transition."""
        self.show_modal = not self.show_modal
        self._update_html()

    def _update_html(self):
        items_html = ""
        for i, item in enumerate(self.items):
            items_html += f'''
                <li class="fade-item"
                    dj-transition="fade"
                    id="item-{i}">
                    {item}
                    <button dj-click="remove_item" data-index="{i}">×</button>
                </li>
            '''

        modal_html = ""
        if self.show_modal:
            modal_html = '''
                <div class="modal-overlay"
                     dj-transition="fade"
                     dj-click="toggle_modal">
                    <div class="modal-content"
                         dj-transition-enter="scale-95 opacity-0"
                         dj-transition-enter-to="scale-100 opacity-100"
                         dj-transition-leave="scale-100 opacity-100"
                         dj-transition-leave-to="scale-95 opacity-0">
                        <h2>Modal Title</h2>
                        <p>Modal content here.</p>
                        <button dj-click="toggle_modal">Close</button>
                    </div>
                </div>
            '''

        self._html = f'''
            <div id="transition-demo">
                <button dj-click="add_item" data-text="Item {len(self.items) + 1}">
                    Add Item
                </button>
                <button dj-click="toggle_modal">
                    {'Close' if self.show_modal else 'Open'} Modal
                </button>

                <ul id="item-list">{items_html}</ul>
                {modal_html}
            </div>
        '''

    def get_context_data(self):
        return {"items": self.items, "show_modal": self.show_modal}

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


class BlurChangeView:
    """View for testing dj-change blur behavior on text inputs."""

    template_name = "blur_change.html"

    def __init__(self):
        self.name = ""
        self.email = ""
        self.change_events = []
        self._pending_push_events = []
        self._html = ""

    def _initialize_temporary_assigns(self):
        pass

    def mount(self, request, **kwargs):
        self.name = kwargs.get("name", "")
        self.email = kwargs.get("email", "")
        self.change_events = []
        self._update_html()

    def on_name_change(self, value: str = "", field: str = ""):
        """Called when name field loses focus (blur)."""
        self.name = value
        self.change_events.append(("name", value))
        self._update_html()

    def on_email_change(self, value: str = "", field: str = ""):
        """Called when email field loses focus (blur)."""
        self.email = value
        self.change_events.append(("email", value))
        self._update_html()

    def on_select_change(self, value: str = "", field: str = ""):
        """Called immediately when select changes."""
        self.change_events.append(("select", value))
        self._update_html()

    def _update_html(self):
        events_html = "".join(
            f'<li class="change-event">{field}: {val}</li>'
            for field, val in self.change_events
        )
        self._html = f'''
            <div id="blur-change-demo">
                <!-- Text input: change fires on blur -->
                <input type="text"
                       id="name-input"
                       name="name"
                       dj-change="on_name_change"
                       value="{self.name}"
                       placeholder="Name">

                <!-- Email input: change fires on blur -->
                <input type="email"
                       id="email-input"
                       name="email"
                       dj-change="on_email_change"
                       value="{self.email}"
                       placeholder="Email">

                <!-- Select: change fires immediately -->
                <select id="category-select"
                        name="category"
                        dj-change="on_select_change">
                    <option value="">Select...</option>
                    <option value="a">Option A</option>
                    <option value="b">Option B</option>
                </select>

                <p>Name: <span id="name-display">{self.name}</span></p>
                <p>Email: <span id="email-display">{self.email}</span></p>

                <h3>Change Events:</h3>
                <ul id="events-list">{events_html}</ul>
            </div>
        '''

    def get_context_data(self):
        return {
            "name": self.name,
            "email": self.email,
            "change_events": self.change_events,
        }

    def _drain_push_events(self):
        events = self._pending_push_events
        self._pending_push_events = []
        return events


# Patch render for views that use _html
_original_render = LiveViewTestClient.render


def _patched_render(self):
    if hasattr(self.view_instance, "_html"):
        return self.view_instance._html
    return _original_render(self)


LiveViewTestClient.render = _patched_render


# ============================================================================
# Tests: dj-confirm
# ============================================================================


class TestDjConfirm:
    """Tests for dj-confirm confirmation dialog attribute."""

    def test_renders_confirm_attribute(self):
        """dj-confirm attribute is rendered in HTML."""
        client = LiveViewTestClient(ConfirmDeleteView)
        client.mount()
        html = client.html

        assert 'dj-confirm="Are you sure you want to delete Item 1?"' in html
        assert 'dj-confirm="Delete ALL items? This cannot be undone!"' in html

    def test_handler_called_directly_without_confirm(self):
        """Server handler works when called directly (confirm happens client-side)."""
        client = LiveViewTestClient(ConfirmDeleteView)
        client.mount(items=["A", "B", "C"])

        # In real usage, JS would show confirm dialog first
        # Here we test that handler works when called
        client.click("delete_item", item_id=1)

        assert client.state["items"] == ["A", "C"]
        assert client.state["deleted_item"] == "B"

    def test_delete_all_handler(self):
        """Delete all handler works correctly."""
        client = LiveViewTestClient(ConfirmDeleteView)
        client.mount(items=["A", "B", "C"])

        client.click("delete_all")

        assert client.state["items"] == []
        assert client.state["deleted_item"] == "all"

    def test_confirm_message_dynamic(self):
        """Confirm message can include dynamic content."""
        client = LiveViewTestClient(ConfirmDeleteView)
        client.mount(items=["Special Item"])
        html = client.html

        assert "Are you sure you want to delete Special Item?" in html


# ============================================================================
# Tests: dj-loading
# ============================================================================


class TestDjLoading:
    """Tests for dj-loading.* loading state attributes."""

    def test_renders_loading_disable(self):
        """dj-loading.disable attribute is rendered."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()
        html = client.html

        assert 'dj-loading.disable' in html
        assert 'id="action-btn"' in html

    def test_renders_loading_class(self):
        """dj-loading.class attribute is rendered with class name."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()
        html = client.html

        assert 'dj-loading.class="btn-loading"' in html

    def test_renders_loading_show(self):
        """dj-loading.show attribute is rendered."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()
        html = client.html

        assert 'dj-loading.show' in html
        assert 'id="spinner"' in html

    def test_renders_loading_show_with_display_value(self):
        """dj-loading.show can specify display value like flex."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()
        html = client.html

        assert 'dj-loading.show="flex"' in html
        assert 'id="flex-spinner"' in html

    def test_renders_loading_hide(self):
        """dj-loading.hide attribute is rendered."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()
        html = client.html

        assert 'dj-loading.hide' in html
        assert 'id="content"' in html

    def test_handler_updates_state(self):
        """Handler called during loading state updates view state."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()

        client.click("slow_action")

        assert client.state["result"] == "Action completed!"
        assert client.state["loading_message"] == "Done"

    def test_fetch_data_handler(self):
        """Fetch data handler works correctly."""
        client = LiveViewTestClient(LoadingStatesView)
        client.mount()

        client.click("fetch_data")

        assert client.state["result"] == "Data fetched"


# ============================================================================
# Tests: dj-debounce / dj-throttle
# ============================================================================


class TestDjDebounce:
    """Tests for dj-debounce rate limiting attribute."""

    def test_renders_debounce_attribute(self):
        """data-debounce attribute is rendered."""
        client = LiveViewTestClient(DebounceThrottleView)
        client.mount()
        html = client.html

        assert 'data-debounce="300"' in html
        assert 'dj-input="search"' in html

    def test_search_handler_receives_value(self):
        """Search handler receives the debounced value."""
        client = LiveViewTestClient(DebounceThrottleView)
        client.mount()

        # Simulate debounced input (JS handles actual debouncing)
        client.click("search", value="test query")

        assert client.state["search_query"] == "test query"
        assert client.state["search_count"] == 1

    def test_multiple_search_calls(self):
        """Multiple search calls increment counter."""
        client = LiveViewTestClient(DebounceThrottleView)
        client.mount()

        client.click("search", value="first")
        client.click("search", value="second")
        client.click("search", value="third")

        assert client.state["search_query"] == "third"
        assert client.state["search_count"] == 3


class TestDjThrottle:
    """Tests for dj-throttle rate limiting attribute."""

    def test_renders_throttle_attribute(self):
        """data-throttle attribute is rendered."""
        client = LiveViewTestClient(DebounceThrottleView)
        client.mount()
        html = client.html

        assert 'data-throttle="100"' in html
        assert 'dj-input="update_slider"' in html

    def test_slider_handler_receives_value(self):
        """Slider handler receives the throttled value."""
        client = LiveViewTestClient(DebounceThrottleView)
        client.mount()

        client.click("update_slider", value=75)

        assert client.state["slider_value"] == 75
        assert client.state["slider_update_count"] == 1

    def test_multiple_slider_updates(self):
        """Multiple slider updates increment counter."""
        client = LiveViewTestClient(DebounceThrottleView)
        client.mount()

        client.click("update_slider", value=25)
        client.click("update_slider", value=50)
        client.click("update_slider", value=100)

        assert client.state["slider_value"] == 100
        assert client.state["slider_update_count"] == 3


# ============================================================================
# Tests: dj-target
# ============================================================================


class TestDjTarget:
    """Tests for dj-target scoped DOM update attribute."""

    def test_renders_target_attribute(self):
        """dj-target attribute is rendered with CSS selector."""
        client = LiveViewTestClient(TargetedUpdateView)
        client.mount()
        html = client.html

        assert 'dj-target="#sidebar"' in html
        assert 'dj-target="#main-content"' in html
        assert 'dj-target="#footer"' in html

    def test_update_sidebar_only(self):
        """Sidebar update only affects sidebar state."""
        client = LiveViewTestClient(TargetedUpdateView)
        client.mount()

        client.click("update_sidebar")

        assert client.state["sidebar_count"] == 1
        assert client.state["main_count"] == 0
        assert client.state["footer_count"] == 0

    def test_update_main_only(self):
        """Main update only affects main state."""
        client = LiveViewTestClient(TargetedUpdateView)
        client.mount()

        client.click("update_main")

        assert client.state["sidebar_count"] == 0
        assert client.state["main_count"] == 1
        assert client.state["footer_count"] == 0

    def test_update_footer_only(self):
        """Footer update only affects footer state."""
        client = LiveViewTestClient(TargetedUpdateView)
        client.mount()

        client.click("update_footer")

        assert client.state["sidebar_count"] == 0
        assert client.state["main_count"] == 0
        assert client.state["footer_count"] == 1

    def test_independent_updates(self):
        """Multiple targeted updates are independent."""
        client = LiveViewTestClient(TargetedUpdateView)
        client.mount()

        client.click("update_sidebar")
        client.click("update_main")
        client.click("update_main")
        client.click("update_footer")
        client.click("update_sidebar")

        assert client.state["sidebar_count"] == 2
        assert client.state["main_count"] == 2
        assert client.state["footer_count"] == 1


# ============================================================================
# Tests: dj-transition
# ============================================================================


class TestDjTransition:
    """Tests for dj-transition CSS animation attributes."""

    def test_renders_transition_attribute(self):
        """dj-transition attribute is rendered."""
        client = LiveViewTestClient(TransitionView)
        client.mount(items=["Test Item"])
        html = client.html

        assert 'dj-transition="fade"' in html

    def test_renders_explicit_transition_classes(self):
        """Explicit transition class attributes are rendered."""
        client = LiveViewTestClient(TransitionView)
        client.mount()
        # Open modal to see explicit transition classes
        client.click("toggle_modal")
        html = client.html

        assert 'dj-transition-enter="scale-95 opacity-0"' in html
        assert 'dj-transition-enter-to="scale-100 opacity-100"' in html
        assert 'dj-transition-leave="scale-100 opacity-100"' in html
        assert 'dj-transition-leave-to="scale-95 opacity-0"' in html

    def test_add_item_with_transition(self):
        """Adding item renders element with transition attribute."""
        client = LiveViewTestClient(TransitionView)
        client.mount()

        client.click("add_item", text="New Entry")
        html = client.html

        assert "New Entry" in html
        assert 'dj-transition="fade"' in html
        assert client.state["items"] == ["New Entry"]

    def test_remove_item(self):
        """Removing item updates state (JS handles leave transition)."""
        client = LiveViewTestClient(TransitionView)
        client.mount(items=["A", "B", "C"])

        client.click("remove_item", index=1)

        assert client.state["items"] == ["A", "C"]

    def test_toggle_modal(self):
        """Modal toggle updates show_modal state."""
        client = LiveViewTestClient(TransitionView)
        client.mount()

        assert client.state["show_modal"] is False

        client.click("toggle_modal")
        assert client.state["show_modal"] is True
        assert "Modal Title" in client.html

        client.click("toggle_modal")
        assert client.state["show_modal"] is False
        assert "Modal Title" not in client.html


# ============================================================================
# Tests: dj-change blur behavior
# ============================================================================


class TestDjChangeBlur:
    """Tests for dj-change behavior on text inputs (fires on blur)."""

    def test_renders_change_attribute_on_text_input(self):
        """dj-change attribute is rendered on text inputs."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()
        html = client.html

        assert 'dj-change="on_name_change"' in html
        assert 'type="text"' in html
        assert 'id="name-input"' in html

    def test_renders_change_attribute_on_email_input(self):
        """dj-change attribute is rendered on email inputs."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()
        html = client.html

        assert 'dj-change="on_email_change"' in html
        assert 'type="email"' in html

    def test_renders_change_attribute_on_select(self):
        """dj-change attribute is rendered on select elements."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()
        html = client.html

        assert 'dj-change="on_select_change"' in html
        assert '<select' in html

    def test_name_change_handler(self):
        """Name change handler receives value and field."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()

        client.click("on_name_change", value="John Doe", field="name")

        assert client.state["name"] == "John Doe"
        assert ("name", "John Doe") in client.state["change_events"]

    def test_email_change_handler(self):
        """Email change handler receives value and field."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()

        client.click("on_email_change", value="john@example.com", field="email")

        assert client.state["email"] == "john@example.com"
        assert ("email", "john@example.com") in client.state["change_events"]

    def test_select_change_handler(self):
        """Select change handler receives value and field."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()

        client.click("on_select_change", value="a", field="select")

        assert ("select", "a") in client.state["change_events"]

    def test_multiple_changes_tracked(self):
        """Multiple change events are tracked in order."""
        client = LiveViewTestClient(BlurChangeView)
        client.mount()

        client.click("on_name_change", value="Alice", field="name")
        client.click("on_email_change", value="alice@test.com", field="email")
        client.click("on_select_change", value="b", field="select")
        client.click("on_name_change", value="Alice Smith", field="name")

        events = client.state["change_events"]
        assert len(events) == 4
        assert events[0] == ("name", "Alice")
        assert events[1] == ("email", "alice@test.com")
        assert events[2] == ("select", "b")
        assert events[3] == ("name", "Alice Smith")


# ============================================================================
# Tests: Combined Features
# ============================================================================


class TestCombinedFeatures:
    """Tests for views using multiple JS features together."""

    def test_confirm_with_target(self):
        """dj-confirm and dj-target can be used together."""
        # This is primarily a rendering test - the JS handles the interaction

        class ConfirmTargetView:
            template_name = "confirm_target.html"

            def __init__(self):
                self.count = 0
                self._pending_push_events = []
                self._html = ""

            def _initialize_temporary_assigns(self):
                pass

            def mount(self, request, **kwargs):
                self.count = 0
                self._update_html()

            def dangerous_action(self):
                self.count += 1
                self._update_html()

            def _update_html(self):
                self._html = f'''
                    <div id="wrapper">
                        <div id="target-area">
                            <p>Count: {self.count}</p>
                        </div>
                        <button dj-click="dangerous_action"
                                dj-confirm="Are you sure?"
                                dj-target="#target-area"
                                dj-loading.disable>
                            Danger!
                        </button>
                    </div>
                '''

            def get_context_data(self):
                return {"count": self.count}

            def _drain_push_events(self):
                events = self._pending_push_events
                self._pending_push_events = []
                return events

        client = LiveViewTestClient(ConfirmTargetView)
        client.mount()
        html = client.html

        # All attributes present on same element
        assert 'dj-confirm="Are you sure?"' in html
        assert 'dj-target="#target-area"' in html
        assert 'dj-loading.disable' in html

        # Handler works
        client.click("dangerous_action")
        assert client.state["count"] == 1

    def test_debounce_with_target(self):
        """dj-debounce and dj-target can be used together."""

        class SearchTargetView:
            template_name = "search_target.html"

            def __init__(self):
                self.results = []
                self._pending_push_events = []
                self._html = ""

            def _initialize_temporary_assigns(self):
                pass

            def mount(self, request, **kwargs):
                self.results = []
                self._update_html()

            def search(self, value: str = ""):
                self.results = [f"Result for {value}"] if value else []
                self._update_html()

            def _update_html(self):
                results_html = "".join(f"<li>{r}</li>" for r in self.results)
                self._html = f'''
                    <div id="search-wrapper">
                        <input type="text"
                               dj-input="search"
                               data-debounce="200"
                               dj-target="#results">
                        <ul id="results">{results_html}</ul>
                    </div>
                '''

            def get_context_data(self):
                return {"results": self.results}

            def _drain_push_events(self):
                events = self._pending_push_events
                self._pending_push_events = []
                return events

        client = LiveViewTestClient(SearchTargetView)
        client.mount()
        html = client.html

        assert 'data-debounce="200"' in html
        assert 'dj-target="#results"' in html

        client.click("search", value="test")
        assert "Result for test" in client.state["results"][0]


# ============================================================================
# Pytest fixtures
# ============================================================================


@pytest.fixture
def live_view_client():
    def _factory(view_class, user=None, **mount_params):
        client = LiveViewTestClient(view_class, user=user)
        client.mount(**mount_params)
        return client
    return _factory


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
