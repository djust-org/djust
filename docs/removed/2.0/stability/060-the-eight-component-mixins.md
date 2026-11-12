### The eight component mixins → `djust.components.descriptors`

`AccordionMixin`, `TabsMixin`, `ModalMixin`, `CollapsibleMixin`, `SheetMixin`,
`DropdownMixin`, `TooltipMixin` and `CarouselMixin`, with their `*State`
classes, are gone from `djust.components` and `djust.components.mixins`
(deprecated since 1.3). Importing one raises `ImportError`. `ComponentMixin`,
`TypedState` and `DataTableMixin` stay.

Declare the matching descriptor on the view instead. It holds the state and
registers its event handler; the template reads the state off the attribute.

```python
# Before
from djust.components.mixins import TabsMixin

class SettingsView(TabsMixin, LiveView):
    def mount(self, request, **kwargs):
        self.init_tabs("nav", active="general")

# After
from djust.components.descriptors import Tabs

class SettingsView(LiveView):
    nav = Tabs(active="general")  # template: {{ nav.active }}
```

A descriptor handles one event, so it does not replace every handler a mixin
defined. The handlers with no descriptor equivalent are now the application's
to define, or to route to the descriptor's event through the tag's `*_event`
argument:

| Mixin handlers | Descriptor event | Not handled by the descriptor |
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
