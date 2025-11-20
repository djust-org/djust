# Marketing Site TODO

Project tracking document for the djust marketing site.

## 🚀 Current Status

**Phase 1: Documentation** ✅ Complete
- Created `docs/BEST_PRACTICES_AI.md` - AI-optimized best practices guide

**Phase 2: Site Conversion** ✅ Complete
- All 11 pages converted to djust framework
- Dark theme design matching original site
- Interactive features implemented (pricing toggle, FAQ search/filter, playground)

## 📋 Immediate Tasks

### 🔴 High Priority

- [x] **Fix duplicate client.js loading** ✅ COMPLETED
  - ✅ Removed explicit `<script src="{% static 'djust/client.js' %}">` from base.html
  - ✅ djust automatically inlines client.js content, so manual inclusion was causing duplicate declarations
  - ✅ Fixed "Identifier 'LiveViewWebSocket' has already been declared" error

### 🔴 High Priority (Framework Improvements)

- [x] **Add djust static files** ✅ COMPLETED
  - ✅ Configured whitenoise middleware for static file serving
  - ✅ Added STATIC_ROOT and ran collectstatic (129 files)
  - ✅ Both `/static/djust/client.js` and `/static/djust/debug-panel.css` now return 200
  - ✅ Enabled gzip compression for better performance

- [x] **Fix GitHub stars API call** ✅ COMPLETED
  - ✅ Implemented 5-minute caching to avoid rate limits
  - ✅ Made repository configurable via GITHUB_REPO environment variable
  - ✅ Added graceful fallback to 0 stars on error
  - ✅ Proper error handling for network failures

- [x] **Complete Playground functionality** ✅ COMPLETED
  - ✅ Monaco editor integration with Python and HTML syntax highlighting
  - ✅ Example selector (Counter, Search with Debounce, Todo List)
  - ✅ Live code validation with syntax error reporting
  - ✅ Debounced updates (500ms delay)
  - ✅ Copy code functionality
  - ✅ Event handler integration with djust LiveView

- [x] **Implement DjustTemplateBackend (Proper Django Integration)** ✅ COMPLETED
  - ✅ Created `djust.template_backend.DjustTemplateBackend` - proper Django template backend
  - ✅ Implements `BaseEngine` interface for full Django compatibility
  - ✅ Works with standard Django views (`TemplateView`, `render()`, etc.)
  - ✅ Supports template directories, `APP_DIRS`, and context processors
  - ✅ Uses Rust rendering (10-100x faster) WITHOUT client.js injection
  - ✅ Achieved ~70% size reduction (e.g., home: 91KB → 26KB) on static pages
  - ✅ Enables ANY Django project to use djust's Rust templates without LiveView
  - ✅ Marketing site now uses DjustTemplateBackend for all static pages
  - ✅ Interactive pages (playground, FAQ, pricing) still use LiveView with full features

- [ ] **Fix `{% url %}` template tag support**
  - Current workaround using reverse() in Python + hardcoded paths
  - Need to add {% url %} support to djust's Rust template engine
  - See detailed note in Known Issues section below

- [ ] **Add missing Django template filters to Rust engine**
  - Currently missing: `|split`, `|escapejs`, `|slice`
  - Current workaround: Pre-process data in Python views
  - Need to implement these filters in djust's Rust template engine
  - See detailed note in Known Issues section below

### 🟡 Medium Priority

- [ ] **Add form examples**
  - Newsletter signup form
  - Contact form
  - Demo form with validation

- [ ] **Optimize performance**
  - Enable Redis state backend for production
  - Add query optimization for FAQ filtering
  - Consider adding database caching

- [ ] **Add analytics**
  - Google Analytics or Plausible integration
  - Track page views, interactions
  - Monitor conversion funnel

- [ ] **SEO improvements**
  - Add meta descriptions to all pages
  - Add OpenGraph tags
  - Create sitemap.xml
  - Add robots.txt

### 🟢 Low Priority

- [ ] **Enhance mobile experience**
  - Test all pages on mobile devices
  - Optimize touch targets
  - Improve mobile menu UX

- [ ] **Add more examples**
  - Shopping cart example
  - Chat interface example
  - Dashboard with charts example
  - Form wizard example

- [ ] **Accessibility improvements**
  - Add ARIA labels
  - Keyboard navigation testing
  - Screen reader testing
  - Color contrast validation

- [ ] **Documentation links**
  - Update "Read Documentation" link in quickstart.py (line 139)
  - Link to actual GitHub repository
  - Add changelog

## 🐛 Known Issues

### Template Limitations

- **Rust template engine limitations**
  - No support for `{% verbatim %}` tag (using HTML entities instead)
  - No support for `{% url %}` tag (using reverse() in Python instead)
  - Missing Django template filters: `|split`, `|escapejs`, `|slice`
  - Custom filters not supported (using tuples/lists instead)

