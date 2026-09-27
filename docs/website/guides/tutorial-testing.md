---
title: "Tutorial: Test a LiveView with LiveViewTestClient"
slug: tutorial-testing
section: guides
order: 73
level: intermediate
description: "Write end-to-end tests for a counter, a form, and a list — without a real WebSocket. The synchronous LiveViewTestClient runs the same code path as the consumer minus the transport, so you get fast, deterministic tests of mount → handler → re-render. Plus the right way to test pushed events, redirects, and validation errors."
---

# Tutorial: Test a LiveView with LiveViewTestClient

Most LiveView bugs are state-transition bugs: the user clicked the
button but the right state var didn't change, or it changed but the
template branch didn't update. The fastest feedback loop for those
bugs is a unit test that mounts the view, fires events, and asserts
on state — no Selenium, no Playwright, no test browser.

djust ships `LiveViewTestClient` for exactly this. It instantiates
the view, calls `mount()`, and invokes handlers directly. Same code
path as the production WebSocket consumer minus the transport
layer. Tests run in tens of milliseconds, are deterministic, and
fail with clean tracebacks pointing at the line in your view.

By the end of this tutorial you'll have:

- A test for a **counter view** — mount, fire events, assert state.
- A test for a **form view** with validation — including the
  failure path (empty submission → error message).
- A test for **page-level auth** (anonymous user hits an
  auth-gated view → 302 to /login/).
- A test for a **pushed event** (server tells the client to
  show a flash toast).
- A pattern for **testing a LiveComponent in isolation**, mounted
  without its parent.

| You'll learn | Documented in |
|---|---|
| `LiveViewTestClient.mount(**params)` | [Testing](testing.md) |
| `client.send_event("name", …)` and `assert_state(...)` | [Testing](testing.md) |
| `client.assert_push_event("flash", {...})` | [Testing](testing.md) |
| `client.render()` for HTML-level assertions | [Testing](testing.md) |
| Where this stops — when to reach for a real browser | This tutorial |

> **Prerequisites:** [Installation](../getting-started/installation.md), pytest with
> `pytest-django` configured, and one of the earlier tutorials so
> you have a view worth testing. The examples below use the
> [search-as-you-type](tutorial-search-as-you-type.md) view
> as the running case.

---

## What this client gives you (and what it doesn't)

The `LiveViewTestClient` is a **server-side** test harness. It runs:

- `mount(request, **params)` — the view's own `mount()`. Page-level
  auth (`login_required`, `get_object()` /
  `has_object_permission()`) is not applied here; see Step 4.
- Each `@event_handler` you `send_event` for.
- Template rendering (when you call `client.render()`).
- All assertions are against `self.*` attrs after the handler runs.

It does NOT run:

- The WebSocket transport (no real frames; no `LiveViewConsumer`).
- The client-side JS runtime (no `dj-debounce` timer, no
  `dj-loading.*` class application — those are client-only).
- Real DOM mounting / patching (no JSDOM, no IntersectionObserver).

Use this client for **logic** tests — does the handler change the
state correctly? Does the template render the right text for the
right state? Use a real-browser tool (Playwright, Selenium) for
**transport** tests — does the WebSocket actually reconnect after
a flap? Does `dj-debounce` cancel the timer correctly? Most teams
need 100 of the former for every 1 of the latter.

---

## Step 1 — Test the search-as-you-type view's happy path

