### The eight component mixins → `djust.components.descriptors`

`AccordionMixin`, `TabsMixin`, `ModalMixin`, `CollapsibleMixin`, `SheetMixin`,
`DropdownMixin`, `TooltipMixin` and `CarouselMixin`, with their `*State`
classes, are gone from `djust.components` and `djust.components.mixins`
(deprecated since 1.3). Importing one raises `ImportError`. `ComponentMixin`,
`TypedState` and `DataTableMixin` stay.

Declare the matching descriptor on the view instead. It holds the state and
registers its event handler; the template reads the state off the attribute.

<!-- doc-snippet-check: anti-pattern -->
```python
# Before (removed: raises ImportError)
from djust.components.mixins import TabsMixin

class SettingsView(TabsMixin, LiveView):
    def mount(self, request, **kwargs):
        self.init_tabs("nav", active="general")
```

```python
# After
from djust import LiveView
from djust.components.descriptors import Tabs

class SettingsView(LiveView):
    nav = Tabs(active="general")  # template: {{ nav.active }}
```

Each descriptor answers the events its mixin handled, including the defaults
its tag emits (#4037): one through `Meta.event`, the rest as `@event_handler`
methods named in `Meta.events`. Each event is registered on the view as well,
so a tag's event reaches the single instance of its component without
`component_id`; with two instances of one type, the tag's `component_id=`
names the target.

| Mixin handlers | Descriptor `Meta.event` | Descriptor `Meta.events` |
| --- | --- | --- |
| `accordion_toggle` | `accordion_toggle` | none |
| `set_tab` | `set_tab` | none |
| `toggle_collapsible` | `toggle_collapsible` | none |
| `open_modal`, `close_modal`, `toggle_modal` | `toggle_modal` | `open_modal`, `close_modal` (the `{% modal %}` default `close_event`) |
| `open_sheet`, `close_sheet` | `toggle_sheet` | `open_sheet`, `close_sheet` (the `{% sheet %}` default `close_event`) |
| `toggle_dropdown`, `close_dropdown` | `toggle_dropdown` | `close_dropdown` |
| `show_tooltip`, `hide_tooltip` | `toggle_tooltip` | `show_tooltip`, `hide_tooltip` |
| `carousel_prev`, `carousel_next`, `carousel_go` | `carousel_go` | `carousel_prev`, `carousel_next` (the `{% carousel %}` default `prev_event` / `next_event`) |

The `init_*()` and `get_*_ctx()` helpers have no equivalent: the descriptor's
constructor arguments and attribute replace them.
