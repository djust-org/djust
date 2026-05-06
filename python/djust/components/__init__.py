"""
djust Components — Comprehensive library of reusable, reactive components.

Includes both the original core component classes (Component, LiveComponent,
AlertComponent, etc.) and the djust-components library (template tags,
descriptors, mixins, rust handlers, gallery).

**Template Tags (declarative):**

    {% load djust_components %}
    {% modal id="confirm" title="Are you sure?" %}
        <p>This action cannot be undone.</p>
    {% endmodal %}

**Component Classes (programmatic):**

    from djust.components import AlertComponent, CardComponent, Badge

    class MyView(LiveView):
        def mount(self, request):
            self.alert = AlertComponent(message="Welcome!", type="success")

**Descriptors (class-attribute style):**

    from djust.components import Accordion, Tabs, Modal

**LiveViews:**

    from djust.components.ttyd import TtydTerminalView

**Component Gallery:**

    python manage.py component_gallery
"""

__version__ = "0.9.4rc5"

# ---------------------------------------------------------------------------
# Core component classes (original djust.components)
# ---------------------------------------------------------------------------
from .base import Component, LiveComponent
from .registry import (
    register_component,
    get_component,
    list_components,
    unregister_component,
)

# UI Components
from .ui import (
    AlertComponent,
    BadgeComponent,
    ButtonComponent,
    CardComponent,
    DropdownComponent,
    ModalComponent,
    ProgressComponent,
    SpinnerComponent,
)

# Layout Components
from .layout import (
    TabsComponent,
)

# Data Components
from .data import (
    TableComponent,
    PaginationComponent,
)

# Form Components
from .forms import (
    ForeignKeySelect,
    ManyToManySelect,
)

# Auto-register built-in components
register_component("alert", AlertComponent)
register_component("badge", BadgeComponent)
register_component("button", ButtonComponent)
register_component("card", CardComponent)
register_component("dropdown", DropdownComponent)
register_component("modal", ModalComponent)
register_component("progress", ProgressComponent)
register_component("spinner", SpinnerComponent)
register_component("tabs", TabsComponent)
register_component("table", TableComponent)
register_component("pagination", PaginationComponent)

# ---------------------------------------------------------------------------
# djust-components library (folded in)
# ---------------------------------------------------------------------------
from .ttyd import TtydTerminalView  # noqa: E402
from .mixins import (  # noqa: E402
    ComponentMixin,
    DataTableMixin,
    AccordionMixin,
    TabsMixin,
    ModalMixin,
    CollapsibleMixin,
    SheetMixin,
    DropdownMixin,
    TooltipMixin,
    CarouselMixin,
)
from .server_event_toast import ServerEventToastMixin  # noqa: E402
from .icons import render_icon  # noqa: E402
from .helpers import push_toast, confirm_action  # noqa: E402
from .presets import register_preset, get_preset  # noqa: E402
from .descriptors import (  # noqa: E402
    Accordion,
    Tabs,
    Modal,
    Collapsible,
    Sheet,
    Dropdown,
    Tooltip,
    Carousel,
)

__all__ = [
    # Base classes
    "Component",
    "LiveComponent",
    # Registry functions
    "register_component",
    "get_component",
    "list_components",
    "unregister_component",
    # UI Components
    "AlertComponent",
    "BadgeComponent",
    "ButtonComponent",
    "CardComponent",
    "DropdownComponent",
    "ModalComponent",
    "ProgressComponent",
    "SpinnerComponent",
    # Layout Components
    "TabsComponent",
    # Data Components
    "TableComponent",
    "PaginationComponent",
    # Form Components
    "ForeignKeySelect",
    "ManyToManySelect",
    # LiveViews
    "TtydTerminalView",
    # Descriptor components (preferred — DEP-002)
    "Accordion",
    "Tabs",
    "Modal",
    "Collapsible",
    "Sheet",
    "Dropdown",
    "Tooltip",
    "Carousel",
    # Mixins
    "ComponentMixin",
    "DataTableMixin",
    "AccordionMixin",
    "TabsMixin",
    "ModalMixin",
    "CollapsibleMixin",
    "SheetMixin",
    "DropdownMixin",
    "TooltipMixin",
    "CarouselMixin",
    "ServerEventToastMixin",
    # Helpers
    "render_icon",
    "push_toast",
    "confirm_action",
    "register_preset",
    "get_preset",
]
