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


#: tag name -> the events it sends. Only the tags whose events have a
#: framework answer through a descriptor, plus the application actions those
#: same tags send, are listed.
TAG_EVENTS: dict[str, tuple[TagEvent, ...]] = {
    "modal": (TagEvent("close_event", "close_modal", "Modal"),),
    "sheet": (TagEvent("close_event", "close_sheet", "Sheet"),),
    "lightbox": (
        TagEvent("close_event", "close_lightbox", "Lightbox"),
        TagEvent("navigate_event", "lightbox_navigate", "Lightbox"),
    ),
    "command_palette": (
        TagEvent("close_event", "close_palette", "CommandPalette"),
        TagEvent("search_event", "palette_search"),
    ),
    "export_dialog": (
        TagEvent("close_event", "close_export", "ExportDialog"),
        TagEvent("event", "export"),
    ),
    "cookie_consent": (TagEvent("accept_event", "accept_cookies"),),
    "announcement_bar": (
        TagEvent("dismiss_event", "dismiss_announcement", "AnnouncementBar", "dismissible"),
    ),
    "nav_menu": (TagEvent("toggle_event", "toggle_nav", "NavMenu"),),
    "expandable_text": (TagEvent("toggle_event", "toggle_expand", "ExpandableText"),),
    "truncated_list": (TagEvent("toggle_event", "toggle_list", "TruncatedList"),),
    "markdown_textarea": (TagEvent("toggle_event", "toggle_preview", "MarkdownTextarea"),),
    "stepper": (TagEvent("event", "set_step", "Stepper"),),
    "wizard": (TagEvent("event", "set_step", "Stepper"),),
}
