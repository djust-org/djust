---
title: "Tutorial: Push live UI updates from a webhook (Stripe, GitHub, etc.)"
slug: tutorial-webhook-push
section: guides
order: 51
level: advanced
description: "Receive a webhook from Stripe (or GitHub, or any external service) and surface the event in connected users' dashboards within ~50ms — no polling, no client-side fetching. Uses push_to_view() to ship state updates from a plain Django view straight into every connected LiveView session."
---

# Tutorial: Push live UI updates from a webhook (Stripe, GitHub, etc.)

External services don't speak WebSocket. Stripe POSTs to your
`/webhooks/stripe/` endpoint when a payment succeeds. GitHub
POSTs to `/webhooks/github/` when a PR opens. Slack POSTs to
`/slack/events/` when someone reacts. Each one is a regular HTTP
request hitting a regular Django view.

The interesting question: **how does a payment-succeeded webhook
update the dashboard for the customer who just paid?** They're
already on `/dashboard/`, watching their balance. Polling every
5 seconds is wasteful. Refreshing on `visibilitychange` is
laggy. The right shape is **push the update from the webhook
view straight into their open LiveView session.**

djust ships **`push_to_view()`** for exactly this. Any backend
code (a webhook view, a Celery task, a management command, a
Django signal) can push state updates or fire handlers on every
connected LiveView of a given class, or, with `scope=`, only on the
sessions that joined that scope. The framework routes through
Channels.

By the end of this tutorial you'll have:

- A **`/webhooks/stripe/`** endpoint that verifies Stripe's
  signature and processes a `payment_intent.succeeded` event.
- An **`AccountView`** LiveView showing the user's balance.
- When the webhook fires, **the customer's open dashboard
  updates within ~50 ms** — no polling, no fetch, no manual
  refresh.
- The **same pattern applied to a GitHub `pull_request`
  webhook** that flips a PR's status badge in real time.

| You'll learn | Documented in |
|---|---|
| `push_to_view(view_path, state={...})` for state updates | [Server Push](../advanced/server-push.md) |
| `push_to_view(view_path, handler="handle_...", payload={...})` for handler invocations | [Server Push](../advanced/server-push.md) |
| `push_scope` + `scope=` to reach one customer's dashboard, not all | [Server Push](../advanced/server-push.md) |
| Webhook signature verification (the security part) | This tutorial |

