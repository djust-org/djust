---
title: "Sticky-Child State Persistence"
slug: sticky-child-persistence
section: guides
order: 6.85
level: intermediate
description: "What a sticky child keeps across a WebSocket reconnect in 2.0: its declared persist=\"server\" fields"
---

# Sticky-Child State Persistence

A djust page can embed a full `LiveView` subclass inside another one with
`{% live_render ... sticky=True %}`. The [Sticky LiveViews
guide](sticky-liveviews.md) covers how such a *sticky child* survives a
`live_redirect`. This page is about a **WebSocket reconnect**, where the
child's Python instance is gone and the child is built again.

## In 2.0: declare what the child keeps

A sticky child under [explicit exposure](../state/explicit-exposure.md) keeps
exactly the fields it declares with `state(..., persist="server")`. On a
reconnect the child is built again, `mount()` runs, and those fields are put
back from the session. Nothing else is restored.

```python
from djust import LiveView
from djust.decorators import event_handler, state


class CartWidget(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "cart"
    template = """<div dj-root>
        <span>{{ count }} items</span>
        <button dj-click="add">Add</button>
    </div>"""

    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["count"] = self.count
        return context

    @event_handler()
    def add(self, **kwargs):
        self.count += 1
```

Give a sticky child a stable `sticky_id`: it identifies the child on the page.

## Removed in 2.0: the `enable_state_snapshot` contract

djust 1.x (from 1.0.0rc4, [ADR-018](../../adr/018-sticky-child-state-persistence.md))
had a second mechanism for legacy-exposure children: when both the sticky child
and its parent set `enable_state_snapshot = True`, the child's whole public
and private state was saved to the session after each of its events, under a
`__sticky__` key, and restored instead of `mount()` on a reconnect. System
check `djust.V011` warned when only the child opted in.

2.0 removed `enable_state_snapshot`, this contract and `djust.V011`. A view
that still sets the attribute is refused when the class is created. Move the
child to explicit exposure and declare its fields as above; see
[Migrating to 2.0](migrating-to-2.0.md#python-names).
