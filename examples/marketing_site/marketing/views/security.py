"""
Security page view.
"""
from .base import StaticMarketingView


class SecurityView(StaticMarketingView):
    """
    Security page highlighting djust's security features.

    Covers:
    - Built-in protections (CSRF, XSS, injection)
    - Best practices
    - Security architecture
    """

    template_name = 'marketing/security.html'
    page_slug = 'security'

    def mount(self, request, **kwargs):
        """Initialize security page state."""
        super().mount(request, **kwargs)

        # Hero section
        self.hero_badge = "Security by Architecture"
        self.hero_title = "The Most Secure API"
        self.hero_title_gradient = "Is No API."
        self.hero_subtitle = "Stop leaking business logic to the browser. Stop managing permissions for 50 different REST endpoints. <b>Keep your code on your server, where it belongs.</b>"

        # Glass House Problem section
        self.glass_house_description = "Modern Single Page Applications (SPAs) force you to ship your proprietary business logic to the client's browser in a JavaScript bundle."

        self.security_risks = [
            {
                'icon': '❌',
                'title': 'IP Theft:',
                'description': 'Competitors can reverse-engineer your pricing algorithms from `bundle.js`.',
            },
            {
                'icon': '❌',
                'title': 'Data Leaks:',
                'description': 'APIs often return full user objects (`password_hash`, `admin_notes`) even if the UI doesn\'t show them.',
            },
            {
                'icon': '❌',
                'title': 'Attack Surface:',
                'description': 'Every REST endpoint is a potential entry point for hackers.',
            },
        ]

        # Code example showing exposed logic
        self.exposed_code_example = """// ⚠️ EXPOSED LOGIC IN CLIENT BUNDLE

function calculateDiscount(user) {
  if (user.enterpriseTier) {
    return 0.20; // Secret discount exposed!
  }
  return 0.05;
}"""

        # Black Box Guarantee section
        self.black_box_description = "With djust, your Python logic stays safely on the server. The client receives <b>HTML pixels</b>, not logic. Your intellectual property remains a black box."

        self.black_box_features = [
            {
                'title': 'IP Protection',
                'description': 'Your proprietary algorithms never leave the data center. The browser only sees the result, never the formula. Perfect for FinTech and SaaS.',
                'icon_bg': 'bg-brand-rust/10',
                'icon_color': 'text-brand-rust',
                'icon_path': 'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
            },
            {
                'title': 'Zero Data Leaks',
                'description': 'Our <b>Rust JIT Engine</b> scans your templates. If a field (like `email`) isn\'t rendered in the HTML, it is <b>never fetched</b> from the DB. It is physically impossible to leak data you didn\'t display.',
                'icon_bg': 'bg-brand-django/10',
                'icon_color': 'text-brand-django',
                'icon_path': 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
            },
            {
                'title': 'Unified Permissions',
                'description': 'Stop duplicating validation logic in JavaScript and Python. Define permissions once in Django. If the user can\'t see it, the HTML is never generated.',
                'icon_bg': 'bg-blue-500/10',
                'icon_color': 'text-blue-400',
                'icon_path': 'M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10',
            },
        ]

        # Architecture Comparison table
        self.comparison_rows = [
            {
                'aspect': 'Code Visibility',
                'react': 'Public (Bundled JS)',
                'djust': 'Private (Server Only)',
            },
            {
                'aspect': 'Attack Surface',
                'react': 'High (Dozens of API Endpoints)',
                'djust': 'Minimal (1 WebSocket)',
            },
            {
                'aspect': 'Data Fetching',
                'react': 'Manual (Easy to over-fetch)',
                'djust': 'Automated (JIT Restricted)',
            },
            {
                'aspect': 'Validation',
                'react': 'Duplicated (Client + Server)',
                'djust': 'Unified (Server Only)',
            },
        ]

    def get_context_data(self, **kwargs):
        """Add security page context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'hero_badge': self.hero_badge,
            'hero_title': self.hero_title,
            'hero_title_gradient': self.hero_title_gradient,
            'hero_subtitle': self.hero_subtitle,
            'glass_house_description': self.glass_house_description,
            'security_risks': self.security_risks,
            'exposed_code_example': self.exposed_code_example,
            'black_box_description': self.black_box_description,
            'black_box_features': self.black_box_features,
            'comparison_rows': self.comparison_rows,
        })
        return context
