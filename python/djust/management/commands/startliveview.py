"""
djust startliveview — scaffold a new LiveView with boilerplate.

Usage:
    python manage.py startliveview myview
    python manage.py startliveview myview --app myapp
    python manage.py startliveview myview --with-form
    python manage.py startliveview myview --with-hooks

Creates:
    - views.py entry (or new file) with LiveView boilerplate
    - Template file with common dj-* directives
    - Optional JavaScript hooks file
    - URL pattern suggestion
"""

import os
import re
from pathlib import Path
from textwrap import dedent

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings


# =============================================================================
# TEMPLATES
# =============================================================================

VIEW_TEMPLATE = '''
class {class_name}(LiveView):
    """
    {class_name} LiveView
    
    TODO: Add description of what this view does.
    """
    
    template_name = '{app_name}/{template_name}'
    
    def mount(self, **kwargs):
        """
        Initialize view state.
        
        Called once when the view loads. Set instance variables here
        that will be available in the template context.
        """
        # TODO: Initialize your state
        pass
    
    def handle_params(self, params: dict):
        """
        Handle URL parameter changes.
        
        Called when URL params change via live_patch/live_redirect.
        """
        pass
    
    @event_handler
    def example_handler(self, value: str = ""):
        """
        Example event handler.
        
        Called from template with: dj-click="example_handler"
        Type hints enable automatic coercion of string params.
        """
        # TODO: Implement your logic
        pass
'''

VIEW_WITH_FORM_TEMPLATE = '''
class {form_class_name}(forms.Form):
    """Form for {class_name}."""
    # TODO: Add your form fields
    name = forms.CharField(max_length=100)
    email = forms.EmailField()


class {class_name}(FormMixin, LiveView):
    """
    {class_name} LiveView with form support.
    
    TODO: Add description of what this view does.
    """
    
    template_name = '{app_name}/{template_name}'
    form_class = {form_class_name}
    
    def mount(self, **kwargs):
        """Initialize form state."""
        super().mount(**kwargs)
        self.submitted = False
    
    def form_valid(self, form):
        """
        Handle successful form submission.
        
        Called when all validation passes.
        """
        self.submitted = True
        self.success_message = "Form submitted successfully!"
        # TODO: Process form.cleaned_data
    
    def form_invalid(self, form):
        """Handle failed validation."""
        self.error_message = "Please fix the errors below."
'''

HTML_TEMPLATE = '''{{#
{class_name} Template

djust directives used:
- dj-click: Trigger event handlers on click
- dj-loading: Show loading state during requests
- {{{{ variable }}}}: Standard Django template variables
#}}

<div class="{css_class}">
    <h1>{title}</h1>
    
    {{# Loading state indicator #}}
    <div dj-loading="class:loading">
        {{# Your content here #}}
        <p>TODO: Add your content</p>
        
        {{# Example event handler button #}}
        <button dj-click="example_handler" data-value="test" class="btn">
            Click Me
        </button>
    </div>
</div>

<style>
    .{css_class} {{
        max-width: 600px;
        margin: 2rem auto;
        padding: 2rem;
        font-family: system-ui, -apple-system, sans-serif;
    }}
    
    .{css_class}.loading {{
        opacity: 0.5;
    }}
    
    .btn {{
        padding: 0.75rem 1.5rem;
        background: #333;
        color: white;
        border: none;
        border-radius: 6px;
        cursor: pointer;
    }}
    
    .btn:hover {{
        background: #555;
    }}
</style>
'''

