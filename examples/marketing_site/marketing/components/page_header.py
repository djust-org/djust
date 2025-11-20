"""
Page header component.
"""
from djust.components.base import Component


class PageHeader(Component):
    """
    Page header with title, subtitle, and optional CTA.
    """

    def __init__(self, title, subtitle=None, cta_text=None, cta_url=None, centered=True):
        """
        Initialize page header.

        Args:
            title: Main page title
            subtitle: Optional subtitle/description
            cta_text: Optional call-to-action button text
            cta_url: Optional call-to-action button URL
            centered: Whether to center the header content
        """
        self.title = title
        self.subtitle = subtitle
        self.cta_text = cta_text
        self.cta_url = cta_url
        self.centered = centered

    def render(self) -> str:
        """Render page header HTML."""
        centered_class = ' text-center' if self.centered else ''

        # Build subtitle if provided
        subtitle_html = ''
        if self.subtitle:
            subtitle_html = f'<p class="page-subtitle">{self.subtitle}</p>'

        # Build CTA button if provided
        cta_html = ''
        if self.cta_text and self.cta_url:
            if self.cta_url.startswith('#') or self.cta_url.startswith('http'):
                cta_html = f'''
                    <div class="page-cta">
                        <a href="{self.cta_url}" class="btn btn-primary btn-lg">
                            {self.cta_text}
                        </a>
                    </div>
                '''
            else:
                cta_html = f'''
                    <div class="page-cta">
                        <a href="{{% url '{self.cta_url}' %}}" class="btn btn-primary btn-lg">
                            {self.cta_text}
                        </a>
                    </div>
                '''

        return f'''
            <div class="page-header{centered_class}">
                <div class="container">
                    <h1 class="page-title">{self.title}</h1>
                    {subtitle_html}
                    {cta_html}
                </div>
            </div>
        '''
