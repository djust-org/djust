# CSS Framework Setup

djust works with any CSS framework (Tailwind, Bootstrap, UnoCSS, etc.). This guide covers the recommended setup for Tailwind CSS, the most popular choice.

## Quick Start

The fastest way to set up Tailwind CSS:

```bash
python manage.py djust_setup_css tailwind
```

This command:
- ✓ Creates `static/css/input.css` with Tailwind v4 syntax
- ✓ Creates `tailwind.config.js` (if missing)
- ✓ Auto-detects all template directories
- ✓ Finds Tailwind CLI (npx, node_modules, standalone, or global)
- ✓ Builds `static/css/output.css`
- ✓ Shows file size and next steps

## Development Workflow

### Watch Mode (Recommended)

For automatic CSS rebuilds during development:

```bash
python manage.py djust_setup_css tailwind --watch
```

This runs Tailwind in watch mode — CSS rebuilds automatically when templates change.

### Graceful Fallback

If you haven't compiled CSS yet, djust automatically falls back to Tailwind CDN in development mode (`DEBUG=True`):

```
[djust] Tailwind CSS configured but output.css not found.
        Using CDN as fallback in development.
        Run 'python manage.py djust_setup_css tailwind' to compile CSS.
```

This means you can start immediately without CSS setup, but djust guides you toward proper compilation for better performance.

## Production Deployment

### Compile CSS

Before deploying to production, compile CSS with minification:

```bash
python manage.py djust_setup_css tailwind --minify
```

This creates a production-optimized `static/css/output.css` (~30-40KB compressed).

### System Checks

djust automatically checks for common CSS issues via `python manage.py check`:

**djust.C010: Tailwind CDN in Production**
```
Warning: Tailwind CDN detected in production template: base.html
Hint: Compile CSS instead: python manage.py djust_setup_css tailwind --minify
```

**djust.C011: Missing Compiled CSS**
```
Warning: Tailwind CSS configured but output.css not found.
Hint: Run: python manage.py djust_setup_css tailwind --minify
```

**djust.C012: Manual client.js Loading**
```
Warning: Manual client.js detected in base.html:222
Hint: djust automatically injects client.js. Remove manual tag to avoid double-loading.
```

These checks prevent common deployment issues before they reach production.

## Manual Setup

If you prefer manual setup or need custom configuration:

### 1. Install Tailwind CLI

```bash
npm install -D tailwindcss
```

Or download the standalone binary:
```bash
curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-arm64
chmod +x tailwindcss-macos-arm64
```

### 2. Create `static/css/input.css`

**Tailwind v4 (recommended):**
```css
@import "tailwindcss";

@source "../../templates";
@source "../../app/templates";

/* Custom styles */
```

**Tailwind v3:**
```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* Custom styles */
```

### 3. Create `tailwind.config.js`

```javascript
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './app/templates/**/*.html',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

### 4. Build CSS

Development:
```bash
npx tailwindcss -i static/css/input.css -o static/css/output.css --watch
```

Production:
```bash
npx tailwindcss -i static/css/input.css -o static/css/output.css --minify
```

### 5. Add to Template

In your base template:

```html
{% load static %}
<!DOCTYPE html>
<html>
<head>
    <link rel="stylesheet" href="{% static 'css/output.css' %}">
    <!-- djust auto-injects client.js -->
</head>
<body>
    {% block content %}{% endblock %}
</body>
</html>
```

**Important:** Do NOT manually include `<script src="{% static 'djust/client.js' %}">` — djust automatically injects it. Manual loading causes duplicate initialization and race conditions.

## Bootstrap Setup

For Bootstrap + Sass:

### 1. Install Dependencies

```bash
npm install bootstrap sass
```

### 2. Create `static/css/input.scss`

```scss
@import "bootstrap/scss/bootstrap";

