# Documented-example execution inventory

Generated 2026-10-28 from `tests/test_doc_snippets_execute.py` (set `DJUST_DOC_SNIPPET_INVENTORY=<file.jsonl>` and run it to regenerate the snippet section), a Playwright crawl of `examples/demo_project`, and a browser check of `djust new` plus the README "add to an existing project" steps. The commands behind every number are in the PR that added this file (test: execute documented LiveView snippets and the demo project end to end).

## 1. Fenced `python` blocks that define a LiveView / LiveComponent

Scope: every ```python block under `docs/website/guides/*.md`, `QUICKSTART.md` and `docs/website/getting-started/*.md` whose source defines a `LiveView` or `LiveComponent` subclass: **224 blocks**. Each is executed; every `LiveView` is mounted through `LiveViewTestClient`, rendered, and every `@event_handler` fired with no arguments; every `LiveComponent` goes through `LiveComponentTestClient`.

| Outcome | Blocks |
|---|---|
| pass — executes, mounts, renders, handlers run | 126 |
| fail — a reader copying the block hits an error | 8 |
| not runnable in isolation — needs the reader's app, model, template file, URL kwargs, or network | 90 |

A block is counted as passing when it has no inline template too: it is mounted on a stub `<div dj-root>` so `mount()`, `get_context_data()` and the handlers still run. 121 of the 126 passes used that stub; 5 rendered their own inline template.

### 1a. Failures

| file:line | class | what failed | likely cause |
|---|---|---|---|
| `docs/website/guides/declarative-ux-attrs.md:L710` | ChatView | ChatView.send: 'ChatView' object has no attribute '_post' | phantom attribute |
| `docs/website/guides/error-codes.md:L533` | MyView | MyView.toggle_sidebar: 'MyView' object has no attribute 'sidebar_open' | phantom attribute |
| `docs/website/guides/error-overlay.md:L44` | OrderView | OrderView.charge: amount must be positive | deliberate: the guide shows the overlay a raising handler produces |
| `docs/website/guides/hooks.md:L468` | DashboardView | DashboardView: AttributeError: 'DashboardView' object has no attribute 'fetch_sales' | phantom attribute |
| `docs/website/guides/http-api.md:L416` | MyView | MyView: AttributeError: 'MyView' object has no attribute 'subscribe_presence' | phantom attribute |
| `docs/website/guides/time-travel-debugging.md:L25` | CounterView | CounterView: the client has nothing to attach to (template-requirements.md) | template contract (no root) |
| `docs/website/guides/tutorial-multi-step-wizard.md:L117` | SignupWizardView | SignupWizardView.back_step: 'SignupWizardView' object has no attribute 'step' | phantom attribute |
| `docs/website/guides/tutorial-streaming-ai.md:L78` | — | TypeError: 'module' object is not callable (snippet line 18, doc line 96) | public-API name collision (`djust.rate_limit` module shadows the decorator) |

Every row above is listed in `KNOWN_FAILURES` in the test as `xfail(strict=True)`: a new failure turns the suite red; a doc fix that makes a listed block pass also turns it red until the entry is removed.

### 1b. Not runnable in isolation (recorded, not failed)

| reason | blocks | examples |
|---|---|---|
| references a name the block does not define | 61 | `docs/website/guides/BEST_PRACTICES.md:L53` (get_all_statuses); `docs/website/guides/BEST_PRACTICES.md:L127` (boto3); `docs/website/guides/BEST_PRACTICES.md:L180` (Product); `docs/website/guides/BEST_PRACTICES.md:L543` (Message) |
| relative import of the reader's app | 16 | `docs/website/guides/admin-widgets.md:L46` (ImportError); `docs/website/guides/forms.md:L246` (ImportError); `docs/website/guides/forms.md:L281` (ImportError); `docs/website/guides/tutorial-authentication.md:L105` (ImportError) |
| defines a Django model (needs an app) | 4 | `docs/website/guides/database-notifications.md:L19` (RuntimeError); `docs/website/guides/database-notifications.md:L191` (RuntimeError); `docs/website/guides/database-notifications.md:L215` (RuntimeError); `docs/website/guides/database-notifications.md:L238` (RuntimeError) |
| needs a template file | 3 | `docs/website/guides/components.md:L433` (UserCard); `docs/website/guides/components.md:L1621` (Card); `docs/website/guides/tutorial-live-component.md:L75` (StarRating) |
| mount() requires URL kwargs | 3 | `docs/website/guides/error-codes.md:L2596` (ContactDetailView); `docs/website/guides/template-cheatsheet.md:L660` (ArticleView); `docs/website/guides/tutorial-real-time-comments.md:L283` (CommentThreadView) |
| performs a network call | 2 | `docs/website/guides/services.md:L85` (ConnectError); `docs/website/guides/services.md:L311` (ConnectError) |
| uses a ... placeholder | 1 | `docs/website/guides/wizards.md:L289` (AttributeError) |

Undefined names the blocks assume (count): `LiveViewTestClient` ×6, `TutorialMixin` ×5, `boto3` ×4, `Product` ×3, `Message` ×3, `TenantMixin` ×3, `AnyOf` ×2, `User` ×2, `ContactForm` ×2, `Item` ×2, `require_auth` ×2, `get_all_statuses` ×1, `LeaseForm` ×1, `TestCase` ×1, `get_metrics` ×1, `get_admin_stats` ×1, `StaffRequiredMixin` ×1, `get_public_features` ×1, `Document` ×1, `StatusDot` ×1, `Badge` ×1, `CounterWidget` ×1, `Claim` ×1, `MySerializableConfig` ×1, `MyForm` ×1, `Project` ×1, `TenantScopedMixin` ×1, `require_verified_email` ×1, `require_admin` ×1, `User.profile` ×1, `OfflineMixin` ×1, `room` ×1, `User.cart_items` ×1, `User.preferences` ×1, `fetch_rows` ×1, `ImageUploadPreview` ×1, `MyWriter` ×1, `PersonalInfoForm` ×1.

Handlers that take required arguments are not fired (the block documents their signature); views whose `mount()` needs URL kwargs are recorded, not mounted.

## 2. Demo project crawl (`examples/demo_project`, headless Chromium)

147 parameterless URLs from the root urlconf, visited anonymously on a fresh SQLite database: **131 answered 200**, `/_djust/observability/eval_handler/` → 404 , `/_djust/observability/handler_timings/` → 404 , `/_djust/observability/health/` → 404 , `/_djust/observability/last_traceback/` → 404 , `/_djust/observability/log/` → 404 , `/_djust/observability/reset_view_state/` → 404 , `/_djust/observability/sql_queries/` → 404 , `/_djust/observability/turn_timings/` → 404 , `/_djust/observability/view_assigns/` → 404 , `/accounts/logout/` → 405 , `/demos/markdown-stream/` → 500 , `/demos/model-form/` → 500 , `/demos/no-template/` → 500 , `/rentals/portal/` → 500 , `/rentals/portal/maintenance/` → 500 , `/rentals/portal/payments/` → 500 . 143 were HTML pages; 64 carried a `dj-root`, and 64 of those reached the connected state (`body.dj-connected`) within 1.5 s.

The first visible `dj-click` control was clicked on 49 pages; the dj-root's HTML changed on 29 of them and the socket stayed connected on 36.

### 2a. Defects, verified against the demo source

| page | symptom in the browser | source |
|---|---|---|
| `/demos/markdown-stream/`, `/demos/model-form/`, `/demos/no-template/`, `/rentals/portal/`, `/rentals/portal/maintenance/`, `/rentals/portal/payments/` | HTTP 500 on GET | see the server exception in the table below (missing `djust_markdown` tag library; `demo_app_product` table absent; `render_full_template()` signature drift; three views read `self.error_message` that `mount()` never sets) |
| `/` | clicking Increment Counter: `Event 'increment_counter' on IndexView is not decorated with @event_handler` | `djust_homepage/views.py:326` `def increment_counter(self)` has no decorator |
| `/demos/`, `/demos/design2/`, `/demos/design3/`, `/demos/hybrid/` | clicking Decrement: `No handler found for event: decrement — Available handlers on DemosIndexView: update_model` | `djust_demos/templates/demos/index.html:412` binds `dj-click="decrement"`; `djust_demos/views/demos.py:15` DemosIndexView defines no `decrement` |
| `/demos/optimistic-counter/` | clicking Decrement: `Event 'decrement' on OptimisticCounterView is not decorated with @event_handler` | `djust_demos/views/optimistic_counter_demo.py:160-161` has `@optimistic` but no `@event_handler` |
| `/rentals/financials/` | clicking a period: `Event 'change_period' on FinancialDashboardView is not decorated with @event_handler` | `djust_rentals/views/financials.py:124` `def change_period` has no decorator |
| `/docs/components/`, `/forms/registration/` | `[LiveView] Server error: View djust_docs.views.ComponentsGuideView is not allowed` / `djust_forms.views.RegistrationFormView is not allowed`; the page never connects, the first click times out | `demo_project/settings.py:159-168` LIVEVIEW_ALLOWED_MODULES lists demo_app, djust_demos, djust_shared, djust_rentals, djust_tests and djust — not djust_docs or djust_forms |
| `/home-demos/counter/`, `/home-demos/data/`, `/home-demos/navbar-badge/`, `/home-demos/search/`, `/home-demos/todo/`, `/forms/auto/*`, `/forms/contact/`, `/forms/simple/`, `/demos/components-only/`, `/demos/dropdown/`, `/demos/navbar/`, `/demos/offcanvas/`, `/demos/unified-components/` | console: `[LiveView] No containers found! Your template root element needs dj-view=...`; clicking the first `dj-click` does nothing and the socket never connects | the inline templates have no `dj-root` / `dj-view` element, e.g. `djust_homepage/views.py:17-30` HomeCounterDemo (inferred: the home-demos views may be meant to be embedded by the index page rather than visited directly; the forms/* and demos/* pages are top-level) |

Not defects: the nine `/_djust/observability/*` endpoints answer 404 to an anonymous browser GET (DEBUG-gated, localhost-only API), and `/accounts/logout/` is POST-only (405).

### 2b. Every page that has a dj-root, a non-200 status, or a browser error

| url | status | dj-root | connected | clicked | DOM changed | console/page errors |
|---|---|---|---|---|---|---|
| `/` | 200 | 1 | True | BUTTON dj-click=increment_counter | True | [LiveView] Server error: Event 'increment_counter' on IndexView is not decorated with @event_handler. Fix: Add the decorator: @event_handler |
| `/_djust/observability/eval_handler/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/handler_timings/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/health/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/last_traceback/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/log/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/reset_view_state/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/sql_queries/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/turn_timings/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/_djust/observability/view_assigns/` | 404 | 0 | False |  |  | Failed to load resource: the server responded with a status of 404 (Not Found) |
| `/accounts/logout/` | 405 | 0 | False |  |  | Failed to load resource: the server responded with a status of 405 (Method Not Allowed) |
| `/demos/` | 200 | 1 | True | BUTTON dj-click=decrement | False | [LiveView] Server error: No handler found for event: decrement Available handlers on DemosIndexView: update_model |
| `/demos/activity/` | 200 | 1 | True | BUTTON dj-click=switch_tab('profile') | False |  |
| `/demos/browser-smoke/` | 200 | 1 | True | BUTTON dj-click=bump | True |  |
| `/demos/cache/` | 200 | 1 | True |  |  |  |
| `/demos/chat/` | 200 | 1 | True |  |  |  |
| `/demos/component-showcase/` | 200 | 1 | True | BUTTON dj-click=decrement | True |  |
| `/demos/components-only/` | 200 | 0 | False | BUTTON dj-click=dismiss | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/demos/components/` | 200 | 1 | True |  |  |  |
| `/demos/counter/` | 200 | 1 | True | BUTTON dj-click=increment | True |  |
| `/demos/datatable/` | 200 | 1 | True |  |  |  |
| `/demos/debounce/` | 200 | 1 | True |  |  |  |
| `/demos/design1/` | 200 | 1 | True |  |  |  |
| `/demos/design2/` | 200 | 1 | True | BUTTON dj-click=decrement | True | [LiveView] Server error: No handler found for event: decrement Available handlers on DemosIndexDesign2View: update_model |
| `/demos/design3/` | 200 | 1 | True | BUTTON dj-click=decrement | False | [LiveView] Server error: No handler found for event: decrement Available handlers on DemosIndexDesign3View: update_model |
| `/demos/dropdown/` | 200 | 0 | False |  |  | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/demos/embedded-directives/` | 200 | 1 | True | BUTTON dj-click=got_click | True |  |
| `/demos/exposure-legacy/` | 200 | 1 | True | BUTTON dj-click=increment | True |  |
| `/demos/exposure/` | 200 | 1 | True | BUTTON dj-click=increment | True |  |
| `/demos/hybrid/` | 200 | 1 | True | BUTTON dj-click=decrement | True | [LiveView] Server error: No handler found for event: decrement Available handlers on DemosIndexHybridView: update_model |
| `/demos/interactive-acceptance/` | 200 | 1 | True | BUTTON dj-click=refresh | True |  |
| `/demos/interactive-collection/` | 200 | 1 | True | BUTTON dj-click=reverse | True |  |
| `/demos/interactive-dropdown/` | 200 | 1 | True | BUTTON dj-click=show_observed | True |  |
| `/demos/interactive-nav/` | 200 | 1 | True | BUTTON dj-click=toggle | True |  |
| `/demos/kitchen-sink/` | 200 | 1 | True | BUTTON dj-click=dismiss | True |  |
| `/demos/markdown-stream/` | 500 | 0 | False |  |  | server: TemplateSyntaxError: Invalid block tag on line 32: 'djust_markdown' (KeyError: 'djust_markdown' — tag library not loaded/registered) / Failed to load resource: the server responded with a status of 500 (Internal Server Error) |
| `/demos/model-form/` | 500 | 0 | False |  |  | server: OperationalError: no such table: demo_app_product (demo_app is not in INSTALLED_APPS/migrated) / Failed to load resource: the server responded with a status of 500 (Internal Server Error) |
| `/demos/multi-view/` | 200 | 6 | True | BUTTON dj-click=click | True |  |
| `/demos/nav-a/` | 200 | 1 | True | BUTTON dj-click=bump | True |  |
| `/demos/nav-b/` | 200 | 1 | True | BUTTON dj-click=bump | True |  |
| `/demos/navbar/` | 200 | 0 | False |  |  | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/demos/no-template/` | 500 | 0 | False |  |  | server: TypeError: NoTemplateDemo.render_full_template() got an unexpected keyword argument 'serialized_context' / Failed to load resource: the server responded with a status of 500 (Internal Server Error) |
| `/demos/offcanvas/` | 200 | 0 | False |  |  | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/demos/optimistic-counter/` | 200 | 1 | True | BUTTON dj-click=decrement | False | [LiveView] Server error: Event 'decrement' on OptimisticCounterView is not decorated with @event_handler. Fix: Add the decorator: @event_han |
| `/demos/optimistic-todo/` | 200 | 1 | True |  |  |  |
| `/demos/performance/` | 200 | 1 | True | BUTTON dj-click=add_items_10 | True |  |
| `/demos/pwa/` | 200 | 1 | True |  |  |  |
| `/demos/react/` | 200 | 1 | True | BUTTON dj-click=increment_server | True |  |
| `/demos/rust-components/` | 200 | 1 | True | BUTTON dj-click=switch_basic_tab | True |  |
| `/demos/server-function/` | 200 | 1 | True |  |  |  |
| `/demos/shadcn/` | 200 | 1 | True | BUTTON dj-click=decrement | True |  |
| `/demos/smart-dashboard/` | 200 | 1 | True | BUTTON dj-click=simulate_sensor | False |  |
| `/demos/strict-parameters/` | 200 | 1 | True | BUTTON dj-click=pick | True |  |
| `/demos/table-select/` | 200 | 1 | True |  |  |  |
| `/demos/tenant/` | 200 | 1 | True | BUTTON dj-click=increment | True |  |
| `/demos/throttle/` | 200 | 1 | True |  |  |  |
| `/demos/todo/` | 200 | 1 | True |  |  |  |
| `/demos/unified-components/` | 200 | 0 | False | BUTTON dj-click=increment | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/demos/whats-new/0.4.2/` | 200 | 1 | True | BUTTON dj-click=start_tutorial | True |  |
| `/djust-admin-demo/logout/` | 200 | 0 | False |  |  | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/docs/components/` | 200 | 1 | True | BUTTON dj-click=increment_demo_count |  | [LiveView] Server error: View djust_docs.views.ComponentsGuideView is not allowed TimeoutError: Locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for locator("[dj-click]:visible").first
    - locator resolved to <button dj-click="increment_demo_count" class="btn btn-primary btn-lg me-2">↵                            Increment Counter↵  …</button>
  - attempting click action
    2 × |
| `/forms/auto/` | 200 | 0 | False | BUTTON dj-click=reset_form | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/forms/auto/compare/` | 200 | 0 | False |  |  | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/forms/auto/plain/` | 200 | 0 | False | BUTTON dj-click=reset_form | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/forms/auto/tailwind/` | 200 | 0 | False | BUTTON dj-click=reset_form | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/forms/contact/` | 200 | 0 | False | BUTTON dj-click=reset_form | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/forms/profile/` | 200 | 1 | True | BUTTON dj-click=reset_form | False |  |
| `/forms/registration/` | 200 | 1 | True | BUTTON dj-click=reset_form |  | [LiveView] Server error: View djust_forms.views.RegistrationFormView is not allowed TimeoutError: Locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for locator("[dj-click]:visible").first
    - locator resolved to <button type="button" dj-click="reset_form" class="btn btn-outline-secondary">↵                                    Reset Form↵ …</button>
  - attempting click action
    2 |
| `/forms/simple/` | 200 | 0 | False | BUTTON dj-click=reset_form | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/home-demos/counter/` | 200 | 0 | False | BUTTON dj-click=decrement | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/home-demos/data/` | 200 | 0 | False | BUTTON dj-click=refresh_data | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/home-demos/navbar-badge/` | 200 | 0 | False | BUTTON dj-click=increment_notifications | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/home-demos/search/` | 200 | 0 | False |  |  | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/home-demos/todo/` | 200 | 0 | False | BUTTON dj-click=delete_todo | False | [LiveView] No containers found! Your template root element needs: dj-view="app.views.MyView" Example: <div dj-view="myapp.views.DashboardVie |
| `/rentals/` | 200 | 1 | True |  |  |  |
| `/rentals/financials/` | 200 | 1 | True | BUTTON dj-click=change_period | False | [LiveView] Server error: Event 'change_period' on FinancialDashboardView is not decorated with @event_handler. Fix: Add the decorator: @even |
| `/rentals/financials/expenses/` | 200 | 1 | True |  |  |  |
| `/rentals/financials/income/` | 200 | 1 | True |  |  |  |
| `/rentals/financials/profit-loss/` | 200 | 1 | True |  |  |  |
| `/rentals/leases/` | 200 | 1 | True |  |  |  |
| `/rentals/leases/add/` | 200 | 1 | True |  |  |  |
| `/rentals/maintenance/` | 200 | 1 | True |  |  |  |
| `/rentals/portal/` | 500 | 0 | False |  |  | server: AttributeError: 'TenantDashboardView' object has no attribute 'error_message' / Failed to load resource: the server responded with a status of 500 (Internal Server Error) |
| `/rentals/portal/maintenance/` | 500 | 0 | False |  |  | server: AttributeError: 'TenantMaintenanceListView' object has no attribute 'error_message' / Failed to load resource: the server responded with a status of 500 (Internal Server Error) |
| `/rentals/portal/maintenance/create/` | 200 | 1 | True |  |  |  |
| `/rentals/portal/payments/` | 500 | 0 | False |  |  | server: AttributeError: 'TenantPaymentsView' object has no attribute 'error_message' / Failed to load resource: the server responded with a status of 500 (Internal Server Error) |
| `/rentals/properties/` | 200 | 1 | True |  |  |  |
| `/rentals/properties/add/` | 200 | 1 | True |  |  |  |
| `/rentals/tenants/` | 200 | 1 | True |  |  |  |
| `/rentals/tenants/add/` | 200 | 1 | True |  |  |  |
| `/sticky_demo/dashboard/` | 200 | 3 | True | BUTTON dj-click=refresh_metrics | True |  |
| `/sticky_demo/reports/` | 200 | 1 | True |  |  |  |
| `/sticky_demo/settings/` | 200 | 1 | True | BUTTON dj-click=set_theme | True |  |
| `/tests/cache/` | 200 | 1 | True |  |  |  |
| `/tests/draft-mode/` | 200 | 1 | True | BUTTON dj-click=save_article | True |  |
| `/tests/loading/` | 200 | 1 | True | BUTTON dj-click=slow_operation | True |  |
| `/tests/virtual-keyed/` | 200 | 1 | True | BUTTON dj-click=edit_first | True |  |

## 3. `djust new` and the README "add to an existing project" path

**`djust new myproject`** (main's CLI, clean environment; `uv venv --python ">=3.11"` picked CPython 3.14 free-threaded and `uv pip install` resolved `djust[components]>=1.3.0rc14` to the PyPI 1.3.0rc14 wheel): setup, `makemigrations`, `migrate --run-syncdb` and `check` all completed. Served with `uvicorn myproject.asgi:application` on :8767, the home page answered 200, had 1 `dj-root`, connected=True, and clicking the first control (`INPUT dj-click=toggle_item`) changed the dj-root's DOM (True) with 0 console errors. The scaffold's demo is a todo list (`toggle_item` / `delete_item`), not a counter.

**README "add to an existing project" (steps 1-4 applied verbatim to a fresh `django-admin startproject` + `startapp myapp`, served with `uvicorn myproject.asgi:application` as step 4 says):** `manage.py check` passes and `GET /counter/` answers 200 with `Count: 0` — but the counter is dead. `body.dj-connected` = False; the heading reads `Count: 0` before and `Count: 0` after two clicks. Console: `Failed to load resource: the server responded with a status of 404 (Not Found)`; `Refused to apply style from 'http://127.0.0.1:8768/static/djust/debug-panel.css' because its MIME type ('text/`; `Failed to load resource: the server responded with a status of 404 (Not Found)`. `GET /static/djust/client.js` is 404: the README's `asgi.py` block (README.md:80-98, the `http` entry at :91) uses a bare `get_asgi_application()`, so under uvicorn nothing serves `/static/`, the djust client never loads and no WebSocket is opened. The installation guide (`docs/website/getting-started/installation.md:355-364`), the scaffold and the demo project all wrap it in `ASGIStaticFilesHandler`.

Wrapping the README's handler in `ASGIStaticFilesHandler(get_asgi_application())` and restarting: `/static/djust/client.js` → 200, connected=True, heading `Count: 0` → `Count: 1` → `Count: 2` after two clicks, console errors: [].
