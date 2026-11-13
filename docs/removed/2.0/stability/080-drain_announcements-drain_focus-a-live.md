### `_drain_announcements` / `_drain_focus` → a live region and `JS.focus()`

The WebSocket consumer and the SSE runtime drain two duck-typed methods on the
view behind `hasattr`: `_drain_announcements()` (sends an `accessibility` frame)
and `_drain_focus()` (sends a `focus` frame). No 1.x release has ever defined
either: `mixins/accessibility.py` (with `announce()` / `focus()`) shipped only in
the 0.6.0 and 0.7.0 tags, which are not ancestors of `main`, and no tag from 0.9.0
on has it. The shipped client assigns no `window.djust.accessibility`, so the
frames reach only an object the application's own script defines, and the HTTP
fallback never drained them.

The names are underscore-prefixed, which this policy treats as internal. They are
deprecated rather than deleted because the `accessibility` and `focus` frames are
part of the wire protocol (point 7 above) and `djust.d.ts` types
`window.djust.accessibility`, so an application can have supplied both halves.

Defining either method on a `LiveView` subclass (in its body or through a mixin)
emits one `DeprecationWarning` per hook at the `class` statement. A hook added
after the class exists (`setattr`, a decorator, an instance attribute) is not
detected and does not warn. Behaviour is unchanged until 2.0.0, where the flush is
removed. Migrate to a live region the view renders and `JS.focus()` through
`push_commands()` (see the [accessibility guide](website/guides/accessibility.md#announcing-a-change-and-moving-focus-from-the-server)).
The replacement is not identical: a live region announces only when its text
changes, so the same message sent in two turns in a row announces once, and
several messages in one turn must be rendered together. The guide shows the
workaround (alternate a no-break-space suffix).

```python
# Before
class OrderView(LiveView):
    def _drain_announcements(self):
        out, self._said = self._said, []
        return out

# After: render the message into <p role="status" aria-live="polite">{{ status }}</p>
class OrderView(LiveView):
    @event_handler()
    def save(self, **kwargs):
        self.status = "Order saved"
        self.push_commands(JS.focus("#order-summary"))
```

---
