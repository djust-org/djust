# Template Conversion Guide: Using base.html

**Date**: November 14, 2024
**Status**: In Progress
**Purpose**: Guide for converting existing templates to use the new base.html template

---

## Overview

We've created a unified `base.html` template with global navbar from a context processor. This eliminates template duplication and ensures consistent navigation across all pages.

## What Changed

### New Infrastructure:
1. **`demo_app/templates/base.html`** - Shared base template
2. **`demo_app/context_processors.py`** - Provides navbar globally
3. **`settings.py`** - Registers context processor
4. **`views/navbar_example.py`** - Updated BaseViewWithNavbar (no longer sets navbar)

### Benefits:
- Consistent navbar across all pages
- ~50 lines less code per page
- Single source of truth for navigation
- No more serialization issues with navbar objects

---

## Conversion Pattern

### Step 1: Identify Template Structure

**Before** (Original template):
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page Title - djust</title>

    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="/static/css/theme.css">
    <link rel="stylesheet" href="/static/css/components.css">
    <link rel="stylesheet" href="/static/css/utilities.css">
    <script src="https://cdn.tailwindcss.com"></script>

    <!-- Optional: Syntax highlighting -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
</head>
<body>
    <div data-djust-root>
    <!-- Navbar Component -->
    {{ navbar.render }}

    <!-- Theme Toggle -->
    <button class="theme-toggle" id="theme-toggle" aria-label="Toggle theme">
        <!-- SVG icons... -->
    </button>

    <!-- Page Content Here -->
    <section>...</section>

    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="/static/js/theme-switcher.js"></script>

    <!-- Optional: Syntax highlighting -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <script>hljs.highlightAll();</script>
</body>
</html>
```

### Step 2: Convert to base.html

**After** (Using base template):
```html
{% extends "base.html" %}

{% block title %}Page Title - djust{% endblock %}

{% block syntax_highlighting %}
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
{% endblock %}

{% block body_start %}<div data-djust-root>{% endblock %}

{% block content %}
    <!-- Page Content Here -->
    <section>...</section>
    </div>
{% endblock %}

{% block extra_js %}
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>hljs.highlightAll();</script>
{% endblock %}
```

### Step 3: Key Points

**Remove these (now in base.html):**
- ✂️ `<!DOCTYPE html>`, `<html>`, `<head>`, `<body>` tags
- ✂️ Bootstrap CSS link
- ✂️ Theme CSS links (theme.css, components.css, utilities.css)
- ✂️ Tailwind script tag
- ✂️ Navbar component (`{{ navbar.render }}`)
- ✂️ Theme toggle button
- ✂️ Bootstrap JS script
- ✂️ Theme switcher script
- ✂️ Closing `</body>` and `</html>` tags

**Keep these (page-specific):**
- ✅ Page title (in `{% block title %}`)
- ✅ Page content (in `{% block content %}`)
- ✅ Optional syntax highlighting (in `{% block syntax_highlighting %}` and `{% block extra_js %}`)
- ✅ Any page-specific CSS (in `{% block extra_css %}`)
- ✅ Any page-specific JS (in `{% block extra_js %}`)

**Special case for LiveView:**
- ✅ Add `{% block body_start %}<div data-djust-root>{% endblock %}` at the top
- ✅ Close the div at the end of `{% block content %}`

---

## Available Blocks in base.html

```django
{% block title %}djust - Blazing Fast Reactive Django{% endblock %}
{% block extra_css %}{% endblock %}
{% block syntax_highlighting %}{% endblock %}
{% block extra_head %}{% endblock %}
{% block body_start %}{% endblock %}
{% block navbar %}{{ navbar.render }}{% endblock %}
{% block content %}{% endblock %}
{% block extra_js %}{% endblock %}
```

**Usage:**
- `title`: Page title (appears in browser tab)
- `extra_css`: Additional CSS files or inline styles
- `syntax_highlighting`: Code highlighting CSS (e.g., highlight.js)
- `extra_head`: Any other head content
- `body_start`: Content at start of body (e.g., `<div data-djust-root>`)
- `navbar`: Navbar component (usually don't override this)
- `content`: Main page content
- `extra_js`: Additional JavaScript files or inline scripts

---

## Example: Counter Demo Conversion

### Before (222 lines)
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Counter Demo - djust</title>
    <!-- ... 20 lines of CSS/JS imports ... -->
</head>
<body>
    <div data-djust-root>
    {{ navbar.render }}
    <!-- ... 15 lines of theme toggle ... -->

    <!-- Content starts here -->
    <section class="hero-section">
        <!-- ... actual demo content ... -->
    </section>
    <!-- Content ends here -->

    </div>
    <!-- ... 10 lines of scripts ... -->
</body>
</html>
```

