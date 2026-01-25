"""
End-to-end benchmarks for the full LiveView rendering cycle.

These benchmarks measure the complete flow:
1. Template rendering
2. VDOM creation
3. VDOM diffing
4. Patch generation

This simulates real-world usage where a user interaction triggers
a state change and the UI needs to be updated.
"""

import pytest

# Try to import Rust extension
try:
    from djust._rust import render_template, diff_vdom, create_vnode, VNode
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False

# Try to import Python LiveView components
try:
    from djust.live_view import LiveView
    from djust.context import Context
    LIVEVIEW_AVAILABLE = True
except ImportError:
    LIVEVIEW_AVAILABLE = False


class TestFullRenderCycle:
    """Benchmark the complete render cycle using Rust extension."""

    @pytest.fixture
    def counter_template(self):
        return """
<div id="counter" dj-id="0">
    <h1 dj-id="1">Counter: {{ count }}</h1>
    <button dj-id="2" dj-click="increment">+</button>
    <button dj-id="3" dj-click="decrement">-</button>
</div>
"""

    @pytest.fixture
    def todo_template(self):
        return """
<div id="todos" dj-id="0">
    <h1 dj-id="1">Todo List ({{ items|length }} items)</h1>
    <ul dj-id="2">
        {% for item in items %}
        <li dj-id="item-{{ forloop.counter }}" class="{% if item.done %}completed{% endif %}">
            <input type="checkbox" {% if item.done %}checked{% endif %} dj-id="cb-{{ forloop.counter }}">
            <span dj-id="txt-{{ forloop.counter }}">{{ item.text }}</span>
        </li>
        {% endfor %}
    </ul>
    <form dj-id="3" dj-submit="add_todo">
        <input type="text" name="text" dj-id="4" value="{{ new_todo }}">
        <button type="submit" dj-id="5">Add</button>
    </form>
</div>
"""

    @pytest.mark.benchmark(group="e2e_simple")
    def test_counter_increment(self, benchmark, counter_template):
        """Benchmark counter increment: render old, render new, diff."""
        def cycle():
            old_html = render_template(counter_template, {"count": 5})
            new_html = render_template(counter_template, {"count": 6})
            return old_html, new_html

        result = benchmark(cycle)
        assert "Counter: 5" in result[0]
        assert "Counter: 6" in result[1]

    @pytest.mark.benchmark(group="e2e_simple")
    def test_counter_many_updates(self, benchmark, counter_template):
        """Benchmark 10 sequential counter updates."""
        def cycle():
            results = []
            for i in range(10):
                html = render_template(counter_template, {"count": i})
                results.append(html)
            return results

        result = benchmark(cycle)
        assert len(result) == 10

    @pytest.mark.benchmark(group="e2e_list")
    def test_todo_add_item(self, benchmark, todo_template):
        """Benchmark adding an item to a todo list."""
        items_before = [
            {"text": "Buy groceries", "done": False},
            {"text": "Walk the dog", "done": True},
            {"text": "Write code", "done": False},
        ]
        items_after = items_before + [{"text": "New task", "done": False}]

        def cycle():
            old_html = render_template(todo_template, {"items": items_before, "new_todo": ""})
            new_html = render_template(todo_template, {"items": items_after, "new_todo": ""})
            return old_html, new_html

        result = benchmark(cycle)
        assert "3 items" in result[0]
        assert "4 items" in result[1]

    @pytest.mark.benchmark(group="e2e_list")
    def test_todo_toggle_item(self, benchmark, todo_template):
        """Benchmark toggling a todo item's done state."""
        items_before = [
            {"text": "Task 1", "done": False},
            {"text": "Task 2", "done": False},
            {"text": "Task 3", "done": False},
        ]
        items_after = [
            {"text": "Task 1", "done": False},
            {"text": "Task 2", "done": True},  # Toggled
            {"text": "Task 3", "done": False},
        ]

        def cycle():
            old_html = render_template(todo_template, {"items": items_before, "new_todo": ""})
            new_html = render_template(todo_template, {"items": items_after, "new_todo": ""})
            return old_html, new_html

        result = benchmark(cycle)
        assert "completed" not in result[0] or result[0].count("completed") == 0
        assert "completed" in result[1]

    @pytest.mark.benchmark(group="e2e_list")
    def test_todo_large_list(self, benchmark, todo_template):
        """Benchmark rendering a large todo list (50 items)."""
        items = [{"text": f"Task {i}", "done": i % 3 == 0} for i in range(50)]

        def cycle():
            return render_template(todo_template, {"items": items, "new_todo": ""})

        result = benchmark(cycle)
        assert "50 items" in result


