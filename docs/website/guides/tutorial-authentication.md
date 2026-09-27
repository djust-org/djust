---
title: "Tutorial: Auth-gated pages and per-handler permissions"
slug: tutorial-authentication
section: guides
order: 69
level: intermediate
description: "Wire login + role-based authorization into a LiveView with three primitives: LoginRequiredMixin for the page itself, get_object() + has_object_permission() for team membership, and @permission_required on each event handler. Plus the right way to handle 'session expired mid-WebSocket' so the user doesn't see a half-broken UI."
---

# Tutorial: Auth-gated pages and per-handler permissions

Every shipped LiveView eventually answers two questions: "can this
user see this page?" and "can this user perform this action?" The
two are different — a viewer might be allowed to *see* a team
admin page but only an admin can press the "Remove member" button
on it. Mixing them up leads to either a too-restrictive UI (forcing
admins to log in twice) or a too-permissive one (every viewer can
fire the admin event over the WebSocket).

djust separates them cleanly:

- **Page-level auth** — `LoginRequiredMixin` plus djust's
  object-level hooks (`get_object()` + `has_object_permission()`).
  Checked on the initial HTTP render, on the WebSocket mount, and
  again before every event.
- **Action-level auth** — `@permission_required("…")` on each
  event handler. Decided server-side before the handler runs.
  The user's role can change mid-session and the next event is
  re-checked.

By the end of this tutorial you'll have:

