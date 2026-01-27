# Building Automated Test Pages

This guide explains how to create automated test pages for djust features using the reusable base template system.

## Overview

Test pages in djust are special LiveView pages that automatically run tests on load and display results in a consistent, styled interface. They serve multiple purposes:

- **Developer verification**: Quick visual confirmation that features work correctly
- **Integration testing**: Real-world testing in the browser with actual WebSocket/HTTP
- **Documentation**: Living examples that demonstrate feature usage
- **Debugging**: Interactive test environment with console logging

## Base Template System

All test pages extend `tests/base_test.html`, which provides:

- **Purple gradient header** with status icons
- **Consistent card-based layout** with responsive grid
- **Pre-styled test result items** (green for pass, red for fail)
- **Stat card components** with hover effects
- **Code block styling** with dark theme
- **Custom CSS variables** for test colors
- **Extensible blocks** for customization

### Available Template Blocks

```html
{% extends "tests/base_test.html" %}

{% block title %}Your Test Name{% endblock %}

{% block status_icon %}
<!-- Custom icon (default: flask) -->
<i class="bi bi-check-circle-fill text-success"></i>
{% endblock %}

{% block page_title %}Your Test Title{% endblock %}

{% block page_description %}Description of what this tests{% endblock %}

{% block content %}
<!-- Your test UI and results -->
{% endblock %}

{% block extra_css %}
<!-- Custom CSS if needed -->
{% endblock %}

{% block extra_js %}
<script>
// Auto-run tests on load
window.djustDebug = true;  // Enable debug logging
setTimeout(() => runYourTests(), 2000);
</script>
{% endblock %}
```

## Step-by-Step: Creating a Test Page

### 1. Create the View

Create a new view in `examples/demo_project/demo_app/views/`:

```python
# my_feature_test.py
from djust import LiveView
from djust.decorators import my_decorator

class MyFeatureTestView(LiveView):
    """
    Automated test for @my_decorator functionality.

    This view runs tests automatically on page load to verify:
    - Feature X works correctly
    - Edge case Y is handled
    - Integration with Z functions
    """

    template_name = 'tests/my_feature_test.html'

    def mount(self, request):
        """Initialize test state"""
        self.test_results = []
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.all_tests_passed = False

        # Feature-specific state
        self.call_count = 0
        self.result = None

    def get_context_data(self, **kwargs):
        """Compute derived values for template"""
        context = super().get_context_data(**kwargs)

        # Add computed stats
        if self.call_count > 0:
            context['success_rate'] = round(
                (self.tests_passed / self.tests_run) * 100
            )
        else:
            context['success_rate'] = 0

        return context

    @my_decorator(params="here")
    def test_handler(self, value: str = "", **kwargs):
        """Handler to test - decorated with the feature being tested"""
        self.call_count += 1
        self.result = f"Processed: {value}"

        # Update test results
        self._update_test_results()

    def _update_test_results(self):
        """
        Analyze handler behavior and update test results.

        This method is called after each handler execution to check
        if the behavior matches expected outcomes.
        """
        self.test_results = []
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0

        # Test 1: Handler was called
        test = {
            'name': 'Handler Invocation',
            'expected': 'Handler should be called',
            'actual': f'Handler called {self.call_count} times',
            'passed': self.call_count > 0
        }
        self.test_results.append(test)
        self.tests_run += 1
        if test['passed']:
            self.tests_passed += 1
        else:
            self.tests_failed += 1

        # Test 2: Feature-specific behavior
        test = {
            'name': 'Feature Behavior',
            'expected': 'Should process input correctly',
            'actual': self.result or 'No result',
            'passed': self.result is not None
        }
        self.test_results.append(test)
        self.tests_run += 1
        if test['passed']:
            self.tests_passed += 1
        else:
            self.tests_failed += 1

        # Update overall status
        self.all_tests_passed = self.tests_failed == 0
```

### 2. Create the Template

Create a template in `examples/demo_project/demo_app/templates/tests/`:

