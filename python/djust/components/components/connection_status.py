"""ConnectionStatus component showing WebSocket connection state."""

import html

from djust import Component


class ConnectionStatus(Component):
    """Slim bar showing WebSocket state.

    Hidden when connected, yellow "Reconnecting..." when disconnected,
    green "Reconnected" flash on recovery. Hooks into djust client.js lifecycle.

    Usage in a LiveView::

        self.status_bar = ConnectionStatus()

        # Custom text
        self.status_bar = ConnectionStatus(
            reconnecting_text="Connection lost...",
            connected_text="Back online!",
        )

    In template::

        {{ status_bar|safe }}

    CSS Custom Properties::

        --dj-connection-status-bg: background when reconnecting
        --dj-connection-status-fg: text color when reconnecting
        --dj-connection-status-connected-bg: background on reconnect flash

    Args:
        reconnecting_text: Text shown while reconnecting
        connected_text: Text shown briefly after reconnection
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        reconnecting_text: str = "Reconnecting...",
        connected_text: str = "Reconnected",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            reconnecting_text=reconnecting_text,
            connected_text=connected_text,
            custom_class=custom_class,
            **kwargs,
        )
        self.reconnecting_text = reconnecting_text
        self.connected_text = connected_text
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-connection-status"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_reconnecting = html.escape(self.reconnecting_text)
        e_connected = html.escape(self.connected_text)

        return (
            f'<div class="{cls}" '
            f'data-reconnecting-text="{e_reconnecting}" '
            f'data-connected-text="{e_connected}" '
            f'role="status" aria-live="polite" style="display:none">'
            f'<span class="dj-connection-status__text">{e_reconnecting}</span>'
            f"</div>"
        )