HTML_FORM_TEMPLATE = '''{{#
{class_name} Form Template

djust directives used:
- dj-model: Two-way input binding
- dj-change: Trigger validation on field change
- dj-submit: Handle form submission
- dj-loading: Show loading state
#}}

<div class="{css_class}">
    <h1>{title}</h1>
    
    {{% if submitted %}}
    <div class="success-message">
        <p>{{{{ success_message }}}}</p>
        <a href="" class="btn btn-outline">Submit Another</a>
    </div>
    {{% else %}}
    
    <form dj-submit="submit_form" dj-loading="class:submitting">
        {{% if error_message %}}
        <div class="error-banner">{{{{ error_message }}}}</div>
        {{% endif %}}
        
        {{# TODO: Add your form fields #}}
        <div class="form-group {{% if field_errors.name %}}has-error{{% endif %}}">
            <label for="name">Name</label>
            <input 
                type="text" 
                id="name" 
                name="name"
                value="{{{{ form_data.name }}}}"
                dj-model="form_data.name"
                dj-change="validate_field"
                data-field_name="name"
            >
            {{% if field_errors.name %}}
            <span class="error">{{{{ field_errors.name }}}}</span>
            {{% endif %}}
        </div>
        
        <div class="form-group {{% if field_errors.email %}}has-error{{% endif %}}">
            <label for="email">Email</label>
            <input 
                type="email" 
                id="email" 
                name="email"
                value="{{{{ form_data.email }}}}"
                dj-model="form_data.email"
                dj-change="validate_field"
                data-field_name="email"
            >
            {{% if field_errors.email %}}
            <span class="error">{{{{ field_errors.email }}}}</span>
            {{% endif %}}
        </div>
        
        <button type="submit" class="btn">
            <span dj-loading="hide">Submit</span>
            <span dj-loading="show" style="display:none">Submitting...</span>
        </button>
    </form>
    {{% endif %}}
</div>

<style>
    .{css_class} {{
        max-width: 500px;
        margin: 2rem auto;
        padding: 2rem;
        font-family: system-ui, -apple-system, sans-serif;
    }}
    
    .form-group {{
        margin-bottom: 1.5rem;
    }}
    
    .form-group label {{
        display: block;
        margin-bottom: 0.5rem;
        font-weight: 500;
    }}
    
    .form-group input {{
        width: 100%;
        padding: 0.75rem;
        border: 2px solid #ddd;
        border-radius: 6px;
        font-size: 1rem;
        box-sizing: border-box;
    }}
    
    .form-group.has-error input {{
        border-color: #e53e3e;
    }}
    
    .error {{
        color: #e53e3e;
        font-size: 0.875rem;
        margin-top: 0.5rem;
        display: block;
    }}
    
    .error-banner {{
        background: #fed7d7;
        color: #c53030;
        padding: 1rem;
        border-radius: 6px;
        margin-bottom: 1.5rem;
    }}
    
    .success-message {{
        text-align: center;
        padding: 2rem;
        background: #c6f6d5;
        border-radius: 8px;
    }}
    
    .btn {{
        padding: 0.75rem 1.5rem;
        background: #333;
        color: white;
        border: none;
        border-radius: 6px;
        cursor: pointer;
        font-size: 1rem;
    }}
    
    .btn-outline {{
        background: transparent;
        color: #333;
        border: 2px solid #333;
        text-decoration: none;
        display: inline-block;
        margin-top: 1rem;
    }}
    
    .submitting {{
        opacity: 0.6;
        pointer-events: none;
    }}
</style>
'''

HOOKS_TEMPLATE = '''/**
 * {class_name} JavaScript Hooks
 * 
 * Hooks run client-side JS when djust elements mount, update, or unmount.
 * 
 * Usage in template:
 *   <div dj-hook="{class_name}Hook" data-value="{{{{ value }}}}">...</div>
 */

window.djust = window.djust || {{ hooks: {{}} }};

const {class_name}Hook = {{
    mounted(el) {{
        console.log('[{class_name}Hook] mounted', el);
        // TODO: Initialize your client-side widget
        // - Parse data from el.dataset
        // - Set up event listeners
        // - Initialize third-party libraries
    }},
    
    updated(el) {{
        console.log('[{class_name}Hook] updated', el);
        // TODO: Handle data changes
        // - Re-read el.dataset values
        // - Update your widget
    }},
    
    destroyed(el) {{
        console.log('[{class_name}Hook] destroyed', el);
        // TODO: Clean up
        // - Remove event listeners
        // - Destroy third-party widget instances
    }}
}};

// Register hook
if (window.djust.registerHook) {{
    window.djust.registerHook('{class_name}Hook', {class_name}Hook);
}} else {{
    window.djust.hooks.{class_name}Hook = {class_name}Hook;
}}
'''


