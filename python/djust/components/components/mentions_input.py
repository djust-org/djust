"""Mentions / @input component for text input with user lookup on @."""

import html
import json
from typing import List, Optional

from djust import Component


class MentionsInput(Component):
    """Text input that triggers a user mention dropdown on @ character.

    Renders a text input with an associated hidden suggestion list. When the user
    types @, JavaScript (via dj-hook) triggers a lookup event. The suggestion list
    is populated from the `users` prop.

    Usage in a LiveView::

        self.msg_input = MentionsInput(
            name="message",
            users=[
                {"id": "1", "name": "Alice", "avatar": "/img/alice.jpg"},
                {"id": "2", "name": "Bob"},
            ],
            event="send_message",
        )

    In template::

        {{ msg_input|safe }}

    CSS Custom Properties::

        --dj-mentions-bg: input background
        --dj-mentions-border: border color
        --dj-mentions-radius: border-radius (default: 0.5rem)
        --dj-mentions-dropdown-bg: suggestion dropdown background
        --dj-mentions-item-hover-bg: suggestion item hover background

    Args:
        name: Input field name attribute.
        users: List of user dicts with id, name, and optional avatar.
        event: Event name fired on submit (default: "send").
        placeholder: Placeholder text.
        disabled: Whether the input is disabled.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        name: str = "message",
        users: Optional[List[dict]] = None,
        event: str = "send",
        placeholder: str = "Type @ to mention...",
        disabled: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            name=name,
            users=users,
            event=event,
            placeholder=placeholder,
            disabled=disabled,
            custom_class=custom_class,
            **kwargs,
        )
        self.name = name
        self.users = users or []
        self.event = event
        self.placeholder = placeholder
        self.disabled = disabled
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        e_name = html.escape(self.name)
        e_event = html.escape(self.event)
        e_placeholder = html.escape(self.placeholder)
        e_class = html.escape(self.custom_class) if self.custom_class else ""
        disabled_attr = " disabled" if self.disabled else ""

        cls = "dj-mentions"
        if self.disabled:
            cls += " dj-mentions--disabled"
        if e_class:
            cls += f" {e_class}"

        # Render suggestion items
        items_html = []
        for user in self.users:
            uid = html.escape(str(user.get("id", "")))
            uname = html.escape(str(user.get("name", "")))
            avatar_src = html.escape(str(user.get("avatar", "")))

            initials = (
                html.escape(
                    "".join(w[0].upper() for w in str(user.get("name", "")).split()[:2] if w)
                )
                or "?"
            )

            if avatar_src:
                avatar_html = (
                    f'<img src="{avatar_src}" alt="{uname}" class="dj-mentions__avatar-img">'
                )
            else:
                avatar_html = f'<span class="dj-mentions__avatar-initials">{initials}</span>'

            items_html.append(
                f'<li class="dj-mentions__item" data-user-id="{uid}" '
                f'data-user-name="{uname}" role="option">'
                f'<span class="dj-mentions__avatar">{avatar_html}</span>'
                f'<span class="dj-mentions__name">{uname}</span>'
                f"</li>"
            )

        # Encode users as JSON for JS hook
        users_json = html.escape(json.dumps(self.users, default=str))

        return (
            f'<div class="{cls}" dj-hook="MentionsInput" '
            f'data-users="{users_json}">'
            f'<input type="text" class="dj-mentions__input" name="{e_name}" '
            f'placeholder="{e_placeholder}" autocomplete="off"{disabled_attr} '
            f'dj-keydown.enter="{e_event}">'
            f'<ul class="dj-mentions__dropdown" role="listbox">'
            f"{''.join(items_html)}"
            f"</ul>"
            f"</div>"
        )
