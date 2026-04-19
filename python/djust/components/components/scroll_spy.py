"""Scroll spy component for IntersectionObserver-based active section tracking."""

import html

from djust import Component


class ScrollSpy(Component):
    """Style-agnostic scroll spy component.

    Uses IntersectionObserver to track which section is currently visible
    and fires events when the active section changes.

    Usage in a LiveView::

        self.spy = ScrollSpy(
            sections=["intro", "features", "pricing"],
            active="intro",
            active_event="section_changed",
        )

    In template::

        {{ spy|safe }}

    Args:
        sections: List of section IDs to observe
        active: Currently active section ID
        active_event: djust event fired when active section changes
        offset: Offset from top for intersection (default: "0px")
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        sections: list = None,
        active: str = "",
        active_event: str = "section_changed",
        offset: str = "0px",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            sections=sections,
            active=active,
            active_event=active_event,
            offset=offset,
            custom_class=custom_class,
            **kwargs,
        )
        self.sections = sections or []
        self.active = active
        self.active_event = active_event
        self.offset = offset
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-scroll-spy"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_event = html.escape(self.active_event)
        e_offset = html.escape(self.offset)

        import json

        sections_json = html.escape(json.dumps(self.sections))

        nav_items = []
        for section in self.sections:
            if isinstance(section, dict):
                s_id = section.get("id", "")
                s_label = section.get("label", s_id)
            else:
                s_id = str(section)
                s_label = s_id
            e_id = html.escape(str(s_id))
            e_label = html.escape(str(s_label))
            active_cls = " dj-scroll-spy__item--active" if str(s_id) == str(self.active) else ""
            nav_items.append(
                f'<a href="#{e_id}" '
                f'class="dj-scroll-spy__item{active_cls}" '
                f'data-section="{e_id}">{e_label}</a>'
            )

        return (
            f'<nav class="{class_str}" '
            f'dj-hook="ScrollSpy" '
            f'data-sections="{sections_json}" '
            f'data-event="{e_event}" '
            f'data-offset="{e_offset}" '
            f'role="navigation" aria-label="Section navigation">'
            f"{''.join(nav_items)}</nav>"
        )
