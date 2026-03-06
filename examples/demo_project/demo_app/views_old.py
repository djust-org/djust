"""
Demo LiveView examples
"""

from djust import LiveView, FormMixin
from djust._rust import fast_json_dumps
from django.views.generic import TemplateView
from django.http import JsonResponse
from .forms import RegistrationForm, ContactForm, ProfileForm, SearchForm
from .views.base import BaseTemplateView
from .views.navbar_example import BaseViewWithNavbar
from djust.components.layout import NavbarComponent, NavItem


class IndexView(LiveView):
    """
    Landing page with links to demos.

    A LiveView to demonstrate reactive navbar badges AND inline demos on the main page!
    """
    template_name = 'index.html'

    def mount(self, request, **kwargs):
        """Initialize with notification counter and demo state"""
        self.notification_count = 0

        # Inline demo state
        self.demo_counter = 0
        self.search_query = ""
        self.all_languages = [
            "Python", "JavaScript", "TypeScript", "Java", "Go",
            "Rust", "Ruby", "PHP", "C++", "C#", "Swift", "Kotlin"
        ]
        self.filtered_languages = self.all_languages
        self.demo_todos = []

    def increment_notifications(self):
        """Event handler to increment notifications"""
        self.notification_count += 1

    def reset_notifications(self):
        """Event handler to reset notifications"""
        self.notification_count = 0

    # Inline demo event handlers
    def increment_counter(self):
        """Counter demo: increment the counter"""
        self.demo_counter += 1

    def on_search_demo(self, value):
        """Search demo: filter languages"""
        self.search_query = value
        if value:
            self.filtered_languages = [
                lang for lang in self.all_languages
                if value.lower() in lang.lower()
            ]
        else:
            self.filtered_languages = self.all_languages

    def add_todo(self, todo_text=""):
        """Todo demo: add a new todo from user input"""
        if todo_text.strip():
            self.demo_todos.append({
                'text': todo_text.strip(),
                'done': False
            })

    def toggle_todo(self, index=0, **kwargs):
        """Todo demo: toggle todo completion"""
        if index == '' or index is None:
            index = 0
        index = int(index)
        if 0 <= index < len(self.demo_todos):
            self.demo_todos[index]['done'] = not self.demo_todos[index]['done']

    def delete_todo(self, index=0, **kwargs):
        """Todo demo: delete a todo"""
        if index == '' or index is None:
            index = 0
        index = int(index)
        if 0 <= index < len(self.demo_todos):
            self.demo_todos.pop(index)

    def get_context_data(self, **kwargs):
        """Add navbar with notification badge and demo data"""
        context = super().get_context_data(**kwargs)

        # Create navbar with notification badge on Demos
        navbar = NavbarComponent(
            brand_name="",
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=[
                NavItem("Home", "/", active=True),
                NavItem("Demos", "/demos/", badge=self.notification_count, badge_variant="danger"),
                NavItem("Components", "/kitchen-sink/"),
                NavItem("Forms", "/forms/"),
                NavItem("Docs", "/docs/"),
                NavItem("Hosting ↗", "https://djustlive.com", external=True),
            ],
            fixed_top=True,
            logo_height=16,
        )
        # Render the component to HTML before adding to context
        context['navbar'] = navbar.render()

        # Pass notification count to template
        context['notification_count'] = self.notification_count

        # Pass inline demo data
        context['demo_counter'] = self.demo_counter
        context['search_query'] = self.search_query
        context['filtered_languages'] = self.filtered_languages
        context['demo_todos'] = self.demo_todos

        return context


class NavbarBadgeDemo(LiveView):
    """
    Standalone demo showing reactive navbar badges.

    This is embedded in the homepage to demonstrate how navbar badges
    update in real-time when state changes.
    """
    template = """
    <div class="p-4 bg-white rounded-lg border border-gray-200">
        <!-- Navbar with badge -->
        {{ navbar.render }}

        <!-- Demo controls -->
        <div class="mt-6 text-center">
            <h3 class="text-xl font-bold text-gray-900 mb-4">
                Reactive Navbar Badge Demo
            </h3>
            <p class="text-gray-600 mb-4">
                Click buttons to see the navbar badge update in real-time!
            </p>

            <div class="flex gap-3 justify-center items-center mb-4">
                <button @click="increment_notifications"
                        class="px-6 py-3 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 transition">
                    🔔 Add Notification
                </button>
                <button @click="reset_notifications"
                        class="px-6 py-3 bg-gray-600 text-white rounded-lg font-semibold hover:bg-gray-700 transition">
                    🔄 Reset
                </button>
            </div>

            <div class="inline-block bg-blue-50 px-6 py-3 rounded-lg">
                <p class="text-sm text-gray-600 mb-1">Current Notifications</p>
                <p class="text-4xl font-bold text-blue-600">{{ notification_count }}</p>
            </div>

            <div class="mt-4 text-sm text-gray-500">
                <p>👆 Look at the "Demos" link in the navbar above</p>
                <p>The badge updates instantly via WebSocket!</p>
            </div>
        </div>
    </div>
    """

    def mount(self, request, **kwargs):
        """Initialize with notification counter"""
        self.notification_count = 0

    def increment_notifications(self):
        """Event handler to increment notifications"""
        self.notification_count += 1

    def reset_notifications(self):
        """Event handler to reset notifications"""
        self.notification_count = 0

    def get_context_data(self, **kwargs):
        """Add navbar with notification badge"""
        context = super().get_context_data(**kwargs)

        # Create navbar with notification badge on Demos
        context['navbar'] = NavbarComponent(
            brand_name="",
            brand_logo="/static/images/djust.png",
            brand_href="/",
            items=[
                NavItem("Home", "/", active=False),
                NavItem("Demos", "/demos/", badge=self.notification_count, badge_variant="danger"),
                NavItem("Components", "/kitchen-sink/"),
                NavItem("Forms", "/forms/"),
                NavItem("Docs", "/docs/"),
                NavItem("Hosting ↗", "https://djustlive.com", external=True),
            ],
            fixed_top=False,  # Not fixed in the iframe
            logo_height=16,
        )

        context['notification_count'] = self.notification_count

        return context