class TestFormValidation:
    """Benchmark form validation scenarios."""

    @pytest.fixture
    def form_template(self):
        return """
<form id="signup" dj-id="form">
    <div class="field" dj-id="f1">
        <label dj-id="l1">Username</label>
        <input type="text" name="username" value="{{ username }}"
               class="{% if errors.username %}is-invalid{% endif %}" dj-id="i1">
        {% if errors.username %}
        <div class="error" dj-id="e1">{{ errors.username }}</div>
        {% endif %}
    </div>
    <div class="field" dj-id="f2">
        <label dj-id="l2">Email</label>
        <input type="email" name="email" value="{{ email }}"
               class="{% if errors.email %}is-invalid{% endif %}" dj-id="i2">
        {% if errors.email %}
        <div class="error" dj-id="e2">{{ errors.email }}</div>
        {% endif %}
    </div>
    <div class="field" dj-id="f3">
        <label dj-id="l3">Password</label>
        <input type="password" name="password"
               class="{% if errors.password %}is-invalid{% endif %}" dj-id="i3">
        {% if errors.password %}
        <div class="error" dj-id="e3">{{ errors.password }}</div>
        {% endif %}
    </div>
    <button type="submit" dj-id="btn">Sign Up</button>
</form>
"""

    @pytest.mark.benchmark(group="e2e_form")
    def test_form_show_errors(self, benchmark, form_template):
        """Benchmark showing validation errors."""
        valid_state = {"username": "", "email": "", "errors": {}}
        error_state = {
            "username": "",
            "email": "bad",
            "errors": {
                "username": "Username is required",
                "email": "Invalid email format",
                "password": "Password must be at least 8 characters",
            },
        }

        def cycle():
            old_html = render_template(form_template, valid_state)
            new_html = render_template(form_template, error_state)
            return old_html, new_html

        result = benchmark(cycle)
        assert "is-invalid" not in result[0]
        assert "is-invalid" in result[1]

    @pytest.mark.benchmark(group="e2e_form")
    def test_form_clear_errors(self, benchmark, form_template):
        """Benchmark clearing validation errors."""
        error_state = {
            "username": "john",
            "email": "john@example.com",
            "errors": {"password": "Too short"},
        }
        valid_state = {
            "username": "john",
            "email": "john@example.com",
            "errors": {},
        }

        def cycle():
            old_html = render_template(form_template, error_state)
            new_html = render_template(form_template, valid_state)
            return old_html, new_html

        result = benchmark(cycle)
        assert "Too short" in result[0]
        assert "Too short" not in result[1]


