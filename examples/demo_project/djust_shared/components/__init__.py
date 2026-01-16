"""Demo app reusable components"""

from .ui import (
    CodeBlock,
    HeroSection,
    Card,
    FeatureCard,
    FeatureGrid,
    Button,
    Section,
    BackButton,
)

from .builders import ComponentBuilder, C, demo_page_context

__all__ = [
    # Components
    'CodeBlock',
    'HeroSection',
    'Card',
    'FeatureCard',
    'FeatureGrid',
    'Button',
    'Section',
    'BackButton',
    # Builders
    'ComponentBuilder',
    'C',  # Short alias
    'demo_page_context',
]
