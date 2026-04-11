"""
Django template tags for djust guided tours.

ADR-002 Phase 1c. Provides the ``{% tutorial_bubble %}`` tag that renders
a container element for tour narration — the client-side listener in
``src/28-tutorial-bubble.js`` catches ``tour:narrate`` CustomEvents
(dispatched by :class:`djust.tutorials.TutorialMixin` via
``push_commands(JS.dispatch(...))``) and updates the bubble's content.

Usage::

    {% load djust_tutorials %}

    <!-- Default bubble with auto-positioning -->
    {% tutorial_bubble %}

    <!-- Override the CSS class so app-level theming kicks in -->
    {% tutorial_bubble css_class="my-tour-bubble" %}

    <!-- Listen for a different event name (uncommon) -->
    {% tutorial_bubble event="my:tour-narrate" %}

The rendered element has ``dj-update="ignore"`` so morphdom doesn't
clobber the bubble's live content during VDOM patches — apps can also
apply their own styles via the ``#dj-tutorial-bubble`` id selector.
"""

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def tutorial_bubble(
    css_class: str = "dj-tutorial-bubble",
    event: str = "tour:narrate",
    position: str = "bottom",
) -> str:
    """
    Render the tutorial narration bubble container element.

    The element is a floating ``<div>`` that starts hidden and becomes
    visible when the tutorial dispatches a ``tour:narrate`` (or custom)
    CustomEvent. The client-side listener in ``src/28-tutorial-bubble.js``
    reads ``detail.text``, ``detail.target``, ``detail.position``,
    ``detail.step``, and ``detail.total`` from the event and updates
    the bubble's text content, positions it next to the target, and
    shows/hides it via a ``data-visible`` attribute that apps can style.

    Args:
        css_class: CSS class added to the container. Default is
            ``"dj-tutorial-bubble"`` which apps can style directly.
            Override to use a different class (e.g. app-specific
            theming from ``djust-theming`` or Tailwind utilities).
        event: Name of the CustomEvent the bubble listens for.
            Default ``"tour:narrate"`` matches
            :attr:`TutorialStep.narrate_event`. Change only if your
            tour uses a custom event name throughout.
        position: Default position hint when the event's ``detail``
            doesn't specify one. One of ``"top"``, ``"bottom"``,
            ``"left"``, ``"right"``. Default ``"bottom"``.

    Returns:
        Safe HTML string for the bubble ``<div>``.

    Example:

        .. code-block:: django

            {% load djust_tutorials %}
            <body>
                {{ content }}
                {% tutorial_bubble %}
            </body>

        Then in CSS::

            .dj-tutorial-bubble {
                position: absolute;
                padding: 12px 16px;
                background: #1e293b;
                color: white;
                border-radius: 8px;
                max-width: 320px;
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
                z-index: 10000;
                display: none;
            }
            .dj-tutorial-bubble[data-visible="true"] {
                display: block;
            }
    """
    if position not in ("top", "bottom", "left", "right"):
        position = "bottom"

    return format_html(
        '<div id="dj-tutorial-bubble" class="{}" dj-update="ignore"'
        ' data-event="{}" data-default-position="{}" data-visible="false"'
        ' role="status" aria-live="polite">'
        '<p class="dj-tutorial-bubble__text"></p>'
        '<div class="dj-tutorial-bubble__progress">'
        '<span class="dj-tutorial-bubble__step"></span>'
        "</div>"
        '<div class="dj-tutorial-bubble__actions">'
        '<button type="button" class="dj-tutorial-bubble__skip"'
        ' dj-click="skip_tutorial">Skip</button>'
        '<button type="button" class="dj-tutorial-bubble__cancel"'
        ' dj-click="cancel_tutorial">Close</button>'
        "</div>"
        "</div>",
        css_class,
        event,
        position,
    )
