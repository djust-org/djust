"""
Table component for djust - stateless, high-performance.

Provides data tables with headers and rows for display purposes.
This is a stateless Component optimized for performance.
For interactive tables with event handlers, use them in LiveView event handlers.
"""

from typing import List, Dict, Any
from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustTable

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustTable = None


class Table(Component):
    """
    Simple, stateless table component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to Python rendering (uses loops for rows/columns).

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Python fallback: ~50-200μs per render (depends on data size)

    Use Cases:
        - Display-only data tables (rendered but @click handled by parent)
        - Static data displays
        - Reports and dashboards
        - High-frequency rendering

    Note: This is a stateless component for rendering. Event handlers are
    attached in the template using @click directives.

    Args:
        columns: List of column dicts with 'key', 'label', 'sortable' keys
        data: List of row dicts with data matching column keys
        striped: Use striped row style
        bordered: Add borders to table
        hover: Enable hover effect on rows
        size: Table size (sm, md)
        responsive: Wrap in responsive container

    Examples:
        # Simple table
        columns = [
            {'key': 'name', 'label': 'Name'},
            {'key': 'email', 'label': 'Email'},
            {'key': 'status', 'label': 'Status'},
        ]
        data = [
            {'name': 'John Doe', 'email': 'john@example.com', 'status': 'Active'},
            {'name': 'Jane Smith', 'email': 'jane@example.com', 'status': 'Inactive'},
        ]
        table = Table(columns=columns, data=data)
        html = table.render()

        # Striped table with hover
        table = Table(
            columns=columns,
            data=data,
            striped=True,
            hover=True
        )

        # Bordered small table
        table = Table(
            columns=columns,
            data=data,
            bordered=True,
            size="sm"
        )

        # Responsive table (wraps in scrollable container)
        table = Table(
            columns=columns,
            data=data,
            responsive=True
        )

        # Sortable columns (display only, @click handler in parent)
        columns = [
            {'key': 'name', 'label': 'Name', 'sortable': True},
            {'key': 'email', 'label': 'Email', 'sortable': True},
            {'key': 'status', 'label': 'Status'},
        ]
        table = Table(columns=columns, data=data)
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustTable if _RUST_AVAILABLE else None

    # Note: Not using template because nested loops are needed
    # Using Python _render_custom() which is still fast (~50-200μs)

    def __init__(
        self,
        columns: List[Dict[str, Any]],
        data: List[Dict[str, Any]],
        striped: bool = False,
        bordered: bool = False,
        hover: bool = False,
        size: str = "md",
        responsive: bool = True,
    ):
        """
        Initialize table component.

        Args:
            columns: List of column dicts with 'key', 'label', 'sortable' keys
            data: List of row dicts with data matching column keys
            striped: Use striped row style
            bordered: Add borders to table
            hover: Enable hover effect on rows
            size: Table size (sm, md)
            responsive: Wrap in responsive container
        """
        super().__init__(
            columns=columns,
            data=data,
            striped=striped,
            bordered=bordered,
            hover=hover,
            size=size,
            responsive=responsive,
        )

        # Store for Python rendering
        self.columns = columns
        self.data = data
        self.striped = striped
        self.bordered = bordered
        self.hover = hover
        self.size = size
        self.responsive = responsive

    def get_context_data(self):
        """Context for hybrid rendering (if Rust template engine supports loops in future)"""
        return {
            "columns": self.columns,
            "data": self.data,
            "striped": self.striped,
            "bordered": self.bordered,
            "hover": self.hover,
            "size": self.size,
            "responsive": self.responsive,
        }

    def _render_custom(self) -> str:
        """Custom Python rendering (fallback)"""
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 table"""
        # Build table classes
        classes = ["table"]

        if self.striped:
            classes.append("table-striped")
        if self.bordered:
            classes.append("table-bordered")
        if self.hover:
            classes.append("table-hover")
        if self.size == "sm":
            classes.append("table-sm")

        table_class = " ".join(classes)

        parts = []

        # Responsive wrapper
        if self.responsive:
            parts.append('<div class="table-responsive">')

        # Table opening
        parts.append(f'<table class="{table_class}">')

        # Header
        parts.append("    <thead>")
        parts.append("        <tr>")
        for col in self.columns:
            label = col.get("label", "")
            sortable = col.get("sortable", False)
            if sortable:
                # Add sortable styling (but @click must be added by parent)
                parts.append(
                    f'            <th scope="col" class="sortable" style="cursor: pointer;">{label}</th>'
                )
            else:
                parts.append(f'            <th scope="col">{label}</th>')
        parts.append("        </tr>")
        parts.append("    </thead>")

        # Body
        parts.append("    <tbody>")
        for row in self.data:
            parts.append("        <tr>")
            for col in self.columns:
                key = col.get("key", "")
                value = row.get(key, "")
                parts.append(f"            <td>{value}</td>")
            parts.append("        </tr>")
        parts.append("    </tbody>")

        # Table closing
        parts.append("</table>")

        # Close responsive wrapper
        if self.responsive:
            parts.append("</div>")

        return "\n".join(parts)

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS table"""
        parts = []

        # Responsive wrapper
        if self.responsive:
            parts.append('<div class="overflow-x-auto">')

        # Table classes
        classes = ["min-w-full", "divide-y", "divide-gray-200"]

        if self.size == "sm":
            classes.append("text-sm")

        table_class = " ".join(classes)

        # Table opening
        parts.append(f'<table class="{table_class}">')

        # Header
        parts.append('    <thead class="bg-gray-50">')
        parts.append("        <tr>")
        for col in self.columns:
            label = col.get("label", "")
            sortable = col.get("sortable", False)

            if self.size == "sm":
                th_padding = "px-3 py-2"
            else:
                th_padding = "px-6 py-3"

            th_class = (
                f"{th_padding} text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
            )

            if sortable:
                th_class += " cursor-pointer hover:bg-gray-100"

            parts.append(f'            <th scope="col" class="{th_class}">{label}</th>')
        parts.append("        </tr>")
        parts.append("    </thead>")

        # Body
        body_class = "bg-white divide-y divide-gray-200"
        parts.append(f'    <tbody class="{body_class}">')

        for idx, row in enumerate(self.data):
            # Striped effect
            row_class = ""
            if self.striped and idx % 2 == 1:
                row_class = "bg-gray-50"
            if self.hover:
                row_class += " hover:bg-gray-100" if row_class else "hover:bg-gray-100"

            row_tag = f'<tr class="{row_class}">' if row_class else "<tr>"
            parts.append(f"        {row_tag}")

            for col in self.columns:
                key = col.get("key", "")
                value = row.get(key, "")

                if self.size == "sm":
                    td_padding = "px-3 py-2"
                else:
                    td_padding = "px-6 py-4"

                td_class = f"{td_padding} whitespace-nowrap text-sm text-gray-900"
                parts.append(f'            <td class="{td_class}">{value}</td>')
            parts.append("        </tr>")

        parts.append("    </tbody>")

        # Table closing
        parts.append("</table>")

        # Close responsive wrapper
        if self.responsive:
            parts.append("</div>")

        return "\n".join(parts)

    def _render_plain(self) -> str:
        """Render plain HTML table"""
        classes = ["table"]

        if self.striped:
            classes.append("table-striped")
        if self.bordered:
            classes.append("table-bordered")
        if self.hover:
            classes.append("table-hover")
        if self.size == "sm":
            classes.append("table-sm")

        table_class = " ".join(classes)

        parts = []

        # Responsive wrapper
        if self.responsive:
            parts.append('<div class="table-responsive">')

        # Table opening
        parts.append(f'<table class="{table_class}">')

        # Header
        parts.append("    <thead>")
        parts.append("        <tr>")
        for col in self.columns:
            label = col.get("label", "")
            sortable = col.get("sortable", False)
            if sortable:
                parts.append(f'            <th class="sortable">{label}</th>')
            else:
                parts.append(f"            <th>{label}</th>")
        parts.append("        </tr>")
        parts.append("    </thead>")

        # Body
        parts.append("    <tbody>")
        for row in self.data:
            parts.append("        <tr>")
            for col in self.columns:
                key = col.get("key", "")
                value = row.get(key, "")
                parts.append(f"            <td>{value}</td>")
            parts.append("        </tr>")
        parts.append("    </tbody>")

        # Table closing
        parts.append("</table>")

        # Close responsive wrapper
        if self.responsive:
            parts.append("</div>")

        return "\n".join(parts)