```html
{% extends "tests/base_test.html" %}

{% block title %}@my_decorator Test{% endblock %}

{% block status_icon %}
{% if all_tests_passed %}
<i class="bi bi-check-circle-fill text-success"></i>
{% else %}
<i class="bi bi-x-circle-fill text-danger"></i>
{% endif %}
{% endblock %}

{% block page_title %}@my_decorator Automated Test{% endblock %}

{% block page_description %}
Automated testing for the @my_decorator feature
{% endblock %}

{% block content %}
<div data-djust-root>
    <!-- Test Status Summary -->
    <div class="alert {% if all_tests_passed %}alert-success{% else %}alert-danger{% endif %} mb-4">
        <h5 class="mb-2">
            {% if all_tests_passed %}
            ✅ All Tests Passed!
            {% else %}
            ❌ Tests Failed
            {% endif %}
        </h5>
        <p class="mb-0">
            {{ tests_run }} tests run, {{ tests_passed }} passed, {{ tests_failed }} failed
        </p>
    </div>

    <!-- Stats Cards -->
    <div class="row g-4 mb-4">
        <div class="col-md-3">
            <div class="stat-card p-3">
                <div class="stat-label text-muted small">Handler Calls</div>
                <div class="stat-value h3 mb-0 text-primary">{{ call_count }}</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="stat-card p-3">
                <div class="stat-label text-muted small">Success Rate</div>
                <div class="stat-value h3 mb-0 text-success">{{ success_rate }}%</div>
            </div>
        </div>
        <!-- Add more stat cards as needed -->
    </div>

    <!-- Test Results -->
    <h4 class="mb-3">Test Results</h4>
    <div class="list-group mb-4">
        {% for test in test_results %}
        <div class="list-group-item test-result-item {% if test.passed %}test-result-passed{% else %}test-result-failed{% endif %}">
            <div class="d-flex justify-content-between align-items-start">
                <div class="flex-grow-1">
                    <h6 class="mb-1">
                        {% if test.passed %}✅{% else %}❌{% endif %}
                        {{ test.name }}
                    </h6>
                    <p class="mb-1"><strong>Expected:</strong> {{ test.expected }}</p>
                    <p class="mb-0"><strong>Actual:</strong> {{ test.actual }}</p>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- Manual Test Section -->
    <h4 class="mb-3">Manual Testing</h4>
    <div class="card">
        <div class="card-body">
            <p class="text-muted">
                Use the controls below to manually test the feature:
            </p>
            <div class="mb-3">
                <input
                    type="text"
                    class="form-control"
                    placeholder="Enter test value..."
                    dj-input="test_handler"
                    value=""
                />
            </div>
            <div class="alert alert-info">
                <strong>Result:</strong> {{ result|default:"No result yet" }}
            </div>
        </div>
    </div>

    <!-- How It Works Section -->
    <div class="card mt-4">
        <div class="card-header bg-light">
            <h5 class="mb-0">How This Test Works</h5>
        </div>
        <div class="card-body">
            <ol>
                <li>Page loads and auto-runs tests after 2 seconds</li>
                <li>Test handler is called with various inputs</li>
                <li>Results are compared against expected outcomes</li>
                <li>Pass/fail status is displayed for each test</li>
                <li>Console logs show detailed decorator behavior</li>
            </ol>

            <h6 class="mt-3">Console Output</h6>
            <p class="mb-0 text-muted">
                Open your browser console (F12) to see detailed logs of:
            </p>
            <ul class="text-muted">
                <li><code>[LiveView]</code> - WebSocket connection and events</li>
                <li><code>[LiveView:my_decorator]</code> - Decorator-specific logs</li>
                <li>Handler invocations and parameter passing</li>
            </ul>
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
// Enable debug logging
window.djustDebug = true;

// Auto-run tests after page loads and LiveView connects
setTimeout(function() {
    console.log('[Test] Starting automated tests...');
    runMyFeatureTests();
}, 2000);

function runMyFeatureTests() {
    // Simulate user interactions to test the feature

    // Test 1: Basic invocation
    setTimeout(() => {
        const input = document.querySelector('input');
        input.value = 'test value';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }, 100);

    // Test 2: Edge case
    setTimeout(() => {
        const input = document.querySelector('input');
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }, 600);

    // Add more test scenarios as needed
}
</script>
{% endblock %}
```

### 3. Register the View

Add imports to `views/__init__.py`:

```python
# Import test views
from .my_feature_test import MyFeatureTestView

__all__ = [
    # ... existing exports
    'MyFeatureTestView',
]
```

### 4. Add URL Route

Add route to `urls.py`:

