# Admin Widgets — Per-Page LiveView Slots & Bulk-Action Progress (v0.7.0)

`djust.admin_ext` ships a drop-in reactive replacement for Django's
stock admin (`DjustAdminSite`, `DjustModelAdmin`, plugin system,
dashboard widgets).  v0.7.0 adds two building blocks that close the gap
with real-world admin requirements:

1. **Per-page widget slots** on `DjustModelAdmin` — embed any LiveView
   into a model's change-list or change-form page via
   `change_list_widgets` / `change_form_widgets` class attributes.
2. **Bulk-action progress** — turn any ModelAdmin action into a
   background job with a live progress page using
   `@admin_action_with_progress`.

## Why this design (and why we didn't ship `DjustAdminMixin`)

A lot of demos reach for "one mixin you sprinkle on `admin.ModelAdmin`
and your admin goes live." We prototyped that.  In practice it
duplicates ~60% of `DjustAdminSite`, fights Django's `change_view` /
`changelist_view` MRO (20+ override points, complex
`template_name` resolution, `ChangeList` introspection), and diverges
from the recommended adoption path (ADR-007 Phase 4: `djust[admin]` =
`DjustAdminSite`).

Instead, v0.7.0:

- Extends the existing `DjustModelAdmin` with **thin** per-page widget
  slots that reuse the already-shipped `{% live_render %}` machinery.
- Adds `BulkActionProgressWidget` as a first-class LiveView that any
  ModelAdmin action can redirect to via a one-line decorator.

Result: an admin page that wants a live revenue chart is

```python
class OrderAdmin(DjustModelAdmin):
    change_list_widgets = [RevenueChartView]
```

...and the chart *is* a regular LiveView — same class you'd use on any
page.  No new framework surface, nothing novel to learn.

## Per-page widget slots

### Quick start — `change_form_widgets`

```python
# myapp/djust_admin.py
from djust import LiveView
from djust.admin_ext import DjustModelAdmin, site
from djust.decorators import event_handler, state
from .models import Order


class OrderActivityWidget(LiveView):
    """Shows recent activity on the order currently being edited."""

    template_name = "myapp/admin/order_activity.html"
    label = "Recent activity"
    size = "lg"  # "sm" | "md" | "lg" — controls grid column span

    events = state(default=[])

    def mount(self, request, object_id=None, **kwargs):
        self.request = request
        self.object_id = object_id
        self.events = list(
            Order.objects.get(pk=object_id).activity.order_by("-ts")[:10]
        )

    def get_context_data(self, **kwargs):
        return {"events": self.events}


@site.register(Order)
class OrderAdmin(DjustModelAdmin):
    change_form_widgets = [OrderActivityWidget]
```

That's it.  Open `/admin/myapp/order/42/change/` and the widget renders
above the form, receives `object_id=42`, and behaves exactly like any
other LiveView.

### `change_list_widgets`

