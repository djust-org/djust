"""
Pricing page view with interactive toggle.
"""
from djust import LiveView
from djust.decorators import event_handler, optimistic
from .base import BaseMarketingView


class PricingView(BaseMarketingView):
    """
    Pricing page with interactive billing period toggle.

    Demonstrates @optimistic decorator for instant UI updates.
    """

    template_name = 'marketing/pricing.html'
    page_slug = 'pricing'

    def mount(self, request, **kwargs):
        """Initialize pricing page state."""
        super().mount(request, **kwargs)

        # Billing period state
        self.billing_period = 'monthly'  # 'monthly' or 'annual'

        # Pricing tiers
        self.pricing_tiers = [
            {
                'name': 'Starter',
                'description': 'Perfect for small projects and prototypes',
                'monthly_price': 0,
                'annual_price': 0,
                'features': [
                    'Unlimited projects',
                    'Community support',
                    'MIT license',
                    'All core features',
                    'Basic documentation',
                ],
                'cta': 'Get Started',
                'cta_link': 'marketing:quickstart',
                'popular': False,
            },
            {
                'name': 'Professional',
                'description': 'For production applications and teams',
                'monthly_price': 49,
                'annual_price': 470,  # ~20% discount
                'features': [
                    'Everything in Starter',
                    'Priority support',
                    'Advanced components',
                    'Performance profiling',
                    'Private Slack channel',
                    'Code review assistance',
                ],
                'cta': 'Start Free Trial',
                'cta_link': '#',
                'popular': True,
            },
            {
                'name': 'Enterprise',
                'description': 'Custom solutions for large organizations',
                'monthly_price': 'Custom',
                'annual_price': 'Custom',
                'features': [
                    'Everything in Professional',
                    'Dedicated support engineer',
                    'Custom feature development',
                    'SLA guarantees',
                    'Security audit',
                    'Training workshops',
                    'Architecture consulting',
                ],
                'cta': 'Contact Sales',
                'cta_link': '#',
                'popular': False,
            },
        ]

    @event_handler()
    def toggle_billing(self, **kwargs):
        """
        Toggle between monthly and annual billing.

        Uses @optimistic to update UI immediately before server confirms.
        """
        # Toggle billing period
        self.billing_period = 'annual' if self.billing_period == 'monthly' else 'monthly'

    def get_context_data(self, **kwargs):
        """Add pricing page context with calculated prices."""
        context = super().get_context_data(**kwargs)

        # Calculate display prices based on billing period
        display_tiers = []
        for tier in self.pricing_tiers:
            tier_copy = tier.copy()

            # Calculate display price
            if isinstance(tier['monthly_price'], int) and tier['monthly_price'] > 0:
                if self.billing_period == 'annual':
                    tier_copy['display_price'] = tier['annual_price']
                    tier_copy['price_period'] = 'year'
                    # Calculate monthly equivalent for annual
                    monthly_equiv = tier['annual_price'] / 12
                    tier_copy['price_note'] = f'${monthly_equiv:.0f}/month billed annually'
                else:
                    tier_copy['display_price'] = tier['monthly_price']
                    tier_copy['price_period'] = 'month'
                    tier_copy['price_note'] = 'billed monthly'
            else:
                tier_copy['display_price'] = tier['monthly_price']
                tier_copy['price_period'] = ''
                tier_copy['price_note'] = ''

            display_tiers.append(tier_copy)

        # Calculate savings for annual billing
        annual_savings = 0
        if self.billing_period == 'annual':
            for tier in self.pricing_tiers:
                if isinstance(tier['monthly_price'], int) and tier['monthly_price'] > 0:
                    monthly_total = tier['monthly_price'] * 12
                    annual_total = tier['annual_price']
                    annual_savings += (monthly_total - annual_total)

        context.update({
            'billing_period': self.billing_period,
            'pricing_tiers': display_tiers,
            'annual_savings': annual_savings,
            'show_savings': self.billing_period == 'annual' and annual_savings > 0,
        })
        return context
