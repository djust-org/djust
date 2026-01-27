"""
ForeignKey field components for djust.

Provides reactive select components for Django model relationships
with support for autocomplete and large querysets.
"""

from typing import Any, Callable, Dict, List, Optional
from django.db.models import QuerySet
from ..base import LiveComponent


class ForeignKeySelect(LiveComponent):
    """
    Select component for ForeignKey fields with autocomplete support.

    Features:
    - Load options from Django queryset
    - Search/autocomplete for large querysets
    - Configurable display field
    - Optional "empty" choice
    - Real-time filtering via LiveView
    - Bootstrap 5 and Tailwind CSS support

    Usage:
        from djust.components.forms import ForeignKeySelect

        # In your LiveView:
        def mount(self, request):
            self.author_select = ForeignKeySelect(
                name="author",
                queryset=Author.objects.all(),
                label_field="name",  # Field to display
                value_field="id",    # Field for value (default: pk)
                label="Author",
                required=True,
                searchable=True,     # Enable search
                search_fields=["name", "email"],  # Fields to search
            )

        # In template:
        {{ author_select.render }}

        # Or with value:
        self.author_select = ForeignKeySelect(
            name="author",
            queryset=Author.objects.all(),
            value=request.user.id,  # Pre-selected value
        )
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize the ForeignKey select component."""
        self.name: str = kwargs.get("name", "")
        self.queryset: Optional[QuerySet] = kwargs.get("queryset", None)
        self.label_field: str = kwargs.get("label_field", "__str__")
        self.value_field: str = kwargs.get("value_field", "pk")
        self.label: Optional[str] = kwargs.get("label", None)
        self.value: Any = kwargs.get("value", None)
        self.help_text: Optional[str] = kwargs.get("help_text", None)
        self.required: bool = kwargs.get("required", False)
        self.disabled: bool = kwargs.get("disabled", False)
        self.empty_label: Optional[str] = kwargs.get("empty_label", "---------")
        self.size: str = kwargs.get("size", "md")  # sm, md, lg

        # Search/autocomplete options
        self.searchable: bool = kwargs.get("searchable", False)
        self.search_fields: List[str] = kwargs.get("search_fields", [])
        self.min_search_length: int = kwargs.get("min_search_length", 2)
        self.max_options: int = kwargs.get("max_options", 100)  # Limit for large querysets

        # Internal state
        self.search_query: str = ""
        self.is_loading: bool = False

        # Event handlers
        self.on_change: Optional[Callable] = kwargs.get("on_change", None)

        # Validation state
        self.validation_state: Optional[str] = kwargs.get("validation_state", None)
        self.validation_message: Optional[str] = kwargs.get("validation_message", None)

    def get_options(self) -> List[Dict[str, Any]]:
        """Get options from queryset, optionally filtered by search query."""
        if self.queryset is None:
            return []

        qs = self.queryset

        # Apply search filter if searchable and query provided
        if (
            self.searchable
            and self.search_query
            and len(self.search_query) >= self.min_search_length
        ):
            from django.db.models import Q

            q_objects = Q()
            search_fields = self.search_fields or [self.label_field]
            for field in search_fields:
                if field != "__str__":
                    q_objects |= Q(**{f"{field}__icontains": self.search_query})
            qs = qs.filter(q_objects)

        # Limit results
        qs = qs[: self.max_options]

        # Build options list
        options = []
        for obj in qs:
            if self.label_field == "__str__":
                label = str(obj)
            else:
                label = getattr(obj, self.label_field, str(obj))

            value = getattr(obj, self.value_field, obj.pk)

            options.append(
                {
                    "value": value,
                    "label": label,
                }
            )

        return options

    def get_context_data(self) -> Dict[str, Any]:
        """Get context for rendering."""
        return {
            "name": self.name,
            "options": self.get_options(),
            "label": self.label,
            "value": self.value,
            "help_text": self.help_text,
            "required": self.required,
            "disabled": self.disabled,
            "empty_label": self.empty_label,
            "size": self.size,
            "searchable": self.searchable,
            "search_query": self.search_query,
            "is_loading": self.is_loading,
            "validation_state": self.validation_state,
            "validation_message": self.validation_message,
        }

    def search(self, query: str):
        """Handle search input (called from template)."""
        self.search_query = query
        self.trigger_update()

    def select(self, value: Any):
        """Handle option selection."""
        # Convert value to appropriate type
        if value == "" or value is None:
            self.value = None
        else:
            # Try to convert to int if it looks like an ID
            try:
                self.value = int(value)
            except (ValueError, TypeError):
                self.value = value

        if self.on_change:
            self.on_change(self.value)

        self.trigger_update()

    def clear(self):
        """Clear the selection."""
        self.value = None
        self.search_query = ""
        if self.on_change:
            self.on_change(None)
        self.trigger_update()

    def render(self) -> str:
        """Render the component."""
        from django.utils.safestring import mark_safe
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_bootstrap())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 select."""
        options = self.get_options()
        select_id = f"id_{self.name}"

        html = f'<div class="mb-3" id="{self.component_id}">'

        # Label
        if self.label:
            required_mark = ' <span class="text-danger">*</span>' if self.required else ""
            html += (
                f'<label for="{select_id}" class="form-label">{self.label}{required_mark}</label>'
            )

        # Build select classes
        select_classes = ["form-select"]
        if self.size != "md":
            select_classes.append(f"form-select-{self.size}")
        if self.validation_state == "valid":
            select_classes.append("is-valid")
        elif self.validation_state == "invalid":
            select_classes.append("is-invalid")

        # Select attributes
        attrs = [
            f'class="{" ".join(select_classes)}"',
            f'id="{select_id}"',
            f'name="{self.name}"',
            'dj-change="select(value)"',
        ]
        if self.required:
            attrs.append("required")
        if self.disabled:
            attrs.append("disabled")

        # Searchable wrapper
        if self.searchable:
            html += '<div class="position-relative">'
            html += f'<input type="text" class="form-control mb-1" placeholder="Search..." value="{self.search_query}" dj-input="search(value)">'

        html += f'<select {" ".join(attrs)}>'

        # Empty option
        if self.empty_label and not self.required:
            selected = " selected" if self.value is None else ""
            html += f'<option value=""{selected}>{self.empty_label}</option>'

        # Options
        for opt in options:
            opt_value = opt["value"]
            opt_label = opt["label"]
            selected = " selected" if str(opt_value) == str(self.value) else ""
            html += f'<option value="{opt_value}"{selected}>{opt_label}</option>'

        html += "</select>"

        if self.searchable:
            html += "</div>"

        # Help text
        if self.help_text:
            html += f'<div class="form-text">{self.help_text}</div>'

        # Validation feedback
        if self.validation_message:
            feedback_class = (
                "valid-feedback" if self.validation_state == "valid" else "invalid-feedback"
            )
            html += f'<div class="{feedback_class}">{self.validation_message}</div>'

        html += "</div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS select."""
        options = self.get_options()
        select_id = f"id_{self.name}"

        html = f'<div class="mb-4" id="{self.component_id}">'

        # Label
        if self.label:
            required_mark = ' <span class="text-red-500">*</span>' if self.required else ""
            html += f'<label for="{select_id}" class="block text-sm font-medium text-gray-700 mb-1">{self.label}{required_mark}</label>'

        # Build select classes
        select_classes = [
            "block",
            "w-full",
            "rounded-md",
            "border-gray-300",
            "shadow-sm",
            "focus:border-indigo-500",
            "focus:ring-indigo-500",
        ]
        if self.size == "sm":
            select_classes.append("text-sm")
        elif self.size == "lg":
            select_classes.append("text-lg")
        else:
            select_classes.append("text-base")

        if self.validation_state == "valid":
            select_classes.extend(["border-green-500", "focus:border-green-500"])
        elif self.validation_state == "invalid":
            select_classes.extend(["border-red-500", "focus:border-red-500"])

        # Select attributes
        attrs = [
            f'class="{" ".join(select_classes)}"',
            f'id="{select_id}"',
            f'name="{self.name}"',
            'dj-change="select(value)"',
        ]
        if self.required:
            attrs.append("required")
        if self.disabled:
            attrs.append("disabled")

        # Searchable input
        if self.searchable:
            html += '<div class="relative">'
            html += f'<input type="text" class="block w-full rounded-md border-gray-300 shadow-sm focus:border-indigo-500 focus:ring-indigo-500 text-sm mb-1" placeholder="Search..." value="{self.search_query}" dj-input="search(value)">'

        html += f'<select {" ".join(attrs)}>'

        # Empty option
        if self.empty_label and not self.required:
            selected = " selected" if self.value is None else ""
            html += f'<option value=""{selected}>{self.empty_label}</option>'

        # Options
        for opt in options:
            opt_value = opt["value"]
            opt_label = opt["label"]
            selected = " selected" if str(opt_value) == str(self.value) else ""
            html += f'<option value="{opt_value}"{selected}>{opt_label}</option>'

        html += "</select>"

        if self.searchable:
            html += "</div>"

        # Help text
        if self.help_text:
            html += f'<p class="mt-1 text-sm text-gray-500">{self.help_text}</p>'

        # Validation feedback
        if self.validation_message:
            if self.validation_state == "valid":
                html += f'<p class="mt-1 text-sm text-green-600">{self.validation_message}</p>'
            else:
                html += f'<p class="mt-1 text-sm text-red-600">{self.validation_message}</p>'

        html += "</div>"
        return html


