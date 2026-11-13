"""Voice Input Button component with mic recording animation."""

import html
from typing import Any, NamedTuple

from djust import Component

#: Shown, always, beside the microphone. A blank value cannot remove it.
DEFAULT_DISCLOSURE = (
    "Voice input uses your browser's speech recognition. Chrome and Edge may send your voice "
    "to Google or Microsoft to turn it into text. This component does not record or store audio; "
    "only final transcripts are sent to this site."
)

# Control characters and the invisible and bidirectional-override characters a transcript
# has no use for: C0 and C1 controls (tab and newline are whitespace, handled below),
# line/paragraph separators, zero-width and direction marks, bidi embeddings and isolates, the BOM.
_BAD_RANGES = (
    (0, 8),
    (11, 31),
    (127, 159),
    (8232, 8233),
    (8203, 8207),
    (8234, 8238),
    (8294, 8297),
    (65279, 65279),
    (0xD800, 0xDFFF),
)
_BAD = {code: " " for lo, hi in _BAD_RANGES for code in range(lo, hi + 1)}


def clean_transcript(value: Any, *, max_length: int = 2000) -> str:
    """The text of a voice transcript, made safe to keep: a string, control and
    bidirectional-override characters and unpaired surrogates removed, whitespace collapsed, at most
    ``max_length`` characters. Anything that is not a string becomes ``""``.

    The transcript comes from the browser (and, through it, from whatever a
    speech service returned): it is untrusted text. Show it escaped (never
    ``|safe``), and validate it again for any use beyond display.
    """
    if not isinstance(value, str):
        return ""
    text = value[: max_length * 4].translate(_BAD)
    return " ".join(text.split())[: max(0, int(max_length))]


class VoiceExtras(NamedTuple):
    """What the hook needs beyond the button the component always rendered."""

    wrap_open: str
    wrap_close: str
    button_attrs: str
    disclosure: str
    status: str
    interim: str


def voice_extras(disclosure: Any = None, max_seconds: Any = 60) -> VoiceExtras:
    """The wrapper, the disclosure line and the status region around the button.

    Shared by the component class, the Django tag and the Rust tag handler so the
    three cannot drift; the text is escaped here. A missing or blank disclosure is
    the default one: the line cannot be removed, only reworded.
    """
    text = (
        DEFAULT_DISCLOSURE
        if disclosure is None or not str(disclosure).strip()
        else str(disclosure).strip()
    )
    try:
        seconds = max(1, min(600, int(max_seconds)))
    except (ValueError, TypeError, OverflowError):
        seconds = 60
    return VoiceExtras(
        wrap_open='<span class="dj-voice-field">',
        wrap_close="</span>",
        button_attrs=f' data-max-seconds="{seconds}"',
        disclosure=f'<span class="dj-voice-input__disclosure">{html.escape(text)}</span>',
        status='<span class="dj-voice-input__status" role="status" aria-live="polite"></span>',
        interim='<span class="dj-voice-input__interim" aria-hidden="true"></span>',
    )


class VoiceInput(Component):
    """Mic button with recording animation for speech input.

    Renders a microphone button that uses the browser's Web Speech API for
    voice-to-text transcription, with a recording animation while it listens.
    Load ``djust_components/voice-input.js`` after the djust client.

    Usage in a LiveView::

        self.mic = VoiceInput(event="transcribe", lang="en-US")

    In template::

        {{ mic|safe }}

    **Privacy.** Speech recognition is done by the browser, and in Chrome and
    Edge that means the audio may be sent to the browser vendor's speech
    service. The component therefore shows a disclosure line beside the
    microphone at all times (``disclosure`` rewords it; a blank value cannot
    remove it), starts nothing until the reader presses the microphone, listens
    only while it is pressed and the page is visible and focused, and stops when
    focus leaves it, the tab is hidden or left, the page navigates, the component
    goes away, or ``max_seconds`` pass. It never records, stores or uploads
    audio (no ``getUserMedia``, no ``MediaRecorder``): only the final text is
    sent. A browser without the API gets a disabled button and the words "not supported", not a button
    that does nothing.

    **What is sent.** Each final transcript is sent as ``{text}`` to ``event``;
    interim guesses are shown to the reader and never sent. The text is
    untrusted (it comes from the browser, which got it from a speech service):
    ``djust.components.components.voice_input.clean_transcript`` removes
    control and bidirectional-override characters, collapses whitespace and caps
    the length::

        from djust.components.components.voice_input import clean_transcript

        @event_handler()
        def transcribe(self, text="", **kwargs):
            text = clean_transcript(text, max_length=500)
            if text:
                self.note += (" " if self.note else "") + text  # shown escaped, never |safe

    Problems (permission denied, no speech heard, no microphone, no connection
    to the speech service, an unsupported language) are announced in a polite
    status region next to the button, in plain words.

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
        continuous: Keep listening after each phrase until the reader presses the
            microphone again (default: False: one phrase, then it stops). It is
            still bounded by ``max_seconds`` and every stop condition above.
        custom_class: Additional CSS classes (on the button).
        disclosure: Text of the privacy line (default: see ``DEFAULT_DISCLOSURE``).
        max_seconds: Longest one listening session (default 60, at most 600).
    """

    def __init__(
        self,
        event: str = "transcribe",
        lang: str = "en-US",
        continuous: bool = False,
        custom_class: str = "",
        disclosure: Any = None,
        max_seconds: int = 60,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            event=event,
            lang=lang,
            continuous=continuous,
            custom_class=custom_class,
            disclosure=disclosure,
            max_seconds=max_seconds,
            **kwargs,
        )
        self.event = event
        self.lang = lang
        self.continuous = continuous
        self.custom_class = custom_class
        self.disclosure = disclosure
        self.max_seconds = max_seconds

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

        extras = voice_extras(self.disclosure, self.max_seconds)

        return (
            f"{extras.wrap_open}"
            f'<button type="button" class="{cls}" '
            f'dj-hook="VoiceInput" '
            f'data-event="{e_event}" data-lang="{e_lang}" '
            f'data-continuous="{"true" if self.continuous else "false"}"{extras.button_attrs} '
            f'aria-label="Voice input" aria-pressed="false">'
            f"{mic_svg}"
            f'<span class="dj-voice-input__pulse"></span>'
            f"</button>"
            f"{extras.disclosure}{extras.interim}{extras.status}{extras.wrap_close}"
        )
