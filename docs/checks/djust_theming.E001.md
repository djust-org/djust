---
id: djust_theming.E001
---

<!-- doc: guide -->
### djust_theming.E001: theme_context processor missing

**Severity**: Warning (an Error before 1.2.1; the id keeps its `E` prefix so existing `SILENCED_SYSTEM_CHECKS` entries still match)

The processor is optional. `{% theme_head %}`, `{% theme_switcher %}` and `{% theme_panel %}` work without it; it only supplies the `{{ theme_head }}`-style variables. It renders those chunks lazily, on first read, so adding it costs a request that never reads them almost nothing (before 1.3 it rendered them on every request that used a `RequestContext`, which is why this was once an Error). If your templates use only the tags, you can silence the check instead of adding the processor.

**What causes it**: "djust.theming.context_processors.theme_context is not in any TEMPLATES backend's context_processors list. Theme template variables (theme_head, theme_switcher, etc.) will not be available; the {% theme_head %} / {% theme_switcher %} tags still work."

**Fix**: Add `"djust.theming.context_processors.theme_context"` to `TEMPLATES[0]['OPTIONS']['context_processors']` if templates use the `{{ theme_head }}` variables, or add `"djust_theming.E001"` to `SILENCED_SYSTEM_CHECKS` if they use only the tags.

---
