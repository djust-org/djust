"""Code Snippet component — code block with copy button and language badge."""

import html
import uuid

from djust import Component
from typing import Any


#: Pygments class prefix on the highlighted spans — `hl-k`, `hl-s`, `hl-c` …
#: styled by `.dj-code-snippet .hl-*` in components.css from the theme's own
#: custom properties, so the colours follow the preset and mode.
_HIGHLIGHT_PREFIX = "hl-"


def highlight_code(code: str, language: str = "") -> str:
    """Escaped, and highlighted when Pygments is importable and knows the
    language. Pygments is not a hard dependency of djust: without it, or for
    an unknown language, the code is HTML-escaped exactly as before. The
    element's ``textContent`` is unchanged either way, so ``dj-copy`` still
    copies the raw code."""
    if language:
        try:
            from pygments import highlight
            from pygments.formatters import HtmlFormatter
            from pygments.lexers import get_lexer_by_name
        except ImportError:  # pragma: no cover — optional dependency absent
            return html.escape(code)
        try:
            lexer = get_lexer_by_name(_LEXER_ALIASES.get(language, language), stripnl=False)
        except Exception:  # noqa: BLE001 — unknown language: plain text
            return html.escape(code)
        formatter = HtmlFormatter(nowrap=True, classprefix=_HIGHLIGHT_PREFIX)
        return str(highlight(code, lexer, formatter)).rstrip("\n")
    return html.escape(code)


#: Names the storybook and docs use that Pygments spells differently.
_LEXER_ALIASES = {
    "django": "html+django",
    "template": "html+django",
    "py": "python",
    "js": "javascript",
    "sh": "bash",
}


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
        **kwargs: Any,
    ) -> None:
        super().__init__(code=code, language=language, custom_class=custom_class, **kwargs)
        self.code = code
        self.language = language
        self.custom_class = custom_class
        # Identifies this instance's <code> block so the copy button can name it
        # as a `dj-copy` target. Private, so it stays out of the template
        # context; the same `uuid4().hex[:6]` shape the templatetag components
        # use for their per-instance ids.
        self._copy_target_id = f"dj-code-snippet-{uuid.uuid4().hex[:8]}"

    def _render_custom(self) -> str:
        """Render the code snippet HTML."""
        classes = ["dj-code-snippet"]
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_code = highlight_code(self.code, self.language)
        e_lang = html.escape(self.language)

        lang_badge = ""
        if self.language:
            lang_badge = f'<span class="dj-code-snippet__lang">{e_lang}</span>'

        # `dj-copy="#<id>"` — the framework's own client-side clipboard
        # attribute: it copies the named element's `textContent`, so the
        # clipboard gets the raw code rather than the escaped HTML the `<code>`
        # holds, and adds its "Copied!" feedback. The button previously carried
        # no handler at all, so it rendered as a working control and did
        # nothing when clicked.
        #
        # Deliberately not a server event. A clipboard write is a browser API;
        # there is nothing for the server to do, and a round-trip would only add
        # latency and a failure mode to a purely local action.
        return (
            f'<div class="{class_str}">'
            f'<div class="dj-code-snippet__header">'
            f"{lang_badge}"
            f'<button class="dj-code-snippet__copy" aria-label="Copy code" '
            f'type="button" dj-copy="#{self._copy_target_id}" '
            f'dj-copy-feedback="Copied!">Copy</button>'
            f"</div>"
            f'<pre class="dj-code-snippet__pre">'
            f'<code class="dj-code-snippet__code" id="{self._copy_target_id}">{e_code}</code>'
            f"</pre>"
            f"</div>"
        )