```python
urlpatterns = [
    # ... existing routes

    # Test Pages
    path('tests/', views.TestIndexView.as_view(), name='tests-index'),
    path('tests/my-feature/', views.MyFeatureTestView.as_view(), name='tests-my-feature'),
]
```

### 5. Add to Test Index

The test will automatically appear in the test index (see next section).

## Test Writing Best Practices

### 1. Use Descriptive Test Names

```python
test = {
    'name': 'Cache Hit After First Call',  # Clear and specific
    'expected': 'Second call should return cached result',
    'actual': f'Server calls: {self.server_calls}',
    'passed': self.server_calls == 1
}
```

### 2. Test Multiple Scenarios

```python
def _update_test_results(self):
    """Test multiple scenarios comprehensively"""

    # Happy path
    self._test_basic_functionality()

    # Edge cases
    self._test_empty_input()
    self._test_special_characters()

    # Error conditions
    self._test_invalid_input()

    # Integration
    self._test_with_other_decorators()
```

### 3. Provide Clear Expected vs Actual

```python
test = {
    'name': 'Debounce Delay',
    'expected': '500ms wait before server call',
    'actual': f'Waited {elapsed_ms}ms',
    'passed': 450 <= elapsed_ms <= 550  # Allow 50ms tolerance
}
```

### 4. Enable Debug Logging

Always set `window.djustDebug = true` in test pages to help developers debug issues.

### 5. Include Manual Test Section

Allow developers to manually interact with the feature after automated tests complete.

### 6. Document Console Output

Tell developers what to look for in the console:

```html
<p>Expected console output:</p>
<ul>
    <li><code>[LiveView:cache] Checking cache for key: search-laptop</code></li>
    <li><code>[LiveView:cache] Cache miss - calling server</code></li>
    <li><code>[LiveView:cache] Cached result for key: search-laptop</code></li>
</ul>
```

## Test Index Page

All test pages are automatically listed at `/tests/` with:

- Test name and description
- Link to run the test
- Feature being tested
- Phase/version information

See `views/test_index.py` for implementation details.

## Example Test Pages

### Cache Decorator Test
- **Location**: `views/cache_test.py`
- **URL**: `/demos/cache-test/`
- **Tests**: Cache hits/misses, TTL expiration, key generation

### State Management Test
- **Location**: `views/state_test.py` (example)
- **URL**: `/tests/state/`
- **Tests**: State persistence, updates, synchronization

## Troubleshooting

### Tests Not Running

**Problem**: Automated tests don't execute on page load.

**Solution**:
1. Check `window.djustDebug = true` is set
2. Verify setTimeout delay (2000ms) is sufficient for WebSocket connection
3. Check browser console for JavaScript errors
4. Ensure `data-djust-root` attribute is present

### Test Results Not Updating

**Problem**: Test results don't update in the UI after handler execution.

**Solution**:
1. Verify `_update_test_results()` is called after state changes
2. Check that test result variables are in `get_context_data()`
3. Ensure template loops over `test_results` correctly

### Styling Issues

**Problem**: Test page doesn't match expected styling.

**Solution**:
1. Verify `{% extends "tests/base_test.html" %}` is first line
2. Check block names match exactly (case-sensitive)
3. Ensure Bootstrap 5 classes are used correctly
4. Inspect browser dev tools for CSS conflicts

## Future Improvements

Potential enhancements to the test page system:

1. **Test runner framework**: Run multiple test pages in sequence
2. **Test reporting**: Export test results to JSON/HTML
3. **Performance metrics**: Track execution time, memory usage
4. **Screenshot capture**: Automated visual regression testing
5. **CI integration**: Run test pages in headless browser
6. **Test categories**: Group tests by feature/phase/complexity
7. **Test coverage**: Track which features have test pages

## Related Documentation

- [CONTRIBUTING.md](../CONTRIBUTING.md) - Development workflow
- [STATE_MANAGEMENT_API.md](STATE_MANAGEMENT_API.md) - Decorator documentation
- [Component testing](../COMPONENT_BEST_PRACTICES.md) - Component-specific testing

## Questions?

If you have questions about creating test pages, please:

1. Check existing test pages for examples
2. Review the base template source code
3. Open an issue on GitHub
4. Ask in the community Discord

Happy testing! 🧪
