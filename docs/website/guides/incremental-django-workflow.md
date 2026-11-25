---
title: "Adding djust to an existing Django app, one piece at a time"
slug: incremental-django-workflow
section: guides
order: 39
level: intermediate
description: "Start from a plain Django list view and convert it step by step: a URL-backed filter and pagination, a detail page, a validated form, permissions, loading and error states, and what survives a reconnect."
---

# Adding djust to an existing Django app, one piece at a time

You do not need a rewrite to use djust. This guide starts with an ordinary
Django model and a plain list view, and converts one piece per step. After each
step the app still works and you can stop there.

Everything below is excerpted from a running example,
[`examples/demo_project/djust_workflow`](https://github.com/djust-org/djust/tree/main/examples/demo_project/djust_workflow),
served at `/workflow/` by the demo project. Its tests are in
`tests/integration/test_incremental_workflow_3659.py`, and one of them fails if a
snippet here stops matching the example's source.

The example is a small ticket tracker:

| Page | URL | Step |
| --- | --- | --- |
| List with filter and pagination | `/workflow/?status=open&q=vpn&page=2` | 2 |
| Detail, with a "close" action | `/workflow/<pk>/` | 4, 6 |
| Create and edit | `/workflow/new/`, `/workflow/<pk>/edit/` | 5 |

## Step 0: the plain Django you already have

A model, and a list view that reads the filter from `request.GET`:

<!-- example: models.py -->
```python
class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
```

```python
from django.core.paginator import Paginator
from django.shortcuts import render

from .models import Ticket


def ticket_list(request):
    tickets = Ticket.objects.all()
    if request.GET.get("status") in ("open", "closed"):
        tickets = tickets.filter(status=request.GET["status"])
    page = Paginator(tickets, 5).get_page(request.GET.get("page"))
    return render(request, "tickets/list.html", {"page": page})
```

Nothing in the model knows about djust, and nothing in it changes in this guide
except one queryset helper in step 4.

## Step 1: install djust and keep the page working

Add `channels` and `djust` to `INSTALLED_APPS`, route the WebSocket with
`djust.routing` as the [installation guide](../getting-started/installation.md) shows, and
load `{% load live_tags %}` plus `{% djust_client_config %}` in your base
template. The example's base template does this:

<!-- example: templates/djust_workflow/base.html -->
```html
{% load live_tags %}
```

No page changes yet. Convert pages only when you want them interactive.

## Step 2: the list view, with the filter and page in the URL

Describe the query string as a Django form. It is the contract for the page's
URL, and it is the only place the values are parsed:

<!-- example: forms.py -->
```python
class TicketListParams(forms.Form):
    """The list page's query string: what ``?status=open&q=vpn&page=2`` may say."""

    status = forms.ChoiceField(
        choices=[("all", "All"), ("open", "Open"), ("closed", "Closed")],
        required=False,
        initial="all",
    )
    q = forms.CharField(max_length=100, required=False, initial="")
    page = forms.IntegerField(min_value=1, required=False, initial=1)
```

Then turn the function into a `LiveView`. The filter and page become `state()`
fields filled from the URL by `handle_params()`, and the query runs when the page
renders, in `get_context_data()`:

<!-- example: views.py -->
```python
class TicketListView(LiveView):
    template_name = "djust_workflow/ticket_list.html"
    login_required = True
    permission_required = "djust_workflow.view_ticket"

    # The URL is the source of truth: these three come from the query string.
    params_form = TicketListParams
    params_form_fallback = "fields"
    status = state("all")
    q = state("")
    page = state(1)
    # Declared so the messages reach the template: an undeclared attribute is not rendered.
    params_errors = state(default_factory=dict)

    def handle_params(self, params, uri):
        self.status = params["status"]
        self.q = params["q"]
        self.page = params["page"]
```

`handle_params()` runs on the first page load, on every `live_patch()` and on the
browser's back and forward buttons, so the same code reads the URL in all three.
`params` is the form's `cleaned_data`: `page` is an `int`, `status` is one of the
three choices. `?page=abc` does not raise. The form rejects it, the page renders
its defaults (with `params_form_fallback = "fields"` a valid `status` in the same
URL is kept), and the template gets the messages in `params_errors`. Declare
`params_errors` with `state()`, as above: djust 2.0 renders only declared state.

Now the filter form. A handler does not set the state itself. It changes the
URL, and `handle_params()` reads the new URL:

<!-- example: views.py -->
```python
class TicketListView(LiveView):
    # ...
    @require_permission("djust_workflow.view_ticket")
    @event_handler()
    def apply_filter(self, status: str = "all", q: str = ""):
        # Only the URL changes here. The browser then reports the new URL and
        # ``handle_params`` validates it, so a typed ``?page=abc`` and a form
        # submit go through the same declaration.
        self.live_patch(params={"status": status, "q": q.strip(), "page": 1})
```

The handler repeats the view's permission because `djust_check` (S009) asks for a
gate on every public handler; see step 4 for the decorator. A new filter returns
to page 1, which is why the handler sets `"page": 1`.
`get_context_data()` builds the visible page from the state:

<!-- example: views.py -->
```python
class TicketListView(LiveView):
    # ...
    def get_context_data(self, **kwargs):
        # ...
        page = Paginator(tickets, PAGE_SIZE).get_page(self.page)
        context.update(
            tickets=list(page.object_list),
            page_number=page.number,
            num_pages=page.paginator.num_pages,
            total=page.paginator.count,
            prev_page=page.number - 1 if page.has_previous() else None,
            next_page=page.number + 1 if page.has_next() else None,
            # ...
        )
```

`get_page()` clamps a page number past the end to the last page, as in plain
Django. The template uses `dj-patch` links for pagination (they change the
query string and keep the view mounted), `dj-navigate` for links to other live
pages, and a `dj-submit` form for the filter:

<!-- example: templates/djust_workflow/ticket_list.html -->
```html
    <form dj-submit="apply_filter">
        <input type="search" name="q" value="{{ q }}" placeholder="Search tickets" aria-label="Search tickets">
```

<!-- example: templates/djust_workflow/ticket_list.html -->
```html
        {% if prev_page %}<a href="?status={{ status|urlencode }}&q={{ q|urlencode }}&page={{ prev_page }}" dj-patch>Previous</a>{% endif %}
```

Because every link is a real URL, copy and paste, reload, middle-click and the
back button all work. The test `test_url_change_is_back_forward` steps a view
from one query string to another the way a popstate does.

## Step 3: loading feedback

A `dj-loading.*` attribute covers the round trip of an event: it starts when the
event is sent and ends when the server answers.

<!-- example: templates/djust_workflow/ticket_list.html -->
```html
        <button type="submit" dj-loading.disable>Filter</button>
        <span class="loading muted" dj-loading.show>Loading...</span>
```

`dj-loading.disable` stops a double submit, and `dj-loading.show` reveals the
span (the example's `.loading` class hides it otherwise). For work that outlives
the response, drive the indicator from state and `start_async()` instead; see
[loading states](loading-states.md).

## Step 4: a detail page and permissions

Permissions are declared on the view. The checks run before `mount()`, on every
transport, so a socket cannot open a page the HTTP request would refuse:

<!-- example: views.py -->
```python
class TicketDetailView(LiveView):
    template_name = "djust_workflow/ticket_detail.html"
    login_required = True
    permission_required = "djust_workflow.view_ticket"
```

An anonymous visitor is sent to the login page; a signed-in user without
`view_ticket` is refused with a 403. That is the role check. The object check says
*which* tickets a user may open. The queryset helper in `models.py` is the only
change to the model layer:

<!-- example: models.py -->
```python
class TicketQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Staff with ``change_ticket`` see every ticket; everyone else sees their own."""
        if user.has_perm("djust_workflow.change_ticket"):
            return self
        return self.filter(created_by=user)
```

The list uses it to scope what it shows, and the detail view uses the same rule
through `get_object()` and `has_object_permission()`. djust runs both at mount
and again before every event, so access revoked while the page is open stops the
next click:

<!-- example: views.py -->
```python
class TicketDetailView(LiveView):
    # ...
    def get_object(self):
        return Ticket.objects.select_related("created_by").get(pk=self.ticket_pk)

    def has_object_permission(self, request, obj):
        return Ticket.objects.visible_to(request.user).filter(pk=obj.pk).exists()
```

A handler can need more than the page does. Closing a ticket needs
`change_ticket`; the decorator is checked before the handler runs:

<!-- example: views.py -->
```python
class TicketDetailView(LiveView):
    # ...
    @require_permission("djust_workflow.change_ticket")
    @event_handler()
    def close_ticket(self):
        self.error_message = ""
```

The example imports the decorator as `require_permission`. Inside a class that
sets the attribute `permission_required = "..."`, the plain name
`permission_required` is the string, not the decorator.

Still, hide what the user may not do. Rendering a button only for people who can
use it is for convenience; the checks above are the control:

<!-- example: views.py -->
```python
class TicketDetailView(LiveView):
    # ...
    def get_context_data(self, **kwargs):
    # ...
        context["can_close"] = self.request.user.has_perm("djust_workflow.change_ticket")
```

See [authorization](authorization.md) for the four layers.

## Step 5: create and edit forms with server validation

Keep your Django `ModelForm`. Leave out fields the browser must not set
(`created_by`, `status`). The rules live in the form, so they run on the server
for every submission, and `FormMixin` also runs them per field as the user
tabs away:

<!-- example: forms.py -->
```python
class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        # ``created_by`` and ``status`` are left out so a browser cannot set them.
        fields = ["title", "body", "priority"]

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if len(title) < 5:
            raise forms.ValidationError("Use at least 5 characters.")
        duplicates = Ticket.objects.filter(title__iexact=title, status=Ticket.Status.OPEN)
```

The same file has a cross-field rule in `clean()` (an urgent ticket needs real
detail), covered by `test_a_cross_field_rule_and_a_database_rule`.

Creating uses `FormMixin`. `form_valid()` runs only for a valid form, and saving
is yours to write, so the owner comes from the request, not the form:

<!-- example: views.py -->
```python
class TicketCreateView(FormMixin, LiveView):
    template_name = "djust_workflow/ticket_form.html"
    login_required = True
    permission_required = "djust_workflow.add_ticket"
    form_class = TicketForm

    def form_valid(self, form):
        ticket = form.save(commit=False)
        ticket.created_by = self.request.user
        ticket.save()
        self.live_redirect(reverse("djust_workflow:detail", kwargs={"pk": ticket.pk}))

    def form_invalid(self, form):
        self.error_message = "Please correct the errors below."
```

Editing one existing record uses `ModelFormMixin`, which looks the record up from
the URL and checks access before the form exists and again on each event. Scope
`get_queryset()` so a user cannot edit a record by guessing its id:

<!-- example: views.py -->
```python
class TicketEditView(ModelFormMixin[Ticket], LiveView):
    template_name = "djust_workflow/ticket_form.html"
    login_required = True
    permission_required = "djust_workflow.change_ticket"
    model = Ticket
    form_class = TicketForm

    def get_queryset(self):
        return Ticket.objects.visible_to(self.request.user)
```

Both pages share one template. Errors come from `field_errors`, and each field
sits next to its message:

<!-- example: templates/djust_workflow/ticket_form.html -->
```html
    <form dj-submit="submit_form">
        {% csrf_token %}
        <p>
            <label>Title <input name="title" value="{{ form_data.title }}" dj-change="validate_field"></label>
            {% if field_errors.title %}<span class="error">{{ field_errors.title.0 }}</span>{% endif %}
        </p>
```

Keep `{% csrf_token %}`: it is what the HTTP fallback transport (below) verifies.

## Step 6: an error the user can see

Something will eventually fail: a mail server, a stale page. Catch what you can
recover from and put a message in a `state()` field, rendered next to the action.
The example closes a ticket and then notifies the requester. If the notification
fails, the database change rolls back and the page says so:

<!-- example: views.py -->
```python
class TicketDetailView(LiveView):
    # ...
    def close_ticket(self):
    # ...
        try:
            with transaction.atomic():
                ticket = Ticket.objects.select_for_update().get(pk=self.ticket_pk)
                if ticket.status == Ticket.Status.CLOSED:
                    self.error_message = "This ticket was already closed."
                    return
                ticket.status = Ticket.Status.CLOSED
                ticket.save(update_fields=["status"])
                notify_requester(ticket)
        except NotificationError:
            # The transaction rolled back, so the ticket is still open.
            self.error_message = "The requester could not be notified, so the ticket stays open."
            return
        self.success_message = "Ticket closed."
```

<!-- example: templates/djust_workflow/ticket_detail.html -->
```html
    {% if error_message %}<p class="error" role="alert">{{ error_message }}</p>{% endif %}
    {% if success_message %}<p class="ok" role="status">{{ success_message }}</p>{% endif %}
```

`role="alert"` makes a screen reader announce the failure. The second case in
that handler, a ticket already closed in another tab, is a stale page, not a
bug. Both are tested (`test_a_failing_notification_rolls_back_and_shows_the_error`,
`test_closing_twice_shows_the_stale_state_error`). An exception you do not catch
is not shown to the user as your message; djust reports it through its error
handling and, in development, the error overlay, so catch the ones you can
explain.

## What survives a reconnect

A reconnect is a new connection and a fresh `mount()`. What the page looks like
afterwards depends on where each piece of state lives:

| State | After a reconnect | Why |
| --- | --- | --- |
| Filter, search text, page | Same | They are in the URL. The browser reconnects at the same address, and `handle_params()` reads it again. `test_a_reconnect_at_the_same_url_shows_the_same_page` mounts twice at one URL. |
| The ticket on a detail page | Same | The URL's id is passed to `mount()`, and the ticket is read from the database on each render. |
| Permissions | Re-checked | `login_required`, `permission_required` and `has_object_permission()` run on every mount, so a user whose access ended while offline is refused. |
| Typed but unsaved form input | Restored | `FormMixin` input and errors are saved with the view's state and the client restores field values that differ from the defaults. Password-like fields are not kept. |
| `error_message` and `success_message` | Kept while saved state holds them | They are ordinary `state()` fields, saved with the view. A handler that set one is not re-run, so a message can outlive the moment it described. Clear them at the start of the next action, as `close_ticket` does. |
| A save or close in flight | May be lost | An event that was not answered before the connection dropped is not replayed. The page shows the database's state after the reconnect, so check it instead of assuming. |

The saved state is kept in the Django session, so a deployment with several
processes needs a session engine and a channel layer that every process shares.
[What survives a reconnect, a restart and a second process](state-survival.md)
lists each kind of state, and [reconnection](reconnection.md) covers the banner,
backoff and form recovery. When the WebSocket is unavailable, the client sends
the event to the page's URL as a CSRF-protected JSON request; this is why the
form keeps `{% csrf_token %}` (see [http-only mode](http-only-mode.md)).

## Testing each step

`LiveViewTestClient` mounts the view with a user, sends events and reads state,
without a browser. The example's tests are the map of what to assert:

| Behavior | Test |
| --- | --- |
| Filter and page from the URL | `test_filter_and_page_come_from_the_url` |
| Back and forward | `test_url_change_is_back_forward` |
| A bad query string | `test_a_bad_query_string_is_a_message_not_a_500` |
| Anonymous, and no permission | `test_anonymous_is_sent_to_login`, `test_a_user_without_view_ticket_is_denied` |
| Someone else's object | `test_detail_denies_somebody_elses_ticket` |
| Validation errors | `test_create_validates_on_the_server` |
| The error state | `test_a_failing_notification_rolls_back_and_shows_the_error` |

```bash
PYTHONPATH=python python -m pytest tests/integration/test_incremental_workflow_3659.py -q
```

Passing tests do not prove a reconnect: that needs a real browser and a real
socket drop. Check it yourself with the demo project (`make start`, then open
`/workflow/`): stop the server mid-edit, start it again, and watch the form
refill.

## Where to go next

- [Navigation](navigation.md): `live_patch`, `live_redirect`, `params_form`.
- [Forms](forms.md): `FormMixin`, `ModelFormMixin`, live field rendering.
- [Authorization](authorization.md): the four-layer onion.
- [Loading states](loading-states.md): `dj-loading.*` and background work.
- [State survival](state-survival.md): what a reconnect or a second process keeps.
