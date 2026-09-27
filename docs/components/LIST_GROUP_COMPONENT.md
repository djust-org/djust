# ListGroup Component Documentation

> **Current API:** This page's constructor and rendering examples match
> `djust.components.ui.ListGroup`. The test counts, implementation history,
> performance estimates, and file paths near the end are historical notes, not
> current release guarantees. `RustListGroup` is not currently exported by the
> compiled extension; ListGroup uses its Python renderer.

## Overview

The `ListGroup` component is a stateless component for displaying lists of items with optional actions, active highlighting, and color variants.

## Component Details

- **File**: `python/djust/components/ui/list_group_simple.py`
- **Type**: Stateless Component (uses `_render_custom()` method)
- **Complexity**: Medium (uses loops, Python-based)
- **Rendering**: Python `_render_custom()` implementation

## Features

- List of items with labels and optional URLs
- Active item highlighting
- Disabled items support
- Flush variant (removes borders for edge-to-edge styling)
- Numbered list option
- Color variants (primary, success, danger, warning, info)
- Badge support for individual items
- Framework-agnostic (Bootstrap 5, Tailwind CSS, Plain HTML)

## Parameters

- **items**: `List[Dict]` - List of item dictionaries
- **flush**: `bool` - Remove borders (default: False)
- **numbered**: `bool` - Display as numbered list (default: False)

### Item Dictionary Format

Each item in the `items` list can have the following keys:

```python
{
    'label': 'Item Label',           # Required - Text to display
    'url': '/path/to/page',          # Optional - Creates clickable link
    'active': False,                 # Optional - Highlight as active
    'disabled': False,               # Optional - Disable interaction
    'variant': 'primary',            # Optional - Color variant
    'badge': {                       # Optional - Add badge to item
        'text': '5',
        'variant': 'primary'
    }
}
```

## Usage Examples

### Basic Navigation List

```python
from djust.components.ui import ListGroup

nav_list = ListGroup(items=[
    {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
    {'label': 'Profile', 'url': '/profile'},
    {'label': 'Settings', 'url': '/settings'},
])

```

In a Django template:

```django
{{ nav_list }}
```

Output (Bootstrap 5):
```html
<ul class="list-group">
  <a href="/dashboard" class="list-group-item list-group-item-action active" aria-current="true">Dashboard</a>
  <a href="/profile" class="list-group-item list-group-item-action">Profile</a>
  <a href="/settings" class="list-group-item list-group-item-action">Settings</a>
</ul>
```

### List with Badges

```python
list_with_badges = ListGroup(items=[
    {
        'label': 'Messages',
        'url': '/messages',
        'badge': {'text': '5', 'variant': 'primary'}
    },
    {
        'label': 'Notifications',
        'url': '/notifications',
        'badge': {'text': '12', 'variant': 'danger'}
    },
])
```

### Flush Variant (No Borders)

```python
sidebar_menu = ListGroup(
    items=[
        {'label': 'Profile Settings', 'url': '/settings/profile'},
        {'label': 'Security', 'url': '/settings/security'},
        {'label': 'Notifications', 'url': '/settings/notifications'},
    ],
    flush=True
)
```

### Numbered List

```python
task_list = ListGroup(
    items=[
        {'label': 'Review pull requests', 'url': '#'},
        {'label': 'Update documentation', 'url': '#'},
        {'label': 'Deploy to production', 'url': '#', 'disabled': True},
    ],
    numbered=True
)
```

### Color Variants

```python
status_list = ListGroup(items=[
    {'label': 'All systems operational', 'variant': 'success'},
    {'label': 'Scheduled maintenance tonight', 'variant': 'warning'},
    {'label': 'High CPU usage detected', 'variant': 'danger'},
])
```

### Complex Example

```python
complex_list = ListGroup(items=[
    {
        'label': 'Dashboard',
        'url': '/dashboard',
        'active': True,
        'badge': {'text': 'New', 'variant': 'primary'}
    },
    {
        'label': 'Messages',
        'url': '/messages',
        'badge': {'text': '5', 'variant': 'danger'}
    },
    {
        'label': 'Important Notice',
        'variant': 'warning'
    },
    {
        'label': 'Settings',
        'url': '/settings',
        'disabled': True
    },
])
```

## In LiveView