class CounterView(BaseViewWithNavbar):
    """
    Simple counter demo - showcases reactive state updates
    """
    template_name = "demos/counter.html"

    def mount(self, request, **kwargs):
        self.count = 0

    def increment(self):
        self.count += 1

    def decrement(self):
        self.count -= 1

    def reset(self):
        self.count = 0


class TodoView(BaseViewWithNavbar):
    """
    Todo list demo - showcases list manipulation and forms
    """
    template_name = "demos/todo.html"

    def mount(self, request, **kwargs):
        self.todos = []
        self.next_id = 1

    def add_todo(self, text=""):
        if text.strip():
            self.todos.append({
                'id': self.next_id,
                'text': text,
                'done': False,
            })
            self.next_id += 1

    def toggle_todo(self, id=None, **kwargs):
        # Accept both 'id' and 'todo_id' for backwards compatibility
        item_id = id or kwargs.get('todo_id')
        if item_id:
            for todo in self.todos:
                if todo['id'] == int(item_id):
                    todo['done'] = not todo['done']
                    break

    def delete_todo(self, id=None, **kwargs):
        # Accept both 'id' and 'todo_id' for backwards compatibility
        item_id = id or kwargs.get('todo_id')
        if item_id:
            self.todos = [t for t in self.todos if t['id'] != int(item_id)]

    @property
    def active_count(self):
        return sum(1 for t in self.todos if not t['done'])

    @property
    def done_count(self):
        return sum(1 for t in self.todos if t['done'])


class ChatView(BaseViewWithNavbar):
    """
    Chat demo - showcases real-time communication
    """
    template_name = "demos/chat.html"

    def mount(self, request, **kwargs):
        self.messages = []
        self.username = request.user.username if request.user.is_authenticated else "Guest"

    def send_message(self, message=""):
        if message.strip():
            import datetime
            self.messages.append({
                'user': self.username,
                'text': message,
                'time': datetime.datetime.now().strftime("%H:%M"),
            })


class ReactDemoView(BaseViewWithNavbar):
    """
    React integration demo - showcases React components within LiveView templates

    This demonstrates:
    - Using JSX-style component syntax in templates
    - Server-side rendering of React components with Rust
    - Client-side hydration for interactivity
    - Mixing server-side LiveView state with client-side React state
    """
    template_name = "demos/react.html"

    def mount(self, request, **kwargs):
        """Initialize component state"""
        # Import React components
        from . import react_components  # Ensure components are registered

        self.server_count = 0
        self.client_count = 0
        self.todos = [
            {'text': 'Try React integration', 'done': False},
            {'text': 'Build amazing apps', 'done': False},
        ]

    def increment_server(self):
        """Increment server-side counter (LiveView state)"""
        self.server_count += 1

    def add_todo_item(self, text=""):
        """Add a new todo item"""
        if text.strip():
            self.todos.append({'text': text, 'done': False})

    def delete_todo_item(self, text=""):
        """Delete a todo item by text"""
        self.todos = [t for t in self.todos if t['text'] != text]


class PerformanceTestView(BaseViewWithNavbar):
    """
    Performance test demo - stress testing with many interactive elements

    Features:
    - Large list rendering (100-1000 items)
    - Real-time filtering and sorting
    - Batch operations
    - Performance metrics tracking
    """
    template_name = "demos/performance.html"

    def mount(self, request, **kwargs):
        """Initialize with empty state"""
        self.items = []
        self.next_id = 1
        self.filter_text = ""
        self.sort_by = "name"

    def _generate_items(self, count):
        """Generate sample items"""
        import time
        import random

        priorities = ["Low", "Medium", "High"]
        base_time = time.time()

        new_items = []
        for i in range(count):
            new_items.append({
                'id': self.next_id,
                'name': f'Item {self.next_id:04d}',
                'priority': random.choice([True, False]),
                'selected': False,
                'timestamp': time.strftime('%H:%M:%S', time.localtime(base_time - i * 60)),
            })
            self.next_id += 1

        return new_items

    def _apply_filter_and_sort(self):
        """Apply current filter and sort settings"""
        if not hasattr(self, '_items'):
            return []

        # Filter
        if self.filter_text:
            filtered = [item for item in self._items if self.filter_text.lower() in item['name'].lower()]
        else:
            filtered = self._items[:]

        # Sort
        if self.sort_by == "name":
            filtered.sort(key=lambda x: x['name'])
        elif self.sort_by == "priority":
            filtered.sort(key=lambda x: (not x['priority'], x['name']))
        elif self.sort_by == "timestamp":
            filtered.sort(key=lambda x: x['timestamp'], reverse=True)

        return filtered

    @property
    def items(self):
        """Return filtered and sorted items"""
        return self._apply_filter_and_sort()

    @items.setter
    def items(self, value):
        """Set the raw items list"""
        self._items = value

    @property
    def item_count(self):
        return len(self._items) if hasattr(self, '_items') else 0

    @property
    def selected_count(self):
        if not hasattr(self, '_items'):
            return 0
        return sum(1 for item in self._items if item.get('selected', False))

    def add_items_10(self):
        """Add 10 items"""
        if not hasattr(self, '_items'):
            self._items = []
        self._items.extend(self._generate_items(10))

    def add_items_100(self):
        """Add 100 items"""
        if not hasattr(self, '_items'):
            self._items = []
        self._items.extend(self._generate_items(100))

    def add_items_1000(self):
        """Add 1000 items"""
        if not hasattr(self, '_items'):
            self._items = []
        self._items.extend(self._generate_items(1000))

    def clear_all(self):
        """Clear all items"""
        self._items = []
        self.next_id = 1
        self.filter_text = ""

    def filter_items(self, value=""):
        """Update filter text"""
        self.filter_text = value

    def sort_items(self, value="name"):
        """Update sort order"""
        self.sort_by = value

    def toggle_item(self, **kwargs):
        """Toggle item selection"""
        item_id = kwargs.get('id')
        if item_id and hasattr(self, '_items'):
            item_id = int(item_id)
            for item in self._items:
                if item['id'] == item_id:
                    item['selected'] = not item.get('selected', False)
                    break

    def select_all(self):
        """Select all items"""
        if hasattr(self, '_items'):
            for item in self._items:
                item['selected'] = True

    def deselect_all(self):
        """Deselect all items"""
        if hasattr(self, '_items'):
            for item in self._items:
                item['selected'] = False

    def delete_selected(self):
        """Delete all selected items"""
        if hasattr(self, '_items'):
            self._items = [item for item in self._items if not item.get('selected', False)]

    def toggle_priority(self, **kwargs):
        """Toggle item priority"""
        item_id = kwargs.get('id')
        if item_id and hasattr(self, '_items'):
            item_id = int(item_id)
            for item in self._items:
                if item['id'] == item_id:
                    item['priority'] = not item.get('priority', False)
                    break

    def toggle_priority_selected(self):
        """Toggle priority for all selected items"""
        if hasattr(self, '_items'):
            for item in self._items:
                if item.get('selected', False):
                    item['priority'] = not item.get('priority', False)

    def delete_item(self, **kwargs):
        """Delete a specific item"""
        item_id = kwargs.get('id')
        if item_id and hasattr(self, '_items'):
            item_id = int(item_id)
            self._items = [item for item in self._items if item['id'] != item_id]