class TestDataTable:
    """Benchmark data table scenarios."""

    @pytest.fixture
    def table_template(self):
        return """
<div id="data-table" dj-id="0">
    <div class="controls" dj-id="1">
        <input type="text" placeholder="Search..." value="{{ search }}" dj-id="search">
        <select dj-id="sort">
            <option value="name" {% if sort == 'name' %}selected{% endif %}>Name</option>
            <option value="email" {% if sort == 'email' %}selected{% endif %}>Email</option>
            <option value="created" {% if sort == 'created' %}selected{% endif %}>Created</option>
        </select>
    </div>
    <table dj-id="2">
        <thead dj-id="3">
            <tr dj-id="4">
                <th dj-id="5">Name</th>
                <th dj-id="6">Email</th>
                <th dj-id="7">Created</th>
                <th dj-id="8">Actions</th>
            </tr>
        </thead>
        <tbody dj-id="9">
            {% for row in rows %}
            <tr dj-id="row-{{ forloop.counter }}" class="{% if row.selected %}selected{% endif %}">
                <td dj-id="n-{{ forloop.counter }}">{{ row.name }}</td>
                <td dj-id="e-{{ forloop.counter }}">{{ row.email }}</td>
                <td dj-id="c-{{ forloop.counter }}">{{ row.created }}</td>
                <td dj-id="a-{{ forloop.counter }}">
                    <button dj-click="edit:{{ row.id }}">Edit</button>
                    <button dj-click="delete:{{ row.id }}">Delete</button>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
    <div class="pagination" dj-id="10">
        Page {{ page }} of {{ total_pages }}
    </div>
</div>
"""

    @pytest.mark.benchmark(group="e2e_table")
    def test_table_10_rows(self, benchmark, table_template):
        """Benchmark rendering a 10-row table."""
        rows = [
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com", "created": "2024-01-01", "selected": False}
            for i in range(10)
        ]

        def cycle():
            return render_template(table_template, {
                "rows": rows,
                "search": "",
                "sort": "name",
                "page": 1,
                "total_pages": 1,
            })

        result = benchmark(cycle)
        assert "User 0" in result
        assert "User 9" in result

    @pytest.mark.benchmark(group="e2e_table")
    def test_table_50_rows(self, benchmark, table_template):
        """Benchmark rendering a 50-row table."""
        rows = [
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com", "created": "2024-01-01", "selected": False}
            for i in range(50)
        ]

        def cycle():
            return render_template(table_template, {
                "rows": rows,
                "search": "",
                "sort": "name",
                "page": 1,
                "total_pages": 5,
            })

        result = benchmark(cycle)
        assert "User 49" in result

    @pytest.mark.benchmark(group="e2e_table")
    def test_table_row_selection(self, benchmark, table_template):
        """Benchmark selecting a row in the table."""
        rows_before = [
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com", "created": "2024-01-01", "selected": False}
            for i in range(20)
        ]
        rows_after = [
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com", "created": "2024-01-01", "selected": i == 5}
            for i in range(20)
        ]

        def cycle():
            old_html = render_template(table_template, {
                "rows": rows_before,
                "search": "",
                "sort": "name",
                "page": 1,
                "total_pages": 2,
            })
            new_html = render_template(table_template, {
                "rows": rows_after,
                "search": "",
                "sort": "name",
                "page": 1,
                "total_pages": 2,
            })
            return old_html, new_html

        result = benchmark(cycle)
        assert 'class="selected"' not in result[0]
        assert 'class="selected"' in result[1]

    @pytest.mark.benchmark(group="e2e_table")
    def test_table_pagination(self, benchmark, table_template):
        """Benchmark changing pages (complete data swap)."""
        page1_rows = [
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com", "created": "2024-01-01", "selected": False}
            for i in range(20)
        ]
        page2_rows = [
            {"id": i, "name": f"User {i}", "email": f"user{i}@example.com", "created": "2024-01-01", "selected": False}
            for i in range(20, 40)
        ]

        def cycle():
            old_html = render_template(table_template, {
                "rows": page1_rows,
                "search": "",
                "sort": "name",
                "page": 1,
                "total_pages": 5,
            })
            new_html = render_template(table_template, {
                "rows": page2_rows,
                "search": "",
                "sort": "name",
                "page": 2,
                "total_pages": 5,
            })
            return old_html, new_html

        result = benchmark(cycle)
        assert "Page 1 of 5" in result[0]
        assert "Page 2 of 5" in result[1]


