#!/usr/bin/env python3
"""
Example usage of the Breadcrumb component in djust.

This demonstrates how to use the Breadcrumb component in various scenarios.
"""

import sys
import os

# Add project to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))

# Configure Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test_settings')

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='test-secret-key',
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

from djust.components.ui import Breadcrumb


def example_basic():
    """Example 1: Basic breadcrumb for a product page"""
    print("=" * 70)
    print("Example 1: Basic Product Page Breadcrumb")
    print("=" * 70)

    breadcrumb = Breadcrumb(items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'Products', 'url': '/products'},
        {'label': 'Electronics', 'url': '/products/electronics'},
        {'label': 'Laptops', 'url': '/products/electronics/laptops'},
        {'label': 'MacBook Pro 16"'},  # Current page, no URL
    ])

    print(breadcrumb.render())
    print()


def example_with_home_icon():
    """Example 2: Breadcrumb with home icon"""
    print("=" * 70)
    print("Example 2: Breadcrumb with Home Icon")
    print("=" * 70)

    breadcrumb = Breadcrumb(
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Dashboard', 'url': '/dashboard'},
            {'label': 'Analytics', 'url': '/dashboard/analytics'},
            {'label': 'Monthly Report'},
        ],
        show_home=True
    )

    print(breadcrumb.render())
    print()


def example_blog_post():
    """Example 3: Blog post breadcrumb"""
    print("=" * 70)
    print("Example 3: Blog Post Breadcrumb")
    print("=" * 70)

    breadcrumb = Breadcrumb(items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'Blog', 'url': '/blog'},
        {'label': 'Web Development', 'url': '/blog/category/web-development'},
        {'label': 'Building a Breadcrumb Component'},
    ])

    print(breadcrumb.render())
    print()


def example_settings_page():
    """Example 4: Settings page breadcrumb"""
    print("=" * 70)
    print("Example 4: Settings Page Breadcrumb")
    print("=" * 70)

    breadcrumb = Breadcrumb(
        items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Settings', 'url': '/settings'},
            {'label': 'Account', 'url': '/settings/account'},
            {'label': 'Privacy'},
        ],
        show_home=True
    )

    print(breadcrumb.render())
    print()


def example_minimal():
    """Example 5: Minimal breadcrumb with just two items"""
    print("=" * 70)
    print("Example 5: Minimal Breadcrumb (Two Items)")
    print("=" * 70)

    breadcrumb = Breadcrumb(items=[
        {'label': 'Home', 'url': '/'},
        {'label': 'About Us'},
    ])

    print(breadcrumb.render())
    print()


def example_in_liveview():
    """Example 6: How to use in a LiveView"""
    print("=" * 70)
    print("Example 6: Using Breadcrumb in LiveView")
    print("=" * 70)

    code = '''
from djust import LiveView
from djust.components.ui import Breadcrumb

class ProductDetailView(LiveView):
    template = """
    <div class="container">
        {{ breadcrumb.render|safe }}

        <h1>{{ product.name }}</h1>
        <p>{{ product.description }}</p>
    </div>
    """

    def mount(self, request, product_id):
        # Fetch product
        self.product = get_product(product_id)

        # Build breadcrumb dynamically
        self.breadcrumb = Breadcrumb(items=[
            {'label': 'Home', 'url': '/'},
            {'label': 'Products', 'url': '/products'},
            {'label': self.product.category, 'url': f'/products/{self.product.category}'},
            {'label': self.product.name},  # Current page
        ])

    def get_context_data(self, **kwargs):
        return {
            'breadcrumb': self.breadcrumb,
            'product': self.product,
        }
'''

    print(code)
    print()


def example_dynamic_breadcrumb():
    """Example 7: Dynamically building breadcrumbs from URL"""
    print("=" * 70)
    print("Example 7: Dynamically Building Breadcrumbs")
    print("=" * 70)

    def build_breadcrumb_from_path(path_segments):
        """Helper to build breadcrumb from URL path"""
        items = [{'label': 'Home', 'url': '/'}]

        url_parts = []
        for segment in path_segments:
            url_parts.append(segment)
            items.append({
                'label': segment.replace('-', ' ').title(),
                'url': '/' + '/'.join(url_parts)
            })

        # Last item is current page (no URL)
        if items:
            items[-1]['url'] = None

        return Breadcrumb(items=items)

    # Example URL: /products/electronics/laptops
    path = ['products', 'electronics', 'laptops']
    breadcrumb = build_breadcrumb_from_path(path)

    print(breadcrumb.render())
    print()


def main():
    """Run all examples"""
    print("\n" + "=" * 70)
    print("BREADCRUMB COMPONENT EXAMPLES")
    print("=" * 70 + "\n")

    example_basic()
    example_with_home_icon()
    example_blog_post()
    example_settings_page()
    example_minimal()
    example_in_liveview()
    example_dynamic_breadcrumb()

    print("=" * 70)
    print("Examples completed!")
    print("=" * 70)


if __name__ == '__main__':
    main()
