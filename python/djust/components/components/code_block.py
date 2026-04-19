"""CodeBlock component."""

import html
from djust import Component


class CodeBlock(Component):
    """Code block with syntax highlighting component.

    Args:
        code: source code text
        language: programming language
        filename: optional filename display
        theme: highlight.js theme name"""

    def __init__(
        self,
        code: str = "",
        language: str = "",
        filename: str = "",
        theme: str = "github-dark",
        custom_class: str = "",
        **kwargs,
    ):
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

    def _render_custom(self) -> str:
        """Render the codeblock HTML."""
        import re

        e_language = html.escape(self.language or "text")
        e_code = html.escape(self.code)
        safe_theme = self.theme if re.match(r"^[a-zA-Z0-9_-]+$", str(self.theme)) else "github-dark"
        cls = "code-block"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"
        filename_html = (
            f'<span class="code-block-filename">{html.escape(self.filename)}</span>'
            if self.filename
            else ""
        )
        lang_html = f'<span class="code-block-lang">{e_language}</span>'
        copy_html = '<button class="code-block-copy">Copy</button>'
        return (
            f'<div class="{cls}">'
            f'<div class="code-block-header">{filename_html}{lang_html}{copy_html}</div>'
            f'<pre class="code-block-pre"><code class="language-{e_language}">{e_code}</code></pre>'
            f"</div>"
        )
