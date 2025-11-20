"""
Navigation bar component.
"""
from djust.components.base import Component


class Navbar(Component):
    """
    Site navigation bar.

    Displays navigation menu with active page highlighting.
    """

    def __init__(self, nav_items, active_page, github_stars=0):
        """
        Initialize navbar.

        Args:
            nav_items: List of navigation items with 'name', 'url', 'slug'
            active_page: Slug of currently active page
            github_stars: GitHub star count to display
        """
        self.nav_items = nav_items
        self.active_page = active_page
        self.github_stars = github_stars

    def render(self) -> str:
        """Render navigation bar HTML."""
        # Build nav items HTML
        nav_html = []
        for item in self.nav_items:
            active_class = ' active' if item['slug'] == self.active_page else ''
            nav_html.append(f'''
                <a href="{{% url '{item['url']}' %}}"
                   class="nav-link{active_class}">
                    {item['name']}
                </a>
            ''')

        # GitHub stars badge
        stars_badge = ''
        if self.github_stars > 0:
            stars_badge = f'''
                <a href="https://github.com/yourusername/djust"
                   class="github-stars"
                   target="_blank"
                   rel="noopener">
                    ⭐ {self.github_stars:,}
                </a>
            '''

        return f'''
            <nav class="navbar">
                <div class="container">
                    <div class="navbar-brand">
                        <a href="{{% url 'marketing:home' %}}" class="brand-logo">
                            <span class="brand-name">djust</span>
                            <span class="brand-tagline">Django LiveView</span>
                        </a>
                    </div>
                    <div class="navbar-menu">
                        {''.join(nav_html)}
                    </div>
                    <div class="navbar-actions">
                        {stars_badge}
                        <a href="{{% url 'marketing:quickstart' %}}"
                           class="btn btn-primary">
                            Get Started
                        </a>
                    </div>
                </div>
            </nav>
        '''