### After (193 lines)
```html
{% extends "base.html" %}

{% block title %}Counter Demo - djust{% endblock %}

{% block syntax_highlighting %}
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
{% endblock %}

{% block body_start %}<div data-djust-root>{% endblock %}

{% block content %}
    <!-- Content starts here -->
    <section class="hero-section">
        <!-- ... actual demo content ... -->
    </section>
    <!-- Content ends here -->
    </div>
{% endblock %}

{% block extra_js %}
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>hljs.highlightAll();</script>
{% endblock %}
```

**Savings**: ~30 lines removed, much cleaner!

---

## View Changes (Important!)

If your view inherits from `BaseViewWithNavbar`, **no changes needed** - it still works!

The `BaseViewWithNavbar` class has been updated to no longer set the navbar (since it's now global). Views can safely inherit from `LiveView` directly instead, but inheriting from `BaseViewWithNavbar` won't break anything.

```python
# Both work fine:
class CounterView(BaseViewWithNavbar):  # ✅ Works (backward compatible)
    pass

class CounterView(LiveView):  # ✅ Also works (preferred going forward)
    pass
```

---

## Conversion Checklist

For each template file:

### Analysis Phase:
- [ ] Identify if template uses navbar (`{{ navbar }}` or `{{ navbar.render }}`)
- [ ] Check if template has LiveView (`data-djust-root`)
- [ ] Note any page-specific CSS/JS
- [ ] Note if syntax highlighting is used

### Conversion Phase:
- [ ] Add `{% extends "base.html" %}` at top
- [ ] Convert title to `{% block title %}`
- [ ] Move syntax highlighting CSS to `{% block syntax_highlighting %}`
- [ ] Add `{% block body_start %}` for LiveView div if needed
- [ ] Wrap main content in `{% block content %}`
- [ ] Move extra JS to `{% block extra_js %}`
- [ ] Remove all boilerplate (head, navbar, theme toggle, scripts)

### Testing Phase:
- [ ] Test page loads (HTTP 200)
- [ ] Verify navbar appears and correct item is active
- [ ] Verify theme toggle works
- [ ] Verify all page functionality works
- [ ] Test on different screen sizes (responsive)

---

## Templates to Convert

### High Priority (Core Demos):
- [x] `/demos/counter/` - ✅ **Completed** (proof of concept)
- [ ] `/demos/todo/`
- [ ] `/demos/chat/`
- [ ] `/demos/react/`
- [ ] `/demos/datatable/`

### Medium Priority (Phase 5 Demos):
- [ ] `/demos/debounce/` (uses template_string, may not need conversion)
- [ ] `/demos/throttle/` (uses template_string, may not need conversion)
- [ ] `/demos/cache/` (uses template_string, may not need conversion)
- [ ] `/demos/optimistic-counter/` (uses template_string, may not need conversion)
- [ ] `/demos/optimistic-todo/` (uses template_string, may not need conversion)

### Low Priority (Other Pages):
- [ ] `/` (homepage)
- [ ] `/demos/` (demos index)
- [ ] `/forms/` (forms pages)
- [ ] `/tests/` (test pages)

---

## Troubleshooting

### Issue: "TypeError: Object of type NavbarComponent is not JSON serializable"

**Cause**: View is setting `context['navbar']` with NavbarComponent object, which conflicts with global navbar and causes LiveView serialization issues.

**Solution**:
1. Check if view inherits from `BaseViewWithNavbar` - it should be updated to not set navbar
2. If view manually sets navbar, remove that code
3. Ensure `context_processors.navbar` is registered in settings

### Issue: Navbar not appearing

**Cause**: Context processor not registered or not working.

**Solution**:
```python
# Check settings.py TEMPLATES config:
'context_processors': [
    # ... other processors ...
    'demo_app.context_processors.navbar',  # ← Must be present
],
```

### Issue: Wrong navbar item is active

**Cause**: Path matching in context processor might need adjustment.

**Solution**: Check `context_processors.py` active logic:
```python
NavItem("Demos", "/demos/", active=path.startswith("/demos/"))
```

---

## Progress Tracking

**Total Templates**: ~30
**Converted**: 1 (counter demo)
**Remaining**: 29

**Progress**: 3% complete

---

**Last Updated**: November 14, 2024
