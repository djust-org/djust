# NavBar Component - Quick Start Guide

## Installation

The NavBar component is already included in djust. No additional installation needed.

## Basic Usage

```python
from djust.components.ui import NavBar

# Create a simple navbar
navbar = NavBar(
    brand={'text': 'MyApp', 'url': '/'},
    items=[
        {'label': 'Home', 'url': '/', 'active': True},
        {'label': 'About', 'url': '/about'},
        {'label': 'Contact', 'url': '/contact'},
    ]
)

# Render in template
html = navbar.render()
```

## Common Patterns

### Pattern 1: Simple Navbar

```python
navbar = NavBar(
    brand={'text': 'MyApp', 'url': '/'},
    items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'Features', 'url': '/features'},
        {'label': 'Pricing', 'url': '/pricing'},
    ]
)
```

### Pattern 2: Dark Navbar with Logo

```python
navbar = NavBar(
    brand={
        'text': 'MyApp',
        'url': '/',
        'logo': '/static/logo.png'
    },
    items=[
        {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
        {'label': 'Settings', 'url': '/settings'},
    ],
    variant='dark'
)
```

### Pattern 3: Navbar with Dropdowns

```python
navbar = NavBar(
    brand={'text': 'MyApp', 'url': '/'},
    items=[
        {'label': 'Home', 'url': '/', 'active': True},
        {
            'label': 'Products',
            'dropdown': [
                {'label': 'All Products', 'url': '/products'},
                {'label': 'Categories', 'url': '/categories'},
                {'divider': True},
                {'label': 'Add New', 'url': '/products/new'},
            ]
        },
        {'label': 'About', 'url': '/about'},
    ]
)
```

### Pattern 4: Sticky Navbar

```python
navbar = NavBar(
    brand={'text': 'MyApp', 'url': '/'},
    items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'Features', 'url': '/features'},
    ],
    sticky='top',
    variant='dark'
)
```

## In LiveView

```python
from djust import LiveView
from djust.components.ui import NavBar

class MyView(LiveView):
    template_name = 'my_template.html'

    def mount(self, request):
        self.navbar = NavBar(
            brand={'text': 'MyApp', 'url': '/'},
            items=[
                {'label': 'Home', 'url': '/', 'active': True},
                {'label': 'Profile', 'url': '/profile'},
            ]
        )

    def get_context_data(self):
        return {'navbar': self.navbar}
```

**Template (my_template.html):**
```html
<!DOCTYPE html>
<html>
<head>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
    {{ navbar.render }}

    <div class="container mt-4">
        <h1>Your Content Here</h1>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
```

## Parameters Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `items` | `List[Dict]` | Required | List of navigation items |
| `brand` | `Dict` | `None` | Brand config: `{'text', 'url', 'logo'}` |
| `variant` | `str` | `'light'` | `'light'` or `'dark'` |
| `sticky` | `str/bool` | `False` | `'top'`, `'bottom'`, or `False` |
| `container` | `str` | `'fluid'` | `'container'` or `'fluid'` |
| `expand` | `str` | `'lg'` | `'sm'`, `'md'`, `'lg'`, `'xl'`, `'xxl'` |
| `id` | `str` | Auto | Navbar ID (auto-generated) |

## Item Formats

### Regular Item
```python
{
    'label': 'Home',
    'url': '/',
    'active': True,     # Optional
    'disabled': False   # Optional
}
```

### Dropdown Item
```python
{
    'label': 'Products',
    'dropdown': [
        {'label': 'Item 1', 'url': '/item1'},
        {'label': 'Item 2', 'url': '/item2'},
        {'divider': True},
        {'label': 'Item 3', 'url': '/item3'},
    ]
}
```

## Required HTML Dependencies

### For Bootstrap 5 (Default)
```html
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
```

### For Tailwind CSS
```html
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js"></script>
```

## Demo Page

View live examples at:
```
http://localhost:8002/demos/navbar/
```

## Full Example

```python
# views.py
from djust import LiveView
from djust.components.ui import NavBar

class AppView(LiveView):
    template_name = 'app.html'

    def mount(self, request):
        # Get current path for active state
        current_path = request.path

        self.navbar = NavBar(
            brand={
                'text': 'Enterprise App',
                'url': '/',
                'logo': '/static/img/logo.png'
            },
            items=[
                {
                    'label': 'Dashboard',
                    'url': '/dashboard',
                    'active': current_path == '/dashboard'
                },
                {
                    'label': 'Products',
                    'dropdown': [
                        {'label': 'All Products', 'url': '/products'},
                        {'label': 'Categories', 'url': '/categories'},
                        {'divider': True},
                        {'label': 'Add New', 'url': '/products/new'},
                    ]
                },
                {
                    'label': 'Reports',
                    'dropdown': [
                        {'label': 'Sales', 'url': '/reports/sales'},
                        {'label': 'Inventory', 'url': '/reports/inventory'},
                    ]
                },
                {
                    'label': 'Settings',
                    'url': '/settings',
                    'active': current_path == '/settings'
                },
            ],
            variant='dark',
            sticky='top',
            container='fluid',
            expand='lg'
        )

    def get_context_data(self):
        return {
            'navbar': self.navbar,
            'page_title': 'Dashboard'
        }
```

```html
<!-- app.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ page_title }}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body>
    <!-- NavBar Component -->
    {{ navbar.render }}

    <!-- Page Content -->
    <div class="container-fluid mt-4">
        <h1>{{ page_title }}</h1>
        <p>Your application content goes here.</p>
    </div>

    <!-- Bootstrap JS (required for dropdowns) -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
```

## Tips

1. **Active State:** Set `active: True` on the current page's nav item
2. **Mobile First:** Test on mobile devices - navbar collapses automatically
3. **Dropdowns:** Require Bootstrap JS for proper functionality
4. **Sticky Nav:** Use `sticky='top'` for fixed navigation
5. **Dark Mode:** Use `variant='dark'` for dark backgrounds
6. **Container:** Use `container='fluid'` for full-width layouts

## Framework Switching

```python
from djust.config import config

# Use Bootstrap 5 (default)
config._config = {'css_framework': 'bootstrap5'}
navbar = NavBar(brand={'text': 'App'}, items=[...])

# Use Tailwind CSS
config._config = {'css_framework': 'tailwind'}
navbar = NavBar(brand={'text': 'App'}, items=[...])

# Use Plain HTML
config._config = {'css_framework': 'plain'}
navbar = NavBar(brand={'text': 'App'}, items=[...])
```

## Troubleshooting

### Dropdowns not working
- ✅ Make sure Bootstrap JS is included
- ✅ Check that Bootstrap CSS is loaded
- ✅ Verify dropdown items format is correct

### Navbar not responsive
- ✅ Include viewport meta tag
- ✅ Check that Bootstrap CSS is loaded
- ✅ Test `expand` parameter setting

### Styling issues
- ✅ Verify CSS framework is loaded
- ✅ Check for CSS conflicts
- ✅ Ensure proper variant ('light' or 'dark')

## More Information

- **Full Documentation:** See `NAVBAR_COMPONENT_REPORT.md`
- **Test Suite:** Run `python test_navbar_simple.py`
- **Live Demo:** Visit `http://localhost:8002/demos/navbar/`
- **Component Code:** `python/djust/components/ui/navbar_simple.py`
