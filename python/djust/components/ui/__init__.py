"""
UI components for djust.

Basic building blocks for user interfaces.

Provides both stateless (Component) and stateful (LiveComponent) variants:
- Badge: Simple stateless badge for display
- BadgeComponent: Interactive badge with dismiss functionality
- Button: Simple stateless button for display
- ButtonComponent: Interactive button with state
"""

# Stateless components (high-performance, display-only)
from .accordion_simple import Accordion
from .alert_simple import Alert
from .avatar_simple import Avatar
from .badge_simple import Badge
from .breadcrumb_simple import Breadcrumb
from .button_simple import Button
from .button_group_simple import ButtonGroup
from .card_simple import Card
from .checkbox_simple import Checkbox
from .divider_simple import Divider
from .dropdown_simple import Dropdown
from .icon_simple import Icon
from .input_simple import Input
from .list_group_simple import ListGroup
from .modal_simple import Modal
from .navbar_simple import NavBar
from .offcanvas_simple import Offcanvas
from .pagination_simple import Pagination
from .progress_simple import Progress
from .radio_simple import Radio
from .range_simple import Range
from .select_simple import Select
from .spinner_simple import Spinner
from .switch_simple import Switch
from .table_simple import Table
from .tabs_simple import Tabs
from .textarea_simple import TextArea
from .toast_simple import Toast
from .tooltip_simple import Tooltip

# Stateful components (interactive, with lifecycle)
from .alert import AlertComponent
from .badge import BadgeComponent
from .button import ButtonComponent
from .card import CardComponent
from .dropdown import DropdownComponent, DropdownItem
from .modal import ModalComponent
from .progress import ProgressComponent
from .spinner import SpinnerComponent

__all__ = [
    # Simple stateless components
    "Accordion",
    "Alert",
    "Avatar",
    "Badge",
    "Breadcrumb",
    "Button",
    "ButtonGroup",
    "Card",
    "Checkbox",
    "Divider",
    "Dropdown",
    "Icon",
    "Input",
    "ListGroup",
    "Modal",
    "NavBar",
    "Offcanvas",
    "Pagination",
    "Progress",
    "Radio",
    "Range",
    "Select",
    "Switch",
    "Spinner",
    "Table",
    "Tabs",
    "TextArea",
    "Toast",
    "Tooltip",
    # Interactive stateful components
    "AlertComponent",
    "BadgeComponent",
    "ButtonComponent",
    "CardComponent",
    "DropdownComponent",
    "DropdownItem",
    "ModalComponent",
    "ProgressComponent",
    "SpinnerComponent",
]
