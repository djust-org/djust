---
title: "Tutorial: Build a typeahead with @server_function"
slug: tutorial-typeahead-server-function
section: guides
order: 63
level: intermediate
description: "Build an autocomplete dropdown that fetches suggestions from the server on every keystroke without re-rendering anything else on the page. Uses @server_function for browser-to-Python RPC — the right primitive when you want server data without a VDOM diff."
---

# Tutorial: Build a typeahead with @server_function

In [Tutorial: Build a search-as-you-type feature](/guides/tutorial-search-as-you-type/)
we used `@event_handler` so every keystroke re-rendered the results
list as part of the LiveView's normal diff cycle. That's the right
choice when the suggestions are *part of* the page being built up.

Sometimes you want the opposite: an autocomplete dropdown attached
to one field of a larger form, where typing in the field shouldn't
disturb anything else the user has filled in. The whole form is
already mounted with state — re-rendering it just to show a list
of suggestions risks losing focus, scroll position, or in-progress
input on other fields.

That's what `@server_function` is for: pure browser-to-Python RPC,
with **no VDOM diff and no re-render**. The server returns data;
your JS decides what to do with it.

By the end of this tutorial you'll have:

- A "Tag this issue" form with an autocomplete `<input>` for tags.
- Each keystroke calls a `@server_function` that returns matching
  tag suggestions.
- A `<ul>` dropdown rendered by tiny JS — no LiveView re-render.
- The user's other in-progress fields (title, description) remain
  exactly as they typed them.

| You'll learn | Documented in |
|---|---|
| The 3 RPC decorators and when to use which | [Server Functions](/guides/server-functions/) |
| `djust.call(view_slug, fn_name, kwargs)` on the client | [Server Functions § djust.call](/guides/server-functions/) |
| Why no-re-render matters for partial-page widgets | This tutorial |
| Debouncing in plain JS without a framework | This tutorial |

> **Prerequisites:** [Quickstart](/getting-started/),
> [Forms & Validation](/guides/forms/), and the
> [search-as-you-type tutorial](/guides/tutorial-search-as-you-type/)
> (recommended — this tutorial argues against that approach for one
> specific use case).

---

## Why not just use `@event_handler`?

If you tried this with `@event_handler` first, you'd see the bug
immediately: while the user is typing in the **tag input**, every
keystroke triggers a re-render of the entire `IssueForm` LiveView,
which re-mounts the title `<input>`, the description `<textarea>`,
the existing-tags chips, and any other state. Anything the user
hadn't yet sent to the server (e.g. a partially-typed sentence in
the textarea) is preserved by djust's diffing in most cases — but
ancillary state like cursor position and IME composition can flicker.

For a textarea that the user is in the middle of typing in, even
imperceptible flicker is bad. And if you have a long form, the
overhead of re-rendering it 5–10 times per second while the user
types in *one* field is wasteful.

The fix: tell the framework "I'm calling the server but I don't
want a re-render." That's `@server_function`.

---

## Step 1 — The model

Standard Django:

```python
# myapp/models.py
from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-usage_count", "name"]


class Issue(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    tags = models.ManyToManyField(Tag)
```

---

## Step 2 — The LiveView with the `@server_function`

```python
# myapp/views.py
from djust import LiveView, state, action
from djust.decorators import server_function

from .models import Issue, Tag


class NewIssueView(LiveView):
    template_name = "new_issue.html"

    # Required for djust.call() to address this view from the client
    api_name = "issues.new"

    title = state("")
    description = state("")
    selected_tags = state(default_factory=list)  # list of {id, name}

    @server_function
    def search_tags(self, q: str = "", limit: int = 8, **kwargs) -> list[dict]:
        q = q.strip()
        if not q:
            return []
        hits = Tag.objects.filter(name__istartswith=q).order_by(
            "-usage_count", "name"
        )[:limit]
        return [{"id": t.id, "name": t.name} for t in hits]

    @action
    def add_tag(self, id: int = 0, name: str = "", **kwargs):
        if not id or not name:
            raise ValueError("Tag missing")
        if any(t["id"] == id for t in self.selected_tags):
            return  # already added
        self.selected_tags.append({"id": id, "name": name})

    @action
    def remove_tag(self, id: int = 0, **kwargs):
        self.selected_tags = [t for t in self.selected_tags if t["id"] != id]

    @action
    def submit(self, **kwargs):
        if not self.title.strip():
            raise ValueError("Title is required")
        issue = Issue.objects.create(
            title=self.title.strip(),
            description=self.description,
        )
        if self.selected_tags:
            issue.tags.set([t["id"] for t in self.selected_tags])
        return {"id": issue.id}
```

Two things to call out:

1. **`api_name = "issues.new"`** — required so `djust.call()` from the
   client can address this LiveView. The slug is opaque to the user
   but lives in the page-load envelope so JS knows where to dispatch.
2. **`search_tags` is a `@server_function`, but `add_tag` /
   `remove_tag` / `submit` are `@action`.** That's deliberate: tag
   suggestions don't change the LiveView's state, but adding /
   removing a tag *does* (it changes `self.selected_tags`, which is
   reflected in the chip list). Use `@server_function` only when
   you genuinely don't want a re-render.

