#!/usr/bin/env python3
"""
Visual demonstration of the Divider component.

Shows all variations of the Divider component with different styles,
margins, and text options.
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

# Configure Django
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='demo-secret-key',
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
        ],
    )
    django.setup()

from djust.components.ui import Divider


def print_demo(title: str, divider: Divider):
    """Print a demo section"""
    print(f"\n{title}")
    print("-" * 60)
    print(divider.render())
    print()


def main():
    print("=" * 60)
    print("Divider Component Visual Demo")
    print("=" * 60)

    # Simple dividers
    print("\n### SIMPLE DIVIDERS ###")
    print_demo("Default divider (solid, medium margin):", Divider())
    print_demo("Dashed divider:", Divider(style="dashed"))
    print_demo("Dotted divider:", Divider(style="dotted"))

    # Margins
    print("\n### MARGIN VARIATIONS ###")
    print_demo("Small margin:", Divider(margin="sm"))
    print_demo("Medium margin (default):", Divider(margin="md"))
    print_demo("Large margin:", Divider(margin="lg"))

    # With text
    print("\n### DIVIDERS WITH TEXT ###")
    print_demo("OR divider:", Divider(text="OR"))
    print_demo("AND divider:", Divider(text="AND"))
    print_demo("Section separator:", Divider(text="Section 2"))

    # Combined
    print("\n### COMBINED OPTIONS ###")
    print_demo("OR with dashed style:", Divider(text="OR", style="dashed"))
    print_demo("AND with large margin:", Divider(text="AND", margin="lg"))
    print_demo("Section with dotted style and small margin:",
              Divider(text="Details", style="dotted", margin="sm"))

    # Real-world examples
    print("\n### REAL-WORLD USAGE ###")
    print_demo("Form section separator:", Divider(text="Personal Information"))
    print_demo("Login/Register separator:", Divider(text="OR", margin="lg"))
    print_demo("Content break:", Divider(style="dashed", margin="md"))

    # Check implementation
    print("\n### IMPLEMENTATION INFO ###")
    print("-" * 60)
    try:
        from djust._rust import RustDivider
        print("✓ Using Rust implementation (high-performance)")
        test = RustDivider()
        print(f"  Rust render test: {test.render()}")
    except (ImportError, AttributeError) as e:
        print("✓ Using Python fallback (template rendering)")
        print(f"  Note: {e}")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == '__main__':
    main()
