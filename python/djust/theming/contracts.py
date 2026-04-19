"""
Component contracts define the structural requirements for each theme component.

Each contract specifies:
- Required context variables (name, type, default value)
- Required HTML elements that must appear in rendered output
- Required accessibility attributes
- Available slot variables for composability

These contracts are used by the test harness to validate that component templates
(both default and theme-overridden) meet structural and accessibility requirements.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ContextVar:
    """A context variable expected by a component template."""

    name: str
    type: str  # e.g. "str", "bool", "Optional[str]"
    default: Any = None
    required: bool = False


@dataclass(frozen=True)
class RequiredElement:
    """An HTML element that must exist in the rendered output."""

    tag: str
    attrs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AccessibilityRequirement:
    """An accessibility attribute that must exist in the rendered output."""

    description: str
    # CSS-like selector hint (for documentation), e.g. "button", "[role=alert]"
    selector_hint: str
    attr: str
    # If value is None, the attr just needs to be present.
    value: Optional[str] = None


@dataclass(frozen=True)
class ComponentContract:
    """Full contract for a single component."""

    name: str
    required_context: tuple[ContextVar, ...] = ()
    optional_context: tuple[ContextVar, ...] = ()
    required_elements: tuple[RequiredElement, ...] = ()
    accessibility: tuple[AccessibilityRequirement, ...] = ()
    available_slots: tuple[str, ...] = ()
    category: str = "Core UI"


# ---------------------------------------------------------------------------
# Contract definitions
# ---------------------------------------------------------------------------

BUTTON_CONTRACT = ComponentContract(
    name="button",
    required_context=(ContextVar(name="text", type="str", required=True),),
    optional_context=(
        ContextVar(name="variant", type="str", default="primary"),
        ContextVar(name="size", type="str", default="md"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_icon", type="str", default=None),
        ContextVar(name="slot_content", type="str", default=None),
        ContextVar(name="slot_loading", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="button"),),
    accessibility=(),
    available_slots=("slot_icon", "slot_content", "slot_loading"),
    category="Core UI",
)

CARD_CONTRACT = ComponentContract(
    name="card",
    required_context=(),
    optional_context=(
        ContextVar(name="title", type="Optional[str]", default=None),
        ContextVar(name="content", type="str", default=""),
        ContextVar(name="footer", type="Optional[str]", default=None),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_header", type="str", default=None),
        ContextVar(name="slot_body", type="str", default=None),
        ContextVar(name="slot_footer", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div"),),
    accessibility=(),
    available_slots=("slot_header", "slot_body", "slot_footer"),
    category="Core UI",
)

ALERT_CONTRACT = ComponentContract(
    name="alert",
    required_context=(ContextVar(name="message", type="str", required=True),),
    optional_context=(
        ContextVar(name="title", type="Optional[str]", default=None),
        ContextVar(name="variant", type="str", default="default"),
        ContextVar(name="dismissible", type="bool", default=False),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_icon", type="str", default=None),
        ContextVar(name="slot_message", type="str", default=None),
        ContextVar(name="slot_actions", type="str", default=None),
        ContextVar(name="slot_dismiss", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div", attrs={"role": "alert"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Alert container must have role=alert",
            selector_hint="div",
            attr="role",
            value="alert",
        ),
    ),
    available_slots=("slot_icon", "slot_message", "slot_actions", "slot_dismiss"),
    category="Core UI",
)

BADGE_CONTRACT = ComponentContract(
    name="badge",
    required_context=(ContextVar(name="text", type="str", required=True),),
    optional_context=(
        ContextVar(name="variant", type="str", default="default"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_content", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="span"),),
    accessibility=(),
    available_slots=("slot_content",),
    category="Core UI",
)

INPUT_CONTRACT = ComponentContract(
    name="input",
    required_context=(ContextVar(name="name", type="str", required=True),),
    optional_context=(
        ContextVar(name="label", type="Optional[str]", default=None),
        ContextVar(name="placeholder", type="str", default=""),
        ContextVar(name="type", type="str", default="text"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
        ContextVar(name="slot_input", type="str", default=None),
        ContextVar(name="slot_help_text", type="str", default=None),
        ContextVar(name="slot_error", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div"),),
    accessibility=(
        AccessibilityRequirement(
            description="Label must reference input via for attribute",
            selector_hint="label",
            attr="for",
        ),
    ),
    available_slots=("slot_label", "slot_input", "slot_help_text", "slot_error"),
    category="Core UI",
)


MODAL_CONTRACT = ComponentContract(
    name="modal",
    required_context=(ContextVar(name="id", type="str", required=True),),
    optional_context=(
        ContextVar(name="title", type="Optional[str]", default=None),
        ContextVar(name="size", type="str", default="md"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_header", type="str", default=None),
        ContextVar(name="slot_body", type="str", default=None),
        ContextVar(name="slot_footer", type="str", default=None),
        ContextVar(name="slot_close", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div", attrs={"role": "dialog"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Modal must have role=dialog",
            selector_hint="div",
            attr="role",
            value="dialog",
        ),
        AccessibilityRequirement(
            description="Modal must have aria-modal=true",
            selector_hint="div",
            attr="aria-modal",
            value="true",
        ),
    ),
    available_slots=("slot_header", "slot_body", "slot_footer", "slot_close"),
    category="Core UI",
)

DROPDOWN_CONTRACT = ComponentContract(
    name="dropdown",
    required_context=(
        ContextVar(name="id", type="str", required=True),
        ContextVar(name="label", type="str", required=True),
    ),
    optional_context=(
        ContextVar(name="align", type="str", default="left"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_trigger", type="str", default=None),
        ContextVar(name="slot_menu", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="div"),
        RequiredElement(tag="button", attrs={"aria-haspopup": "true"}),
    ),
    accessibility=(
        AccessibilityRequirement(
            description="Trigger must have aria-haspopup=true",
            selector_hint="button",
            attr="aria-haspopup",
            value="true",
        ),
        AccessibilityRequirement(
            description="Trigger must have aria-expanded",
            selector_hint="button",
            attr="aria-expanded",
        ),
    ),
    available_slots=("slot_trigger", "slot_menu"),
    category="Core UI",
)

TABS_CONTRACT = ComponentContract(
    name="tabs",
    required_context=(
        ContextVar(name="id", type="str", required=True),
        ContextVar(name="tabs", type="list", required=True),
    ),
    optional_context=(
        ContextVar(name="active", type="int", default=0),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
    ),
    required_elements=(RequiredElement(tag="div"),),
    accessibility=(
        AccessibilityRequirement(
            description="Tab list must have role=tablist",
            selector_hint="div",
            attr="role",
            value="tablist",
        ),
    ),
    available_slots=(),
    category="Core UI",
)

TABLE_CONTRACT = ComponentContract(
    name="table",
    required_context=(
        ContextVar(name="headers", type="list", required=True),
        ContextVar(name="rows", type="list", required=True),
    ),
    optional_context=(
        ContextVar(name="variant", type="str", default="default"),
        ContextVar(name="caption", type="Optional[str]", default=None),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_caption", type="str", default=None),
        ContextVar(name="slot_header", type="str", default=None),
        ContextVar(name="slot_body", type="str", default=None),
        ContextVar(name="slot_footer", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="div"),
        RequiredElement(tag="table"),
    ),
    accessibility=(),
    available_slots=("slot_caption", "slot_header", "slot_body", "slot_footer"),
    category="Core UI",
)

PAGINATION_CONTRACT = ComponentContract(
    name="pagination",
    required_context=(
        ContextVar(name="current_page", type="int", required=True),
        ContextVar(name="total_pages", type="int", required=True),
        ContextVar(name="url_pattern", type="str", required=True),
    ),
    optional_context=(
        ContextVar(name="show_edges", type="bool", default=True),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_prev", type="str", default=None),
        ContextVar(name="slot_next", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="nav", attrs={"aria-label": "Pagination"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Pagination nav must have aria-label",
            selector_hint="nav",
            attr="aria-label",
            value="Pagination",
        ),
    ),
    available_slots=("slot_prev", "slot_next"),
    category="Core UI",
)


SELECT_CONTRACT = ComponentContract(
    name="select",
    required_context=(ContextVar(name="name", type="str", required=True),),
    optional_context=(
        ContextVar(name="label", type="Optional[str]", default=None),
        ContextVar(name="options", type="list", default=None),
        ContextVar(name="placeholder", type="str", default=""),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
        ContextVar(name="slot_select", type="str", default=None),
        ContextVar(name="slot_help_text", type="str", default=None),
        ContextVar(name="slot_error", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="div"),
        RequiredElement(tag="select"),
    ),
    accessibility=(
        AccessibilityRequirement(
            description="Label must reference select via for attribute",
            selector_hint="label",
            attr="for",
        ),
    ),
    available_slots=("slot_label", "slot_select", "slot_help_text", "slot_error"),
    category="Core UI",
)

TEXTAREA_CONTRACT = ComponentContract(
    name="textarea",
    required_context=(ContextVar(name="name", type="str", required=True),),
    optional_context=(
        ContextVar(name="label", type="Optional[str]", default=None),
        ContextVar(name="placeholder", type="str", default=""),
        ContextVar(name="rows", type="int", default=4),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
        ContextVar(name="slot_textarea", type="str", default=None),
        ContextVar(name="slot_help_text", type="str", default=None),
        ContextVar(name="slot_error", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="div"),
        RequiredElement(tag="textarea"),
    ),
    accessibility=(
        AccessibilityRequirement(
            description="Label must reference textarea via for attribute",
            selector_hint="label",
            attr="for",
        ),
    ),
    available_slots=("slot_label", "slot_textarea", "slot_help_text", "slot_error"),
    category="Core UI",
)

CHECKBOX_CONTRACT = ComponentContract(
    name="checkbox",
    required_context=(ContextVar(name="name", type="str", required=True),),
    optional_context=(
        ContextVar(name="label", type="str", default=""),
        ContextVar(name="description", type="Optional[str]", default=None),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
        ContextVar(name="slot_description", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="div"),
        RequiredElement(tag="input", attrs={"type": "checkbox"}),
    ),
    accessibility=(
        AccessibilityRequirement(
            description="Label must reference checkbox via for attribute",
            selector_hint="label",
            attr="for",
        ),
    ),
    available_slots=("slot_label", "slot_description"),
    category="Core UI",
)

RADIO_CONTRACT = ComponentContract(
    name="radio",
    required_context=(ContextVar(name="name", type="str", required=True),),
    optional_context=(
        ContextVar(name="label", type="Optional[str]", default=None),
        ContextVar(name="options", type="list", default=None),
        ContextVar(name="selected", type="str", default=""),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
        ContextVar(name="slot_options", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="fieldset", attrs={"role": "radiogroup"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Radio group must have role=radiogroup",
            selector_hint="fieldset",
            attr="role",
            value="radiogroup",
        ),
    ),
    available_slots=("slot_label", "slot_options"),
    category="Core UI",
)


BREADCRUMB_CONTRACT = ComponentContract(
    name="breadcrumb",
    required_context=(ContextVar(name="items", type="list", required=True),),
    optional_context=(
        ContextVar(name="separator", type="str", default="/"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_separator", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="nav", attrs={"aria-label": "Breadcrumb"}),
        RequiredElement(tag="ol"),
    ),
    accessibility=(
        AccessibilityRequirement(
            description="Breadcrumb nav must have aria-label=Breadcrumb",
            selector_hint="nav",
            attr="aria-label",
            value="Breadcrumb",
        ),
    ),
    available_slots=("slot_separator",),
    category="Navigation",
)

AVATAR_CONTRACT = ComponentContract(
    name="avatar",
    required_context=(),
    optional_context=(
        ContextVar(name="src", type="Optional[str]", default=None),
        ContextVar(name="alt", type="str", default=""),
        ContextVar(name="name", type="str", default=""),
        ContextVar(name="size", type="str", default="md"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_image", type="str", default=None),
        ContextVar(name="slot_fallback", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div"),),
    accessibility=(),
    available_slots=("slot_image", "slot_fallback"),
    category="Core UI",
)

TOAST_CONTRACT = ComponentContract(
    name="toast",
    required_context=(ContextVar(name="message", type="str", required=True),),
    optional_context=(
        ContextVar(name="variant", type="str", default="info"),
        ContextVar(name="position", type="str", default="top-right"),
        ContextVar(name="duration", type="int", default=5000),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_message", type="str", default=None),
        ContextVar(name="slot_actions", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div", attrs={"role": "status"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Toast must have role=status",
            selector_hint="div",
            attr="role",
            value="status",
        ),
        AccessibilityRequirement(
            description="Toast must have aria-live=polite",
            selector_hint="div",
            attr="aria-live",
            value="polite",
        ),
    ),
    available_slots=("slot_message", "slot_actions"),
    category="Feedback",
)

PROGRESS_CONTRACT = ComponentContract(
    name="progress",
    required_context=(),
    optional_context=(
        ContextVar(name="value", type="Optional[int]", default=None),
        ContextVar(name="max", type="int", default=100),
        ContextVar(name="label", type="str", default=""),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="div", attrs={"role": "progressbar"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Progress must have role=progressbar",
            selector_hint="div",
            attr="role",
            value="progressbar",
        ),
    ),
    available_slots=("slot_label",),
    category="Core UI",
)

SKELETON_CONTRACT = ComponentContract(
    name="skeleton",
    required_context=(),
    optional_context=(
        ContextVar(name="variant", type="str", default="text"),
        ContextVar(name="width", type="str", default="100%"),
        ContextVar(name="height", type="str", default="1rem"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
    ),
    required_elements=(RequiredElement(tag="div", attrs={"aria-hidden": "true"}),),
    accessibility=(),
    available_slots=(),
    category="Core UI",
)

TOOLTIP_CONTRACT = ComponentContract(
    name="tooltip",
    required_context=(ContextVar(name="text", type="str", required=True),),
    optional_context=(
        ContextVar(name="position", type="str", default="top"),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_content", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="span", attrs={"data-tooltip": None}),),
    accessibility=(),
    available_slots=("slot_content",),
    category="Core UI",
)


NAV_ITEM_CONTRACT = ComponentContract(
    name="nav_item",
    required_context=(
        ContextVar(name="label", type="str", required=True),
        ContextVar(name="url", type="str", required=True),
    ),
    optional_context=(
        ContextVar(name="icon", type="Optional[str]", default=None),
        ContextVar(name="active", type="Optional[bool]", default=None),
        ContextVar(name="badge", type="Optional[str]", default=None),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_icon", type="str", default=None),
        ContextVar(name="slot_badge", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="a"),),
    accessibility=(
        AccessibilityRequirement(
            description="Active nav item must have aria-current=page",
            selector_hint="a",
            attr="aria-current",
            value="page",
        ),
    ),
    available_slots=("slot_icon", "slot_badge"),
    category="Navigation",
)

NAV_GROUP_CONTRACT = ComponentContract(
    name="nav_group",
    required_context=(ContextVar(name="label", type="str", required=True),),
    optional_context=(
        ContextVar(name="items", type="list", default=None),
        ContextVar(name="icon", type="Optional[str]", default=None),
        ContextVar(name="expanded", type="bool", default=True),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_label", type="str", default=None),
        ContextVar(name="slot_items", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="details"),
        RequiredElement(tag="summary"),
    ),
    accessibility=(),
    available_slots=("slot_label", "slot_items"),
    category="Navigation",
)

NAV_CONTRACT = ComponentContract(
    name="nav",
    required_context=(),
    optional_context=(
        ContextVar(name="brand", type="Optional[str]", default=None),
        ContextVar(name="items", type="list", default=None),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_brand", type="str", default=None),
        ContextVar(name="slot_items", type="str", default=None),
        ContextVar(name="slot_actions", type="str", default=None),
    ),
    required_elements=(RequiredElement(tag="nav", attrs={"role": "navigation"}),),
    accessibility=(
        AccessibilityRequirement(
            description="Nav must have role=navigation",
            selector_hint="nav",
            attr="role",
            value="navigation",
        ),
        AccessibilityRequirement(
            description="Nav must have aria-label",
            selector_hint="nav",
            attr="aria-label",
            value="Main",
        ),
    ),
    available_slots=("slot_brand", "slot_items", "slot_actions"),
    category="Navigation",
)

SIDEBAR_NAV_CONTRACT = ComponentContract(
    name="sidebar_nav",
    required_context=(),
    optional_context=(
        ContextVar(name="sections", type="list", default=None),
        ContextVar(name="css_prefix", type="str", default=""),
        ContextVar(name="attrs", type="dict", default=None),
        ContextVar(name="slot_header", type="str", default=None),
        ContextVar(name="slot_sections", type="str", default=None),
        ContextVar(name="slot_footer", type="str", default=None),
    ),
    required_elements=(
        RequiredElement(tag="nav", attrs={"role": "navigation", "aria-label": "Sidebar"}),
    ),
    accessibility=(
        AccessibilityRequirement(
            description="Sidebar nav must have role=navigation",
            selector_hint="nav",
            attr="role",
            value="navigation",
        ),
        AccessibilityRequirement(
            description="Sidebar nav must have aria-label=Sidebar",
            selector_hint="nav",
            attr="aria-label",
            value="Sidebar",
        ),
    ),
    available_slots=("slot_header", "slot_sections", "slot_footer"),
    category="Navigation",
)


COMPONENT_CONTRACTS: dict[str, ComponentContract] = {
    "button": BUTTON_CONTRACT,
    "card": CARD_CONTRACT,
    "alert": ALERT_CONTRACT,
    "badge": BADGE_CONTRACT,
    "input": INPUT_CONTRACT,
    "modal": MODAL_CONTRACT,
    "dropdown": DROPDOWN_CONTRACT,
    "tabs": TABS_CONTRACT,
    "table": TABLE_CONTRACT,
    "pagination": PAGINATION_CONTRACT,
    "select": SELECT_CONTRACT,
    "textarea": TEXTAREA_CONTRACT,
    "checkbox": CHECKBOX_CONTRACT,
    "radio": RADIO_CONTRACT,
    "breadcrumb": BREADCRUMB_CONTRACT,
    "avatar": AVATAR_CONTRACT,
    "toast": TOAST_CONTRACT,
    "progress": PROGRESS_CONTRACT,
    "skeleton": SKELETON_CONTRACT,
    "tooltip": TOOLTIP_CONTRACT,
    "nav_item": NAV_ITEM_CONTRACT,
    "nav_group": NAV_GROUP_CONTRACT,
    "nav": NAV_CONTRACT,
    "sidebar_nav": SIDEBAR_NAV_CONTRACT,
}


def get_contract(component_name: str) -> ComponentContract:
    """Return the contract for a component, or raise KeyError."""
    return COMPONENT_CONTRACTS[component_name]
