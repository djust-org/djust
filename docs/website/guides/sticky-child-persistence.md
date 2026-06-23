---
title: "Sticky-Child State Persistence"
slug: sticky-child-persistence
section: guides
order: 6.85
level: intermediate
description: "Persist sticky child LiveView state across WebSocket reconnects — the both-opt-in contract, the stable sticky_id requirement, and the non-sticky-embed limitation"
---

# Sticky-Child State Persistence

A djust page can embed a full ``LiveView`` subclass inside another one
with ``{% live_render ... sticky=True %}``. That embedded view — a
*sticky child* — is registered on the parent's ``StickyChildRegistry``
and its events route by ``view_id``. The [Sticky LiveViews
guide](sticky-liveviews.md) covers how a sticky child survives a
``live_redirect`` navigation: its DOM subtree and Python instance carry
across the page change.

This guide is about the *other* lifecycle event: a **WebSocket
reconnect** — a page refresh, a network blip, an idle-timeout
disconnect, a server restart, or a snapshot/restore. On a reconnect the
sticky child's Python instance is gone; the child is reconstructed and
``mount()``ed from scratch. Without persistence, its event-driven state
(a counter, a form value, a selection) is silently lost.

Sticky-child persistence — added in v1.0.0rc4 ([ADR-018](../../adr/018-sticky-child-state-persistence.md)) —
saves a sticky child's state to the Django session on every child
event, and restores it the next time the child is constructed. This
guide covers when it applies, the opt-in contract, the stable-``sticky_id``
requirement, a round-trip example, and how ``djust check`` catches the
common misconfiguration.

## What persists, and what doesn't

A top-level ``LiveView`` already persists across a reconnect when it
sets ``enable_state_snapshot = True`` (see [ADR-011](../../adr/011-sticky-liveviews.md)
and the [Sticky LiveViews guide](sticky-liveviews.md)): its public and
private state is written to the session under ``liveview_<path>`` and
restored at the next mount, skipping ``mount()``'s state-init.

A sticky child is different — it does not exist at the parent's
``mount()`` time. It is constructed *during the parent's template
render*, when ``{% live_render %}`` runs. Before v1.0.0rc4 the
per-event state-save block was gated to the page-root view only, so a
sticky-child event was skipped entirely and the child's state never
reached the session.

With sticky-child persistence:

* **Sticky children with a stable ``sticky_id``** — `{% live_render ...
  sticky=True %}` embeds — **persist** across a reconnect, *provided
  both the child and the parent opt in* (see below).