- **`{% url %}` Template Tag Fix Needed**
  - ⚠️ **Current Workaround**: Using `reverse()` in Python views + hardcoded paths in templates
  - **Problem**: djust's Rust template engine doesn't support Django's `{% url %}` tag
  - **Impact**: URL changes require updating both Python views and template strings
  - **Solution Needed**: Either:
    1. Add `{% url %}` support to djust's Rust template engine
    2. Create a custom template filter that works with Rust engine
    3. Implement URL resolution during template rendering phase
  - **Files Affected**:
    - `marketing/views/base.py` (line 23-45: using reverse() to resolve all nav URLs)
    - `marketing/templates/marketing/base.html` (using `{{ item.url }}` instead of `{% url %}`)
    - `marketing/views/quickstart.py` (line 131-145: hardcoded paths in next_steps)
  - **Priority**: Medium (functional but not ideal for maintainability)

- **Missing Template Filters Fix Needed**
  - ⚠️ **Current Workaround**: Pre-processing strings in Python views using `json.dumps()` for escaping
  - **Problem**: djust's Rust template engine doesn't support several common Django filters
  - **Impact**: Can't use template filters directly; must pre-process data in Python
  - **Missing Filters**:
    1. `|escapejs` - Escapes JavaScript strings (quotes, backslashes, newlines)
    2. `|split` - Splits string into list by delimiter
    3. `|slice` - Extracts substring using Python slice notation
  - **Files Affected & Workarounds**:
    - `marketing/views/examples.py` (lines 354-364):
      - **Original**: `{{ demo.python_code|escapejs }}` in template
      - **Workaround**: `json.dumps(code)[1:-1]` in Python to escape strings
    - `marketing/views/playground.py` (lines 229-230):
      - **Original**: `{{ code|safe|escapejs }}` in template
      - **Workaround**: `json.dumps(self.code)[1:-1]` in Python
    - `marketing/views/comparison.py` (lines 201, 218, 235):
      - **Original**: `{{ comparison.title|slice:"4:" }}` in template
      - **Workaround**: Added `framework_name` field to avoid slicing
    - `marketing/templates/features.html` (was line 239):
      - **Original**: `{{ pattern.code|split:'\n'|first }}` for multi-line code
      - **Workaround**: Removed splitting, used `<pre>` tag instead
  - **Solution Needed**: Implement these filters in `crates/djust_templates/src/filters.rs`
  - **Priority**: Medium (functional but verbose Python workarounds)

### Static Files & Scripts ✅ FIXED

- ✅ **djust static assets serving correctly**
  - Configured whitenoise middleware
  - Ran collectstatic (129 files collected)
  - Both `/static/djust/client.js` and `/static/djust/debug-panel.css` now return 200

- ✅ **Duplicate client.js loading fixed**
  - djust LiveView automatically inlines client.js content
  - Removed explicit `<script src>` tag that was causing duplicates
  - No more "Identifier 'LiveViewWebSocket' has already been declared" error

### API Integration ✅ FIXED

- ✅ **GitHub API with caching**
  - Implemented 5-minute cache to avoid rate limits
  - Made repository configurable via GITHUB_REPO environment variable
  - Graceful fallback to 0 stars on error

### Expected Browser Warnings (Not Issues)

The following browser console warnings are **expected in development** and can be ignored:

1. **Cross-Origin-Opener-Policy header ignored**
   - Occurs when using HTTP with `0.0.0.0` instead of `localhost`
   - Not an issue in development
   - Won't occur in production with HTTPS

2. **Tailwind CDN warning**
   - "cdn.tailwindcss.com should not be used in production"
   - Expected for development/demo site
   - For production, install Tailwind CSS properly with PostCSS

3. **"No LiveView container found for auto-mounting"**
   - Informational warning, not an error
   - Occurs on pages without LiveView interactive elements (static marketing pages)
   - Normal behavior for marketing pages that don't need real-time updates

## 💡 Future Enhancements

### Content

- [ ] Add blog section
- [ ] Add case studies
- [ ] Add video tutorials
- [ ] Add interactive demos
- [ ] Add community showcase

### Features

- [ ] Add search functionality across all pages
- [ ] Add "Try it now" live coding sandbox
- [ ] Add version switcher for documentation
- [ ] Add dark/light theme toggle
- [ ] Add language switcher (i18n)

### Technical

- [ ] Add automated tests
  - Unit tests for views
  - Integration tests for interactive features
  - E2E tests with Playwright
- [ ] Set up CI/CD pipeline
- [ ] Add monitoring (Sentry for errors)
- [ ] Add performance monitoring
- [ ] Docker containerization
- [ ] Kubernetes deployment manifests

### Marketing