class TestComplexComponent:
    """Benchmark complex nested component scenarios."""

    @pytest.fixture
    def dashboard_template(self):
        return """
<div id="dashboard" dj-id="0">
    <header dj-id="1">
        <h1 dj-id="2">{{ title }}</h1>
        <nav dj-id="3">
            {% for item in nav_items %}
            <a href="{{ item.url }}" class="{% if item.active %}active{% endif %}" dj-id="nav-{{ forloop.counter }}">
                {{ item.label }}
            </a>
            {% endfor %}
        </nav>
    </header>
    <aside dj-id="4">
        <ul dj-id="5">
            {% for widget in sidebar_widgets %}
            <li dj-id="widget-{{ forloop.counter }}">
                <h3 dj-id="wh-{{ forloop.counter }}">{{ widget.title }}</h3>
                <p dj-id="wp-{{ forloop.counter }}">{{ widget.content }}</p>
            </li>
            {% endfor %}
        </ul>
    </aside>
    <main dj-id="6">
        {% for card in cards %}
        <div class="card" dj-id="card-{{ forloop.counter }}">
            <h2 dj-id="ch-{{ forloop.counter }}">{{ card.title }}</h2>
            <p dj-id="cp-{{ forloop.counter }}">{{ card.description }}</p>
            <div class="stats" dj-id="cs-{{ forloop.counter }}">
                <span dj-id="s1-{{ forloop.counter }}">{{ card.stat1 }}</span>
                <span dj-id="s2-{{ forloop.counter }}">{{ card.stat2 }}</span>
            </div>
        </div>
        {% endfor %}
    </main>
    <footer dj-id="7">
        <p dj-id="8">Last updated: {{ last_updated }}</p>
    </footer>
</div>
"""

    @pytest.mark.benchmark(group="e2e_complex")
    def test_dashboard_full_render(self, benchmark, dashboard_template):
        """Benchmark rendering a complex dashboard."""
        context = {
            "title": "Analytics Dashboard",
            "nav_items": [
                {"url": "/", "label": "Home", "active": True},
                {"url": "/reports", "label": "Reports", "active": False},
                {"url": "/settings", "label": "Settings", "active": False},
            ],
            "sidebar_widgets": [
                {"title": "Quick Stats", "content": "1,234 visitors today"},
                {"title": "Alerts", "content": "No new alerts"},
                {"title": "Tasks", "content": "5 pending tasks"},
            ],
            "cards": [
                {"title": "Revenue", "description": "Monthly revenue", "stat1": "$12,345", "stat2": "+5.2%"},
                {"title": "Users", "description": "Active users", "stat1": "1,234", "stat2": "+12.3%"},
                {"title": "Orders", "description": "New orders", "stat1": "567", "stat2": "-2.1%"},
                {"title": "Conversion", "description": "Conversion rate", "stat1": "3.2%", "stat2": "+0.5%"},
            ],
            "last_updated": "2024-01-15 10:30:00",
        }

        result = benchmark(render_template, dashboard_template, context)
        assert "Analytics Dashboard" in result
        assert "$12,345" in result

    @pytest.mark.benchmark(group="e2e_complex")
    def test_dashboard_stat_update(self, benchmark, dashboard_template):
        """Benchmark updating stats in the dashboard."""
        base_context = {
            "title": "Analytics Dashboard",
            "nav_items": [
                {"url": "/", "label": "Home", "active": True},
                {"url": "/reports", "label": "Reports", "active": False},
            ],
            "sidebar_widgets": [
                {"title": "Quick Stats", "content": "1,234 visitors today"},
            ],
            "cards": [
                {"title": "Revenue", "description": "Monthly revenue", "stat1": "$12,345", "stat2": "+5.2%"},
                {"title": "Users", "description": "Active users", "stat1": "1,234", "stat2": "+12.3%"},
            ],
            "last_updated": "2024-01-15 10:30:00",
        }

        updated_context = {
            **base_context,
            "cards": [
                {"title": "Revenue", "description": "Monthly revenue", "stat1": "$12,500", "stat2": "+5.8%"},
                {"title": "Users", "description": "Active users", "stat1": "1,250", "stat2": "+13.1%"},
            ],
            "last_updated": "2024-01-15 10:31:00",
        }

        def cycle():
            old_html = render_template(dashboard_template, base_context)
            new_html = render_template(dashboard_template, updated_context)
            return old_html, new_html

        result = benchmark(cycle)
        assert "$12,345" in result[0]
        assert "$12,500" in result[1]
