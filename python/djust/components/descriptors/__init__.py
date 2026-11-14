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
        # Event handlers auto-registered from Meta.event / Meta.events
"""

from .accordion import Accordion
from .tabs import Tabs
from .modal import Modal
from .collapsible import Collapsible
from .sheet import Sheet
from .dropdown import Dropdown
from .tooltip import Tooltip
from .carousel import Carousel
from .announcement_bar import AnnouncementBar
from .command_palette import CommandPalette
from .expandable_text import ExpandableText
from .export_dialog import ExportDialog
from .lightbox import Lightbox
from .markdown_textarea import MarkdownTextarea
from .nav_menu import NavMenu
from .stepper import Stepper
from .truncated_list import TruncatedList

__all__ = [
    "Accordion",
    "Tabs",
    "Modal",
    "Collapsible",
    "Sheet",
    "Dropdown",
    "Tooltip",
    "Carousel",
    "AnnouncementBar",
    "CommandPalette",
    "ExpandableText",
    "ExportDialog",
    "Lightbox",
    "MarkdownTextarea",
    "NavMenu",
    "Stepper",
    "TruncatedList",
]