- [ ] Add email signup flow
- [ ] Add testimonials section
- [ ] Add partner/sponsor logos
- [ ] Add comparison calculator
- [ ] Add ROI calculator

## 📝 Documentation Needed

- [ ] Deployment guide
- [ ] Contributing guide
- [ ] Code of conduct
- [ ] License file
- [ ] Security policy
- [ ] API documentation
- [ ] Architecture decision records (ADRs)

## 🔧 Technical Debt

### Code Quality

- [ ] Add type hints to all view methods
- [ ] Add docstrings to all classes and methods
- [ ] Create reusable components for common patterns
- [ ] Refactor duplicated code in templates
- [ ] Create template fragments for shared sections
- [ ] **Remove template filter workarounds once filters are implemented in Rust engine**
  - Replace `json.dumps()[1:-1]` pattern with `|escapejs` filter (examples.py, playground.py)
  - Replace `framework_name` field with `|slice` filter (comparison.py)
  - Consider re-adding `|split` usage if beneficial (features.html)

### Configuration

- [ ] Separate development/production settings
- [ ] Environment variable configuration
- [ ] Secrets management (vault or similar)
- [ ] Logging configuration
- [ ] Error handling standardization

### Testing

- [ ] Write unit tests for all views
- [ ] Write integration tests for LiveView interactions
- [ ] Add performance benchmarks
- [ ] Add load testing scenarios
- [ ] Add security testing

## 📊 Metrics to Track

- [ ] Page load times
- [ ] Time to interactive
- [ ] Conversion rates (signups, downloads)
- [ ] Bounce rates
- [ ] Search queries
- [ ] Popular pages
- [ ] User flows

## 🎯 Success Criteria

### Phase 1: Launch (Current)
- ✅ All 11 pages functional
- ✅ Dark theme design implemented
- ✅ Navigation working
- ✅ Interactive features (pricing, FAQ, playground)
- ⬜ Static files serving correctly
- ⬜ GitHub stars displaying

### Phase 2: Polish
- ⬜ All forms functional
- ⬜ Mobile responsive verified
- ⬜ SEO optimized
- ⬜ Analytics integrated
- ⬜ Performance optimized

### Phase 3: Scale
- ⬜ Redis backend configured
- ⬜ CDN integration
- ⬜ Monitoring in place
- ⬜ Automated tests passing
- ⬜ CI/CD pipeline running

## 🗓️ Timeline

### Week 1: Launch Prep
- Fix static files issue
- Complete playground functionality
- Add basic forms
- Deploy to staging

### Week 2: Polish
- SEO improvements
- Analytics integration
- Mobile optimization
- Performance tuning

### Week 3: Production
- Redis backend setup
- Production deployment
- Monitoring setup
- Documentation complete

## 📌 Notes

### Design Decisions

- **Why dark theme?** Matches developer tool aesthetic, reduces eye strain
- **Why Tailwind CDN?** Rapid prototyping, no build step needed for demo
- **Why Inter + JetBrains Mono?** Professional, readable, great code display
- **Why glass panels?** Modern aesthetic, depth without heaviness

### Technical Decisions

- **Why ASGI/Daphne?** Required for WebSocket support with djust
- **Why InMemory state backend?** Simpler for demo, Redis for production
- **Why URL reverse() vs {% url %}?** Rust template engine limitation
- **Why HTML entities vs {% verbatim %}?** Rust template engine limitation

### Lessons Learned

1. **Template compatibility**: Check which Django template tags/filters are supported by Rust engine
2. **URL resolution**: Do URL resolution in Python, not templates
3. **Dictionary access**: Use tuples/lists instead of custom filters
4. **Static files**: Configure STATICFILES_DIRS early in project setup
5. **Navigation pattern**: Resolve URLs once in base view, reuse across all pages
6. **Template filters workaround**: Use `json.dumps()[1:-1]` in Python to escape strings for JavaScript instead of `|escapejs` filter
7. **String manipulation**: Pre-process strings (splitting, slicing) in Python views rather than using template filters
8. **Filter detection**: Search templates early for unsupported filters using `grep -rn "{{.*|"` to find issues before runtime
9. **Client script injection**: djust automatically inlines client.js - **DO NOT** add explicit `<script src="{% static 'djust/client.js' %}">` tags as it will cause duplicate declarations
10. **Script loading pattern**: LiveView reads client.js from static files and injects it inline before `</body>` - this is intentional for immediate availability
11. **DjustTemplateBackend pattern**: For static pages, use Django's `TemplateView` with `djust.template_backend.DjustTemplateBackend` in TEMPLATES setting - gets Rust rendering speed (10-100x faster) WITHOUT client.js injection (~70% size reduction)
12. **Django template backend integration**: djust provides a proper `BaseEngine` implementation that works with ANY Django view, enabling Rust template rendering across entire Django projects without LiveView

---

Last Updated: 2025-11-19
