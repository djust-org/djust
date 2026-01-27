"""
Debounce Demo - demonstrates @debounce decorator with search
"""

from djust import LiveView
from djust.decorators import debounce


class DebounceSearchView(LiveView):
    """
    Demonstrates @debounce decorator with a search input.

    Features:
    - Debounces search input (waits 500ms after typing stops)
    - Shows search count (should be much lower than keypress count)
    - Demonstrates max_wait forcing execution after 2 seconds
    """

    template = """
    <div data-djust-root class="container mt-5">
        <div class="row">
            <div class="col-md-8 offset-md-2">
                <div class="card">
                    <div class="card-header bg-primary text-white">
                        <h3 class="mb-0">Debounce Demo</h3>
                        <p class="mb-0 small">Type rapidly - search waits until you stop typing</p>
                    </div>
                    <div class="card-body">
                        <!-- Search Input -->
                        <div class="mb-4">
                            <label class="form-label">Search Query</label>
                            <input
                                type="text"
                                class="form-control form-control-lg"
                                @input="search"
                                placeholder="Type something..."
                                value="{{ query }}"
                            >
                            <small class="text-muted">
                                Debounced: waits 500ms after typing stops (max wait: 2s)
                            </small>
                        </div>

                        <!-- Stats -->
                        <div class="row g-3 mb-4">
                            <div class="col-md-4">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h5 class="card-title text-muted small mb-1">Current Query</h5>
                                        <p class="h4 mb-0">{{ query or '(empty)' }}</p>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-4">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h5 class="card-title text-muted small mb-1">Server Calls</h5>
                                        <p class="h4 mb-0 text-success">{{ search_count }}</p>
                                        <small class="text-muted">Should be low</small>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-4">
                                <div class="card bg-light">
                                    <div class="card-body">
                                        <h5 class="card-title text-muted small mb-1">Query Length</h5>
                                        <p class="h4 mb-0">{{ query|length }}</p>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Instructions -->
                        <div class="alert alert-info">
                            <h5 class="alert-heading">How to test:</h5>
                            <ol class="mb-0">
                                <li>Open browser console and run: <code>window.djustDebug = true</code></li>
                                <li>Type rapidly in the search box</li>
                                <li>Watch the console - you'll see debounce logs</li>
                                <li>Notice "Server Calls" only increments when you stop typing</li>
                                <li>Try typing continuously for >2 seconds (max_wait will force execution)</li>
                            </ol>
                        </div>

                        <!-- How it works -->
                        <div class="mt-4">
                            <h5>How it works:</h5>
                            <pre class="bg-light p-3"><code>@debounce(wait=0.5, max_wait=2.0)
def search(self, value: str = "", **kwargs):
    self.query = value
    self.search_count += 1</code></pre>
                            <ul>
                                <li><code>wait=0.5</code> - Waits 500ms after last keystroke</li>
                                <li><code>max_wait=2.0</code> - Forces execution after 2 seconds max</li>
                                <li>Reduces server calls by ~90% for typical typing</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    def mount(self, request):
        """Initialize state"""
        self.query = ""
        self.search_count = 0

    @debounce(wait=0.5, max_wait=2.0)
    def search(self, value: str = "", **kwargs):
        """
        Handle search input with debouncing.

        This will only execute after user stops typing for 500ms,
        or after 2 seconds maximum (max_wait).
        """
        self.query = value
        self.search_count += 1
