---
title: "CSS Frameworks"
slug: css-frameworks
section: guides
order: 11
level: beginner
description: "Set up Tailwind CSS or Bootstrap 5 with djust, configure the css_framework setting, and integrate CSS builds into CI/CD."
---

# CSS Frameworks

djust ships with built-in support for Tailwind CSS and Bootstrap 5. The framework you choose affects how djust renders form fields and components automatically — no manual widget styling required.

## Quick Setup

One command sets up and builds your CSS:

```bash
# Tailwind CSS (recommended)
python manage.py djust_setup_css tailwind

# Bootstrap (manual install — see below)
python manage.py djust_setup_css bootstrap

# No CSS framework (plain HTML)
python manage.py djust_setup_css none
```

## Configuring the CSS Framework

Add `css_framework` to `LIVEVIEW_CONFIG` in `settings.py`:

```python
LIVEVIEW_CONFIG = {
    'css_framework': 'tailwind',   # 'tailwind', 'bootstrap5', or None
}
```

| Value | Description |
|---|---|
| `'bootstrap5'` | Bootstrap 5 classes on forms and components (default) |
| `'bootstrap4'` | Bootstrap 4 classes — for legacy projects, NYC Core Framework, government sites |
| `'tailwind'` | Tailwind utility classes on forms and components |
| `None` / `'plain'` | Unstyled plain HTML |

This setting controls how `FormMixin` and djust's built-in components render their fields. You can still use any CSS framework for your own templates — this only affects the auto-generated widget HTML.

---

## Tailwind CSS

### One-command Setup

```bash
python manage.py djust_setup_css tailwind
```

This command:
1. Creates `static/css/input.css` with `@import "tailwindcss"` and `@source` directives for your template directories
2. Creates `tailwind.config.js` (Tailwind v3) if it doesn't exist
3. Detects your `TEMPLATES` directories automatically
4. Runs the Tailwind CLI to compile `static/css/output.css`

### Prerequisites

The command auto-detects the Tailwind CLI. Install it via npm:

```bash
npm install -D tailwindcss
```

Or use the [standalone CLI](https://tailwindcss.com/blog/standalone-cli) (no Node required):

```bash
# macOS ARM
curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-arm64
chmod +x tailwindcss-macos-arm64
```

### `settings.py` Requirements

`STATICFILES_DIRS` must be configured so the command knows where to write the output CSS:

```python
BASE_DIR = Path(__file__).resolve().parent.parent

STATICFILES_DIRS = [BASE_DIR / "static"]
```

### Add CSS to Base Template

After running the setup command, link the compiled CSS in your base template:

```html
{% load static %}
<link rel="stylesheet" href="{% static 'css/output.css' %}">
```

### Watch Mode (Development)

Auto-rebuild when templates change:

```bash
python manage.py djust_setup_css tailwind --watch
```

### Tailwind v3 vs v4

| Version | Config file | Input CSS |
|---|---|---|
| **v4** (recommended) | No config needed | `@import "tailwindcss"; @source "../templates/";` |
| **v3** | `tailwind.config.js` with `content:` paths | `@tailwind base; @tailwind components; @tailwind utilities;` |

`djust_setup_css` generates v4-style `input.css` by default. For v3, it also creates a `tailwind.config.js`.

---

## Bootstrap 5

### Manual Setup

Bootstrap does not require a build step if you use the CDN:

```html
<!-- In your base template <head> -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
```

Configure djust to use Bootstrap 5 styling:

```python
LIVEVIEW_CONFIG = {
    'css_framework': 'bootstrap5',
}
```

### npm Install

For a self-hosted setup:

```bash
npm install bootstrap
```

Then import in your CSS entry point:

```css
@import "bootstrap/dist/css/bootstrap.min.css";
```

### Bootstrap + Sass Compilation

For custom theming with Sass variables:

```bash
npm install bootstrap sass
```

```scss
// static/scss/main.scss
$primary: #6366f1;   // Override Bootstrap variables
$border-radius: 0.5rem;

@import "bootstrap/scss/bootstrap";
```

Compile:

```bash
npx sass static/scss/main.scss static/css/main.css
```

---

## Bootstrap 4

Bootstrap 4 still ships across many legacy projects, government sites,
and frameworks like NYC Core. djust's `Bootstrap4Adapter` (added in
v0.5.0) renders form widgets with the BS4 class set instead of BS5.

```python
# settings.py
LIVEVIEW_CONFIG = {
    'css_framework': 'bootstrap4',
}
```

The adapter handles the four BS4-specific class differences from BS5:

| Element type | BS4 class | BS5 class |
|---|---|---|
| `<select>` | `custom-select` | `form-select` |
| Checkbox/radio wrapper | `custom-control custom-checkbox` | `form-check` |
| Form group wrapper | `form-group` | `mb-3` |
| Help text | `form-text text-muted` | `form-text` |

Install Bootstrap 4 the same way you would for any Bootstrap project —
bring your own Bootstrap 4 CSS via npm/CDN. djust does not bundle it.

### Radio buttons (separate config keys)

