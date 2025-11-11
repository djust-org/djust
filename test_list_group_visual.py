"""
Visual test for ListGroup component - generates HTML file to view in browser.
"""

import os
import sys
import django
from django.conf import settings

# Configure Django settings
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

from python.djust.components.ui import ListGroup
from python.djust.config import config


def generate_html():
    """Generate HTML file with all ListGroup variations"""

    # Example 1: Basic Navigation
    nav_list = ListGroup(items=[
        {'label': 'Dashboard', 'url': '#', 'active': True},
        {'label': 'Profile', 'url': '#'},
        {'label': 'Settings', 'url': '#'},
        {'label': 'Logout', 'url': '#', 'disabled': True},
    ])

    # Example 2: List with Badges
    message_list = ListGroup(items=[
        {
            'label': 'Inbox',
            'url': '#',
            'badge': {'text': '5', 'variant': 'primary'}
        },
        {
            'label': 'Notifications',
            'url': '#',
            'badge': {'text': '12', 'variant': 'danger'}
        },
        {
            'label': 'Updates',
            'url': '#',
            'badge': {'text': '3', 'variant': 'success'}
        },
    ])

    # Example 3: Color Variants
    status_list = ListGroup(items=[
        {'label': 'All systems operational', 'variant': 'success'},
        {'label': 'Database backup in progress', 'variant': 'info'},
        {'label': 'Scheduled maintenance tonight', 'variant': 'warning'},
        {'label': 'High memory usage detected', 'variant': 'danger'},
    ])

    # Example 4: Numbered Task List
    task_list = ListGroup(
        items=[
            {'label': 'Install dependencies', 'url': '#'},
            {'label': 'Configure settings', 'url': '#'},
            {'label': 'Run migrations', 'url': '#'},
            {'label': 'Deploy to production', 'url': '#', 'disabled': True},
        ],
        numbered=True
    )

    # Example 5: Flush Variant (No borders)
    flush_list = ListGroup(
        items=[
            {'label': 'Profile Settings', 'url': '#'},
            {'label': 'Security', 'url': '#'},
            {'label': 'Notifications', 'url': '#'},
            {'label': 'Privacy', 'url': '#'},
            {'label': 'Account', 'url': '#'},
        ],
        flush=True
    )

    # Example 6: Complex Example
    complex_list = ListGroup(items=[
        {
            'label': 'Dashboard',
            'url': '#',
            'active': True,
            'badge': {'text': 'New', 'variant': 'primary'}
        },
        {
            'label': 'Messages',
            'url': '#',
            'badge': {'text': '5', 'variant': 'danger'}
        },
        {
            'label': 'Important Notice',
            'variant': 'warning'
        },
        {
            'label': 'Settings',
            'url': '#',
            'disabled': True
        },
    ])

    # Example 7: Non-link Items
    info_list = ListGroup(items=[
        {'label': 'Python 3.12.0'},
        {'label': 'Django 5.0.0'},
        {'label': 'djust 1.0.0'},
        {'label': 'Bootstrap 5.3.0'},
    ])

    # Generate HTML
    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ListGroup Component - Visual Test</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{
            background: #f8f9fa;
            padding: 2rem 0;
        }}
        .example-section {{
            background: white;
            border-radius: 8px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .example-title {{
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: #333;
        }}
        .example-description {{
            color: #666;
            margin-bottom: 1rem;
        }}
        code {{
            background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="row mb-4">
            <div class="col-12">
                <h1 class="display-4 mb-2">ListGroup Component</h1>
                <p class="lead text-muted">Visual test of all ListGroup variations</p>
            </div>
        </div>

        <div class="row">
            <!-- Example 1: Basic Navigation -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">1. Basic Navigation</div>
                    <div class="example-description">
                        Simple navigation list with active and disabled states.
                    </div>
                    {nav_list.render()}
                </div>
            </div>

            <!-- Example 2: List with Badges -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">2. List with Badges</div>
                    <div class="example-description">
                        Items with badge indicators showing counts.
                    </div>
                    {message_list.render()}
                </div>
            </div>

            <!-- Example 3: Color Variants -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">3. Color Variants</div>
                    <div class="example-description">
                        Status indicators with contextual colors.
                    </div>
                    {status_list.render()}
                </div>
            </div>

            <!-- Example 4: Numbered List -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">4. Numbered Task List</div>
                    <div class="example-description">
                        Ordered list with automatic numbering.
                    </div>
                    {task_list.render()}
                </div>
            </div>

            <!-- Example 5: Flush Variant -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">5. Flush Variant (No Borders)</div>
                    <div class="example-description">
                        Edge-to-edge list, perfect for cards and sidebars.
                    </div>
                    <div class="card">
                        <div class="card-header">Settings Menu</div>
                        {flush_list.render()}
                    </div>
                </div>
            </div>

            <!-- Example 6: Complex Example -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">6. Complex Example</div>
                    <div class="example-description">
                        Combining multiple features: active, badges, variants, disabled.
                    </div>
                    {complex_list.render()}
                </div>
            </div>

            <!-- Example 7: Non-link Items -->
            <div class="col-md-6">
                <div class="example-section">
                    <div class="example-title">7. Non-link Items</div>
                    <div class="example-description">
                        Static content without links (read-only list).
                    </div>
                    {info_list.render()}
                </div>
            </div>
        </div>

        <!-- Code Examples -->
        <div class="row mt-4">
            <div class="col-12">
                <div class="example-section">
                    <div class="example-title">Usage Example</div>
                    <pre><code class="language-python">from djust.components.ui import ListGroup

# Basic navigation
nav_list = ListGroup(items=[
    {{'label': 'Dashboard', 'url': '#', 'active': True}},
    {{'label': 'Profile', 'url': '#'}},
    {{'label': 'Settings', 'url': '#', 'disabled': True}},
])

# With badges
message_list = ListGroup(items=[
    {{
        'label': 'Inbox',
        'url': '#',
        'badge': {{'text': '5', 'variant': 'primary'}}
    }},
])

# Numbered list
task_list = ListGroup(
    items=[
        {{'label': 'Step 1', 'url': '#'}},
        {{'label': 'Step 2', 'url': '#'}},
    ],
    numbered=True
)

# In template
{{{{ nav_list.render|safe }}}}</code></pre>
                </div>
            </div>
        </div>

        <!-- Feature Summary -->
        <div class="row mt-4">
            <div class="col-12">
                <div class="example-section">
                    <div class="example-title">Features Summary</div>
                    <div class="row">
                        <div class="col-md-6">
                            <h6>Item Properties</h6>
                            <ul>
                                <li><code>label</code> - Text to display</li>
                                <li><code>url</code> - Optional link URL</li>
                                <li><code>active</code> - Highlight as active</li>
                                <li><code>disabled</code> - Disable interaction</li>
                                <li><code>variant</code> - Color variant</li>
                                <li><code>badge</code> - Add badge to item</li>
                            </ul>
                        </div>
                        <div class="col-md-6">
                            <h6>List Options</h6>
                            <ul>
                                <li><code>flush=True</code> - Remove borders</li>
                                <li><code>numbered=True</code> - Number items</li>
                            </ul>
                            <h6 class="mt-3">Color Variants</h6>
                            <ul>
                                <li>primary, secondary</li>
                                <li>success, danger, warning, info</li>
                                <li>light, dark</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer -->
        <div class="row mt-4">
            <div class="col-12 text-center text-muted">
                <p>ListGroup Component for djust • Bootstrap 5 • Python + Rust</p>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

    return html


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Generating Visual Test HTML")
    print("="*60 + "\n")

    try:
        html = generate_html()

        output_file = 'list_group_visual_test.html'
        with open(output_file, 'w') as f:
            f.write(html)

        print(f"✓ Generated: {output_file}")
        print(f"✓ Open in browser to view all ListGroup variations")
        print(f"\n  file://{os.path.abspath(output_file)}\n")

        # Try to open in browser (macOS)
        import subprocess
        try:
            subprocess.run(['open', output_file], check=False)
            print("✓ Opening in browser...")
        except:
            pass

        print("="*60 + "\n")

    except Exception as e:
        print(f"\n✗ Error: {e}\n")
        raise