class ProductDataTableView(BaseViewWithNavbar):
    """
    React DataTable demo - showcases hybrid LiveView + React components

    This view demonstrates how to integrate React components into LiveView:
    - Server manages the data state
    - React handles rich UI (sorting, filtering, pagination)
    - Custom POST handler returns JSON instead of patches
    """

    template_name = "demos/datatable.html"

    # Disable normal LiveView patching for this view
    use_dom_patching = False

    def get_template_OLD(self):
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Product DataTable - Django Rust Live</title>
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
            <link rel="stylesheet" href="/static/css/datatable.css">
            <style>
                body {
                    background-color: #f5f5f5;
                    padding: 20px;
                }
                .header {
                    background: white;
                    padding: 30px;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    margin-bottom: 20px;
                }
                .stats {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 15px;
                    margin-top: 20px;
                }
                .stat-card {
                    background: #f8f9fa;
                    padding: 15px;
                    border-radius: 6px;
                    text-align: center;
                }
                .stat-value {
                    font-size: 24px;
                    font-weight: bold;
                    color: #007bff;
                }
                .stat-label {
                    font-size: 14px;
                    color: #6c757d;
                    margin-top: 5px;
                }
                .actions {
                    display: flex;
                    gap: 10px;
                    margin-top: 15px;
                }
            </style>
        </head>
        <body>
            <div class="container-fluid">
                <div class="header">
                    <h1>Product DataTable</h1>
                    <p class="text-muted">Hybrid LiveView + React DataTable Example</p>

                    <div class="stats">
                        <div class="stat-card">
                            <div class="stat-value" id="stat-total">{{ total_products }}</div>
                            <div class="stat-label">Total Products</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="stat-active">{{ active_products }}</div>
                            <div class="stat-label">Active</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="stat-value">${{ total_value }}</div>
                            <div class="stat-label">Total Inventory Value</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value" id="stat-low-stock">{{ low_stock_count }}</div>
                            <div class="stat-label">Low Stock Items</div>
                        </div>
                    </div>

                    <div class="actions">
                        <button onclick="handleAction('add_sample_products')" class="btn btn-primary">Add Sample Products</button>
                        <button onclick="handleAction('clear_products')" class="btn btn-danger">Clear All</button>
                        <button onclick="handleAction('toggle_inactive')" class="btn btn-secondary">Toggle Inactive</button>
                    </div>
                </div>

                <!-- React DataTable Mount Point -->
                <div id="react-datatable-root"></div>
            </div>

            <!-- React and Babel -->
            <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
            <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
            <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>

            <!-- Initial data from server -->
            <script>
                window.INITIAL_PRODUCTS = {{ products_json }};
            </script>

            <!-- DataTable Component + Initialization -->
            <script type="text/babel">
                const { useState, useEffect, useMemo } = React;

                // DataTable Component
                function DataTable({ data, columns, onEvent }) {
                    console.log('[DataTable] Component called with', data?.length, 'items');
                    const [sortColumn, setSortColumn] = useState(null);
                    const [sortDirection, setSortDirection] = useState('asc');
                    const [filterText, setFilterText] = useState('');
                    const [currentPage, setCurrentPage] = useState(1);
                    const [pageSize, setPageSize] = useState(10);

                    // Sort data
                    const sortedData = useMemo(() => {
                        if (!sortColumn) return data;
                        return [...data].sort((a, b) => {
                            const aVal = a[sortColumn];
                            const bVal = b[sortColumn];
                            if (aVal === bVal) return 0;
                            const comparison = aVal < bVal ? -1 : 1;
                            return sortDirection === 'asc' ? comparison : -comparison;
                        });
                    }, [data, sortColumn, sortDirection]);

                    // Filter data
                    const filteredData = useMemo(() => {
                        if (!filterText) return sortedData;
                        const lowerFilter = filterText.toLowerCase();
                        return sortedData.filter(row => {
                            return columns.some(col => {
                                const value = String(row[col.key] || '').toLowerCase();
                                return value.includes(lowerFilter);
                            });
                        });
                    }, [sortedData, filterText, columns]);

                    // Paginate data
                    const paginatedData = useMemo(() => {
                        const start = (currentPage - 1) * pageSize;
                        return filteredData.slice(start, start + pageSize);
                    }, [filteredData, currentPage, pageSize]);

                    const totalPages = Math.ceil(filteredData.length / pageSize);

                    const handleSort = (columnKey) => {
                        if (sortColumn === columnKey) {
                            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
                        } else {
                            setSortColumn(columnKey);
                            setSortDirection('asc');
                        }
                    };

                    const handleRowClick = (row) => {
                        if (onEvent) {
                            onEvent('row_click', { id: row.id });
                        }
                    };

                    const handlePageChange = (page) => {
                        setCurrentPage(page);
                    };

                    // Define styles to avoid Django template {{ }} conflicts
                    const thStyle = { cursor: 'pointer', userSelect: 'none' };
                    const rowStyle = { cursor: 'pointer' };

                    return (
                        <div className="datatable-container">
                            {/* Filter */}
                            <div className="datatable-filter">
                                <input
                                    type="text"
                                    className="form-control"
                                    placeholder="Search..."
                                    value={filterText}
                                    onChange={(e) => {
                                        setFilterText(e.target.value);
                                        setCurrentPage(1);
                                    }}
                                />
                            </div>

                            {/* Table */}
                            <div className="table-responsive">
                                <table className="table table-striped table-hover">
                                    <thead>
                                        <tr>
                                            {columns.map((col) => (
                                                <th
                                                    key={col.key}
                                                    onClick={() => handleSort(col.key)}
                                                    style={thStyle}
                                                >
                                                    {col.label}
                                                    {sortColumn === col.key && (
                                                        <span className="sort-indicator">
                                                            {sortDirection === 'asc' ? ' ▲' : ' ▼'}
                                                        </span>
                                                    )}
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {paginatedData.length === 0 ? (
                                            <tr>
                                                <td colSpan={columns.length} className="text-center text-muted">
                                                    No data found
                                                </td>
                                            </tr>
                                        ) : (
                                            paginatedData.map((row, idx) => (
                                                <tr
                                                    key={row.id || idx}
                                                    onClick={() => handleRowClick(row)}
                                                    style={rowStyle}
                                                >
                                                    {columns.map((col) => (
                                                        <td key={col.key}>
                                                            {col.render
                                                                ? col.render(row[col.key], row)
                                                                : row[col.key]}
                                                        </td>
                                                    ))}
                                                </tr>
                                            ))
                                        )}
                                    </tbody>
                                </table>
                            </div>

                            {/* Pagination */}
                            <div className="datatable-pagination">
                                <div className="pagination-info">
                                    Showing {(currentPage - 1) * pageSize + 1} to{' '}
                                    {Math.min(currentPage * pageSize, filteredData.length)} of{' '}
                                    {filteredData.length} entries
                                    {filterText && ` (filtered from ${data.length} total)`}
                                </div>

                                <div className="pagination-controls">
                                    <select
                                        className="form-control form-control-sm"
                                        value={pageSize}
                                        onChange={(e) => {
                                            setPageSize(Number(e.target.value));
                                            setCurrentPage(1);
                                        }}
                                    >
                                        <option value="10">10</option>
                                        <option value="25">25</option>
                                        <option value="50">50</option>
                                        <option value="100">100</option>
                                    </select>

                                    <nav>
                                        <ul className="pagination pagination-sm mb-0">
                                            <li className={`page-item ${currentPage === 1 ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(1)}
                                                    disabled={currentPage === 1}
                                                >
                                                    First
                                                </button>
                                            </li>
                                            <li className={`page-item ${currentPage === 1 ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(currentPage - 1)}
                                                    disabled={currentPage === 1}
                                                >
                                                    Previous
                                                </button>
                                            </li>

                                            {/* Page numbers */}
                                            {[...Array(Math.min(5, totalPages))].map((_, i) => {
                                                let pageNum;
                                                if (totalPages <= 5) {
                                                    pageNum = i + 1;
                                                } else if (currentPage <= 3) {
                                                    pageNum = i + 1;
                                                } else if (currentPage >= totalPages - 2) {
                                                    pageNum = totalPages - 4 + i;
                                                } else {
                                                    pageNum = currentPage - 2 + i;
                                                }

                                                return (
                                                    <li
                                                        key={pageNum}
                                                        className={`page-item ${currentPage === pageNum ? 'active' : ''}`}
                                                    >
                                                        <button
                                                            className="page-link"
                                                            onClick={() => handlePageChange(pageNum)}
                                                        >
                                                            {pageNum}
                                                        </button>
                                                    </li>
                                                );
                                            })}

                                            <li className={`page-item ${currentPage === totalPages ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(currentPage + 1)}
                                                    disabled={currentPage === totalPages}
                                                >
                                                    Next
                                                </button>
                                            </li>
                                            <li className={`page-item ${currentPage === totalPages ? 'disabled' : ''}`}>
                                                <button
                                                    className="page-link"
                                                    onClick={() => handlePageChange(totalPages)}
                                                    disabled={currentPage === totalPages}
                                                >
                                                    Last
                                                </button>
                                            </li>
                                        </ul>
                                    </nav>
                                </div>
                            </div>
                        </div>
                    );
                }

                // Bridge between server and React
                let reactSetData = null;  // Will be set by React component

                // Bridge between server and React
                function LiveViewDataTable({ initialData }) {
                    console.log('[DataTable] LiveViewDataTable rendering with', initialData?.length, 'products');
                    const [data, setData] = useState(initialData);

                    // Expose setData globally so server events can update it
                    React.useEffect(() => {
                        console.log('[DataTable] Setting up reactSetData');
                        reactSetData = setData;
                    }, []);

                    // Handle events from React back to server
                    const handleEvent = async (eventName, params) => {
                        console.log('[React->Server] Event:', eventName, params);
                        // Could send to server if needed
                    };

                    // Define columns
                    const columns = [
                        { key: 'id', label: 'ID' },
                        { key: 'name', label: 'Product Name' },
                        { key: 'category', label: 'Category' },
                        {
                            key: 'price',
                            label: 'Price',
                            render: (value) => (
                                <span className="price">${value}</span>
                            )
                        },
                        {
                            key: 'stock',
                            label: 'Stock',
                            render: (value) => {
                                const className = value < 10 ? 'stock low' : value < 50 ? 'stock medium' : 'stock high';
                                return <span className={className}>{value}</span>;
                            }
                        },
                        {
                            key: 'is_active',
                            label: 'Status',
                            render: (value) => (
                                <span className={`badge ${value ? 'badge-success' : 'badge-danger'}`}>
                                    {value ? 'Active' : 'Inactive'}
                                </span>
                            )
                        }
                    ];

                    console.log('[DataTable] Rendering DataTable component...');
                    console.log('[DataTable] Data length:', data?.length);
                    console.log('[DataTable] Columns:', columns);

                    // Test simple render first
                    if (!data || data.length === 0) {
                        console.log('[DataTable] No data, rendering empty message');
                        return React.createElement('div', { style: { padding: '20px', background: 'yellow' } },
                            'No data available'
                        );
                    }

                    console.log('[DataTable] Calling DataTable component');
                    const result = React.createElement(DataTable, { data, columns, onEvent: handleEvent });
                    console.log('[DataTable] Created element:', result);
                    return result;
                }

                // Error Boundary
                class ErrorBoundary extends React.Component {
                    constructor(props) {
                        super(props);
                        this.state = { hasError: false, error: null };
                    }

                    static getDerivedStateFromError(error) {
                        return { hasError: true, error };
                    }

                    componentDidCatch(error, errorInfo) {
                        console.error('[ErrorBoundary] Caught error:', error, errorInfo);
                    }

                    render() {
                        if (this.state.hasError) {
                            return React.createElement('div', { style: { color: 'red', padding: '20px' } },
                                React.createElement('h2', null, 'Something went wrong'),
                                React.createElement('pre', null, this.state.error?.toString())
                            );
                        }
                        return this.props.children;
                    }
                }

                // Initial render
                try {
                    const rootElement = document.getElementById('react-datatable-root');
                    console.log('[DataTable] Root element:', rootElement);

                    if (!rootElement) {
                        console.error('[DataTable] Could not find react-datatable-root element!');
                    } else {
                        const root = ReactDOM.createRoot(rootElement);
                        console.log('[DataTable] ReactDOM root created, rendering...');
                        console.log('[DataTable] INITIAL_PRODUCTS:', window.INITIAL_PRODUCTS);

                        // Test with simple component first
                        root.render(
                            React.createElement(ErrorBoundary, null,
                                React.createElement(LiveViewDataTable, { initialData: window.INITIAL_PRODUCTS })
                            )
                        );
                        console.log('[DataTable] Render called successfully');
                    }
                } catch (error) {
                    console.error('[DataTable] Error during initialization:', error);
                }
            </script>

            <!-- Handle server actions -->
            <script>
                function getCookie(name) {
                    let cookieValue = null;
                    if (document.cookie && document.cookie !== '') {
                        const cookies = document.cookie.split(';');
                        for (let i = 0; i < cookies.length; i++) {
                            const cookie = cookies[i].trim();
                            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                                break;
                            }
                        }
                    }
                    return cookieValue;
                }

                async function handleAction(action) {
                    console.log('[Client] Action:', action);

                    try {
                        const response = await fetch(window.location.href, {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': getCookie('csrftoken'),
                            },
                            body: JSON.stringify({
                                event: action,
                                params: {}
                            })
                        });

                        if (response.ok) {
                            const data = await response.json();
                            console.log('[Server] Response:', data);

                            // Update stats
                            if (data.stats) {
                                document.getElementById('stat-total').textContent = data.stats.total_products;
                                document.getElementById('stat-active').textContent = data.stats.active_products;
                                document.getElementById('stat-value').textContent = '$' + data.stats.total_value;
                                document.getElementById('stat-low-stock').textContent = data.stats.low_stock_count;
                            }

                            // Update React table data
                            if (data.products && reactSetData) {
                                reactSetData(data.products);
                            }
                        }
                    } catch (error) {
                        console.error('[Client] Error:', error);
                    }
                }
            </script>
        </body>
        </html>
        """

    def post(self, request, *args, **kwargs):
        """Override POST to return JSON instead of patches"""
        import json

        try:
            # Ensure products is initialized
            if not hasattr(self, 'products'):
                self.products = self._generate_sample_products(20)

            data = json.loads(request.body)
            event = data.get('event')
            params = data.get('params', {})

            # Call the event handler
            handler = getattr(self, event, None)
            if handler and callable(handler):
                if params:
                    handler(**params)
                else:
                    handler()

            # Return JSON with updated data and stats
            total_value = sum(float(p['price']) * p['stock'] for p in self.products)
            active_products = sum(1 for p in self.products if p['is_active'])
            low_stock = sum(1 for p in self.products if p['stock'] < 10)

            return JsonResponse({
                'products': self.products,
                'stats': {
                    'total_products': len(self.products),
                    'active_products': active_products,
                    'total_value': f"{total_value:,.2f}",
                    'low_stock_count': low_stock,
                }
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({'error': 'An error occurred. Check server logs.'}, status=500)

    def mount(self, request, **kwargs):
        """Initialize with sample data"""
        self.products = self._generate_sample_products(20)

    def add_sample_products(self):
        """Add more sample products"""
        new_products = self._generate_sample_products(10, start_id=len(self.products) + 1)
        self.products.extend(new_products)

    def clear_products(self):
        """Clear all products"""
        self.products = []

    def toggle_inactive(self):
        """Toggle active status of some products"""
        import random
        for product in random.sample(self.products, min(5, len(self.products))):
            product['is_active'] = not product['is_active']

    def get_context_data(self, **kwargs):
        """Provide data to template"""
        # Get base context (includes navbar)
        context = super().get_context_data(**kwargs)

        # Use Rust-powered JSON serialization
        # Benefits: Releases GIL (better for concurrent workloads), more memory efficient
        # Trade-off: Slightly slower than Python json.dumps for small datasets due to PyO3 overhead

        total_value = sum(float(p['price']) * p['stock'] for p in self.products)
        active_products = sum(1 for p in self.products if p['is_active'])
        low_stock = sum(1 for p in self.products if p['stock'] < 10)

        context.update({
            'products': self.products,
            'products_json': fast_json_dumps(self.products),  # Rust-powered serialization
            'total_products': len(self.products),
            'active_products': active_products,
            'total_value': f"{total_value:,.2f}",
            'low_stock_count': low_stock,
        })

        return context

    def _generate_sample_products(self, count, start_id=1):
        """Generate sample product data"""
        import random

        categories = ['Electronics', 'Clothing', 'Food', 'Books', 'Toys', 'Sports', 'Home & Garden']
        adjectives = ['Premium', 'Deluxe', 'Standard', 'Economy', 'Pro', 'Ultra', 'Mini', 'Max']
        nouns = ['Widget', 'Gadget', 'Device', 'Tool', 'Item', 'Product', 'Kit', 'Set']

        products = []
        for i in range(count):
            product_id = start_id + i
            name = f"{random.choice(adjectives)} {random.choice(nouns)} {product_id}"
            category = random.choice(categories)
            price = round(random.uniform(9.99, 499.99), 2)
            stock = random.randint(0, 200)
            is_active = random.choice([True, True, True, False])  # 75% active

            products.append({
                'id': product_id,
                'name': name,
                'category': category,
                'price': str(price),
                'stock': stock,
                'is_active': is_active,
            })

        return products


class RegistrationFormView(FormMixin, LiveView):
    """
    User registration form with real-time validation

    Demonstrates:
    - Field-level validation on change
    - Password matching validation
    - Custom clean methods
    - Success/error messaging
    """

    form_class = RegistrationForm

    template = """
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background: #f5f5f5; }
        .card { border-radius: 10px; }
        .card-header { border-radius: 10px 10px 0 0 !important; }
        .form-label { font-weight: 500; }
        .invalid-feedback { display: block; }
    </style>
    <div class="container">
        <div class="row justify-content-center mt-5">
            <div class="col-md-6">
                <div class="card shadow">
                    <div class="card-header bg-primary text-white">
                        <h2 class="mb-0">Create Account</h2>
                    </div>
                    <div class="card-body">
                        <div class="alert alert-success alert-dismissible fade show{% if not success_message %} d-none{% endif %}" role="alert">
                            {{ success_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>

                        <div class="alert alert-danger alert-dismissible fade show{% if not error_message %} d-none{% endif %}" role="alert">
                            {{ error_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>

                        <form @submit="submit_form" class="needs-validation" novalidate>
                            <!-- Username -->
                            <div class="mb-3">
                                <label for="username" class="form-label">Username</label>
                                <input
                                    type="text"
                                    name="username"
                                    id="username"
                                    class="form-control {% if field_errors.username %}is-invalid{% endif %}"
                                    value="{{ form_data.username }}"
                                    @change="validate_field"
                                    data-field="username"
                                    required
                                />
                                <small class="form-text text-muted">Username must be 3-150 characters</small>
                                {% if field_errors.username %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.username %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Email -->
                            <div class="mb-3">
                                <label for="email" class="form-label">Email</label>
                                <input
                                    type="email"
                                    name="email"
                                    id="email"
                                    class="form-control {% if field_errors.email %}is-invalid{% endif %}"
                                    value="{{ form_data.email }}"
                                    @change="validate_field"
                                    data-field="email"
                                    required
                                />
                                <small class="form-text text-muted">We'll never share your email</small>
                                {% if field_errors.email %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.email %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Password -->
                            <div class="mb-3">
                                <label for="password" class="form-label">Password</label>
                                <input
                                    type="password"
                                    name="password"
                                    id="password"
                                    class="form-control {% if field_errors.password %}is-invalid{% endif %}"
                                    value="{{ form_data.password }}"
                                    @change="validate_field"
                                    data-field="password"
                                    required
                                />
                                <small class="form-text text-muted">Password must be at least 8 characters</small>
                                {% if field_errors.password %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.password %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Confirm Password -->
                            <div class="mb-3">
                                <label for="password_confirm" class="form-label">Confirm Password</label>
                                <input
                                    type="password"
                                    name="password_confirm"
                                    id="password_confirm"
                                    class="form-control {% if field_errors.password_confirm %}is-invalid{% endif %}"
                                    value="{{ form_data.password_confirm }}"
                                    @change="validate_field"
                                    data-field="password_confirm"
                                    required
                                />
                                {% if field_errors.password_confirm %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.password_confirm %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Terms and Conditions -->
                            <div class="mb-3 form-check">
                                <input
                                    type="checkbox"
                                    name="agree_terms"
                                    id="agree_terms"
                                    class="form-check-input {% if field_errors.agree_terms %}is-invalid{% endif %}"
                                    {% if form_data.agree_terms %}checked{% endif %}
                                    @change="validate_field"
                                    data-field="agree_terms"
                                    required
                                />
                                <label class="form-check-label" for="agree_terms">
                                    I agree to the Terms and Conditions
                                </label>
                                {% if field_errors.agree_terms %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.agree_terms %}
                                    <div>{{ error }}</div>
                                    {% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Non-field errors -->
                            {% if form_errors %}
                            <div class="alert alert-danger">
                                {% for error in form_errors %}
                                <div>{{ error }}</div>
                                {% endfor %}
                            </div>
                            {% endif %}

                            <!-- Submit Button -->
                            <div class="d-grid gap-2">
                                <button type="submit" class="btn btn-primary btn-lg">
                                    Create Account
                                </button>
                                <button type="button" class="btn btn-outline-secondary" @click="reset_form">
                                    Reset Form
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
    """

    def form_valid(self, form):
        """Handle successful registration"""
        self.success_message = f"Account created successfully for {form.cleaned_data['username']}!"
        # In real app: save user, send email, etc.

    def form_invalid(self, form):
        """Handle validation errors"""
        self.error_message = "Please correct the errors below"

    def clear_message(self, **kwargs):
        """Clear success/error messages"""
        self.success_message = ""
        self.error_message = ""


class ContactFormView(FormMixin, LiveView):
    """
    Contact form with various field types

    Demonstrates:
    - Text, email, textarea fields
    - Select dropdowns
    - Radio buttons
    - Checkboxes
    - Custom validation
    """

    form_class = ContactForm

    template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ContactForm - Django Rust Live</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
            .card { border-radius: 10px; }
            .card-header { border-radius: 10px 10px 0 0 !important; }
            .form-label { font-weight: 500; }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="row justify-content-center mt-5">
            <div class="col-md-8">
                <div class="card shadow">
                    <div class="card-header bg-success text-white">
                        <h2 class="mb-0">Contact Us</h2>
                    </div>
                    <div class="card-body">
                        {% if success_message %}
                        <div class="alert alert-success alert-dismissible fade show" role="alert">
                            {{ success_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>
                        {% endif %}

                        {% if error_message %}
                        <div class="alert alert-danger" role="alert">
                            {{ error_message }}
                        </div>
                        {% endif %}

                        <form @submit="submit_form" class="needs-validation" novalidate>
                            <div class="row">
                                <!-- Name -->
                                <div class="col-md-6 mb-3">
                                    <label for="name" class="form-label">Name</label>
                                    <input
                                        type="text"
                                        name="name"
                                        id="name"
                                        class="form-control {% if field_errors.name %}is-invalid{% endif %}"
                                        value="{{ form_data.name }}"
                                        @change="validate_field"
                                        data-field="name"
                                        required
                                    />
                                    {% if field_errors.name %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.name %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Email -->
                                <div class="col-md-6 mb-3">
                                    <label for="email" class="form-label">Email</label>
                                    <input
                                        type="email"
                                        name="email"
                                        id="email"
                                        class="form-control {% if field_errors.email %}is-invalid{% endif %}"
                                        value="{{ form_data.email }}"
                                        @change="validate_field"
                                        data-field="email"
                                        required
                                    />
                                    {% if field_errors.email %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.email %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <!-- Subject -->
                            <div class="mb-3">
                                <label for="subject" class="form-label">Subject</label>
                                <select
                                    name="subject"
                                    id="subject"
                                    class="form-control {% if field_errors.subject %}is-invalid{% endif %}"
                                    @change="validate_field"
                                    data-field="subject"
                                    required
                                >
                                    <option value="">Select a subject...</option>
                                    <option value="general" {% if form_data.subject == "general" %}selected{% endif %}>General Inquiry</option>
                                    <option value="support" {% if form_data.subject == "support" %}selected{% endif %}>Technical Support</option>
                                    <option value="billing" {% if form_data.subject == "billing" %}selected{% endif %}>Billing Question</option>
                                    <option value="feedback" {% if form_data.subject == "feedback" %}selected{% endif %}>Feedback</option>
                                    <option value="other" {% if form_data.subject == "other" %}selected{% endif %}>Other</option>
                                </select>
                                {% if field_errors.subject %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.subject %}{{ error }}{% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Priority -->
                            <div class="mb-3">
                                <label class="form-label">Priority</label>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_low" value="low"
                                           {% if form_data.priority == "low" %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_low">Low</label>
                                </div>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_medium" value="medium"
                                           {% if form_data.priority == "medium" or not form_data.priority %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_medium">Medium</label>
                                </div>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_high" value="high"
                                           {% if form_data.priority == "high" %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_high">High</label>
                                </div>
                                <div class="form-check">
                                    <input class="form-check-input" type="radio" name="priority" id="priority_urgent" value="urgent"
                                           {% if form_data.priority == "urgent" %}checked{% endif %}
                                           @change="validate_field" data-field="priority">
                                    <label class="form-check-label" for="priority_urgent">Urgent</label>
                                </div>
                            </div>

                            <!-- Message -->
                            <div class="mb-3">
                                <label for="message" class="form-label">Message</label>
                                <textarea
                                    name="message"
                                    id="message"
                                    class="form-control {% if field_errors.message %}is-invalid{% endif %}"
                                    rows="5"
                                    @change="validate_field"
                                    data-field="message"
                                    required
                                >{{ form_data.message }}</textarea>
                                <small class="form-text text-muted">Please provide details (minimum 10 characters)</small>
                                {% if field_errors.message %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.message %}{{ error }}{% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <!-- Newsletter -->
                            <div class="mb-3 form-check">
                                <input
                                    type="checkbox"
                                    name="subscribe_newsletter"
                                    id="subscribe_newsletter"
                                    class="form-check-input"
                                    {% if form_data.subscribe_newsletter %}checked{% endif %}
                                    @change="validate_field"
                                    data-field="subscribe_newsletter"
                                />
                                <label class="form-check-label" for="subscribe_newsletter">
                                    Subscribe to newsletter
                                </label>
                            </div>

                            <!-- Submit -->
                            <div class="d-grid gap-2">
                                <button type="submit" class="btn btn-success btn-lg">
                                    Send Message
                                </button>
                                <button type="button" class="btn btn-outline-secondary" @click="reset_form">
                                    Reset Form
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
    </body>
    </html>
    """

    def form_valid(self, form):
        """Handle successful submission"""
        self.success_message = f"Thank you {form.cleaned_data['name']}! Your message has been sent."
        # In real app: send email, save to database, etc.

    def form_invalid(self, form):
        """Handle validation errors"""
        self.error_message = "Please correct the errors below"

    def clear_message(self, **kwargs):
        """Clear messages"""
        self.success_message = ""
        self.error_message = ""


class ProfileFormView(FormMixin, LiveView):
    """
    Profile form demonstrating various field types

    Demonstrates:
    - Date fields
    - URL fields
    - Phone fields
    - Optional fields
    - Field help text
    """

    form_class = ProfileForm

    template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ProfileForm - Django Rust Live</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
            .card { border-radius: 10px; }
            .card-header { border-radius: 10px 10px 0 0 !important; }
            .form-label { font-weight: 500; }
        </style>
    </head>
    <body>
    <div class="container">
        <div class="row justify-content-center mt-5">
            <div class="col-md-8">
                <div class="card shadow">
                    <div class="card-header bg-info text-white">
                        <h2 class="mb-0">Edit Profile</h2>
                    </div>
                    <div class="card-body">
                        {% if success_message %}
                        <div class="alert alert-success alert-dismissible fade show" role="alert">
                            {{ success_message }}
                            <button type="button" class="btn-close" @click="clear_message"></button>
                        </div>
                        {% endif %}

                        <form @submit="submit_form" class="needs-validation" novalidate>
                            <div class="row">
                                <!-- First Name -->
                                <div class="col-md-6 mb-3">
                                    <label for="first_name" class="form-label">First Name</label>
                                    <input
                                        type="text"
                                        name="first_name"
                                        id="first_name"
                                        class="form-control {% if field_errors.first_name %}is-invalid{% endif %}"
                                        value="{{ form_data.first_name }}"
                                        @change="validate_field"
                                        data-field="first_name"
                                        required
                                    />
                                    {% if field_errors.first_name %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.first_name %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Last Name -->
                                <div class="col-md-6 mb-3">
                                    <label for="last_name" class="form-label">Last Name</label>
                                    <input
                                        type="text"
                                        name="last_name"
                                        id="last_name"
                                        class="form-control {% if field_errors.last_name %}is-invalid{% endif %}"
                                        value="{{ form_data.last_name }}"
                                        @change="validate_field"
                                        data-field="last_name"
                                        required
                                    />
                                    {% if field_errors.last_name %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.last_name %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <!-- Bio -->
                            <div class="mb-3">
                                <label for="bio" class="form-label">Bio</label>
                                <textarea
                                    name="bio"
                                    id="bio"
                                    class="form-control {% if field_errors.bio %}is-invalid{% endif %}"
                                    rows="4"
                                    @change="validate_field"
                                    data-field="bio"
                                >{{ form_data.bio }}</textarea>
                                <small class="form-text text-muted">Tell us about yourself (max 500 characters)</small>
                                {% if field_errors.bio %}
                                <div class="invalid-feedback d-block">
                                    {% for error in field_errors.bio %}{{ error }}{% endfor %}
                                </div>
                                {% endif %}
                            </div>

                            <div class="row">
                                <!-- Birth Date -->
                                <div class="col-md-6 mb-3">
                                    <label for="birth_date" class="form-label">Birth Date</label>
                                    <input
                                        type="date"
                                        name="birth_date"
                                        id="birth_date"
                                        class="form-control {% if field_errors.birth_date %}is-invalid{% endif %}"
                                        value="{{ form_data.birth_date }}"
                                        @change="validate_field"
                                        data-field="birth_date"
                                    />
                                    {% if field_errors.birth_date %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.birth_date %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Country -->
                                <div class="col-md-6 mb-3">
                                    <label for="country" class="form-label">Country</label>
                                    <select
                                        name="country"
                                        id="country"
                                        class="form-control {% if field_errors.country %}is-invalid{% endif %}"
                                        @change="validate_field"
                                        data-field="country"
                                    >
                                        <option value="">Select country...</option>
                                        <option value="US" {% if form_data.country == "US" %}selected{% endif %}>United States</option>
                                        <option value="UK" {% if form_data.country == "UK" %}selected{% endif %}>United Kingdom</option>
                                        <option value="CA" {% if form_data.country == "CA" %}selected{% endif %}>Canada</option>
                                        <option value="AU" {% if form_data.country == "AU" %}selected{% endif %}>Australia</option>
                                        <option value="DE" {% if form_data.country == "DE" %}selected{% endif %}>Germany</option>
                                        <option value="FR" {% if form_data.country == "FR" %}selected{% endif %}>France</option>
                                        <option value="JP" {% if form_data.country == "JP" %}selected{% endif %}>Japan</option>
                                        <option value="other" {% if form_data.country == "other" %}selected{% endif %}>Other</option>
                                    </select>
                                    {% if field_errors.country %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.country %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <div class="row">
                                <!-- Phone -->
                                <div class="col-md-6 mb-3">
                                    <label for="phone" class="form-label">Phone</label>
                                    <input
                                        type="text"
                                        name="phone"
                                        id="phone"
                                        class="form-control {% if field_errors.phone %}is-invalid{% endif %}"
                                        value="{{ form_data.phone }}"
                                        @change="validate_field"
                                        data-field="phone"
                                        placeholder="+1 (555) 123-4567"
                                    />
                                    <small class="form-text text-muted">Optional contact number</small>
                                    {% if field_errors.phone %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.phone %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>

                                <!-- Website -->
                                <div class="col-md-6 mb-3">
                                    <label for="website" class="form-label">Website</label>
                                    <input
                                        type="url"
                                        name="website"
                                        id="website"
                                        class="form-control {% if field_errors.website %}is-invalid{% endif %}"
                                        value="{{ form_data.website }}"
                                        @change="validate_field"
                                        data-field="website"
                                        placeholder="https://yourwebsite.com"
                                    />
                                    {% if field_errors.website %}
                                    <div class="invalid-feedback d-block">
                                        {% for error in field_errors.website %}{{ error }}{% endfor %}
                                    </div>
                                    {% endif %}
                                </div>
                            </div>

                            <!-- Receive Updates -->
                            <div class="mb-3 form-check">
                                <input
                                    type="checkbox"
                                    name="receive_updates"
                                    id="receive_updates"
                                    class="form-check-input"
                                    {% if form_data.receive_updates %}checked{% endif %}
                                    @change="validate_field"
                                    data-field="receive_updates"
                                />
                                <label class="form-check-label" for="receive_updates">
                                    Receive email updates
                                </label>
                            </div>

                            <!-- Submit -->
                            <div class="d-grid gap-2">
                                <button type="submit" class="btn btn-info btn-lg text-white">
                                    Save Profile
                                </button>
                                <button type="button" class="btn btn-outline-secondary" @click="reset_form">
                                    Reset Form
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
    </body>
    </html>
    """

    def form_valid(self, form):
        """Handle successful submission"""
        self.success_message = "Profile updated successfully!"
        # In real app: save to database

    def clear_message(self, **kwargs):
        """Clear messages"""
        self.success_message = ""


# FormsIndexView has been moved to views/forms_demo.py
# SimpleContactFormView has been moved to views/forms_demo.py
