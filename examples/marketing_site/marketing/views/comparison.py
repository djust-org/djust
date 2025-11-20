"""
Framework comparison view.
"""
from .base import StaticMarketingView


class ComparisonView(StaticMarketingView):
    """
    Framework comparison page.

    Compares djust with Phoenix LiveView, Laravel Livewire,
    HTMX, and traditional SPA frameworks.
    """

    template_name = 'marketing/comparison.html'
    page_slug = 'comparison'

    def mount(self, request, **kwargs):
        """Initialize comparison page state."""
        super().mount(request, **kwargs)

        # Quick comparison table data
        self.comparison_features = [
            {
                'feature': 'Language',
                'djust': 'Python',
                'liveview': 'Elixir',
                'livewire': 'PHP',
                'htmx': 'Any'
            },
            {
                'feature': 'Framework',
                'djust': 'Django',
                'liveview': 'Phoenix',
                'livewire': 'Laravel',
                'htmx': 'N/A'
            },
            {
                'feature': 'VDOM Engine',
                'djust': {'check': True, 'label': 'Rust'},
                'liveview': {'check': True, 'label': 'Elixir'},
                'livewire': {'check': True, 'label': 'PHP'},
                'htmx': {'check': False, 'label': 'Full swap'}
            },
            {
                'feature': 'Client Bundle Size',
                'djust': '~5 KB',
                'liveview': '~30 KB',
                'livewire': '~50 KB',
                'htmx': '~14 KB',
                'highlight': 'djust'
            },
            {
                'feature': 'Rendering Speed',
                'djust': '0.8ms',
                'liveview': '~1-2ms',
                'livewire': '~5-10ms',
                'htmx': 'N/A',
                'highlight': 'djust'
            },
            {
                'feature': 'VDOM Diff Speed',
                'djust': '<100μs',
                'liveview': '~200μs',
                'livewire': '~500μs',
                'htmx': 'N/A',
                'highlight': 'djust'
            },
            {
                'feature': 'WebSocket Required',
                'djust': {'partial': True, 'label': 'Optional'},
                'liveview': {'check': True, 'label': 'Required'},
                'livewire': {'check': False, 'label': 'HTTP only'},
                'htmx': {'check': False, 'label': 'HTTP only'}
            },
            {
                'feature': 'Form Integration',
                'djust': {'check': True, 'label': 'Django Forms'},
                'liveview': {'check': True, 'label': 'Ecto'},
                'livewire': {'check': True, 'label': 'Form Requests'},
                'htmx': {'partial': True, 'label': 'Manual'}
            },
            {
                'feature': 'Component System',
                'djust': {'check': True, 'label': 'Stateful'},
                'liveview': {'check': True, 'label': 'Stateful'},
                'livewire': {'check': True, 'label': 'Stateful'},
                'htmx': {'check': False, 'label': 'None'}
            },
            {
                'feature': 'State Management',
                'djust': {'check': True, 'label': 'Decorators'},
                'liveview': {'check': True, 'label': 'Hooks'},
                'livewire': {'check': True, 'label': 'Properties'},
                'htmx': {'check': False, 'label': 'Manual'}
            }
        ]

        # Code comparison examples
        self.code_examples = [
            {
                'name': 'djust',
                'language': 'Python',
                'code': '''from djust import LiveView

class CounterView(LiveView):
    template_string = """
    <div>
        <h1>&#123;&#123; count &#125;&#125;</h1>
        <button @click="increment">+</button>
    </div>
    """

    def mount(self, request):
        self.count = 0

    def increment(self):
        self.count += 1'''
            },
            {
                'name': 'Phoenix LiveView',
                'language': 'Elixir',
                'code': '''defmodule CounterLive do
  use Phoenix.LiveView

  def render(assigns) do
    ~H"""
    <div>
      <h1><%= @count %></h1>
      <button phx-click="increment">+</button>
    </div>
    """
  end

  def mount(_params, _session, socket) do
    {:ok, assign(socket, count: 0)}
  end

  def handle_event("increment", _, socket) do
    {:noreply, assign(socket, count: socket.assigns.count + 1)}
  end
end'''
            },
            {
                'name': 'Laravel Livewire',
                'language': 'PHP',
                'code': '''namespace App\\Livewire;

use Livewire\\Component;

class Counter extends Component
{
    public $count = 0;

    public function increment()
    {
        $this->count++;
    }

    public function render()
    {
        return view('livewire.counter');
    }
}

// resources/views/livewire/counter.blade.php
<div>
    <h1>{{ $count }}</h1>
    <button wire:click="increment">+</button>
</div>'''
            },
            {
                'name': 'HTMX',
                'language': 'Any Backend',
                'code': '''# Backend (any language)
@app.route("/counter")
def counter():
    count = session.get('count', 0)
    return render_template('counter.html', count=count)

@app.route("/increment", methods=['POST'])
def increment():
    count = session.get('count', 0) + 1
    session['count'] = count
    return f'<h1>{count}</h1>'

<!-- Template -->
<div>
    <h1 id="count">{{ count }}</h1>
    <button hx-post="/increment"
            hx-target="#count"
            hx-swap="outerHTML">+</button>
</div>'''
            }
        ]

        # Detailed comparisons
        self.detailed_comparisons = [
            {
                'title': 'vs Phoenix LiveView',
                'framework_name': 'Phoenix LiveView',
                'choose_djust': [
                    "You're already using Django/Python",
                    "You need the fastest VDOM diffing (Rust)",
                    "You want the smallest client bundle (~5KB)",
                    "You prefer Python decorators over Elixir syntax",
                    "You need Django ORM integration"
                ],
                'choose_other': [
                    "You're already using Phoenix/Elixir",
                    "You need proven scalability (>2M connections)",
                    "You prefer functional programming",
                    "You value ecosystem maturity (2019 release)"
                ]
            },
            {
                'title': 'vs Laravel Livewire',
                'framework_name': 'Laravel Livewire',
                'choose_djust': [
                    "10x faster rendering (Rust vs PHP)",
                    "10x smaller bundle (~5KB vs ~50KB)",
                    "Real-time WebSocket support (optional)",
                    "Sub-millisecond VDOM diffing",
                    "You prefer Python over PHP"
                ],
                'choose_other': [
                    "You're already using Laravel/PHP",
                    "You prefer HTTP polling over WebSockets",
                    "You value large ecosystem (2019 release)",
                    "You need mature UI component libraries"
                ]
            },
            {
                'title': 'vs HTMX',
                'framework_name': 'HTMX',
                'choose_djust': [
                    "You need stateful components",
                    "You want automatic VDOM diffing",
                    "You need real-time WebSocket updates",
                    "You want form integration (Django Forms)",
                    "You prefer less manual wiring"
                ],
                'choose_other': [
                    "You want framework-agnostic approach",
                    "You prefer explicit HTML attributes",
                    "You don't need stateful components",
                    "You want to enhance existing HTML",
                    "You value simplicity over features"
                ]
            }
        ]

        # Performance metrics
        self.performance_data = [
            {
                'label': 'djust',
                'time': '0.8ms',
                'width': 100,
                'color': 'brand-django'
            },
            {
                'label': 'LiveView (Elixir)',
                'time': '~1-2ms',
                'width': 50,
                'color': 'purple-500'
            },
            {
                'label': 'Livewire (PHP)',
                'time': '~5-10ms',
                'width': 15,
                'color': 'red-500'
            }
        ]

    def get_context_data(self, **kwargs) -> dict:
        """Add comparison page context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'comparison_features': self.comparison_features,
            'code_examples': self.code_examples,
            'detailed_comparisons': self.detailed_comparisons,
            'performance_data': self.performance_data,
        })
        return context
