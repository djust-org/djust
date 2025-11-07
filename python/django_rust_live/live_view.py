"""
LiveView base class and decorator for reactive Django views
"""

import json
import asyncio
from typing import Any, Dict, Optional, Callable
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.template.loader import render_to_string

try:
    from .django_rust_live import RustLiveView
except ImportError:
    RustLiveView = None


class LiveView(View):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

            def decrement(self):
                self.count -= 1
    """

    template_name: Optional[str] = None
    template_string: Optional[str] = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view: Optional[RustLiveView] = None
        self._session_id: Optional[str] = None

    def get_template(self) -> str:
        """Get the template source for this view"""
        if self.template_string:
            return self.template_string
        elif self.template_name:
            return render_to_string(self.template_name, {})
        else:
            raise ValueError("Either template_name or template_string must be set")

    def mount(self, request, **kwargs):
        """
        Called when the view is mounted. Override to set initial state.

        Args:
            request: The Django request object
            **kwargs: URL parameters
        """
        pass

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables
        """
        context = {}

        # Add all non-private attributes as context
        for key in dir(self):
            if not key.startswith('_') and not callable(getattr(self, key)):
                context[key] = getattr(self, key)

        return context

    def _initialize_rust_view(self):
        """Initialize the Rust LiveView backend"""
        if self._rust_view is None:
            template_source = self.get_template()
            self._rust_view = RustLiveView(template_source)

    def _sync_state_to_rust(self):
        """Sync Python state to Rust backend"""
        if self._rust_view:
            context = self.get_context_data()
            self._rust_view.update_state(context)

    def render(self) -> str:
        """Render the view to HTML"""
        self._initialize_rust_view()
        self._sync_state_to_rust()
        return self._rust_view.render()

    def render_with_diff(self) -> tuple[str, Optional[str]]:
        """
        Render the view and compute diff from last render.

        Returns:
            Tuple of (html, patches_json)
        """
        self._initialize_rust_view()
        self._sync_state_to_rust()
        return self._rust_view.render_with_diff()

    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load"""
        self.mount(request, **kwargs)
        html = self.render()

        # Inject LiveView client script
        html = self._inject_client_script(html)

        return HttpResponse(html)

    def post(self, request, *args, **kwargs):
        """Handle POST requests - event handling"""
        try:
            data = json.loads(request.body)
            event_name = data.get('event')
            params = data.get('params', {})

            # Restore state from session or previous render
            # (In production, this would use session storage)

            # Call the event handler
            handler = getattr(self, event_name, None)
            if handler and callable(handler):
                if params:
                    handler(**params)
                else:
                    handler()

            # Render and get diff
            html, patches = self.render_with_diff()

            return JsonResponse({
                'patches': json.loads(patches) if patches else None,
                'html': html,
            })

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    def _inject_client_script(self, html: str) -> str:
        """Inject the LiveView client JavaScript into the HTML"""
        script = """
        <script src="/static/django_rust_live/client.js"></script>
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                DjangoRustLive.connect();
            });
        </script>
        """

        if '</body>' in html:
            html = html.replace('</body>', f'{script}</body>')
        else:
            html += script

        return html


def live_view(template_name: Optional[str] = None,
              template_string: Optional[str] = None):
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

    Args:
        template_name: Path to Django template
        template_string: Inline template string

    Returns:
        View function
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(request, *args, **kwargs):
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                pass

            if template_name:
                DynamicLiveView.template_name = template_name
            if template_string:
                DynamicLiveView.template_string = template_string

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
                        setattr(view, key, value)

            # Handle the request
            if request.method == 'GET':
                return view.get(request, *args, **kwargs)
            elif request.method == 'POST':
                return view.post(request, *args, **kwargs)

        return wrapper

    return decorator
