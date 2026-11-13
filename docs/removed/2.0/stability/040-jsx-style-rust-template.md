### JSX-style `<Rust…/>` template tags → `djust.components.ui`

A template tag such as `<RustButton id="save" label="Save" />` renders through a
Rust component path that djust 2.0.0 removes. Each render of one emits a
`DeprecationWarning` (deprecated since 1.3, removed no earlier than 2.0.0). In
2.0 the tag is a template error. Render the matching `djust.components.ui`
component instead:

```python
# Before (template)
# <RustButton id="save" label="Save" variant="primary" />

# After (view)
from djust.components.ui import Button

save_button = Button("Save", variant="primary")  # template: {{ save_button }}
```
