# Current Example Site Structure

**Last Updated**: November 14, 2024
**Location**: `examples/demo_project/`

This document provides a comprehensive inventory of the current example site structure, including all pages, URLs, templates, and views.

---

## Site Map

### Public Pages

```
/                    Homepage (marketing landing page)
/demos/              Demos index (grid of demo cards)
/demos/counter/      Counter demo
/demos/todo/         Todo list demo
/demos/chat/         Chat demo
/demos/search/       Search demo
/demos/data-table/   Data table demo
/demos/perf/         Performance test
/demos/python/       Python components demo
/demos/components/   Components-only demo
/demos/no-template/  No-template demo
/forms/              Forms demo index
/forms/auto/         Auto-rendered forms
/forms/auto/compare/ Framework comparison
/docs/               Documentation page
/tests/              Test suite index
/tests/cache/        @cache decorator test
/tests/draft-mode/   DraftModeMixin test
/tests/loading/      @loading attribute test
```

### Hidden Pages (Implemented but NOT in Index)

```
/demos/debounce/           ✅ DebounceSearchView (exists, not showcased)
/demos/throttle/           ✅ ThrottleScrollView (exists, not showcased)
/demos/cache/              ✅ CacheDemoView (exists, not showcased)
/demos/optimistic-counter/ ✅ OptimisticCounterView (exists, not showcased)
/demos/optimistic-todo/    ✅ OptimisticTodoView (exists, not showcased)
```

---

## Directory Structure

```
examples/demo_project/
├── demo_app/
│   ├── templates/
│   │   ├── base.html                   # Base template
│   │   ├── index.html                  # Homepage
│   │   ├── demos/
│   │   │   ├── index.html             # Demos index (NEEDS UPDATE)
│   │   │   ├── counter.html
│   │   │   ├── todo.html
│   │   │   ├── chat.html
│   │   │   ├── search.html
│   │   │   ├── data_table.html
│   │   │   ├── performance_test.html
│   │   │   ├── python_components.html
│   │   │   ├── components_only.html
│   │   │   ├── no_template.html
│   │   │   ├── debounce_demo.html     # ✅ Exists
│   │   │   ├── throttle_demo.html     # ✅ Exists
│   │   │   ├── cache_demo.html        # ✅ Exists
│   │   │   ├── optimistic_counter.html # ✅ Exists
│   │   │   └── optimistic_todo.html   # ✅ Exists
│   │   ├── forms/
│   │   │   ├── index.html
│   │   │   ├── auto_form.html
│   │   │   └── framework_comparison.html
│   │   ├── docs/
│   │   │   └── index.html             # Documentation page
│   │   └── tests/
│   │       ├── index.html             # Test suite index
│   │       ├── base_test.html
│   │       ├── cache_test.html
│   │       ├── draft_mode_test.html
│   │       └── loading_test.html
│   ├── views/
│   │   ├── __init__.py
│   │   ├── counter_demo.py
│   │   ├── todo_demo.py
│   │   ├── chat_demo.py
│   │   ├── search_demo.py
│   │   ├── data_table_demo.py
│   │   ├── performance_test.py
│   │   ├── python_components_demo.py
│   │   ├── components_only_demo.py
│   │   ├── no_template_demo.py
│   │   ├── debounce_demo.py           # ✅ Exists
│   │   ├── throttle_demo.py           # ✅ Exists
│   │   ├── cache_demo.py              # ✅ Exists
│   │   ├── optimistic_counter_demo.py # ✅ Exists
│   │   ├── optimistic_todo_demo.py    # ✅ Exists
│   │   ├── cache_test.py              # ✅ Test view
│   │   ├── draft_mode_test.py         # ✅ Test view
│   │   ├── loading_test.py            # ✅ Test view
│   │   └── test_index.py              # ✅ Test index view
│   ├── urls.py
│   └── ...
├── demo_project/
│   ├── settings.py
│   ├── urls.py
│   └── asgi.py
└── manage.py
```

