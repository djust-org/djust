"""
Quick start guide view.
"""
from .base import StaticMarketingView


class QuickStartView(StaticMarketingView):
    """
    Quick start guide for getting started with djust.

    Step-by-step instructions from installation to first LiveView.
    """

    template_name = 'marketing/quickstart.html'
    page_slug = 'quickstart'

    def mount(self, request, **kwargs):
        """Initialize quick start page state."""
        super().mount(request, **kwargs)

        # Quick start steps
        self.steps = [
            {
                'number': 1,
                'title': 'Install djust',
                'description': 'Install via pip',
                'code': 'pip install djust',
                'language': 'bash',
            },
            {
                'number': 2,
                'title': 'Configure Django Settings',
                'description': 'Add djust and channels to INSTALLED_APPS',
                'code': '''INSTALLED_APPS = [
    'daphne',  # Must be first
    # ... other apps ...
    'channels',
    'djust',
    'myapp',
]

ASGI_APPLICATION = 'myproject.asgi.application'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer'
    }
}''',
                'language': 'python',
            },
            {
                'number': 3,
                'title': 'Configure ASGI',
                'description': 'Set up WebSocket routing',
                'code': '''# myproject/asgi.py
import os
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

django_asgi_app = get_asgi_application()

from djust.websocket import LiveViewConsumer

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": URLRouter([
        path('ws/live/', LiveViewConsumer.as_asgi()),
    ]),
})''',
                'language': 'python',
            },
            {
                'number': 4,
                'title': 'Create Your First LiveView',
                'description': 'Build a simple counter',
                'code': '''# myapp/views.py
from djust import LiveView

class CounterView(LiveView):
    template_name = 'counter.html'

    def mount(self, request):
        self.count = 0

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1''',
                'language': 'python',
            },
            {
                'number': 5,
                'title': 'Create Template',
                'description': 'Add template with event handlers',
                'code': '''<!-- templates/counter.html -->
<div>
    <h1>Counter: {{ count }}</h1>
    <button @click="decrement">-</button>
    <button @click="increment">+</button>
</div>''',
                'language': 'html',
            },
            {
                'number': 6,
                'title': 'Add URL Route',
                'description': 'Wire up the view',
                'code': '''# myapp/urls.py
from django.urls import path
from .views import CounterView

urlpatterns = [
    path('counter/', CounterView.as_view(), name='counter'),
]''',
                'language': 'python',
            },
            {
                'number': 7,
                'title': 'Run Development Server',
                'description': 'Start the ASGI server',
                'code': 'daphne -b 0.0.0.0 -p 8000 myproject.asgi:application',
                'language': 'bash',
            },
        ]

        # Next steps
        self.next_steps = [
            {
                'title': 'Explore Examples',
                'description': 'Check out the examples page for more complex use cases.',
                'link': '/examples/',
            },
            {
                'title': 'Read Documentation',
                'description': 'Dive deep into LiveView, components, and forms.',
                'link': 'https://github.com/yourusername/djust',  # TODO: Update
            },
            {
                'title': 'Try the Playground',
                'description': 'Experiment with djust in your browser.',
                'link': '/playground/',
            },
        ]

    def get_context_data(self, **kwargs):
        """Add quick start page context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'steps': self.steps,
            'next_steps': self.next_steps,
        })
        return context
