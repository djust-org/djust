"""
Reusable UI components for demo app.

These are stateless presentational components that render common UI patterns
like code blocks, hero sections, cards, etc.
"""

from djust.components.base import Component
from typing import Optional, List, Dict


class CodeBlock(Component):
    """
    Syntax-highlighted code block with optional header.

    This component automatically includes highlight.js dependencies via the
    dependency management system. Just use CodeBlock and the JS/CSS will be
    included automatically in your template.

    Usage:
        code = CodeBlock(
            code="def hello():\n    print('world')",
            language="python",
            filename="example.py",
            theme="atom-one-dark"  # Optional theme
        )
        # In template: {{ code.render }}
    """

    # Declare dependency on highlight.js (auto-collected by DependencyManager)
    requires_dependencies = ['highlight.js']

    def __init__(
        self,
        code: str,
        language: str = "python",
        filename: Optional[str] = None,
        show_header: bool = True,
        theme: str = "atom-one-dark"
    ):
        self.code = code
        self.language = language
        self.filename = filename
        self.show_header = show_header
        self.theme = theme

    def render(self) -> str:
        import html

        # HTML escape the code to prevent XSS and highlight.js security warnings
        # This escapes <, >, &, quotes, etc. to their HTML entities
        escaped_code = html.escape(self.code)

        # NOTE: We do NOT need to escape Django template syntax ({{ }}) here because:
        # 1. This method returns a plain Python string (not a template)
        # 2. The string is inserted into templates with |safe filter
        # 3. Django's template engine doesn't process strings marked as safe
        # 4. The {{ }} in code examples are just literal characters in the HTML

        header = ""
        if self.show_header:
            badge = self.language.upper() if self.language else "CODE"
            file_label = self.filename or f"{self.language}"
            header = f"""
                <div class="code-header-modern">
                    <span>{file_label}</span>
                    <span class="code-badge-modern">{badge}</span>
                </div>
            """

        return f"""
            <div class="code-block-modern">
                {header}
                <pre><code class="language-{self.language}">{escaped_code}</code></pre>
            </div>
        """


class HeroSection(Component):
    """
    Hero section with icon, title, and subtitle.

    Usage:
        hero = HeroSection(
            title="Counter Demo",
            subtitle="Real-time reactive counter",
            icon="⚡"
        )
    """

    def __init__(
        self,
        title: str,
        subtitle: Optional[str] = None,
        icon: Optional[str] = None,
        padding: str = "4rem 0 2rem"
    ):
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.padding = padding

    def render(self) -> str:
        icon_html = ""
        if self.icon:
            icon_html = f'<div class="text-5xl">{self.icon}</div>'

        subtitle_html = ""
        if self.subtitle:
            subtitle_html = f'<p class="hero-subtitle">{self.subtitle}</p>'

        return f"""
            <section class="hero-section" style="padding: {self.padding};">
                <div class="container mx-auto px-4">
                    <div class="max-w-4xl mx-auto text-center">
                        <div class="flex items-center justify-center gap-3 mb-4">
                            {icon_html}
                            <h1 class="hero-title" style="font-size: 2.5rem; margin-bottom: 0;">{self.title}</h1>
                        </div>
                        {subtitle_html}
                    </div>
                </div>
            </section>
        """


class Card(Component):
    """
    Modern card with optional header.

    Usage:
        card = Card(
            title="Live Demo",
            subtitle="Click buttons to see instant updates",
            content="<p>Card content here</p>",
            header_color="rgba(59, 130, 246, 0.03)"
        )
    """

    def __init__(
        self,
        content: str,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        header_color: Optional[str] = None,
        show_header: bool = True
    ):
        self.content = content
        self.title = title
        self.subtitle = subtitle
        self.header_color = header_color or "rgba(59, 130, 246, 0.03)"
        self.show_header = show_header and (title or subtitle)

    def render(self) -> str:
        header = ""
        if self.show_header:
            subtitle_html = ""
            if self.subtitle:
                subtitle_html = f'''
                    <p class="text-sm m-0 mt-2" style="color: var(--color-text-muted);">
                        {self.subtitle}
                    </p>
                '''

            header = f'''
                <div class="p-4 border-b" style="border-color: var(--color-border); background: {self.header_color};">
                    <h3 class="text-xl font-bold m-0" style="color: var(--color-text);">{self.title}</h3>
                    {subtitle_html}
                </div>
            '''

        return f'''
            <div class="demo-card-modern">
                {header}
                <div class="p-4" style="background: var(--color-bg-elevated);">
                    {self.content}
                </div>
            </div>
        '''


