"""Terminal component — monospace terminal emulator with ANSI color support."""

import html
import re
from typing import Optional

from djust import Component


class Terminal(Component):
    """Monospace terminal emulator display with ANSI color support.

    Renders terminal output lines with optional ANSI color code translation
    to CSS spans. Supports streaming via ``dj-hook``.

    Usage in a LiveView::

        self.term = Terminal(
            output=["$ ls -la", "total 42", "drwxr-xr-x ..."],
            title="Build Output",
        )

    In template::

        {{ term|safe }}

    CSS Custom Properties::

        --dj-terminal-bg: background color
        --dj-terminal-fg: text color
        --dj-terminal-font-size: font size
        --dj-terminal-radius: border radius
        --dj-terminal-max-height: max height (scroll)

    Args:
        output: list of text lines to display
        title: optional title bar text
        stream_event: djust event for streaming new lines
        show_line_numbers: show line numbers (default False)
        wrap: wrap long lines (default False)
        custom_class: additional CSS classes
    """

    ANSI_RE = re.compile(r"\033\[([0-9;]*)m")
    ANSI_COLORS = {
        "30": "#000",
        "31": "#e74c3c",
        "32": "#2ecc71",
        "33": "#f1c40f",
        "34": "#3498db",
        "35": "#9b59b6",
        "36": "#1abc9c",
        "37": "#ecf0f1",
        "90": "#7f8c8d",
        "91": "#ff6b6b",
        "92": "#55efc4",
        "93": "#ffeaa7",
        "94": "#74b9ff",
        "95": "#a29bfe",
        "96": "#81ecec",
        "97": "#fff",
    }

    def __init__(
        self,
        output: Optional[list] = None,
        title: str = "",
        stream_event: str = "",
        show_line_numbers: bool = False,
        wrap: bool = False,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            output=output,
            title=title,
            stream_event=stream_event,
            show_line_numbers=show_line_numbers,
            wrap=wrap,
            custom_class=custom_class,
            **kwargs,
        )
        self.output = output or []
        self.title = title
        self.stream_event = stream_event
        self.show_line_numbers = show_line_numbers
        self.wrap = wrap
        self.custom_class = custom_class

    @classmethod
    def _ansi_to_html(cls, text: str) -> str:
        """Convert ANSI escape codes to styled spans."""
        result = []
        open_spans = 0
        last_end = 0

        for m in cls.ANSI_RE.finditer(text):
            start, end = m.span()
            # Add text before this escape (escaped)
            before = html.escape(text[last_end:start])
            result.append(before)
            last_end = end

            codes = m.group(1).split(";")
            for code in codes:
                if code == "0" or code == "":
                    # Reset
                    result.append("</span>" * open_spans)
                    open_spans = 0
                elif code == "1":
                    result.append('<span style="font-weight:bold">')
                    open_spans += 1
                elif code in cls.ANSI_COLORS:
                    color = cls.ANSI_COLORS[code]
                    result.append(f'<span style="color:{color}">')
                    open_spans += 1

        # Remaining text
        result.append(html.escape(text[last_end:]))
        result.append("</span>" * open_spans)
        return "".join(result)

    def _render_custom(self) -> str:
        classes = ["dj-terminal"]
        if self.wrap:
            classes.append("dj-terminal--wrap")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        title_html = ""
        if self.title:
            e_title = html.escape(self.title)
            title_html = (
                f'<div class="dj-terminal__titlebar">'
                f'<span class="dj-terminal__title">{e_title}</span>'
                f'<span class="dj-terminal__dots">'
                f'<span class="dj-terminal__dot dj-terminal__dot--red"></span>'
                f'<span class="dj-terminal__dot dj-terminal__dot--yellow"></span>'
                f'<span class="dj-terminal__dot dj-terminal__dot--green"></span>'
                f"</span></div>"
            )

        lines_html = []
        for i, line in enumerate(self.output):
            line_text = self._ansi_to_html(str(line))
            num_html = ""
            if self.show_line_numbers:
                num_html = f'<span class="dj-terminal__line-num">{i + 1}</span>'
            lines_html.append(
                f'<div class="dj-terminal__line">{num_html}'
                f'<span class="dj-terminal__text">{line_text}</span></div>'
            )

        stream_attr = ""
        if self.stream_event:
            e_stream = html.escape(self.stream_event)
            stream_attr = f' data-stream-event="{e_stream}"'

        return (
            f'<div class="{class_str}" dj-hook="Terminal"{stream_attr}>'
            f"{title_html}"
            f'<div class="dj-terminal__body">{"".join(lines_html)}</div>'
            f"</div>"
        )
