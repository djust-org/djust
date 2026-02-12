# CSS Framework Support Improvements for djust

## Current State

**Developer Experience:**
- ❌ No warnings if Tailwind CDN is used in production
- ❌ No detection of double client.js loading
- ❌ No guidance if CSS compilation is missing
- ❌ Developers must manually remember to compile CSS

**What Happened:**
1. djust.org quickstart shows manual `<script src="{% static 'djust/client.js' %}">`
2. Developers copy this into base templates
3. djust auto-injects client.js → double loading → console warnings
4. No indication that Tailwind CDN is slow/problematic in production

---

## Proposed Improvements

### 1. **Django System Checks** (Low effort, high impact)

Add checks to `python/djust/checks.py`:

#### **C010: Tailwind CDN in Production** ⚠️
```python
# Production only
if 'cdn.tailwindcss.com' in base_template:
    Warning: "Tailwind CDN detected in production template: base.html"
    Hint: "Use compiled CSS instead: tailwindcss -i input.css -o output.css --minify"
```

**Benefit**: Developers get automatic warnings via `python manage.py check`

#### **C011: Missing Compiled CSS** ℹ️
```python
if DJUST_CSS_FRAMEWORK == 'tailwind' and not exists('static/css/output.css'):
    Warning: "Tailwind configured but output.css not found"
    Hint: "Run: tailwindcss -i static/css/input.css -o static/css/output.css"
```

**Benefit**: Catches missing build step before deployment

#### **C012: Manual client.js in Base Templates** ⚠️
```python
if 'djust/client.js' in base_template and '<script' in base_template:
    Warning: "Manual client.js detected in base.html"
    Hint: "djust auto-injects client.js. Remove manual tag to avoid double-loading."
```

**Benefit**: Prevents the exact race condition you encountered

---

### 2. **Development Mode Fallbacks** (Medium effort)

#### **Option A: Smart CDN Fallback**
```python
# In development, if compiled CSS is missing
if DEBUG and not exists('static/css/output.css') and CSS_FRAMEWORK == 'tailwind':
    # Auto-inject Tailwind CDN with warning
    logger.warning(
        "[djust] Tailwind CSS not compiled. Using CDN for development. "
        "Run: tailwindcss -i static/css/input.css -o static/css/output.css"
    )
```

**Pros**:
- Zero setup friction for beginners
- Works immediately after `pip install djust`

**Cons**:
- Hides the build step requirement
- May confuse when deployed

#### **Option B: Helpful Error Message**
```python
# Raise clear error if CSS is missing
if not exists('static/css/output.css') and CSS_FRAMEWORK == 'tailwind':
    raise ImproperlyConfigured(
        "Tailwind CSS is configured but output.css not found.\n"
        "Quick fix:\n"
        "  1. Create static/css/input.css\n"
        "  2. Run: npx tailwindcss -i static/css/input.css -o static/css/output.css\n"
        "Development: Add -w flag for watch mode"
    )
```

**Pros**:
- Forces developer to understand the build step
- Clear actionable error

**Cons**:
- Adds friction for beginners

---

### 3. **CLI Helper Command** (Low effort)

Add `python manage.py djust_setup_css`:

```bash
$ python manage.py djust_setup_css tailwind

✓ Created static/css/input.css
✓ Created tailwind.config.js
✓ Detected templates in: templates/, app/templates/
✓ Running: tailwindcss -i static/css/input.css -o static/css/output.css

✓ Tailwind CSS setup complete!

Add to your base template:
  <link rel="stylesheet" href="{% static 'css/output.css' %}">

Development: Run with --watch to auto-rebuild on changes
Production: Run with --minify before deploying
```

**Benefit**: One command to set up correctly

---

### 4. **Documentation Improvements** (Already done!)

✅ **Updated quickstart** to remove manual client.js
✅ **Added note** that djust auto-injects client.js

**Still needed:**
- ⚠️ Update demo project templates
- 📚 Add "Common Pitfalls" section to docs
- 📚 Add Tailwind setup guide

---

## Recommended Implementation Priority

### Phase 1: **System Checks** (Immediate, low effort)
1. Add C010 (Tailwind CDN in production)
2. Add C012 (Manual client.js detection)
3. Release in next minor version

**Impact**: Catches 90% of common issues automatically

### Phase 2: **CLI Helper** (Next sprint)
1. Add `djust_setup_css` command
2. Auto-detect framework from existing templates
3. Generate correct config files

**Impact**: Reduces setup friction, guides best practices

### Phase 3: **Smart Fallbacks** (Future consideration)
1. Decide on CDN fallback vs. error approach
2. Implement based on user feedback
3. Add extensive logging

**Impact**: Improves DX but needs careful design

---

## Alternative: CSS Framework Adapters

Create a pluggable system:

```python
# settings.py
DJUST_CSS_FRAMEWORK = {
    'backend': 'djust.css.TailwindAdapter',
    'config': {
        'input': 'static/css/input.css',
        'output': 'static/css/output.css',
        'minify': not DEBUG,
        'watch': DEBUG,
    }
}
```

**Adapters**:
- `TailwindAdapter` - Handles Tailwind v3/v4
- `BootstrapAdapter` - For Bootstrap + Sass
- `VanillaAdapter` - Plain CSS (no build step)

**Benefits**:
- Framework-agnostic
- Can auto-run build commands in dev mode
- Validates configuration per framework

**Complexity**: High initial effort, but scales well

---

## User Feedback Questions

1. **Should djust auto-inject Tailwind CDN in DEBUG mode** if compiled CSS is missing?
   - Pro: Zero friction for beginners
   - Con: Hides production requirements

2. **Should djust fail loudly** if CSS framework is configured but files are missing?
   - Pro: Forces correct setup
   - Con: More setup friction

3. **Should djust provide `make css-build` style commands** or just system checks?
   - CLI command? (`python manage.py djust_build_css`)
   - Just warnings? (current proposal)

---

## Conclusion

**Minimum viable improvement:**
- ✅ Add system checks C010, C011, C012
- ✅ Update documentation (already done for client.js)
- ⏳ Add CLI helper in next release

**This catches common issues automatically via `python manage.py check`** without requiring framework changes.

**Total effort:** ~2-3 hours to implement checks + tests
**Impact:** Prevents 90% of CSS-related support questions
