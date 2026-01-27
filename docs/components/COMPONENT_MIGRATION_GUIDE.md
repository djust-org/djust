# Component Migration Guide

## Table of Contents

1. [Overview](#overview)
2. [Migration Strategy](#migration-strategy)
3. [Pre-Migration Checklist](#pre-migration-checklist)
4. [Identifying Component Types](#identifying-component-types)
5. [Migrating Simple Components](#migrating-simple-components)
6. [Migrating to LiveComponents](#migrating-to-livecomponents)
7. [Updating Parent Views](#updating-parent-views)
8. [Testing After Migration](#testing-after-migration)
9. [Common Migration Scenarios](#common-migration-scenarios)
10. [Troubleshooting](#troubleshooting)

## Overview

This guide helps you migrate existing djust components to the new two-tier architecture (Component vs LiveComponent).

**Goals:**
- ✅ Maintain backward compatibility where possible
- ✅ Improve performance through proper component types
- ✅ Enable efficient VDOM patching
- ✅ Simplify component code

**Timeline:** Most migrations can be done incrementally - no need to migrate everything at once!

## Migration Strategy

### Phase 1: Assessment (1-2 days)

1. **Audit existing components**
   - Count total components
   - Categorize by complexity
   - Identify dependencies

2. **Prioritize migration**
   - Start with high-value components
   - Or start with simple components (quick wins)

### Phase 2: Incremental Migration (1-2 weeks)

1. **Migrate simple components first**
   - Badges, buttons, icons
   - Quick wins, low risk

2. **Migrate complex components**
   - Tabs, pagination, modals
   - Higher value, more testing needed

3. **Update parent views**
   - Add event handlers
   - Remove manual coordination

### Phase 3: Optimization (ongoing)

1. **Remove deprecated patterns**
2. **Add prop reactivity**
3. **Performance tuning**

## Pre-Migration Checklist

Before migrating, ensure you have:

- [ ] Read [LiveComponent Architecture](LIVECOMPONENT_ARCHITECTURE.md)
- [ ] Read [API Reference](API_REFERENCE_COMPONENTS.md)
- [ ] Read [Best Practices](COMPONENT_BEST_PRACTICES.md)
- [ ] Working test suite for existing components
- [ ] Git branch for migration work
- [ ] List of all components to migrate

## Identifying Component Types

### Quick Assessment Tool

Run through each component and check:

```python
# For each component, ask:

1. Does it have instance variables that change? (self.foo = ...)
   YES → LiveComponent
   NO → Continue

2. Does it handle events? (dj-click, dj-change handlers)
   YES → LiveComponent
   NO → Continue

3. Does it load data or have side effects?
   YES → LiveComponent
   NO → Continue

4. Is it just rendering props to HTML?
   YES → Simple Component (or template syntax!)
   NO → Re-evaluate above

5. Is it so simple you could write it in the template?
   YES → No component needed!
   NO → Simple Component
```

### Examples

```python
# Current component - what type should it be?

class BadgeComponent(LiveComponent):  # Current
    def mount(self, text, variant):
        self.text = text
        self.variant = variant

    def get_context_data(self):
        return {'text': self.text, 'variant': self.variant}

# Assessment:
# - No changing state ✗
# - No event handlers ✗
# - No data loading ✗
# - Pure presentation ✓
#
# VERDICT: Simple Component (or template syntax)


class TabsComponent(LiveComponent):  # Current
    def mount(self, tabs, active):
        self.tabs = tabs
        self.active = active

    def switch_tab(self, tab):
        self.active = tab  # Changes state!
        self.send_parent("tab_changed", {"tab": tab})

    def get_context_data(self):
        return {'tabs': self.tabs, 'active': self.active}

# Assessment:
# - Changing state (self.active) ✓
# - Event handler (switch_tab) ✓
# - Sends events to parent ✓
#
# VERDICT: Keep as LiveComponent
```

## Migrating Simple Components

### Pattern 1: From LiveComponent to Component

**Before (over-engineered):**
```python
from djust import LiveComponent

class BadgeComponent(LiveComponent):
    template_name = 'components/badge.html'

    def mount(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def get_context_data(self):
        return {
            'text': self.text,
            'variant': self.variant
        }
```

**After (simplified):**
```python
from djust.components import Component
from django.utils.safestring import mark_safe

class BadgeComponent(Component):
    """Simple stateless badge - no lifecycle needed"""

    def __init__(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def render(self) -> str:
        return mark_safe(f'<span class="badge bg-{self.variant}">{self.text}</span>')
```

**Changes:**
1. Inherit from `Component` instead of `LiveComponent`
2. Remove `template_name`
3. Change `mount()` to `__init__()`
4. Change `get_context_data()` to `render()`
5. Return HTML string directly

**Benefits:**
- ⬇️ 50% less code
- ⚡ Faster (no VDOM overhead)
- 🔧 Easier to test
- 📝 More Pythonic

### Pattern 2: From Component to Template Syntax

**Before (component):**
```python
class BadgeComponent(Component):
    def __init__(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def render(self):
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'

# Usage in view
def get_context_data(self):
    return {
        'badge': BadgeComponent(text=str(self.count), variant='primary')
    }

# Usage in template
{{ badge.render }}
```

**After (template syntax):**
```python
# Remove component entirely!

# Usage in view
def get_context_data(self):
    return {
        'count': self.count,
        'badge_variant': 'primary'
    }

# Usage in template
<span class="badge bg-{{ badge_variant }}">{{ count }}</span>
```

**When to do this:**
- Component used in only 1-2 places
- Very simple HTML
- No framework-specific rendering

## Migrating to LiveComponents

### Pattern 1: Adding State Management

**Before (stateless, recreated every render):**
```python
class TabsComponent(Component):
    def __init__(self, tabs, active):
        self.tabs = tabs
        self.active = active

    def render(self):
        # Returns HTML but loses state on parent re-render
        return '...'

# Parent recreates component every time
def get_context_data(self):
    return {
        'tabs': TabsComponent(tabs=self.tabs, active=self.active_tab)
    }
```

**After (stateful, persistent):**
```python
class TabsComponent(LiveComponent):
    """Stateful tabs with persistent state"""

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
        """Called once - initialize state"""
        self.tabs = tabs
        self.active = active or tabs[0].id

    def switch_tab(self, tab):
        """Event handler - updates state"""
        self.active = tab
        self.send_parent("tab_changed", {"tab": tab})

    def get_context_data(self):
        return {
            'tabs': self.tabs,
            'active': self.active
        }

# Parent creates component once in mount()
def mount(self, request):
    self.tabs_component = TabsComponent(
        tabs=[{"id": "home", "label": "Home"}]
    )
```

**Key Changes:**
1. Add `template_string` (or keep `template_name`)
2. Change `__init__` to `mount()`
3. Add event handlers (`switch_tab`)
4. Add `send_parent()` calls
5. Create component once in `mount()`, not in `get_context_data()`

### Pattern 2: Adding Reactivity

**Before (manual prop updates):**
```python
class UserDetailComponent(LiveComponent):
    def mount(self, user):
        self.user = user
        self.stats = None
        if user:
            self._load_stats()

    def set_user(self, user):
        """Parent must manually call this"""
        self.user = user
        self._load_stats()

    def _load_stats(self):
        self.stats = get_user_stats(self.user)

# Parent manually updates
def handle_user_selected(self, user_id):
    user = User.objects.get(id=user_id)
    self.user_detail.set_user(user)  # Manual call
```

**After (automatic reactivity):**
```python
class UserDetailComponent(LiveComponent):
    def mount(self, user=None):
        self.user = user
        self.stats = None
        if user:
            self._load_stats()

    def update(self, **props):
        """Framework calls this when props change"""
        if 'user' in props and props['user'] != self.user:
            self.user = props['user']
            if self.user:
                self._load_stats()
            else:
                self.stats = None

    def _load_stats(self):
        self.stats = get_user_stats(self.user)

# Parent just updates state - component reacts automatically!
def handle_user_selected(self, user_id):
    self.selected_user = User.objects.get(id=user_id)
    # Framework calls user_detail.update(user=self.selected_user)
```

**Key Changes:**
1. Add `update(**props)` method
2. Check for prop changes
3. React to changes (load data, update state)
4. Remove manual `set_*` methods
5. Parent just updates state

## Updating Parent Views

### Pattern 1: Moving from Manual to Event-Based Coordination

**Before (manual coordination):**
```python
class DashboardView(LiveView):
    def mount(self, request):
        self.selected_user = None
        self.user_list = UserListComponent(users=User.objects.all())
        self.user_detail = UserDetailComponent()

    def select_user(self, user_id):
        """Called directly by template button"""
        self.selected_user = User.objects.get(id=user_id)

        # Manual coordination
        self.user_list.set_active(user_id)
        self.user_detail.set_user(self.selected_user)
        self.activity_log.add_entry(f"Viewed user {user_id}")
```

**After (event-based):**
```python
class DashboardView(LiveView):
    def mount(self, request):
        self.selected_user_id = None
        self.user_list = UserListComponent(users=User.objects.all())
        self.user_detail = UserDetailComponent()
        self.activity_log = ActivityLogComponent()

    def handle_component_event(self, component_id, event, data):
        """Single coordination point"""
        if event == "user_selected":
            self.selected_user_id = data["user_id"]
            # Components automatically update via props!

# UserListComponent sends event instead of calling parent
class UserListComponent(LiveComponent):
    def select_user(self, user_id):
        self.selected_id = user_id
        self.send_parent("user_selected", {"user_id": user_id})
```

### Pattern 2: Using Computed Properties

**Before (manual state synchronization):**
```python
class DashboardView(LiveView):
    def mount(self, request):
        self.selected_user_id = None
        self.selected_user = None
        self.user_permissions = []

    def handle_user_selected(self, user_id):
        # Manual sync of related state
        self.selected_user_id = user_id
        self.selected_user = User.objects.get(id=user_id)
        self.user_permissions = self.selected_user.get_permissions()
```

**After (computed properties):**
```python
class DashboardView(LiveView):
    def mount(self, request):
        self.selected_user_id = None

    @property
    def selected_user(self):
        """Automatically computed when selected_user_id changes"""
        if self.selected_user_id:
            return User.objects.get(id=self.selected_user_id)
        return None

    @property
    def user_permissions(self):
        """Automatically computed from selected_user"""
        if self.selected_user:
            return self.selected_user.get_permissions()
        return []

    def handle_component_event(self, component_id, event, data):
        if event == "user_selected":
            self.selected_user_id = data["user_id"]
            # selected_user and user_permissions automatically update!
```

## Testing After Migration

### Test Simple Components

```python
def test_badge_component():
    """Simple components are pure functions - easy to test"""
    badge = BadgeComponent(text="New", variant="primary")
    html = badge.render()

    assert 'badge' in html
    assert 'bg-primary' in html
    assert 'New' in html

# No mocking, no setup, just pure function testing!
```

### Test LiveComponents

```python
def test_tabs_component_state():
    """Test component manages state correctly"""
    tabs = [{"id": "home", "label": "Home"}, {"id": "about", "label": "About"}]
    component = TabsComponent(tabs=tabs, active="home")

    # Test initial state
    assert component.active == "home"

    # Test state change
    component.switch_tab(tab="about")
    assert component.active == "about"

def test_tabs_component_events():
    """Test component sends events to parent"""
    tabs = [{"id": "home", "label": "Home"}]
    component = TabsComponent(tabs=tabs)

    # Mock parent communication
    events = []
    component.send_parent = lambda event, data: events.append((event, data))

    # Trigger event
    component.switch_tab(tab="home")

    # Verify event sent
    assert events == [("tab_changed", {"tab": "home"})]
```

### Test Parent Coordination

```python
def test_parent_coordinates_components():
    """Test parent handles component events"""
    view = DashboardView()
    request = RequestFactory().get('/')
    view.mount(request)

    # Simulate component event
    view.handle_component_event(
        component_id="user_list",
        event="user_selected",
        data={"user_id": 123}
    )

    # Verify parent state updated
    assert view.selected_user_id == 123

    # Verify computed properties work
    assert view.selected_user is not None
```

## Common Migration Scenarios

### Scenario 1: Badge Component

**Current:**
```python
class BadgeComponent(LiveComponent):
    template_name = 'components/badge.html'

    def mount(self, text, variant="primary"):
        self.text = text
        self.variant = variant
```

**Migration Decision:** Simple Component (or template syntax)

**Migrated:**
```python
# Option A: Simple Component
class BadgeComponent(Component):
    def __init__(self, text, variant="primary"):
        self.text = text
        self.variant = variant

    def render(self):
        return f'<span class="badge bg-{self.variant}">{self.text}</span>'

# Option B: Template Syntax (recommended)
# Just use: <span class="badge bg-{{ variant }}">{{ text }}</span>
```

### Scenario 2: Tabs Component

**Current:**
```python
class TabsComponent(LiveComponent):
    template_name = 'components/tabs.html'

    def mount(self, tabs, active):
        self.tabs = tabs
        self.active = active

    def activate_tab(self, tab_id):
        self.active = tab_id
        self.trigger_update()  # Old API
```

**Migration Decision:** Stay as LiveComponent (has state)

**Migrated:**
```python
class TabsComponent(LiveComponent):
    template_string = """
        <ul class="nav nav-tabs">
            {% for tab in tabs %}
            <button dj-click="switch_tab" data-tab="{{ tab.id }}"
                    class="nav-link {% if tab.id == active %}active{% endif %}">
                {{ tab.label }}
            </button>
            {% endfor %}
        </ul>
    """

    def mount(self, tabs, active=None):
        self.tabs = tabs
        self.active = active or tabs[0].id

    def switch_tab(self, tab):
        self.active = tab
        self.send_parent("tab_changed", {"tab": tab})  # New API

    def get_context_data(self):
        return {'tabs': self.tabs, 'active': self.active}
```

**Changes:**
- Removed `activate_tab()` (called by parent) → Changed to `switch_tab()` (called by user)
- Added `send_parent()` to notify parent
- Added `template_string` (optional - could keep template file)

### Scenario 3: Form Component

**Current:**
```python
class UserFormComponent(LiveComponent):
    def mount(self, user=None):
        self.user = user
        self.errors = {}

    def validate_field(self, field, value):
        # Validation logic
        if not value:
            self.errors[field] = "Required"
        else:
            self.errors.pop(field, None)

    def submit_form(self, form_data):
        # Validate all fields
        if self.validate_all(form_data):
            # Save to database
            if self.user:
                self.user.update(**form_data)
            else:
                self.user = User.objects.create(**form_data)

            # Notify parent
            self.trigger_update()  # Old API
```

**Migration Decision:** Stay as LiveComponent, improve event handling

**Migrated:**
```python
class UserFormComponent(LiveComponent):
    def mount(self, user=None):
        self.user = user
        self.errors = {}
        self.is_submitting = False

    def validate_field(self, field, value):
        """Real-time field validation"""
        if not value:
            self.errors[field] = "Required"
        else:
            self.errors.pop(field, None)

    def submit_form(self, **form_data):
        """Submit form and notify parent"""
        self.is_submitting = True

        if self.validate_all(form_data):
            if self.user:
                self.user.update(**form_data)
            else:
                self.user = User.objects.create(**form_data)

            # New API: Send success event to parent
            self.send_parent("form_submitted", {
                "user_id": self.user.id,
                "action": "update" if self.user else "create"
            })
        else:
            # Send error event
            self.send_parent("form_error", {
                "errors": self.errors
            })

        self.is_submitting = False

    def get_context_data(self):
        return {
            'user': self.user,
            'errors': self.errors,
            'is_submitting': self.is_submitting
        }
```

**Changes:**
- Added `send_parent()` for success/error events
- Added `is_submitting` state for UX
- Removed `trigger_update()` (automatic)

## Troubleshooting

### Issue 1: Component State Lost on Re-render

**Symptom:** Component resets every time parent updates

**Cause:** Creating component in `get_context_data()` instead of `mount()`

**Fix:**
```python
# ❌ Wrong - recreates every render
def get_context_data(self):
    return {
        'tabs': TabsComponent(tabs=self.tabs)  # New instance!
    }

# ✅ Correct - create once in mount
def mount(self, request):
    self.tabs = TabsComponent(tabs=self.tab_data)

def get_context_data(self):
    return {
        'tabs': self.tabs  # Same instance
    }
```

### Issue 2: Events Not Working

**Symptom:** Clicking buttons does nothing

**Cause:** Not sending events to parent, or parent not handling them

**Fix:**
```python
# In component - send event
def switch_tab(self, tab):
    self.active = tab
    self.send_parent("tab_changed", {"tab": tab})  # Add this!

# In parent - handle event
def handle_component_event(self, component_id, event, data):
    if event == "tab_changed":
        self.current_tab = data["tab"]  # Add handler!
```

### Issue 3: Props Not Updating

**Symptom:** Component doesn't react to parent state changes

**Cause:** Missing `update()` method

**Fix:**
```python
class UserDetailComponent(LiveComponent):
    def mount(self, user=None):
        self.user = user

    # Add this method!
    def update(self, **props):
        if 'user' in props:
            self.user = props['user']
            self._load_user_data()
```

### Issue 4: VDOM Patches Not Working

**Symptom:** Full page refresh instead of patches

**Cause:** Template structure changed (dynamic template_string)

**Fix:** Use static `template_string`, not `@property`

```python
# ❌ Wrong - dynamic template breaks VDOM
@property
def template_string(self):
    return f"""<div>{self.build_html()}</div>"""

# ✅ Correct - static template
template_string = """
    <div>
        {% for item in items %}
        <li>{{ item }}</li>
        {% endfor %}
    </div>
"""
```

## Migration Checklist

Use this checklist for each component:

- [ ] Identified component type (simple or stateful)
- [ ] Migrated class definition
- [ ] Migrated `mount()` / `__init__()`
- [ ] Migrated template (file or string)
- [ ] Added event handlers if needed
- [ ] Added `send_parent()` calls
- [ ] Added `update()` method if reactive
- [ ] Updated parent to create component in `mount()`
- [ ] Updated parent to handle events
- [ ] Added tests
- [ ] Verified VDOM patching works
- [ ] Performance tested
- [ ] Documentation updated

## Summary

**Migration Path:**

1. **Audit** → Identify component types
2. **Simplify** → Convert to simple components or template syntax
3. **Enhance** → Add state and events to LiveComponents
4. **Coordinate** → Update parent event handling
5. **Test** → Verify behavior and performance
6. **Optimize** → Add reactivity, computed properties

**Remember:**
- Migrate incrementally (not all at once)
- Test thoroughly after each migration
- Simple components first (quick wins)
- Parent coordination is key

**Next Steps:**
- [Examples](COMPONENT_EXAMPLES.md) - See complete migrated examples
- [Best Practices](COMPONENT_BEST_PRACTICES.md) - Learn patterns
- [API Reference](API_REFERENCE_COMPONENTS.md) - Detailed API