/* Custom styles */
```

### 3. Build CSS

Development:
```bash
npx sass static/css/input.scss static/css/output.css --watch
```

Production:
```bash
npx sass static/css/input.scss static/css/output.css --style compressed
```

## Other CSS Frameworks

djust works with any CSS framework:

- **UnoCSS**: Use `@unocss` CLI
- **Tachyons**: Pre-built CSS (no build step)
- **Bulma**: SCSS compilation like Bootstrap
- **Plain CSS**: No build step needed

The key principle: **Compile CSS before deployment, avoid CDN in production.**

## Common Pitfalls

### 1. Tailwind CDN in Production

**Problem:** Using `<script src="https://cdn.tailwindcss.com"></script>` in production.

**Issues:**
- Slow: ~300KB uncompressed, blocks rendering
- Console warnings about JIT compilation
- No tree-shaking (includes unused utilities)

**Solution:** Compile CSS with `djust_setup_css` or Tailwind CLI. System check `djust.C010` warns automatically.

### 2. Manual client.js Loading

**Problem:** Adding `<script src="{% static 'djust/client.js' %}">` to base template.

**Issues:**
- Duplicate initialization
- Race conditions between manual and auto-injected scripts
- Console warnings: "client.js already loaded, skipping duplicate initialization"

**Solution:** Remove manual script tag. djust auto-injects `client.js` for all LiveView pages. System check `djust.C012` detects this automatically.

### 3. Missing CSS Build Step

**Problem:** Deploying to production without compiling CSS.

**Issues:**
- Dev fallback (Tailwind CDN) doesn't work in production
- Unstyled pages in production

**Solution:** Run `djust_setup_css tailwind --minify` before deployment. System check `djust.C011` warns if `output.css` is missing.

### 4. Wrong Template Paths

**Problem:** Tailwind config doesn't match actual template locations.

**Issues:**
- Missing utility classes (purged during build)
- Larger CSS file (includes unused classes)

**Solution:** Use `djust_setup_css` to auto-detect template directories, or manually verify paths in `tailwind.config.js` match your project structure.

## CI/CD Integration

### GitHub Actions

```yaml
name: Build CSS
on: [push]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          npm install -D tailwindcss

      - name: Build CSS
        run: python manage.py djust_setup_css tailwind --minify

      - name: Run checks
        run: python manage.py check
```

### Docker

```dockerfile
FROM python:3.12-slim

# Install Node.js for Tailwind CLI
RUN apt-get update && apt-get install -y nodejs npm

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Install Tailwind and build CSS
RUN npm install -D tailwindcss
RUN python manage.py djust_setup_css tailwind --minify

# Run Django checks
RUN python manage.py check

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

## Advanced: Custom CSS Adapter

If you need framework-specific behavior, create a custom adapter:

```python
# myproject/css_adapter.py
from djust.css import CSSAdapter

class UnoAdapter(CSSAdapter):
    framework = "unocss"

    def create_config(self, template_dirs):
        return {
            "content": [f"{d}/**/*.html" for d in template_dirs],
            "theme": {},
        }

    def build_command(self, input_path, output_path, minify=False):
        cmd = ["npx", "unocss", input_path, "-o", output_path]
        if minify:
            cmd.append("--minify")
        return cmd
```

Then configure in `settings.py`:

```python
DJUST_CSS_ADAPTER = "myproject.css_adapter.UnoAdapter"
```

## Summary

**Best Practice:**
1. Use `python manage.py djust_setup_css tailwind` for zero-friction setup
2. Run with `--watch` during development for live CSS rebuilds
3. Run with `--minify` before production deployment
4. Let system checks (`python manage.py check`) catch issues automatically

**Don't:**
- ❌ Use Tailwind CDN in production
- ❌ Manually load `djust/client.js` in templates
- ❌ Deploy without compiled CSS

**Do:**
- ✅ Compile CSS before deployment
- ✅ Let djust auto-inject client.js
- ✅ Run `python manage.py check` before deployment
