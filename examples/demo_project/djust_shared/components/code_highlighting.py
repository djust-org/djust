"""
Code highlighting setup component.

This component provides all the JavaScript needed for syntax highlighting
with highlight.js, including LiveView compatibility.
"""

from djust.components.base import Component


class CodeHighlightingSetup(Component):
    """
    Sets up syntax highlighting for all code blocks on the page.

    Include this ONCE per page that uses CodeBlock components.
    It handles:
    - Loading highlight.js CSS and JS
    - Initial highlighting
    - Re-highlighting after LiveView DOM updates

    Usage:
        # In get_context_data():
        context['code_highlighting_setup'] = CodeHighlightingSetup()

        # In template (in {% block extra_css %} or {% block extra_js %}):
        {{ code_highlighting_setup|safe }}

    Or better yet, use demo_page_context() which includes it automatically!
    """

    def __init__(self, theme: str = "atom-one-dark"):
        """
        Args:
            theme: highlight.js theme name (default: atom-one-dark)
                   See: https://highlightjs.org/static/demo/
        """
        super().__init__()
        self.theme = theme

    def render(self) -> str:
        return f"""
<!-- Syntax Highlighting Setup (auto-included by CodeBlock component) -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/{self.theme}.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>
(function() {{
    'use strict';

    function highlightCodeBlocks() {{
        if (typeof hljs !== 'undefined') {{
            // Re-highlight all code blocks
            document.querySelectorAll('pre code:not(.hljs)').forEach(function(block) {{
                hljs.highlightElement(block);
            }});
            console.log('[CodeHighlight] Syntax highlighting applied');
        }}
    }}

    // Initial highlighting after page load
    window.addEventListener('load', function() {{
        highlightCodeBlocks();

        // Watch for LiveView updates and re-highlight
        const observer = new MutationObserver(function(mutations) {{
            mutations.forEach(function(mutation) {{
                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {{
                    // Check if code blocks were added/modified
                    const hasCodeBlocks = Array.from(mutation.addedNodes).some(function(node) {{
                        return node.nodeType === 1 && (
                            node.querySelector && node.querySelector('pre code') ||
                            (node.tagName === 'CODE' && node.parentElement.tagName === 'PRE')
                        );
                    }});
                    if (hasCodeBlocks) {{
                        console.log('[CodeHighlight] Code blocks updated by LiveView, re-highlighting...');
                        setTimeout(highlightCodeBlocks, 50);
                    }}
                }}
            }});
        }});

        // Observe the LiveView root for changes
        const liveviewRoot = document.querySelector('[dj-root]');
        if (liveviewRoot) {{
            observer.observe(liveviewRoot, {{
                childList: true,
                subtree: true
            }});
        }}
    }});
}})();
</script>
"""


class CodeBlockWithHighlighting(Component):
    """
    Code block that includes highlighting setup automatically.

    This is a convenience component that bundles CodeBlock + CodeHighlightingSetup.
    Use this if you only have one code block on the page.

    For multiple code blocks, use CodeBlock + CodeHighlightingSetup separately.
    """

    def __init__(self, code: str, language: str = "python", filename: str = None,
                 show_header: bool = True, theme: str = "atom-one-dark"):
        super().__init__()
        from .ui import CodeBlock
        self.code_block = CodeBlock(code, language, filename, show_header)
        self.setup = CodeHighlightingSetup(theme)

    def render(self) -> str:
        return self.code_block.render() + "\n" + self.setup.render()
