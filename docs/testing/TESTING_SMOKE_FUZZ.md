# Smoke & Fuzz Testing Guide

`LiveViewSmokeTest` is a test mixin that auto-discovers your LiveView classes, mounts each one, and runs smoke tests and fuzz tests against their event handlers. It catches rendering errors, N+1 queries, XSS escaping failures, and unhandled exceptions from malformed input.

## Quick Start

```python
from django.test import TestCase
from djust.testing import LiveViewSmokeTest

class TestMyAppViews(TestCase, LiveViewSmokeTest):
    app_label = "myapp"
```

Run it:

```bash
pytest -k "TestMyAppViews" -v
```

This single class generates six test methods automatically:

| Test | What it checks |
|------|---------------|
| `test_smoke_render` | Every view mounts and renders without exceptions |
| `test_smoke_queries` | Every view stays within its query budget (its own `query_budget`, else `max_queries`) |
| `test_smoke_event_queries` | Every handler with an event budget (`query_budget["event"]` / `["events"]`) stays within it |
| `test_fuzz_xss` | XSS payloads don't appear unescaped in rendered HTML |
| `test_fuzz_no_unhandled_crash` | Wrong-type params don't cause unhandled exceptions |
| `test_fuzz_handlers_succeed` | Handlers handle the fuzz payloads without returning `success=False` |

## Configuration

All attributes are optional:

```python
class TestAllViews(TestCase, LiveViewSmokeTest):
    # Filter to a single Django app (default: all apps)
    app_label = "crm"

    # Max DB queries allowed per view, mount + render (default: 50). A view can
    # declare its own with `query_budget`; see below.
    max_queries = 20

    # Enable/disable fuzz tests (default: True)
    fuzz = True

    # Skip specific view classes that need special setup
    skip_views = [AdminDashboardView, SetupWizardView]

    # Per-view configuration for mount params and user
    view_config = {
        DealDetailView: {
            "mount_params": {"object_id": 1},
            "user": staff_user,
        },
        SettingsView: {
            "user": admin_user,
        },
    }
```

### `app_label`

Limits discovery to views in a specific Django app. Without it, all LiveView subclasses across all installed apps are tested. Use this to create focused test classes per app:

```python
class TestCRMViews(TestCase, LiveViewSmokeTest):
    app_label = "crm"

class TestBillingViews(TestCase, LiveViewSmokeTest):
    app_label = "billing"
```

### `max_queries`

The query count threshold for `test_smoke_queries`. If any view exceeds this during mount and render, the test fails with details about which views and how many queries they ran. Set this to your project's acceptable limit (default: 50). It is the fallback: a view's own `query_budget` and `view_config[View]["max_queries"]` take precedence (see below).

### `query_budget` (on the view)

A view declares its own budget so one number does not have to fit every view:

```python
from djust import LiveView


class ProductListView(LiveView):
    template_name = "shop/product_list.html"
    query_budget = 3  # mount + first render, together


class SalesReportView(LiveView):
    template_name = "shop/sales_report.html"
    query_budget = {"mount": 30, "render": 5}  # each phase on its own
```

Resolution, most local first: `view_config[View]["max_queries"]`, then `View.query_budget`, then `max_queries`. An int covers mount and first render together; a dict covers `"mount"` and `"render"` separately, and a phase it omits falls back to `max_queries`. `"event": n` and `"events": {handler: n}` cap a handler's queries and are checked by `test_smoke_event_queries` (below). Any other dict key (a typo), a handler name in `"events"` the view does not have or that is `async def` / `@background` (nothing measures those), or a value that is not a non-negative int fails the test with a `ValueError` naming the view, since a budget the test cannot enforce would only look like protection. Failures read `shop.views.ProductListView: 5 queries (max 3, from query_budget)`. `LiveViewSmokeTest` reads the budget, and a view that declares one is also measured while the app runs in `DEBUG`: see [Per-view query budgets](../website/testing/index.md#per-view-query-budgets).

### Event budgets (`test_smoke_event_queries`)

Counts the handler step's own queries (as the run-time meter does, so not the per-event object-permission re-check). Calls each budgeted handler on a freshly mounted view, with the `view_config[View]["event_params"]` you supply and the generated fuzz payloads, and fails when any call ran more queries than the budget. It runs whether or not `fuzz` is on. A handler named in `"events"` that no call succeeded for is an error (it was not measured); one covered only by `"event"` is a `UserWarning`.

### `fuzz`

Set to `False` to skip `test_fuzz_xss` and `test_fuzz_no_unhandled_crash`. Useful if you want smoke tests only:

```python
class TestQuickSmoke(TestCase, LiveViewSmokeTest):
    fuzz = False  # Only test mount + render
```

### `skip_views`

List of view classes to exclude from testing. Use this for views that require complex setup (e.g., multi-step wizards, views that depend on external services):

```python
skip_views = [PaymentView, OAuthCallbackView]
```

### `view_config`

Dict mapping view classes to their mount configuration:

- `mount_params` (dict): Keyword arguments passed to `client.mount(**params)`. Use this for views that require URL kwargs like `object_id`.
- `user` (User instance): Passed to `LiveViewTestClient(view, user=user)`. Use this for views with `login_required = True`.
- `max_queries` (int): The query budget for this view in this test class, ahead of the view's `query_budget` and the class `max_queries`. It does not replace the view's event budget.
- `event_params` (dict): `{handler_name: {...params}}` (or a list of dicts per handler): real parameters `test_smoke_event_queries` calls the handler with, for a handler a generated payload cannot get past its first check.