* **Non-sticky ``{% live_render %}`` embeds** — auto-assigned a volatile
  ``child_N`` id — **do not persist**, by design (see
  [The stable ``sticky_id`` requirement](#the-stable-sticky_id-requirement)).
* **LiveComponents** — assigned as parent attributes
  (``self.foo = MyComponent(...)``) — already persisted before
  v1.0.0rc4 via the parent's own snapshot; they are unaffected by this
  feature.

> **In-connection continuity vs cross-reconnect persistence.** This
> guide's both-opt-in contract is about the *cross-reconnect* axis — a
> page refresh, network blip, or server restart, where the child's
> Python instance is gone and must be reconstructed. That still requires
> ``enable_state_snapshot = True`` on both the child and parent.
>
> Within a *single live WebSocket connection*, a sticky child's state is
> preserved automatically — no opt-in needed — across parent re-renders
> and ``html_recovery`` (the on-demand full-HTML recovery the client
> requests when a VDOM patch fails to apply). The parent reuses the
> already-registered live child instance instead of mounting a fresh one,
> so a parent event or a recovery never resets the sticky child to its
> ``mount()`` defaults (fixed in #1813). The opt-in contract below only
> matters once the connection itself is lost.

## The both-opt-in contract

A sticky child is persisted only when **both** of these are true:

```python
class CounterChild(LiveView):
    sticky = True
    sticky_id = "page-counter"
    enable_state_snapshot = True      # (1) the CHILD opts in

class DashboardView(LiveView):
    template_name = "dashboard.html"
    enable_state_snapshot = True      # (2) the PARENT opts in too
```

Both the embedded child class and the embedding parent class must set
``enable_state_snapshot = True``. If either is missing, the child's
state is **not** saved.

**Why the parent too?** Reconnect restore has to be *tree-consistent*.
If a child restored to its saved state while its parent re-``mount()``ed
fresh, the two would diverge — the parent renders at default state, the
child at saved state, and any parent → child prop the child read at
``mount()`` is now stale. Requiring the parent to opt in guarantees the
whole subtree restores together or not at all (ADR-018 Decision 5).

A child that opts in under a parent that does *not* is a
misconfiguration: the child looks like it should persist, but its save
is silently skipped. djust surfaces this — see [Catching
misconfigurations](#catching-misconfigurations) below.

## The stable `sticky_id` requirement

Persistence keys each child's session entry on its ``sticky_id`` class
attribute:

```
liveview_<parent_path>__sticky__<sticky_id>            # public state
liveview_<parent_path>__sticky__<sticky_id>__private   # private state
```

``<parent_path>`` namespaces the entry by the embedding parent's request
path, so the same child class embedded under different routes keeps
distinct state. ``<sticky_id>`` is the child's stable identifier — the
same id [ADR-011](../../adr/011-sticky-liveviews.md) uses for sticky
reattach.

A ``sticky_id`` is **only** stable for a ``sticky=True`` embed. A plain
``{% live_render %}`` embed (no ``sticky=True``) is auto-assigned a
``child_N`` id by ``StickyChildRegistry`` — a process-global monotonic
stamp that depends on instantiation order and resets on process
restart. ``child_3`` in one worker is ``child_8`` in another; it cannot
be a session key.

**Therefore: only ``{% live_render ... sticky=True %}`` embeds — which
carry a stable ``sticky_id`` — persist.** This is ADR-018 Decision 1.

### The non-sticky-embed limitation

A non-sticky ``{% live_render %}`` embed is **by design** not expected
to outlive a navigation, and is not persisted across a reconnect. This
is a documented limitation, not a bug.

If a non-sticky embed's state *must* survive a reconnect, promote it to
a sticky embed: add ``sticky = True`` and a ``sticky_id`` to the child
class, embed it with ``{% live_render ... sticky=True %}``, and set
``enable_state_snapshot = True`` on both the child and the parent.

## Round-trip example

A dashboard page embeds a sticky counter. Both classes opt in.

```python
# myapp/views.py
from djust import LiveView
from djust.decorators import event_handler


class CounterChild(LiveView):
    sticky = True
    sticky_id = "page-counter"
    enable_state_snapshot = True          # child opts in
    template_name = "myapp/counter_child.html"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler
    def increment(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


class DashboardView(LiveView):
    enable_state_snapshot = True          # parent opts in
    template_name = "myapp/dashboard.html"

    def mount(self, request, **kwargs):
        self.title = "Dashboard"

    def get_context_data(self, **kwargs):
        return {"title": self.title}
```

```django
{# myapp/templates/myapp/dashboard.html #}
{% load live_tags %}
<div dj-root>
    <h1>{{ title }}</h1>
    {% live_render "myapp.views.CounterChild" sticky=True %}
</div>
```

```django
{# myapp/templates/myapp/counter_child.html #}
<div>
    <p>Count: {{ count }}</p>
    <button dj-click="increment">+1</button>
</div>
```

Round trip:

1. The user loads the dashboard and clicks **+1** three times. Each
   ``increment`` event runs through the sticky child; on each event the
   framework writes the child's public state
   (``{"count": 3}``) to the session under
   ``liveview_/dashboard/__sticky__page-counter``, and any private
   (``_``-prefixed) state under
   ``liveview_/dashboard/__sticky__page-counter__private``. A GC ledger
   ``liveview_/dashboard/__sticky_ids`` records ``["page-counter"]`` so
   stale entries for children no longer rendered can be pruned.
2. The user refreshes the page (or the WebSocket reconnects after a
   network blip).
3. ``DashboardView`` re-mounts. When its template renders,
   ``{% live_render "myapp.views.CounterChild" sticky=True %}``
   constructs the child — and *before* calling the child's ``mount()``,
   the tag checks the session for
   ``liveview_/dashboard/__sticky__page-counter``. It finds
   ``{"count": 3}``, applies it in lieu of ``mount()``'s ``count = 0``,
   and replays the child's ``_restore_*`` side-effect hooks.
4. The page renders with **Count: 3** — the child's state survived the
   reconnect.

Restore is wrapped defensively: a corrupt or partial session entry
falls through to a fresh ``mount()`` rather than breaking the parent
render.

## Catching misconfigurations

The common mistake is opting the child in but forgetting the parent —
the child's save is then silently skipped. djust surfaces this two
ways.

### `djust check` — the `V011` system check

Run the system checks:

```bash
python manage.py check
```

The ``djust.V011`` check (``check_sticky_child_optin``, category **V**,
a ``DjustWarning``) scans your templates for
``{% live_render ... sticky=True %}`` tags, resolves the embedded child
class, and matches the embedding parent ``LiveView`` by
``template_name``. It warns when a child sets
``enable_state_snapshot = True`` but a matched parent does **not**:

```
?: (djust.V011) CounterChild: used as a sticky child with
   enable_state_snapshot=True, but embedding parent DashboardView does
   not opt in — the child's state will be silently dropped on reconnect.
   HINT: ADR-018 Decision 5 requires both the child and its embedding
   parent to set enable_state_snapshot = True for a tree-consistent
   restore. Add enable_state_snapshot = True to myapp.views.DashboardView,
   or remove it from myapp.views.CounterChild.
```

``V011`` is conservative — it skips dynamic ``{% live_render variable %}``
paths, unresolvable child classes, ``{% verbatim %}`` doc examples, and
templates with no statically-resolvable parent. Those gaps are covered
by the runtime warning below. ``V011`` only fires on a fully-resolved,
unambiguous misconfiguration, so it does not produce false positives.

If a ``V011`` warning is a false positive for your setup (for example,
template inheritance the static scan can't follow), suppress it:

```python
# settings.py
DJUST_CONFIG = {
    "suppress_checks": ["V011"],
}
```

### The runtime warning

The static check can't see every embedding (dynamic paths, parents it
can't resolve). As a safety net, the framework also emits a one-shot
``logger.warning`` the first time a child save is skipped because the
child opted in but the parent did not:

```
Sticky child 'CounterChild' (sticky_id='page-counter') has
enable_state_snapshot=True but its parent 'DashboardView' does not —
the child's state is NOT persisted across reconnect. Set
enable_state_snapshot=True on the parent too (ADR-018 Decision 5).
```

The warning fires **at most once per ``(parent class, sticky_id)``** —
it won't spam your logs on every event. It is wired into both the
WebSocket save path and the HTTP-POST save path.

## Limitations

1. **Single-level only.** A sticky child that itself embeds further
   sticky children (a nested-sticky tree) is out of scope for v1 — the
   path-namespacing is by the *immediate* parent. The design does not
   preclude a later recursive pass.
2. **Non-sticky embeds do not persist.** Only ``sticky=True`` embeds
   with a stable ``sticky_id`` are persistable (see above).
3. **Session-store size is the app author's responsibility.** Many
   sticky children, each with large state, means large session rows.
   The GC ledger bounds the *count* of entries (it prunes children no
   longer rendered) but per-child state size is yours to manage — the
   same boundary the parent's own snapshot draws.
4. **Persistence rides on the configured session backend.** If the
   Django session backend is Redis- or DB-backed, restore works
   cross-process; if it is in-memory, it does not survive a worker
   change. No new cross-process registry is introduced.

## See also

- [Sticky LiveViews](sticky-liveviews.md) — the sticky-child embedding
  model and ``live_redirect`` survival.
- [ADR-018](../../adr/018-sticky-child-state-persistence.md) — the full
  design: the stable-key scheme, the tag-driven restore, the GC ledger,
  and the both-opt-in contract.
- [ADR-011](../../adr/011-sticky-liveviews.md) — the sticky LiveViews
  baseline.
