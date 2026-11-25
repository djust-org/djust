### `djust.react` / `ReactMixin` → JavaScript hooks

`djust.react` (`ReactMixin`, `react_components`, `register_react_component`)
turned a capitalized tag such as `<Counter initialCount="0" />` into a
placeholder `<div data-react-component="Counter">` that the server post-processed
and that only a demo Counter ever hydrated on the client. djust `2.0.0` removed
it (#4006), together with the client's `initReactCounters`. `from djust import
ReactMixin` (and the other two names) raises `ImportError` naming the
replacement and the migration guide, and a capitalized tag in a template is a
template error that names the same replacement. HTML reads element
names in any case, so an ordinary HTML or SVG element spelled in capitals or mixed
case (`<DIV>`, `<BR>`, `<TextArea>`) is still HTML; the component shape, a
self-closing non-void element such as `<Button label="x" />`, and a name HTML
does not have, such as `<Widget>`, are refused. Mount a
client-side widget with a hook:

<!-- doc-snippet-check: anti-pattern -->
```python
# Before (removed in 2.0, #4006)
from djust import LiveView, ReactMixin

class Page(ReactMixin, LiveView):
    template = '<div dj-root><Counter initialCount="0" /></div>'
```

```python
# After
from djust import LiveView

class Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><div dj-hook="Counter" dj-hook-value-initial="0"></div></div>'
```

```javascript
window.djust.hooks.Counter = {
  mounted() { this.el.textContent = this.el.dataset.initial || "0"; },
};
```
