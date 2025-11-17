"""
Builder helpers for common component patterns.

These functions make it easy to create standard component configurations
without repeating boilerplate code.
"""

from typing import List, Dict, Optional
from .ui import (
    CodeBlock, HeroSection, Button, FeatureCard,
    FeatureGrid, Card, Section, BackButton
)


class ComponentBuilder:
    """Fluent API for building components."""

    @staticmethod
    def hero(title: str, subtitle: str = None, icon: str = None) -> HeroSection:
        """Quick hero section."""
        return HeroSection(title=title, subtitle=subtitle, icon=icon)

    @staticmethod
    def code(code: str, language: str = "python", filename: str = None) -> CodeBlock:
        """Quick code block."""
        return CodeBlock(code=code, language=language, filename=filename)

    @staticmethod
    def button(text: str, event: str, variant: str = "primary", icon: str = None) -> Button:
        """
        Quick button with common icon patterns.

        Args:
            icon: Short name like "plus", "minus", "reset", "arrow-left"
        """
        icon_paths = {
            "plus": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>',
            "minus": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 12H4"></path>',
            "reset": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>',
            "arrow-left": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>',
            "check": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>',
            "x": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>',
        }

        icon_svg = icon_paths.get(icon) if icon else None
        return Button(text=text, event=event, variant=variant, icon_svg=icon_svg)

    @staticmethod
    def features(*features: tuple) -> FeatureGrid:
        """
        Quick feature grid from tuples.

        Usage:
            ComponentBuilder.features(
                ("🔌", "WebSocket", "Real-time connection"),
                ("⚡", "Fast", "Sub-millisecond updates"),
            )
        """
        cards = [
            FeatureCard(icon=icon, title=title, description=desc)
            for icon, title, desc in features
        ]
        return FeatureGrid(cards)

    @staticmethod
    def back(href: str = "/demos/", text: str = "Back to Demos") -> BackButton:
        """Quick back button."""
        return BackButton(href=href, text=text)


# Convenience shortcut
C = ComponentBuilder


def demo_page_context(
    title: str,
    subtitle: str = None,
    icon: str = None,
    code_examples: List[Dict[str, str]] = None,
    features: List[tuple] = None,
    back_href: str = "/demos/",
    highlight_theme: str = "atom-one-dark"
) -> dict:
    """
    Build a complete demo page context in one call.

    This function automatically manages dependencies via the DependencyManager.
    Components declare their dependencies, and they're automatically collected
    and rendered in the template.

    Args:
        title: Hero title
        subtitle: Hero subtitle
        icon: Hero icon emoji
        code_examples: List of dicts with 'code', 'language', 'filename'
        features: List of (icon, title, description) tuples
        back_href: Back button URL
        highlight_theme: highlight.js theme (default: atom-one-dark)

    Returns:
        Dict of pre-rendered components ready for context

    Usage:
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context.update(demo_page_context(
                title="My Demo",
                subtitle="Description",
                icon="🚀",
                code_examples=[
                    {"code": "def foo():\\n    pass", "language": "python", "filename": "demo.py"}
                ],
                features=[
                    ("⚡", "Fast", "Blazing fast updates"),
                    ("🔒", "Secure", "Battle tested"),
                ],
            ))
            return context
    """
    from .dependencies import DependencyManager

    result = {}

    # Hero
    result['hero'] = HeroSection(
        title=title,
        subtitle=subtitle,
        icon=icon
    )

    # Code examples
    if code_examples:
        for i, example in enumerate(code_examples):
            key = f"code_{i}" if i > 0 else "code"
            result[key] = CodeBlock(
                code=example.get('code', ''),
                language=example.get('language', 'python'),
                filename=example.get('filename'),
                theme=highlight_theme  # Pass theme to CodeBlock
            )

    # Features
    if features:
        result['features'] = C.features(*features)

    # Back button
    result['back_btn'] = BackButton(href=back_href)

    # Auto-collect dependencies from all components
    deps = DependencyManager()
    deps.collect_from_context(result)
    deps.configure(theme=highlight_theme)  # Configure theme for highlight.js
    result['dependencies'] = deps

    return result
