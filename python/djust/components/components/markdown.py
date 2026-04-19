"""Markdown component — renders Markdown text as sanitized HTML."""

import html

from typing import Optional

import markdown as md_lib
import nh3

from djust import Component

# Allowlist of tags safe for prose content.
_ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "strong",
    "em",
    "s",
    "del",
    "ins",
    "sup",
    "sub",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "a",
    "code",
    "pre",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "img",
    "div",
    "span",
}

# Per-tag attribute allowlist.  No style= or on* anywhere.
_ALLOWED_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align", "colspan", "rowspan"},
    "td": {"align", "colspan", "rowspan"},
    "code": {"class"},  # for syntax-highlight class names
    "pre": {"class"},
    "div": {"class"},
    "span": {"class"},
}


def _sanitize(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        link_rel=None,  # don't force rel= on every link; trust the allowlist
        url_schemes={"http", "https", "mailto"},  # blocks javascript:, data:, vbscript:
    )


class Markdown(Component):
    """Render Markdown text as sanitized HTML.

    Passes the source text directly to the markdown library so that code spans
    and fenced code blocks are escaped correctly by the library itself (e.g.
    ``\\`<<<<<<<\\``` renders as ``<<<<<<<``).  After conversion, dangerous
    tags (``<script>``, ``<iframe>``, etc.) and ``on*`` event attributes are
    stripped from the output to prevent XSS.

    The output is wrapped in a ``<div class="dj-prose">`` container. Style
    it with CSS targeting ``.dj-prose`` to control headings, lists, code
    blocks, tables, links, and blockquotes.

    Usage in a LiveView::

        self.body = Markdown(task.spec)
        self.summary = Markdown(agent.output, custom_class="text-sm")

    In template::

        {{ body|safe }}
        {{ summary|safe }}

    CSS Custom Properties::

        --dj-prose-font-size: base font size (default: 1rem)
        --dj-prose-line-height: line height (default: 1.6)
        --dj-prose-heading-color: heading color (default: var(--foreground))
        --dj-prose-link-color: link color (default: var(--primary))
        --dj-prose-code-bg: inline code background (default: var(--muted))
        --dj-prose-code-color: inline code text (default: var(--foreground))
        --dj-prose-blockquote-border: blockquote left border color (default: var(--border))
        --dj-prose-table-border: table border color (default: var(--border))

    Args:
        text: Markdown source text to render.
        custom_class: Additional CSS classes to add to the wrapper div.
        extensions: List of markdown extensions. Defaults to
            ``["fenced_code", "tables", "nl2br"]``.
    """

    _DEFAULT_EXTENSIONS = ["fenced_code", "tables", "nl2br"]

    def __init__(
        self,
        text: str = "",
        custom_class: str = "",
        extensions: Optional[list] = None,
        **kwargs,
    ):
        super().__init__(text=text, custom_class=custom_class, **kwargs)
        self.text = text
        self.custom_class = custom_class
        self._extensions = extensions if extensions is not None else self._DEFAULT_EXTENSIONS
        self._md = md_lib.Markdown(extensions=self._extensions)

    def _render_custom(self) -> str:
        if not self.text:
            return ""

        self._md.reset()

        # Let the markdown library handle all escaping (it correctly escapes <
        # and > inside code spans and fenced blocks).  Strip dangerous tags
        # from the rendered output instead of pre-escaping the source.
        body = _sanitize(self._md.convert(self.text))

        classes = ["dj-prose"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        return f'<div class="{class_str}">{body}</div>'