Recall the view from [tutorial #1](tutorial-search-as-you-type.md):

<!-- Excerpt: LiveView, state and event_handler are imported in the
     tutorial it comes from. -->
<!-- doc-snippet-check: skip -->
```python
class SearchView(LiveView):
    template_name = "search.html"

    query = state("")
    results = state(default_factory=list)
    error = state("")

    @event_handler
    def search(self, value: str = "", **kwargs):
        self.query = value
        self.error = ""
        q = value.strip()
        if not q:
            self.results = []
            return
        # ... query DB ...
        self.results = [{"id": d.id, "title": d.title} for d in qs]
```

The simplest possible test:

<!-- pytest and the myapp.* modules are the reader's project, not
     something the doc checker can import. -->
<!-- doc-snippet-check: skip -->
```python
# tests/test_search_view.py
import pytest
from djust.testing import LiveViewTestClient

from myapp.models import Document
from myapp.views import SearchView


@pytest.mark.django_db
def test_typing_a_query_populates_results():
    Document.objects.create(title="Indexing strategies for Postgres")
    Document.objects.create(title="Why we picked Rust")
    Document.objects.create(title="Postgres tuning notes")

    client = LiveViewTestClient(SearchView).mount()
    client.send_event("search", value="postgres")

    state = client.get_state()
    assert state["query"] == "postgres"
    assert len(state["results"]) == 2
    titles = [r["title"] for r in state["results"]]
    assert "Indexing strategies for Postgres" in titles
    assert "Postgres tuning notes" in titles


@pytest.mark.django_db
def test_empty_query_clears_results():
    Document.objects.create(title="Anything")

    client = LiveViewTestClient(SearchView).mount()
    client.send_event("search", value="anything")
    assert len(client.get_state()["results"]) == 1

    client.send_event("search", value="")
    state = client.get_state()
    assert state["results"] == []
    assert state["query"] == ""
```

Two patterns:

| Pattern | Use |
|---|---|
| `LiveViewTestClient(SearchView).mount()` | Instantiate + run mount. Pass URL kwargs as `mount(**params)` for views that take them: `LiveViewTestClient(PostView).mount(post_id=42)`. |
| `client.send_event("search", value="postgres")` | Fire the handler. Kwargs become handler kwargs — `value` here, because that is what `dj-input` sends. |

Note the `@pytest.mark.django_db` — needed because the view talks
to the DB. Without the marker, the test runs against an empty
in-memory DB and `Document.objects.create` would fail.

---

## Step 2 — Test the failure path

The search view's handler swallows errors and writes them to
`self.error`. Test the path:

<!-- pytest and the myapp.* modules are the reader's project, not
     something the doc checker can import. -->
<!-- doc-snippet-check: skip -->
```python
@pytest.mark.django_db
def test_search_handles_db_error_gracefully(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(
        "myapp.views.Document.objects.filter",
        boom,
    )

    client = LiveViewTestClient(SearchView).mount()
    client.send_event("search", value="anything")

    state = client.get_state()
    assert state["results"] == []
    assert "connection lost" in state["error"]
```

The `monkeypatch` fixture is pytest's standard way to swap a
callable inline; here we make `Document.objects.filter` raise
unconditionally and confirm the handler catches the error and
sets `self.error`.

---

## Step 3 — Test a pushed event (flash toast)

If the view fires `self.push_event("flash", {...})`, the test
client captures it:

<!-- pytest and the myapp.* modules are the reader's project, not
     something the doc checker can import. -->
<!-- doc-snippet-check: skip -->
```python
class CommentView(LiveView):
    @event_handler
    def post(self, body: str = "", **kwargs):
        if not body.strip():
            self.push_event("flash", {"type": "error", "message": "Empty comment"})
            return
        # ... save ...
        self.push_event("flash", {"type": "success", "message": "Posted!"})


@pytest.mark.django_db
def test_empty_comment_pushes_error_flash():
    client = LiveViewTestClient(CommentView).mount()
    client.send_event("post", body="")

    client.assert_push_event("flash", {"type": "error", "message": "Empty comment"})
```

`assert_push_event` passes when any event queued since `mount()`
has that name and a payload containing the given keys (a subset
match, so later payload additions don't break the test). The queue
is not cleared between `send_event` calls; to check exactly what one
handler pushed, use a fresh client.

---

## Step 4 — Test page-level auth

`LiveViewTestClient.mount()` calls your `mount()` directly, so it
does not apply `login_required` or the `get_object()` /
`has_object_permission()` check. Test those through the page load
with Django's test client (pytest-django's `client` fixture). The
auth-gated team admin from [the authentication tutorial](tutorial-authentication.md)
sends anonymous visitors to `/login/` and answers 403 to a signed-in
non-member:

<!-- pytest and the myapp.* modules are the reader's project, not
     something the doc checker can import. -->
<!-- doc-snippet-check: skip -->
```python
import pytest

from myapp.models import Team


@pytest.mark.django_db
def test_anonymous_user_redirects_to_login(client):
    response = client.get("/teams/1/")
    assert response.status_code == 302
    assert response.url.startswith("/login/?next=")


@pytest.mark.django_db
def test_non_member_is_refused(client, django_user_model):
    user = django_user_model.objects.create_user(username="alice", password="pw")
    Team.objects.create(id=1, name="The Team")
    client.force_login(user)

    response = client.get("/teams/1/")
    assert response.status_code == 403  # not a member of team 1
```

Once the page-level matrix is covered, use `LiveViewTestClient`'s
`user=` kwarg for handler logic that depends on who is signed in:
`LiveViewTestClient(TeamAdminView, user=user).mount(team_id=1)` sets
`request.user` for the lifecycle (without it, the request has
`AnonymousUser`). `client.assert_redirect("/teams/")` checks that a
handler called `self.live_redirect("/teams/")`.

---

## Step 5 — Test the rendered HTML, not just state

Sometimes the bug is in the template, not the handler. State
asserts pass; the user still sees the wrong thing. Use
`client.render()` for HTML-level assertions:

<!-- pytest and the myapp.* modules are the reader's project, not
     something the doc checker can import. -->
<!-- doc-snippet-check: skip -->
```python
@pytest.mark.django_db
def test_results_render_with_titles():
    Document.objects.create(title="Indexing strategies")
    Document.objects.create(title="Why we picked Rust")

    client = LiveViewTestClient(SearchView).mount()
    client.send_event("search", value="indexing")

    html = client.render()
    assert "Indexing strategies" in html
    assert "Why we picked Rust" not in html
    # Check the empty-state isn't shown when we have results
    assert "Type to search" not in html
```

Don't reach for `client.render()` for every test — it's slower
than `assert_state` and couples the test to template markup. Use
it when the bug class is "the conditional template branch picked
the wrong arm" or "the format string lost a variable."

---

## Step 6 — Test a LiveComponent in isolation

The [`StarRating` component from the LiveComponent tutorial](tutorial-live-component.md)
can be tested without its parent:

<!-- pytest and the myapp.* modules are the reader's project, not
     something the doc checker can import. -->
<!-- doc-snippet-check: skip -->
```python
from djust.testing import LiveComponentTestClient

from myapp.components.star_rating import StarRating


def test_hover_changes_hover_state_only():
    client = LiveComponentTestClient(StarRating).mount(value=3, max=5)
    assert client.get_state()["hover"] == 0

    client.send_event("hover_star", n=4)
    state = client.get_state()
    assert state["hover"] == 4
    assert state["value"] == 3  # value untouched


def test_commit_rating_bubbles_to_parent():
    client = LiveComponentTestClient(StarRating).mount(value=3)
    sent = []
    client.component._set_parent_callback(sent.append)  # stand-in parent

    client.send_event("commit_rating", n=5)

    assert client.get_state()["value"] == 5
    assert sent[-1]["event"] == "rating_changed"
    assert sent[-1]["data"] == {"value": 5}
```

`send_parent()` calls whatever callback the parent registered; with
no parent it does nothing. Registering a list's `append` as the
callback captures the bubble-up, so you don't need a real parent
view in the test. `LiveComponentTestClient` calls the component's
methods directly and does not enforce `@event_handler`; for full
integration (parent + component), mount the parent with
`LiveViewTestClient`.

---

## Beyond logic tests: when to reach for a real browser

The test client doesn't simulate the client runtime. Tests that
depend on the runtime need a real browser (Playwright is
standard):

| Behavior | Test with |
|---|---|
| `dj-debounce` cancels the previous timer | Real browser — JS-level |
| `dj-loading.show` toggles correctly during in-flight events | Real browser |
| WebSocket reconnect resumes after a network flap | Real browser + network throttle |
| IntersectionObserver fires `dj-viewport-bottom` | Real browser |
| The chart's animation tweens between datasets | Real browser |
| @event_handler runs in the right order | `LiveViewTestClient` |
| State changes in the right way | `LiveViewTestClient` |
| Pushed events fire with the right params | `LiveViewTestClient` |
| Page-level auth redirects and 403s | Django's test client (`client.get`) |
| Handlers that call `live_redirect` | `LiveViewTestClient` (`assert_redirect`) |
| Templates render the right branch for the right state | `LiveViewTestClient` |

The 100:1 ratio holds: most of your bug surface is in handler
logic and template branches. Cover that with the test client.
Reserve the slow real-browser tests for the few features whose
correctness genuinely depends on the transport or runtime.

---

## Where to go next

- **Snapshot testing:** mix in `SnapshotTestMixin` ([Testing
  guide](testing.md)) to assert on rendered HTML diffs,
  not just substrings. Catches accidental template regressions
  the substring asserts would miss.
- **Smoke test the whole site:** `LiveViewSmokeTest` discovers
  every LiveView (or one app's, with `app_label`), mounts and
  renders each, and fuzzes their handlers. Catches
  "I changed an import and now half the views 500" before
  deploying.
- **Time-travel debugging in tests:** time-travel branches live
  in memory only; to keep one as a regression test, reproduce its
  event sequence with `send_event`. See the [time-travel
  guide](time-travel-debugging.md).
- **CI integration:** point pytest at `tests/` in your CI
  config; the test client doesn't need any special setup beyond
  what `pytest-django` already provides.

The four-line shape of a test (`mount` → `send_event` →
`assert_state` or `assert_push_event`) is the
entire surface for ~95% of LiveView tests. Start there; reach for
real-browser tests only when you've proven you need them.