---

## URL Routing

### Current Routes (`demo_app/urls.py`)

```python
urlpatterns = [
    # Homepage
    path('', HomeView.as_view(), name='home'),

    # Demos
    path('demos/', DemoIndexView.as_view(), name='demo_index'),
    path('demos/counter/', CounterView.as_view(), name='counter'),
    path('demos/todo/', TodoView.as_view(), name='todo'),
    path('demos/chat/', ChatView.as_view(), name='chat'),
    path('demos/search/', SearchView.as_view(), name='search'),
    path('demos/data-table/', DataTableView.as_view(), name='data_table'),
    path('demos/perf/', PerformanceTestView.as_view(), name='perf'),
    path('demos/python/', PythonComponentsView.as_view(), name='python_components'),
    path('demos/components/', ComponentsOnlyView.as_view(), name='components_only'),
    path('demos/no-template/', NoTemplateView.as_view(), name='no_template'),

    # Phase 5 Demos (NOT in demos index)
    path('demos/debounce/', DebounceSearchView.as_view(), name='debounce_demo'),
    path('demos/throttle/', ThrottleScrollView.as_view(), name='throttle_demo'),
    path('demos/cache/', CacheDemoView.as_view(), name='cache_demo'),
    path('demos/optimistic-counter/', OptimisticCounterView.as_view(), name='optimistic_counter'),
    path('demos/optimistic-todo/', OptimisticTodoView.as_view(), name='optimistic_todo'),

    # Forms
    path('forms/', FormIndexView.as_view(), name='form_index'),
    path('forms/auto/', AutoFormView.as_view(), name='auto_form'),
    path('forms/auto/compare/', FrameworkComparisonView.as_view(), name='framework_comparison'),

    # Documentation
    path('docs/', DocsView.as_view(), name='docs'),

    # Tests
    path('tests/', TestIndexView.as_view(), name='test_index'),
    path('tests/cache/', CacheTestView.as_view(), name='cache_test'),
    path('tests/draft-mode/', DraftModeTestView.as_view(), name='draft_mode_test'),
    path('tests/loading/', LoadingTestView.as_view(), name='loading_test'),
]
```

---

## Page Inventory

### 1. Homepage (`/`)

**Template**: `index.html`
**View**: `HomeView`

**Sections**:
- Hero section with live counter demo
- Embedded demos (search, todo, live data, navbar badge)
- Code comparison (djust vs React)
- Performance metrics
- Feature grid
- Use cases
- Comparison matrix
- Hosting section (djustlive.com)
- Getting started guide

**Status**: ✅ Complete, needs Phase 5 section

---

### 2. Demos Index (`/demos/`)

**Template**: `demos/index.html`
**View**: `DemoIndexView`

**Current Demos (9)**:
1. Counter - Basic LiveView counter
2. Todo List - Full CRUD operations
3. Chat - Real-time chat
4. React-like - Component composition
5. Data Table - Sorting, filtering, pagination
6. Performance Test - Stress testing
7. Python Components - Pure Python
8. Components-Only - No templates
9. No-Template - Inline rendering

**Missing from Index (7)**:
1. @debounce - Search autocomplete
2. @throttle - Scroll tracking
3. @cache - Cached search
4. @optimistic (Counter) - Instant updates
5. @optimistic (Todo) - Todo list
6. @loading - Button states
7. DraftModeMixin - Auto-save

**Status**: ⚠️ NEEDS UPDATE - Add Phase 5 demos

---

### 3. Documentation Page (`/docs/`)

**Template**: `docs/index.html`
**View**: `DocsView`

**Current Sections**:
- Installation
- Quick Start
- Core Concepts
- LiveView
- Components
- Event Handling
- Forms
- Templates
- State Management (minimal)
- Styling
- Deployment
- API Reference