Radio inputs use their own config keys rather than reusing the
checkbox classes (BS4 and BS5 style them differently). Override in the
adapter or via `LIVEVIEW_CONFIG`:

| Key | Bootstrap 4 default | Bootstrap 5 default |
|---|---|---|
| `radio_class` | `custom-control-input` | `form-check-input` |
| `radio_label_class` | `custom-control-label` | `form-check-label` |
| `radio_wrapper_class` | `custom-control custom-radio` | `form-check` |

If a radio key is not set, djust falls back to the corresponding
checkbox key — so existing Bootstrap 5 projects don't need to define
radio classes explicitly.

### Select widgets

`ChoiceField` rendered with `widget=Select` reads its class from
`select_class` (default `form-select` for BS5, `custom-select` for
BS4) instead of the generic `field_class` used for `<input>` fields.
This keeps native dropdowns from picking up the wrong borders/padding.

---

## Theme tokens → framework classes

djust's theming system exposes design tokens as CSS variables
(`--primary`, `--border`, `--background`, …). The
`{% theme_framework_overrides %}` template tag emits a `<style>` block
that maps those tokens onto the active framework's class selectors so
a theme switch automatically restyles Bootstrap components without
editing Bootstrap source:

```django
{% load djust %}
<head>
  …
  {% theme_framework_overrides %}
</head>
```

Example output (Bootstrap 5, abridged):

```css
.btn-primary { background-color: var(--primary); border-color: var(--primary); }
.form-control { border-color: var(--border); background: var(--background); }
.alert-info { background: color-mix(in oklch, var(--primary), white 80%); }
```

The tag is a no-op when `css_framework='tailwind'` or `None` — Tailwind
projects manage their palette via `@theme` in `input.css`, so the
bridge would just duplicate work.

---

## How djust Uses the CSS Framework

When `css_framework` is set, djust's `FormMixin` and built-in components automatically apply framework classes to rendered widgets.

### Form Field Rendering

```python
# In your LiveView
class ContactView(LiveView, FormMixin):
    form_class = ContactForm
```

With `css_framework='bootstrap5'`, fields render as:

```html
<div class="mb-3">
    <label for="id_email" class="form-label">Email</label>
    <input type="email" name="email" id="id_email" class="form-control" />
</div>
```

With `css_framework='tailwind'`, fields render as:

```html
<div class="mb-4">
    <label for="id_email" class="block text-sm font-medium text-gray-700">Email</label>
    <input type="email" name="email" id="id_email"
           class="mt-1 block w-full rounded-md border-gray-300 shadow-sm" />
</div>
```

With `css_framework=None`, fields render as unstyled plain HTML.

### Custom Adapter

Register a custom adapter to override framework classes:

```python
from djust.frameworks import BaseAdapter, register_adapter

class MyAdapter(BaseAdapter):
    # Static HTML only — never interpolate user data here
    required_marker = ' <span class="required">*</span>'
    help_text_class = "hint"

register_adapter('my_theme', MyAdapter())

# settings.py
LIVEVIEW_CONFIG = {'css_framework': 'my_theme'}
```

---

## Production Builds

### Minified CSS

```bash
# Tailwind
python manage.py djust_setup_css tailwind --minify

# Sass/Bootstrap
npx sass static/scss/main.scss static/css/main.min.css --style compressed
```

### CI/CD Integration

Add the CSS build step before `collectstatic` in your CI pipeline:

```yaml
# GitHub Actions example
- name: Install Node dependencies
  run: npm ci

- name: Build CSS
  run: python manage.py djust_setup_css tailwind --minify

- name: Collect static files
  run: python manage.py collectstatic --noinput
```

### Docker

```dockerfile
# In your Dockerfile — before collectstatic
RUN npm ci
RUN python manage.py djust_setup_css tailwind --minify
RUN python manage.py collectstatic --noinput
```

### Makefile Targets

```makefile
css:
	python manage.py djust_setup_css tailwind

css-watch:
	python manage.py djust_setup_css tailwind --watch

css-prod:
	python manage.py djust_setup_css tailwind --minify
```

---

## Demo utility classes

The djust demo project ships a tiny `utilities.css` you can crib
from. The most-reached-for class:

| Class | Equivalent |
|---|---|
| `.flex-between` | `display: flex; align-items: center; justify-content: space-between;` |

Use it on card headers or any flex container that needs a title on
the left and an action widget on the right — saves repeating the
same three flex declarations across templates. It's not a djust
runtime requirement, just a convention worth borrowing.

---

## Troubleshooting

**`STATICFILES_DIRS not configured` error**

Add to `settings.py`:
```python
STATICFILES_DIRS = [BASE_DIR / "static"]
```

**Tailwind CLI not found**

Install via npm (`npm install -D tailwindcss`) or download the standalone binary. See [Tailwind installation](https://tailwindcss.com/docs/installation).

**Classes not being applied**

Check that your `input.css` has `@source` directives (v4) or `content:` paths (v3) pointing to your template directories. Run `djust_setup_css tailwind` again to regenerate.

**Bootstrap JS not loading**

Bootstrap's JavaScript (dropdowns, modals) must be loaded separately. Add the `bootstrap.bundle.min.js` script tag or import it via npm.
