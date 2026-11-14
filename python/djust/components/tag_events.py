"""The server events djust component tags send by default (#4126).

A tag such as ``{% modal %}`` draws controls that send an event
(``close_modal``) to the view that renders it. ``manage.py check`` reads this
table to report, as ``djust.T019``, a tag whose event no handler on the view
answers: a declared component (``Modal()``), an app ``@event_handler`` of that
name, or nothing at all, in which case the server rejects every click.

``tests`` render each tag with no event arguments and compare the events it
draws with this table, so the table cannot drift from the tags.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TagEvent:
    """One event argument of a component tag."""

    #: The tag argument that renames the event (``close_event``).
    argument: str
    #: The event sent when the argument is not given.
    default: str
    #: The ``djust.components.descriptors`` class that answers ``default``, or
    #: None when the event is the application's to answer.
    component: Optional[str] = None
    #: A tag argument that must be given and true for the control to be drawn
    #: (``{% announcement_bar %}`` draws its close button only when
    #: ``dismissible``), or None when the control is always drawn.
    requires: Optional[str] = None
    #: A tag argument that, given and true, replaces the event with something
    #: else (``{% tabs param="section" %}`` draws ``dj-patch`` links instead of
    #: ``set_tab``), or None.
    unless: Optional[str] = None
    #: How to declare the component, when ``Name()`` would be wrong
    #: (``{% wizard %}`` counts steps by id, so ``Stepper(active="<first step
    #: id>")``), or None for ``Name()``.
    declare: Optional[str] = None
    #: Whether the control is optional, so the argument has an off state: given
    #: ``None``, ``False`` or ``""`` the tag leaves the control out (a close or
    #: dismiss button, #4134). False where the control is what the tag is for
    #: (a toggle, a step): there such a value still draws it, and T019 says so.
    optional: bool = False


#: tag name -> the events it sends. Every tag whose events have a framework
#: answer through a descriptor, plus the application actions those same tags
#: send, is listed. Each tag listed takes ``component_id=``, which names the
#: declared component its controls drive when a view declares two of a type
#: (#4134).
TAG_EVENTS: dict[str, tuple[TagEvent, ...]] = {
    "accordion": (TagEvent("event", "accordion_toggle", "Accordion"),),
    "carousel": (
        TagEvent("prev_event", "carousel_prev", "Carousel", declare="Carousel(total=N)"),
        TagEvent("next_event", "carousel_next", "Carousel", declare="Carousel(total=N)"),
        TagEvent("go_event", "carousel_go", "Carousel", declare="Carousel(total=N)"),
    ),
    "dropdown": (TagEvent("toggle_event", "toggle_dropdown", "Dropdown"),),
    "tabs": (TagEvent("event", "set_tab", "Tabs", unless="param"),),
    "modal": (TagEvent("close_event", "close_modal", "Modal", optional=True),),
    "sheet": (TagEvent("close_event", "close_sheet", "Sheet", optional=True),),
    "lightbox": (
        TagEvent("close_event", "close_lightbox", "Lightbox", optional=True),
        TagEvent("navigate_event", "lightbox_navigate", "Lightbox"),
    ),
    "command_palette": (
        TagEvent("close_event", "close_palette", "CommandPalette", optional=True),
        TagEvent("search_event", "palette_search"),
    ),
    "export_dialog": (
        TagEvent("close_event", "close_export", "ExportDialog", optional=True),
        TagEvent("event", "export"),
    ),
    "cookie_consent": (TagEvent("accept_event", "accept_cookies"),),
    "announcement_bar": (
        TagEvent(
            "dismiss_event",
            "dismiss_announcement",
            "AnnouncementBar",
            "dismissible",
            optional=True,
        ),
    ),
    "nav_menu": (TagEvent("toggle_event", "toggle_nav", "NavMenu"),),
    "expandable_text": (TagEvent("toggle_event", "toggle_expand", "ExpandableText"),),
    "truncated_list": (TagEvent("toggle_event", "toggle_list", "TruncatedList"),),
    "markdown_textarea": (TagEvent("toggle_event", "toggle_preview", "MarkdownTextarea"),),
    "stepper": (TagEvent("event", "set_step", "Stepper"),),
    "wizard": (
        TagEvent("event", "set_step", "Stepper", declare='Stepper(active="<first step id>")'),
    ),
    # #4134
    "dropdown_menu": (TagEvent("toggle_event", "toggle_menu", "Dropdown"),),
    "split_button": (TagEvent("toggle_event", "toggle_split_menu", "Dropdown"),),
    "sidebar": (TagEvent("toggle_event", "toggle_sidebar", "Sidebar"),),
    "notification_center": (
        TagEvent("open_event", "toggle_notifications", "NotificationCenter"),
        TagEvent("mark_read_event", "mark_notification_read"),
        TagEvent("clear_event", "clear_notifications"),
    ),
    "notification_popover": (
        TagEvent("toggle_event", "toggle_notifications", "NotificationCenter"),
        TagEvent("mark_read_event", "mark_read"),
    ),
    "tree_view": (
        TagEvent("expand_event", "tree_expand", "TreeView"),
        TagEvent("select_event", "tree_select"),
    ),
    "alert": (TagEvent("event", "dismiss_alert", "Alert", "dismissible", optional=True),),
    "page_alert": (
        TagEvent("dismiss_event", "dismiss_alert", "Alert", "dismissible", optional=True),
    ),
    "toast_container": (TagEvent("dismiss_event", "dismiss_toast", "Toasts", optional=True),),
    "date_picker": (
        TagEvent("prev_event", "date_prev_month", "DatePicker"),
        TagEvent("next_event", "date_next_month", "DatePicker"),
        TagEvent("select_event", "date_select"),
    ),
}