- A team admin page that **redirects anonymous visitors to /login/**
  before they see anything.
- The page **renders for any logged-in member** of the team — they
  can view the roster, see invite codes, etc.
- A "Remove member" button that's **only callable by admins** — even
  if a non-admin somehow fires the event from the console, the
  server rejects it.
- A graceful **"Your permissions changed"** message if the user's
  permissions change mid-session, with a Reload link that
  preserves their place.

| You'll learn | Documented in |
|---|---|
| `LoginRequiredMixin` + `get_object()` / `has_object_permission()` | [Authorization](authorization.md) |
| `@permission_required` per handler | [API: Decorators](../api-reference/decorators.md) |
| Mid-session permission revocation handling | This tutorial |

> **Prerequisites:** [Your First LiveView](../getting-started/first-liveview.md), Django auth
> already wired in your project (a `User` model, `/login/` URL,
> the `django.contrib.auth.middleware.AuthenticationMiddleware`
> in `MIDDLEWARE`). No login pages yet? djust ships themed sign-in,
> sign-up and password-reset pages; see [Accounts](accounts.md).
> Familiarity with Django permissions
> ([docs](https://docs.djangoproject.com/en/stable/topics/auth/default/#permissions-and-authorization))
> helps but isn't required.

---

## Step 1 — The model + permissions

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Team(models.Model):
    name = models.CharField(max_length=80)
    invite_code = models.CharField(max_length=32, unique=True)

    class Meta:
        permissions = [
            ("manage_team_members", "Can add or remove team members"),
        ]


class TeamMembership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[("member", "Member"), ("admin", "Admin")])

    class Meta:
        unique_together = [("team", "user")]
```

The `manage_team_members` Django permission is what we'll gate
the destructive button on. Assign it to admins via a Django
signal, the admin site, or your team-creation flow.

---

## Step 2 — The view: page-level auth

```python
# myapp/views.py
from djust import LiveView, state
from djust.auth import LoginRequiredMixin
from djust.decorators import event_handler, permission_required

from .models import Team, TeamMembership


class TeamAdminView(LoginRequiredMixin, LiveView):
    template_name = "team_admin.html"
    login_url = "/login/"  # Where to send anonymous users

    error = state("")

    def mount(self, request, team_id: int = 0, **kwargs):
        self.team_id = team_id

    def get_object(self):
        return Team.objects.get(pk=self.team_id)

    def has_object_permission(self, request, obj):
        # Logged in is not enough: the user must belong to this team.
        return obj.memberships.filter(user=request.user).exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        team = self._object  # fetched and verified for this render
        if team is not None:
            context["team_name"] = team.name
            context["invite_code"] = team.invite_code
            context["members"] = [
                {"id": m.id, "username": m.user.username, "role": m.role}
                for m in team.memberships.select_related("user").order_by("user__username")
            ]
        return context
```

Two complementary checks, both run by the framework:

| Check | Who handles it | What happens on failure |
|---|---|---|
| `request.user.is_authenticated` | `LoginRequiredMixin` (`djust.auth`) | HTTP 302 → `/login/?next=/teams/<id>/` |
| `has_object_permission()` | Our membership rule | HTTP 403 on the page load; the WebSocket mount is refused with close code 4403 |

`LoginRequiredMixin` from `djust.auth` sets `login_required = True`, which
djust checks before `mount()` on the HTTP render and again on the WebSocket
mount. (Django's own `django.contrib.auth.mixins.LoginRequiredMixin` is
honoured on both paths too.) `get_object()` and `has_object_permission()` run
after `mount()`, because `get_object()` reads the `team_id` that `mount()`
stored, and they run again before every event, so a user removed from the
team loses access on their next click. A missing team (`Team.DoesNotExist`)
is treated as "no object" rather than a 403, so the response doesn't reveal
which team ids exist; `self._object` is then `None`, which is why
`get_context_data()` checks it. See
[Authorization](authorization.md) for the full four-layer model.

---

## Step 3 — The view: action-level auth on the handler

```python
class TeamAdminView(LoginRequiredMixin, LiveView):
    # ... mount(), get_object() and friends as above ...

    @event_handler
    @permission_required("myapp.manage_team_members")
    def remove_member(self, member_id: int = 0, **kwargs):
        if not member_id:
            return
        try:
            membership = self._object.memberships.get(id=member_id)
        except TeamMembership.DoesNotExist:
            self.error = "That member is no longer on the team."
            return
        # Don't let an admin remove themselves into a teamless state.
        if membership.user_id == self.request.user.id:
            self.error = "You can't remove yourself from the team."
            return
        membership.delete()
        self.error = ""
```

Three things to call out:

1. **`@permission_required("myapp.manage_team_members")`** is checked
   server-side BEFORE the handler runs. A non-admin who fires
   this event from the JS console gets a "Permission denied" error
   from the framework — `remove_member` is never invoked, no DB
   changes happen.
2. **Decorator order doesn't change behaviour.** `@permission_required`
   only records metadata that the dispatcher reads, so either order
   works. By convention `@event_handler` goes outside and
   `@permission_required` inside (closer to `def`).
3. **Self-protection** (`membership.user_id == self.request.user.id`) is
   business logic, not auth — it stays inside the handler. The
   framework's auth layer doesn't know about your team rules.

The member list re-renders from `get_context_data()` after the handler,
so the removed row disappears without any manual refresh.

> **Name collision.** If the view also sets the class attribute
> `permission_required = "..."` (the view-level check), that string
> shadows the decorator inside the class body. Import the decorator
> under another name in that case:
> `from djust.decorators import permission_required as require_permission`.

---

## Step 4 — The template: hide the button for non-admins

```html
<!-- myapp/templates/team_admin.html -->
<section class="team-admin" dj-root>
  <header>
    <h1>{{ team_name }}</h1>
    <p>Invite code: <code>{{ invite_code }}</code></p>
  </header>

  {% if error %}
    <p role="alert" class="err">{{ error }}</p>
  {% endif %}

  <table class="members">
    <thead>
      <tr>
        <th>Member</th>
        <th>Role</th>
        <th aria-hidden="true"></th>
      </tr>
    </thead>
    <tbody>
      {% for member in members %}
        <tr>
          <td>{{ member.username }}</td>
          <td>{{ member.role|capfirst }}</td>
          <td>
            {% if perms.myapp.manage_team_members %}
              <button
                type="button"
                dj-click="remove_member"
                data-member-id:int="{{ member.id }}"
                class="btn-danger"
              >Remove</button>
            {% endif %}
          </td>
        </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
```

The `{% if perms.myapp.manage_team_members %}` is a Django
template feature — `perms` is auto-injected by
`django.contrib.auth.context_processors.auth`. **It hides the
button visually for non-admins, but it does NOT enforce auth.**
The `@permission_required` decorator on the handler is what
actually blocks the action; the template guard is a UX nicety
(don't taunt the user with a button that wouldn't work).

> **Defence in depth.** Always have BOTH: the template guard
> for UX, the decorator for actual security. Without the decorator,
> any non-admin can trigger the event by typing a few lines into
> their console — the template hide is browser-controlled.

---

## Step 5 — Mid-session permission revocation

The hard case: a user loaded the page as an admin, then was demoted
mid-session by another admin. The "Remove" button is still visible
in their DOM (the page hasn't re-rendered) but the server now
rejects their `remove_member` events.

The handler never runs, so there is no server-side hook to catch
this in: the framework rejects the event before dispatch and sends a
`{"type": "error", "error": "Permission denied"}` frame back. The
client turns every error frame into a `djust:error` window event,
so listen for that and show a banner:

```html
<!-- in your base template, after the dj-root element -->
<div id="perm-revoked" class="banner banner-warn" role="alert" hidden>
  Your permissions on this team have changed.
  <a href="{{ request.path }}">Reload</a> to see the updated view.
</div>
<script>
  window.addEventListener("djust:error", (e) => {
    if (e.detail && e.detail.error === "Permission denied") {
      document.getElementById("perm-revoked").hidden = false;
    }
  });
</script>
```

Keep the banner and the script outside the `dj-root` element: the
reactive root is re-rendered from the server, which would reset the
`hidden` attribute, and a `<script>` inside it is not re-executed
after a morph.

Now when the demoted admin clicks Remove, instead of a silent
fail, they see a clear banner explaining what happened. Reloading
the page re-runs `mount()`, the up-to-date member list renders,
and the (now hidden) Remove button isn't shown.

A member removed from the team entirely is handled by Step 2 instead:
`has_object_permission()` re-runs before their next event and denies
it with a `permission_denied` error frame.

---

## What just happened, end to end

```
   Anonymous visitor                      Member (non-admin)              Admin
        │                                       │                            │
        │ GET /teams/42/                        │                            │
        │ ─────────────►                        │                            │
        │ ◄ 302 /login/?next=...                │                            │
        │ (LoginRequiredMixin)                  │                            │
        │                                       │                            │
        │                                       │ GET /teams/42/             │
        │                                       │ ─────────────►             │
        │                                       │ membership check passes    │
        │                                       │ ◄ render page              │
        │                                       │   (no Remove buttons,      │
        │                                       │    template-hidden)        │
        │                                       │                            │
        │                                       │                            │ GET /teams/42/
        │                                       │                            │ ─────────────►
        │                                       │                            │ ◄ render page
        │                                       │                            │   (Remove buttons
        │                                       │                            │    visible)
        │                                       │                            │
        │                                       │                            │ click Remove
        │                                       │                            │ ─────────────►
        │                                       │                            │ @permission_required ✓
        │                                       │                            │ membership.delete()
        │                                       │                            │ ◄ patch: row gone
        │                                       │                            │
        │                                       │ console: dispatch          │
        │                                       │ ('remove_member', {id})    │
        │                                       │ ─────────────►             │
        │                                       │ @permission_required ✗     │
        │                                       │ ◄ "Permission denied"      │
        │                                       │   (no DB change)           │
```

The non-admin can fire the event from the console, but the server
refuses to run the handler. Defense at the layer that matters.

---

## Where to go next

- **Per-team roles:** `@permission_required` checks Django's
  permission strings, which are usually granted globally. For "this
  admin can manage *this* team but not that one," check the
  membership role inside the handler (`self._object` is the team
  `has_object_permission()` just verified):
  <!-- doc-snippet-check: skip -->
  ```python
  membership = self._object.memberships.get(user=self.request.user)
  if membership.role != "admin":
      raise PermissionDenied
  ```
- **Rate-limit destructive actions:** stack
  `@rate_limit(rate=0.1, burst=5)` (a burst of 5, then one every
  10 seconds; see the [optimistic-updates
  tutorial](tutorial-optimistic-updates.md)) so a compromised admin
  account can't bulk-delete members in a script.
- **Audit log:** wrap `remove_member` in a `with audit_log(...):`
  context manager that writes to a separate audit table — the
  who/what/when of each destructive action, separate from the
  business data.
- **Two-factor for high-stakes actions:** for true destructive
  ops (delete team, transfer ownership), require a fresh password
  re-confirm in a modal before firing the event. Pair with the
  [multi-step wizard tutorial](tutorial-multi-step-wizard.md)
  pattern.

The recipe (`LoginRequiredMixin` plus `has_object_permission()` for
page-level, `@permission_required` for action-level, template
`{% if perms.X %}` for UX-only hide) is the entire auth surface for ~95% of LiveViews.
Everything beyond that is business-logic checks inside the handler
body — and those are just regular Python.
