---
title: "Tutorial: Build a real-time comment thread"
slug: tutorial-real-time-comments
section: guides
order: 61
level: intermediate
description: "Build a comment thread that streams new posts to every reader the moment they're written — no polling, no manual refresh. Combines @action for the post handler, dj-form-pending for the submit UX, dj-for for rendering, and database notifications for the live broadcast."
---

# Tutorial: Build a real-time comment thread

By the end of this tutorial you'll have a comment thread that:

- Lets logged-in users **post a comment** through a single form, with
  full pending/error/success UX wired through `@action` and
  `dj-form-pending`.
- **Renders the existing comments** with `dj-for`, preserving the
  scroll position when the list grows.
- **Streams new comments to every connected reader** within a few
  hundred milliseconds of any user posting one — using PostgreSQL
  `LISTEN` / `NOTIFY` (no polling, no `setInterval`).

It's the smallest realistic example of djust's "one stack, one truth"
pitch: the same Python view that handles the form submission also
broadcasts to other connected users, with no separate API or job
queue.

| You'll learn | Documented in |
|---|---|
| Server actions for form submissions | [Server Actions](/guides/server-actions/) |
| Pending UX during submit | [Loading States](/guides/loading-states/) |
| Rendering reactive lists | [Lists (`dj-for`)](/guides/lists/) |
| Live cross-user broadcasts | [Database Notifications](/guides/database-notifications/) |

> **Prerequisites:** The [quickstart](/getting-started/), a working
> Django project with djust, a configured database (PostgreSQL for the
> live-update step), and a Django user model. Familiarity with
> [`@event_handler`](/guides/server-actions/) helps but isn't required.

---

## What you're building

```
Comments on this post (3)
─────────────────────────
@alice  · 2 min ago
  This is the third comment.

@bob    · 5 min ago
  Glad someone built this in djust.

@carla  · 10 min ago
  First!

Add a comment
[ Type here…                                         ]
[ Post ]   ← flips to "Posting…" while in flight
```

When any user submits a comment, it appears in their own thread
*and* in every other open browser viewing the same thread, within
roughly the time it takes the database to publish a `NOTIFY`.

---

## Step 1 — The model

Standard Django:

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Post(models.Model):
    title = models.CharField(max_length=200)


