"""
Reusable UI components for demo app.

These are stateless presentational components that render common UI patterns
like code blocks, hero sections, cards, etc.

These render with ``format_html``: every text value (titles, labels, icons,
hrefs, CSS values) is escaped. A value documented as HTML (``Card.content``,
``Section.content``, ``Button.icon_svg``, ``Button.extra_attrs``) is inserted
as HTML only when it is already marked safe (``format_html(...)``,
``mark_safe`` on a literal, or another component's render); a plain string is
escaped like any other value.
"""

from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

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
        super().__init__()
        self.code = code
        self.language = language
        self.filename = filename
        self.show_header = show_header
        self.theme = theme

    def render(self) -> str:
        # NOTE: The code is escaped like every other value, so a code example's
        # {{ }} and <tags> are literal characters in the HTML.
        header = ""
        if self.show_header:
            badge = self.language.upper() if self.language else "CODE"
            file_label = self.filename or self.language
            header = format_html(
                """
                <div class="code-header-modern">
                    <span>{}</span>
                    <span class="code-badge-modern">{}</span>
                </div>
            """,
                file_label,
                badge,
            )

        return format_html(
            """
            <div class="code-block-modern">
                {}
                <pre><code class="language-{}">{}</code></pre>
            </div>
        """,
            header,
            self.language,
            self.code,
        )
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
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.padding = padding

    def render(self) -> str:
        icon_html = ""
        if self.icon:
            icon_html = format_html('<div class="text-5xl">{}</div>', self.icon)

        subtitle_html = ""
        if self.subtitle:
            subtitle_html = format_html('<p class="hero-subtitle">{}</p>', self.subtitle)

        return format_html(
            """
            <section class="hero-section" style="padding: {};">
                <div class="container mx-auto px-4">
                    <div class="max-w-4xl mx-auto text-center">
                        <div class="flex items-center justify-center gap-3 mb-4">
                            {}
                            <h1 class="hero-title" style="font-size: 2.5rem; margin-bottom: 0;">{}</h1>
                        </div>
                        {}
                    </div>
                </div>
            </section>
        """,
            self.padding,
            icon_html,
            self.title,
            subtitle_html,
        )
class Card(Component):
    """
    Modern card with optional header.

    Usage:
        card = Card(
            title="Live Demo",
            subtitle="Click buttons to see instant updates",
            content=format_html("<p>{}</p>", text),  # markup: marked safe
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
        super().__init__()
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
                subtitle_html = format_html(
                    """
                    <p class="text-sm m-0 mt-2" style="color: var(--color-text-muted);">
                        {}
                    </p>
                """,
                    self.subtitle,
                )

            header = format_html(
                """
                <div class="p-4 border-b" style="border-color: var(--color-border); background: {};">
                    <h3 class="text-xl font-bold m-0" style="color: var(--color-text);">{}</h3>
                    {}
                </div>
            """,
                self.header_color,
                self.title or "",
                subtitle_html,
            )

        # content is HTML only when marked safe; a plain string is escaped.
        return format_html(
            """
            <div class="demo-card-modern">
                {}
                <div class="p-4" style="background: var(--color-bg-elevated);">
                    {}
                </div>
            </div>
        """,
            header,
            self.content,
        )
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
        super().__init__()
        self.icon = icon
        self.title = title
        self.description = description

    def render(self) -> str:
        return format_html(
            """
            <div>
                <div class="text-2xl mb-2">{}</div>
                <h4 class="font-semibold mb-1" style="color: var(--color-text); font-size: 1rem;">
                    {}
                </h4>
                <p class="text-sm" style="color: var(--color-text-muted);">
                    {}
                </p>
            </div>
        """,
            self.icon,
            self.title,
            self.description,
        )
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
        super().__init__()
        self.features = features
        self.columns = columns

    def render(self) -> str:
        # Each feature's render is already escaped (and marked safe); anything
        # else in the list is escaped here.
        features_html = format_html_join("\n", "{}", ((f.render(),) for f in self.features))

        return format_html(
            """
            <div class="feature-card-modern p-4">
                <h3 class="text-lg font-bold mb-3" style="color: var(--color-text);">How It Works</h3>
                <div class="grid grid-cols-1 md:grid-cols-{} gap-4">
                    {}
                </div>
            </div>
        """,
            self.columns,
            features_html,
        )
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
            icon_svg=mark_safe('<path d="..."></path>')  # markup: marked safe
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
        super().__init__()
        self.text = text
        self.event = event
        self.variant = variant

        # Support both short icon names and custom SVG paths
        if icon and icon in self.ICON_PATHS:
            # A constant of this class, not a value: its markup is trusted.
            self.icon_svg = mark_safe(self.ICON_PATHS[icon])
        else:
            self.icon_svg = icon_svg

        self.extra_attrs = extra_attrs or ""

    def render(self) -> str:
        css_class = "btn-%s-modern" % self.variant

        icon_html = ""
        if self.icon_svg:
            # icon_svg is HTML only when marked safe (the ICON_PATHS are).
            icon_html = format_html(
                """
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    {}
                </svg>
            """,
                self.icon_svg,
            )

        # extra_attrs is markup only when marked safe; a plain string is escaped.
        return format_html(
            """
            <button dj-click="{}" class="{}" {}>
                {}
                <span>{}</span>
            </button>
        """,
            self.event,
            css_class,
            self.extra_attrs,
            icon_html,
            self.text,
        )
class Section(Component):
    """
    Section wrapper with optional title and subtitle.

    Usage:
        section = Section(
            content=format_html("<div>{}</div>", text),  # markup: marked safe
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
        super().__init__()
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
                subtitle_html = format_html(
                    '<p class="section-subtitle-modern">{}</p>', self.subtitle
                )

            header = format_html(
                """
                <h2 class="section-title-modern">{}</h2>
                {}
            """,
                self.title,
                subtitle_html,
            )

        # content is HTML only when marked safe; a plain string is escaped.
        return format_html(
            """
            <section class="section-modern" style="padding: {};">
                <div class="container mx-auto px-4" style="max-width: {};">
                    {}
                    {}
                </div>
            </section>
        """,
            self.padding,
            self.max_width,
            header,
            self.content,
        )
class BackButton(Component):
    """
    Back button navigation component.

    Usage:
        back = BackButton(href="/demos/", text="Back to Demos")
    """

    def __init__(self, href: str = "/demos/", text: str = "Back to Demos"):
        super().__init__()
        self.href = href
        self.text = text

    def render(self) -> str:
        return format_html(
            """
            <div class="text-center mt-4">
                <a href="{}" class="btn-secondary-modern">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18"></path>
                    </svg>
                    <span>{}</span>
                </a>
            </div>
        """,
            self.href,
            self.text,
        )
