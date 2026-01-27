"""
djust Components

A comprehensive library of reusable, reactive components with framework-aware styling.

Usage:
    from djust.components import AlertComponent, CardComponent

    class MyView(LiveView):
        def mount(self, request):
            self.alert = AlertComponent(message="Welcome!", type="success")
            self.card = CardComponent(title="User Profile", body="User info here")
"""

# Core component classes
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
# UI Components
register_component("alert", AlertComponent)
register_component("badge", BadgeComponent)
register_component("button", ButtonComponent)
register_component("card", CardComponent)
register_component("dropdown", DropdownComponent)
register_component("modal", ModalComponent)
register_component("progress", ProgressComponent)
register_component("spinner", SpinnerComponent)

# Layout Components
register_component("tabs", TabsComponent)

# Data Components
register_component("table", TableComponent)
register_component("pagination", PaginationComponent)

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
]

__version__ = "0.2.0"