> **Prerequisites:** [Installation](../getting-started/installation.md), the [streaming
> AI tutorial](tutorial-streaming-ai.md) (sets up the
> background-work mental model), and a Django project with
> Channels configured (which you already have if you're running
> djust). For Stripe specifically, a [Stripe test account](https://dashboard.stripe.com/)
> and the `stripe` Python SDK.

---

## Step 1 — The models and the LiveView (no webhook code yet)

Two models: the account whose balance the dashboard shows, and a record
of every webhook event already applied, which Step 2 uses to ignore
retries.

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Account(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    balance_cents = models.BigIntegerField(default=0)


class ProcessedWebhookEvent(models.Model):
    # The provider's event id (Stripe's evt_..., GitHub's delivery id).
    # unique=True is what makes a replayed delivery a no-op.
    event_id = models.CharField(max_length=255, unique=True)
    received_at = models.DateTimeField(auto_now_add=True)
```

The view:

```python
# myapp/views.py
from djust import LiveView, state

from .models import Account


class AccountView(LiveView):
    template_name = "account.html"
    login_required = True  # anonymous visitors are sent to login_url
    login_url = "/login/"

    balance_cents = state(0)
    last_event = state("")

    def mount(self, request, **kwargs):
        account = Account.objects.get(owner=request.user)
        self.balance_cents = account.balance_cents
        # This session receives scoped pushes for its own account only.
        self.push_scope = f"account-{account.id}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["balance"] = f"{self.balance_cents / 100:.2f}"
        return context

    def handle_balance_changed(self, new_balance_cents: int = 0, description: str = "", **kwargs):
        """Called when push_to_view targets this view with
        handler="handle_balance_changed". The webhook view fires this."""
        self.balance_cents = new_balance_cents
        self.last_event = description or "Balance updated"
```

`handle_balance_changed` is a plain method, not an `@event_handler`,
so a client click can't call it. It's invoked by
`push_to_view(handler="handle_balance_changed", payload={...})`,
and the payload's keys arrive as keyword arguments. A pushed handler
must start with `handle_` (or be an `@event_handler`); djust blocks
any other name. That's the distinction: `@event_handler` for
client-driven events; `handle_*` methods for server-driven
(push-driven) events.

---

## Step 2 — The webhook view

<!-- The stripe package is a third-party dependency the doc checker
     does not install. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/webhooks.py
import json
import logging

import stripe
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from djust import push_to_view

from .models import Account, ProcessedWebhookEvent

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


@csrf_exempt   # Webhooks come from Stripe, not your form. CSRF
@require_POST  # cookie won't be present. Verify the signature instead.
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.SignatureVerificationError):
        return HttpResponseBadRequest("Invalid signature")

    if event["type"] != "payment_intent.succeeded":
        return HttpResponse(status=200)

    intent = event["data"]["object"]
    amount = intent["amount"]  # in cents
    try:
        account_id = int(intent["metadata"].get("account_id", ""))
    except (TypeError, ValueError):
        # A 4xx/5xx makes Stripe retry for days; this event will never
        # succeed, so log it and acknowledge it.
        logger.warning("Stripe event %s has no usable account_id", event["id"])
        return HttpResponse(status=200)

    with transaction.atomic():
        # Stripe delivers at least once and retries on timeouts. Recording
        # the event id in the same transaction as the credit makes a
        # retry a no-op instead of a second credit.
        _, created = ProcessedWebhookEvent.objects.get_or_create(event_id=event["id"])
        if not created:
            return HttpResponse(status=200)
        account = Account.objects.select_for_update().filter(pk=account_id).first()
        if account is None:
            logger.warning(
                "Stripe event %s names unknown account %s", event["id"], account_id
            )
            return HttpResponse(status=200)
        account.balance_cents += amount
        account.save(update_fields=["balance_cents"])

    # Push to the AccountView sessions of this account only
    push_to_view(
        "myapp.views.AccountView",
        handler="handle_balance_changed",
        scope=f"account-{account.id}",
        payload={
            "new_balance_cents": account.balance_cents,
            "description": f"Payment received: ${amount / 100:.2f}",
        },
    )
    return HttpResponse(status=200)
```

Five things to call out:

1. **`@csrf_exempt`** is required because the request comes from
   Stripe, not your form. Don't worry — `stripe.Webhook.construct_event`
   verifies the cryptographic signature, which is stronger than
   CSRF. Same applies to GitHub webhooks (HMAC-SHA256 in
   `X-Hub-Signature-256`), Slack (`X-Slack-Signature`), etc.
2. **`push_to_view(view_path, handler=..., payload=..., scope=...)`**
   is the one new API. It sends one Channels group message; every
   connected `AccountView` session in that scope receives it, and
   the framework calls the named handler with the payload as keyword
   arguments, then re-renders and sends the patch.
3. **`scope=` picks the sessions.** Each `AccountView` joined
   `account-<id>` in `mount()` by setting `push_scope`, so the push
   reaches only the paying customer's open dashboards. Without
   `scope`, it would reach every `AccountView` session.
4. **Idempotency.** Stripe delivers each event at least once and
   retries when your endpoint is slow or fails, so the same
   `payment_intent.succeeded` can arrive twice. The
   `ProcessedWebhookEvent` row is written in the same transaction as
   the credit, and its `unique` event id makes a second delivery (even a
   concurrent one) find the existing row and return without crediting
   again.
5. **Always acknowledge what you can't process.** Any non-2xx response
   makes Stripe retry. An event naming an account you don't have will
   never succeed, so the view logs it and returns 200 instead of
   raising `DoesNotExist` (a 500 Stripe would keep retrying).

---

## Step 3 — Wire the URL

```python
# myapp/urls.py
from django.urls import path
from .views import AccountView
from .webhooks import stripe_webhook

urlpatterns = [
    path("account/", AccountView.as_view()),
    path("webhooks/stripe/", stripe_webhook, name="stripe_webhook"),
]
```

The webhook URL needs to be reachable from the public internet.
For local dev: use `stripe listen --forward-to localhost:8000/webhooks/stripe/`
or [ngrok](https://ngrok.com/) to tunnel.

---

## Step 4 — The template (almost incidental)

```html
<!-- myapp/templates/account.html -->
<div dj-root>
<section class="account">
  <h1>Your account</h1>
  <p class="balance">
    Balance: <strong>${{ balance }}</strong>
  </p>
  {% if last_event %}
    <p class="last-event" role="status">{{ last_event }}</p>
  {% endif %}
</section>
</div>
```

`dj-root` marks the region djust keeps live; without it the page renders
once and never connects, so pushes have nowhere to land. `balance` is the dollars string `get_context_data()` builds from
`balance_cents`. The user opens `/account/`, sees
their current balance. They make a payment in another tab (or
elsewhere). Stripe POSTs to `/webhooks/stripe/`. The webhook
view persists + pushes. Within ~50 ms, the balance on the open
dashboard updates and the "last_event" line shows "Payment
received: $19.99."

---

## Step 5 — Try it locally

In one terminal: run djust dev server.
In another: forward Stripe webhooks:

```bash
stripe listen --forward-to localhost:8000/webhooks/stripe/
# → outputs the webhook signing secret; put that in settings.STRIPE_WEBHOOK_SECRET
```

In a third terminal: trigger a test payment:

```bash
stripe trigger payment_intent.succeeded \
    --add payment_intent:metadata.account_id=1
```

Watch the connected browser. The balance line updates. No reload,
no manual fetch.

---

## Same pattern, different webhook

GitHub `pull_request.opened` → flip a "PRs awaiting review" badge:

<!-- Continues myapp/webhooks.py from Step 2, whose imports it uses. -->
<!-- doc-snippet-check: skip -->
```python
# myapp/webhooks.py
import hashlib, hmac
from django.conf import settings


def _verify_github(request) -> bool:
    sig = request.META.get("HTTP_X_HUB_SIGNATURE_256", "")
    expected = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(),
        request.body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


@csrf_exempt
@require_POST
def github_webhook(request):
    if not _verify_github(request):
        return HttpResponseBadRequest("Invalid signature")

    event_type = request.META.get("HTTP_X_GITHUB_EVENT", "")
    delivery_id = request.META.get("HTTP_X_GITHUB_DELIVERY", "")
    payload = json.loads(request.body)

    if event_type == "pull_request" and payload.get("action") == "opened":
        # GitHub redelivers too; skip a delivery id we've already handled.
        if delivery_id:
            _, created = ProcessedWebhookEvent.objects.get_or_create(
                event_id=f"github:{delivery_id}"
            )
            if not created:
                return HttpResponse(status=200)
        repo = payload["repository"]["full_name"]
        pr_number = payload["number"]
        push_to_view(
            "myapp.views.RepoDashboardView",
            handler="handle_pr_opened",
            scope=repo,  # dashboards that set push_scope to this repo
            payload={"repo": repo, "pr_number": pr_number},
        )

    return HttpResponse(status=200)
```

The shape is identical: receive POST, verify signature, process
event, `push_to_view`. Each external service has its own
signature verification scheme; the rest of the file is the same.

---

## Filtering and authorization concerns

**Without `scope`, `push_to_view` reaches ALL connected sessions of
the view class.** Each of them runs the handler and re-renders, so
the work grows with the number of open sessions, and every session
would receive another customer's balance.

The webhook view doesn't know which user owns which WebSocket
session, and it doesn't need to. The LiveView decides, in `mount()`,
which scope its session belongs to (`self.push_scope`), after the
view's own access checks (`login_required`) have run. The webhook
then pushes to the scope:

```python
push_to_view(
    "myapp.views.AccountView",
    handler="handle_balance_changed",
    scope=f"account-{account.id}",
    payload={...},
)
```

Keep the scope derived from data the session was allowed to see
(here, the signed-in user's own account), never from a request
parameter. A scope is a `str` or an `int`; a session can be in up to
64 of them.

---

## What just happened, end to end

```
   Browser                  Server (HTTP)              Server (WS)              Stripe
       │                         │                         │                       │
       │ GET /account/           │                         │                       │
       │ ───────────────────────►│ mount: balance=1000    │                       │
       │ ◄ render: $10.00 ───────│                         │                       │
       │                         │                         │                       │
       │ WS upgrade /ws/         │                         │                       │
       │ ────────────────────────────────────────────────► consumer mounted        │
       │                         │                         │                       │
       │   ... user pays $20 elsewhere ...                                         │
       │                         │                         │ Stripe receives card  │
       │                         │                         │ Stripe POSTs ────────►│ webhook
       │                         │                         │ ◄────────────────────│ delivery
       │                         │ stripe_webhook(request) │                       │
       │                         │   verify signature ✓    │                       │
       │                         │   account.balance += 2000                       │
       │                         │   push_to_view(            │                    │
       │                         │     "AccountView",         │                    │
       │                         │     handler="handle_balance_changed",           │
       │                         │     scope="account-1", payload={...})           │
       │                         │ ─── group_send (scope group) ──►                │
       │                         │                         │ handle_balance_changed(**payload)
       │                         │                         │ self.balance_cents = 3000
       │                         │                         │ render diff           │
       │ ◄ patch: $10 → $30 ──── │ ─────────────────────── │                       │
```

Two transports: HTTP for the webhook in (one direction, Stripe
→ Django), WebSocket for the patch out (Django → browser).
`push_to_view` bridges them.

---

## Where to go next

- **Several views, one event:** scopes belong to one view path.
  If a payment should also update, say, an admin view, push to
  each view path separately.
- **Pruning processed events:** `ProcessedWebhookEvent` grows by
  one row per event. Stripe stops retrying after about three days, so
  a periodic job can delete rows older than a week.
- **Replay tools:** during dev / debugging, store every
  received webhook (raw body + headers) so you can re-fire it
  into the handler without coordinating with Stripe again.
- **Sentry breadcrumbs:** the webhook handler runs in HTTP
  context; add `sentry_sdk.set_context("stripe_event",
  event)` so failures attach the originating event for
  debugging.
- **Cross-process / multi-server:** `push_to_view` uses
  Channels under the hood, so it works across your fleet
  automatically — webhook hitting server A reaches the user
  whose WebSocket landed on server B as long as both share a
  Redis Channels backend. See [Deployment](deployment.md) for the
  channel-layer settings. Server push reaches WebSocket sessions
  only; SSE and HTTP-only sessions receive none.

The two-line shape — `push_to_view(view, handler="handle_...",
scope=..., payload=...)` from the webhook + a plain `def
handle_...(self, **payload)` on the view — is the entire
"external event → live UI" surface. Once it clicks, every
"can we update X without polling?" question gets a clean
"yes, push from wherever the event arrives" answer.