class ManyToManySelect(LiveComponent):
    """
    Multi-select component for ManyToMany fields.

    Similar to ForeignKeySelect but allows multiple selections.
    Renders as a multi-select dropdown or checkbox list.

    Usage:
        from djust.components.forms import ManyToManySelect

        # In your LiveView:
        def mount(self, request):
            self.tags_select = ManyToManySelect(
                name="tags",
                queryset=Tag.objects.all(),
                label_field="name",
                label="Tags",
                render_as="checkboxes",  # or "select"
            )

        # In template:
        {{ tags_select.render }}
    """

    template_name = None

    def mount(self, **kwargs):
        """Initialize the ManyToMany select component."""
        self.name: str = kwargs.get("name", "")
        self.queryset: Optional[QuerySet] = kwargs.get("queryset", None)
        self.label_field: str = kwargs.get("label_field", "__str__")
        self.value_field: str = kwargs.get("value_field", "pk")
        self.label: Optional[str] = kwargs.get("label", None)
        self.values: List[Any] = kwargs.get("values", [])  # List of selected values
        self.help_text: Optional[str] = kwargs.get("help_text", None)
        self.required: bool = kwargs.get("required", False)
        self.disabled: bool = kwargs.get("disabled", False)
        self.render_as: str = kwargs.get("render_as", "select")  # "select" or "checkboxes"
        self.max_options: int = kwargs.get("max_options", 100)

        # Search options
        self.searchable: bool = kwargs.get("searchable", False)
        self.search_fields: List[str] = kwargs.get("search_fields", [])
        self.search_query: str = ""

        # Event handlers
        self.on_change: Optional[Callable] = kwargs.get("on_change", None)

        # Validation
        self.validation_state: Optional[str] = kwargs.get("validation_state", None)
        self.validation_message: Optional[str] = kwargs.get("validation_message", None)

    def get_options(self) -> List[Dict[str, Any]]:
        """Get options from queryset."""
        if self.queryset is None:
            return []

        qs = self.queryset

        # Apply search filter
        if self.searchable and self.search_query:
            from django.db.models import Q

            q_objects = Q()
            search_fields = self.search_fields or [self.label_field]
            for field in search_fields:
                if field != "__str__":
                    q_objects |= Q(**{f"{field}__icontains": self.search_query})
            qs = qs.filter(q_objects)

        qs = qs[: self.max_options]

        options = []
        for obj in qs:
            if self.label_field == "__str__":
                label = str(obj)
            else:
                label = getattr(obj, self.label_field, str(obj))

            value = getattr(obj, self.value_field, obj.pk)

            options.append(
                {
                    "value": value,
                    "label": label,
                    "selected": value in self.values or str(value) in [str(v) for v in self.values],
                }
            )

        return options

    def toggle(self, value: Any):
        """Toggle selection of a value."""
        # Try to convert to int for consistency with model PKs.
        # If conversion fails, use the original value (e.g., UUID, string PK).
        try:
            value = int(value)
        except (ValueError, TypeError):
            pass  # Keep original value if not convertible to int

        if value in self.values:
            self.values = [v for v in self.values if v != value]
        else:
            self.values = self.values + [value]

        if self.on_change:
            self.on_change(self.values)

        self.trigger_update()

    def get_context_data(self) -> Dict[str, Any]:
        """Get context for rendering."""
        return {
            "name": self.name,
            "options": self.get_options(),
            "label": self.label,
            "values": self.values,
            "help_text": self.help_text,
            "required": self.required,
            "disabled": self.disabled,
            "render_as": self.render_as,
            "searchable": self.searchable,
            "search_query": self.search_query,
            "validation_state": self.validation_state,
            "validation_message": self.validation_message,
        }

    def search(self, query: str):
        """Handle search input."""
        self.search_query = query
        self.trigger_update()

    def clear(self):
        """Clear all selections."""
        self.values = []
        if self.on_change:
            self.on_change([])
        self.trigger_update()

    def select_all(self):
        """Select all options."""
        self.values = [opt["value"] for opt in self.get_options()]
        if self.on_change:
            self.on_change(self.values)
        self.trigger_update()

    def render(self) -> str:
        """Render the component."""
        from django.utils.safestring import mark_safe
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if self.render_as == "checkboxes":
            if framework == "tailwind":
                return mark_safe(self._render_checkboxes_tailwind())
            else:
                return mark_safe(self._render_checkboxes_bootstrap())
        else:
            if framework == "tailwind":
                return mark_safe(self._render_select_tailwind())
            else:
                return mark_safe(self._render_select_bootstrap())

    def _render_checkboxes_bootstrap(self) -> str:
        """Render as Bootstrap 5 checkboxes."""
        options = self.get_options()

        html = f'<div class="mb-3" id="{self.component_id}">'

        if self.label:
            required_mark = ' <span class="text-danger">*</span>' if self.required else ""
            html += f'<label class="form-label">{self.label}{required_mark}</label>'

        # Search input
        if self.searchable:
            html += f'<input type="text" class="form-control form-control-sm mb-2" placeholder="Search..." value="{self.search_query}" dj-input="search(value)">'

        html += '<div class="border rounded p-2" style="max-height: 200px; overflow-y: auto;">'

        for opt in options:
            checked = "checked" if opt["selected"] else ""
            disabled = "disabled" if self.disabled else ""
            html += f"""
            <div class="form-check">
                <input type="checkbox" class="form-check-input" id="{self.name}_{opt["value"]}"
                       {checked} {disabled} dj-click="toggle({opt["value"]})">
                <label class="form-check-label" for="{self.name}_{opt["value"]}">{opt["label"]}</label>
            </div>
            """

        html += "</div>"

        if self.help_text:
            html += f'<div class="form-text">{self.help_text}</div>'

        if self.validation_message:
            feedback_class = (
                "valid-feedback d-block"
                if self.validation_state == "valid"
                else "invalid-feedback d-block"
            )
            html += f'<div class="{feedback_class}">{self.validation_message}</div>'

        html += "</div>"
        return html

    def _render_checkboxes_tailwind(self) -> str:
        """Render as Tailwind checkboxes."""
        options = self.get_options()

        html = f'<div class="mb-4" id="{self.component_id}">'

        if self.label:
            required_mark = ' <span class="text-red-500">*</span>' if self.required else ""
            html += f'<label class="block text-sm font-medium text-gray-700 mb-1">{self.label}{required_mark}</label>'

        if self.searchable:
            html += f'<input type="text" class="block w-full rounded-md border-gray-300 shadow-sm text-sm mb-2" placeholder="Search..." value="{self.search_query}" dj-input="search(value)">'

        html += '<div class="border border-gray-300 rounded-md p-2 max-h-48 overflow-y-auto">'

        for opt in options:
            checked = "checked" if opt["selected"] else ""
            disabled = "disabled" if self.disabled else ""
            html += f"""
            <div class="flex items-center mb-1">
                <input type="checkbox" class="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                       id="{self.name}_{opt["value"]}" {checked} {disabled} dj-click="toggle({opt["value"]})">
                <label class="ml-2 text-sm text-gray-700" for="{self.name}_{opt["value"]}">{opt["label"]}</label>
            </div>
            """

        html += "</div>"

        if self.help_text:
            html += f'<p class="mt-1 text-sm text-gray-500">{self.help_text}</p>'

        if self.validation_message:
            if self.validation_state == "valid":
                html += f'<p class="mt-1 text-sm text-green-600">{self.validation_message}</p>'
            else:
                html += f'<p class="mt-1 text-sm text-red-600">{self.validation_message}</p>'

        html += "</div>"
        return html

    def _render_select_bootstrap(self) -> str:
        """Render as Bootstrap 5 multi-select."""
        options = self.get_options()
        select_id = f"id_{self.name}"

        html = f'<div class="mb-3" id="{self.component_id}">'

        if self.label:
            required_mark = ' <span class="text-danger">*</span>' if self.required else ""
            html += (
                f'<label for="{select_id}" class="form-label">{self.label}{required_mark}</label>'
            )

        select_classes = ["form-select"]
        if self.validation_state == "invalid":
            select_classes.append("is-invalid")
        elif self.validation_state == "valid":
            select_classes.append("is-valid")

        disabled = "disabled" if self.disabled else ""

        html += f'<select class="{" ".join(select_classes)}" id="{select_id}" name="{self.name}" multiple size="5" {disabled}>'

        for opt in options:
            selected = "selected" if opt["selected"] else ""
            html += f'<option value="{opt["value"]}" {selected} dj-click="toggle({opt["value"]})">{opt["label"]}</option>'

        html += "</select>"

        if self.help_text:
            html += f'<div class="form-text">{self.help_text}</div>'

        if self.validation_message:
            feedback_class = (
                "valid-feedback" if self.validation_state == "valid" else "invalid-feedback"
            )
            html += f'<div class="{feedback_class}">{self.validation_message}</div>'

        html += "</div>"
        return html

    def _render_select_tailwind(self) -> str:
        """Render as Tailwind multi-select."""
        options = self.get_options()
        select_id = f"id_{self.name}"

        html = f'<div class="mb-4" id="{self.component_id}">'

        if self.label:
            required_mark = ' <span class="text-red-500">*</span>' if self.required else ""
            html += f'<label for="{select_id}" class="block text-sm font-medium text-gray-700 mb-1">{self.label}{required_mark}</label>'

        select_classes = ["block", "w-full", "rounded-md", "border-gray-300", "shadow-sm"]
        if self.validation_state == "invalid":
            select_classes.append("border-red-500")
        elif self.validation_state == "valid":
            select_classes.append("border-green-500")

        disabled = "disabled" if self.disabled else ""

        html += f'<select class="{" ".join(select_classes)}" id="{select_id}" name="{self.name}" multiple size="5" {disabled}>'

        for opt in options:
            selected = "selected" if opt["selected"] else ""
            html += f'<option value="{opt["value"]}" {selected} dj-click="toggle({opt["value"]})">{opt["label"]}</option>'

        html += "</select>"

        if self.help_text:
            html += f'<p class="mt-1 text-sm text-gray-500">{self.help_text}</p>'

        if self.validation_message:
            if self.validation_state == "valid":
                html += f'<p class="mt-1 text-sm text-green-600">{self.validation_message}</p>'
            else:
                html += f'<p class="mt-1 text-sm text-red-600">{self.validation_message}</p>'

        html += "</div>"
        return html