**Missing**:
- Link to STATE_MANAGEMENT_API.md
- Link to STATE_MANAGEMENT_TUTORIAL.md
- Link to state management portal
- Phase 5 decorators documentation

**Status**: ⚠️ NEEDS UPDATE - Add state management links

---

### 4. Test Suite (`/tests/`)

**Template**: `tests/index.html`
**View**: `TestIndexView`

**Features**:
- Professional UI with stats grid
- Real-time test execution
- Pass/fail indicators
- Links to individual tests

**Current Tests (3)**:
1. @cache Decorator - ✅ Passing
2. DraftModeMixin - ✅ Passing
3. @loading Attribute - ✅ Passing

**Status**: ✅ Complete and working

---

### 5. Forms Demo (`/forms/`)

**Template**: `forms/index.html`
**View**: `FormIndexView`

**Features**:
- Auto-rendered forms
- Framework comparison (Bootstrap/Tailwind/Plain)
- Real-time validation

**Status**: ✅ Complete

---

## Phase 5 Demo Details

### Implemented but Hidden

#### 1. @debounce Demo (`/demos/debounce/`)

**Template**: `demos/debounce_demo.html`
**View**: `DebounceSearchView`
**Features**:
- Real-time search autocomplete
- 500ms debounce delay
- Loading indicator
- Results counter

**Status**: ✅ Implemented, not showcased

---

#### 2. @throttle Demo (`/demos/throttle/`)

**Template**: `demos/throttle_demo.html`
**View**: `ThrottleScrollView`
**Features**:
- Scroll position tracking
- 1000ms throttle
- Leading/trailing edge options
- Event counter

**Status**: ✅ Implemented, not showcased

---

#### 3. @cache Demo (`/demos/cache/`)

**Template**: `demos/cache_demo.html`
**View**: `CacheDemoView`
**Features**:
- Product search with caching
- 300s TTL
- Cache hit/miss indicators
- LRU eviction

**Status**: ✅ Implemented, not showcased

---

#### 4. @optimistic Counter (`/demos/optimistic-counter/`)

**Template**: `demos/optimistic_counter.html`
**View**: `OptimisticCounterView`
**Features**:
- Instant UI updates
- Automatic rollback on error
- Simulated network delay
- Error injection for testing

**Status**: ✅ Implemented, not showcased

---

#### 5. @optimistic Todo (`/demos/optimistic-todo/`)

**Template**: `demos/optimistic_todo.html`
**View**: `OptimisticTodoView`
**Features**:
- Optimistic CRUD operations
- Rollback on error
- Pending indicators
- Error recovery

**Status**: ✅ Implemented, not showcased

---

#### 6. @loading Test (`/tests/loading/`)

**Template**: `tests/loading_test.html`
**View**: `LoadingTestView`
**Features**:
- Button disable/enable
- Class additions
- Show/hide elements
- Automated test suite

**Status**: ✅ Implemented, in test suite

---

#### 7. DraftModeMixin Test (`/tests/draft-mode/`)

**Template**: `tests/draft_mode_test.html`
**View**: `DraftModeTestView`
**Features**:
- Auto-save to localStorage
- Draft/publish/discard flow
- Persistence across reloads
- Automated test suite

**Status**: ✅ Implemented, in test suite

---

## Template Analysis

### Homepage (`templates/index.html`)

**Size**: ~500 lines
**Framework**: Bootstrap 5
**Sections**: 12 major sections
**Embedded Demos**: 5 live demos

**Needs**:
- Phase 5 hero section (after component showcase)
- 87% code reduction stat
- Code comparison: manual JS vs decorators
- Link to STATE_MANAGEMENT_TUTORIAL.md

---

### Demos Index (`templates/demos/index.html`)

**Size**: ~300 lines
**Framework**: Bootstrap 5
**Current Cards**: 9 demos
**Layout**: CSS Grid (3 columns)

