"""Code Snippet component — code block with copy button and language badge."""

import html

from djust import Component


class CodeSnippet(Component):
    """Code block with copy button and language badge.

    Composes a ``<pre><code>`` block with an inline copy button and a language
    indicator badge.

    Usage in a LiveView::

        self.snippet = CodeSnippet(language="bash", code="pip install djust")

    In template::

        {{ snippet|safe }}

    CSS Custom Properties::

        --dj-code-snippet-bg: background color
        --dj-code-snippet-fg: text color
        --dj-code-snippet-border: border color
        --dj-code-snippet-radius: border radius
        --dj-code-snippet-font-size: code font size
        --dj-code-snippet-badge-bg: language badge background
        --dj-code-snippet-badge-fg: language badge text

    Args:
        code: The source code text
        language: Programming language label (e.g. "python", "bash")
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        code: str = "",
        language: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(code=code, language=language, custom_class=custom_class, **kwargs)
        self.code = code
        self.language = language
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the code snippet HTML."""
        classes = ["dj-code-snippet"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_code = html.escape(self.code)
        e_lang = html.escape(self.language)

        lang_badge = ""
        if self.language:
            lang_badge = f'<span class="dj-code-snippet__lang">{e_lang}</span>'

        return (
            f'<div class="{class_str}">'
            f'<div class="dj-code-snippet__header">'
            f"{lang_badge}"
            f'<button class="dj-code-snippet__copy" aria-label="Copy code" '
            f'type="button">Copy</button>'
            f"</div>"
            f'<pre class="dj-code-snippet__pre">'
            f'<code class="dj-code-snippet__code">{e_code}</code>'
            f"</pre>"
            f"</div>"
        )
