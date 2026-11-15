---
id: djust_theming.W005
---

<!-- doc: guide -->
### djust_theming.W005: strict-CSP alert or toast close button without components.js

**Severity**: Warning

**What causes it**: `LIVEVIEW_CONFIG["csp_strict"]` is on, a project template renders `{% theme_head include_js=False %}`, a project template renders `{% theme_toast %}` or `{% theme_alert dismissible=... %}`, and no project template loads `djust_theming/js/components.js`. By default the close button of the alert and the toast is an inline `onclick`, which needs no script (and the check stays quiet). A `script-src` without `'unsafe-inline'` refuses that, so in strict mode it is a `data-theme-dismiss` marker that `components.js` handles ([#4146](https://github.com/djust-org/djust/issues/4146)). `{% theme_head %}` loads that script unless it is given `include_js=False`, so on such a page the button does nothing.

**Fix**: Load the script on those pages, `<script src="{% static 'djust_theming/js/components.js' %}" defer></script>`, or drop `include_js=False`. The check reads project templates only; a page that loads the script some other way can silence it with `SILENCED_SYSTEM_CHECKS = ["djust_theming.W005"]`.