Same deal, but on the list page. Widgets registered here receive no
`object_id` (since the page isn't scoped to a single model instance).
Typical uses:

- Live KPI tiles (total orders today, open support tickets)
- Filter-aware summaries (sum of currently-shown rows)
- Pinned announcements / banners

```python
@site.register(Order)
class OrderAdmin(DjustModelAdmin):
    change_list_widgets = [TodayRevenueWidget, OpenTicketsWidget]
```

### Permissions

Any widget class can declare `permission_required`. Users without the
permission simply don't see the widget — no fallback placeholder is
rendered.

```python
class InventoryAlertWidget(LiveView):
    template_name = "myapp/admin/inventory_alert.html"
    permission_required = "inventory.view_low_stock"
    label = "Low stock alerts"
```

## Bulk-action progress

### Quick start — `@admin_action_with_progress`

```python
# myapp/djust_admin.py
from djust.admin_ext import DjustModelAdmin, site
from djust.admin_ext.progress import admin_action_with_progress
from .models import Order


@site.register(Order)
class OrderAdmin(DjustModelAdmin):
    actions = ["refund_selected"]

    @admin_action_with_progress(description="Refund selected orders")
    def refund_selected(self, request, queryset, progress):
        total = queryset.count()
        progress.update(current=0, total=total, message="Starting refunds…")
        for i, order in enumerate(queryset.iterator(), start=1):
            order.refund()
            progress.update(
                current=i,
                total=total,
                message=f"Refunded order #{order.pk}",
            )
        progress.update(message="All done.")
```

When a user runs this action:

1. The decorator pins the selected rows to a list of primary keys
   (so lazy-queryset re-evaluation against the session can't affect
   the thread).
2. Spawns a daemon thread that runs the function body.
3. Returns an `HttpResponseRedirect` to
   `/admin/djust-progress/<job_id>/`, which is served by
   `BulkActionProgressWidget`.
4. The progress page polls `progress.current / total / message / log`
   every 500 ms and re-renders the progress bar, status, and log.
5. If the user clicks **Cancel**, both `done=True` and
   `cancelled=True` flip on the job, and the polling loop exits on
   the next tick.

### Cancellation is cooperative

> **Important.** Clicking **Cancel** on the progress page only flips
> `progress.cancelled = True`. Python cannot safely interrupt a
> running thread mid-statement, so the **action body must
> periodically check `progress.cancelled` and return early** to
> actually stop. If your loop body never checks, the action runs to
> completion even though the user cancelled — and any destructive
> side-effects (row updates, API calls) still happen.
>
> The pattern:
>
> ```python
> @admin_action_with_progress(description="Sync with vendor")
> def sync_vendor(self, request, queryset, progress):
>     total = queryset.count()
>     for i, obj in enumerate(queryset):
>         if progress.cancelled:
>             progress.update(message="Cancelled by user.")
>             return
>         obj.sync_with_vendor()
>         progress.update(current=i + 1, total=total,
>                         message=f"Synced {obj.pk}")
> ```
>
> For actions that can't be safely interrupted, skip the check and
> make it clear in the `description`: `"Finalize orders (cannot be
> cancelled)"`.

### Permissions (`permissions=[...]`)

`@admin_action_with_progress(permissions=[...])` stamps an
`allowed_permissions` attribute on the wrapped action function.
**`DjustModelAdmin.run_action` enforces this server-side** — before
dispatching the action it calls `request.user.has_perms(allowed)` and
raises `PermissionDenied` if the user lacks any declared perm. This
closes the gap where Django's default `has_*_permission` methods
return `True` for any authenticated staff user, which would otherwise
let a view-only staff user fire a destructive action just because the
action dropdown rendered for them.

```python
@admin_action_with_progress(
    description="Refund selected orders",
    permissions=["orders.refund_order", "orders.view_order"],
)
def refund_selected(self, request, queryset, progress):
    ...
```

Users without *all* of the listed perms see a `403` when they try to
run the action; the progress page is never created.

### Known limitations

> **Single-worker only (v0.7.0).** The process-local `_JOBS` dict that
> backs `BulkActionProgressWidget` is not shared across workers. If
> your deployment has `gunicorn --workers 4` (or uvicorn with
> `--workers > 1`), the progress-page redirect may land on a different
> worker than the one running the background thread — producing a "Job
> not found or expired." error.
>
> **Workarounds for v0.7.0:**
>
> - Run a single ASGI worker (`--workers 1`) on the service handling
>   admin traffic.
> - Enable sticky sessions (cookie-affinity) on your load balancer so
>   the admin user stays on one worker.
> - Don't use `@admin_action_with_progress` for workflows that need to
>   survive worker crashes.
>
> **v0.7.1 plans** to back `_JOBS` with the project's channel layer
> (same broker as `NotificationMixin.listen()`), making multi-worker
> deploys work out of the box without changes to your action code.
> A `djust.A073` system check fires at startup any time an admin site
> has a `@admin_action_with_progress`-decorated action, so this
> limitation is impossible to miss during `manage.py check`.

Other edge cases worth knowing:

- **Single-worker `_JOBS` + LRU cap.** `_JOBS` is a process-local dict
  capped at `_MAX_JOBS = 500` entries (oldest entries evicted on insert
  once the cap is reached). Combined with the single-worker limitation
  above, this means you're always looking at the last 500 jobs on one
  worker — fine for typical admin bulk-action workloads, but don't
  treat `_JOBS` as a durable job store.
- **Per-message truncation.** Both `Job.message` and `Job.error` are
  individually truncated to `_MAX_MESSAGE_CHARS = 4096` characters on
  each `progress.update(...)` call — longer strings get `"..."`
  appended. Keeps the WebSocket payload and in-memory job size bounded
  no matter how noisy the action is.
- **Long-running actions keep running across user sessions** — closing
  the browser tab doesn't cancel the job, only `cancel()` does. Jobs
  live 30 seconds after completion so late-arriving progress pages can
  still see the final state.
- **Log is capped at 50 lines.** Call `progress.update(message=...)`
  sparingly; the oldest entries are trimmed.
- **Exceptions are captured** with full tracebacks logged at ERROR
  level via ``logger.exception`` (logger name:
  ``djust.admin_ext.progress``); `done=True` still flips in the
  `finally` clause. The user-facing `job.error` is a generic
  message — `"Action failed — see server logs for details"` — and the
  raw exception text lives only on the server-side `_error_raw`
  attribute (server-only, never sent to the client). Don't leak
  exception messages to admin users.
- **Queryset is pinned to PKs** before the thread starts. If you need
  the freshest data, re-fetch inside the thread: we already pin to the
  default manager's queryset but you can chain further filters.

## Troubleshooting

### `djust.A072` — non-LiveView in widget slot

```
Admin djust_admin — change_form_widgets on myapp.order contains
non-LiveView class 'NotALiveView'. Widget slots can only embed djust
LiveView subclasses.
```

You registered a plain class (or a stock `admin.ModelAdmin`-style
widget) where a `LiveView` subclass was expected. The fix is to make
your widget inherit from `djust.LiveView`:

```python
from djust import LiveView

class MyWidget(LiveView):
    template_name = "..."
```

Widget slots expect LiveViews because they're rendered via
`{% live_render %}` — the same tag used for nested LiveViews
elsewhere in your app. If you want a static server-rendered card, use
the dashboard `AdminWidget` (from `djust.admin_ext.plugins`) instead —
those are plain Django template renders.

### `djust.A073` — multi-worker progress limitation

```
Admin djust_admin — uses @admin_action_with_progress with
DJUST_ASGI_WORKERS=4. The v0.7.0 BulkActionProgressWidget keeps job
state in a process-local dict (_JOBS); multi-worker deploys must pin
the progress URL to the worker that started the job (sticky sessions)
or run a single ASGI worker. v0.7.1 will back this with a channel
layer.
```

An informational notice — NOT an error. A073 is gated on the
`DJUST_ASGI_WORKERS` setting: it only fires when you set
`DJUST_ASGI_WORKERS > 1` in your Django settings (so single-worker
development stays silent). Unset it or leave it at `1` to indicate
you're running a single worker.

```python
# settings.py
DJUST_ASGI_WORKERS = 4  # This tells djust.A073 you're multi-worker.
```

See the "Known limitations" section above for the options. You can
also silence this check with `DJUST_CONFIG = {"suppress_checks":
["A073"]}` once you've picked a mitigation.

### Defense-in-depth — `DJUST_LIVE_RENDER_ALLOWED_MODULES`

Because widget slots resolve dotted paths via `{% live_render %}`, you
can opt into an allowlist of acceptable module prefixes. Set
`DJUST_LIVE_RENDER_ALLOWED_MODULES = ["myapp.widgets", "myorg.admin"]`
in Django settings and any widget path that doesn't start with one of
those prefixes will raise `TemplateSyntaxError` at render time. When
unset (the default) all resolvable paths are permitted. This is not a
bug fix — just an extra layer for deployments that want to constrain
which modules the admin can reach through the live-render machinery.

## Comparison with stock Django admin

| Capability | Stock `admin.ModelAdmin` | `DjustModelAdmin` (v0.7.0) |
| --- | --- | --- |
| CRUD templates | full-page reload | real-time LiveView |
| Search / filter / sort | full-page reload | WebSocket round-trip |
| Add widgets to a change page | custom `change_form_template` + JS | `change_form_widgets = [LiveView, …]` |
| Add widgets to a list page | custom `change_list_template` + JS | `change_list_widgets = [LiveView, …]` |
| Bulk action with progress | none built-in | `@admin_action_with_progress` + live page |
| Permission-filter widgets | manual template conditional | `permission_required` on widget class |
| Multi-worker job routing | not applicable (no background jobs) | v0.7.0: single-worker; v0.7.1: channel layer |

v0.7.0 is intentionally a **small** addition to an already-shipped
admin.  If `DjustAdminSite` doesn't fit your project, you can still
reach for a custom admin app — `BulkActionProgressWidget` is a plain
LiveView and `admin_action_with_progress` is a plain decorator; both
work outside `DjustAdminSite` if you route their URLs manually.
