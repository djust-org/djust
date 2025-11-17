"""
Live Demos - interactive examples showcasing Django Rust Live features
"""

from djust import LiveView
from djust._rust import fast_json_dumps
from django.http import JsonResponse
from djust_shared.views import BaseTemplateView
from djust_shared.views import BaseViewWithNavbar
from .counter_demo import CounterView  # Counter demo in separate file
from djust_demos.demo_classes import CounterDemo, DropdownDemo, DebounceDemo, CacheDemo


class DemosIndexView(BaseViewWithNavbar):
    """
    Unified demos page with multiple embedded interactive examples.

    This page contains inline working demos similar to NiceGUI's documentation style.
    Each demo section is interactive and updates in real-time.

    Each demo is a self-contained class that manages its own state and code examples.
    The view automatically routes events to the appropriate demo instance.
    """
    template_name = 'demos/index.html'


class DemosIndexDesign1View(BaseViewWithNavbar):
    """Design Proposal 1: Modern Card-Based Layout"""
    template_name = 'demos/index_design1.html'


class DemosIndexDesign2View(BaseViewWithNavbar):
    """Design Proposal 2: Showcase/Portfolio Style"""
    template_name = 'demos/index_design2.html'


class DemosIndexDesign3View(BaseViewWithNavbar):
    """Design Proposal 3: Interactive Playground"""
    template_name = 'demos/index_design3.html'


class DemosIndexHybridView(BaseViewWithNavbar):
    """Hybrid Design: Showcase + Playground"""
    template_name = 'demos/index_design_hybrid.html'


class DemosIndexShadcnView(BaseViewWithNavbar):
    """shadcn/ui Design: Modern component system"""
    template_name = 'demos/index_shadcn.html'

    def mount(self, request, **kwargs):
        # Demo instances are lightweight and recreated on every access
        # State is stored directly on this view (counter, dropdown_open, etc.)
        pass

    def _get_demos(self):
        """Get or create demo instances (always fresh, never serialized)"""
        # Always create fresh instances - they're lightweight
        # This ensures they never get serialized with view state
        return {
            'counter': CounterDemo(self),
            'dropdown': DropdownDemo(self),
            'debounce': DebounceDemo(self),
            'cache': CacheDemo(self),
        }

    # Counter demo event handlers
    def increment(self):
        demos = self._get_demos()
        demos['counter'].increment()

    def decrement(self):
        demos = self._get_demos()
        demos['counter'].decrement()

    def reset(self):
        demos = self._get_demos()
        demos['counter'].reset()

    # Dropdown demo event handlers
    def toggle_dropdown(self):
        demos = self._get_demos()
        demos['dropdown'].toggle_dropdown()

    def select_item(self, item: str = "", **kwargs):
        demos = self._get_demos()
        demos['dropdown'].select_item(item=item, **kwargs)

    # Debounce demo event handler
    def debounce_search(self, value: str = "", **kwargs):
        demos = self._get_demos()
        demos['debounce'].debounce_search(value=value, **kwargs)

    # Cache demo event handler
    def cache_search(self, query: str = "", **kwargs):
        demos = self._get_demos()
        demos['cache'].cache_search(query=query, **kwargs)

    def get_context_data(self, **kwargs):
        """Merge context from all demos"""
        context = super().get_context_data(**kwargs)

        demos = self._get_demos()

        # Each demo provides its own context
        context.update(demos['counter'].get_context())
        context.update(demos['dropdown'].get_context())
        context.update(demos['debounce'].get_context())
        context.update(demos['cache'].get_context())

        return context


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
            self.todos = [todo for todo in self.todos if todo['id'] != int(item_id)]


class ChatView(BaseViewWithNavbar):
    """
    Chat demo - real-time messaging
    """
    template_name = "demos/chat.html"

    def mount(self, request, **kwargs):
        self.messages = []
        self.next_id = 1

    def send_message(self, text=""):
        if text.strip():
            self.messages.append({
                'id': self.next_id,
                'text': text,
                'user': 'You',
                'time': 'Just now',
            })
            self.next_id += 1


class ReactDemoView(BaseViewWithNavbar):
    """
    React-like UI demo showcasing hybrid server/client patterns
    """
    template_name = "demos/react.html"

    def mount(self, request, **kwargs):
        self.server_count = 0
        self.client_count = 0
        self.todo_items = []
        self.next_id = 1

    def increment_server(self):
        """Server-side counter increment"""
        self.server_count += 1

    def add_todo_item(self, text=""):
        """Add todo item from server-side"""
        if text.strip():
            self.todo_items.append({
                'id': self.next_id,
                'text': text,
                'done': False,
            })
            self.next_id += 1

    def get_context_data(self, **kwargs):
        """Add React demo context"""
        context = super().get_context_data(**kwargs)
        context['server_count'] = self.server_count
        context['client_count'] = self.client_count
        context['todos'] = self.todo_items
        return context