---

## Step 3 — The template + tiny JS

```html
<!-- myapp/templates/new_issue.html -->
<form dj-submit="submit">
  <label>
    Title
    <input name="title" value="{{ title }}" required />
  </label>

  <label>
    Description
    <textarea name="description">{{ description }}</textarea>
  </label>

  <fieldset>
    <legend>Tags</legend>

    <ul class="chips">
      {% dj-for tag in selected_tags %}
        <li>
          {{ tag.name }}
          <button type="button" dj-click="remove_tag" dj-payload-id="{{ tag.id }}" aria-label="Remove tag">×</button>
        </li>
      {% end-dj-for %}
    </ul>

    <div class="typeahead">
      <input
        id="tag-input"
        type="text"
        autocomplete="off"
        placeholder="Add a tag…"
        aria-label="Search tags"
        aria-autocomplete="list"
        aria-controls="tag-suggestions"
      />
      <ul id="tag-suggestions" role="listbox" hidden></ul>
    </div>
  </fieldset>

  <button type="submit" dj-form-pending="disabled">
    <span dj-form-pending="hide">Create issue</span>
    <span dj-form-pending="show" hidden>Creating&hellip;</span>
  </button>
</form>

<script>
  (function () {
    const input = document.getElementById('tag-input');
    const dropdown = document.getElementById('tag-suggestions');
    let timer = null;

    async function fetchSuggestions(q) {
      try {
        return await djust.call('issues.new', 'search_tags', { q });
      } catch (err) {
        console.error('tag search failed', err);
        return [];
      }
    }

    function renderSuggestions(hits) {
      if (!hits.length) {
        dropdown.hidden = true;
        return;
      }
      dropdown.innerHTML = hits.map(h => `
        <li role="option" data-id="${h.id}" data-name="${h.name}">${h.name}</li>
      `).join('');
      dropdown.hidden = false;
    }

    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const hits = await fetchSuggestions(input.value);
        renderSuggestions(hits);
      }, 150);
    });

    dropdown.addEventListener('click', (ev) => {
      const li = ev.target.closest('li[data-id]');
      if (!li) return;
      // Tell the LiveView to add the tag (this DOES re-render)
      djust.dispatch('add_tag', {
        id: parseInt(li.dataset.id, 10),
        name: li.dataset.name,
      });
      input.value = '';
      dropdown.hidden = true;
    });
  })();
</script>
```

What's doing what:

| Element | Behavior |
|---|---|
| `djust.call('issues.new', 'search_tags', { q })` | Calls the `@server_function` over HTTP, returns the JSON result. **No re-render.** |
| `setTimeout(..., 150)` | 150 ms client-side debounce — same tradeoff as `dj-debounce` but with vanilla JS because we're orchestrating manually. |
| `djust.dispatch('add_tag', {...})` | Fires the `add_tag` event on the LiveView — this *does* trigger a re-render, which is what we want (the chip list needs to update). |
| `dj-form-pending` on submit button | Standard form-pending UX during the final submit. |

The user can type in the description textarea, focus the tag input,
type to search, click a suggestion, and watch the chip appear at the
top — all without losing the cursor in the textarea or seeing any
flicker on unrelated elements.

---

## When to use which RPC primitive

This is the table from the [Server Functions](/guides/server-functions/)
guide, restated as a decision rule:

| You want… | Use |
|---|---|
| Click / submit / input → state change → DOM update | `@event_handler` |
| Same as above, but also callable from mobile / S2S / AI agents | `@event_handler(expose_api=True)` |
| Server data, no re-render, no external API | **`@server_function`** |

The third row is the rarest of the three — most interactions *do*
want a re-render. But for autocomplete, "is this email already
registered" inline checks, async price calculators, and any other
"side data fetch" you don't want polluting the diff cycle,
`@server_function` is the cleanest tool.

---

## Where to go next

- **Keyboard navigation:** add ↑/↓/Enter handling on the suggestion
  dropdown so users don't have to mouse over each option. The
  pattern is identical to the [Cmd+K search modal in
  docs.djust.org](https://github.com/djust-org/docs.djust.org/blob/main/static/js/search.js)
  if you want a reference.
- **Cancellation:** if the user types "ab" then "abc" before the
  "ab" call returns, you'll briefly render stale "ab" suggestions
  on top of the "abc" results. Use a per-call sequence number and
  drop responses whose sequence is older than the latest one fired.
- **Inline validation:** the same pattern works for "is this email
  already registered" — `@server_function` returns `{available: bool,
  reason?: str}`, the JS toggles a small icon next to the field.
  No re-render, so the password field below isn't re-mounted.
- **Result cache:** for hot autocomplete (e.g. country picker), wrap
  the `@server_function` body in `@functools.lru_cache` keyed on `q`.
  djust does *not* cache `@server_function` responses for you.

The decision between `@event_handler` and `@server_function` is one
of the rare actually-architectural choices djust pushes onto you.
Most of the time you want the diff cycle. When you don't — when the
data you're fetching is *adjacent to* the page state, not part of
it — `@server_function` exists and is the cleanest tool for the job.
