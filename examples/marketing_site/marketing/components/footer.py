"""
Footer component.
"""
from djust.components.base import Component


class Footer(Component):
    """
    Site footer with links and information.
    """

    def __init__(self):
        """Initialize footer."""
        self.footer_links = {
            'Product': [
                {'name': 'Features', 'url': 'marketing:features'},
                {'name': 'Pricing', 'url': 'marketing:pricing'},
                {'name': 'Security', 'url': 'marketing:security'},
                {'name': 'Benchmarks', 'url': 'marketing:benchmarks'},
            ],
            'Developers': [
                {'name': 'Quick Start', 'url': 'marketing:quickstart'},
                {'name': 'Documentation', 'url': '#'},
                {'name': 'Examples', 'url': 'marketing:examples'},
                {'name': 'Playground', 'url': 'marketing:playground'},
            ],
            'Resources': [
                {'name': 'Use Cases', 'url': 'marketing:use_cases'},
                {'name': 'Comparison', 'url': 'marketing:comparison'},
                {'name': 'FAQ', 'url': 'marketing:faq'},
                {'name': 'GitHub', 'url': '#'},
            ],
            'Company': [
                {'name': 'About', 'url': '#'},
                {'name': 'Blog', 'url': '#'},
                {'name': 'Contact', 'url': '#'},
                {'name': 'Privacy', 'url': '#'},
            ],
        }

    def render(self) -> str:
        """Render footer HTML."""
        # Build footer columns
        columns_html = []
        for category, links in self.footer_links.items():
            links_html = []
            for link in links:
                if link['url'].startswith('#'):
                    links_html.append(f'''
                        <li><a href="{link['url']}">{link['name']}</a></li>
                    ''')
                else:
                    links_html.append(f'''
                        <li><a href="{{% url '{link['url']}' %}}">{link['name']}</a></li>
                    ''')

            columns_html.append(f'''
                <div class="footer-column">
                    <h4>{category}</h4>
                    <ul>
                        {''.join(links_html)}
                    </ul>
                </div>
            ''')

        return f'''
            <footer class="footer">
                <div class="container">
                    <div class="footer-content">
                        <div class="footer-brand">
                            <h3>djust</h3>
                            <p>Phoenix LiveView for Django.<br/>Built with Python and Rust.</p>
                            <div class="footer-social">
                                <a href="https://github.com/yourusername/djust" target="_blank" rel="noopener">
                                    GitHub
                                </a>
                                <a href="https://twitter.com/djust" target="_blank" rel="noopener">
                                    Twitter
                                </a>
                                <a href="https://discord.gg/djust" target="_blank" rel="noopener">
                                    Discord
                                </a>
                            </div>
                        </div>
                        {''.join(columns_html)}
                    </div>
                    <div class="footer-bottom">
                        <p>&copy; 2025 djust. Released under MIT License.</p>
                    </div>
                </div>
            </footer>
        '''
