---
id: djust_theming.W004
---

<!-- doc: guide -->
### djust_theming.W004: djust.components installed without component_catalog_css

**Severity**: Warning

**What causes it**: `djust.components` is in `INSTALLED_APPS` and `LIVEVIEW_CONFIG["theme"]` does not state `component_catalog_css`. Since djust 2.0, `{% theme_head %}` links the component catalog's stylesheet (`djust_components/components.css`, about 330 KB) only when that setting is `True` or the page passes `component_catalog=True`; before 2.0 it linked it whenever the app was installed ([#4003](https://github.com/djust-org/djust/issues/4003)).

**Fix**: Set `LIVEVIEW_CONFIG["theme"]["component_catalog_css"] = True` if pages render catalog tags such as `{% modal %}`, `{% data_table %}` or `{% app_shell %}`, or `False` if none does (or the pages that do link the file themselves).
