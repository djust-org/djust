"""Cookie consent banner component for GDPR compliance."""

import html

from djust import Component


class CookieConsent(Component):
    """Style-agnostic cookie consent banner.

    GDPR-compliant banner with accept/reject actions.

    Usage in a LiveView::

        self.cookies = CookieConsent(
            accept_event="accept_cookies",
            reject_event="reject_cookies",
        )

    In template::

        {{ cookies|safe }}

    Args:
        message: Consent message text
        accept_event: djust event for accepting cookies
        reject_event: djust event for rejecting cookies (optional)
        accept_label: Accept button text (default: "Accept")
        reject_label: Reject button text (default: "Decline")
        privacy_url: Link to privacy policy
        show_reject: Show reject button (default: True)
        position: Position variant (bottom, top)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        message: str = "We use cookies to improve your experience.",
        accept_event: str = "accept_cookies",
        reject_event: str = "",
        accept_label: str = "Accept",
        reject_label: str = "Decline",
        privacy_url: str = "",
        show_reject: bool = True,
        position: str = "bottom",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            message=message,
            accept_event=accept_event,
            reject_event=reject_event,
            accept_label=accept_label,
            reject_label=reject_label,
            privacy_url=privacy_url,
            show_reject=show_reject,
            position=position,
            custom_class=custom_class,
            **kwargs,
        )
        self.message = message
        self.accept_event = accept_event
        self.reject_event = reject_event
        self.accept_label = accept_label
        self.reject_label = reject_label
        self.privacy_url = privacy_url
        self.show_reject = show_reject
        self.position = position
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        classes = ["dj-cookie-consent", f"dj-cookie-consent--{self.position}"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_msg = html.escape(self.message)
        e_accept_event = html.escape(self.accept_event)
        e_accept_label = html.escape(self.accept_label)

        privacy_html = ""
        if self.privacy_url:
            e_url = html.escape(self.privacy_url)
            privacy_html = f' <a href="{e_url}" class="dj-cookie-consent__link">Privacy Policy</a>'

        buttons = [
            f'<button class="dj-cookie-consent__accept" '
            f'dj-click="{e_accept_event}">{e_accept_label}</button>'
        ]

        if self.show_reject and self.reject_event:
            e_reject_event = html.escape(self.reject_event)
            e_reject_label = html.escape(self.reject_label)
            buttons.append(
                f'<button class="dj-cookie-consent__reject" '
                f'dj-click="{e_reject_event}">{e_reject_label}</button>'
            )

        return (
            f'<div class="{class_str}" role="banner" aria-label="Cookie consent">'
            f'<p class="dj-cookie-consent__message">{e_msg}{privacy_html}</p>'
            f'<div class="dj-cookie-consent__actions">{"".join(buttons)}</div>'
            f"</div>"
        )
