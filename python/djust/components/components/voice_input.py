"""Voice Input Button component with mic recording animation."""

import html

from djust import Component


class VoiceInput(Component):
    """Mic button with recording animation for speech input.

    Renders a microphone button that uses the Web Speech API for
    voice-to-text transcription. Shows recording animation when active.

    Usage in a LiveView::

        self.mic = VoiceInput(event="transcribe", lang="en-US")

    In template::

        {{ mic|safe }}

    CSS Custom Properties::

        --dj-voice-input-size: button size (default: 3rem)
        --dj-voice-input-bg: button background (default: #f3f4f6)
        --dj-voice-input-active-bg: recording background (default: #fee2e2)
        --dj-voice-input-color: icon color (default: #374151)
        --dj-voice-input-active-color: recording icon color (default: #dc2626)
        --dj-voice-input-radius: border radius (default: 9999px)

    Args:
        event: Event name for transcription result.
        lang: BCP 47 language tag (default: en-US).
        continuous: Whether to continue recording (default: False).
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        event: str = "transcribe",
        lang: str = "en-US",
        continuous: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            event=event,
            lang=lang,
            continuous=continuous,
            custom_class=custom_class,
            **kwargs,
        )
        self.event = event
        self.lang = lang
        self.continuous = continuous
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-voice-input"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_event = html.escape(self.event)
        e_lang = html.escape(self.lang)

        mic_svg = (
            '<svg class="dj-voice-input__icon" viewBox="0 0 24 24" '
            'width="20" height="20" fill="none" stroke="currentColor" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>'
            '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>'
            '<line x1="12" y1="19" x2="12" y2="23"/>'
            '<line x1="8" y1="23" x2="16" y2="23"/>'
            "</svg>"
        )

        return (
            f'<button type="button" class="{cls}" '
            f'dj-hook="VoiceInput" '
            f'data-event="{e_event}" data-lang="{e_lang}" '
            f'data-continuous="{"true" if self.continuous else "false"}" '
            f'aria-label="Voice input" aria-pressed="false">'
            f"{mic_svg}"
            f'<span class="dj-voice-input__pulse"></span>'
            f"</button>"
        )
