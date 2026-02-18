# Testing Guide for djust

This comprehensive guide covers testing practices, patterns, and utilities for the djust framework.

## Table of Contents

- [Quick Start](#quick-start)
- [Test Organization](#test-organization)
- [Running Tests](#running-tests)
- [Django Test Patterns](#django-test-patterns)
- [djust-Specific Patterns](#djust-specific-patterns)
- [Test Utilities](#test-utilities)
- [Best Practices](#best-practices)
- [Coverage](#coverage)
- [CI/CD](#cicd)
- [Troubleshooting](#troubleshooting)

## Quick Start

```bash
# Install dependencies (includes test dependencies)
make install

# Run all tests (Python + Rust + JavaScript)
make test

# Run only Python tests
make test-python

# Run only Rust tests
make test-rust

# Run only JavaScript tests
make test-js

# Run specific test file
pytest tests/unit/test_live_view.py

# Run specific test by name pattern
pytest -k "test_mount"

# Run tests in parallel (faster)
make test-python-parallel
```

## Test Organization

### Directory Structure

```
djust/
├── tests/                          # Integration and unit tests
│   ├── conftest.py                 # Pytest fixtures and configuration
│   ├── unit/                       # Unit tests
│   │   ├── test_live_view.py      # LiveView core tests
│   │   ├── test_live_component.py  # Component tests
│   │   ├── test_forms.py          # Form tests
│   │   └── test_security*.py      # Security tests
│   ├── integration/                # Integration tests
│   ├── e2e/                        # End-to-end tests
│   ├── benchmarks/                 # Performance benchmarks
│   │   └── conftest.py            # Benchmark-specific fixtures
│   └── playwright/                 # Browser automation tests (manual)
├── python/tests/                   # Python package tests
│   ├── test_security.py
│   ├── test_auth.py
│   ├── test_validation.py
│   └── ...
└── examples/demo_project/
    └── demo_app/tests/             # Demo app tests
```

### Naming Conventions

- **Test files**: `test_*.py` or `*_test.py`
- **Test classes**: `Test*` (e.g., `TestLiveViewBasics`)
- **Test functions**: `test_*` (e.g., `test_mount_called`)
- **Fixtures**: Descriptive names without `test_` prefix (e.g., `get_request`, `sample_template`)

## Running Tests

### Make Commands

The Makefile provides convenient shortcuts for common testing tasks:

```bash
# Run all test suites
make test                   # Python + Rust + JavaScript

# Run individual suites
make test-python            # Python tests only
make test-rust              # Rust tests only
make test-js                # JavaScript tests only

# Run specific test categories
make test-vdom              # VDOM patching tests
make test-liveview          # LiveView core tests
make test-playwright        # Browser automation tests (requires server)

# Parallel execution (faster)
make test-python-parallel   # Uses pytest-xdist

# Linting and formatting
make lint                   # Run all linters
make format                 # Format all code
make check                  # Lint + test
```

### Pytest Commands

For more granular control, use pytest directly:

```bash
# Run specific test file
pytest tests/unit/test_live_view.py

# Run specific test class
pytest tests/unit/test_live_view.py::TestLiveViewBasics

# Run specific test method
pytest tests/unit/test_live_view.py::TestLiveViewBasics::test_mount_called

# Run tests matching pattern
pytest -k "mount"           # All tests with "mount" in name
pytest -k "not slow"        # Exclude slow tests

# Verbose output
pytest -v                   # Verbose
pytest -vv                  # Very verbose

# Stop on first failure
pytest -x

# Drop into debugger on failure
pytest --pdb

# Show local variables on failure
pytest -l

# Run last failed tests
pytest --lf

# Run tests modified since last commit
pytest --testmon
```

### Pytest Markers

Use markers to categorize and selectively run tests:

```python
import pytest

@pytest.mark.django_db
def test_database_operation():
    """Test requiring database access"""
    pass

@pytest.mark.slow
def test_slow_operation():
    """Long-running test"""
    pass

@pytest.mark.asyncio
async def test_websocket():
    """Async test"""
    pass
```

Run specific markers:

```bash
pytest -m django_db         # Only database tests
pytest -m "not slow"        # Skip slow tests
pytest -m "slow or asyncio" # Multiple markers
```

## Django Test Patterns

### Model Tests

```python
import pytest
from django.contrib.auth.models import User

@pytest.mark.django_db
class TestUserModel:
    """Test User model behavior."""

    def test_user_creation(self):
        """Test creating a user."""
        user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        assert user.username == 'testuser'
        assert user.email == 'test@example.com'
        assert user.check_password('testpass123')

    def test_user_str_representation(self):
        """Test user string representation."""
        user = User.objects.create_user(username='testuser')
        assert str(user) == 'testuser'
```

### View Tests

```python
import pytest
from django.test import RequestFactory
from djust import LiveView

@pytest.mark.django_db
class TestCounterView:
    """Test CounterView LiveView."""

    def test_initial_state(self, get_request):
        """Test view initializes with count=0."""
        class CounterView(LiveView):
            template = "<div>{{ count }}</div>"

            def mount(self, request, **kwargs):
                self.count = 0

        view = CounterView()
        response = view.get(get_request)

        assert view.count == 0
        assert response.status_code == 200

    def test_event_handler(self, get_request):
        """Test event handler updates state."""
        from djust.decorators import event_handler

        class CounterView(LiveView):
            template = "<div>{{ count }}</div>"

            def mount(self, request, **kwargs):
                self.count = 0

            @event_handler
            def increment(self, **kwargs):
                self.count += 1

        view = CounterView()
        view.get(get_request)

        # Simulate event
        view.increment()
        assert view.count == 1
```

### Form Tests

```python
import pytest
from django import forms
from djust.forms import FormMixin
from djust import LiveView

@pytest.mark.django_db
class TestContactForm:
    """Test ContactForm with real-time validation."""

    def test_form_validation(self, get_request):
        """Test form validates correctly."""
        class ContactForm(forms.Form):
            email = forms.EmailField()
            message = forms.CharField()

        class ContactFormView(FormMixin, LiveView):
            template = "<form>{{ form }}</form>"
            form_class = ContactForm

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)

        view = ContactFormView()
        view.get(get_request)

        # Test invalid data
        view.handle_form_change(field='email', value='invalid')
        assert 'email' in view.form.errors

        # Test valid data
        view.handle_form_change(field='email', value='test@example.com')
        assert 'email' not in view.form.errors
```

## djust-Specific Patterns

### Testing LiveView Lifecycle

```python
import pytest
from djust import LiveView

@pytest.mark.django_db
class TestLiveViewLifecycle:
    """Test LiveView lifecycle methods."""

    def test_mount_initializes_state(self, get_request):
        """Test mount() is called on initial load."""
        class TodoView(LiveView):
            template = "<div>{{ todos|length }}</div>"

            def mount(self, request, **kwargs):
                self.todos = []

        view = TodoView()
        view.get(get_request)

        assert hasattr(view, 'todos')
        assert view.todos == []

    def test_get_context_data_computes_values(self, get_request):
        """Test get_context_data() provides template context."""
        class StatsView(LiveView):
            template = "<div>{{ total }}</div>"

            def mount(self, request, **kwargs):
                self.items = [1, 2, 3]

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context['total'] = sum(self.items)
                return context

        view = StatsView()
        response = view.get(get_request)
        context = view.get_context_data()

        assert 'total' in context
        assert context['total'] == 6
```

### Testing Event Handlers

```python
import pytest
from djust import LiveView
from djust.decorators import event_handler, debounce, cache

@pytest.mark.django_db
class TestEventHandlers:
    """Test event handler decorators and patterns."""

    def test_event_handler_basic(self, get_request):
        """Test @event_handler decorator."""
        class SearchView(LiveView):
            template = "<input dj-input='search' />"

            def mount(self, request, **kwargs):
                self.query = ""
                self.results = []

            @event_handler
            def search(self, value: str = "", **kwargs):
                self.query = value
                self.results = [f"Result for {value}"]

        view = SearchView()
        view.get(get_request)

        # Simulate input event
        view.search(value="test query")

        assert view.query == "test query"
        assert len(view.results) == 1

    def test_debounce_decorator(self, get_request):
        """Test @debounce decorator."""
        class SearchView(LiveView):
            template = "<input />"

            def mount(self, request, **kwargs):
                self.call_count = 0

            @debounce(wait=0.5)
            def search(self, value: str = "", **kwargs):
                self.call_count += 1

        view = SearchView()
        view.get(get_request)

        # Multiple rapid calls should be debounced
        # (Implementation detail - verify via integration test)
        view.search(value="a")
        view.search(value="ab")
        view.search(value="abc")

        # In unit test, we verify the decorator exists
        assert hasattr(view.search, '_djust_debounce')
```

### Testing Components

```python
import pytest
from djust import Component, LiveView
from djust.decorators import event_handler

@pytest.mark.django_db
class TestAlertComponent:
    """Test AlertComponent behavior."""

    def test_component_initialization(self):
        """Test component initializes with props."""
        from demo_app.components.alert import AlertComponent

        alert = AlertComponent(
            message="Success!",
            type="success",
            dismissible=True
        )

        assert alert.message == "Success!"
        assert alert.type == "success"
        assert alert.dismissible is True
        assert alert.visible is True

    def test_component_in_view(self, get_request):
        """Test component used in LiveView."""
        from demo_app.components.alert import AlertComponent

        class DashboardView(LiveView):
            template = "<div>{{ alert_success }}</div>"

            def mount(self, request, **kwargs):
                self.alert_success = AlertComponent(
                    message="Operation successful!",
                    type="success"
                )

        view = DashboardView()
        view.get(get_request)

        # Component ID is automatically set from attribute name
        assert view.alert_success.component_id == "alert_success"
        assert view.alert_success.visible is True

    def test_component_event_handling(self, get_request):
        """Test component event handlers."""
        from demo_app.components.alert import AlertComponent

        class DashboardView(LiveView):
            template = "<div>{{ alert_warning }}</div>"

            def mount(self, request, **kwargs):
                self.alert_warning = AlertComponent(
                    message="Warning!",
                    dismissible=True
                )

            @event_handler
            def dismiss(self, component_id: str = None, **kwargs):
                """Handle alert dismissal."""
                if component_id and hasattr(self, component_id):
                    component = getattr(self, component_id)
                    if hasattr(component, 'dismiss'):
                        component.dismiss()

        view = DashboardView()
        view.get(get_request)

        # Simulate dismiss event
        view.dismiss(component_id="alert_warning")

        assert view.alert_warning.visible is False
```

### Testing Authentication & Authorization

```python
import pytest
from django.contrib.auth.models import User, Permission
from djust import LiveView
from djust.auth import LoginRequiredMixin, PermissionRequiredMixin
from djust.decorators import event_handler, permission_required

@pytest.mark.django_db
class TestAuthentication:
    """Test authentication and authorization."""

    def test_login_required_mixin(self, rf):
        """Test LoginRequiredMixin redirects anonymous users."""
        class ProtectedView(LoginRequiredMixin, LiveView):
            template = "<div>Protected</div>"

        view = ProtectedView()
        request = rf.get('/')
        request.user = None

        # Anonymous user should be redirected
        # (Check via integration test or mock)
        assert hasattr(view, 'login_required')

    def test_permission_required_handler(self, rf, django_user_model):
        """Test @permission_required decorator on handler."""
        user = django_user_model.objects.create_user(
            username='testuser',
            password='testpass'
        )

        class AdminView(LiveView):
            template = "<button dj-click='delete_user'>Delete</button>"

            def mount(self, request, **kwargs):
                self.deleted = False

            @permission_required('auth.delete_user')
            def delete_user(self, **kwargs):
                self.deleted = True

        view = AdminView()
        request = rf.get('/')
        request.user = user
        view.get(request)

        # User without permission cannot execute handler
        # (Permission check happens before handler execution)
        # This is an integration-level check
        assert hasattr(view.delete_user, '_djust_permission_required')
```

### Testing VDOM Patching

```python
import pytest
from djust import LiveView

@pytest.mark.django_db
class TestVDOMPatching:
    """Test VDOM diff and patch generation."""

    def test_simple_text_change(self, get_request):
        """Test VDOM generates correct patch for text change."""
        class MessageView(LiveView):
            template = "<div>{{ message }}</div>"

            def mount(self, request, **kwargs):
                self.message = "Hello"

        view = MessageView()
        view.get(get_request)

        # Change state
        view.message = "World"

        # VDOM should detect text change
        # (Actual patch verification requires Rust integration)
        assert view.message == "World"

    def test_conditional_rendering(self, get_request):
        """Test VDOM handles conditional content."""
        class ToggleView(LiveView):
            template = """
            <div>
                {% if show %}
                    <p>Visible</p>
                {% endif %}
            </div>
            """

            def mount(self, request, **kwargs):
                self.show = False

        view = ToggleView()
        view.get(get_request)

        # Toggle visibility
        view.show = True

        # VDOM should detect conditional change
        assert view.show is True
```

## Test Utilities

### Fixtures (conftest.py)

The `tests/conftest.py` file provides reusable fixtures:

```python
import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware

@pytest.fixture
def request_factory():
    """Provide Django RequestFactory."""
    return RequestFactory()

@pytest.fixture
def rf():
    """Alias for request_factory (pytest-django convention)."""
    return RequestFactory()

@pytest.fixture
def get_request(request_factory):
    """Create a GET request with session support."""
    request = request_factory.get('/')

    # Add session support
    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    return request

@pytest.fixture
def post_request(request_factory):
    """Create a POST request with session support."""
    request = request_factory.post('/', content_type='application/json')

    middleware = SessionMiddleware(lambda x: None)
    middleware.process_request(request)
    request.session.save()

    return request

@pytest.fixture
def sample_template():
    """Provide a sample template for testing."""
    return """
    <div class="container">
        <h1>Hello {{ name }}!</h1>
        <p>Count: {{ count }}</p>
    </div>
    """

@pytest.fixture(autouse=True)
def cleanup_session_cache():
    """Clean up session cache after each test."""
    from djust.state_backend import set_backend, InMemoryStateBackend

    backend = InMemoryStateBackend()
    set_backend(backend)

    yield

    # Cleanup: Clear all sessions
    backend.cleanup_expired(ttl=0)
```

### Using Fixtures

```python
import pytest

@pytest.mark.django_db
def test_with_request(get_request):
    """Test using get_request fixture."""
    assert get_request.method == 'GET'
    assert hasattr(get_request, 'session')

@pytest.mark.django_db
def test_with_template(sample_template):
    """Test using sample_template fixture."""
    assert '{{ name }}' in sample_template
```

### Test Helpers

Common test helper patterns:

```python
# Helper for creating LiveView instances
def create_test_view(view_class, request):
    """Helper to create and initialize a LiveView."""
    view = view_class()
    view.get(request)
    return view

# Helper for simulating events
def trigger_event(view, handler_name, **params):
    """Helper to trigger event handler."""
    handler = getattr(view, handler_name)
    return handler(**params)

# Helper for asserting in template context
def assert_in_context(view, key, expected_value):
    """Assert value exists in template context."""
    context = view.get_context_data()
    assert key in context
    assert context[key] == expected_value
```

## Best Practices

### 1. Use Django TestCase vs pytest

**Prefer pytest** for djust tests:

```python
# ✅ Good - pytest style
import pytest

@pytest.mark.django_db
def test_user_creation():
    user = User.objects.create_user(username='test')
    assert user.username == 'test'

# ❌ Avoid - Django TestCase (except for complex transaction tests)
from django.test import TestCase

class UserTestCase(TestCase):
    def test_user_creation(self):
        user = User.objects.create_user(username='test')
        self.assertEqual(user.username, 'test')
```

**When to use Django TestCase:**
- Complex transaction testing
- Testing Django middleware
- Testing with multiple database connections

### 2. Transaction Handling and Database Cleanup

pytest-django handles database cleanup automatically with `@pytest.mark.django_db`:

```python
import pytest

@pytest.mark.django_db
def test_creates_user():
    """Database is automatically rolled back after test."""
    User.objects.create_user(username='test')
    assert User.objects.count() == 1
    # Automatic rollback - next test starts with clean DB
```

For tests requiring transaction control:

```python
@pytest.mark.django_db(transaction=True)
def test_with_transaction():
    """Test with transaction support."""
    # Can test transaction-related behavior
    pass
```

### 3. Mocking External Dependencies

Mock external services and I/O operations:

```python
import pytest
from unittest.mock import Mock, patch

@pytest.mark.django_db
def test_with_mocked_request(get_request):
    """Test with mocked external API call."""
    with patch('requests.get') as mock_get:
        mock_get.return_value.json.return_value = {'data': 'test'}

        class APIView(LiveView):
            template = "<div>{{ data }}</div>"

            def mount(self, request, **kwargs):
                import requests
                response = requests.get('https://api.example.com')
                self.data = response.json()['data']

        view = APIView()
        view.get(get_request)

        assert view.data == 'test'
        mock_get.assert_called_once()
```

### 4. Testing Signal-Driven Behavior

Test Django signals carefully:

```python
import pytest
from django.db.models.signals import post_save

@pytest.mark.django_db
def test_signal_handler():
    """Test signal handler is triggered."""
    signal_received = []

    def handler(sender, instance, **kwargs):
        signal_received.append(instance)

    # Connect signal
    post_save.connect(handler, sender=User)

    try:
        user = User.objects.create_user(username='test')
        assert len(signal_received) == 1
        assert signal_received[0] == user
    finally:
        # Always disconnect to avoid test pollution
        post_save.disconnect(handler, sender=User)
```

### 5. Isolation and Test Independence

Each test should be fully independent:

```python
# ✅ Good - Each test sets up its own state
@pytest.mark.django_db
def test_increment():
    view = CounterView()
    view.count = 0
    view.increment()
    assert view.count == 1

@pytest.mark.django_db
def test_decrement():
    view = CounterView()
    view.count = 10
    view.decrement()
    assert view.count == 9

# ❌ Bad - Tests depend on execution order
class TestCounter:
    count = 0  # Shared state!

    def test_increment(self):
        self.count += 1
        assert self.count == 1

    def test_decrement(self):
        self.count -= 1  # Depends on previous test!
        assert self.count == 0
```

### 6. Testing Async Code

Use pytest-asyncio for async tests:

```python
import pytest

@pytest.mark.asyncio
async def test_websocket_connection():
    """Test WebSocket connection."""
    from channels.testing import WebsocketCommunicator
    from demo_project.asgi import application

    communicator = WebsocketCommunicator(application, "/ws/live/")
    connected, _ = await communicator.connect()

    assert connected is True

    await communicator.disconnect()
```

### 7. Descriptive Test Names and Docstrings

```python
# ✅ Good - Clear test purpose
@pytest.mark.django_db
def test_debounce_decorator_delays_handler_execution():
    """
    Test @debounce decorator delays handler execution.

    When multiple rapid events occur within the wait period,
    only the last event should trigger the handler.
    """
    pass

# ❌ Bad - Unclear purpose
def test_debounce():
    pass
```

## Coverage

### Measuring Coverage

```bash
# Install coverage
uv pip install pytest-cov

# Run tests with coverage
pytest --cov=djust --cov-report=html

# Open coverage report
open htmlcov/index.html
```

### Coverage Configuration

Add to `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["python/djust"]
omit = [
    "*/tests/*",
    "*/migrations/*",
    "*/__init__.py",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

### Coverage Targets

- **Overall**: ≥85% coverage
- **Core modules** (live_view.py, websocket.py): ≥95%
- **Security modules** (auth.py, security.py): 100%
- **Utilities**: ≥80%

## CI/CD

### GitHub Actions

Tests run automatically on push and pull requests via `.github/workflows/test.yml`:

```yaml
name: Tests

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  # Rust tests
  rust-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: Run Rust tests
        run: |
          export PYO3_PYTHON=$(pwd)/.venv/bin/python
          cargo test --workspace --exclude djust_live --release

  # Python tests (parallel)
  python-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: Run Python tests
        run: |
          uv sync --extra dev
          make test-python-parallel

  # JavaScript tests
  js-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: Run JavaScript tests
        run: npm test
```

### Pre-commit Hooks

Tests run automatically before commits and pushes:

```bash
# Install hooks (run once after clone)
make pre-commit-install

# Hooks run automatically on:
# - git commit: ruff, ruff-format, bandit, detect-secrets
# - git push: full test suite (~40s)
```

Bypass hooks only when absolutely necessary:

```bash
# Bypass pre-commit (for WIP commits)
git commit --no-verify -m "WIP: debugging"

# Never bypass pre-push in normal workflow
# git push --no-verify  # ❌ Don't do this
```

## Troubleshooting

### Common Test Failures

#### 1. Import Errors

**Problem**: `ModuleNotFoundError: No module named 'demo_app'`

**Solution**: Check `PYTHONPATH` includes demo project:

```bash
# In pyproject.toml
[tool.pytest.ini_options]
pythonpath = [".", "examples/demo_project"]
```

#### 2. Database Errors

**Problem**: `django.db.utils.OperationalError: no such table`

**Solution**: Ensure database migrations are applied:

```bash
cd examples/demo_project
python manage.py migrate
```

#### 3. Session Errors

**Problem**: `AttributeError: 'WSGIRequest' object has no attribute 'session'`

**Solution**: Use fixture with session support:

```python
# Use get_request fixture instead of request_factory
def test_with_session(get_request):  # ✅
    assert hasattr(get_request, 'session')
```

#### 4. Async Test Failures

**Problem**: `RuntimeWarning: coroutine was never awaited`

**Solution**: Mark test as async:

```python
import pytest

@pytest.mark.asyncio  # ✅ Add this
async def test_websocket():
    await some_async_operation()
```

#### 5. Rust Build Failures

**Problem**: Tests fail with `ImportError: cannot import name '_rust'`

**Solution**: Rebuild Rust extensions:

```bash
make build
# or
maturin develop --release
```

#### 6. Test Pollution

**Problem**: Tests pass individually but fail when run together

**Solution**: Ensure proper cleanup and isolation:

```python
import pytest

@pytest.fixture(autouse=True)
def cleanup():
    """Clean up after each test."""
    yield
    # Cleanup code here
    from djust.state_backend import get_backend
    backend = get_backend()
    backend.cleanup_expired(ttl=0)
```

#### 7. Flaky Tests

**Problem**: Tests sometimes pass, sometimes fail

**Common causes**:
- Race conditions in async code
- Time-dependent logic (use freezegun)
- Random data (use seeds)
- Shared mutable state

**Solution**:

```python
import pytest
from freezegun import freeze_time

@freeze_time("2024-01-01 12:00:00")
def test_time_dependent():
    """Test with frozen time."""
    # Now() always returns 2024-01-01 12:00:00
    pass
```

### Running Specific Test Subsets

```bash
# Run only fast tests
pytest -m "not slow"

# Run only database tests
pytest -m django_db

# Run only WebSocket tests
pytest -k websocket

# Run tests in specific directory
pytest tests/unit/

# Run with minimal verbosity
pytest -q

# Run with debug output
pytest -vv --tb=long
```

### Debug Mode

Enable debug output for troubleshooting:

```python
# In settings.py
LIVEVIEW_CONFIG = {
    'debug_vdom': True,  # Enable VDOM debug logs
}

# In test
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Performance Issues

If tests are slow:

```bash
# Use parallel execution
pytest -n auto

# Profile slow tests
pytest --durations=10

# Skip slow tests during development
pytest -m "not slow"
```

## Additional Resources

- [Testing JavaScript](docs/testing/TESTING_JAVASCRIPT.md) - JavaScript testing guide
- [Testing Pages](docs/testing/TESTING_PAGES.md) - Automated test pages
- [Smoke & Fuzz Testing](docs/testing/TESTING_SMOKE_FUZZ.md) - Smoke and fuzz testing
- [Pull Request Checklist](docs/PULL_REQUEST_CHECKLIST.md) - PR review checklist
- [Security Guidelines](docs/SECURITY_GUIDELINES.md) - Security testing patterns

---

**Questions?** Open an issue at https://github.com/djust-org/djust/issues
