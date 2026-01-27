"""
Code highlighting setup component for Prism.js.

Adapted from djust_shared/components/code_highlighting.py to use Prism.js
instead of highlight.js.
"""

from djust.components.base import Component


class CodeHighlightingSetup(Component):
    """
    Sets up syntax highlighting for all code blocks on the page using Prism.js.

    Include this ONCE per page that uses CodeTabs/CodeBlock components.
    It handles:
    - Loading Prism.js CSS and JS
    - Initial highlighting
    - Re-highlighting after LiveView DOM updates
    - Tab switching for CodeTabs component

    Usage:
        # In get_context_data():
        context['code_highlighting_setup'] = CodeHighlightingSetup()

        # In template (in {% block extra_head %}):
        {{ code_highlighting_setup.render_css|safe }}

        # In template (in {% block extra_scripts %}):
        {{ code_highlighting_setup.render_js|safe }}
    """

    def __init__(self, theme: str = "tomorrow"):
        """
        Args:
            theme: Prism.js theme name (default: tomorrow)
                   Options: default, dark, funky, okaidia, twilight, coy, solarizedlight, tomorrow
        """
        self.theme = theme

    def render_css(self) -> str:
        """Render CSS includes for Prism.js."""
        return f"""<!-- Prism.js Syntax Highlighting CSS -->
<link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-{self.theme}.min.css" rel="stylesheet" />
<link href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/line-numbers/prism-line-numbers.min.css" rel="stylesheet" />"""

    def render_js(self) -> str:
        """Render JavaScript includes and initialization for Prism.js."""
        return """<!-- Prism.js Syntax Highlighting Scripts -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/plugins/line-numbers/prism-line-numbers.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>

<script>
(function() {
    'use strict';

    function highlightCodeBlocks() {
        if (typeof Prism !== 'undefined') {
            // Re-highlight all code blocks
            Prism.highlightAll();
            console.log('[CodeHighlight] Prism syntax highlighting applied');
        }
    }

    // Initial highlighting after page load
    window.addEventListener('load', function() {
        highlightCodeBlocks();

        // Watch for LiveView updates and re-highlight
        const observer = new MutationObserver(function(mutations) {
            mutations.forEach(function(mutation) {
                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                    // Check if code blocks were added/modified
                    const hasCodeBlocks = Array.from(mutation.addedNodes).some(function(node) {
                        return node.nodeType === 1 && (
                            node.querySelector && node.querySelector('pre code') ||
                            (node.tagName === 'CODE' && node.parentElement.tagName === 'PRE')
                        );
                    });
                    if (hasCodeBlocks) {
                        console.log('[CodeHighlight] Code blocks updated by LiveView, re-highlighting...');
                        setTimeout(highlightCodeBlocks, 50);
                    }
                }
            });
        });

        // Observe the LiveView root for changes
        const liveviewRoot = document.querySelector('[data-djust-root]');
        if (liveviewRoot) {
            observer.observe(liveviewRoot, {
                childList: true,
                subtree: true
            });
        }
    });
})();
</script>"""

    def render(self) -> str:
        """Render both CSS and JS (for convenience)."""
        return self.render_css() + "\n" + self.render_js()
