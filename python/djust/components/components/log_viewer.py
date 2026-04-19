"""Log Viewer component — monospace streaming log display."""

import html
import re
from typing import Optional

from djust import Component


class LogViewer(Component):
    """Monospace streaming log display with level coloring.

    Renders log lines with automatic level detection (INFO, WARN, ERROR, DEBUG)
    and color coding. Supports streaming via ``dj-hook``.

    Usage in a LiveView::

        self.logs = LogViewer(
            lines=["2026-03-25 INFO Server started", "2026-03-25 ERROR Timeout"],
            stream_event="new_logs",
        )

    In template::

        {{ logs|safe }}

    CSS Custom Properties::

        --dj-log-bg: background color
        --dj-log-fg: text color
        --dj-log-font-size: font size
        --dj-log-radius: border radius
        --dj-log-max-height: max height (scroll)
        --dj-log-info-color: INFO level color
        --dj-log-warn-color: WARN level color
        --dj-log-error-color: ERROR level color
        --dj-log-debug-color: DEBUG level color

    Args:
        lines: list of log line strings
        stream_event: djust event for receiving new log lines
        show_line_numbers: show line numbers (default True)
        auto_scroll: auto-scroll to bottom on new lines (default True)
        filter_level: filter to specific level (info/warn/error/debug)
        wrap: wrap long lines (default False)
        max_lines: maximum lines to display (0 = unlimited)
        custom_class: additional CSS classes
    """

    LEVEL_RE = re.compile(
        r"\b(INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL|CRITICAL)\b", re.IGNORECASE
    )

    def __init__(
        self,
        lines: Optional[list] = None,
        stream_event: str = "",
        show_line_numbers: bool = True,
        auto_scroll: bool = True,
        filter_level: str = "",
        wrap: bool = False,
        max_lines: int = 0,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            lines=lines,
            stream_event=stream_event,
            show_line_numbers=show_line_numbers,
            auto_scroll=auto_scroll,
            filter_level=filter_level,
            wrap=wrap,
            max_lines=max_lines,
            custom_class=custom_class,
            **kwargs,
        )
        self.lines = lines or []
        self.stream_event = stream_event
        self.show_line_numbers = show_line_numbers
        self.auto_scroll = auto_scroll
        self.filter_level = filter_level
        self.wrap = wrap
        self.max_lines = max_lines
        self.custom_class = custom_class

    @classmethod
    def _detect_level(cls, line: str) -> str:
        """Detect log level from line text."""
        m = cls.LEVEL_RE.search(line)
        if m:
            level = m.group(1).upper()
            if level in ("WARN", "WARNING"):
                return "warn"
            if level in ("ERROR", "FATAL", "CRITICAL"):
                return "error"
            if level == "DEBUG":
                return "debug"
            if level == "TRACE":
                return "debug"
            return "info"
        return ""

    def _render_custom(self) -> str:
        classes = ["dj-log-viewer"]
        if self.wrap:
            classes.append("dj-log-viewer--wrap")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        display_lines = self.lines
        if self.max_lines and self.max_lines > 0:
            display_lines = display_lines[-self.max_lines :]

        lines_html = []
        for i, line in enumerate(display_lines):
            line_str = str(line)
            level = self._detect_level(line_str)
            e_line = html.escape(line_str)

            if self.filter_level and level != self.filter_level.lower():
                continue

            level_cls = f" dj-log-viewer__line--{level}" if level else ""
            num_html = ""
            if self.show_line_numbers:
                num_html = f'<span class="dj-log-viewer__num">{i + 1}</span>'
            lines_html.append(
                f'<div class="dj-log-viewer__line{level_cls}">'
                f'{num_html}<span class="dj-log-viewer__text">{e_line}</span></div>'
            )

        stream_attr = ""
        if self.stream_event:
            e_stream = html.escape(self.stream_event)
            stream_attr = f' data-stream-event="{e_stream}"'

        scroll_attr = ' data-auto-scroll="true"' if self.auto_scroll else ""

        return (
            f'<div class="{class_str}" dj-hook="LogViewer"'
            f'{stream_attr}{scroll_attr} role="log" aria-live="polite">'
            f'<div class="dj-log-viewer__body">{"".join(lines_html)}</div>'
            f"</div>"
        )