class Comment(models.Model):
    post = models.ForeignKey(
        Post, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
```

Run `python manage.py makemigrations && migrate` and create a
single `Post` row for the demo.

---

## Step 2 — The LiveView, with the post action

Create the LiveView. We track the list of comments as state and
expose a single `@action` for posting:

```python
# myapp/views.py
from djust import LiveView, action, state

from .models import Comment, Post


class CommentThreadView(LiveView):
    template_name = "comment_thread.html"

    post_id = state(0)
    comments = state(default_factory=list)

    def mount(self, request, *, post_id: int, **kwargs):
        if not request.user.is_authenticated:
            self.redirect("/login/")
            return
        self.post_id = post_id
        self.comments = self._fetch_comments()

    def _fetch_comments(self):
        qs = (
            Comment.objects
            .filter(post_id=self.post_id)
            .select_related("author")
            .order_by("-created_at")[:50]
        )
        return [
            {
                "id": c.id,
                "author": c.author.username,
                "body": c.body,
                "created_at": c.created_at.isoformat(),
            }
            for c in qs
        ]

    @action
    def post_comment(self, body: str = "", **kwargs):
        body = body.strip()
        if not body:
            raise ValueError("Comment cannot be empty.")
        Comment.objects.create(
            post_id=self.post_id,
            author=self.request.user,
            body=body,
        )
        # Re-fetch so we render the freshly-saved row alongside any
        # comments that arrived from other readers in the meantime.
        self.comments = self._fetch_comments()
        return {"posted": True}
```

`@action` does three things compared to a bare `@event_handler`:

1. Tracks `pending / error / result` state across the handler's
   lifetime.
2. Auto-injects that state into the template under the action's name
   — so the template can read `post_comment.pending` without any
   extra wiring.
3. Lets you pair with `dj-form-pending` for declarative in-flight
   form UX (see Step 3).

---

## Step 3 — The template

```html
<!-- myapp/templates/comment_thread.html -->
<section>
  <h2>Comments on this post ({{ comments|length }})</h2>
  <hr />

  <ul class="comments">
    {% dj-for comment in comments %}
      <li>
        <strong>@{{ comment.author }}</strong>
        <time datetime="{{ comment.created_at }}">{{ comment.created_at }}</time>
        <p>{{ comment.body }}</p>
      </li>
    {% end-dj-for %}
  </ul>

  <hr />

  <form dj-submit="post_comment">
    <label>
      Add a comment
      <textarea name="body" rows="3" required dj-form-pending="disabled"></textarea>
    </label>

    <button type="submit" dj-form-pending="disabled">
      <span dj-form-pending="hide">Post</span>
      <span dj-form-pending="show" hidden>Posting&hellip;</span>
    </button>

    {% if post_comment.error %}
      <p role="alert" class="error">{{ post_comment.error }}</p>
    {% endif %}
  </form>
</section>
```

What's doing what:

| Element | Behavior |
|---|---|
| `dj-for comment in comments` | Per-row diffing — when `self.comments` grows by one, only that one `<li>` is patched into the DOM. The other rows don't re-render. |
| `<form dj-submit="post_comment">` | Submit fires the `post_comment` event, sending `name="body"` as a kwarg. |
| `dj-form-pending="disabled"` on the textarea & button | Both get `disabled` while the submit is in flight. No prop drilling. |
| `dj-form-pending="hide"` / `="show"` on the spans | The "Post" label hides and "Posting…" shows during the round-trip. |
| `{% if post_comment.error %}` | Reads the auto-injected action state. Rendered only when the handler raised — empty body, DB failure, etc. |

---

## Step 4 — URL and try it

```python
# myapp/urls.py
from django.urls import path
from .views import CommentThreadView

urlpatterns = [
    path("posts/<int:post_id>/", CommentThreadView.as_view()),
]
```

Open `/posts/1/` in two browser windows logged in as different
users. Post a comment in one. **The comment shows up in that
window — but not yet in the other.** That's what the next step
fixes.

---

## Step 5 — Broadcast new comments to every reader

For other connected users to see the new comment, the LiveView
needs to be told that a write happened. The cleanest path is a
PostgreSQL `LISTEN` / `NOTIFY` channel — fire-and-forget on the
write side, push-based on the read side. djust ships a
`@dj_listen("channel_name")` decorator that wires this up:

```python
from djust import LiveView, action, state, dj_listen

from .models import Comment


class CommentThreadView(LiveView):
    # ... as before ...

    @dj_listen("comments_changed")
    def on_comments_changed(self, payload, **kwargs):
        """Fired when *any* connection NOTIFY's comments_changed.

        Payload comes from the NOTIFY's optional argument — we use
        it to scope updates to the current post.
        """
        if payload.get("post_id") != self.post_id:
            return
        self.comments = self._fetch_comments()
```

And on the write side — fire the NOTIFY after a successful insert.
The cleanest pattern is a Django signal:

```python
# myapp/signals.py
import json
from django.db import connection
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Comment


@receiver(post_save, sender=Comment)
def broadcast_comment(sender, instance, created, **kwargs):
    if not created:
        return
    payload = json.dumps({"post_id": instance.post_id})
    with connection.cursor() as cur:
        cur.execute("NOTIFY comments_changed, %s", [payload])
```

Wire it up in `apps.py`:

```python
from django.apps import AppConfig

class MyappConfig(AppConfig):
    name = "myapp"

    def ready(self):
        from . import signals  # noqa: F401
```

Now reload both browser windows. Post a comment in window A — it
appears in **both windows** within ~50–200 ms. No polling, no
JavaScript timers, no separate API endpoint. The same Python view
that handled the submit also reacted to the broadcast.

---

## What just happened, end to end

```
   Browser A                Server                Browser B
      │                       │                       │
      │  submit (post_comment)│                       │
      │ ─────────────────────►│                       │
      │                       │ INSERT into comments  │
      │                       │ ─────► postgres ──────│
      │                       │ post_save signal      │
      │                       │ ───► NOTIFY           │
      │                       │ ◄─── LISTEN dispatch  │
      │                       │ on_comments_changed   │
      │                       │   (every connected    │
      │                       │    LiveView for       │
      │                       │    this post)         │
      │  patch (new <li>)     │                       │
      │ ◄─────────────────────│                       │
      │                       │  patch (new <li>)     │
      │                       │ ─────────────────────►│
```

Five concrete primitives carried the whole feature:

1. **`state(...)`** — declares `comments` as reactive; reassignment
   triggers diff + patch.
2. **`@action`** — wraps `post_comment` so the template can read
   `post_comment.pending` / `.error` automatically.
3. **`dj-form-pending`** — declarative in-flight form UX without a
   single line of client JS.
4. **`dj-for`** — per-row diffing so adding one comment patches one
   `<li>`, not the whole list.
5. **`@dj_listen`** — server-push subscription to a Postgres channel,
   so any DB write becomes a UI update for every connected viewer.

---

## Where to go next

- **"Who's reading right now":** add the [Presence](/guides/presence/)
  helper to show online viewers in the header — five lines of
  template, zero new infrastructure.
- **Pagination:** the demo loads 50 most-recent comments. For longer
  threads, add cursor-based pagination triggered by an
  IntersectionObserver-backed `dj-click="load_more"`. See
  [Lists (`dj-for`)](/guides/lists/).
- **Optimistic update:** instead of `self.comments = self._fetch_comments()`,
  push the new comment client-side immediately by appending to
  `self.comments` before the DB write. The `LISTEN` echo then
  reconciles.
- **Per-thread channels:** for very high write volume, scope the
  `LISTEN` channel by post id (e.g. `comments_changed_{post_id}`)
  so each LiveView only receives notifications for the post it's
  rendering.
- **Soft-deletes & moderation:** add an `@action` for
  `delete_comment(comment_id)` with `@permission_required("moderator")`,
  same broadcast pattern.

The comment-thread shape — submit + render + cross-user broadcast
— is the template for chat, live dashboards, collaborative
editors, multiplayer game lobbies, and pretty much any
multiplayer UI. Once these five primitives click, you have the
full toolkit.
