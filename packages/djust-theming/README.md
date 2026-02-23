# djust-theming

CSS theming utilities and base stylesheets for [djust](https://github.com/djust-framework/djust) applications.

Provides dark/light mode with CSS variables, design tokens, and component styles. Works independently of djust core — usable in any Django project.

## Installation

```bash
pip install djust-theming
```

Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    ...
    "djust_theming",
]
```

## Usage

In your base template:

```html
{% load static %}
<link rel="stylesheet" href="{% static 'djust_theming/css/theme.css' %}">
<link rel="stylesheet" href="{% static 'djust_theming/css/components.css' %}">
<link rel="stylesheet" href="{% static 'djust_theming/css/utilities.css' %}">
```

Add a theme toggle button:

```html
<button id="theme-toggle" class="theme-toggle">
  <svg class="theme-icon-dark" ...><!-- moon icon --></svg>
  <svg class="theme-icon-light" ...><!-- sun icon --></svg>
</button>

<script src="{% static 'djust_theming/js/theme-switcher.js' %}"></script>
```

## What's Included

### `theme.css`
CSS variable design tokens for both themes. Set `data-theme="dark"` or `data-theme="light"` on `<html>` to switch.

**Tokens:**
- Spacing: `--spacing-xs` … `--spacing-2xl`
- Border radius: `--radius-sm`, `--radius-md`, `--radius-lg`
- Transitions: `--transition-fast`, `--transition-base`, `--transition-slow`
- Colors: `--color-bg`, `--color-text`, `--color-accent`, `--color-success`, `--color-warning`, `--color-error`, and more
- Shadows: `--shadow-sm`, `--shadow-md`, `--shadow-lg`

### `components.css`
Pre-built component styles using the design tokens: navbar, buttons, cards, badges, code blocks, hero sections, comparison tables.

### `utilities.css`
Helper classes and Tailwind/Bootstrap overrides that respect the active theme.

### `theme-switcher.js`
~1KB script that:
- Reads `localStorage` for the user's saved preference
- Falls back to `prefers-color-scheme` media query
- Toggles `data-theme` on `<html>` on button click
- Listens for OS-level theme changes

## Themes

Defaults to dark. To default to light, set on `<html>`:

```html
<html data-theme="light">
```

## Requirements

- Django ≥ 3.2
- Python ≥ 3.8

## License

MIT