```python
from djust import LiveView
from djust.components.ui import ListGroup

class DashboardView(LiveView):
    template_name = 'dashboard.html'

    def mount(self, request, **kwargs):
        # Create navigation list
        self.nav_list = ListGroup(items=[
            {'label': 'Dashboard', 'url': '/', 'active': True},
            {'label': 'Users', 'url': '/users', 'badge': {'text': '12', 'variant': 'primary'}},
            {'label': 'Products', 'url': '/products'},
        ])

        # Create status list
        self.status_list = ListGroup(items=[
            {'label': 'System Online', 'variant': 'success'},
            {'label': 'Database Connected', 'variant': 'success'},
            {'label': 'Cache Warming Up', 'variant': 'warning'},
        ])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['nav_list'] = self.nav_list
        context['status_list'] = self.status_list
        return context
```

Template:
```html
<div class="container">
    <!-- Navigation -->
    <div class="card mb-3">
        <div class="card-header">Navigation</div>
        <div class="card-body p-0">
            {{ nav_list }}
        </div>
    </div>

    <!-- Status -->
    <div class="card">
        <div class="card-header">System Status</div>
        <div class="card-body">
            {{ status_list }}
        </div>
    </div>
</div>
```

## Framework Support

### Bootstrap 5 (Default)

Uses Bootstrap 5 list-group classes:
- `list-group`, `list-group-item`
- `list-group-flush`, `list-group-numbered`
- `list-group-item-action` for clickable items
- `list-group-item-{variant}` for colors
- `active`, `disabled` states

### Tailwind CSS

Uses Tailwind utility classes:
- `divide-y divide-gray-200` for borders
- `rounded-lg border border-gray-200` for container
- `px-4 py-3` for spacing
- `bg-{color}-50 text-{color}-900` for variants
- `hover:bg-gray-50` for hover states

### Plain HTML

Uses semantic HTML with custom CSS classes:
- `list-group`, `list-group-item`
- `list-group-flush`, `list-group-numbered`
- `list-group-item-{variant}` for colors
- `list-group-item-active`, `list-group-item-disabled` for states

## Historical Test Report

All 12 tests passed successfully:

1. ✓ Basic list group with simple items
2. ✓ Active and disabled states
3. ✓ Flush variant (no borders)
4. ✓ Numbered list
5. ✓ Color variants (success, warning, danger, primary)
6. ✓ Items with badges
7. ✓ Empty list
8. ✓ Non-link items (static content)
9. ✓ Mixed items (links and non-links)
10. ✓ Complex example with all features
11. ✓ Tailwind framework support
12. ✓ Plain HTML framework support

## Rendering Notes

- **Current**: Python rendering using loops.
- Component-specific render timings have not been measured in the current
  benchmark suite. The compiled extension does not currently export a
  `RustListGroup` implementation.

## Common Use Cases

1. **Navigation Menus**: Primary navigation with active states
2. **Action Lists**: Items with click handlers or links
3. **Status Indicators**: System status with color coding
4. **Settings Panels**: Configuration options lists
5. **Notification Lists**: Messages/alerts with badges
6. **Task Lists**: Todo items with numbered steps
7. **Sidebar Menus**: Flush variant in sidebars/cards
8. **Dashboard Widgets**: Quick links with counts

## Integration with Other Components

ListGroup works well with:
- **Card**: Wrap ListGroup in Card for bordered sections
- **Badge**: Individual items can have badges
- **Modal**: Use ListGroup inside modals for selections
- **Tabs**: Different lists in different tabs
- **Dropdown**: Alternative to dropdown menus

## Accessibility

The component includes proper ARIA attributes:
- `aria-current="true"` for active items
- `aria-disabled="true"` for disabled items
- Semantic HTML (`<ul>`, `<ol>`, `<li>`, `<a>`)

## Export Status

Available from `djust.components.ui`:
- Import: `from .list_group_simple import ListGroup`
- Export: Added to `__all__` list

## Historical Implementation Notes

The original implementation report and test counts describe an earlier checkout;
they are retained as project history and are not a statement about the current
test suite or repository layout.

## Next Steps

`ListGroup` is available to import from `djust.components.ui`.

## Code Quality

- Follows djust component patterns (Base Component class)
- Uses type hints for better IDE support
- Comprehensive docstrings
- Framework-agnostic implementation
- Dynamic text, classes, and URLs are escaped by the current renderer.
- Semantic HTML structure
- Accessibility attributes included
