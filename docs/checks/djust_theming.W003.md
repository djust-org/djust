---
id: djust_theming.W003
---

<!-- doc: guide -->
### djust_theming.W003: Invalid or unknown selectable_presets

**Severity**: Warning

**What causes it**: `LIVEVIEW_CONFIG["theme"]["selectable_presets"]` is not a list of preset names, or names a preset that is not registered. Messages: 'LIVEVIEW_CONFIG["theme"]["selectable_presets"] is invalid: <reason>' and 'LIVEVIEW_CONFIG["theme"]["selectable_presets"] names unknown preset(s) <names>, which are ignored.' At render time such a value is logged and ignored, so this check is where the mistake is reported.

**Fix**: Use a list of registered preset names, such as `["default", "legal"]`. `python manage.py djust_theme list-presets` prints the registered names.