class FeatureCard(Component):
    """
    Feature card with icon, title, and description.

    Usage:
        feature = FeatureCard(
            icon="🔌",
            title="WebSocket Connection",
            description="Persistent connection keeps client and server in sync"
        )
    """

    def __init__(self, icon: str, title: str, description: str):
        self.icon = icon
        self.title = title
        self.description = description

    def render(self) -> str:
        return f'''
            <div>
                <div class="text-2xl mb-2">{self.icon}</div>
                <h4 class="font-semibold mb-1" style="color: var(--color-text); font-size: 1rem;">
                    {self.title}
                </h4>
                <p class="text-sm" style="color: var(--color-text-muted);">
                    {self.description}
                </p>
            </div>
        '''


class FeatureGrid(Component):
    """
    Grid layout for feature cards.

    Usage:
        features = FeatureGrid([
            FeatureCard("🔌", "WebSocket", "Real-time connection"),
            FeatureCard("⚡", "Fast", "Sub-millisecond updates"),
        ])
    """

    def __init__(self, features: List[Component], columns: int = 3):
        self.features = features
        self.columns = columns

    def render(self) -> str:
        features_html = "\n".join([f.render() for f in self.features])

        return f'''
            <div class="feature-card-modern p-4">
                <h3 class="text-lg font-bold mb-3" style="color: var(--color-text);">How It Works</h3>
                <div class="grid grid-cols-1 md:grid-cols-{self.columns} gap-4">
                    {features_html}
                </div>
            </div>
        '''


class Button(Component):
    """
    Modern button with icon support.

    Usage:
        # Short icon names
        btn = Button(text="Increment", event="increment", variant="primary", icon="plus")

        # Or custom SVG path
        btn = Button(
            text="Custom",
            event="action",
            variant="primary",
            icon_svg='<path d="..."></path>'
        )
    """

    # Icon path mappings
    ICON_PATHS = {
        "plus": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"></path>',
        "minus": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 12H4"></path>',
        "reset": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>',
        "arrow-left": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>',
        "check": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>',
        "x": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>',
    }

    def __init__(
        self,
        text: str,
        event: str,
        variant: str = "primary",
        icon: Optional[str] = None,
        icon_svg: Optional[str] = None,
        extra_attrs: Optional[str] = None
    ):
        self.text = text
        self.event = event
        self.variant = variant

        # Support both short icon names and custom SVG paths
        if icon and icon in self.ICON_PATHS:
            self.icon_svg = self.ICON_PATHS[icon]
        else:
            self.icon_svg = icon_svg

        self.extra_attrs = extra_attrs or ""

    def render(self) -> str:
        css_class = f"btn-{self.variant}-modern"

        icon_html = ""
        if self.icon_svg:
            icon_html = f'''
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    {self.icon_svg}
                </svg>
            '''

        return f'''
            <button @click="{self.event}" class="{css_class}" {self.extra_attrs}>
                {icon_html}
                <span>{self.text}</span>
            </button>
        '''


class Section(Component):
    """
    Section wrapper with optional title and subtitle.

    Usage:
        section = Section(
            content="<div>Section content</div>",
            title="Features",
            subtitle="Everything you need"
        )
    """

    def __init__(
        self,
        content: str,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        padding: str = "4rem 0",
        max_width: str = "1200px"
    ):
        self.content = content
        self.title = title
        self.subtitle = subtitle
        self.padding = padding
        self.max_width = max_width

    def render(self) -> str:
        header = ""
        if self.title:
            subtitle_html = ""
            if self.subtitle:
                subtitle_html = f'<p class="section-subtitle-modern">{self.subtitle}</p>'

            header = f'''
                <h2 class="section-title-modern">{self.title}</h2>
                {subtitle_html}
            '''

        return f'''
            <section class="section-modern" style="padding: {self.padding};">
                <div class="container mx-auto px-4" style="max-width: {self.max_width};">
                    {header}
                    {self.content}
                </div>
            </section>
        '''


class BackButton(Component):
    """
    Back button navigation component.

    Usage:
        back = BackButton(href="/demos/", text="Back to Demos")
    """

    def __init__(self, href: str = "/demos/", text: str = "Back to Demos"):
        self.href = href
        self.text = text

    def render(self) -> str:
        return f'''
            <div class="text-center mt-4">
                <a href="{self.href}" class="btn-secondary-modern">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>
                    </svg>
                    <span>{self.text}</span>
                </a>
            </div>
        '''
