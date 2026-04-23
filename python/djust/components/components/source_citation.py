"""Source Citation component for inline footnote references."""

import html
from typing import Optional

from djust import Component


class SourceCitation(Component):
    """Inline footnote marker with hover popover showing source details.

    Renders a superscript citation number that reveals a popover with
    source title, URL, and relevance score on hover.

    Usage in a LiveView::

        self.cite = SourceCitation(
            index=1,
            title="API Documentation",
            url="https://docs.example.com/api",
            relevance=0.92,
        )

        # Minimal citation
        self.ref = SourceCitation(index=2, title="Wikipedia")

    In template::

        Some claim{{ cite|safe }} backed by evidence{{ ref|safe }}.

    CSS Custom Properties::

        --dj-citation-color: marker text color
        --dj-citation-bg: marker background
        --dj-citation-popover-bg: popover background
        --dj-citation-popover-shadow: popover box-shadow
        --dj-citation-radius: border radius

    Args:
        index: Citation number (displayed as superscript)
        title: Source title
        url: Source URL (optional)
        relevance: Relevance score 0-1 (optional)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        index: int = 1,
        title: str = "",
        url: str = "",
        relevance: Optional[float] = None,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            index=index,
            title=title,
            url=url,
            relevance=relevance,
            custom_class=custom_class,
            **kwargs,
        )
        self.index = index
        self.title = title
        self.url = url
        self.relevance = relevance
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        cls = "dj-citation"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_title = html.escape(self.title)
        e_url = html.escape(self.url) if self.url else ""
        idx = int(self.index) if isinstance(self.index, (int, float)) else 1

        popover_parts = []
        if e_title:
            popover_parts.append(f'<span class="dj-citation__title">{e_title}</span>')
        if e_url:
            popover_parts.append(
                f'<a class="dj-citation__url" href="{e_url}" '
                f'target="_blank" rel="noopener noreferrer">{e_url}</a>'
            )
        if self.relevance is not None:
            try:
                pct = min(100, max(0, float(self.relevance) * 100))
                popover_parts.append(
                    f'<span class="dj-citation__relevance">Relevance: {pct:.0f}%</span>'
                )
            except (ValueError, TypeError):
                # Relevance is optional; skip if not coercible to float.
                pass

        popover_html = "".join(popover_parts)

        return (
            f'<span class="{cls}">'
            f'<sup class="dj-citation__marker">[{idx}]</sup>'
            f'<span class="dj-citation__popover">{popover_html}</span>'
            f"</span>"
        )
