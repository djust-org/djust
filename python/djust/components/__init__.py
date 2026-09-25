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

__version__ = "1.3.0rc2"

# ---------------------------------------------------------------------------
# Core component classes (original djust.components)
# ---------------------------------------------------------------------------
import importlib
from typing import Any

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


def __getattr__(name: str) -> Any:
    """Lazily expose the component classes defined under ``components/``.

    ``djust.components`` re-exported only four of the 151 component classes, so
    the documented import — ``from djust.components import Accordion``, which
    this module's own docstring shows — worked for those four and failed for
    the rest. Callers had to reach into ``djust.components.components.<module>``,
    an internal layout whose name reads like a mistake.

    Resolved on demand rather than re-exported eagerly: the subpackage holds
    ~150 classes and importing them all at package-import time would cost every
    project that never touches them. A module-level ``__getattr__`` costs
    nothing until a name is asked for.

    Names already defined here win — ``__getattr__`` is only consulted for a
    missing attribute — so the nine that exist in both namespaces are
    unaffected.

    Two things below are deliberate. ``importlib.import_module`` is the one
    that matters: ``from . import components`` asks the import machinery for
    an attribute on THIS module, so until the submodule is bound the lookup
    re-enters here and recurses until the interpreter gives up — every
    ``hasattr(djust.components, x)`` for an absent ``x`` crashed instead of
    answering ``False``. Resolving through ``sys.modules`` cannot form that
    cycle. Refusing underscored names up front is a cheap extra: it is no
    longer load-bearing, and on its own it does NOT fix the recursion, since
    the cycle runs through ``components``, which has no underscore.
    """
    if name.startswith("_"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    # `from . import components` is what must NOT appear here. That form asks
    # the import machinery for the attribute `components` on this module, and
    # until the submodule is bound that lookup comes straight back into this
    # function, which asks again — the recursion that made any
    # `hasattr(djust.components, missing)` blow the stack. `import_module`
    # resolves through `sys.modules` instead and never consults us.
    subpackage = importlib.import_module(f"{__name__}.components")
    if name == "components":
        return subpackage

    try:
        return getattr(subpackage, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
