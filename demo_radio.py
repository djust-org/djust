"""
Demo of Radio component for djust.

This script demonstrates the various features of the Radio component.
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

from django.conf import settings
if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='demo-secret-key',
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [],
            'APP_DIRS': True,
        }],
    )
    django.setup()

from djust.components.ui.radio_simple import Radio


def print_section(title):
    """Print a section divider."""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print('=' * 80)


def main():
    """Run the Radio component demo."""
    print("\n" + "=" * 80)
    print("  Radio Component Demo")
    print("=" * 80)

    # Example 1: Basic radio button group
    print_section("Example 1: Basic Radio Button Group")
    radio1 = Radio(
        name="size",
        label="Select Size",
        options=[
            {'value': 's', 'label': 'Small'},
            {'value': 'm', 'label': 'Medium'},
            {'value': 'l', 'label': 'Large'},
        ],
    )
    print(radio1.render())

    # Example 2: With pre-selected value
    print_section("Example 2: Pre-selected Value")
    radio2 = Radio(
        name="priority",
        label="Select Priority",
        options=[
            {'value': 'low', 'label': 'Low'},
            {'value': 'medium', 'label': 'Medium'},
            {'value': 'high', 'label': 'High'},
        ],
        value="medium"
    )
    print(radio2.render())

    # Example 3: Inline layout
    print_section("Example 3: Inline Layout")
    radio3 = Radio(
        name="answer",
        label="Do you agree?",
        options=[
            {'value': 'yes', 'label': 'Yes'},
            {'value': 'no', 'label': 'No'},
            {'value': 'maybe', 'label': 'Maybe'},
        ],
        inline=True,
        value="yes"
    )
    print(radio3.render())

    # Example 4: With disabled option
    print_section("Example 4: With Disabled Option")
    radio4 = Radio(
        name="plan",
        label="Choose a Plan",
        options=[
            {'value': 'free', 'label': 'Free'},
            {'value': 'pro', 'label': 'Pro ($9.99/mo)'},
            {'value': 'enterprise', 'label': 'Enterprise (Contact Sales)', 'disabled': True},
        ],
        value="free",
        help_text="Upgrade to Pro for advanced features"
    )
    print(radio4.render())

    # Example 5: Payment method selector
    print_section("Example 5: Payment Method Selector")
    radio5 = Radio(
        name="payment_method",
        label="Payment Method",
        options=[
            {'value': 'credit_card', 'label': 'Credit Card'},
            {'value': 'paypal', 'label': 'PayPal'},
            {'value': 'bank_transfer', 'label': 'Bank Transfer'},
            {'value': 'crypto', 'label': 'Cryptocurrency', 'disabled': True},
        ],
        value="credit_card",
        help_text="Choose your preferred payment method"
    )
    print(radio5.render())

    # Example 6: Notification preferences (inline)
    print_section("Example 6: Notification Preferences (Inline)")
    radio6 = Radio(
        name="notifications",
        label="Email Notifications",
        options=[
            {'value': 'all', 'label': 'All'},
            {'value': 'important', 'label': 'Important Only'},
            {'value': 'none', 'label': 'None'},
        ],
        value="important",
        inline=True,
        help_text="How often would you like to receive email notifications?"
    )
    print(radio6.render())

    # Example 7: No label (minimal)
    print_section("Example 7: Minimal (No Group Label)")
    radio7 = Radio(
        name="rating",
        options=[
            {'value': '1', 'label': '⭐'},
            {'value': '2', 'label': '⭐⭐'},
            {'value': '3', 'label': '⭐⭐⭐'},
            {'value': '4', 'label': '⭐⭐⭐⭐'},
            {'value': '5', 'label': '⭐⭐⭐⭐⭐'},
        ],
        value="5",
        inline=True
    )
    print(radio7.render())

    print("\n" + "=" * 80)
    print("  Demo Complete!")
    print("=" * 80 + "\n")


if __name__ == '__main__':
    main()