class Command(BaseCommand):
    help = 'Create a new djust LiveView with boilerplate code.'

    def add_arguments(self, parser):
        parser.add_argument(
            'name',
            help='Name of the LiveView (e.g., "counter", "user_profile")'
        )
        parser.add_argument(
            '--app',
            default=None,
            help='Django app to create the view in (default: first app in INSTALLED_APPS)'
        )
        parser.add_argument(
            '--with-form',
            action='store_true',
            help='Include form boilerplate (FormMixin, validation)'
        )
        parser.add_argument(
            '--with-hooks',
            action='store_true',
            help='Create JavaScript hooks file'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without writing files'
        )

    def handle(self, *args, **options):
        name = options['name']
        app_name = options['app']
        with_form = options['with_form']
        with_hooks = options['with_hooks']
        dry_run = options['dry_run']

        # Validate name
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            raise CommandError(
                f"'{name}' is not a valid view name. "
                "Use lowercase letters, numbers, and underscores (e.g., 'my_view')."
            )

        # Find app directory
        app_dir = self._find_app_dir(app_name)
        if not app_dir:
            raise CommandError(
                f"Could not find app '{app_name or 'any'}'. "
                "Specify --app or ensure an app exists in INSTALLED_APPS."
            )

        app_name = app_dir.name

        # Generate names
        class_name = self._to_class_name(name)
        template_name = f'{name}.html'
        css_class = name.replace('_', '-')
        title = name.replace('_', ' ').title()
        form_class_name = f'{class_name}Form'

        ctx = {
            'name': name,
            'class_name': class_name,
            'app_name': app_name,
            'template_name': template_name,
            'css_class': css_class,
            'title': title,
            'form_class_name': form_class_name,
        }

        # Generate content
        if with_form:
            view_code = VIEW_WITH_FORM_TEMPLATE.format(**ctx)
            html_code = HTML_FORM_TEMPLATE.format(**ctx)
            imports = "from djust import LiveView, event_handler\nfrom djust.forms import FormMixin\nfrom django import forms"
        else:
            view_code = VIEW_TEMPLATE.format(**ctx)
            html_code = HTML_TEMPLATE.format(**ctx)
            imports = "from djust import LiveView, event_handler"

        hooks_code = HOOKS_TEMPLATE.format(**ctx) if with_hooks else None

        # File paths
        views_path = app_dir / 'views.py'
        template_dir = app_dir / 'templates' / app_name
        template_path = template_dir / template_name
        hooks_dir = app_dir / 'static' / app_name
        hooks_path = hooks_dir / f'{name}_hooks.js'

        if dry_run:
            self._show_dry_run(
                views_path, view_code, imports,
                template_path, html_code,
                hooks_path if with_hooks else None, hooks_code,
                ctx
            )
            return

        # Create files
        created = []

        # Update/create views.py
        self._add_to_views(views_path, view_code, imports, class_name)
        created.append(f'  - {views_path} (added {class_name})')

        # Create template
        template_dir.mkdir(parents=True, exist_ok=True)
        template_path.write_text(html_code.lstrip('\n'))
        created.append(f'  - {template_path}')

        # Create hooks if requested
        if with_hooks:
            hooks_dir.mkdir(parents=True, exist_ok=True)
            hooks_path.write_text(hooks_code)
            created.append(f'  - {hooks_path}')

        # Success message
        self.stdout.write(self.style.SUCCESS(f'\n✓ Created {class_name} LiveView:\n'))
        for item in created:
            self.stdout.write(item)

        # URL pattern suggestion
        self.stdout.write(self.style.NOTICE('\n📝 Add to urls.py:'))
        self.stdout.write(f'''
    from {app_name}.views import {class_name}
    
    urlpatterns = [
        path('{name}/', {class_name}.as_view(), name='{name}'),
    ]
''')

        if with_hooks:
            self.stdout.write(self.style.NOTICE('📝 Include hooks in your base template:'))
            self.stdout.write(f'''
    <script src="{{% static '{app_name}/{name}_hooks.js' %}}"></script>
''')

    def _find_app_dir(self, app_name=None):
        """Find the app directory to create the view in."""
        # Get base directory
        base_dir = Path(settings.BASE_DIR)

        if app_name:
            # Look for specific app
            for app in settings.INSTALLED_APPS:
                if app == app_name or app.endswith(f'.{app_name}'):
                    app_path = base_dir / app_name
                    if app_path.is_dir():
                        return app_path
            return None

        # Find first user app (not django.*, not djust, not third-party)
        skip_prefixes = ('django.', 'djust', 'channels', 'daphne', 'rest_framework')
        for app in settings.INSTALLED_APPS:
            if any(app.startswith(p) for p in skip_prefixes):
                continue
            # Check if it's a local app
            app_name = app.split('.')[-1]
            app_path = base_dir / app_name
            if app_path.is_dir() and (app_path / '__init__.py').exists():
                return app_path

        return None

    def _to_class_name(self, name):
        """Convert snake_case to PascalCase and add View suffix."""
        parts = name.split('_')
        class_name = ''.join(part.capitalize() for part in parts)
        if not class_name.endswith('View'):
            class_name += 'View'
        return class_name

    def _add_to_views(self, views_path, view_code, imports, class_name):
        """Add view to views.py, creating if needed."""
        if views_path.exists():
            content = views_path.read_text()
            
            # Check if class already exists
            if f'class {class_name}' in content:
                raise CommandError(f"Class {class_name} already exists in {views_path}")
            
            # Add imports if missing
            for imp in imports.split('\n'):
                if imp and imp not in content:
                    # Add import at top (after existing imports)
                    if 'import' in content:
                        # Find last import line
                        lines = content.split('\n')
                        last_import_idx = 0
                        for i, line in enumerate(lines):
                            if line.startswith('import ') or line.startswith('from '):
                                last_import_idx = i
                        lines.insert(last_import_idx + 1, imp)
                        content = '\n'.join(lines)
                    else:
                        content = imp + '\n' + content
            
            # Add view class at end
            content = content.rstrip() + '\n\n' + view_code.strip() + '\n'
        else:
            # Create new views.py
            content = f'"""{views_path.parent.name} LiveViews."""\n\n{imports}\n\n{view_code.strip()}\n'

        views_path.write_text(content)

    def _show_dry_run(self, views_path, view_code, imports, template_path, html_code, hooks_path, hooks_code, ctx):
        """Show what would be created in dry-run mode."""
        self.stdout.write(self.style.WARNING('\n[DRY RUN] Would create:\n'))
        
        self.stdout.write(f'\n{views_path}:')
        self.stdout.write(self.style.SQL_KEYWORD(imports))
        self.stdout.write(self.style.SQL_FIELD(view_code))
        
        self.stdout.write(f'\n{template_path}:')
        self.stdout.write(html_code[:500] + '...')
        
        if hooks_path:
            self.stdout.write(f'\n{hooks_path}:')
            self.stdout.write(hooks_code[:300] + '...')
