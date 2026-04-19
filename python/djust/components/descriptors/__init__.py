"""
Descriptor-based components for djust LiveViews.

These components use Python's descriptor protocol to provide declarative,
type-safe state management as class attributes on LiveViews::

    from djust.components.descriptors import Accordion, Tabs, Modal

    class MyPage(LiveView):
        faq = Accordion(active="q1")
        nav = Tabs(active="overview")
        confirm = Modal()

        # self.faq.active → "q1"  (typed, IDE autocomplete)
        # Event handlers auto-registered from Meta.event
"""

from .accordion import Accordion
from .tabs import Tabs
from .modal import Modal
from .collapsible import Collapsible
from .sheet import Sheet
from .dropdown import Dropdown
from .tooltip import Tooltip
from .carousel import Carousel

__all__ = [
    "Accordion",
    "Tabs",
    "Modal",
    "Collapsible",
    "Sheet",
    "Dropdown",
    "Tooltip",
    "Carousel",
]
