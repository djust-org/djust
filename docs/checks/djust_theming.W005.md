---
id: djust_theming.W005
---

<!-- doc: guide -->
### djust_theming.W005: alert or toast close button without components.js

**Severity**: Warning

**What causes it**: a project template renders `{% theme_head include_js=False %}`, a project template renders `{% theme_toast %}` or `{% theme_alert dismissible=... %}`, and no project template loads `djust_theming/js/components.js`. The close button of the alert and the toast used to be an inline `onclick`, which a `script-src` without `'unsafe-inline'` refuses; it is now a `data-theme-dismiss` marker that `components.js` handles ([#4146](https://github.com/djust-org/djust/issues/4146)). `{% theme_head %}` loads that script unless it is given `include_js=False`, so on such a page the button does nothing.

**Fix**: Load the script on those pages, `<script src="{% static 'djust_theming/js/components.js' %}" defer></script>`, or drop `include_js=False`. The check reads project templates only; a page that loads the script some other way can silence it with `SILENCED_SYSTEM_CHECKS = ["djust_theming.W005"]`.
