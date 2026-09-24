"""CodeBlock component."""

import html
import re
import uuid

from .code_snippet import highlight_code
from djust import Component
from typing import Any

# A CSS identifier that needs no escaping in an ``#id`` selector.
_CSS_IDENT = re.compile(r"-?[A-Za-z_][A-Za-z0-9_-]*")


class CodeBlock(Component):
    """Code block with syntax highlighting component.

    Args:
        code: source code text
        language: programming language
        filename: optional filename display
        theme: highlight.js theme name

    The Copy button uses djust's ``dj-copy``: it copies the ``<code>``
    element's text, which is the raw source (highlighting keeps
    ``textContent`` equal to the input). ``dj-copy`` is bound by the LiveView
    client, so the button works on a LiveView page and is inert on a plain
    Django page.
    """

    def __init__(
        self,
        code: str = "",
        language: str = "",
        filename: str = "",
        theme: str = "github-dark",
        custom_class: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            code=code,
            language=language,
            filename=filename,
            theme=theme,
            custom_class=custom_class,
            **kwargs,
        )
        self.code = code
        self.language = language
        self.filename = filename
        self.theme = theme
        self.custom_class = custom_class
        # The ``<code>`` element's id, which the Copy button's ``dj-copy``
        # names as ``#<id>``. An explicit ``id=`` is used when it is a plain CSS
        # identifier (anything else would make the selector invalid, and
        # ``dj-copy`` would copy the selector text); otherwise a per-instance
        # id, the same shape ``CodeSnippet`` uses, so two blocks on one page
        # never copy each other's code. Private: not template context.
        explicit = self._explicit_id or ""
        self._copy_target_id = (
            explicit if _CSS_IDENT.fullmatch(explicit) else f"dj-code-block-{uuid.uuid4().hex[:8]}"
        )

    def _render_custom(self) -> str:
        """Render the codeblock HTML."""
        e_language = html.escape(self.language or "text")
        e_code = highlight_code(self.code, self.language)
        cls = "code-block"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        filename_html = (
            f'<span class="code-block-filename">{html.escape(self.filename)}</span>'
            if self.filename
            else ""
        )
        lang_html = f'<span class="code-block-lang">{e_language}</span>'
        # `dj-copy="#<id>"` copies the named element's textContent. The button
        # used to carry no handler at all, so it did nothing when clicked (#3008).
        copy_html = (
            '<button type="button" class="code-block-copy" aria-label="Copy code" '
            f'dj-copy="#{self._copy_target_id}" dj-copy-feedback="Copied!">Copy</button>'
        )
        return (
            f'<div class="{cls}">'
            f'<div class="code-block-header">{filename_html}{lang_html}{copy_html}</div>'
            f'<pre class="code-block-pre"><code class="language-{e_language}" id="{self._copy_target_id}">{e_code}</code></pre>'
            f"</div>"
        )
