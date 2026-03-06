"""
Code block component with syntax highlighting.
"""
from djust.components.base import Component
from .code_highlighting import CodeHighlightingSetup
import html

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.formatters import HtmlFormatter
    PYGMENTS_AVAILABLE = True
except ImportError:
    PYGMENTS_AVAILABLE = False


class CodeBlock(Component):
    """
    Code block with syntax highlighting using Pygments.
    """

    def __init__(self, code, language='python', filename=None, show_line_numbers=True):
        """
        Initialize code block.

        Args:
            code: The code content to display
            language: Programming language for syntax highlighting
            filename: Optional filename to display above code
            show_line_numbers: Whether to show line numbers
        """
        super().__init__()
        self.code = code.strip()
        self.language = language
        self.filename = filename
        self.show_line_numbers = show_line_numbers

    def render(self) -> str:
        """Render code block HTML with Pygments syntax highlighting."""
        # Build filename header if provided
        filename_html = ''
        if self.filename:
            filename_html = f'''
                <div class="code-header">
                    <span class="code-filename">{self.filename}</span>
                    <button class="code-copy" data-code="{html.escape(self.code, quote=True)}">
                        Copy
                    </button>
                </div>
            '''

        # Generate highlighted code
        if PYGMENTS_AVAILABLE:
            try:
                # Get lexer for the language
                lexer = get_lexer_by_name(self.language, stripall=True)

                # Configure formatter with dark theme
                formatter = HtmlFormatter(
                    style='monokai',
                    linenos='inline' if self.show_line_numbers else False,
                    cssclass='highlight',
                    noclasses=False,
                )

                # Generate highlighted HTML
                highlighted_code = highlight(self.code, lexer, formatter)

            except Exception as e:
                # Fallback to plain escaped code if highlighting fails
                highlighted_code = f'<pre><code>{html.escape(self.code)}</code></pre>'
        else:
            # Fallback if Pygments not available
            highlighted_code = f'<pre><code>{html.escape(self.code)}</code></pre>'

        return f'''
            <div class="code-block">
                {filename_html}
                {highlighted_code}
            </div>
        '''


class CodeTabs(Component):
    """
    Tabbed code blocks for showing multiple languages/files.
    """

    def __init__(self, tabs):
        """
        Initialize code tabs.

        Args:
            tabs: List of dicts with 'label', 'code', 'language', optional 'filename'
        """
        super().__init__()
        self.tabs = tabs

    def render(self) -> str:
        """Render tabbed code blocks HTML."""
        # Build tab buttons
        tab_buttons = []
        for i, tab in enumerate(self.tabs):
            active_class = ' active' if i == 0 else ''
            tab_buttons.append(f'''
                <button class="tab-button{active_class}"
                        data-tab="tab-{i}">
                    {tab['label']}
                </button>
            ''')

        # Build tab content
        tab_contents = []
        for i, tab in enumerate(self.tabs):
            active_class = ' active' if i == 0 else ''
            code_block = CodeBlock(
                code=tab['code'],
                language=tab.get('language', 'python'),
                filename=tab.get('filename'),
            )
            tab_contents.append(f'''
                <div class="tab-content{active_class}" data-tab="tab-{i}">
                    {code_block.render()}
                </div>
            ''')

        return f'''
            <div class="code-tabs">
                <div class="tab-buttons">
                    {''.join(tab_buttons)}
                </div>
                <div class="tab-contents">
                    {''.join(tab_contents)}
                </div>
            </div>
        '''


class CodeTabsWithHighlighting(Component):
    """
    Code tabs bundled with highlighting setup.

    Convenience component that combines CodeTabs + CodeHighlightingSetup.
    The highlighting setup (CSS/JS) is only included on the first instance
    per page to avoid duplication.

    Usage:
        # In view
        code_tabs = CodeTabsWithHighlighting(tabs=[
            {'label': 'views.py', 'code': '...', 'language': 'python'},
            {'label': 'template.html', 'code': '...', 'language': 'html'},
        ])

        # In template
        {{ code_tabs.render|safe }}
    """

    # Class variable to track if highlighting setup has been included on this page
    _highlighting_included = False

    def __init__(self, tabs, theme: str = "tomorrow", include_highlighting: bool = None):
        """
        Initialize code tabs with highlighting.

        Args:
            tabs: List of dicts with 'label', 'code', 'language', optional 'filename'
            theme: Prism.js theme name (default: tomorrow)
            include_highlighting: Override automatic detection - force include/exclude setup
        """
        super().__init__()
        self.code_tabs = CodeTabs(tabs)
        self.setup = CodeHighlightingSetup(theme)

        # Determine if we should include highlighting setup
        if include_highlighting is not None:
            self.should_include_highlighting = include_highlighting
        else:
            # Auto-detect: include on first instance only
            self.should_include_highlighting = not CodeTabsWithHighlighting._highlighting_included
            if self.should_include_highlighting:
                CodeTabsWithHighlighting._highlighting_included = True

    def render(self) -> str:
        """Render code tabs with optional highlighting setup."""
        tabs_html = self.code_tabs.render()

        if self.should_include_highlighting:
            # Include CSS in <head> and JS at end
            # Note: This assumes the component is used in a template with proper structure
            return f'''
                {self.setup.render_css()}
                {tabs_html}
                {self.setup.render_js()}
            '''
        else:
            # Just render the tabs (highlighting already set up by previous instance)
            return tabs_html

    @classmethod
    def reset_highlighting_state(cls):
        """
        Reset the highlighting included flag.

        Useful for testing or if you need to manually control when highlighting
        setup is included.
        """
        cls._highlighting_included = False
