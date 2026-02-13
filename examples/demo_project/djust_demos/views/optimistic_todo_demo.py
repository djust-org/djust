"""
Optimistic Todo Demo - demonstrates @optimistic with todo list
"""

from djust import LiveView
from djust.decorators import optimistic
import time


class OptimisticTodoView(LiveView):
    """
    Demonstrates @optimistic with todo list.

    Features:
    - Instant checkbox toggle (optimistic update)
    - Server validation in background
    - Error handling (revert on failure)
    - Simulated network delay to show optimistic behavior
    """

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            .demo-container {
                max-width: 800px;
                margin: 50px auto;
            }
            .todo-item {
                padding: 15px;
                margin: 10px 0;
                background: #f8f9fa;
                border-radius: 8px;
                display: flex;
                align-items: center;
                transition: all 0.3s ease;
            }
            .todo-item.completed {
                background: #e7f5e7;
            }
            .todo-item input[type="checkbox"] {
                width: 24px;
                height: 24px;
                margin-right: 15px;
                cursor: pointer;
            }
            .todo-text {
                flex: 1;
                font-size: 18px;
            }
            .todo-item.completed .todo-text {
                text-decoration: line-through;
                color: #6c757d;
            }
            .stats {
                display: flex;
                gap: 20px;
                margin-top: 30px;
            }
            .stat-card {
                flex: 1;
                padding: 20px;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                text-align: center;
            }
            .stat-value {
                font-size: 36px;
                font-weight: bold;
                color: #28a745;
            }
            .stat-label {
                color: #6c757d;
                margin-top: 5px;
            }
        </style>
    </head>
    <body>
        <div dj-root class="demo-container">
            <div class="card">
                <div class="card-header bg-primary text-white">
                    <h3 class="mb-0">Optimistic Updates Demo - Todo List</h3>
                    <p class="mb-0 small">Click checkboxes rapidly - notice instant response</p>
                </div>
                <div class="card-body">
                    {% for todo in todos %}
                    <div class="todo-item {% if todo.completed %}completed{% endif %}">
                        <input
                            type="checkbox"
                            @change="toggle_todo"
                            data-id="{{ todo.id }}"
                            {% if todo.completed %}checked{% endif %}
                        >
                        <div class="todo-text">{{ todo.text }}</div>
                    </div>
                    {% endfor %}

                    <div class="stats">
                        <div class="stat-card">
                            <div class="stat-value">{{ completed_count }}</div>
                            <div class="stat-label">Completed</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{{ total_count }}</div>
                            <div class="stat-label">Total</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">{{ completion_percentage }}%</div>
                            <div class="stat-label">Progress</div>
                        </div>
                    </div>

                    <div class="alert alert-info mt-4">
                        <h5 class="alert-heading">How to test:</h5>
                        <ol class="mb-2">
                            <li>Click checkboxes rapidly</li>
                            <li>Notice instant response (no lag)</li>
                            <li>Open console: <code>window.djustDebug = true</code></li>
                            <li>Watch optimistic update logs</li>
                            <li>Note the 500ms simulated server delay</li>
                        </ol>
                        <p class="mb-0 small">
                            <strong>Decorator:</strong> <code>@optimistic</code><br>
                            <strong>Behavior:</strong> UI updates instantly, server validates in background
                        </p>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    def mount(self, request):
        """Initialize todo list"""
        self.todos = [
            {'id': 1, 'text': 'Write Phase 3 implementation', 'completed': False},
            {'id': 2, 'text': 'Add client.js optimistic update logic', 'completed': True},
            {'id': 3, 'text': 'Create demo views', 'completed': False},
            {'id': 4, 'text': 'Manual testing', 'completed': False},
            {'id': 5, 'text': 'Update documentation', 'completed': False},
            {'id': 6, 'text': 'Create PR for Phase 3', 'completed': False},
        ]

    @optimistic
    def toggle_todo(self, id: int = None, checked: bool = None, **kwargs):
        """
        Toggle todo completion (with optimistic update).

        The checkbox will update instantly on the client side.
        This method handles the server-side state update.

        Simulates 500ms network delay to demonstrate optimistic behavior.
        """
        # Find todo by ID
        todo = next((t for t in self.todos if t['id'] == int(id)), None)
        if not todo:
            return

        # Update completed state
        todo['completed'] = not todo['completed']

        # Simulate network delay (remove in production)
        time.sleep(0.5)

    def get_context_data(self, **kwargs):
        """Calculate stats for display"""
        completed_count = sum(1 for t in self.todos if t.get('completed'))
        total_count = len(self.todos)
        completion_percentage = round((completed_count / total_count * 100) if total_count > 0 else 0)

        return {
            'todos': self.todos,
            'completed_count': completed_count,
            'total_count': total_count,
            'completion_percentage': completion_percentage,
        }
