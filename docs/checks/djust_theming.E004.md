---
id: djust_theming.E004
---

<!-- doc: guide -->
### djust_theming.E004: Invalid css_prefix

**Severity**: Error

**What causes it**: 'css_prefix "<prefix>" contains invalid characters. Only letters, digits, and hyphens are allowed, and it must start with a letter.'

**Fix**: Use a prefix like `"djt-"` or `"myapp-"`.

Independently of this check, `get_theme_config()` raises `ImproperlyConfigured` for a `css_prefix` that holds anything but ASCII letters, digits, underscores and hyphens, because the prefix is printed into the theme's `<style>` blocks (#3779).

---
