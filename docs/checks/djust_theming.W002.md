---
id: djust_theming.W002
---

<!-- doc: guide -->
### djust_theming.W002: css_prefix without a trailing hyphen

**Severity**: Warning

**What causes it**: 'css_prefix "<prefix>" does not end with "-". Component classes will render as ".<prefix>btn" instead of ".<prefix>-btn".'

**Fix**: Add a trailing `-`, e.g. `"dj-"` instead of `"dj"`.

---
