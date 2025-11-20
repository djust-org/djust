"""
Examples page view.
"""
from .base import BaseMarketingView
from marketing.components.code_block import CodeTabs


class ExamplesView(BaseMarketingView):
    """
    Examples page showcasing djust code examples.

    Displays various use cases with complete code examples.
    """

    template_name = 'marketing/examples.html'
    page_slug = 'examples'

    def mount(self, request, **kwargs):
        """Initialize examples page state."""
        super().mount(request, **kwargs)

        # Counter demo state (inline, not a component)
        self.count = 0

        # Todo demo state (inline, not a component)
        self.todos = []
        self.next_id = 1

        # Form demo state
        self.form_email = ""
        self.form_password = ""
        self.form_errors = {}
        self.form_success = False

        # Table demo state
        self.search_query = ""
        self.sort_by = "name"
        self.sort_order = "asc"
        self._products = [
            {'id': 1, 'name': 'Laptop Pro', 'price': 1299, 'stock': 42},
            {'id': 2, 'name': 'Wireless Mouse', 'price': 29, 'stock': 156},
            {'id': 3, 'name': 'Mechanical Keyboard', 'price': 89, 'stock': 23},
            {'id': 4, 'name': 'USB-C Hub', 'price': 45, 'stock': 78},
            {'id': 5, 'name': '27" Monitor', 'price': 399, 'stock': 12},
        ]

        # Demo examples with interactive components
        demos_data = [
            {
                'id': 'counter',
                'title': 'Real-Time Counter',
                'description': 'Basic reactivity with server-side state management',
                'python_code': '''from djust import LiveView

class CounterView(LiveView):
    template_name = 'counter.html'

    def mount(self, request):
        """Initialize counter state"""
        self.count = 0

    def increment(self):
        """Increment counter by 1"""
        self.count += 1

    def decrement(self):
        """Decrement counter by 1"""
        self.count -= 1

    def reset(self):
        """Reset counter to 0"""
        self.count = 0

    def get_context_data(self, **kwargs):
        return {'count': self.count}''',
                'template_code': '''<div class="counter-app">
    <h2>Counter Example</h2>

    <div class="count-display">
        {{ count }}
    </div>

    <div class="button-group">
        <button @click="decrement" class="btn-danger">
            Decrement
        </button>
        <button @click="reset" class="btn-secondary">
            Reset
        </button>
        <button @click="increment" class="btn-success">
            Increment
        </button>
    </div>
</div>''',
            },
            {
                'id': 'todo',
                'title': 'Todo List (CRUD)',
                'description': 'Full create, read, update, delete operations',
                'python_code': '''from djust import LiveView

class TodoListView(LiveView):
    template_name = 'todo.html'

    def mount(self, request):
        """Initialize todo list"""
        self.todos = []
        self.next_id = 1

    def add_todo(self, text: str = "", **kwargs):
        """Add new todo item"""
        if text.strip():
            self.todos.append({
                'id': self.next_id,
                'text': text,
                'completed': False
            })
            self.next_id += 1

    def toggle_todo(self, id: int = None, **kwargs):
        """Toggle todo completion status"""
        todo = next((t for t in self.todos if t['id'] == id), None)
        if todo:
            todo['completed'] = not todo['completed']

    def delete_todo(self, id: int = None, **kwargs):
        """Delete todo item"""
        self.todos = [t for t in self.todos if t['id'] != id]

    def get_context_data(self, **kwargs):
        completed = sum(1 for t in self.todos if t['completed'])
        return {
            'todos': self.todos,
            'completed_count': completed,
            'total_count': len(self.todos)
        }''',
                'template_code': '''<div class="todo-app">
    <h2>Todo List</h2>

    <form @submit="add_todo" class="add-form">
        <input type="text"
               name="text"
               placeholder="What needs to be done?" />
        <button type="submit">Add</button>
    </form>

    <div class="todo-list">
        {% for todo in todos %}
        <div class="todo-item">
            <input type="checkbox"
                   @change="toggle_todo"
                   data-id="{{ todo.id }}"
                   {% if todo.completed %}checked{% endif %} />
            <span class="{% if todo.completed %}completed{% endif %}">
                {{ todo.text }}
            </span>
            <button @click="delete_todo"
                    data-id="{{ todo.id }}"
                    class="delete-btn">
                Delete
            </button>
        </div>
        {% endfor %}
    </div>

    <div class="stats">
        <span>{{ completed_count }} completed</span>
        <span>{{ total_count }} total</span>
    </div>
</div>''',
            },
            {
                'id': 'form',
                'title': 'Real-Time Form Validation',
                'description': 'Instant feedback with Django Forms integration',
                'python_code': '''from djust import LiveView
from djust.forms import FormMixin
from .forms import SignupForm

class SignupView(FormMixin, LiveView):
    form_class = SignupForm
    template_name = 'signup.html'

    def mount(self, request):
        """Initialize form view"""
        self.success_message = None

    def validate_field(self, field: str = None, value: str = "", **kwargs):
        """Real-time field validation"""
        # Validation happens automatically via FormMixin
        # Errors are displayed in real-time
        pass

    def form_valid(self, form):
        """Handle valid form submission"""
        # Save the user
        user = form.save()
        self.success_message = "Account created successfully!"
        # Reset form
        self.form = self.form_class()

    def form_invalid(self, form):
        """Handle invalid form submission"""
        # Errors displayed automatically
        pass''',
                'forms_code': '''from django import forms
from django.contrib.auth.models import User

class SignupForm(forms.ModelForm):
    """User signup form with validation"""

    password = forms.CharField(
        widget=forms.PasswordInput,
        min_length=8,
        help_text="At least 8 characters"
    )

    class Meta:
        model = User
        fields = ['email', 'password']

    def clean_email(self):
        """Validate email is unique"""
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                "Email already registered"
            )
        return email

    def clean_password(self):
        """Validate password strength"""
        password = self.cleaned_data.get('password')
        if len(password) < 8:
            raise forms.ValidationError(
                "Password must be at least 8 characters"
            )
        return password''',
                'template_code': '''<div class="signup-form">
    <h2>Sign Up</h2>

    <form @submit="submit_form">
        <div class="form-field">
            <label for="email">Email</label>
            <input type="email"
                   name="email"
                   @change="validate_field"
                   value="{{ form.email.value|default:'' }}" />
            {% if form.email.errors %}
            <p class="error">{{ form.email.errors.0 }}</p>
            {% endif %}
        </div>

        <div class="form-field">
            <label for="password">Password</label>
            <input type="password"
                   name="password"
                   @change="validate_field" />
            {% if form.password.errors %}
            <p class="error">{{ form.password.errors.0 }}</p>
            {% endif %}
            <p class="help">At least 8 characters</p>
        </div>

        <button type="submit">Sign Up</button>

        {% if success_message %}
        <p class="success">✓ {{ success_message }}</p>
        {% endif %}
    </form>
</div>''',
            },
            {
                'id': 'table',
                'title': 'Sortable Data Table',
                'description': 'Interactive table with search and sorting',
                'python_code': '''from djust import LiveView
from .models import Product

class ProductTableView(LiveView):
    template_name = 'product_table.html'

    def mount(self, request):
        """Initialize table view"""
        self._products = Product.objects.all()
        self.search_query = ""
        self.sort_by = "name"
        self.sort_order = "asc"

    def search(self, value: str = "", **kwargs):
        """Search products by name"""
        self.search_query = value
        self._refresh_products()

    def sort(self, field: str = None, **kwargs):
        """Sort products by field"""
        if field == self.sort_by:
            # Toggle order
            self.sort_order = "desc" if self.sort_order == "asc" else "asc"
        else:
            self.sort_by = field
            self.sort_order = "asc"
        self._refresh_products()

    def _refresh_products(self):
        """Refresh product list with filters"""
        queryset = Product.objects.all()

        # Apply search filter
        if self.search_query:
            queryset = queryset.filter(
                name__icontains=self.search_query
            )

        # Apply sorting
        order_prefix = "-" if self.sort_order == "desc" else ""
        queryset = queryset.order_by(f"{order_prefix}{self.sort_by}")

        self._products = queryset

    def get_context_data(self, **kwargs):
        self.products = self._products  # JIT serialization
        context = super().get_context_data(**kwargs)
        context.update({
            'search_query': self.search_query,
            'sort_by': self.sort_by,
            'sort_order': self.sort_order
        })
        return context''',
                'template_code': '''<div class="product-table">
    <h2>Products</h2>

    <input type="text"
           @input="search"
           value="{{ search_query }}"
           placeholder="Search products..." />

    <table>
        <thead>
            <tr>
                <th @click="sort" data-field="name">
                    Name
                    {% if sort_by == 'name' %}
                        {% if sort_order == 'asc' %}↑{% else %}↓{% endif %}
                    {% endif %}
                </th>
                <th @click="sort" data-field="price">
                    Price
                    {% if sort_by == 'price' %}
                        {% if sort_order == 'asc' %}↑{% else %}↓{% endif %}
                    {% endif %}
                </th>
                <th @click="sort" data-field="stock">
                    Stock
                    {% if sort_by == 'stock' %}
                        {% if sort_order == 'asc' %}↑{% else %}↓{% endif %}
                    {% endif %}
                </th>
            </tr>
        </thead>
        <tbody>
            {% for product in products %}
            <tr>
                <td>{{ product.name }}</td>
                <td>${{ product.price }}</td>
                <td>{{ product.stock }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>''',
            },
        ]

        # Add CodeTabs and index to each demo
        for index, demo in enumerate(demos_data):
            demo['index'] = index
            demo['reverse_layout'] = (index % 2 == 1)  # Reverse layout for odd indices (2nd, 4th, etc.)
            tabs = [
                {
                    'label': 'views.py',
                    'code': demo['python_code'],
                    'language': 'python',
                    'filename': 'views.py',
                },
            ]

            # Add forms.py tab if present
            if 'forms_code' in demo:
                tabs.append({
                    'label': 'forms.py',
                    'code': demo['forms_code'],
                    'language': 'python',
                    'filename': 'forms.py',
                })

            # Add template tab
            tabs.append({
                'label': 'template.html',
                'code': demo['template_code'],
                'language': 'html',
                'filename': 'template.html',
            })

            # Pre-render code tabs to avoid template call issues
            demo['code_tabs'] = CodeTabs(tabs=tabs).render()

        self.demos = demos_data

    # Counter event handlers
    def increment(self, **kwargs):
        """Increment counter by 1."""
        self.count += 1

    def decrement(self, **kwargs):
        """Decrement counter by 1."""
        self.count -= 1

    def reset(self, **kwargs):
        """Reset counter to 0."""
        self.count = 0

    # Todo event handlers
    def add_todo(self, text: str = "", **kwargs):
        """Add new todo item."""
        if text.strip():
            self.todos.append({
                'id': self.next_id,
                'text': text,
                'completed': False
            })
            self.next_id += 1

    def toggle_todo(self, id: str = None, **kwargs):
        """Toggle todo completion status."""
        if id:
            todo_id = int(id)
            todo = next((t for t in self.todos if t['id'] == todo_id), None)
            if todo:
                todo['completed'] = not todo['completed']

    def delete_todo(self, id: str = None, **kwargs):
        """Delete todo item."""
        if id:
            todo_id = int(id)
            self.todos = [t for t in self.todos if t['id'] != todo_id]

    # Form event handlers
    def validate_field(self, field_name: str = None, value: str = "", **kwargs):
        """Validate form field in real-time."""
        if field_name == 'email':
            self.form_email = value
            if '@' not in value:
                self.form_errors['email'] = 'Invalid email address'
            else:
                self.form_errors.pop('email', None)
        elif field_name == 'password':
            self.form_password = value
            if len(value) < 8:
                self.form_errors['password'] = 'Password must be at least 8 characters'
            else:
                self.form_errors.pop('password', None)

    def submit_form(self, email: str = "", password: str = "", **kwargs):
        """Handle form submission."""
        self.form_email = email
        self.form_password = password

        # Validate
        errors = {}
        if '@' not in email:
            errors['email'] = 'Invalid email address'
        if len(password) < 8:
            errors['password'] = 'Password must be at least 8 characters'

        if errors:
            self.form_errors = errors
            self.form_success = False
        else:
            self.form_errors = {}
            self.form_success = True

    # Table event handlers
    def search(self, value: str = "", **kwargs):
        """Search products by name."""
        self.search_query = value

    def sort(self, field: str = None, **kwargs):
        """Sort products by field."""
        if field:
            if field == self.sort_by:
                # Toggle order
                self.sort_order = "desc" if self.sort_order == "asc" else "asc"
            else:
                self.sort_by = field
                self.sort_order = "asc"

    def get_context_data(self, **kwargs):
        """Add examples page context."""
        context = super().get_context_data(**kwargs)

        # Filter and sort products for table demo
        filtered_products = self._products
        if self.search_query:
            filtered_products = [
                p for p in filtered_products
                if self.search_query.lower() in p['name'].lower()
            ]

        # Sort products
        reverse = self.sort_order == 'desc'
        sorted_products = sorted(
            filtered_products,
            key=lambda p: p.get(self.sort_by, ''),
            reverse=reverse
        )

        # Add inline demo state (counter, todo, form, table)
        context.update({
            'demos': self.demos,
            'count': self.count,
            'todos': self.todos,
            'completed_count': sum(1 for t in self.todos if t.get('completed', False)),
            'total_count': len(self.todos),
            'form_email': self.form_email,
            'form_password': self.form_password,
            'form_errors': self.form_errors,
            'form_success': self.form_success,
            'search_query': self.search_query,
            'sort_by': self.sort_by,
            'sort_order': self.sort_order,
            'products': sorted_products,
        })
        return context
