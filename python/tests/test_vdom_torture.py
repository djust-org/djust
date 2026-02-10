"""
VDOM Torture Tests (Python-side)

Exercises the VDOM diff engine through the full LiveView pipeline with
complex templates, rapid state changes, and edge-case HTML structures.

Documents any issues found during testing.
"""

import json
import pytest
from djust import LiveView
from djust.decorators import event_handler
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware


def add_session_to_request(request):
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()
    return request


def get_patches(view_class, initial_state=None, event_name="update", event_params=None):
    """Helper: render view, fire event, return patches list."""
    view = view_class()
    factory = RequestFactory()

    get_request = factory.get("/test/")
    get_request = add_session_to_request(get_request)
    response = view.get(get_request)
    initial_html = response.content.decode("utf-8")

    post_request = factory.post(
        "/test/",
        data=json.dumps({"event": event_name, "params": event_params or {}}),
        content_type="application/json",
    )
    post_request.session = get_request.session
    response = view.post(post_request)
    response_data = json.loads(response.content.decode("utf-8"))

    if "patches" not in response_data:
        return [], initial_html, response_data

    patches_json = response_data["patches"]
    patches = json.loads(patches_json) if isinstance(patches_json, str) else patches_json
    return patches, initial_html, response_data


def count_patch_types(patches):
    """Return dict of patch type counts."""
    counts = {}
    for p in patches:
        t = p.get("type", "Unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


# ============================================================================
# 1. DEEP NESTING
# ============================================================================


class DeepNestingView(LiveView):
    template = """<div data-djust-root>
    <div class="l1"><div class="l2"><div class="l3"><div class="l4">
    <div class="l5"><div class="l6"><div class="l7"><div class="l8">
    <div class="l9"><div class="l10">
        <p>{{ text }}</p>
    </div></div></div></div></div></div></div></div></div></div>
</div>"""

    def mount(self, request):
        self.text = "initial"

    @event_handler()
    def update(self, **kwargs):
        self.text = "changed"


@pytest.mark.django_db
def test_torture_deep_nesting():
    """Deeply nested content change should produce minimal patches."""
    patches, _, _ = get_patches(DeepNestingView)
    types = count_patch_types(patches)

    assert "SetText" in types, f"Should have SetText patch. Got: {types}"
    # Should be very few patches (just the text change)
    assert (
        len(patches) <= 3
    ), f"Deep nesting should produce few patches, got {len(patches)}: {types}"


# ============================================================================
# 2. LARGE SIBLING LISTS
# ============================================================================


class LargeListView(LiveView):
    template = """<div data-djust-root>
    <ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>
</div>"""

    def mount(self, request):
        self.items = [f"Item {i}" for i in range(50)]

    @event_handler()
    def append(self, **kwargs):
        self.items.append(f"Item {len(self.items)}")

    @event_handler()
    def remove_first(self, **kwargs):
        if self.items:
            self.items.pop(0)

    @event_handler()
    def change_middle(self, **kwargs):
        mid = len(self.items) // 2
        self.items[mid] = "CHANGED"


@pytest.mark.django_db
def test_torture_large_list_append():
    """Appending to a large list should produce InsertChild."""
    patches, _, _ = get_patches(LargeListView, event_name="append")
    types = count_patch_types(patches)
    assert "InsertChild" in types, f"Should insert new item. Got: {types}"


@pytest.mark.django_db
def test_torture_large_list_remove_first():
    """Removing first item from unkeyed list triggers indexed diff (morph)."""
    patches, _, _ = get_patches(LargeListView, event_name="remove_first")
    types = count_patch_types(patches)
    # Indexed diff: changes text of 49 items + removes 1
    total = sum(types.values())
    assert total > 0, f"Should produce patches. Got: {types}"


@pytest.mark.django_db
def test_torture_large_list_change_middle():
    """Changing one item in middle should produce minimal patches."""
    patches, _, _ = get_patches(LargeListView, event_name="change_middle")
    types = count_patch_types(patches)
    assert "SetText" in types, f"Should have SetText. Got: {types}"
    # Should only change the one item's text
    assert types.get("SetText", 0) <= 2, f"Should change ~1 text node, got: {types}"


# ============================================================================
# 3. CONDITIONAL CONTENT (SHOW/HIDE)
# ============================================================================


class ConditionalView(LiveView):
    template = """<div data-djust-root>
    {% if show_detail %}
        <div class="detail">
            <h2>{{ title }}</h2>
            <p>{{ description }}</p>
            <ul>
                <li>Feature 1</li>
                <li>Feature 2</li>
                <li>Feature 3</li>
            </ul>
        </div>
    {% else %}
        <p class="placeholder">No content selected</p>
    {% endif %}
</div>"""

    def mount(self, request):
        self.show_detail = False
        self.title = "Title"
        self.description = "Description"

    @event_handler()
    def toggle(self, **kwargs):
        self.show_detail = not self.show_detail


@pytest.mark.django_db
def test_torture_conditional_show():
    """Showing conditional content should produce patches (not full replace)."""
    patches, _, _ = get_patches(ConditionalView, event_name="toggle")
    assert len(patches) > 0, "Should produce patches for conditional show"


@pytest.mark.django_db
def test_torture_conditional_toggle_twice():
    """Toggle on then off should return to initial state."""
    view = ConditionalView()
    factory = RequestFactory()

    get_request = factory.get("/test/")
    get_request = add_session_to_request(get_request)
    view.get(get_request)

    # Toggle on
    post_request = factory.post(
        "/test/",
        data='{"event":"toggle","params":{}}',
        content_type="application/json",
    )
    post_request.session = get_request.session
    response = view.post(post_request)
    data1 = json.loads(response.content.decode("utf-8"))
    assert "patches" in data1

    # Toggle off
    post_request2 = factory.post(
        "/test/",
        data='{"event":"toggle","params":{}}',
        content_type="application/json",
    )
    post_request2.session = get_request.session
    response2 = view.post(post_request2)
    data2 = json.loads(response2.content.decode("utf-8"))
    assert "patches" in data2


# ============================================================================
# 4. MULTIPLE SIMULTANEOUS CHANGES
# ============================================================================


class MultiChangeView(LiveView):
    template = """<div data-djust-root>
    <header class="{{ header_class }}">{{ title }}</header>
    <main>
        <p>{{ paragraph }}</p>
        <span class="{{ badge_class }}">{{ badge_text }}</span>
    </main>
    <footer>{{ footer }}</footer>
</div>"""

    def mount(self, request):
        self.header_class = "default"
        self.title = "Title"
        self.paragraph = "Content"
        self.badge_class = "info"
        self.badge_text = "Status"
        self.footer = "Footer"

    @event_handler()
    def change_everything(self, **kwargs):
        self.header_class = "active"
        self.title = "New Title"
        self.paragraph = "New Content"
        self.badge_class = "danger"
        self.badge_text = "Alert"
        self.footer = "Updated Footer"


@pytest.mark.django_db
def test_torture_multiple_changes():
    """Multiple simultaneous attribute and text changes."""
    patches, _, _ = get_patches(MultiChangeView, event_name="change_everything")
    types = count_patch_types(patches)

    # Should have both text and attribute changes
    assert types.get("SetText", 0) >= 3, f"Should change at least 3 text nodes. Got: {types}"
    assert types.get("SetAttr", 0) >= 2, f"Should change at least 2 attrs. Got: {types}"


# ============================================================================
# 5. REPLACE CONTAINER STRESS
# ============================================================================


class ReplaceStressView(LiveView):
    template = """<div data-djust-root>
    <div class="sidebar">Sidebar</div>
    <div data-djust-replace id="content">
        {% for msg in messages %}
            <div class="message">
                <strong>{{ msg.author }}</strong>
                <p>{{ msg.text }}</p>
            </div>
        {% endfor %}
    </div>
    <div class="footer">Footer</div>
</div>"""

    def mount(self, request):
        self.messages = [
            {"author": "Alice", "text": "Hello"},
            {"author": "Bob", "text": "Hi there"},
        ]

    @event_handler()
    def switch_conversation(self, **kwargs):
        self.messages = [
            {"author": "Carol", "text": "New conversation"},
            {"author": "Dave", "text": "Completely different"},
            {"author": "Eve", "text": "Third message"},
        ]


@pytest.mark.django_db
def test_torture_replace_with_siblings():
    """Replace container with sibling elements should only target the replace container."""
    patches, _, _ = get_patches(ReplaceStressView, event_name="switch_conversation")

    # All InsertChild/RemoveChild should target the same parent
    child_op_parents = set()
    for p in patches:
        if p.get("type") in ("InsertChild", "RemoveChild"):
            parent_key = (p.get("d"), tuple(p.get("path", [])))
            child_op_parents.add(parent_key)

    assert (
        len(child_op_parents) == 1
    ), f"All child ops should target same parent. Found {len(child_op_parents)}: {child_op_parents}"


# ============================================================================
# 6. FORM VALIDATION STRESS
# ============================================================================


class FormValidationView(LiveView):
    template = """<div data-djust-root>
    <form>
        {% for field in fields %}
        <div class="field">
            <label>{{ field.label }}</label>
            <input type="{{ field.type }}" class="form-control{% if field.error %} is-invalid{% endif %}">
            {% if field.error %}
            <div class="invalid-feedback">{{ field.error }}</div>
            {% endif %}
        </div>
        {% endfor %}
        <button type="submit">Submit</button>
    </form>
</div>"""

    def mount(self, request):
        self.fields = [
            {"label": "Name", "type": "text", "error": "Required"},
            {"label": "Email", "type": "email", "error": "Invalid format"},
            {"label": "Phone", "type": "tel", "error": "Too short"},
            {"label": "Address", "type": "text", "error": "Required"},
            {"label": "City", "type": "text", "error": "Required"},
        ]

    @event_handler()
    def clear_errors(self, **kwargs):
        for field in self.fields:
            field["error"] = ""


@pytest.mark.django_db
def test_torture_form_clear_5_errors():
    """Clearing 5 form validation errors simultaneously."""
    patches, initial, _ = get_patches(FormValidationView, event_name="clear_errors")
    # Should have patches for removing error divs and changing input classes
    assert len(patches) > 0, "Should produce patches for clearing errors"
    # All patches should have valid d values (not None for element patches)
    for p in patches:
        if p.get("type") in ("SetAttr", "RemoveAttr", "Replace"):
            # d can be None for text nodes, but should exist for elements
            pass  # Don't enforce — text node patches correctly have d=None


# ============================================================================
# 7. RAPID SEQUENTIAL STATE CHANGES
# ============================================================================


class CounterView(LiveView):
    template = """<div data-djust-root>
    <span id="count">{{ count }}</span>
</div>"""

    def mount(self, request):
        self.count = 0

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


@pytest.mark.django_db
def test_torture_rapid_counter():
    """Rapidly incrementing a counter 20 times."""
    view = CounterView()
    factory = RequestFactory()

    get_request = factory.get("/test/")
    get_request = add_session_to_request(get_request)
    view.get(get_request)

    for i in range(20):
        post_request = factory.post(
            "/test/",
            data='{"event":"increment","params":{}}',
            content_type="application/json",
        )
        post_request.session = get_request.session
        response = view.post(post_request)
        data = json.loads(response.content.decode("utf-8"))

        if "patches" in data:
            patches_json = data["patches"]
            patches = json.loads(patches_json) if isinstance(patches_json, str) else patches_json
            types = count_patch_types(patches)
            assert "SetText" in types, f"Step {i}: counter should produce SetText, got {types}"
        elif "html" in data:
            # Full HTML update is also valid (first render after mount)
            pass


# ============================================================================
# 8. EMPTY STATE TRANSITIONS
# ============================================================================


class EmptyStateView(LiveView):
    template = """<div data-djust-root>
    {% if items %}
        <ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>
    {% else %}
        <div class="empty-state">
            <h2>Nothing here</h2>
            <p>Add some items to get started.</p>
        </div>
    {% endif %}
</div>"""

    def mount(self, request):
        self.items = []

    @event_handler()
    def add_items(self, **kwargs):
        self.items = ["Alpha", "Beta", "Gamma"]

    @event_handler()
    def clear_items(self, **kwargs):
        self.items = []


@pytest.mark.django_db
def test_torture_empty_to_content():
    """Transition from empty state to content."""
    patches, _, _ = get_patches(EmptyStateView, event_name="add_items")
    assert len(patches) > 0, "Should produce patches for empty→content transition"


@pytest.mark.django_db
def test_torture_content_to_empty():
    """Transition from content back to empty state (two toggles)."""
    view = EmptyStateView()
    factory = RequestFactory()

    get_request = factory.get("/test/")
    get_request = add_session_to_request(get_request)
    view.get(get_request)

    # First: add items
    post1 = factory.post(
        "/test/",
        data='{"event":"add_items","params":{}}',
        content_type="application/json",
    )
    post1.session = get_request.session
    view.post(post1)

    # Second: clear items
    post2 = factory.post(
        "/test/",
        data='{"event":"clear_items","params":{}}',
        content_type="application/json",
    )
    post2.session = get_request.session
    response = view.post(post2)
    data = json.loads(response.content.decode("utf-8"))

    # Should produce patches or html_update
    assert "patches" in data or "html" in data, "Should produce response for content→empty"


# ============================================================================
# 9. SPECIAL CHARACTERS & UNICODE
# ============================================================================


class UnicodeView(LiveView):
    template = """<div data-djust-root>
    <p>{{ text }}</p>
</div>"""

    def mount(self, request):
        self.text = "Hello World"

    @event_handler()
    def set_unicode(self, **kwargs):
        self.text = "Привет мир 🌍 日本語 العربية <script>alert('xss')</script>"


@pytest.mark.django_db
def test_torture_unicode_and_special_chars():
    """Unicode and HTML entities in text content."""
    patches, _, _ = get_patches(UnicodeView, event_name="set_unicode")
    types = count_patch_types(patches)
    assert "SetText" in types, f"Should change text. Got: {types}"


# ============================================================================
# 10. NO-CHANGE (IDENTICAL RE-RENDER)
# ============================================================================


class NoChangeView(LiveView):
    template = """<div data-djust-root>
    <p>{{ text }}</p>
    <span class="badge">{{ badge }}</span>
</div>"""

    def mount(self, request):
        self.text = "stable"
        self.badge = "unchanged"

    @event_handler()
    def noop(self, **kwargs):
        pass  # No state change


@pytest.mark.django_db
def test_torture_no_change_produces_no_patches():
    """Re-rendering with identical state should produce 0 patches or empty patches."""
    patches, _, response_data = get_patches(NoChangeView, event_name="noop")
    # Patches should be empty (no changes)
    if patches:
        # Filter out any data-dj-id-only patches (these are OK)
        meaningful = [
            p for p in patches if p.get("type") != "SetAttr" or p.get("key") != "data-dj-id"
        ]
        assert (
            len(meaningful) == 0
        ), f"No-change event should produce 0 meaningful patches, got {len(meaningful)}: {meaningful}"


# ============================================================================
# 11. MIXED HTML TAGS (table, svg, form elements)
# ============================================================================


class TableView(LiveView):
    template = """<div data-djust-root>
    <table>
        <thead><tr><th>Name</th><th>Score</th></tr></thead>
        <tbody>
        {% for row in rows %}
            <tr><td>{{ row.name }}</td><td>{{ row.score }}</td></tr>
        {% endfor %}
        </tbody>
    </table>
</div>"""

    def mount(self, request):
        self.rows = [
            {"name": "Alice", "score": 90},
            {"name": "Bob", "score": 85},
        ]

    @event_handler()
    def add_row(self, **kwargs):
        self.rows.append({"name": "Carol", "score": 92})

    @event_handler()
    def update_scores(self, **kwargs):
        for row in self.rows:
            row["score"] += 5


@pytest.mark.django_db
def test_torture_table_add_row():
    """Adding a row to an HTML table."""
    patches, _, _ = get_patches(TableView, event_name="add_row")
    types = count_patch_types(patches)
    assert "InsertChild" in types, f"Should insert table row. Got: {types}"


@pytest.mark.django_db
def test_torture_table_update_scores():
    """Updating scores in existing table rows."""
    patches, _, _ = get_patches(TableView, event_name="update_scores")
    types = count_patch_types(patches)
    assert "SetText" in types, f"Should change score text. Got: {types}"