## What Gets Tested

### View Discovery

`LiveViewSmokeTest` auto-discovers views by:

1. Importing `views`, `admin_views`, and `djust_admin` modules from each installed Django app
2. Walking all `LiveView.__subclasses__()` recursively
3. Filtering out internal djust classes and abstract bases (no `template_name`)
4. Applying `app_label` filter and `skip_views` exclusion

### Smoke Render

Each discovered view is mounted with `LiveViewTestClient`, then `render()` is called. The test fails if:

- Any view raises an exception during mount or render
- Any view returns empty or very short HTML (< 10 characters)

All failures are collected and reported together:

```
AssertionError: Smoke render failed for 2/15 views:
  - myapp.views.BrokenView: ValueError: missing required context
  - myapp.views.EmptyView: render returned empty/tiny HTML (0 chars)
```

### Smoke Queries

Same as smoke render, but with Django's query logging enabled. Each view's mount and first render are counted and compared against that view's budget (`view_config` `max_queries`, else the view's `query_budget`, else `max_queries`). Catches N+1 query issues early.

### Fuzz XSS

For each view, every event handler is called with XSS payloads injected into string parameters. After each call, the view is re-rendered and the HTML is checked for unescaped XSS sentinels.

**XSS payloads tested:**

```
<script>alert("xss")</script>
<img src=x onerror=alert(1)>
"><svg onload=alert(1)>
'; DROP TABLE users; --
${7*7}
{{constructor.constructor('return this')()}}
<a href="javascript:alert(1)">click</a>
```

If any of these appear unescaped in the rendered output, the test fails. Properly escaped output (e.g., `&lt;script&gt;`) is safe and passes.

### Fuzz Type Confusion

For each handler parameter, wrong-type values are sent to check that the handler doesn't crash with an unhandled exception:

| Parameter type | Fuzz values |
|---------------|-------------|
| `str` | `None`, `0`, `True`, `[]`, `{}`, `""`, `"x" * 10000` |
| `int` | `None`, `"not_a_number"`, `""`, `True`, `-1`, `0`, `2^31`, `3.14` |
| `float` | `None`, `"nan"`, `""`, `True`, `float("inf")` |
| `bool` | `None`, `"yes"`, `0`, `1`, `""`, `"false"` |

Additional strategies: empty params (no arguments at all), and missing one required parameter at a time.

A handler returning an error or raising a caught exception is fine. Only unhandled exceptions that would cause a 500 in production are reported as failures.

## Examples

### Basic: Test All Views

```python
from django.test import TestCase
from djust.testing import LiveViewSmokeTest

class TestAllViews(TestCase, LiveViewSmokeTest):
    pass  # Tests every LiveView in the project
```

### Per-App with Configuration

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from djust.testing import LiveViewSmokeTest
from crm.views import DealDetailView, ContactDetailView

User = get_user_model()

class TestCRMViews(TestCase, LiveViewSmokeTest):
    app_label = "crm"
    max_queries = 15

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user("staff", is_staff=True)

    view_config = {}

    def setUp(self):
        # view_config needs live DB objects, set up in setUp
        self.view_config = {
            DealDetailView: {
                "mount_params": {"object_id": 1},
                "user": self.staff,
            },
            ContactDetailView: {
                "mount_params": {"pk": 1},
                "user": self.staff,
            },
        }
```

### Smoke Only (No Fuzz)

```python
class TestQuickSmoke(TestCase, LiveViewSmokeTest):
    app_label = "dashboard"
    fuzz = False
    max_queries = 30
```

### Skip Problematic Views

```python
from myapp.views import ExternalAPIView, WebhookView

class TestMyAppViews(TestCase, LiveViewSmokeTest):
    app_label = "myapp"
    skip_views = [ExternalAPIView, WebhookView]
```

## Troubleshooting

### "No views discovered"

- Ensure your views module is importable (no syntax errors)
- Check that views have `template_name` set (abstract bases without it are skipped)
- Verify `app_label` matches your Django app's label (check `apps.py`)

### Views need database fixtures

Use `setUpTestData` or Django fixtures to create required database objects, then reference them in `view_config`:

```python
@classmethod
def setUpTestData(cls):
    cls.org = Organization.objects.create(name="Test Org")

def setUp(self):
    self.view_config = {
        OrgDashboard: {"mount_params": {"org_id": self.org.pk}},
    }
```

### XSS false positives

If a view intentionally renders raw HTML (e.g., a WYSIWYG editor preview), exclude it via `skip_views` rather than disabling fuzz globally.

### Query count too strict

Start with a high `max_queries` (e.g., 100) and lower it as you optimize. The default of 50 is a reasonable starting point for most views. Once a view is tuned, give it its own `query_budget` so a regression in it is caught at its real number, not the app-wide one.

## Related

- [Security Guidelines](../SECURITY_GUIDELINES.md) — XSS prevention rules and fuzz testing overview
- [Testing JavaScript](TESTING_JAVASCRIPT.md) — Client-side test setup with Vitest
- [Testing Pages](TESTING_PAGES.md) — Browser-based interactive test pages