class PerformanceTestView(BaseViewWithNavbar):
    """
    Performance test demo - stress testing VDOM diffing with large lists
    """
    template_name = "demos/performance.html"

    def mount(self, request, **kwargs):
        self.items = []
        self.next_id = 1
        self.filter_text = ""
        self.sort_by = "name"

    def add_items_10(self):
        """Add 10 items"""
        self._add_items(10)

    def add_items_100(self):
        """Add 100 items"""
        self._add_items(100)

    def add_items_1000(self):
        """Add 1000 items"""
        self._add_items(1000)

    def _add_items(self, count):
        """Helper to add N items"""
        import datetime
        for i in range(count):
            self.items.append({
                'id': self.next_id,
                'name': f'Item {self.next_id}',
                'timestamp': datetime.datetime.now().strftime('%H:%M:%S'),
                'selected': False,
                'priority': False,
            })
            self.next_id += 1

    def clear_all(self):
        """Clear all items"""
        self.items = []

    def filter_items(self, value="", **kwargs):
        """Update filter text"""
        self.filter_text = value

    def sort_items(self, value="", **kwargs):
        """Update sort order"""
        self.sort_by = value
        if self.sort_by == "name":
            self.items.sort(key=lambda x: x['name'])
        elif self.sort_by == "timestamp":
            self.items.sort(key=lambda x: x['timestamp'])
        elif self.sort_by == "priority":
            self.items.sort(key=lambda x: x['priority'], reverse=True)

    def select_all(self):
        """Select all filtered items"""
        filtered = self._get_filtered_items()
        for item in self.items:
            if item in filtered:
                item['selected'] = True

    def deselect_all(self):
        """Deselect all items"""
        for item in self.items:
            item['selected'] = False

    def delete_selected(self):
        """Delete all selected items"""
        self.items = [item for item in self.items if not item['selected']]

    def toggle_priority_selected(self):
        """Toggle priority for all selected items"""
        for item in self.items:
            if item['selected']:
                item['priority'] = not item['priority']

    def toggle_item(self, id=None, **kwargs):
        """Toggle item selection"""
        if id:
            for item in self.items:
                if item['id'] == int(id):
                    item['selected'] = not item['selected']
                    break

    def toggle_priority(self, id=None, **kwargs):
        """Toggle item priority"""
        if id:
            for item in self.items:
                if item['id'] == int(id):
                    item['priority'] = not item['priority']
                    break

    def delete_item(self, id=None, **kwargs):
        """Delete a single item"""
        if id:
            self.items = [item for item in self.items if item['id'] != int(id)]

    def _get_filtered_items(self):
        """Get items matching current filter"""
        if self.filter_text:
            return [item for item in self.items if self.filter_text.lower() in item['name'].lower()]
        return self.items

    def get_context_data(self, **kwargs):
        """Add computed context data"""
        context = super().get_context_data(**kwargs)
        filtered = self._get_filtered_items()
        context['items'] = filtered
        context['item_count'] = len(self.items)
        context['selected_count'] = sum(1 for item in self.items if item['selected'])
        context['filter_text'] = self.filter_text
        context['sort_by'] = self.sort_by
        return context


class ProductDataTableView(BaseViewWithNavbar):
    """
    Product data table demo - showcasing data visualization and table rendering
    """
    template_name = "demos/datatable.html"

    def mount(self, request, **kwargs):
        self.products = self._generate_sample_products(20)

    def _generate_sample_products(self, count):
        """Generate sample product data"""
        import random
        categories = ['Electronics', 'Clothing', 'Food', 'Books', 'Tools', 'Toys']
        products = []

        for i in range(1, count + 1):
            price = round(random.uniform(9.99, 999.99), 2)
            stock = random.randint(0, 200)
            products.append({
                'id': i,
                'name': f'Product {i}',
                'category': random.choice(categories),
                'price': price,
                'stock': stock,
                'active': random.choice([True, False]),
            })

        return products

    def _get_stats(self):
        """Calculate statistics for products"""
        total_products = len(self.products)
        active_products = sum(1 for p in self.products if p.get('active', False))
        total_value = sum(p['price'] * p['stock'] for p in self.products)
        low_stock_count = sum(1 for p in self.products if p['stock'] < 20)

        return {
            'total_products': total_products,
            'active_products': active_products,
            'total_value': round(total_value, 2),
            'low_stock_count': low_stock_count,
        }

    def get_context_data(self, **kwargs):
        """Add product data and stats to context"""
        context = super().get_context_data(**kwargs)

        # Add stats
        stats = self._get_stats()
        context.update(stats)

        # Add products as JSON for client-side table
        import json
        context['products_json'] = json.dumps(self.products)

        return context
