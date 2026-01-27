"""
Dependency management system for components.

This module provides a centralized registry for JavaScript and CSS dependencies
that components may require. Components declare their dependencies, and the
DependencyManager automatically collects and renders them.

Usage:
    # In component
    class CodeBlock(Component):
        requires_dependencies = ['highlight.js']

    # In view
    deps = DependencyManager()
    deps.collect_from_context(context)
    context['dependencies'] = deps

    # In template
    {{ dependencies.render_css|safe }}
    {{ dependencies.render_js|safe }}
"""

from dataclasses import dataclass
from typing import Optional, Set, Dict, Any


@dataclass
class Dependency:
    """
    Represents a single JavaScript or CSS dependency.

    Attributes:
        name: Unique identifier for the dependency
        css: Optional CSS file URL
        js: Optional JavaScript file URL
        init_js: Optional initialization JavaScript code
    """

    name: str
    css: Optional[str] = None
    js: Optional[str] = None
    init_js: Optional[str] = None


# Global registry of available dependencies
DEPENDENCY_REGISTRY: Dict[str, Dependency] = {
    "highlight.js": Dependency(
        name="highlight.js",
        css="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/{theme}.min.css",
        js="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js",
        init_js="""
(function() {
    'use strict';

    function highlightCodeBlocks() {
        if (typeof hljs !== 'undefined') {
            document.querySelectorAll('pre code:not(.hljs)').forEach(function(block) {
                hljs.highlightElement(block);
            });
            console.log('[CodeHighlight] Syntax highlighting applied');
        }
    }

    window.addEventListener('load', function() {
        highlightCodeBlocks();

        // Watch for LiveView updates and re-highlight
        const observer = new MutationObserver(function(mutations) {
            mutations.forEach(function(mutation) {
                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
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

        const liveviewRoot = document.querySelector('[data-djust-root]');
        if (liveviewRoot) {
            observer.observe(liveviewRoot, {
                childList: true,
                subtree: true
            });
        }
    });
})();
""",
    ),
    "bootstrap-tooltips": Dependency(
        name="bootstrap-tooltips",
        init_js="""
document.addEventListener('DOMContentLoaded', function() {
    const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    const tooltipList = [...tooltipTriggerList].map(el => new bootstrap.Tooltip(el));
    console.log('[Bootstrap] Tooltips initialized:', tooltipList.length);
});
""",
    ),
    # Example: Add more libraries as needed
    # 'chart.js': Dependency(
    #     name='chart.js',
    #     js='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js'
    # ),
    # 'flatpickr': Dependency(
    #     name='flatpickr',
    #     css='https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css',
    #     js='https://cdn.jsdelivr.net/npm/flatpickr'
    # ),
}


class DependencyManager:
    """
    Manages dependencies for a page render.

    Template-only object for collecting and rendering component dependencies.
    Should be excluded from LiveView state serialization.

    Collects dependencies from components and renders the appropriate
    CSS and JavaScript tags. Prevents duplicate includes.

    Example:
        deps = DependencyManager()
        deps.require('highlight.js', 'chart.js')

        # Or auto-collect from context
        deps.collect_from_context(context)

        # In template
        {{ deps.render_css|safe }}
        {{ deps.render_js|safe }}
    """

    def __init__(self):
        """Initialize with no dependencies required."""
        self._required: Set[str] = set()
        self._config: Dict[str, Any] = {}

    def require(self, *dep_names: str):
        """
        Mark dependencies as required for this page.

        Args:
            *dep_names: Names of dependencies from DEPENDENCY_REGISTRY

        Raises:
            KeyError: If a dependency name is not in the registry
        """
        for name in dep_names:
            if name not in DEPENDENCY_REGISTRY:
                raise KeyError(
                    f"Unknown dependency '{name}'. "
                    f"Available: {', '.join(DEPENDENCY_REGISTRY.keys())}"
                )
            self._required.add(name)

    def configure(self, **kwargs):
        """
        Set configuration options for dependencies.

        Example:
            deps.configure(theme='github-dark')  # For highlight.js theme
        """
        self._config.update(kwargs)

    def collect_from_context(self, context: Dict[str, Any]):
        """
        Auto-collect dependencies from Component instances in context.

        Scans all context values, finds Component instances, and collects
        their declared dependencies.

        Args:
            context: Template context dictionary
        """
        from djust.components.base import Component

        for value in context.values():
            if isinstance(value, Component):
                deps = getattr(value, "requires_dependencies", [])
                if deps:
                    self.require(*deps)

    def render_css(self) -> str:
        """
        Render all CSS link tags for required dependencies.

        Returns:
            HTML string with <link> tags, or empty string if no CSS needed
        """
        if not self._required:
            return ""

        parts = []
        for name in sorted(self._required):  # Sort for consistent output
            dep = DEPENDENCY_REGISTRY[name]
            if dep.css:
                css_url = dep.css
                # Support template variables (e.g., {theme})
                if "{theme}" in css_url:
                    theme = self._config.get("theme", "atom-one-dark")
                    css_url = css_url.format(theme=theme)
                parts.append(f'<link rel="stylesheet" href="{css_url}">')

        return "\n".join(parts)

    def render_js(self) -> str:
        """
        Render all JavaScript script tags for required dependencies.

        Returns:
            HTML string with <script> tags, or empty string if no JS needed
        """
        if not self._required:
            return ""

        parts = []
        for name in sorted(self._required):  # Sort for consistent output
            dep = DEPENDENCY_REGISTRY[name]

            # External JS file
            if dep.js:
                parts.append(f'<script src="{dep.js}"></script>')

            # Initialization code
            if dep.init_js:
                init_code = dep.init_js
                # Support template variables
                if "{theme}" in init_code:
                    theme = self._config.get("theme", "atom-one-dark")
                    init_code = init_code.format(theme=theme)
                parts.append(f"<script>{init_code}</script>")

        return "\n".join(parts)

    def render(self) -> str:
        """
        Dummy render() method for compatibility with auto-rendering loops.

        Dependencies are actually rendered in base.html via:
            {{ dependencies.render_css|safe }}
            {{ dependencies.render_js|safe }}

        This method returns empty string to avoid double-rendering.
        """
        return ""

    def __bool__(self) -> bool:
        """Return True if any dependencies are required."""
        return bool(self._required)

    def __str__(self) -> str:
        """
        String representation for JSON serialization.

        Dependencies are template-only and don't need meaningful state.
        Returns empty string for compatibility with LiveView state serialization.
        """
        return ""

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"DependencyManager(required={sorted(self._required)})"