**Needs**:
- Add 7 Phase 5 demo cards
- Visual grouping by phase (badges)
- Update stats: "15+ demos" → "16 demos"
- Add "State Management" category

---

### Documentation Page (`templates/docs/index.html`)

**Size**: ~800 lines
**Framework**: Bootstrap 5
**Sidebar**: Collapsible sections
**Content**: Comprehensive guide

**Needs**:
- Add "State Management" subsection
- Link to STATE_MANAGEMENT_API.md
- Link to STATE_MANAGEMENT_TUTORIAL.md
- Link to state management portal
- Add to sidebar navigation

---

## View Analysis

### Phase 5 Views

All Phase 5 views are implemented and functional:

```python
# Debounce
class DebounceSearchView(LiveView):
    template_name = 'demos/debounce_demo.html'

    def mount(self, request):
        self.query = ""
        self.results = []

    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        # Search implementation
        pass

# Throttle
class ThrottleScrollView(LiveView):
    template_name = 'demos/throttle_demo.html'

    def mount(self, request):
        self.scroll_position = 0
        self.event_count = 0

    @throttle(wait=1.0, leading=True, trailing=True)
    def track_scroll(self, position: int = 0, **kwargs):
        # Scroll tracking implementation
        pass

# Cache
class CacheDemoView(LiveView):
    template_name = 'demos/cache_demo.html'

    def mount(self, request):
        self.query = ""
        self.results = []
        self.cache_hits = 0

    @debounce(wait=0.5)
    @cache(ttl=300, key_params=["query"])
    def search_products(self, query: str = "", **kwargs):
        # Product search with caching
        pass

# Optimistic Counter
class OptimisticCounterView(LiveView):
    template_name = 'demos/optimistic_counter.html'

    def mount(self, request):
        self.count = 0

    @optimistic
    def increment(self, **kwargs):
        self.count += 1

# Optimistic Todo
class OptimisticTodoView(LiveView):
    template_name = 'demos/optimistic_todo.html'

    def mount(self, request):
        self.todos = []

    @optimistic
    def add_todo(self, text: str = "", **kwargs):
        # Add todo with optimistic update
        pass

    @optimistic
    def delete_todo(self, todo_id: int = None, **kwargs):
        # Delete todo with optimistic update
        pass
```

**Status**: ✅ All views complete and working

---

## Gap Analysis

### High Priority Gaps

1. **Demos Index** - 7 Phase 5 demos missing
2. **Homepage** - No Phase 5 showcase section
3. **Documentation** - State management not linked

### Medium Priority Gaps

4. **@client_state Demo** - Not implemented
5. **Combined Decorators Demo** - Not implemented
6. **State Management Portal** - Not implemented

### Low Priority Gaps

7. **ROADMAP.md Links** - Not in footer/docs
8. **Competitive Comparison** - Not on homepage
9. **Interactive Playground** - Not implemented

---

## Statistics

### Current Site

- **Total Pages**: 25+
- **Demos Showcased**: 9
- **Demos Hidden**: 7
- **Test Pages**: 3
- **Documentation Pages**: 1

### After Phase 5 Update

- **Total Pages**: 27+ (add portal + client_state + combined)
- **Demos Showcased**: 16 (add 7 Phase 5 demos)
- **Demos Hidden**: 0
- **Test Pages**: 3
- **Documentation Pages**: 2 (add portal)

---

## Technical Notes

### Bootstrap 5 Components Used

- Cards (demo grid)
- Badges (phase indicators)
- Grid system (responsive layout)
- Navbar (navigation)
- Buttons (CTAs)
- Alerts (notifications)
- Code blocks (syntax highlighting)

### Custom CSS

- Gradient backgrounds
- Hover effects
- Transition animations
- Syntax highlighting
- Responsive breakpoints

### JavaScript Libraries

- djust client.js (7.1KB)
- Bootstrap 5 JS (bundle)
- Prism.js (syntax highlighting)

---

**Last Updated**: November 14, 2024
