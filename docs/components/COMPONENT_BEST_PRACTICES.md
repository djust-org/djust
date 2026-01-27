# Component Best Practices

## Table of Contents

1. [The Golden Rule](#the-golden-rule)
2. [Decision Matrix](#decision-matrix)
3. [Common Patterns](#common-patterns)
4. [When to Use Simple Components](#when-to-use-simple-components)
5. [When to Use LiveComponents](#when-to-use-livecomponents)
6. [Component Communication](#component-communication)
7. [Performance Optimization](#performance-optimization)
8. [Anti-Patterns to Avoid](#anti-patterns-to-avoid)
9. [Testing Strategies](#testing-strategies)
10. [Real-World Examples](#real-world-examples)

## The Golden Rule

**Start simple. Add complexity only when needed.**

```python
# ✅ Good: Start with the simplest pattern
class MyView(LiveView):
    template_string = """
        <h1>{{ title }}</h1>
        <button dj-click="increment">{{ count }}</button>
    """

    def mount(self, request):
        self.title = "Counter"
        self.count = 0

    def increment(self):
        self.count += 1

# ❌ Don't: Over-engineer from the start
class MyView(LiveView):
    def mount(self, request):
        self.counter_component = CounterComponent()  # Unnecessary!
        self.title_component = TitleComponent()      # Unnecessary!
```

**Question to ask:** "Can I write this as plain HTML in my template?"
- **If yes** → Use template syntax, not a component
- **If no** → Is it because of complexity or reusability? → Use a component

## Decision Matrix

### Quick Reference

| Scenario | Use | Why |
|----------|-----|-----|
| Badge, icon, simple button | Template syntax | No state, minimal logic |
| Conditional content | Template `{% if %}` | Django templates handle this |
| Repeated items | Template `{% for %}` | Django templates handle this |
| Complex widget used once | LiveComponent | Encapsulation even if not reused |
| Widget used across views | Component or LiveComponent | Reusability |
| Widget with internal state | LiveComponent | State management needed |
| Widget handling events | LiveComponent | Event handling needed |
| Widget loading data | LiveComponent | Side effects needed |

### The Simple Test

Ask these questions in order:

1. **Can I write this in the template with variables?**
   - YES → Use template syntax, no component
   - NO → Continue to #2

2. **Does it need to manage state?**
   - NO → Use simple Component (stateless)
   - YES → Continue to #3

3. **Does it need to handle events or react to changes?**
   - YES → Use LiveComponent
   - NO → Use simple Component

## Common Patterns

### Pattern 1: Simple UI Elements

**Use case:** Badges, buttons, icons, labels

```python
# ✅ Best: Just use template syntax
template_string = """
    <span class="badge bg-primary">{{ count }}</span>
    <button class="btn btn-success" dj-click="save">Save</button>
"""

# ⚠️ OK: Simple component if reused everywhere
class BadgeComponent(Component):
    def __init__(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def render(self):
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'

# ❌ Overkill: LiveComponent for static UI
class BadgeComponent(LiveComponent):  # Don't do this!
    def mount(self, text, variant):
        self.text = text  # Unnecessary state management
```

### Pattern 2: Conditional Content

**Use case:** Show/hide alerts, modals, sections

```python
# ✅ Best: Template conditionals
template_string = """
    {% if show_alert %}
    <div class="alert alert-success">
        {{ message }}
    </div>
    {% endif %}
"""

def mount(self):
    self.show_alert = False
    self.message = ""

def save(self):
    # ... save logic
    self.show_alert = True
    self.message = "Saved successfully!"
```

### Pattern 3: Lists with Simple Items

**Use case:** User lists, todo items, navigation menus

```python
# ✅ Best: Template loops
template_string = """
    <ul>
        {% for item in items %}
        <li class="{% if item.done %}completed{% endif %}">
            {{ item.text }}
            <button dj-click="toggle_item" data-id="{{ item.id }}">✓</button>
        </li>
        {% endfor %}
    </ul>
"""

def toggle_item(self, id):
    item = next(i for i in self.items if i.id == id)
    item.done = not item.done
```

### Pattern 4: Complex Widgets

**Use case:** Tabs, pagination, modals with state, data tables

```python
# ✅ Best: LiveComponent for complex widgets
class TabsComponent(LiveComponent):
    """Self-contained tabs with internal state"""

    template_string = """
        <ul class="nav nav-tabs">
            {% for tab in tabs %}
            <li class="nav-item">
                <button dj-click="switch_tab" data-tab="{{ tab.id }}"
                        class="nav-link {% if tab.id == active %}active{% endif %}">
                    {{ tab.label }}
                </button>
            </li>
            {% endfor %}
        </ul>
    """

    def mount(self, tabs, active=None):
        self.tabs = tabs
        self.active = active or tabs[0].id

    def switch_tab(self, tab):
        self.active = tab
        self.send_parent("tab_changed", {"tab": tab})
```

## When to Use Simple Components

### Clear Indicators

Use a simple `Component` when:

✅ **Pure presentation** - Just converts props to HTML
✅ **No state** - Same props = same output, always
✅ **No events** - Doesn't handle clicks/changes
✅ **Highly reusable** - Used across many views
✅ **Framework-agnostic** - Bootstrap/Tailwind/plain versions

### Examples

```python
# Badge - perfect for simple component
class BadgeComponent(Component):
    def __init__(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def render(self):
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'

# Icon - perfect for simple component
class IconComponent(Component):
    def __init__(self, name, size=16):
        self.name = name
        self.size = size

    def render(self):
        return f'<svg width="{self.size}" height="{self.size}">...</svg>'

# Progress bar - borderline (consider template syntax)
class ProgressComponent(Component):
    def __init__(self, value, max=100, variant="primary"):
        self.value = value
        self.max = max
        self.variant = variant
        self.percentage = (value / max) * 100

    def render(self):
        return f'''
            <div class="progress">
                <div class="progress-bar bg-{self.variant}"
                     style="width: {self.percentage}%">
                    {self.percentage:.0f}%
                </div>
            </div>
        '''
```

### When NOT to Use

❌ **Avoid simple components for:**
- Content that changes frequently (use template syntax)
- One-off UI (just write HTML)
- Logic-heavy rendering (use LiveComponent)

## When to Use LiveComponents

### Clear Indicators

Use a `LiveComponent` when:

✅ **Internal state** - Manages its own data
✅ **Event handling** - Responds to user interactions
✅ **Data loading** - Fetches/computes data
✅ **Side effects** - Timers, animations, logging
✅ **Complex logic** - Non-trivial behavior
✅ **Lifecycle needs** - Setup/teardown required

### Examples

```python
# Tabs - perfect for LiveComponent
class TabsComponent(LiveComponent):
    """Manages active tab state"""
    def mount(self, tabs):
        self.tabs = tabs
        self.active = tabs[0].id  # State!

    def switch_tab(self, tab):
        self.active = tab  # Event handler!

# Pagination - perfect for LiveComponent
class PaginationComponent(LiveComponent):
    """Manages current page and loads data"""
    def mount(self, total, per_page=10):
        self.total = total
        self.per_page = per_page
        self.current_page = 1  # State!

    def next_page(self):
        self.current_page += 1  # Event handler!
        self.send_parent("page_changed", {"page": self.current_page})

# Modal - perfect for LiveComponent
class ModalComponent(LiveComponent):
    """Manages open/close state"""
    def mount(self, title, content):
        self.title = title
        self.content = content
        self.is_open = False  # State!

    def open(self):
        self.is_open = True  # Lifecycle!

    def close(self):
        self.is_open = False
        self.send_parent("modal_closed", {})
```

## Component Communication

### Pattern: Props Down, Events Up

This is **the recommended pattern** for component coordination.

```python
class DashboardView(LiveView):
    """Parent coordinates children via props and events"""

    template_string = """
        <FilterComponent id="filter" :active="current_filter" />
        <DataListComponent id="list" :items="filtered_items" />
    """

    def mount(self):
        self.items = load_items()
        self.current_filter = "all"

        self.filter_comp = FilterComponent(options=["all", "active", "done"])
        self.list = DataListComponent(items=self.items)

    @property
    def filtered_items(self):
        """Computed property - automatically reactive"""
        if self.current_filter == "all":
            return self.items
        return [i for i in self.items if i.status == self.current_filter]

    def handle_component_event(self, component_id, event, data):
        """Single coordination point"""
        if event == "filter_changed":
            self.current_filter = data["filter"]
            # Both components update automatically!
```

**Benefits:**
- ✅ Single source of truth (parent state)
- ✅ Clear data flow (easy to debug)
- ✅ Testable (components work independently)
- ✅ Parent has full visibility

### Pattern: Computed Properties

Use computed properties for derived state that multiple components need:

```python
class DashboardView(LiveView):
    def mount(self):
        self.users = User.objects.all()
        self.selected_id = None

    @property
    def selected_user(self):
        """Automatically updates when selected_id changes"""
        if self.selected_id:
            return User.objects.get(id=self.selected_id)
        return None

    @property
    def user_permissions(self):
        """Depends on selected_user"""
        if self.selected_user:
            return self.selected_user.get_permissions()
        return []
```

Components using `selected_user` or `user_permissions` automatically update when `selected_id` changes!

### Pattern: Convention-Based Handlers

Make event handling predictable with conventions:

```python
class DashboardView(LiveView):
    """Convention: handle_{event}__{component_id}"""

    def handle_component_event(self, component_id, event, data):
        # Auto-dispatch to convention-based handler
        handler = getattr(self, f"handle_{event}__{component_id}", None)
        if handler:
            handler(data)
        else:
            # Fallback to generic handler
            self.handle_generic_event(component_id, event, data)

    def handle_filter_changed__filter_panel(self, data):
        """Automatically called for filter_panel's filter_changed event"""
        self.current_filter = data["filter"]

    def handle_item_selected__item_list(self, data):
        """Automatically called for item_list's item_selected event"""
        self.selected_id = data["id"]
```

**Benefits:**
- Clear naming convention
- Easy to find handlers
- Self-documenting
- Scalable (add handlers as needed)

## Performance Optimization

### Use Simple Components for High-Volume Rendering

```python
# ✅ Good: Simple component for list items
class TodoItemComponent(Component):
    """Lightweight - no VDOM overhead"""
    def __init__(self, item):
        self.item = item

    def render(self):
        return f'<li>{self.item.text}</li>'

# Usage in template
template_string = """
    <ul>
        {% for item in items %}
        {{ item_component(item).render }}
        {% endfor %}
    </ul>
"""

# ❌ Don't: LiveComponent for every list item
# This creates 100 VDOM trees for 100 items!
```

### Minimize Component Count

```python
# ✅ Good: One component manages many items
class TodoListComponent(LiveComponent):
    """Single component, efficient updates"""
    template_string = """
        <ul>
            {% for item in items %}
            <li>{{ item.text }}</li>
            {% endfor %}
        </ul>
    """

# ❌ Don't: Separate component per item
# Unless items are truly independent complex widgets
```

### Use Template Syntax for Frequent Updates

```python
# ✅ Good: Template variable for frequently changing data
template_string = """
    <div class="counter">{{ count }}</div>
"""

def increment(self):
    self.count += 1  # Efficient VDOM patch

# ❌ Don't: Component for simple counter
class CounterComponent(LiveComponent):  # Overkill!
    def mount(self, count):
        self.count = count
```

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: Over-Componentization

```python
# ❌ Bad: Components for everything
class TitleComponent(Component):
    def render(self):
        return f'<h1>{self.text}</h1>'

class ParagraphComponent(Component):
    def render(self):
        return f'<p>{self.text}</p>'

# ✅ Good: Just use HTML!
template_string = """
    <h1>{{ title }}</h1>
    <p>{{ description }}</p>
"""
```

### ❌ Anti-Pattern 2: Stateful Simple Components

```python
# ❌ Bad: Trying to add state to simple component
class BadgeComponent(Component):
    def __init__(self, initial_count):
        self.count = initial_count  # Won't persist!

    def increment(self):  # Won't work!
        self.count += 1

# ✅ Good: Use LiveComponent for state
class BadgeComponent(LiveComponent):
    def mount(self, initial_count):
        self.count = initial_count

    def increment(self):
        self.count += 1
```

### ❌ Anti-Pattern 3: Direct Component-to-Component Communication

```python
# ❌ Bad: Components referencing each other
class ComponentA(LiveComponent):
    def do_something(self):
        self.parent.component_b.update_data()  # Tight coupling!

# ✅ Good: Via parent
class ComponentA(LiveComponent):
    def do_something(self):
        self.send_parent("something_happened", {})

class ParentView(LiveView):
    def handle_component_event(self, component_id, event, data):
        if event == "something_happened":
            self.component_b.update_data()  # Parent coordinates
```

### ❌ Anti-Pattern 4: Bloated Parent

```python
# ❌ Bad: Parent doing everything
class DashboardView(LiveView):
    def mount(self):
        self.users = User.objects.all()
        self.filtered_users = []
        self.sort_order = "name"
        self.current_page = 1
        self.items_per_page = 10
        # ... 50 more state variables

    def filter_users(self, criteria):
        # ... 100 lines of filtering logic

    def sort_users(self, field):
        # ... 100 lines of sorting logic

# ✅ Good: Extract to components
class UserTableComponent(LiveComponent):
    """Encapsulates filtering, sorting, pagination"""
    def mount(self, users):
        self.users = users
        self.filter = None
        self.sort = "name"
        self.page = 1
```

## Testing Strategies

### Testing Simple Components

```python
def test_badge_component():
    """Simple components are pure functions"""
    # Arrange
    badge = BadgeComponent(text="New", variant="primary")

    # Act
    html = badge.render()

    # Assert
    assert 'badge' in html
    assert 'bg-primary' in html
    assert 'New' in html

# Easy to test - no mocking needed!
```

### Testing LiveComponents

```python
def test_tabs_component():
    """Test component in isolation"""
    # Arrange
    tabs = [
        {"id": "home", "label": "Home"},
        {"id": "about", "label": "About"},
    ]
    component = TabsComponent(tabs=tabs)

    # Act - trigger event
    component.switch_tab(tab="about")

    # Assert - check state changed
    assert component.active == "about"

def test_tabs_notifies_parent():
    """Test parent communication"""
    tabs = [{"id": "home", "label": "Home"}]
    component = TabsComponent(tabs=tabs)

    # Mock parent communication
    events = []
    component.send_parent = lambda event, data: events.append((event, data))

    # Act
    component.switch_tab(tab="home")

    # Assert
    assert events == [("tab_changed", {"tab": "home"})]
```

### Testing Parent Coordination

```python
def test_dashboard_coordinates_components(client):
    """Test parent coordinates children"""
    view = DashboardView()
    view.mount(request)

    # Simulate component event
    view.handle_component_event(
        component_id="filter",
        event="filter_changed",
        data={"filter": "active"}
    )

    # Assert parent state updated
    assert view.current_filter == "active"

    # Assert components would receive new props
    context = view.get_context_data()
    assert context["current_filter"] == "active"
```

## Real-World Examples

### Example 1: Simple Dashboard

```python
class DashboardView(LiveView):
    """Real-world dashboard - mix of approaches"""

    template_string = """
        <!-- Simple template syntax for stats -->
        <div class="stats">
            <div class="stat-card">
                <h3>{{ user_count }}</h3>
                <p>Total Users</p>
            </div>
            <div class="stat-card">
                <h3>{{ active_count }}</h3>
                <p>Active</p>
            </div>
        </div>

        <!-- LiveComponent for complex widget -->
        <ChartComponent id="chart" :data="chart_data" />

        <!-- Simple loop for recent activity -->
        <ul class="activity">
            {% for item in recent_activity %}
            <li>{{ item.user }} {{ item.action }} {{ item.target }}</li>
            {% endfor %}
        </ul>
    """

    def mount(self, request):
        self.user_count = User.objects.count()
        self.active_count = User.objects.filter(is_active=True).count()
        self.recent_activity = Activity.objects.all()[:10]
        self.chart = ChartComponent(data=self.get_chart_data())

    def get_chart_data(self):
        return {
            'labels': ['Mon', 'Tue', 'Wed'],
            'values': [10, 20, 15]
        }
```

**Rationale:**
- Stats: Simple template variables (changes frequently, no complexity)
- Chart: LiveComponent (complex rendering, interactions)
- Activity: Template loop (simple list, no per-item state)

### Example 2: User Management

```python
class UserManagementView(LiveView):
    """Coordinating multiple components"""

    template_string = """
        <div class="row">
            <div class="col-md-4">
                <FilterPanelComponent id="filter" :options="filter_options" />
                <UserListComponent id="list" :users="filtered_users" :selected="selected_id" />
            </div>
            <div class="col-md-8">
                <UserDetailComponent id="detail" :user="selected_user" />
            </div>
        </div>
    """

    def mount(self, request):
        self.users = User.objects.all()
        self.filters = {}
        self.selected_id = None

        self.filter_panel = FilterPanelComponent(options=self.get_filter_options())
        self.user_list = UserListComponent(users=self.users)
        self.user_detail = UserDetailComponent()

    @property
    def filtered_users(self):
        """Computed - updates list when filters change"""
        users = self.users
        if self.filters.get('role'):
            users = users.filter(role=self.filters['role'])
        return users

    @property
    def selected_user(self):
        """Computed - updates detail when selection changes"""
        if self.selected_id:
            return User.objects.get(id=self.selected_id)
        return None

    def handle_component_event(self, component_id, event, data):
        """Clean coordination point"""
        if event == "filter_changed":
            self.filters = data["filters"]
        elif event == "user_selected":
            self.selected_id = data["id"]
```

## Summary

**Keep it simple:**

1. Start with template syntax
2. Add simple components for reusable UI
3. Use LiveComponents for complex, stateful widgets
4. Let parent coordinate via props/events
5. Use computed properties for derived state

**Remember:**
- Most things can be template syntax
- Simple components for pure presentation
- LiveComponents for complexity
- Parent coordinates everything

**Next Steps:**
- [Migration Guide](COMPONENT_MIGRATION_GUIDE.md) - Migrating existing code
- [Examples](COMPONENT_EXAMPLES.md) - Complete working examples
- [API Reference](API_REFERENCE_COMPONENTS.md) - Detailed API docs
